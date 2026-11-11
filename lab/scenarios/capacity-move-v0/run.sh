#!/bin/bash
# capacity-move-v0 (hand-over 952e35bc from of_capacity; docs/capacity.md, "The move").
# Runs INSIDE a lab VM as the user `lab`, with the framework at ~/organic-framework and the
# stub harness at ~/.local/bin/claude. Two config dirs, ~/.claude-a and ~/.claude-b, are
# "logged in" with synthetic accounts (a real OAuth login is impossible in a VM). A live
# holder in a herdr pane is moved from a to b with `of capacity move`.
# Output: one PASS/FAIL line per check, then a JSON summary.
set -u
export PATH="$HOME/.local/bin:$PATH" OF_AUTOMATION=1
unset SSH_AUTH_SOCK
OF="$HOME/organic-framework/bin/of"
SID=$(python3 -c 'import uuid; print(uuid.uuid4())')
NAME=lab_mover; A=$HOME/.claude-a; B=$HOME/.claude-b; WORK=$HOME/work
KEY=$(echo "$WORK" | sed 's|[/.]|-|g')
RES=$HOME/capacity-move-results.txt; : > "$RES"
pass() { echo "PASS $1" | tee -a "$RES"; }
fail() { echo "FAIL $1${2:+: $2}" | tee -a "$RES"; }
chk() { if eval "$2"; then pass "$1"; else fail "$1" "${3:-}"; fi; }

# --- 1. two logged-in config dirs, an organization, a herdr server
pkill -f "herdr server" 2>/dev/null; pkill -f "/.local/bin/claude" 2>/dev/null; sleep 1
rm -rf "$A" "$B" "$WORK" "$HOME/of/lab-cap" "$HOME/.local/state/of" "$HOME/.config/of" "$HOME/.config/herdr"
mkdir -p "$WORK" "$HOME/of/lab-cap/registry" "$HOME/.config/of"
for d in a b; do
  mkdir -p "$HOME/.claude-$d"
  echo "{\"oauthAccount\": {\"emailAddress\": \"$d@lab.invalid\"}, \"hasCompletedOnboarding\": true}" > "$HOME/.claude-$d/.claude.json"
  echo '{}' > "$HOME/.claude-$d/settings.json"
  "$OF" hooks install --config-dir "$HOME/.claude-$d" --yes >/dev/null 2>&1 || "$OF" hooks install --config-dir "$HOME/.claude-$d" < /dev/null >/dev/null 2>&1
done
cat > "$HOME/of/lab-cap/registry/positions.yaml" <<EOF
tenant: lab
organization: lab-cap
positions:
  - name: $NAME
    system: lab
    role: mover
    kind: project
    throughput: raise
    owner: founder
    summary: "capacity move subject"
    status: active
    holder:
      runtime: claude-code
      session: $SID
      workplace: herdr lab, workspace lab-cap
    aliases: []
