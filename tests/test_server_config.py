"""of-server's config loader expands every path a listener binds or loads (2026-11-20, of_agent-server: the agent host's
server.yaml said socket: ~/.local/state/of/server/http.sock, and the bind got the literal tilde)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_server  # noqa: E402


class LoadConfigTest(unittest.TestCase):

    def test_the_socket_and_the_tls_paths_are_expanded(self):
        d = tempfile.mkdtemp(prefix="ofcfg-")
        path = os.path.join(d, "server.yaml")
        with open(path, "w") as fh:
            fh.write("socket: ~/.local/state/of/server/http.sock\n"
                     "tls:\n  socket: /srv/of-server/server.sock\n  cert: ~/tls/server-cert.pem\n  key: ~/tls/server.key\n"
                     "  port: 18201\n")
        cfg = of_server.load_config(path)
        home = os.path.expanduser("~")
        self.assertEqual(cfg["socket"], os.path.join(home, ".local/state/of/server/http.sock"))
        self.assertEqual(cfg["tls"], {"socket": "/srv/of-server/server.sock", "cert": os.path.join(home, "tls/server-cert.pem"),
                                      "key": os.path.join(home, "tls/server.key"), "port": 18201})
        for v in (cfg["socket"], *[cfg["tls"][k] for k in ("socket", "cert", "key")]):
            self.assertNotIn("~", v)

    def test_no_socket_and_no_tls_stay_none(self):
        d = tempfile.mkdtemp(prefix="ofcfg-")
        path = os.path.join(d, "server.yaml")
        with open(path, "w") as fh:
            fh.write("port: 18200\n")
        cfg = of_server.load_config(path)
        self.assertIsNone(cfg["socket"])
        self.assertIsNone(cfg["tls"])


if __name__ == "__main__":
    unittest.main()
