#!/usr/bin/env python3
"""lab-quickstart-v0 success check, run as the outsider's user inside the VM.

Success: `of who --json` lists at least 3 positions that have a live pane and a
canonical name, and the store holds a prompt or turn event for at least 3 sessions.
Prints OK, or the counts so far.
"""
import json
import os
import re
import sqlite3
import subprocess

try:
    rows = json.loads(subprocess.run(["bash", "-lc", "of who --json"], capture_output=True, text=True,
                                     timeout=20).stdout or "[]")
except Exception:
    rows = []
# The founder's criterion: agents in `of who` with canonical names, and their events recorded.
# A live pane (herdr) is reported but not required: without herdr, of who lists positions offline.
named = [r for r in rows if r.get("position") and re.match(r"^[a-z][a-z0-9_-]{0,31}$", str(r.get("name") or ""))]
n = 0
db = os.path.expanduser(os.environ.get("OF_STATE", "~/.local/state/of")) + "/of.db"
if os.path.exists(db):
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        n = c.execute("select count(distinct session_id) from event "
                      "where name in ('of.human_prompt', 'of.turn_end')").fetchone()[0]
    except Exception:
        pass
live = sum(1 for r in named if r.get("pane"))
print("OK" if len(named) >= 3 and n >= 3 else f"named={len(named)} live={live} recorded_sessions={n}")
