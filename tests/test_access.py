"""`of access map` (docs/access.md §2, §6 step 1; bin/of_access.py): who reaches what, from the credentials catalogue
and grants, approved by which commit, last used by the broker's records. A made-up organization in a temporary
repository; nothing here opens a credential file."""

import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_access  # noqa: E402
import of_grants  # noqa: E402

GIT = ["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.com"]

REGISTRY = textwrap.dedent("""\
    organization: acme
    credentials:
      grafana-viewer:
        paths: ["~/.config/acme/grafana/viewer.token"]
        acts_as: "Grafana viewer"
        keeper: obs_keeper
      payer-key:
        ref: "op://acme-pay/payer/key"
        keeper: pay_keeper
        custody: true
      spare:
        paths: ["~/.config/acme/spare.token"]
    positions:
      - name: obs_keeper
      - name: pay_keeper
      - name: acme_obs
        credentials: [grafana-viewer]
      - name: acme_payer
        credentials:
          - payer-key
""")


class AccessMapTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, "acme")
        os.makedirs(os.path.join(self.repo, "registry"))
        with open(os.path.join(self.repo, "registry", "positions.yaml"), "w") as fh:
            fh.write(REGISTRY)
        subprocess.run(GIT + ["init", "-q", self.repo], check=True)
        subprocess.run(GIT + ["-C", self.repo, "add", "registry/positions.yaml"], check=True)
        subprocess.run(GIT + ["-C", self.repo, "commit", "-q", "-m", "registry\n\nPosition: acme_org"], check=True)
        self.orgs = os.path.join(self.tmp, "orgs.yaml")
        with open(self.orgs, "w") as fh:
            fh.write(f"orgs:\n  - name: acme\n    path: {self.repo}\n")
        self.keys = of_grants.Keys()
        self.now = "2026-11-19T10:00:00Z"

    def build(self, **kw):
        kw.setdefault("uses", {})
        return of_access.build(orgs_file=self.orgs, keys=self.keys, now_iso=self.now, **kw)

    def test_reach_from_the_catalogue_with_its_commit(self):
        m = self.build()[0]
        got = {(r["position"], r["access"]): r for r in m["rows"]}
        self.assertEqual(set(got), {("acme_obs", "grafana-viewer"), ("obs_keeper", "grafana-viewer"),
                                    ("acme_payer", "payer-key"), ("pay_keeper", "payer-key")})
        self.assertEqual(got[("acme_obs", "grafana-viewer")]["how"], "lists")
        self.assertEqual(got[("obs_keeper", "grafana-viewer")]["how"], "keeps")
        self.assertEqual(got[("acme_obs", "grafana-viewer")]["through"], "file")
        self.assertEqual(got[("acme_payer", "payer-key")]["through"], "1Password via the broker")
        self.assertTrue(got[("acme_payer", "payer-key")]["custody"])
        self.assertTrue(got[("acme_payer", "payer-key")]["approved"].endswith(" acme_org"),
                        got[("acme_payer", "payer-key")]["approved"])
        self.assertEqual(m["unreached"], ["spare"])
        self.assertEqual(m["problems"], [])

    def test_an_uncommitted_listing_says_so(self):
        p = os.path.join(self.repo, "registry", "positions.yaml")
        with open(p, "a") as fh:
            fh.write("  - name: acme_new\n    credentials: [spare]\n")
        got = {(r["position"], r["access"]): r for r in self.build()[0]["rows"]}
        self.assertEqual(got[("acme_new", "spare")]["approved"], "uncommitted")
        self.assertNotEqual(got[("acme_obs", "grafana-viewer")]["approved"], "uncommitted")

    def test_last_use_comes_from_the_broker_and_says_where_nothing_was_seen(self):
        at = time.time() - 3600
        got = {(r["position"], r["access"]): r["last_used"]
               for r in self.build(uses={("acme", "acme_payer", "payer-key"): at})[0]["rows"]}
        self.assertRegex(got[("acme_payer", "payer-key")], r"^\d{4}-\d\d-\d\dT\d\d:\d\dZ$")
        self.assertEqual(got[("pay_keeper", "payer-key")], "none here")
        self.assertEqual(got[("acme_obs", "grafana-viewer")], "not recorded")
        state = os.path.join(self.tmp, "server")
        self.assertIsNone(of_access.broker_uses(state))  # no server on this machine: nothing observed here
        os.makedirs(state)
        c = sqlite3.connect(os.path.join(state, "server.db"))
        c.execute("CREATE TABLE secret_uses (at REAL NOT NULL, org TEXT, position TEXT, session TEXT, machine_id TEXT,"
                  " credential TEXT, decision TEXT NOT NULL, reason TEXT)")
        c.executemany("INSERT INTO secret_uses VALUES (?,?,?,?,?,?,?,?)",
                      [(at, "acme", "acme_payer", "s", "m", "payer-key", "allow", "read"),
                       (at + 60, "acme", "acme_payer", "s", "m", "payer-key", "deny", "not the holder")])
        c.commit()
        c.close()
        self.assertEqual(of_access.broker_uses(state), {("acme", "acme_payer", "payer-key"): at})

    def test_grants_and_one_position(self):
        with open(os.path.join(self.repo, "registry", "grants.yaml"), "w") as fh:
            fh.write(textwrap.dedent("""\
                grants:
                  - id: g-1
                    subject: acme_obs
                    issuer: tenant
                    tier: observe
                    scope: {data: [finance]}
                    expires: 2026-12-01T00:00:00Z
                    provenance: {source: "card AC-1"}
                  - id: g-2
                    subject: acme_payer
                    tier: act_and_report
                    scope: {to_org: other}
            """))
        maps = self.build(position="acme_obs")
        self.assertEqual([r["position"] for r in maps[0]["rows"]], ["acme_obs"])
        self.assertEqual([g["grant"] for g in maps[0]["grants"]], ["g-1"])
        g = maps[0]["grants"][0]
        self.assertEqual(g["scope"], "data: finance")
        self.assertEqual(g["limit"], "until 2026-12-01T00:00:00Z")
        self.assertEqual(g["state"], "bad or missing signature")  # unsigned: never shown as in force
        self.assertIn("card AC-1", g["approved"])
        text = of_access.render(maps)
        self.assertIn("acme_obs", text)
        self.assertNotIn("acme_payer", text)

    def test_an_unknown_organization_and_a_listed_credential_the_catalogue_lacks(self):
        with self.assertRaises(of_access.AccessError):
            self.build(org="nobody")
        p = os.path.join(self.repo, "registry", "positions.yaml")
        with open(p, "a") as fh:
            fh.write("  - name: acme_x\n    credentials: [missing]\n")
        m = self.build(org="acme")[0]
        self.assertIn("acme_x lists missing, which the catalogue does not define", m["problems"])
        self.assertEqual({r["through"] for r in m["rows"] if r["access"] == "missing"}, {"nothing named"})


if __name__ == "__main__":
    unittest.main()
