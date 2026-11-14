"""of tools (bin/of_tools.py): the profile, the generated session config and the catalogue check. Pure logic."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))

import of_tools  # noqa: E402

DOC = {
    "tools_mode": "enforce",
    "units": {"obs": {"tools": ["grafana-read"]}},
    "credentials": {"grafana-viewer": {"ref": "op://acme-agents/grafana-viewer/credential", "keeper": "obs_keeper"},
                    "books-mcp": {"paths": ["~/.config/acme/books-mcp/url"], "keeper": "books_keeper"}},
    "tools": {
        "superpowers": {"kind": "plugin", "plugin": "superpowers@claude-plugins-official", "default": True,
                        "acts_as": "nobody (skills)", "scope": "read", "keeper": "acme_org"},
        "grafana-read": {"kind": "mcp-stdio", "command": ["/opt/mcp-grafana", "--disable-write"],
                         "env": {"GRAFANA_URL": "https://grafana.example"},
                         "credential": {"id": "grafana-viewer", "file": "GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE"},
                         "acts_as": "Grafana service account agents-viewer (Viewer)", "scope": "read",
                         "keeper": "obs_keeper"},
        "slack": {"kind": "plugin", "plugin": "slack@anthropic-plugin-directory", "url": "https://mcp.slack.com/mcp",
                  "account_bound": True, "acts_as": "the founder's Slack user", "scope": ["read", "act-as-person"],
                  "keeper": "acme_dispatcher"},
        "books": {"kind": "mcp-stdio", "command": ["python3", "/of/bin/of_mcp_bridge.py", "--url-file",
                                                   "~/.config/acme/books-mcp/url"],
                  "credential": {"id": "books-mcp"}, "acts_as": "the books MCP server's user", "scope": "read",
                  "keeper": "books_keeper"},
        "docs": {"kind": "connector", "url": "https://api.example/v1/pages/mcp", "account_bound": True,
                 "acts_as": "the account's owner", "scope": "write", "keeper": "acme_org"},
    },
    "positions": [
        {"name": "obs_keeper", "unit": "obs"},
        {"name": "acme_dispatcher", "tools": ["slack", "docs", "-superpowers"]},
        {"name": "acme_org"},
        {"name": "books_keeper", "tools": ["books"]},
    ],
}


class ProfileTest(unittest.TestCase):
    def test_defaults_unit_and_own_with_removal(self):
        p = {x["name"]: x for x in DOC["positions"]}
        self.assertEqual(of_tools.profile(p["obs_keeper"], DOC), ["superpowers", "grafana-read"])
        self.assertEqual(of_tools.profile(p["acme_dispatcher"], DOC), ["slack", "docs"])
        self.assertEqual(of_tools.profile(p["acme_org"], DOC), ["superpowers"])


class SessionConfigTest(unittest.TestCase):
    def cfg(self, name, installed):
        p = next(x for x in DOC["positions"] if x["name"] == name)
        return of_tools.session_config(p, DOC, ofb="/of/bin/of", installed=installed)

    def test_stdio_tool_gets_its_credential_through_the_broker_only(self):
        c = self.cfg("obs_keeper", ["superpowers@claude-plugins-official", "cloudflare@claude-plugins-official"])
        s = c["mcp"]["mcpServers"]["grafana-read"]
        self.assertEqual(s["command"], "/of/bin/of")
        self.assertEqual(s["args"][:6], ["secret", "run", "grafana-viewer", "--file",
                                         "GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE", "--"])
        self.assertEqual(s["args"][6:], ["/opt/mcp-grafana", "--disable-write"])
        self.assertNotIn("TOKEN", "".join(s["env"]))
        self.assertEqual(c["settings"]["allowedMcpServers"], [{"serverName": "grafana-read"}])
        # every installed plugin is named: --settings merges enabledPlugins per entry with the account's own
        self.assertEqual(c["settings"]["enabledPlugins"], {"cloudflare@claude-plugins-official": False,
                                                           "superpowers@claude-plugins-official": True})

    def test_account_bound_tools_pass_by_url_and_are_listed(self):
        c = self.cfg("acme_dispatcher", ["superpowers@claude-plugins-official", "slack@anthropic-plugin-directory"])
        self.assertIsNone(c["mcp"])
        self.assertEqual(c["settings"]["allowedMcpServers"], [{"serverUrl": "https://mcp.slack.com/mcp"},
                                                              {"serverUrl": "https://api.example/v1/pages/mcp"}])
        self.assertEqual(c["account_bound"], ["slack", "docs"])
        self.assertEqual(c["settings"]["enabledPlugins"]["superpowers@claude-plugins-official"], False)

    def test_a_file_credential_is_read_by_the_server_itself(self):
        c = self.cfg("books_keeper", [])
        s = c["mcp"]["mcpServers"]["books"]
        self.assertEqual(s["command"], "python3")
        self.assertNotIn("secret", s["args"])

    def test_a_tilde_in_env_reaches_the_server_expanded(self):
        doc = {"tools_mode": "enforce", "credentials": {"g": {"paths": ["~/.secrets/g.token"]}},
               "tools": {"g": {"kind": "mcp-stdio", "command": ["/opt/mcp-grafana"],
                               "env": {"TOKEN_FILE": "~/.secrets/g.token", "HOMEY": "${HOME}/x", "BARE": "$HOME/y",
                                       "URL": "https://g", "OTHER": "$GRAFANA_ORG/z"},
                               "credential": {"id": "g"}, "acts_as": "s", "scope": "read", "keeper": "a"}},
               "positions": [{"name": "a", "tools": ["g"]}]}
        env = of_tools.session_config(doc["positions"][0], doc, ofb="/of/bin/of", installed=[])["mcp"]["mcpServers"]["g"]["env"]
        self.assertEqual(env["TOKEN_FILE"], os.path.expanduser("~/.secrets/g.token"))
        self.assertEqual(env["HOMEY"], os.path.expanduser("~/x"))
        self.assertEqual(env["BARE"], os.path.expanduser("~/y"))  # Claude Code would not expand the bare form
        self.assertEqual(env["URL"], "https://g")
        self.assertEqual(env["OTHER"], "$GRAFANA_ORG/z")

    def test_no_tools_allows_no_server(self):
        c = self.cfg("acme_org", [])
        self.assertEqual(c["settings"]["allowedMcpServers"], [])

    def test_add_mode_keeps_the_accounts_own_tools(self):
        # 2026-11-12: one catalogue entry made every position of an organization strict, and moved sessions lost
        # the account's tools; until the keeper sets tools_mode: enforce, a profile only adds its portable servers
        doc = dict(DOC, tools_mode="add")
        p = next(x for x in doc["positions"] if x["name"] == "obs_keeper")
        c = of_tools.session_config(p, doc, ofb="/of/bin/of", installed=["cloudflare@claude-plugins-official"])
        self.assertIn("grafana-read", c["mcp"]["mcpServers"])
        self.assertEqual(c["settings"], {})
        self.assertEqual(c["mode"], "add")
        doc.pop("tools_mode")
        self.assertEqual(of_tools.session_config(p, doc, ofb="/of/bin/of", installed=[])["settings"], {})

    def test_an_excluded_plugin_is_off_in_both_modes(self):
        ex = [{"plugin": "slack@anthropic-plugin-directory", "basis": "card e2bb5eef"}]
        installed = ["slack@anthropic-plugin-directory", "cloudflare@claude-plugins-official"]
        for mode in ("add", "enforce"):
            doc = dict(DOC, tools_mode=mode, tools_excluded=ex)
            p = next(x for x in doc["positions"] if x["name"] == "acme_dispatcher")  # its profile names slack
            c = of_tools.session_config(p, doc, ofb="/of/bin/of", installed=installed)
            self.assertIs(c["settings"]["enabledPlugins"]["slack@anthropic-plugin-directory"], False, mode)
            self.assertTrue(any("in tools_excluded" in n for n in c["notes"]), mode)
        # add mode names only the excluded plugin, so the account's others stay as they are
        self.assertNotIn("cloudflare@claude-plugins-official", of_tools.session_config(
            p, dict(DOC, tools_mode="add", tools_excluded=ex), ofb="/of/bin/of", installed=installed)["settings"]["enabledPlugins"])

    def test_an_excluded_url_is_denied_in_both_modes(self):
        ex = [{"url": "https://mcp.slack.com/*", "basis": "card e2bb5eef"}]
        for mode in ("add", "enforce"):
            doc = dict(DOC, tools_mode=mode, tools_excluded=ex)
            p = next(x for x in doc["positions"] if x["name"] == "obs_keeper")
            c = of_tools.session_config(p, doc, ofb="/of/bin/of", installed=[])
            self.assertEqual(c["settings"]["deniedMcpServers"], [{"serverUrl": "https://mcp.slack.com/*"}], mode)
        self.assertEqual(of_tools.check({"acme": dict(DOC, tools_excluded=ex)}), [])

    def test_an_exclusion_applies_without_a_catalogue(self):
        doc = {"positions": [{"name": "a"}],
               "tools_excluded": [{"plugin": "slack@anthropic-plugin-directory", "basis": "ask 1"}]}
        real = of_tools.registries
        try:
            for tools in (None, {}):
                d = dict(doc, tools=tools) if tools is not None else doc
                of_tools.registries = lambda orgs_file=None, d=d: {"acme": d}
                argv, settings = of_tools.launch_parts("a", "s-1", None)
                self.assertEqual(argv, [])
                self.assertEqual(settings, {"enabledPlugins": {"slack@anthropic-plugin-directory": False}})
            of_tools.registries = lambda orgs_file=None: {"acme": {"positions": [{"name": "a"}]}}
            self.assertEqual(of_tools.launch_parts("a", "s-1", None), ([], {}))
        finally:
            of_tools.registries = real

    def test_plugin_missing_on_the_account_is_noted(self):
        c = self.cfg("acme_dispatcher", [])
        self.assertTrue(any("slack@anthropic-plugin-directory is not installed" in n for n in c["notes"]))


class SharedServerTest(unittest.TestCase):
    def doc(self):
        d = dict(DOC, tools=dict(DOC["tools"]))
        d["tools"]["grafana-read"] = dict(DOC["tools"]["grafana-read"], shared={
            "port": 18310, "args": ["--transport", "streamable-http", "--address", "127.0.0.1:{port}"],
            "token_env": "MCP_GRAFANA_SERVER_TOKEN"})
        return d

    def test_a_running_shared_server_replaces_the_sessions_own(self):
        d = self.doc()
        p = next(x for x in d["positions"] if x["name"] == "obs_keeper")
        s = of_tools.session_config(p, d, ofb="/of/bin/of", installed=[], org="acme",
                                    up=lambda port: True)["mcp"]["mcpServers"]["grafana-read"]
        self.assertEqual(s["url"], "http://127.0.0.1:18310/mcp")
        self.assertEqual(s["headersHelper"], "/of/bin/of tools header acme/grafana-read")
        self.assertNotIn("command", s)
        c = of_tools.session_config(p, d, ofb="/of/bin/of", installed=[], org="acme", up=lambda port: False)
        self.assertEqual(c["mcp"]["mcpServers"]["grafana-read"]["type"], "stdio")  # not running: its own, as before
        self.assertTrue(any("is not running" in n for n in c["notes"]))

    def test_serve_command_and_the_header_only_for_the_profile(self):
        import tempfile
        d = self.doc()
        with tempfile.TemporaryDirectory() as st:
            argv, env = of_tools.serve_command("acme", "grafana-read", d, ofb="/of/bin/of", state_dir=st)
            self.assertEqual(argv[-4:], ["--transport", "streamable-http", "--address", "127.0.0.1:18310"])
            self.assertEqual(argv[:3], ["/of/bin/of", "secret", "run"])  # the vault credential still via the broker
            tok = env["MCP_GRAFANA_SERVER_TOKEN"]
            self.assertEqual(env["OF_AUTOMATION"], "1")
            path = os.path.join(st, "tools", "shared", "acme.grafana-read.token")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            mine = os.path.join(st, "tools", "s-1.mcp.json")
            with open(mine, "w") as fh:
                json.dump({"mcpServers": {"grafana-read": {"type": "http", "url": "http://127.0.0.1:18310/mcp"}}}, fh)
            other = os.path.join(st, "tools", "s-2.mcp.json")
            with open(other, "w") as fh:
                json.dump({"mcpServers": {}}, fh)

            def chain(path, sid="s-1", flag="--session-id"):
                return [(11, "/bin/sh -c of tools header acme/grafana-read"),
                        (10, f"claude {flag} {sid} -n x --settings y --mcp-config {path}")]
            hdr = of_tools.header("acme", "grafana-read", {"acme": d}, ancestors=chain(mine), state_dir=st)
            self.assertEqual(hdr, {"Authorization": f"Bearer {tok}"})
            # a moved, restarted or resumed session runs as `claude --resume <session>`
            hdr = of_tools.header("acme", "grafana-read", {"acme": d}, ancestors=chain(mine, flag="--resume"),
                                  state_dir=st)
            self.assertEqual(hdr, {"Authorization": f"Bearer {tok}"})
            refused = [chain(other, "s-2"),                              # a session whose profile lacks it
                       chain(mine, "s-2"),                               # another session's file
                       chain(mine, "s-2", "--resume"),                   # another session's file, resumed
                       chain("/tmp/forged.mcp.json"),                    # a file OF did not write
                       [(10, "claude --session-id s-1")],                # not launched by OF
                       [(10, "/bin/zsh")]]                               # no Claude Code at all
            for anc in refused:
                with self.assertRaises(of_tools.ToolsError, msg=anc):
                    of_tools.header("acme", "grafana-read", {"acme": d}, ancestors=anc, state_dir=st)

    def test_check_shared(self):
        d = self.doc()
        self.assertEqual(of_tools.check({"acme": d}), [])
        open_ = self.doc()
        open_["tools"]["grafana-read"]["shared"] = {"port": 18311, "args": []}
        self.assertTrue(any("without one is open" in x for x in of_tools.check({"acme": open_})))
        p = next(x for x in open_["positions"] if x["name"] == "obs_keeper")  # never served without a token
        self.assertEqual(of_tools.session_config(p, open_, ofb="o", installed=[], org="acme", up=lambda port: True)
                         ["mcp"]["mcpServers"]["grafana-read"]["type"], "stdio")
        self.assertIn("acme: tools.grafana-read: shared.port 18310 is also other/grafana-read's",
                      of_tools.check({"other": self.doc(), "acme": d}))


class CheckTest(unittest.TestCase):
    def test_the_example_is_clean(self):
        self.assertEqual(of_tools.check({"acme": DOC}), [])

    def test_a_file_credential_named_in_env_is_clean(self):
        doc = {"positions": [{"name": "a"}], "credentials": {"g": {"paths": ["~/.secrets/g.token"]}},
               "tools": {"g": {"kind": "mcp-stdio", "command": ["/opt/mcp-grafana"],
                               "env": {"GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE": "~/.secrets/g.token"},
                               "credential": {"id": "g"}, "acts_as": "s", "scope": "read", "keeper": "a"}}}
        self.assertEqual(of_tools.check({"acme": doc}), [])
        doc["tools"]["g"]["env"]["GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE"] = "${HOME}/.secrets/g.token"
        self.assertEqual(of_tools.check({"acme": doc}), [])  # Claude Code expands ${HOME} itself
        doc["tools"]["g"]["env"] = ["GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE=~/.secrets/g.token"]
        self.assertIn("acme: tools.g: env is a mapping NAME: value", of_tools.check({"acme": doc}))  # reported, no raise

    def test_problems(self):
        bad = {"tools_mode": "strict", "positions": [{"name": "a", "tools": ["nope"]}], "credentials": {},
               "tools": {"x": {"kind": "mcp-stdio", "command": ["p"], "env": {"API_TOKEN": "abc123"},
                               "credential": {"id": "missing", "env": "T"}, "acts_as": "s", "scope": "read",
                               "keeper": "ghost"},
                         "y": {"kind": "mcp-remote", "url": "https://h/mcp", "oauth": True,
                               "headers": {"Authorization": {"vault": "Acme/x"}}, "acts_as": "s", "scope": "read",
                               "keeper": "a"}}}
        bad["credentials"]["f"] = {"paths": ["~/.config/acme/f"]}
        bad["tools"]["z"] = {"kind": "mcp-stdio", "command": ["p"], "credential": {"id": "f"}, "acts_as": "s",
                             "scope": "read", "keeper": "a"}
        bad["tools_excluded"] = [{"plugin": "slack@x"}, {"plugin": "slack@x", "basis": "ask 1"}]
        bad["tools"]["s"] = {"kind": "plugin", "plugin": "slack@x", "acts_as": "s", "scope": "read", "keeper": "a"}
        probs = "\n".join(of_tools.check({"acme": bad}))
        for want in ("tool nope is not in the catalogue", "keeper ghost", "credential missing",
                     "API_TOKEN looks like a secret", "mark it account_bound", "never a vault reference",
                     "credential f is a file: the command or an env value names its path",
                     "tools_mode is add or enforce", "tools_excluded[0] needs plugin or url, and basis",
                     "tools.s: plugin slack@x is in tools_excluded"):
            self.assertIn(want, probs)


class NamesPathTest(unittest.TestCase):
    def test_glob_stem_and_exact(self):
        self.assertTrue(of_tools._names_path("~/.config/acme/x", "~/.config/acme/x/**"))
        self.assertTrue(of_tools._names_path("~/.config/acme/x/oauth.json", "~/.config/acme/x/**"))
        self.assertTrue(of_tools._names_path("~/.config/acme/f", "~/.config/acme/f"))
        self.assertFalse(of_tools._names_path("~/.config/acme/xy", "~/.config/acme/x/**"))
        self.assertFalse(of_tools._names_path("python3", "~/.config/acme/x/**"))


class ShapeTest(unittest.TestCase):
    def test_url_shown_by_host_and_a_key_in_it_flagged(self):
        s = of_tools._server_shape({"url": "https://books.mcp.example/mcp/" + "ab12" * 8 + "/message"})
        self.assertEqual(s["url"], "https://books.mcp.example/…")
        self.assertTrue(s["url_key"])
        s = of_tools._server_shape({"type": "http", "url": "https://mcp.slack.com/mcp"})
        self.assertNotIn("url_key", s)


if __name__ == "__main__":
    unittest.main()
