"""`of codex selftest <position>`: the canary checks of a running Codex position (W3 of hand-over 9ee1db9f; the plan
for Codex positions, step 5, approved by the founder in prompt 01a20d66-a69c through of_architect). Run by the
machine's operator, outside any sandbox, against a position `of spawn` started (and again after a resume):

  requirements   the managed requirements file holds Codex to OF's hooks, the guard's among them
  credentials    a canary in each folder the position's profile denies is unreadable from inside its sandbox
  checkout       OF's bin folder, which its hooks run, takes no write from inside its sandbox
  door           of who and of inbox --mine answer through its door; of db stats is refused there
  guard          the hook, given a Codex call that reads a key, denies it
  store          its session's start, its first prompt (the brief), a turn's end and its rollout with a tool call
                 are in the event store
  pane           of who and the relay hold its pane and session

The sandbox checks run `codex sandbox` under the position's own layer and permission profile (`-p of-<launch>
-P of_position --include-managed-config`), so no model is called and each answer is the sandbox's. Each canary is a
file of its own with a fresh random token, removed at the end whatever happened."""

import json
import os
import secrets
import subprocess
import sys

CANARY = "of-canary-"


def live_door(position, doors=None):
    """The door of POSITION's running Codex session ({launch, session, pid, cwd, ...}), or None."""
    if doors is None:
        import of_door
        doors = of_door.read_doors()
    found = [d for d in doors.values() if d.get("position") == position]
    return found[0] if len(found) == 1 else None


def seat_of(launch, seats=None):
    """The CODEX_HOME that holds the launch's layer (of-<launch>.config.toml), or None."""
    import of_codex
    if seats is None:
        import of_accounts
        seats = of_accounts.codex_seats()
    for s in seats:
        home = os.path.expanduser(s.get("home") or "")
        try:
            if os.path.isfile(of_codex.layer_path(home, launch)):
                return home
        except ValueError:
            return None
    return None


def layer_fs(home, launch):
    """The launch's permission profile's filesystem map, as its layer file holds it."""
    import tomllib
    import of_codex
    with open(of_codex.layer_path(home, launch), "rb") as fh:
        doc = tomllib.load(fh)
    return ((doc.get("permissions") or {}).get(of_codex.PROFILE) or {}).get("filesystem") or {}


def canary_dirs(fs):
    """The denied folders a canary can be put in: those that exist as real folders this user may write."""
    out = []
    for p, mode in fs.items():
        if mode == "deny" and not p.startswith(":") and os.path.isdir(p) and not os.path.islink(p) \
                and os.access(p, os.W_OK):
            out.append(p)
    return sorted(out)


def sandbox_argv(codex, launch, cwd, cmd):
    import of_codex
    return [codex, "sandbox", "-p", f"of-{launch}", "-P", of_codex.PROFILE, "-C", cwd, "--include-managed-config",
            "--", *cmd]


def _put(folder):
    token = secrets.token_hex(16)
    path = os.path.join(folder, f"{CANARY}{secrets.token_hex(6)}.txt")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(token + "\n")
    return path, token


def check_credentials(codex, door, fs, run=subprocess.run, timeout=60):
    """(ok, detail): every canary unreadable from inside the sandbox; the canaries removed whatever happened."""
    dirs = canary_dirs(fs)
    if not dirs:
        return False, "the profile denies no folder a canary can be put in"
    made, read = [], []
    try:
        for d in dirs:
            made.append(_put(d))
        for path, token in made:
            r = run(sandbox_argv(codex, door["launch"], door["cwd"], ["cat", path]), capture_output=True, text=True,
                    timeout=timeout)
            if r.returncode == 0 or token in (r.stdout or ""):
                read.append(os.path.dirname(path))
    finally:
        for path, _ in made:
            try:
                os.unlink(path)
            except OSError:
                pass
    return (not read), (f"read from inside: {', '.join(read)}" if read else f"{len(made)} canaries unreadable")


def check_checkout(codex, door, bin_dir, run=subprocess.run, timeout=60):
    """(ok, detail): a write into OF's bin folder from inside the sandbox fails and leaves no file."""
    target = os.path.join(bin_dir, f"{CANARY}{secrets.token_hex(6)}")
    try:
        r = run(sandbox_argv(codex, door["launch"], door["cwd"], ["sh", "-c", f"echo x > '{target}'"]),
                capture_output=True, text=True, timeout=timeout)
        there = os.path.exists(target)
    finally:
        try:
            os.unlink(target)
        except OSError:
            pass
    ok = r.returncode != 0 and not there
    return ok, ("refused" if ok else f"{bin_dir} took a write from inside the sandbox")


def check_door(codex, door, of_bin, run=subprocess.run, timeout=60):
    """(ok, detail): of who and of inbox --mine answer through the door, of db stats is refused there."""
    import of_codex
    import of_door
    sock = os.path.join(of_codex.door_dir(door["launch"]), of_door.SOCKET)
    env = ["env", f"OF_DOOR={sock}", of_bin]
    bad = []
    for args, want in ((["who"], True), (["inbox", "--mine"], True), (["db", "stats"], False)):
        r = run(sandbox_argv(codex, door["launch"], door["cwd"], env + args), capture_output=True, text=True,
                timeout=timeout)
        if (r.returncode == 0) != want:
            bad.append(f"of {' '.join(args)} {'failed' if want else 'was served'} ({(r.stderr or '').strip()[:120]})")
    return (not bad), ("; ".join(bad) if bad else "who and inbox served, db stats refused")


