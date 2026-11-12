#!/bin/sh
# Runs one hook of OF's herdr plugin (bin/of_herdr_plugin.py) with the Python OF itself runs on.
# OF's home: the file of_home in the plugin's config dir (herdr plugin config-dir organic-framework), else the
# folder above this one (the plugin linked from an OF checkout).
# The Python, the first of these that imports PyYAML: $OF_PYTHON; the file python in that config dir, which
# install() writes with the interpreter of init ran under; python3 on PATH; then $OF_PY_FALLBACKS, space-separated
# (by default Homebrew's, /usr/local's and the system's). A machine where OF runs from its own venv, with no
# Homebrew and a system python3 without PyYAML, has only the recorded one (a user organization, 2026-11-12).
OF_AUTOMATION=1; export OF_AUTOMATION
cfg="$HERDR_PLUGIN_CONFIG_DIR"
home=""; recorded=""
if [ -n "$cfg" ] && [ -r "$cfg/of_home" ]; then home=$(sed -n 1p "$cfg/of_home"); fi
if [ -n "$cfg" ] && [ -r "$cfg/python" ]; then recorded=$(sed -n 1p "$cfg/python"); fi
[ -n "$home" ] || home=$(cd "$(dirname "$0")/.." && pwd)
for py in "$OF_PYTHON" "$recorded" python3 ${OF_PY_FALLBACKS-/opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3}; do
  [ -n "$py" ] || continue
  if "$py" -c 'import yaml' >/dev/null 2>&1; then
    exec "$py" "$home/bin/of_herdr_plugin.py" "$@"
  fi
done
if [ -n "$recorded" ]; then
  echo "of herdr plugin: the recorded Python $recorded is gone or has no PyYAML, and no other has it." >&2
else
  echo "of herdr plugin: no Python with PyYAML found, and none is recorded." >&2
fi
echo "Run of init again, or run bin/of_herdr_plugin.py install with the Python OF runs on; it records that one." >&2
exit 1
