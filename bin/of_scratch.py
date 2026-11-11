"""Session scratch folders and free disk, checked by the pulse (Acme through acme_org and of_server, 2026-11-11).

Free disk on the Mac fell from 46 to 28 GiB in about seven hours, 2.7 GiB an hour, while the event store grew 25 MB.
The space went to live sessions' scratch folders (`/private/tmp/claude-<uid>/<project>/<session>`): 49 GB, mostly
full repository worktrees of 1.5 GB each and copies of data. Below about 20 GiB the relay, the event store and every
session are at risk.

What the pulse does with this module:
- sizes each live session's scratch folder at most every SIZE_EVERY_S, within SIZE_BUDGET_S per pulse (a `du` of
  49 GB every 5 minutes would itself load the Mac), and records the sizes and the free disk as `of.disk_usage`;
- tells a position once, in its own pane when it is idle, when its folder passes SCRATCH_CAP (again only after it
  fell under the cap), and re-sizes such a folder every pulse;
- past SCRATCH_QUOTA (Acme's platform_owner through acme_org, 2026-11-11: "a quota, not only a warning"), lists
  the session in `scratch-quota.json` in the state folder, which the guard reads to refuse new writes into that
  folder (reads and deletions stay allowed) until the folder is back under SCRATCH_CAP, and tells the position;
- files one card to the founder when free disk falls under CARD_BELOW, naming the largest folders, and closes it
  itself once free disk is back above CARD_CLEAR.
It never deletes anything: a scratch folder is its session's own work.
"""

import glob
import json
import os
import shutil
import subprocess
import time

import of_events

GiB = 2 ** 30
SCRATCH_CAP = 3 * GiB
SCRATCH_QUOTA = 6 * GiB  # the hard line: the guard refuses new writes into a folder over it (docs/guard.md)
SIZE_EVERY_S = 1800
OVER_EVERY_S = 300       # a folder over the cap is re-sized every pulse: a runaway grows GiBs in 30 minutes
SIZE_BUDGET_S = 45
DU_TIMEOUT_S = 30
CARD_BELOW = 20 * GiB
CARD_CLEAR = 25 * GiB
CARD_SOURCE = "disk_check"


def base_dir():
    """Claude Code's temporary root for this user (macOS resolves /tmp to /private/tmp)."""
    uid = os.getuid()
    for d in (f"/private/tmp/claude-{uid}", f"/tmp/claude-{uid}"):
        if os.path.isdir(d):
            return d
    return f"/tmp/claude-{uid}"


def folders(session, base=None):
    """A session's scratch folders: one per project folder it ran in (normally one)."""
    if not session or "/" in session or session.startswith("."):
        return []
    return sorted(d for d in glob.glob(os.path.join(base or base_dir(), "*", session)) if os.path.isdir(d))


