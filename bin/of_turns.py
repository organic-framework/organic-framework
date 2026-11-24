#!/usr/bin/env python3
"""What starts each turn of an agent's session, and what the turns it starts cost.

of_conductor's token analysis, part 3 (message 01a20e26-b693; the founder's yes, prompt 01a20e26-8701, 2026-11-24):
label every turn by what started it, and count. A turn starts at a user record that is not a tool's result, an
attachment of the turn (an image, a skill's text), a person's shell input or an interrupt mark; every response after
it, until the next start in the same transcript, is that turn's.

    python3 bin/of_turns.py [--since ISO] [--until ISO] [--by day,position,starter] [--json] [TRANSCRIPT ...]

With no transcript named it reads the transcript records this machine's store holds (`transcript_record`); with
transcripts named, those files. A session's position is the one the store's spend view names for it
(`view_spend_owner`, the listener's). It prints counts only, never a prompt's text: per key, turns, responses, tokens
and the list cost (`of_spend.response_cost`). of_capacity's per-turn data calls `turn_starter` and `frame` on each
turn's first record.

The labels, OF's own frame first, then Claude Code's own origin fields (`origin`, `turnOrigin`, `isSidechain`):
- relay: a message another position sent, typed in by the relay ("Message from <org>:<position> (via relay, ...");
- pulse, asks, handover, spawn, of_tool: a message OF's pulse, asks router, hand-over offer, spawn or another `of`
  tool typed in, by the frame's `via`; an ask routed to a unit owner ("Question from ...") is `asks` too;
- capacity_move: of_capacity's note after it moved, carried, restarted or resumed the session;
- founder_answer: the founder's answer or side question, routed in;
- unframed_message: a position's note typed in before the relay's frame existed (2026-11-05 and 06);
- peer: Claude Code's cross-session message;
- cron: a scheduled fire (a cron, a wake-up, a loop);
- background_done, subagent_done, monitor: Claude Code's notice that the session's own background command or
  subagent finished (by its task id), or that its monitor saw an event (a notice with no task id);
- subagent: a subagent's own turn (a sidechain); its `cause` is the starter of the turn that launched it;
- compaction: the summary that continues a session past its context, when it comes as a prompt (Claude Code now
  writes it visible in the transcript only, which opens no turn: the next message does, as of_derive reads it);
- limit_reset: Claude Code's continuation after a usage limit reset;
- sdk: a `claude -p` or SDK prompt;
- typed: none of these: a person typing, or a tool that typed with no frame.
"""
import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

STARTERS = ("relay", "pulse", "asks", "handover", "spawn", "of_tool", "capacity_move", "founder_answer",
            "unframed_message", "peer", "cron", "background_done", "subagent_done", "monitor", "subagent",
            "compaction", "limit_reset", "sdk", "typed")
VIA = {"relay": "relay", "of pulse": "pulse", "of asks": "asks", "of handover": "handover", "of spawn": "spawn"}

PASTE = re.compile(r'^\s*<pasted_content id="[^"]*">\s*')
FRAME = re.compile(r"^Message from (?:(?P<org>[a-z0-9][a-z0-9_-]*):)?(?P<position>[A-Za-z0-9_.-]+) \(via (?P<via>[^,)]+)")
MOVE = re.compile(r"^(?:`?of_capacity`? (?:moved|carried|restarted|resumed) this session|The Mac restarted, and of_capacity)")
ROUTED_ASK = re.compile(r"^Question from [A-Za-z0-9_.-]+ \(unit ")   # of_asks_answer: an ask routed to a unit owner
FOUNDER = ("Answer from the founder", "Side question from the founder")   # his words, routed in (of_paste.FRAMES)
NAMES = r"(?:of_[a-z-]+|organic-framework_[a-z-]+)"   # the unframed days held only OF's own positions
UNFRAMED = re.compile(r"^(?:" + NAMES + r"\s*(?:→|->|:)\s|Hand-over [0-9a-f]{8} from )")
TASK_ID = re.compile(r"<task-id>([^<]*)</task-id>")
NOT_A_TURN = ("[Request interrupted", "<bash-input>", "<bash-stdout>", "<bash-stderr>", "<local-command",
              "<command-name>", "<command-message>", "Base directory for this skill")


