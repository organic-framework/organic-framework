"""of tools (bin/of_tools.py): the profile, the generated session config and the catalogue check. Pure logic."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))

import of_tools  # noqa: E402

DOC = {
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

    def test_no_tools_allows_no_server(self):
        c = self.cfg("acme_org", [])
        self.assertEqual(c["settings"]["allowedMcpServers"], [])

    def test_plugin_missing_on_the_account_is_noted(self):
        c = self.cfg("acme_dispatcher", [])
        self.assertTrue(any("slack@anthropic-plugin-directory is not installed" in n for n in c["notes"]))


class CheckTest(unittest.TestCase):
    def test_the_example_is_clean(self):
        self.assertEqual(of_tools.check({"acme": DOC}), [])

    def test_problems(self):
        bad = {"positions": [{"name": "a", "tools": ["nope"]}], "credentials": {},
               "tools": {"x": {"kind": "mcp-stdio", "command": ["p"], "env": {"API_TOKEN": "abc123"},
                               "credential": {"id": "missing", "env": "T"}, "acts_as": "s", "scope": "read",
                               "keeper": "ghost"},
                         "y": {"kind": "mcp-remote", "url": "https://h/mcp", "oauth": True,
                               "headers": {"Authorization": {"vault": "Acme/x"}}, "acts_as": "s", "scope": "read",
                               "keeper": "a"}}}
        bad["credentials"]["f"] = {"paths": ["~/.config/acme/f"]}
        bad["tools"]["z"] = {"kind": "mcp-stdio", "command": ["p"], "credential": {"id": "f"}, "acts_as": "s",
                             "scope": "read", "keeper": "a"}
        probs = "\n".join(of_tools.check({"acme": bad}))
        for want in ("tool nope is not in the catalogue", "keeper ghost", "credential missing",
                     "API_TOKEN looks like a secret", "mark it account_bound", "never a vault reference",
                     "credential f is a file: the command names its path"):
            self.assertIn(want, probs)


class ShapeTest(unittest.TestCase):
    def test_url_shown_by_host_and_a_key_in_it_flagged(self):
        s = of_tools._server_shape({"url": "https://books.mcp.example/mcp/" + "ab12" * 8 + "/message"})
        self.assertEqual(s["url"], "https://books.mcp.example/…")
        self.assertTrue(s["url_key"])
        s = of_tools._server_shape({"type": "http", "url": "https://mcp.slack.com/mcp"})
        self.assertNotIn("url_key", s)


if __name__ == "__main__":
    unittest.main()
