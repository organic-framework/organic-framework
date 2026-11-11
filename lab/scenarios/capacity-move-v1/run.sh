#!/bin/bash
# capacity-move-v1 (hand-over 4dbaf795 from of_capacity; docs/capacity.md, "The move" and "resume").
# Extends capacity-move-v0 with: a declared-injected hand-over note checked against the injections log, a HELD start
# (the stub sits on the folder trust dialog, the move fails naming it and types nothing), `of capacity resume` (success
# and its three refusals), and the positions.yaml sha256 invariant across every move and resume.
#
# Runs INSIDE a lab VM as the user `lab`, with the framework at ~/organic-framework and the stub harness at
# ~/.local/bin/claude. Two config dirs ~/.claude-a and ~/.claude-b are "logged in" with synthetic accounts (a real
# OAuth login is impossible in a VM). OF_CAPACITY_START_WAIT_S is set to a few seconds so a held start times out fast.
# Output: one PASS/FAIL line per check, then a JSON summary.
set -u
export PATH="$HOME/.local/bin:$PATH" OF_AUTOMATION=1 OF_CAPACITY_START_WAIT_S=4
unset SSH_AUTH_SOCK
OF="$HOME/organic-framework/bin/of"
SID=$(python3 -c 'import uuid; print(uuid.uuid4())')
OTHER=$(python3 -c 'import uuid; print(uuid.uuid4())')
NAME=lab_mover; A=$HOME/.claude-a; B=$HOME/.claude-b; WORK=$HOME/work
KEY=$(echo "$WORK" | sed 's|[/.]|-|g')
REG=$HOME/of/lab-cap/registry/positions.yaml
DB=$HOME/.local/state/of/of.db
RES=$HOME/capacity-move-v1-results.txt; : > "$RES"
pass() { echo "PASS $1" | tee -a "$RES"; }
fail() { echo "FAIL $1${2:+: $2}" | tee -a "$RES"; }
chk() { if eval "$2"; then pass "$1"; else fail "$1" "${3:-}"; fi; }
sha() { sha256sum "$REG" | cut -d' ' -f1; }
trust() { python3 - "$1" "$WORK" "$2" <<'PY'
import json, sys
p, work, how = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(p)); proj = d.setdefault("projects", {})
if how == "on": proj.setdefault(work, {})["hasTrustDialogAccepted"] = True
elif how == "off": proj.pop(work, None)
json.dump(d, open(p, "w"))
PY
}
trusted() { python3 -c "import json,sys;d=json.load(open('$1'));sys.exit(0 if (d.get('projects',{}).get('$WORK') or {}).get('hasTrustDialogAccepted') else 1)"; }
fg_claude() { herdr pane process-info --pane "$1" 2>/dev/null | python3 -c "import json,sys;i=json.load(sys.stdin).get('process_info',{});sys.exit(0 if any('claude' in ' '.join(f.get('argv') or []) for f in (i.get('foreground_processes') or [])) else 1)"; }
# latest of.human_prompt origin for a session, with of.prompt_origin corrections applied
hp_origin() { python3 - "$DB" "$1" <<'PY'
import sqlite3, json, sys
db, sid = sys.argv[1], sys.argv[2]
c = sqlite3.connect(db)
rows = [(e, json.loads(p or '{}')) for e, p in c.execute(
    "select event_id, payload from event where name='of.human_prompt' and session_id=? order by ts", (sid,))]
corr = {json.loads(p)['event_id']: json.loads(p)['origin'] for _, p in
        [(e, p) for e, p in c.execute("select event_id, payload from event where name='of.prompt_origin'")]
        if json.loads(p).get('event_id')}
if not rows: print("none"); sys.exit()
eid, pl = rows[-1]
print(corr.get(eid, pl.get('origin')))
PY
}
# does the injections log hold an of_capacity mark for this session whose sha256 is the latest note's text,
# with its ts at or before the note's of.human_prompt (i.e. declared before the start)?
inj_before_start() { python3 - "$DB" "$1" "$HOME/.local/state/of/injections.jsonl" <<'PY'
import sqlite3, json, sys, hashlib
db, sid, path = sys.argv[1], sys.argv[2], sys.argv[3]
c = sqlite3.connect(db)
rows = [(t, json.loads(p or '{}')) for t, p in c.execute(
    "select ts, payload from event where name='of.human_prompt' and session_id=? order by ts", (sid,))]
rows = [(t, pl) for t, pl in rows if pl.get('injector') == 'of_capacity' or pl.get('origin') == 'injected']
if not rows: print("no injected note human_prompt"); sys.exit(1)
ts_note, pl = rows[-1]
digest = hashlib.sha256((pl.get('text') or '').strip().encode()).hexdigest()
def ep(s):
    import datetime as d
    try: return d.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
    except Exception: return float(s)
marks = [json.loads(l) for l in open(path)] if __import__('os').path.exists(path) else []
mine = [m for m in marks if m.get('session') == sid and m.get('injector') == 'of_capacity' and m.get('sha256') == digest]
if not mine: print("no of_capacity mark with the note sha256"); sys.exit(1)
if min(m['ts'] for m in mine) <= ep(ts_note) + 1: print("ok"); sys.exit(0)
print("mark is not before the start"); sys.exit(1)
PY
}

