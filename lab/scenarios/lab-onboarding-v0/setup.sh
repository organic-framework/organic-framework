#!/bin/bash
# lab-onboarding-v0 setup (bench/onboarding/environments/acme.md §1, §2). Runs as the lab user inside a clean lab VM:
# the stub installed as claude, the three repositories of ~/code with the head's three agents already running (one
# per repo, in a herdr pane whose cwd is the repo, so of init detects them as storefront_dev/checkout_dev/
# warehouse_dev), and herdr running. Then run check.py. Domain and oracles: of_onboarding. Harness: of_lab.
set -u
export PATH="$HOME/.local/bin:$PATH" TERM=xterm OF_AUTOMATION=1
unset SSH_AUTH_SOCK
OFREPO="$HOME/organic-framework"; PY="$HOME/.venv-of/bin/python"

echo "== stub claude as ~/.local/bin/claude =="
install -m755 "$OFREPO/lab/stubs/claude" "$HOME/.local/bin/claude"
sed -i "1s|.*|#!$PY|" "$HOME/.local/bin/claude"

echo "== three code repos (C1/C2 need the dirs; C5 would seed git history from acme.md §2) =="
for r in storefront checkout warehouse; do
  d="$HOME/code/$r"; rm -rf "$d"; mkdir -p "$d"; git -C "$d" init -q
  echo "$r" > "$d/README.md"; git -C "$d" add -A
  git -C "$d" -c user.name=Alex -c user.email=alex@example.com -c commit.gpgsign=false commit -qm "init $r"
done

echo "== herdr, clean (a full config reset so no earlier pane restores) =="
tmux kill-server 2>/dev/null; sleep 2
rm -rf "$HOME/.config/herdr"            # herdr persists sessions; remove so nothing restores (never pkill herdr)
tmux new-session -d -s h -x 220 -y 50 "herdr; sleep 100000"
for i in $(seq 1 15); do herdr status server 2>/dev/null | grep -q "status: running" && break; sleep 1; done
herdr status server 2>&1 | grep "status:" | head -1

echo "== the three running agents, one per repo (herdr pane cwd = the repo) =="
for r in storefront checkout warehouse; do
  sid=$("$PY" -c 'import uuid;print(uuid.uuid4())')
  herdr tab create --cwd "$HOME/code/$r" --label "$r" --no-focus >/tmp/tab.json 2>&1
  pane=$("$PY" -c "import json;print(json.load(open('/tmp/tab.json'))['result']['root_pane']['pane_id'])")
  herdr pane run "$pane" "cd $HOME/code/$r && claude --session-id $sid -n $r" >/dev/null 2>&1
done
sleep 5
herdr agent list 2>&1 | "$PY" -c "import sys,json; print('  running agents:', len(json.load(sys.stdin)['result']['agents']))"

echo "== reset the of event store, trust the org folder in the account (the head's act in a real install) =="
rm -f "$HOME/.local/state/of/of.db"
"$PY" -c "import json,os; p=os.path.expanduser('~/.claude.json'); d=json.load(open(p)) if os.path.exists(p) else {}; d.setdefault('projects',{}).setdefault(os.path.expanduser('~/of/acme'),{})['hasTrustDialogAccepted']=True; json.dump(d,open(p,'w'))"
echo "ready: python3 check.py --only c1,c2"
