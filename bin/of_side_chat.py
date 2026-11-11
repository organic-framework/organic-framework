"""A side chat with an asking session, through a fork of it (the asks popup, key b).

The owner can clarify a question before answering it. Each question runs as
`claude -p --resume <session> --fork-session` (later questions `--resume <fork>`), in the session's
own config dir and working directory, with the session's model. The fork sees the whole context of
the session but never touches its pane or the main agent: nothing is typed anywhere, nothing is
overwritten. It runs read-only (plan mode; no Bash, Edit, Write).

Cost: the first question of a fork writes the whole session context to the prompt cache (the
fork's system prompt and tools differ from the session's, so it cannot reuse the session's cache):
on a 540k-token Opus 5.5 session that is about $2.7 (5-minute cache) to $4.3 (1-hour cache), 12 s
to the reply. Later questions in the same fork read that cache (about $0.11) while it lives.

The run is a detached child process (`python3 of_side_chat.py run <ask_id> <session_id> <file>`)
that writes the chat itself: the history in ~/.local/state/of/asks-chat/<ask_id>.jsonl, the reply
in progress in <ask_id>.partial. So a reply finishes even when the popup closes, and reopening
the card shows the whole chat. The fork's sessions carry OF_SIDE_CHAT=<ask_id>: the recorder marks
their events and never takes them for a position's holder (no herdr pane, no HERDR_* variables).
"""

import glob
import json
import os
import subprocess
import sys
import time

READ_ONLY = ["--permission-mode", "plan", "--disallowedTools", "Bash", "Edit", "Write", "MultiEdit",
             "NotebookEdit"]


def chat_dir():
    import of_events
    d = os.path.join(of_events.STATE_DIR, "asks-chat")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def history_path(ask_id):
    return os.path.join(chat_dir(), f"{ask_id}.jsonl")


def partial_path(ask_id):
    return os.path.join(chat_dir(), f"{ask_id}.partial")


def history(ask_id):
    out = []
    try:
        with open(history_path(ask_id)) as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return out


def append(ask_id, entry):
    with open(os.open(history_path(ask_id), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def partial(ask_id):
    """(text so far, started_at) of a reply in progress, or (None, None)."""
    try:
        with open(partial_path(ask_id)) as fh:
            d = json.load(fh)
        return d.get("text") or "", d.get("started_at")
    except (OSError, ValueError):
        return None, None


def find_session(session_id):
    """(config dir, cwd, model) of a Claude Code session from its transcript, or None."""
    hits = glob.glob(os.path.expanduser(f"~/.claude*/projects/*/{session_id}.jsonl"))
    if not hits:
        return None
    path = max(hits, key=os.path.getmtime)
    cfg = path.split("/projects/")[0]
    cwd, model = None, None
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - 400_000))
        for line in fh.read().decode("utf-8", "replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            cwd = rec.get("cwd") or cwd
            m = (rec.get("message") or {}).get("model") if isinstance(rec.get("message"), dict) else None
            if m and not m.startswith("<"):
                model = m
    return cfg, cwd, model


# $ per million tokens: input, output, cache read, cache write (5 min; 1 h is 2x input)
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20, 5.0), "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
          "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125), "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5)}


def estimate_usd(model, usage):
    """This question's own cost from its tokens. The result's total_cost_usd is the forked session's
    running total (the parent's spend included), so it is never shown. None for an unknown model."""
    p = PRICES.get(model or "")
    if not p:
        return None
    return round((usage.get("input_tokens") or 0) * p[0] / 1e6 + (usage.get("output_tokens") or 0) * p[1] / 1e6
                 + (usage.get("cache_read_input_tokens") or 0) * p[2] / 1e6
                 + (usage.get("cache_creation_input_tokens") or 0) * p[3] / 1e6, 4)


def argv(session_id, question, fork_id=None, model=None):
    a = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
    a += ["--resume", fork_id] if fork_id else ["--resume", session_id, "--fork-session"]
    if model:
        a += ["--model", model]
    return a + READ_ONLY + ["--", question]