EOF
printf 'orgs:\n  - name: lab-cap\n    path: ~/of/lab-cap\n    workspace: lab-cap\n' > "$HOME/.config/of/orgs.yaml"
( herdr server > "$HOME/herdr-server.log" 2>&1 & ); sleep 2
PANE=$(herdr workspace create --label lab-cap --cwd "$WORK" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["root_pane"]["pane_id"])')
herdr pane run "$PANE" "cd $WORK && CLAUDE_CONFIG_DIR=$A claude --session-id $SID -n $NAME --model claude-haiku-5-5" >/dev/null
sleep 3
# herdr infers a stub's state from the screen, so wait on the session file, as of capacity move reads it
idle_a() { for i in $(seq 1 120); do grep -qs '"status": "idle"' "$1"/sessions/*.json && return 0; sleep 0.5; done; return 1; }
P() { herdr agent prompt "$PANE" "$1" >/dev/null 2>&1; sleep 0.5; idle_a "$A"; }

# --- 2. a working session: turns, a subagent, memory, a cron
P "start task alpha: refactor the parser"
P "!subagent"
P "!remember notes.md alpha side: the parser uses a Pratt loop"
P "!remember MEMORY.md - [Parser notes](notes.md) - alpha"
P "!remember only-a.md written only on a"
P "!cron check CI in 10 minutes"
# the target already has its own memory for the project: one file changed on both sides
mkdir -p "$B/projects/$KEY/memory"
echo "- [Deploy notes](deploy.md) - beta" > "$B/projects/$KEY/memory/MEMORY.md"
echo "beta side: the parser is generated" > "$B/projects/$KEY/memory/notes.md"
chk "setup: live session in a" "ls $A/sessions/*.json >/dev/null 2>&1"
chk "setup: herdr binds the pane to the session" "herdr agent list | grep -q $SID"

# --- 3. refusals: a busy session, then a draft in the input box
herdr agent prompt "$PANE" "!sleep 20" >/dev/null 2>&1; sleep 3
out=$("$OF" capacity move $NAME --to "$B" 2>&1)
chk "refuses a busy session" "echo \"\$out\" | grep -qi busy" "$out"
idle_a "$A"; sleep 1
herdr pane send-text "$PANE" "half-typed draft" >/dev/null; sleep 1
out=$("$OF" capacity move $NAME --to "$B" 2>&1)
chk "refuses a draft in the input box" "echo \"\$out\" | grep -qi draft" "$out"
chk "after refusals the session is still in a" "ls $A/sessions/*.json >/dev/null 2>&1 && ! ls $B/sessions/*.json >/dev/null 2>&1"
herdr pane send-keys "$PANE" ctrl+u >/dev/null; sleep 1

# --- 3c. refuses an untrusted target folder (same safety as of spawn: the owner decides, never the dialog)
out=$("$OF" capacity move $NAME --to "$B" 2>&1)
chk "refuses an untrusted target folder" "echo \"\$out\" | grep -qiE 'trusted in no config|owner decides'" "$out"
chk "untrusted refusal did not move" "ls $A/sessions/*.json >/dev/null 2>&1 && ! ls $B/sessions/*.json >/dev/null 2>&1"
# the owner trusted this folder on the source account earlier; the move carries that trust to the target
python3 - "$A/.claude.json" "$WORK" <<'PY'
import json, sys
p, work = sys.argv[1], sys.argv[2]
d = json.load(open(p)); d.setdefault("projects", {})[work] = {"hasTrustDialogAccepted": True}
json.dump(d, open(p, "w"))
PY

# --- 4. the move
out=$("$OF" capacity move $NAME --to "$B" 2>&1); rc=$?
echo "$out" > "$HOME/capacity-move-output.txt"
chk "move succeeds" "[ $rc -eq 0 ]" "$(echo "$out" | tail -3)"
sleep 4
chk "same session id resumes on b" "grep -l '\"sessionId\": \"$SID\"' $B/sessions/*.json >/dev/null 2>&1"
chk "under the canonical name" "grep -h '\"name\"' $B/sessions/*.json | grep -q $NAME"
chk "in the same pane" "herdr agent list | python3 -c \"import json,sys; a=[x for x in json.load(sys.stdin)['result']['agents'] if (x.get('agent_session') or {}).get('value')=='$SID']; sys.exit(0 if a and a[0]['pane_id']=='$PANE' else 1)\""
chk "old process gone from a" "! ls $A/sessions/*.json >/dev/null 2>&1"
chk "transcript carried" "[ -s $B/projects/$KEY/$SID.jsonl ]"
chk "subagent transcripts carried" "ls $B/projects/$KEY/$SID/subagents/*.jsonl >/dev/null 2>&1"
chk "file-history carried" "ls $B/file-history/$SID/* >/dev/null 2>&1"
chk "session-env carried" "ls $B/session-env/$SID/* >/dev/null 2>&1"
chk "memory: file only on a copied" "[ -f $B/projects/$KEY/memory/only-a.md ]"
chk "memory: MEMORY.md merged by union" "grep -q alpha $B/projects/$KEY/memory/MEMORY.md && grep -q beta $B/projects/$KEY/memory/MEMORY.md"
chk "memory: notes.md changed on both sides kept target" "grep -q 'beta side' $B/projects/$KEY/memory/notes.md && ! grep -q 'alpha side' $B/projects/$KEY/memory/notes.md"
chk "memory: conflict copy saved" "ls $HOME/.local/state/of/capacity/moves/$NAME-*/conflicts/notes.md >/dev/null 2>&1"
chk "hand-over note lists the cron" "grep -qi 'check CI' $HOME/.local/state/of/capacity/moves/$NAME-*/note.md"
chk "of.holder_changed written" "sqlite3 $HOME/.local/state/of/of.db \"select count(*) from event where name='of.holder_changed' and position='$NAME'\" | grep -qv '^0$'"
# The account is runtime state, not structure (both registries dropped it on 2026-11-06); of capacity never
# edits a registry (single writer: the structure keeper). So an account-only move implies no registry change.
chk "account-only move makes no registry change (account is runtime state)" "! grep -qi 'account' $HOME/of/lab-cap/registry/positions.yaml"
chk "trust carried to b" "python3 -c \"import json,sys; d=json.load(open('$B/.claude.json')); sys.exit(0 if (d.get('projects',{}).get('$WORK') or {}).get('hasTrustDialogAccepted') else 1)\""
idle_a "$B"; sleep 1
chk "resumed holder answers with its context" "herdr pane read $PANE --source recent --lines 30 | grep -q 'I was working on:'"

python3 - "$RES" <<'PY'
import json, sys
lines = [l.strip() for l in open(sys.argv[1]) if l.strip()]
print(json.dumps({"checks": len(lines), "passed": sum(l.startswith("PASS") for l in lines),
                  "failed": [l[5:] for l in lines if l.startswith("FAIL")]}, indent=1))
PY
