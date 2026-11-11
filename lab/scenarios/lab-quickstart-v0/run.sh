#!/bin/bash
# lab-quickstart-v0, one seed (docs/lab.md, section 12). Runs on the Mac; light.
# The outsider persona is a headless Claude Code session in the Lab's own config dir.
# Its only tool is the `vm` wrapper (commands run inside the lab VM as `dev`), plus web
# search. It cannot read or write the Mac's files.
# Usage: run.sh <seed> <arm: herdr|no-herdr> [model]
set -eu
SEED="${1:?seed}"; ARM="${2:?arm}"; MODEL="${3:-haiku}"
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../../.." && pwd)"
VM="of-lab-qs"
RUN="$HOME/.local/state/of/lab/runs/$(date -u +%Y-%m-%d)-quickstart-$ARM-s$SEED"
mkdir -p "$RUN/bin"
# isolation (docs/lab.md, section 9)
unset SSH_AUTH_SOCK HERDR_ENV HERDR_PANE_ID HERDR_TAB_ID HERDR_WORKSPACE_ID HERDR_SOCKET_PATH
unset CLAUDECODE CLAUDE_CODE_SESSION_ID CLAUDE_CODE_CHILD_SESSION CLAUDE_CODE_MESSAGING_SOCKET CLAUDE_CODE_MESSAGING_TOKEN CLAUDE_PID CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_EXECPATH CLAUDE_CODE_SESSION_ATTENDED
# The founder's example account (founder, 2026-11-06), used without touching its setup: no user
# settings or hooks (--setting-sources project), no MCP servers, no saved session.
export OF_AUTOMATION=1 GIT_CONFIG_GLOBAL=/dev/null CLAUDE_CONFIG_DIR="${OFLAB_CLAUDE_DIR:-$HOME/.claude-e}"

# 1. a fresh machine for every seed
limactl delete --force "$VM" >/dev/null 2>&1 || true
# wait for headroom (production agents come first): up to 30 minutes
for i in $(seq 1 30); do "$REPO/lab/bin/oflab" preflight && break; [ "$i" = 30 ] && exit 3; sleep 60; done
limactl create --tty=false --name "$VM" "$REPO/lab/image/lab.yaml" >/dev/null
limactl start --tty=false "$VM" >/dev/null
(cd "$REPO" && COPYFILE_DISABLE=1 tar --no-xattrs --exclude='__pycache__' --exclude='.git' -czf "$RUN/framework.tgz" bin docs README.md AGENTS.md tests)
limactl copy "$RUN/framework.tgz" "$VM:/tmp/framework.tgz"
limactl copy "$REPO/lab/stubs/claude" "$VM:/tmp/stub-claude"
limactl copy "$HERE/setup.sh" "$VM:/tmp/setup.sh"
limactl copy "$HERE/check.py" "$VM:/tmp/check.py"
limactl copy "$REPO/lab/image/lockdown-internet.sh" "$VM:/tmp/lockdown.sh"
limactl shell "$VM" sudo sh /tmp/setup.sh /tmp/framework.tgz "$ARM"
limactl shell "$VM" sudo sh -c "mkdir -p /opt/oflab && install -m 755 /tmp/check.py /opt/oflab/check.py"
GW=$(limactl shell "$VM" getent hosts host.lima.internal | awk '{print $1}')
VPN=$("$REPO/lab/bin/oflab" vpn-routes)
timeout 120 limactl shell "$VM" sudo sh -c "sh /tmp/lockdown.sh $GW $VPN > /tmp/lockdown.log 2>&1" || echo "lockdown: shell returned $? (checking rules)"
timeout 30 limactl shell "$VM" sudo nft list chain inet oflab output >/dev/null || { echo "lockdown failed"; exit 1; }

[ "${SETUP_ONLY:-}" = 1 ] && { echo "setup only: $VM ready"; exit 0; }

# 2. the vm wrapper: logs every command with time and exit code
cat > "$RUN/bin/vm" <<EOF
#!/bin/bash
# The command goes over as a file, so the VM's shell sees it exactly as written (no expansion on the way).
t0=\$(date +%s.%N)
f=\$(mktemp "$RUN/cmd.XXXXXX"); printf '%s\n' "\$*" > "\$f"; chmod 644 "\$f"
n=/tmp/oflab-cmd-\$(basename "\$f").sh
limactl copy "\$f" $VM:"\$n" >/dev/null 2>&1
out=\$(limactl shell $VM sudo -iu dev bash -l "\$n" < /dev/null 2>&1); rc=\$?
rm -f "\$f"
printf '%s\t%s\t%s\t%s\n' "\$t0" "\$(date +%s.%N)" "\$rc" "\$(printf %s "\$*" | tr '\t\n' '  ')" >> "$RUN/commands.tsv"
printf '%s\n' "\$out" | tail -c 20000
exit \$rc
EOF
chmod +x "$RUN/bin/vm"
: > "$RUN/commands.tsv"

# 3. the success checker runs in the background
(
  while sleep 15; do
    ok=$(limactl shell "$VM" sudo -iu dev bash -lc "python3 /opt/oflab/check.py" 2>/dev/null | tail -1)
    echo "$(date +%s) $ok" >> "$RUN/checks.log"
    [ "$ok" = OK ] && { date +%s > "$RUN/success_at"; break; }
  done
) &
CHECKER=$!

# 4. the persona
date +%s > "$RUN/started_at"
(cd "$RUN" && PATH="$RUN/bin:$PATH" timeout 2700 claude -p "$(cat "$HERE/persona.md")" --model "$MODEL" \
  --allowedTools "Bash(vm:*)" WebSearch WebFetch \
  --disallowedTools Read Write Edit Glob Grep NotebookEdit Agent Task \
  --setting-sources project --strict-mcp-config --no-session-persistence \
  --max-budget-usd "${OFLAB_BUDGET_USD:-4}" --max-turns 120 --output-format json > "$RUN/persona.json" 2> "$RUN/persona.err") || true
date +%s > "$RUN/ended_at"
sleep 20; kill $CHECKER 2>/dev/null || true
limactl stop "$VM" >/dev/null 2>&1 || true   # teardown; the next seed deletes it
python3 - "$RUN" <<'PY'
import json, sys, os
run = sys.argv[1]
s = int(open(f"{run}/started_at").read()); e = int(open(f"{run}/ended_at").read())
ok = os.path.exists(f"{run}/success_at") and int(open(f"{run}/success_at").read())
try: p = json.load(open(f"{run}/persona.json"))
except Exception: p = {}
cmds = [l.rstrip("\n").split("\t") for l in open(f"{run}/commands.tsv")]
fails = sum(1 for c in cmds if c[2] != "0")
src = sum(1 for c in cmds if "/bin/of_" in c[3] or ".py" in c[3] and "cat" in c[3])
print(json.dumps({"run": run, "success": bool(ok), "minutes_to_success": round((ok - s) / 60, 1) if ok else None,
                  "minutes_total": round((e - s) / 60, 1), "commands": len(cmds), "failed_commands": fails,
                  "source_reads": src, "result_line": (p.get("result") or "").strip().splitlines()[-1:] ,
                  "usage": p.get("usage"), "cost_estimate_usd": p.get("total_cost_usd")}, indent=1))
PY