def env_for(cfg, ask_id):
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERDR_") and k not in (
        "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_ENTRYPOINT")}
    env.update(OF_AUTOMATION="1", SSH_AUTH_SOCK="", OF_SIDE_CHAT=ask_id)
    if cfg and os.path.realpath(cfg) != os.path.realpath(os.path.expanduser("~/.claude")):
        env["CLAUDE_CONFIG_DIR"] = cfg  # the default dir is meant by no variable at all
    return env


def start(ask_id, session_id, question):
    """Record the question and start the detached run. Returns a status line. Every refusal is also
    written into the chat, so it shows where the owner is looking, not in a status line that vanishes."""
    if partial(ask_id)[0] is not None:
        return "a reply is still coming; wait for it"
    append(ask_id, {"role": "you", "text": question, "ts": time.time()})
    why = None
    if not session_id:
        why = "this ask has no live session to fork (the position has no holder running)"
    elif not find_session(session_id):
        why = "no Claude Code transcript for this session (a Codex session: answer with r instead)"
    if why:
        append(ask_id, {"role": "agent", "text": f"(not asked: {why})", "ts": time.time(), "error": why})
        return why
    with open(partial_path(ask_id), "w") as fh:
        json.dump({"text": "", "started_at": time.time()}, fh)
    subprocess.Popen([sys.executable, os.path.realpath(__file__), "run", ask_id, session_id, question],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
    return "asked; the reply streams below"


def run(ask_id, session_id, question):
    """The detached child: one question to the fork, streamed into <ask_id>.partial, then the history."""
    cfg, cwd, model = find_session(session_id) or (None, None, None)
    fork_id = next((e.get("fork_session") for e in reversed(history(ask_id)) if e.get("fork_session")), None)
    started = time.time()
    text, result, err = "", {}, ""
    try:
        p = subprocess.Popen(argv(session_id, question, fork_id, model), cwd=cwd or os.path.expanduser("~"),
                             env=env_for(cfg, ask_id), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        last_write = 0.0
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            t = ev.get("type")
            if t == "stream_event":
                d = ((ev.get("event") or {}).get("delta") or {})
                if d.get("type") == "text_delta":
                    text += d.get("text") or ""
            elif t == "assistant":
                full = "".join(c.get("text", "") for c in (ev.get("message") or {}).get("content") or []
                               if isinstance(c, dict) and c.get("type") == "text")
                if full and len(full) >= len(text):
                    text = full
            elif t == "result":
                result = ev
                text = ev.get("result") or text
            if time.time() - last_write > 0.25:
                with open(partial_path(ask_id), "w") as fh:
                    json.dump({"text": text, "started_at": started}, fh)
                last_write = time.time()
        p.wait(timeout=60)
        err = (p.stderr.read() or "").strip()[-300:] if p.returncode else ""
        if p.returncode and not err:
            err = f"claude exited {p.returncode}"
    except Exception as e:  # noqa: BLE001 - the chat says what went wrong
        err = f"{type(e).__name__}: {e}"
    usage = result.get("usage") or {}
    append(ask_id, {"role": "agent", "text": text or f"(no reply: {err or 'empty'})", "ts": time.time(),
                    "fork_session": result.get("session_id") or fork_id, "cost_usd": estimate_usd(model, usage),
                    "input_tokens": usage.get("input_tokens"), "cache_read_tokens": usage.get("cache_read_input_tokens"),
                    "cache_write_tokens": usage.get("cache_creation_input_tokens"),
                    "output_tokens": usage.get("output_tokens"), "seconds": round(time.time() - started, 1),
                    "model": model, "error": err or None})
    try:
        os.remove(partial_path(ask_id))
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    if len(sys.argv) >= 5 and sys.argv[1] == "run":
        run(sys.argv[2], sys.argv[3], sys.argv[4])
