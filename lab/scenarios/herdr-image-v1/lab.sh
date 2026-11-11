#!/bin/sh
# herdr against the lab session only, never the founder's: the pane variables a herdr pane sets are dropped,
# so a call made from inside a herdr pane cannot reach or re-bind the session it runs in.
exec env -u HERDR_ENV -u HERDR_PANE_ID -u HERDR_SOCKET_PATH -u HERDR_PANE_RUNTIME_ID OF_AUTOMATION=1 \
  herdr --session "${OFLAB_SESSION:-oflab}" "$@"
