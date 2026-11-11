#!/bin/sh
# Runs one hook of OF's herdr plugin (bin/of_herdr_plugin.py) with a Python that has PyYAML.
# OF's home: the file of_home in the plugin's config dir (herdr plugin config-dir organic-framework), else the
# folder above this one (the plugin linked from an OF checkout).
OF_AUTOMATION=1; export OF_AUTOMATION
home=""
if [ -n "$HERDR_PLUGIN_CONFIG_DIR" ] && [ -r "$HERDR_PLUGIN_CONFIG_DIR/of_home" ]; then
  home=$(sed -n 1p "$HERDR_PLUGIN_CONFIG_DIR/of_home")
fi
[ -n "$home" ] || home=$(cd "$(dirname "$0")/.." && pwd)
for py in "$OF_PYTHON" python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  [ -n "$py" ] || continue
  if "$py" -c 'import yaml' >/dev/null 2>&1; then
    exec "$py" "$home/bin/of_herdr_plugin.py" "$@"
  fi
done
echo "of herdr plugin: no python3 with PyYAML found (set OF_PYTHON)" >&2
exit 1
