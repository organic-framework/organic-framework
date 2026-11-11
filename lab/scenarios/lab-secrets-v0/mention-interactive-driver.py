#!/usr/bin/env python3
"""Drive an interactive Claude Code under a pty, feed one @file-mention prompt, capture the TUI output.
Answers: does the mentioned file's content reach the session, and does the UserPromptSubmit guard block it
(i.e. does the hook fire before the @-expansion)? Args: <workdir> <prompt> <out-file>. Canary only."""
import os
import pty
import re
import select
import sys
import time

work, prompt, outfile = sys.argv[1], sys.argv[2], sys.argv[3]
env = dict(os.environ)
# a nested interactive agent must not re-bind a herdr pane (AGENTS.md); run it free of herdr
for k in ("HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID"):
    env.pop(k, None)
argv = ["claude", "--setting-sources", "project"]

pid, fd = pty.fork()
if pid == 0:
    os.chdir(work)
    os.environ.clear()
    os.environ.update(env)
    os.environ.update(TERM="xterm-256color", LINES="40", COLUMNS="120")
    os.execvp(argv[0], argv)

buf = bytearray()
deadline = time.time() + 120
sent_trust = sent_prompt = False
trust_at = submit_at = None


def drain():
    while True:
        r, _, _ = select.select([fd], [], [], 0.4)
        if not r:
            return
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            raise SystemExit
        if not chunk:
            raise SystemExit
        buf.extend(chunk)


try:
    while time.time() < deadline:
        drain()
        text = buf.decode("utf-8", "replace")
        plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07", "", text)
        low = plain.lower()
        tail = plain[-800:].lower()
        # the trust dialog: "Quick safety check: Is this a project you created or one you trust?"
        if not sent_trust and ("safety check" in tail or "one you trust" in tail or "do you trust" in tail):
            os.write(fd, b"\r")  # accept the highlighted default (Yes, proceed)
            sent_trust = True
            trust_at = time.time()
            time.sleep(3)
            continue
        # ready for input: the composer shows "? for shortcuts" / a "│ >" box. Only after trust (if any) settled.
        ready = ("shortcuts" in tail or "│ >" in plain[-400:]) and (sent_trust or time.time() - (trust_at or 0) > 0)
        past_trust = (not sent_trust and ("safety check" not in tail and "trust" not in tail) and time.time() > deadline - 100)
        if not sent_prompt and (ready and (sent_trust or trust_at is None) and time.time() - (trust_at or 0) > 2):
            time.sleep(2)
            os.write(fd, prompt.encode())
            time.sleep(1.5)
            os.write(fd, b"\r")
            sent_prompt = True
            submit_at = time.time()
            continue
        if sent_prompt and submit_at and time.time() - submit_at > 35:
            break
    time.sleep(1)
    drain()
finally:
    try:
        os.write(fd, b"\x03")
        time.sleep(0.3)
        os.write(fd, b"\x03")
    except OSError:
        pass
    open(outfile, "wb").write(bytes(buf))
    print(f"captured {len(buf)} bytes; sent_trust={sent_trust} sent_prompt={sent_prompt}")
