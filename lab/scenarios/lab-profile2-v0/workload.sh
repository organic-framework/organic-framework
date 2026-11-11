#!/bin/bash
# lab-profile2-v0 workload on a client: one session in the joined organization, one in an
# organization this machine did not join; then ingest and ship. Prints local counts.
# Usage: workload.sh <main_session_id> <other_session_id> <turns> [tag]
set -u
unset SSH_AUTH_SOCK; export OF_AUTOMATION=1 PATH="$HOME/.local/bin:$PATH"
SID="$1"; OTHER="$2"; N="$3"; TAG="${4:-r1}"
OF="$HOME/organic-framework/bin/of"
mk() { for i in $(seq 1 "$1"); do echo "$TAG turn $i: work item $i"; done; echo /exit; }
mk "$N" > /tmp/main.txt; mk 3 > /tmp/other.txt
(cd "$HOME/work" && claude --session-id "$SID" -n main --script /tmp/main.txt >/dev/null)
(cd "$HOME/other" && claude --session-id "$OTHER" -n other --script /tmp/other.txt >/dev/null)
for i in $(seq 1 60); do [ -z "$(ls -A ~/.local/state/of/pending 2>/dev/null)" ] && break; sleep 1; done
"$OF" ship 2>&1 | tail -3
sqlite3 ~/.local/state/of/of.db "select 'local_events', count(*) from event where session_id in ('$SID'); select 'local_events_other', count(*) from event where session_id='$OTHER'; select 'local_transcript', count(*) from transcript_record where session_id='$SID'; select 'local_hook', count(*) from hook_record where session_id='$SID'"
