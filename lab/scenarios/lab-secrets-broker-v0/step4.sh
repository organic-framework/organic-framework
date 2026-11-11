#!/bin/bash
PY=/home/lab/.venv-of/bin/python
db=$(ls /home/lab/server-state/*.db 2>/dev/null | head -1); echo "server.db: $db"
"$PY" - "$db" <<PY
import sqlite3,sys
c=sqlite3.connect(sys.argv[1])
print("secret_uses columns:", [r[1] for r in c.execute("pragma table_info(secret_uses)")])
print("rows (machine, position, decision, reason):")
for r in c.execute("select substr(machine_id,1,8), position, decision, reason from secret_uses order by at"): print("  ", r)
PY
echo "fake op reads.log (token-last4, vault, ok - no value):"; cat /home/lab/op/reads.log 2>/dev/null | tail -8