def du(path, timeout=DU_TIMEOUT_S):
    """Bytes under path (du -skx, at low priority), or None when it did not finish in time."""
    try:
        r = subprocess.run(["nice", "-n", "15", "du", "-skx", path], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return int(r.stdout.split()[0]) * 1024
    except (IndexError, ValueError):
        return None


def quota_path():
    return os.path.join(of_events.STATE_DIR, "scratch-quota.json")


def read_quota():
    """{session: {position, paths, bytes, since}}: the sessions whose scratch folder is over the quota now."""
    try:
        with open(quota_path()) as fh:
            return (json.load(fh) or {}).get("sessions") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def _write_quota(sessions, now):
    try:
        tmp = quota_path() + f".{os.getpid()}"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump({"at": now, "quota": SCRATCH_QUOTA, "release_below": SCRATCH_CAP, "sessions": sessions}, fh)
        os.replace(tmp, quota_path())
    except OSError:
        pass


def gib(n):
    return f"{n / GiB:.1f}"


def note(name, paths, size, free):
    where = ", ".join(paths)
    return (f"Message from of_pulse (via of pulse, authority: none; scratch size)\n"
            f"Your session's scratch folder ({where}) holds {gib(size)} GiB, over the {gib(SCRATCH_CAP)} GiB cap; "
            f"this Mac has {gib(free)} GiB free, and below about 20 GiB the relay, the event store and every session "
            f"are at risk. Remove what no open task of yours still uses there: finished worktrees (git worktree "
            f"remove), copies of data, build output. Keep anything you are still working from.\n"
            f"[to {name}, once while the folder stays over the cap]")


def quota_note(name, paths, size):
    return (f"Message from of_pulse (via of pulse, authority: none; scratch quota)\n"
            f"Your session's scratch folder ({', '.join(paths)}) holds {gib(size)} GiB, over the {gib(SCRATCH_QUOTA)} "
            f"GiB quota. Prune it now: until it is back under {gib(SCRATCH_CAP)} GiB, OF's guard may refuse new writes "
            f"into it (reading and deleting stay allowed).\n"
            f"[to {name}, once while the folder stays over the quota]")


def card_text(free, top):
    lines = "; ".join(f"{n} {gib(b)} ГиБ" for n, b in top[:5]) or "размеры ещё не измерены"
    return (f"На Маке осталось {gib(free)} ГиБ свободного места (меньше {gib(CARD_BELOW)}). Ниже примерно 20 ГиБ "
            f"под угрозой relay, база событий OF и все сессии. Больше всего занимают папки scratch живых сессий: "
            f"{lines}. Пульс уже попросил позиции сверх {gib(SCRATCH_CAP)} ГиБ почистить свои папки; сам он ничего "
            f"не удаляет. Карточка закроется сама, когда свободно станет больше {gib(CARD_CLEAR)} ГиБ. "
            f"Что делать?")


def _open_cards(conn):
    ids = [eid for (eid,) in conn.execute(
        "SELECT event_id FROM event WHERE name = 'of.agent_question' AND json_extract(payload, '$.source') = ?",
        (CARD_SOURCE,))]
    return sorted(i for i in ids if not conn.execute(
        "SELECT 1 FROM event WHERE name IN ('of.agent_question_answered', 'of.ask_withdrawn') AND payload LIKE ? "
        "LIMIT 1", (f"%{i}%",)).fetchone())


def check(positions, agents, bind, mem, now=None, prompt=None, log=print, free=None, size=du, base=None,
          conn=None, clock=time.monotonic):
    """One pulse's pass. mem is the pulse's memory (key "scratch"). Returns the positions told this pass."""
    now = now or time.time()
    base = base or base_dir()
    free = shutil.disk_usage(os.path.expanduser("~")).free if free is None else free
    st = mem.setdefault("scratch", {})
    seen, measured, told, over = set(), [], [], {}
    start = clock()
    for p, a in bind(positions, agents)[0]:
        sid = (a.get("agent_session") or {}).get("value")
        paths = folders(sid, base)
        if not paths:
            continue
        seen.add(sid)
        e = st.setdefault(sid, {"at": None, "bytes": None, "told": False})
        e["position"] = p["name"]
        every = OVER_EVERY_S if (e.get("bytes") or 0) >= SCRATCH_CAP else SIZE_EVERY_S
        due = e.get("at") is None or now - e["at"] >= every
        if due and clock() - start < SIZE_BUDGET_S:
            sizes = [size(x) for x in paths]
            if all(s is not None for s in sizes):
                e.update(at=now, bytes=sum(sizes))
                measured.append({"position": p["name"], "org": p.get("_org"), "session": sid, "bytes": e["bytes"]})
        b = e.get("bytes")
        if b is None:
            continue
        if b < SCRATCH_CAP:
            e["told"] = False
            e.pop("over_quota", None)
        if b >= SCRATCH_QUOTA and not e.get("over_quota"):
            e["over_quota"], e["quota_told"] = now, False
        if e.get("over_quota"):
            over[sid] = {"position": p["name"], "org": p.get("_org"), "paths": paths, "bytes": b,
                         "since": e["over_quota"]}
            if not e.get("quota_told") and prompt and a.get("agent_status") in ("idle", "done"):
                prompt(a["pane_id"], quota_note(p["name"], paths, b), "of_pulse")
                of_events.emit("nudge_sent", {"position": p["name"], "from_position": "of_pulse",
                                              "to_position": p["name"], "about": f"scratch folder {gib(b)} GiB",
                                              "pane": a["pane_id"], "kind": "scratch_over_quota"},
                               position=p["name"], org=p.get("_org"))
                e["quota_told"] = e["told"] = True
                told.append(p["name"])
                log(f"told {p['name']}: scratch {gib(b)} GiB, over the quota")
        elif b >= SCRATCH_CAP and not e.get("told") and prompt and a.get("agent_status") in ("idle", "done"):
            prompt(a["pane_id"], note(p["name"], paths, b, free), "of_pulse")
            of_events.emit("nudge_sent", {"position": p["name"], "from_position": "of_pulse",
                                          "to_position": p["name"], "about": f"scratch folder {gib(b)} GiB",
                                          "pane": a["pane_id"], "kind": "scratch_over_cap"},
                           position=p["name"], org=p.get("_org"))
            e["told"] = True
            told.append(p["name"])
            log(f"told {p['name']}: scratch {gib(b)} GiB")
    for sid in [s for s in st if s not in seen]:
        st.pop(sid)  # the session ended: its folder is no longer a live position's
    if over or read_quota():
        _write_quota(over, now)
    if measured:
        of_events.emit("disk_usage", {"free_bytes": free, "scratch": measured, "base": base})
    top = sorted(((e["position"], e["bytes"]) for e in st.values() if e.get("bytes")), key=lambda x: -x[1])
    _card(mem, now, free, top, log, conn)
    return told


def _card(mem, now, free, top, log, conn=None):
    """One founder card per low-disk episode; closed by the pulse once free disk is back above CARD_CLEAR."""
    if free < CARD_BELOW and not mem.get("disk_card"):
        eid = of_events.emit("agent_question", {
            "detected_by": "declared", "source": CARD_SOURCE, "from_position": "of_platform", "to": "founder",
            "topic_key": "disk-low", "blocking": False,
            "questions": [{"question": card_text(free, top),
                           "options": ["попросить хранителей структуры разобрать папки (рекомендую: они знают, "
                                       "что из этого ещё в работе)", "разберусь сам"]}]},
            position="of_platform")
        mem["disk_card"] = now
        log(f"disk: {gib(free)} GiB free, founder card {eid}")
    elif free > CARD_CLEAR and mem.get("disk_card"):
        import of_store
        c = conn or of_store.connect()
        try:
            ids = _open_cards(c)
        finally:
            if conn is None:
                c.close()
        if ids:
            of_events.emit("agent_question_answered", {"ask_id": ids[0], "ask_ids": ids, "by": "of_platform",
                                                       "via": "pulse",
                                                       "note": f"free disk is back at {gib(free)} GiB"},
                           position="of_platform")
            log(f"disk: {gib(free)} GiB free again, closed {len(ids)} card(s)")
        mem.pop("disk_card", None)