def text_of(rec):
    """The user record's text, or None for a record holding a tool result: it continues the turn that made the call,
    whatever text Claude Code writes beside it, such as a system reminder (as of_derive keeps it, of_review on PR 647)."""
    c = (rec.get("message") or {}).get("content") if isinstance(rec.get("message"), dict) else None
    if isinstance(c, list):
        if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
            return None
        c = " ".join(str(x.get("text") or "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return str(c or "").strip()


def frame(rec):
    """OF's message frame at the start of the record's text ({org, position, via}), or None. A message pasted into
    the pane (`<pasted_content>`) is read inside its paste."""
    t = text_of(rec)
    f = FRAME.match(PASTE.sub("", t or "", count=1))
    return {"org": f.group("org"), "position": f.group("position"), "via": f.group("via").strip()} if f else None


def turn_starter(rec):
    """What started the turn this user record opens (one of STARTERS), or None when it opens none."""
    if rec.get("type") != "user" or rec.get("isVisibleInTranscriptOnly"):
        return None
    t = text_of(rec)
    if t is None or t.startswith(NOT_A_TURN):
        return None
    o = rec.get("origin") if isinstance(rec.get("origin"), dict) else {}
    kind, turn = o.get("kind"), rec.get("turnOrigin")
    if rec.get("isSidechain"):
        return "subagent"
    if kind == "monitor" or turn == "monitor" or t.startswith("<monitor"):
        return "monitor"
    if kind == "task-notification" or turn == "task_notification" or t.startswith("<task-notification"):
        tid = TASK_ID.search(t)   # a subagent's id is `a` and hex; a background command's `b` and base 36
        if not tid:
            return "monitor"      # a monitor's event comes as a task notification with no task id (of_review on PR 647)
        return "subagent_done" if tid.group(1).startswith("a") else "background_done"
    if turn == "scheduled" or rec.get("scheduledTaskId") or t.startswith("<scheduled"):
        return "cron"
    if t.startswith(("This session is being continued", "<artifact-content-authored-by-others/>")):
        return "compaction"
    if kind == "auto-continuation" or turn == "auto_continuation" or t.startswith("Your claude.ai usage limit has reset"):
        return "limit_reset"
    if kind == "peer" or turn == "peer" or "<cross-session-message" in t[:300]:
        return "peer"
    if turn == "sdk" or rec.get("entrypoint") == "sdk-cli" or rec.get("promptSource") == "sdk":
        return "sdk"
    f = frame(rec)
    if f:
        return VIA.get(f["via"], "of_tool")
    u = PASTE.sub("", t, count=1)
    if MOVE.match(u):
        return "capacity_move"
    if u.startswith(FOUNDER):
        return "founder_answer"
    if ROUTED_ASK.match(u):
        return "asks"
    if rec.get("isMeta"):
        return None   # an attachment of the turn: an image, a skill's text
    if UNFRAMED.match(u):
        return "unframed_message"
    return "typed"


def usage_of(rec):
    """(message id, model, input, output, cache read, cache write, 5-minute write, 1-hour write) of an assistant
    record, or None."""
    m = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    u = m.get("usage") if isinstance(m.get("usage"), dict) else None
    if rec.get("type") != "assistant" or not m.get("id") or u is None:
        return None
    cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    return (m["id"], m.get("model"), u.get("input_tokens"), u.get("output_tokens"), u.get("cache_read_input_tokens"),
            u.get("cache_creation_input_tokens"), cc.get("ephemeral_5m_input_tokens"), cc.get("ephemeral_1h_input_tokens"))


def _t(ts):
    import of_model
    return of_model.epoch(ts) or 0


def session_of(path, sid):
    """A transcript's session: a subagent's file (<session>/subagents/agent-x.jsonl) is its parent's, as of_spend
    reads it (of_review on PR 647: the store may name it by the file)."""
    parts = str(path or "").split(os.sep)
    return parts[-3] if len(parts) >= 3 and parts[-2] == "subagents" else sid


def turns(records, cost=None, seen=None, since=None, until=None):
    """RECORDS: one transcript's records in order, each (ts, session, user record or None, usage or None). Yields
    each turn: {ts, session, prompt_id, starter, sender (a relayed message's), responses, input, output, cache_read,
    cache_write, cost}. A response counts once (its id, across every transcript sharing SEEN: a moved or carried
    session's copy repeats its records), to the turn open when it came; one before any turn counts to a turn
    `before_first_turn`. With SINCE or UNTIL (epochs), only the responses between them count, and only the turns that
    start between them count as turns (`counted`); a turn started before SINCE keeps its starter for its responses
    after it."""
    cost = cost or (lambda *u: 0.0)
    cur, seen = None, set() if seen is None else seen
    inside = lambda ts: (since is None or _t(ts) >= since) and (until is None or _t(ts) < until)
    for ts, session, rec, use in records:
        if rec is not None:
            s = turn_starter(rec)
            if s is None:
                continue
            if cur and (cur["counted"] or cur["responses"]):
                yield cur
            f = frame(rec) if s == "relay" else None
            cur = {"ts": ts, "session": session, "prompt_id": rec.get("promptId"), "starter": s, "counted": inside(ts),
                   "sender": f and (f"{f['org']}:{f['position']}" if f["org"] else f["position"]), "responses": 0,
                   "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0}
        elif use is not None and use[0] not in seen and inside(ts):
            seen.add(use[0])
            if cur is None:
                cur = {"ts": ts, "session": session, "prompt_id": None, "starter": "before_first_turn", "sender": None,
                       "counted": True, "responses": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                       "cost": 0.0}
            _, model, i, o, cr, cw, cw5, cw1 = use
            cur["responses"] += 1
            for k, v in (("input", i), ("output", o), ("cache_read", cr), ("cache_write", cw)):
                cur[k] += v or 0
            cur["cost"] += cost(model, i or 0, o or 0, cr or 0, cw or 0, cw5, cw1)
    if cur and (cur["counted"] or cur["responses"]):
        yield cur


def with_causes(all_turns):
    """ALL_TURNS with `cause`: a turn's own starter, or for a subagent's turn the starter of the turn that launched it,
    so a subagent's spend counts to what made its parent start one. A subagent's first record carries its parent
    turn's `promptId` in the same session (all 18 subagent transcripts on the agent host's store, 2026-11-24); one
    whose parent was not read is `subagent_unlinked`."""
    all_turns = list(all_turns)
    parent = {(t["session"], t["prompt_id"]): t["starter"] for t in all_turns
              if t["prompt_id"] and t["starter"] != "subagent"}
    for t in all_turns:
        t["cause"] = (parent.get((t["session"], t["prompt_id"]), "subagent_unlinked") if t["starter"] == "subagent"
                      else t["starter"])
    return all_turns


MARGIN_S = 2 * 86400   # records read before --since, so a turn open then keeps its starter (`turns` counts by time)


def store_records(conn, since=None, until=None):
    """This machine's store's transcript records, one transcript after another, each record as `turns` reads it;
    only user records' blobs are read. SINCE and UNTIL are epochs; the rows are read from MARGIN_S before SINCE and
    up to a day after UNTIL, since the store's timestamps are text in more than one form, and `turns` cuts by time."""
    import of_model
    import of_store
    q = ("SELECT source_path, timestamp, session_id, type, blob_sha256, message_id, model, input_tokens, output_tokens,"
         " cache_read_tokens, cache_write_tokens, cache_write_5m_tokens, cache_write_1h_tokens FROM transcript_record"
         " WHERE type IN ('user', 'assistant') AND timestamp >= ? AND timestamp < ? ORDER BY source_path, line_offset")
    lo = of_model.iso(since - MARGIN_S)[:10] if since else ""
    hi = of_model.iso(until + 86400)[:10] if until else "~"
    src, buf = None, []
    for (path, ts, sid, typ, sha, mid, model, i, o, cr, cw, cw5, cw1) in conn.execute(q, (lo, hi)):
        sid = session_of(path, sid)
        if path != src and buf:
            yield buf
            buf = []
        src = path
        if typ == "user":
            try:
                rec = json.loads(of_store.get_blob(conn, sha))
            except (TypeError, ValueError):
                continue
            buf.append((ts, sid, rec, None))
        elif mid:
            buf.append((ts, sid, None, (mid, model, i, o, cr, cw, cw5, cw1)))
    if buf:
        yield buf


def file_records(path):
    """A transcript file's records as `turns` reads them, which cuts them by time. Only a regular file is read, never
    one through a link or a pipe (of_review on PR 647); another is left out and named on stderr."""
    import stat
    try:
        st = os.lstat(path)
    except OSError as exc:
        print(f"of_turns.py: {path}: {exc.strerror}", file=sys.stderr)
        return []
    if not stat.S_ISREG(st.st_mode):
        print(f"of_turns.py: {path}: not a regular file, left out", file=sys.stderr)
        return []
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            ts, sid = str(rec.get("timestamp") or ""), session_of(path, rec.get("sessionId"))
            if rec.get("type") == "user":
                out.append((ts, sid, rec, None))
            elif usage_of(rec):
                out.append((ts, sid, None, usage_of(rec)))
    return out


def positions(conn):
    """{session: the position the store's spend view names most for it}."""
    best = {}
    try:
        rows = conn.execute("SELECT session_id, position, n FROM view_spend_owner").fetchall()
    except Exception:   # an older store: no spend view yet
        return {}
    for sid, pos, n in rows:
        if pos and (sid not in best or n > best[sid][1]):
            best[sid] = (pos, n)
    return {sid: p for sid, (p, _) in best.items()}


def count(all_turns, by, pos_of):
    """{key: {turns, responses, input, output, cache_read, cache_write, cost}} over the keys BY names (day, position,
    starter, cause, sender)."""
    out = collections.defaultdict(collections.Counter)
    for t in all_turns:
        vals = {"day": str(t["ts"] or "")[:10], "position": pos_of.get(t["session"], "?"), "starter": t["starter"],
                "cause": t.get("cause") or t["starter"], "sender": t.get("sender") or "-"}
        c = out[tuple(vals[k] for k in by)]
        c["turns"] += 1 if t.get("counted", True) else 0
        for k in ("responses", "input", "output", "cache_read", "cache_write", "cost"):
            c[k] += t[k]
    return out


def table(out, by):
    rows = sorted(out.items(), key=lambda kv: -kv[1]["cost"])
    total = sum(c["cost"] for _, c in rows)
    head = list(by) + ["turns", "responses", "per turn", "cost", "share", "cache write M", "cache read M"]
    body = [list(k) + [c["turns"], c["responses"], f'{c["responses"] / max(1, c["turns"]):.1f}', f'{c["cost"]:.0f}',
                       f'{c["cost"] / (total or 1):.1%}', f'{c["cache_write"] / 1e6:.1f}', f'{c["cache_read"] / 1e6:.1f}']
            for k, c in rows]
    w = [max(len(str(x)) for x in col) for col in zip(head, *body)]
    lines = ["  ".join(str(x).ljust(n) for x, n in zip(r, w)).rstrip() for r in [head] + body]
    lines.append(f'{sum(c["turns"] for _, c in rows)} turns · {sum(c["responses"] for _, c in rows)} responses · '
                 f'{total:.0f} at list prices')
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="of_turns.py", description="what starts each turn, and what its turns cost")
    ap.add_argument("transcripts", nargs="*", help="transcript files; none: this machine's store")
    ap.add_argument("--since", help="ISO time, inclusive")
    ap.add_argument("--until", help="ISO time, exclusive")
    ap.add_argument("--by", default="starter", help="comma-separated: day, position, starter, cause, sender")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    by = tuple(x.strip() for x in a.by.split(",") if x.strip())
    bad = [x for x in by if x not in ("day", "position", "starter", "cause", "sender")]
    if bad:
        ap.error(f"--by takes day, position, starter, cause, sender, not {', '.join(bad)}")
    import of_spend
    import of_store
    conn = of_store.connect()
    pos_of = positions(conn)
    import of_model
    since, until = (of_model.epoch(x) if x else None for x in (a.since, a.until))
    if (a.since and since is None) or (a.until and until is None):
        ap.error("--since and --until take an ISO time")
    sources = ([file_records(p) for p in a.transcripts] if a.transcripts else store_records(conn, since, until))
    seen = set()
    out = count(with_causes(t for recs in sources for t in turns(recs, of_spend.response_cost, seen, since, until)),
                by, pos_of)
    if a.json:
        print(json.dumps([dict(zip(by, k), **{x: round(v, 4) if isinstance(v, float) else v for x, v in c.items()})
                          for k, c in sorted(out.items())], indent=1))
    else:
        print(table(out, by))
    return 0


if __name__ == "__main__":
    sys.exit(main())
