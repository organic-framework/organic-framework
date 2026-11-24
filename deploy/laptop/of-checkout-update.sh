#!/bin/bash
# The update of the laptop's live checkout, ~/.local/share/of/live: the checkout every account's Claude Code hooks,
# the listener, the relay client, of-server and the `of` on PATH run from, apart from the shared working copy that
# sessions write (hand-over cb134c52, after 2026-11-24: a half-applied patch in ~/organic-framework stopped every
# session's tool calls for two hours). The macOS form of organic-framework-build tools/agent-host/of-checkout-update.sh:
# started by launchd on a request (dev.organic-framework.checkout-update, WatchPaths on
# ~/.local/state/of/checkout-update.request, whose content is never read), it fetches public main privately, lets
# `of protected ff` (PR 511) move the checkout to a merged pull request's commit with its checks green, and restarts
# of-server, of-relay and of-listen through launchctl. launchd runs it from the live checkout itself, which G40 keeps
# from every session, so it changes only with a reviewed commit; `of live setup` writes the agent (bin/of_live.py).
set -euo pipefail
export PATH=/usr/bin:/bin SSH_AUTH_SOCK= OF_AUTOMATION=1 GIT_TERMINAL_PROMPT=0
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP
umask 022
URL=https://github.com/organic-framework/organic-framework.git
NET=https
LIVE=$HOME/.local/share/of/live
PY=${OF_PYTHON:?the Python OF runs on, set by the agent}
SITE=$LIVE/.git/of-site   # PyYAML, copied there by of live setup
UNITS="dev.organic-framework.server dev.organic-framework.relay dev.organic-framework.listen"

g() {  # $1: the transports allowed, empty for none
  local allow=$1; shift
  env GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_NO_LAZY_FETCH=1 GIT_NO_REPLACE_OBJECTS=1 \
    GIT_GRAFT_FILE=/dev/null GIT_ALLOW_PROTOCOL="$allow" \
    git -c core.hooksPath=/dev/null -c core.fsmonitor=false -c core.alternateRefsCommand=/usr/bin/true \
    -c hook.reference-transaction.enabled=false -c hook.post-checkout.enabled=false \
    -c hook.post-index-change.enabled=false -c core.commitGraph=false -c submodule.recurse=false \
    -c gc.auto=0 -c maintenance.auto=false -c advice.graftFileDeprecated=false "$@"
}
live() { local allow=$1; shift; g "$allow" -C "$LIVE" --git-dir="$LIVE/.git" --work-tree="$LIVE" "$@"; }
private() { GIT_INDEX_FILE=$P/index g '' -C "$LIVE" --git-dir="$P" --work-tree="$LIVE" "$@"; }  # over the live files

check_checkout() {
  local f line lines
  if [ -L "$LIVE" ] || [ -L "$LIVE/.git" ] || [ ! -d "$LIVE/.git" ]; then echo "refused: $LIVE is no plain checkout"; return 1; fi
  for f in commondir info/grafts info/attributes shallow; do
    if [ -e "$LIVE/.git/$f" ] || [ -L "$LIVE/.git/$f" ]; then echo "refused: the checkout has .git/$f"; return 1; fi
  done
  # the sandbox leaves these two behind as empty mount points
  for f in config.worktree objects/info/alternates; do
    if [ -L "$LIVE/.git/$f" ] || { [ -e "$LIVE/.git/$f" ] && { [ ! -f "$LIVE/.git/$f" ] || [ -s "$LIVE/.git/$f" ]; }; }; then
      echo "refused: the checkout's .git/$f is not empty"; return 1; fi
  done
  if [ -L "$LIVE/.git/config" ] || [ -L "$LIVE/.git/HEAD" ]; then echo "refused: .git/config or HEAD is a link"; return 1; fi
  lines=$(g '' -C / config --file "$LIVE/.git/config" --list) || { echo "refused: its config cannot be read"; return 1; }
  while IFS= read -r line; do
    case "$line" in
      core.repositoryformatversion=0|core.filemode=true|core.bare=false|core.logallrefupdates=true) ;;
      core.ignorecase=true|core.precomposeunicode=true) ;;   # what git writes into any clone on macOS
      remote.origin.tagopt=--no-tags) ;;                      # what `of live setup`'s clone --no-tags writes
      "remote.origin.url=$URL") ;;
      'remote.origin.fetch=+refs/heads/*:refs/remotes/origin/*') ;;
      branch.main.remote=origin|branch.main.merge=refs/heads/main) ;;
      *) echo "refused: the checkout's config holds a line the update does not know, in its section ${line%%.*}"
         return 1 ;;   # the section only: a key's subsection can hold a URL with a user and a token
    esac
  done <<<"$lines"
}

