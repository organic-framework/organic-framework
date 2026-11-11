"""The agents' secrets broker (docs/agent-secrets.md §5.3, §5.4, §10; hand-over 7ae99eac): a position's credential
goes only to its registered holder on a present machine, read with that position's own service account, so a
hand-over moves access with no step in 1Password. A fake `op` stands for 1Password: it enforces each token's vaults
and logs every read."""
import json
import os
import sys
import tempfile
import textwrap
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import of_client  # noqa: E402
import of_credentials  # noqa: E402
import of_events  # noqa: E402
import of_secret  # noqa: E402
import of_server  # noqa: E402
import of_server_secrets as sec  # noqa: E402

FAKE_OP = textwrap.dedent('''\
    #!/usr/bin/env python3
    """A stand-in for 1Password: `op read --no-newline REF` as the service account in OP_SERVICE_ACCOUNT_TOKEN."""
    import json, os, sys
    here = os.path.dirname(os.path.abspath(__file__))
    db = json.load(open(os.path.join(here, "db.json")))
    token, ref = os.environ.get("OP_SERVICE_ACCOUNT_TOKEN", ""), sys.argv[-1]
    vault = ref[len("op://"):].split("/")[0]
    ok = vault in db["tokens"].get(token, []) and ref in db["items"]
    with open(os.path.join(here, "reads.log"), "a") as fh:
        fh.write(json.dumps({"token": token[-4:], "vault": vault, "ok": ok}) + "\\n")
    if not ok:
        print("[ERROR] not authorized", file=sys.stderr)
        sys.exit(1)
    sys.stdout.write(db["items"][ref])
    ''')

REGISTRY = """
organization: t
credentials:
  grafana-viewer:
    ref: "op://t-obs/grafana-viewer/credential"
    keeper: obs_keeper
  kube-admin:
    ref: "op://t-k8s/kube-admin/credential"
    keeper: k8s_keeper
  legacy-file:
    paths: ["~/.config/t/legacy.cfg"]
    keeper: obs_keeper
positions:
  - {{name: obs_reader, credentials: [grafana-viewer], holder: {{session: {reader}}}}}
  - {{name: obs_keeper, holder: {{session: S-keeper}}}}
  - {{name: k8s_keeper, holder: {{session: S-k8s}}}}
"""


class FakeCH:
    def __init__(self):
        self.inserts = []

    def insert(self, database, table, rows):
        self.inserts.append((database, table, rows))

    def ping(self):
        return True


class BrokerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = os.path.join(root, "state")
        self.op_dir = os.path.join(root, "op")
        os.makedirs(self.op_dir)
        self.op = os.path.join(self.op_dir, "op")
        with open(self.op, "w") as fh:
            fh.write(FAKE_OP)
        os.chmod(self.op, 0o755)
        with open(os.path.join(self.op_dir, "db.json"), "w") as fh:
            json.dump({"tokens": {"tok-reader": ["t-obs"], "tok-keeper": ["t-obs"], "tok-k8s": ["t-k8s"]},
                       "items": {"op://t-obs/grafana-viewer/credential": "v-obs",
                                 "op://t-k8s/kube-admin/credential": "v-k8s"}}, fh)
        self.tokens = os.path.join(root, "tokens")
        for pos, tok in (("obs_reader", "tok-reader"), ("obs_keeper", "tok-keeper"), ("k8s_keeper", "tok-k8s")):
            os.makedirs(os.path.join(self.tokens, "t"), exist_ok=True)
            with open(os.path.join(self.tokens, "t", f"{pos}.token"), "w") as fh:
                fh.write(tok)
        self.registry = os.path.join(root, "positions.yaml")
        self.hold("S1")
        sec._cache.clear()
        cfg = dict(of_server.DEFAULTS, state_dir=os.path.join(root, "server"), port=0, flush_every=3600,
                   orgs={"t": "db_t"}, positions_files={"t": self.registry},
                   secrets={"tokens_dir": self.tokens, "op": self.op})
        self.app = of_server.App(cfg, ch=FakeCH(), ship_cfg={"unassigned_database": "db_un", "vault_database": "db_v",
                                                            "raw_to_org": False, "tenant": "x"}, mask_key=b"m" * 32)
        self.httpd = self.app.serve(host="127.0.0.1", port=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.a, self.b = self.machine("a"), self.machine("b")

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.writer.stop.set()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def hold(self, session):
        with open(self.registry, "w") as fh:
            fh.write(REGISTRY.format(reader=session))

    def machine(self, name):
        d = os.path.join(self.tmp.name, name)
        os.makedirs(d)
        c = of_client.Client(self.url, key_path=os.path.join(d, "key"), state_dir=d)
        c.machine_id = f"m-{name}"
        of_server.enroll(self.app.store, c.machine_id, of_client.ensure_key(c.key_path), ["t"], [])
        return c

    def present(self, c, session, position="obs_reader"):
        c._authed("PUT", "/v1/presence", body={"holders": [{"org": "t", "position": position, "session_id": session,
                                                             "kind": "agent", "pane": "w1:p1"}]})

    def use(self, c, session, credential="grafana-viewer", position="obs_reader"):
        try:
            return 200, c.use_secret("t", position, session, credential)
        except of_client.ServerError as e:
            return e.status, str(e)

    def test_0_the_vault_boundary_holds_without_the_broker(self):
        """§10 step 0: one position's token cannot read another position's vault in 1Password itself."""
        self.assertEqual(sec._op_read(self.op, "tok-reader", "op://t-obs/grafana-viewer/credential"), ("v-obs", None))
        value, why = sec._op_read(self.op, "tok-reader", "op://t-k8s/kube-admin/credential")
        self.assertIsNone(value)
        self.assertIn("refused", why)

    def test_the_holder_gets_its_credential_and_nothing_else(self):
        self.present(self.a, "S1")
        status, ans = self.use(self.a, "S1")
        self.assertEqual((status, ans["value"], ans["ref_vault"]), (200, "v-obs", "t-obs"))
        self.assertTrue(self.use(self.a, "S1")[1]["cached"])
        status, why = self.use(self.a, "S1", "kube-admin")
        self.assertEqual(status, 403)
        self.assertIn("neither lists nor keeps", why)
        status, why = self.use(self.b, "S1")                           # another machine: it presents nothing
        self.assertEqual(status, 403)
        self.assertEqual(self.use(self.a, "S1", "nope")[0], 404)
        self.assertEqual(self.use(self.a, "S1", "legacy-file")[0], 403)  # listed by nobody
        self.present(self.a, "S-keeper", "obs_keeper")
        status, why = self.use(self.a, "S-keeper", "legacy-file", "obs_keeper")
        self.assertEqual(status, 409)                                  # its keeper, but a file: no ref
        self.assertIn("no secret reference", why)

    def test_a_hand_over_moves_access_at_the_next_call(self):
        """§5.4, §10 steps 1-4: the registry and presence move; nothing changes in 1Password."""
        self.present(self.a, "S1")
        self.assertEqual(self.use(self.a, "S1")[0], 200)
        self.hold("S9")                                                # the hand-over: a new holder session
        self.present(self.b, "S9")
        status, ans = self.use(self.b, "S9")
        self.assertEqual((status, ans["value"], ans["cached"]), (200, "v-obs", False))   # never the old one's cache
        status, why = self.use(self.a, "S1")                           # the former holder, still presenting
        self.assertEqual(status, 403)
        self.assertIn("registered holder", why)
        status, why = self.use(self.a, "S9")                           # A cannot borrow the new session's id
        self.assertEqual(status, 403)
        for root, _, files in os.walk(self.tmp.name):                  # no token on either machine's folders
            if root.startswith(os.path.join(self.tmp.name, "a")) or root.startswith(os.path.join(self.tmp.name, "b")):
                for f in files:
                    with open(os.path.join(root, f), "rb") as fh:
                        self.assertNotIn(b"tok-", fh.read())
        with self.app.store.conn() as c:
            rows = c.execute("SELECT machine_id, session, decision FROM secret_uses ORDER BY at").fetchall()
            text = json.dumps([list(r) for r in c.execute("SELECT * FROM secret_uses").fetchall()])
        self.assertEqual([tuple(r) for r in rows], [("m-a", "S1", "allow"), ("m-b", "S9", "allow"),
                                                    ("m-a", "S1", "deny"), ("m-a", "S9", "deny")])
        self.assertNotIn("v-obs", text)
        with open(os.path.join(self.op_dir, "reads.log")) as fh:      # 1Password saw only the broker's two reads
            self.assertEqual(len(fh.read().splitlines()), 2)

    def test_no_token_on_the_broker_refuses(self):
        os.remove(os.path.join(self.tokens, "t", "obs_reader.token"))
        self.present(self.a, "S1")
        status, why = self.use(self.a, "S1")
        self.assertEqual(status, 409)
        self.assertIn("of secret plan", why)


class GuardTest(unittest.TestCase):
    def test_the_brokers_token_folder_is_denied_to_every_session(self):
        """of_architect's review (a): on the founder's Mac the broker shares the agents' OS user."""
        import of_guard
        d = os.path.expanduser("~/.config/of/service-accounts/acme")
        for tool, inp in (("Read", {"file_path": d + "/obs_reader.token"}), ("Read", {"file_path": d + "/notes"}),
                          ("Bash", {"command": f"cat {d}/notes"}), ("Bash", {"command": f"cp -r {d} /tmp/x"})):
            self.assertTrue(of_guard.critical_deny(tool, inp), (tool, inp))
        self.assertIsNone(of_guard.critical_deny("Bash", {"command": f"ls {d}"}))   # names only


class ClientTest(unittest.TestCase):
    class Fake:
        def __init__(self, value):
            self.value, self.calls = value, []

        def use_secret(self, org, position, session, credential):
            self.calls.append((org, position, session, credential))
            return {"value": self.value}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = os.path.join(self.tmp.name, "state")
        os.makedirs(of_events.STATE_DIR)
        os.makedirs(os.path.join(self.tmp.name, "org", "registry"))
        with open(os.path.join(self.tmp.name, "org", "registry", "positions.yaml"), "w") as fh:
            fh.write(REGISTRY.format(reader="S1"))
        self.orgs = os.path.join(self.tmp.name, "orgs.yaml")
        with open(self.orgs, "w") as fh:
            fh.write("orgs:\n  - {name: t, path: org}\n")

    def tearDown(self):
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def test_run_hands_the_value_to_the_command_only(self):
        fake = self.Fake("v-obs")
        code = of_secret.run("grafana-viewer", [sys.executable, "-c",
                             "import os, sys; sys.exit(0 if os.environ['GRAFANA_VIEWER'] == 'v-obs' else 3)"],
                             client=fake, orgs_file=self.orgs, session="S1")
        self.assertEqual(code, 0)
        self.assertEqual(fake.calls, [("t", "obs_reader", "S1", "grafana-viewer")])
        probe = os.path.join(self.tmp.name, "seen")
        code = of_secret.run("grafana-viewer", [sys.executable, "-c", textwrap.dedent(f"""\
            import os, stat
            p = os.environ['CFG']
            assert open(p).read() == 'v-obs' and stat.S_IMODE(os.stat(p).st_mode) == 0o600
            open({probe!r}, 'w').write(p)""")], file_name="CFG", client=fake, orgs_file=self.orgs, session="S1")
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(open(probe).read()))            # removed when the command ended
        with self.assertRaises(of_secret.SecretError):
            of_secret.run("grafana-viewer", ["true"], client=fake, orgs_file=self.orgs, session="S-nobody")
        import of_store
        conn = of_store.connect()
        rows = [json.loads(r[0]) for r in conn.execute("SELECT record FROM event").fetchall()]
        conn.close()
        used = [r.get("of.payload") for r in rows if r.get("event.name") == "of.secret_used"]
        self.assertEqual([u["decision"] for u in used], ["allow", "allow"])
        self.assertNotIn("v-obs", json.dumps(rows))

    def test_plan_gives_each_position_exactly_its_vaults(self):
        lines, notes = of_secret.plan(orgs_file=self.orgs, tokens_dir="~/T")
        got = {pos: line for _, pos, line in lines}
        self.assertIn("--vault t-obs:read_items --raw > ~/T/t/obs_reader.token", got["obs_reader"])
        self.assertIn("--vault t-obs:read_items,write_items", got["obs_keeper"])
        self.assertIn("--vault t-k8s:read_items,write_items", got["k8s_keeper"])
        self.assertTrue(all(line.startswith("umask 077;") for line in got.values()))
        self.assertTrue(any("legacy-file has no ref" in n for n in notes))

    def test_the_registry_check_reads_refs(self):
        bad = REGISTRY.format(reader="S1").replace('ref: "op://t-k8s/kube-admin/credential"',
                                                   'ref: "op://other-k8s/kube-admin/credential"')
        bad = bad.replace("  legacy-file:\n    paths: [\"~/.config/t/legacy.cfg\"]\n", "  legacy-file:\n")
        with open(os.path.join(self.tmp.name, "org", "registry", "positions.yaml"), "w") as fh:
            fh.write(bad + "  - {name: x, credentials: []}\n")
        problems = of_credentials.check(self.orgs)
        self.assertTrue(any("not one of t's" in p for p in problems), problems)
        self.assertTrue(any("legacy-file: needs ref" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