# --- 0. the stub harness as ~/.local/bin/claude (the real Claude Code cannot log in inside a VM).
# Rewrite its shebang to the venv Python, as provision.sh does for `of`, so the kernel can run it.
install -d "$HOME/.local/bin"
cp "$HOME/organic-framework/lab/stubs/claude" "$HOME/.local/bin/claude"
sed -i "1s|.*|#!$HOME/.venv-of/bin/python|" "$HOME/.local/bin/claude"
chmod +x "$HOME/.local/bin/claude"

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
cat > "$REG" <<EOF
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
# the owner trusted this folder on both accounts earlier (their decision); the tool never presses the dialog
trust "$A/.claude.json" on; trust "$B/.claude.json" on
herdr pane run "$PANE" "cd $WORK && CLAUDE_CONFIG_DIR=$A claude --session-id $SID -n $NAME --model claude-haiku-5-5" >/dev/null
sleep 3
idle() { for i in $(seq 1 120); do grep -qs '"status": "idle"' "$1"/sessions/*.json 2>/dev/null && return 0; sleep 0.5; done; return 1; }
# of capacity move reads the pane's herdr agent_status, which lags the session file after a `herdr pane run`
# (a resume rebinds the agent and it reads "unknown" until the stub reports): wait for herdr to show the session idle.
hidle() { for i in $(seq 1 80); do herdr agent list 2>/dev/null | python3 -c "import json,sys;a=[x for x in json.load(sys.stdin).get('result',{}).get('agents',[]) if (x.get('agent_session') or {}).get('value')=='$1'];sys.exit(0 if a and a[0].get('agent_status') in ('idle','done') else 1)" && return 0; sleep 0.5; done; return 1; }
# of capacity reads the pane's VISIBLE screen (herdr pane read --source visible) to spot a dialog; a trust dialog the
# held start left behind lingers there and misleads a later resume. Clear the visible screen between stub lifecycles.
pclear() { herdr pane run "$1" clear >/dev/null 2>&1; sleep 1; }
P() { herdr agent prompt "$PANE" "$1" >/dev/null 2>&1; sleep 0.5; idle "$A"; }
idle "$A"
P "start task alpha: refactor the parser"
P "!remember notes.md alpha side"
SHA0=$(sha)
chk "setup: live session in a" "ls $A/sessions/*.json >/dev/null 2>&1"

# ================= CHECK 1: a plain move, the note declared injected before the start =================
hidle "$SID"
out=$("$OF" capacity move $NAME --to "$B" 2>&1); rc=$?
echo "$out" > "$HOME/v1-move1.txt"
chk "1: plain move succeeds" "[ $rc -eq 0 ]" "$(echo "$out" | tail -3)"
sleep 4; idle "$B"; sleep 1
chk "1: same session id resumes on b" "grep -l '\"sessionId\": \"$SID\"' $B/sessions/*.json >/dev/null 2>&1"
chk "1: note of.human_prompt origin is injected" "[ \"\$(hp_origin $SID)\" = injected ]" "$(hp_origin $SID)"
chk "1: of_capacity mark + note sha256 in injections log before the start" "inj_before_start $SID" "$(inj_before_start $SID)"
chk "1: of.holder_changed evidence 'of capacity move'" "sqlite3 $DB \"select count(*) from event where name='of.holder_changed' and position='$NAME' and payload like '%of capacity move%'\" | grep -qv '^0$'"
chk "5: positions.yaml sha256 unchanged by the move" "[ \"\$(sha)\" = \"$SHA0\" ]"
HP_BEFORE=$(sqlite3 "$DB" "select count(*) from event where name='of.human_prompt' and session_id='$SID'")

# ================= CHECK 2: a held start — the stub sits on the trust dialog, the move fails naming it =================
# b already trusts the folder, so the move carries nothing and leaves trust unchanged; the marker makes the stub show
# the dialog anyway (Claude Code may re-prompt even on a carried folder).
touch "$A/.lab-stub-trust-dialog"
trust "$A/.claude.json" on            # a already trusts: ensure_trust writes nothing
A_TRUST_JSON_BEFORE=$(cat "$A/.claude.json")
hidle "$SID"                          # the b session resumed in check 1: wait for herdr to show it idle before moving
out=$("$OF" capacity move $NAME --to "$A" 2>&1); rc=$?
echo "$out" > "$HOME/v1-move2-held.txt"
sleep 2
chk "2: held move fails" "[ $rc -ne 0 ]" "$(echo "$out" | tail -2)"
chk "2: failure names the folder trust dialog" "echo \"\$out\" | grep -qi 'trust dialog'" "$out"
chk "2: no live session on a (stub exited after Esc)" "! ls $A/sessions/*.json >/dev/null 2>&1"
chk "2: the pane is back at its shell (no claude foreground)" "! fg_claude $PANE"
chk "2: a .claude.json trust unchanged by the tool" "[ \"\$(cat $A/.claude.json)\" = \"\$A_TRUST_JSON_BEFORE\" ]"
chk "2: no Enter/Yes was sent (dialog marker still present)" "[ -f $A/.lab-stub-trust-dialog ]"
HP_AFTER=$(sqlite3 "$DB" "select count(*) from event where name='of.human_prompt' and session_id='$SID'")
chk "2: no of.human_prompt carries the note (none added)" "[ \"$HP_AFTER\" = \"$HP_BEFORE\" ]" "$HP_BEFORE -> $HP_AFTER"
chk "5: positions.yaml sha256 unchanged by the held move" "[ \"\$(sha)\" = \"$SHA0\" ]"
# the held move exited the old session and copied files to a; the session is now down, its files on a.
# The dialog marker has served check 2; remove it so the rest sees a normal start (the owner decided the trust).
rm -f "$A/.lab-stub-trust-dialog"
pclear "$PANE"                         # wipe the held start's dialog text from the visible screen

# ================= CHECK 4b: resume refuses a pane where a claude process runs with no trust dialog =================
herdr pane run "$PANE" "cd $WORK && CLAUDE_CONFIG_DIR=$A claude --session-id $OTHER -n other --model claude-haiku-5-5" >/dev/null
sleep 2
out=$("$OF" capacity resume $NAME --session "$SID" --to "$A" --pane "$PANE" 2>&1); rc=$?
chk "4b: resume refuses a pane with a running claude (no dialog)" "[ $rc -ne 0 ] && echo \"\$out\" | grep -qi 'shell'" "$out"
chk "4b: it typed nothing (session still down)" "! ls $A/sessions/*.json 2>/dev/null | grep -q . ; ! grep -ls \"$SID\" $A/sessions/*.json >/dev/null 2>&1"
chk "5: positions.yaml sha256 unchanged by the 4b refusal" "[ \"\$(sha)\" = \"$SHA0\" ]"
herdr agent prompt "$PANE" "/exit" >/dev/null 2>&1; sleep 2
for i in $(seq 1 30); do fg_claude "$PANE" || break; sleep 0.5; done

# ================= CHECK 4c: resume refuses a folder the target does not trust =================
# b holds the session's transcript (from check 1). Drop b's trust for the folder: resume --to b refuses untrusted.
trust "$B/.claude.json" off
out=$("$OF" capacity resume $NAME --session "$SID" --to "$B" --pane "$PANE" 2>&1); rc=$?
chk "4c: resume refuses an untrusted target folder" "[ $rc -ne 0 ] && echo \"\$out\" | grep -qiE 'not trusted|owner decides'" "$out"
chk "4c: it typed nothing (b has no live session)" "! ls $B/sessions/*.json >/dev/null 2>&1"
chk "4c: the untrusted folder's trust was not granted" "! trusted $B/.claude.json"
chk "5: positions.yaml sha256 unchanged by the 4c refusal" "[ \"\$(sha)\" = \"$SHA0\" ]"
trust "$B/.claude.json" on

# ================= CHECK 3: of capacity resume, the hold removed =================
rm -f "$A/.lab-stub-trust-dialog"     # the owner decided the trust; the dialog no longer holds the start
pclear "$PANE"                        # 4b's throwaway claude left its banner; clear before the real resume
out=$("$OF" capacity resume $NAME --session "$SID" --to "$A" --pane "$PANE" 2>&1); rc=$?
echo "$out" > "$HOME/v1-resume.txt"
chk "3: resume succeeds" "[ $rc -eq 0 ]" "$(echo "$out" | tail -3)"
sleep 4; idle "$A"; sleep 1
chk "3: same session id resumes on a" "grep -l '\"sessionId\": \"$SID\"' $A/sessions/*.json >/dev/null 2>&1"
chk "3: under the canonical name" "grep -h '\"name\"' $A/sessions/*.json | grep -q $NAME"
chk "3: in the same pane" "herdr agent list | python3 -c \"import json,sys; a=[x for x in json.load(sys.stdin)['result']['agents'] if (x.get('agent_session') or {}).get('value')=='$SID']; sys.exit(0 if a and a[0]['pane_id']=='$PANE' else 1)\""
chk "3: resume note origin is injected" "[ \"\$(hp_origin $SID)\" = injected ]" "$(hp_origin $SID)"
chk "3: of.holder_changed evidence 'of capacity resume'" "sqlite3 $DB \"select count(*) from event where name='of.holder_changed' and position='$NAME' and payload like '%of capacity resume%'\" | grep -qv '^0$'"
chk "5: positions.yaml sha256 unchanged by the resume" "[ \"\$(sha)\" = \"$SHA0\" ]"

# ================= CHECK 4a: resume refuses a session that is live =================
out=$("$OF" capacity resume $NAME --session "$SID" --to "$A" --pane "$PANE" 2>&1); rc=$?
chk "4a: resume refuses a live session (use move)" "[ $rc -ne 0 ] && echo \"\$out\" | grep -qiE 'is live|use .*capacity move'" "$out"
chk "5: positions.yaml sha256 unchanged after every move and resume" "[ \"\$(sha)\" = \"$SHA0\" ]"

python3 - "$RES" <<'PY'
import json, sys
lines = [l.strip() for l in open(sys.argv[1]) if l.strip()]
print(json.dumps({"checks": len(lines), "passed": sum(l.startswith("PASS") for l in lines),
                  "failed": [l[5:] for l in lines if l.startswith("FAIL")]}, indent=1))
PY