restart() {
  local u bad=0 dom="gui/$(id -u)"
  for u in $UNITS; do
    if launchctl kickstart -k "$dom/$u" >/dev/null 2>&1; then echo "$u: restarted"
    else echo "$u: NOT restarted"; bad=1; fi
  done
  return "$bad"
}

older_than_move() {  # true when a service started before the checkout last moved
  local u pid at moved
  moved=$(stat -f %m "$LIVE/.git/HEAD")
  for u in $UNITS; do
    pid=$(launchctl print "gui/$(id -u)/$u" 2>/dev/null | awk '/^[[:space:]]*pid = /{print $3; exit}')
    case "$pid" in ''|*[!0-9]*) return 0 ;; esac
    at=$(ps -o lstart= -p "$pid" 2>/dev/null) || return 0
    at=$(date -j -f "%a %b %d %T %Y" "$at" +%s 2>/dev/null) || return 0
    if [ "$at" -lt "$moved" ]; then return 0; fi
  done
  return 1
}

main() {
  local old tip now extras rc=0
  echo "update requested"
  check_checkout
  old=$(live '' rev-parse --verify 'HEAD^{commit}')
  # the private store lies inside the live checkout's .git, which G40 keeps: no session can put a .pyc in its
  # cache folder while an update runs (of_secrets-review on PR 629)
  P=$(mktemp -d "$LIVE/.git/of-update.XXXXXX")
  trap "rm -rf '$P'" EXIT
  g '' -C / init -q --bare -b main "$P"
  g "$NET" -C / --git-dir="$P" fetch -q --no-tags "$URL" +refs/heads/main:refs/heads/main
  tip=$(g '' -C / --git-dir="$P" rev-parse refs/heads/main)
  if ! g '' -C / --git-dir="$P" merge-base --is-ancestor "$old" "$tip"; then
    echo "refused: the checkout's $old is not on public main"; return 1
  fi
  # before any of the checkout's code runs, and before a restart at the tip (of_secrets-review on PR 629): its
  # tracked files are exactly old's, read from the private store, or that code could decide its own move (of_server's
  # review, message 01a2032c-9e79)
  private read-tree "$old"
  private update-index -q --refresh >/dev/null || true
  if ! private diff-files --quiet --ignore-submodules=none; then
    echo "refused, none of the checkout's code was run: tracked files differ from $old:"
    private diff-files --name-only | head -20 | LC_ALL=C tr -cd '\11\12\40-\176'
    return 1
  fi
  extras=$(private ls-files --others -- bin deploy | grep -v '/__pycache__/' || true)
  if [ -n "$extras" ]; then
    echo "refused: files that are no commit's lie where the services import from:"
    printf '%s\n' "$extras" | head -20 | LC_ALL=C tr -cd '\11\12\40-\176'
    return 1
  fi

  if [ "$old" = "$tip" ]; then
    if older_than_move; then
      echo "at $tip already, but a service is older than that move: restarting"
      restart || { echo "UPDATE: A SERVICE IS NOT BACK at $tip"; return 1; }
    fi
    echo "UPDATE: nothing to do, at $tip"
    return 0
  fi

  live file fetch -q --no-tags --no-write-fetch-head "$P" refs/heads/main
  # -I: no user site (a folder sessions may write) and no PYTHON variables. On a Mac PyYAML lives in that user site, so
  # `of live setup` copies it into $SITE, inside the checkout's .git, which G40 keeps; only that folder is added to the
  # path (of_secrets-review on PR 629). The cache prefix, an empty private folder: a .pyc planted in a __pycache__ of
  # the checkout is never loaded (-B alone only stops writing them)
  "$PY" -I -B -X pycache_prefix="$P/pycache" -c \
    'import runpy, sys; sys.path.insert(0, sys.argv[1]); sys.argv = sys.argv[2:]; runpy.run_path(sys.argv[0], run_name="__main__")' \
    "$SITE" "$LIVE/bin/of" protected ff "$LIVE" --to "$tip" 2>&1 || rc=$?
  now=$(live '' rev-parse --verify 'HEAD^{commit}')
  if [ "$now" = "$old" ]; then
    echo "UPDATE: not moved, at $old (of protected ff ended $rc, above)"
    return 1
  fi
  if [ "$now" != "$tip" ]; then
    echo "UPDATE: the checkout is at $now, neither $old nor $tip: the owner must look; nothing restarted"
    return 1
  fi
  find "$LIVE/bin" "$LIVE/deploy" -type d -name __pycache__ -prune -exec rm -rf {} +
  restart || { echo "UPDATE: A SERVICE IS NOT BACK; moved $old to $tip"; return 1; }
  echo "UPDATE: moved $old to $tip (of protected ff ended $rc); services restarted"
}
# launchd runs this file from the live checkout it moves: bash has read this whole line before main starts, and the
# exit stops it before it reads on in a file the move may have rewritten. Sourced, by tests/test_live.py, it runs nothing.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then main </dev/null; exit $?; fi