def check_guard(door, of_bin, run=subprocess.run, timeout=60):
    """(ok, detail): the hook, given the call Codex would send for a key's read, denies it."""
    import of_codex
    payload = {"session_id": door["session"], "turn_id": "of-selftest", "hook_event_name": "PreToolUse",
               "cwd": door["cwd"], "tool_name": "Bash", "tool_use_id": "of-selftest",
               "tool_input": {"command": "cat ~/.ssh/id_" + "ed25519"},
               "transcript_path": os.path.join(of_codex.door_dir(door["launch"]),
                                               "sessions/2026/01/01/rollout-of-selftest.jsonl")}
    r = run([sys.executable, of_bin, "hook", "PreToolUse"], input=json.dumps(payload), capture_output=True, text=True,
            timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    ok = "deny" in out or r.returncode == 2
    return ok, ("denied" if ok else f"not denied (exit {r.returncode})")


TOOL_ITEMS = ("function_call", "custom_tool_call", "local_shell_call")


def check_store(conn, door):
    """(ok, detail): the session's start, its first prompt, a turn's end, its rollout and a tool call in it."""
    import of_store
    sid = door["session"]
    counts = dict(conn.execute("SELECT name, COUNT(*) FROM event WHERE session_id = ? AND name IN "
                               "('of.session_start', 'of.human_prompt', 'of.turn_end') GROUP BY name", (sid,)).fetchall())
    rows = conn.execute("SELECT blob_sha256 FROM transcript_record WHERE session_id = ? AND harness = 'codex'",
                        (sid,)).fetchall()
    tools = 0
    for (sha,) in rows:
        try:
            rec = json.loads(of_store.get_blob(conn, sha))
        except Exception:  # noqa: BLE001
            continue
        p = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        if p.get("type") in TOOL_ITEMS:
            tools += 1
    missing = [n for n in ("of.session_start", "of.human_prompt", "of.turn_end") if not counts.get(n)]
    if not rows:
        missing.append("its rollout (of archive, then of import)")
    elif not tools:
        missing.append("a tool call in its rollout")
    detail = (f"{counts.get('of.human_prompt', 0)} prompts, {counts.get('of.turn_end', 0)} turn ends, {len(rows)} "
              f"rollout records, {tools} tool calls")
    return (not missing), (detail if not missing else f"missing: {', '.join(missing)} ({detail})")


def check_pane(door, who_rows, holders):
    """(ok, detail): of who shows its pane, and the relay holds its pane with this session."""
    row = next((r for r in who_rows if r.get("position") == door["position"]), None)
    key = f"{door['org']}:{door['position']}"
    h = holders.get(key) or {}
    bad = []
    if not row or not row.get("pane"):
        bad.append("of who shows no pane for it")
    if not h.get("pane") or h.get("session_id") != door["session"]:
        bad.append(f"the relay holds {h.get('session_id') or 'no session'} in {h.get('pane') or 'no pane'}")
    return (not bad), ("; ".join(bad) if bad else f"pane {h.get('pane')}, session {door['session']}")


def check_start_screen(status, box):
    """(ok, detail): while its pane shows a start screen herdr reads it blocked, and nothing is typed into it (of_herdr,
    message 01a20d6c-7b51; of_architect on hand-over 7472577f)."""
    if status == "blocked":
        return (not box), ("blocked, its input box empty" if not box else f"blocked with {len(box)} characters typed")
    return True, f"no start screen now ({status or 'no state'})"


def check_relay(conn, door, msg_id, timeout=300, sleep=None, clock=None):
    """(ok, detail): the relay typed the selftest's message into the position's input box once it was idle: an
    of.agent_message_in for that message, delivered into this session's pane."""
    import time
    sleep, clock = sleep or time.sleep, clock or time.time
    end = clock() + timeout
    while True:
        rows = conn.execute("SELECT payload FROM event WHERE name = 'of.agent_message_in' AND session_id = ? "
                            "AND payload LIKE ?", (door["session"], f"%{msg_id}%")).fetchall()
        for (payload,) in rows:
            try:
                p = json.loads(payload or "{}")
            except ValueError:
                continue
            if p.get("msg_id") == msg_id and p.get("delivery") == "delivered":
                return True, f"message {msg_id} typed into its pane"
        if clock() >= end:
            return False, f"message {msg_id} not delivered into its pane within {timeout} s"
        sleep(5)


CHECKS = ("requirements", "credentials", "checkout", "door", "guard", "store", "pane", "start_screen", "relay")


def report(results, out=None):
    """Print each check as PASS or FAIL with its detail; 0 when all passed, else 1."""
    out = out or sys.stdout
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<13} {detail}", file=out)
    failed = [n for n, ok, _ in results if not ok]
    print(f"{len(results) - len(failed)} of {len(results)} passed" + (f"; failed: {', '.join(failed)}" if failed else ""),
          file=out)
    return 1 if failed else 0
