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


LIMIT = {"per_call": {"amount": 0.5, "unit": "ETH"}, "per_period": {"amount": 2, "unit": "ETH", "period": "P1D"}}


class KeeperListTest(AccessMapTest):
    """Step 2: a keeper's list, written only by grant and revoke, and checked as `of registry check` checks it."""

    def grant(self, *a, **kw):
        kw.setdefault("by", "acme_org")
        kw.setdefault("now_iso", self.now)
        kw.setdefault("resolve", lambda card: card == "AC-7")
        return of_access.grant(self.repo, *a, **kw)

    def test_a_line_is_checked_before_it_is_written(self):
        with self.assertRaisesRegex(of_access.AccessError, "give --kind"):
            self.grant("acme-vault", "acme_obs", "item:abc", actions=["read"], tier="secret", decided="AC-7",
                       approval="passkey:r1")
        bad = [({"tier": "plain", "decided": "AC-7"}, "secret at least"),
               ({"tier": "secret", "decided": "AC-7"}, "passkey"),
               ({"tier": "secret", "decided": "abc1234", "approval": "passkey:r1"}, "founder's card"),
               ({"tier": "secret", "decided": "AC-8", "approval": "passkey:r1"}, "no ask on this machine")]
        for kw, why in bad:
            with self.assertRaisesRegex(of_access.AccessError, why):
                self.grant("acme-vault", "acme_obs", "item:abc", actions=["read"], kind="vault", host="h1", **kw)
        with self.assertRaisesRegex(of_access.AccessError, "access is item:"):
            self.grant("acme-vault", "acme_obs", "dest:0x12", actions=["read"], tier="secret", decided="AC-7",
                       approval="passkey:r1", kind="vault", host="h1")
        self.assertFalse(os.path.exists(of_access.list_path(self.repo, "acme-vault")))
        e = self.grant("acme-vault", "acme_obs", "item:abc", actions=[], tier="secret", decided="AC-7",
                       approval="passkey:r1", kind="vault", host="h1")
        self.assertEqual(e["actions"], ["read"])
        with open(of_access.list_path(self.repo, "acme-vault")) as fh:
            self.assertTrue(fh.read().startswith("# A keeper's list"))
        with self.assertRaisesRegex(of_access.AccessError, "already"):
            self.grant("acme-vault", "acme_obs", "item:abc", actions=["read"], tier="secret", decided="AC-7",
                       approval="passkey:r1")

    def test_a_signer_line_is_money_with_a_signed_grant_and_both_caps(self):
        kw = dict(actions=["send"], kind="signer", host="h1", decided="AC-7", approval="passkey:r1")
        with self.assertRaisesRegex(of_access.AccessError, "money at least"):
            self.grant("acme-signer", "acme_payer", "dest:0x12", tier="secret", limit=LIMIT, **kw)
        with self.assertRaisesRegex(of_access.AccessError, "grant the founder signed"):
            self.grant("acme-signer", "acme_payer", "dest:0x12", tier="money", limit=LIMIT, **kw)
        with self.assertRaisesRegex(of_access.AccessError, "not in the organization"):
            self.grant("acme-signer", "acme_payer", "dest:0x12", tier="money", limit=LIMIT, grant_id="g-9", **kw)
        with open(os.path.join(self.repo, "registry", "grants.yaml"), "w") as fh:
            fh.write("grants:\n  - id: g-9\n    subject: acme_payer\n    tier: act_and_report\n")
        with self.assertRaisesRegex(of_access.AccessError, "per_period"):
            self.grant("acme-signer", "acme_payer", "dest:0x12", tier="money", grant_id="g-9",
                       limit={"per_call": LIMIT["per_call"]}, **kw)
        with self.assertRaisesRegex(of_access.AccessError, "ISO 8601"):
            self.grant("acme-signer", "acme_payer", "dest:0x12", tier="money", grant_id="g-9",
                       limit={**LIMIT, "per_period": {"amount": 2, "unit": "ETH", "period": "a day"}}, **kw)
        e = self.grant("acme-signer", "acme_payer", "dest:0x12", tier="money", grant_id="g-9", limit=LIMIT, **kw)
        self.assertEqual(e["grant"], "g-9")

    def test_revoke_removes_one_line_and_the_map_and_check_read_the_lists(self):
        self.grant("acme-vault", "acme_obs", "item:abc", actions=["read"], tier="secret", decided="AC-7",
                   approval="passkey:r1", kind="vault", host="h1")
        self.grant("acme-vault", "acme_payer", "item:def", actions=["read"], tier="secret", decided="AC-7",
                   approval="passkey:r2")
        rows = [r for r in self.build()[0]["rows"] if r.get("keeper") == "acme-vault"]
        self.assertEqual({(r["position"], r["access"]) for r in rows}, {("acme_obs", "item:abc"),
                                                                        ("acme_payer", "item:def")})
        self.assertTrue(all(r["approved"].endswith("uncommitted: serves nothing") for r in rows), rows)
        got = of_access.check_lists(self.orgs, resolve=lambda c: c == "AC-7")
        self.assertEqual(len(got), 2)  # the lines' passkey records are not written here (tests/test_keeper.py)
        self.assertTrue(all("has no record in registry/access/approvals/" in g for g in got), got)
        removed = of_access.revoke(self.repo, "acme-vault", "acme_obs", "item:abc")
        self.assertEqual(removed["access"], "item:abc")
        rows = [r for r in self.build()[0]["rows"] if r.get("keeper") == "acme-vault"]
        self.assertEqual([(r["position"], r["access"]) for r in rows], [("acme_payer", "item:def")])
        with self.assertRaises(of_access.AccessError):
            of_access.revoke(self.repo, "acme-vault", "acme_obs", "item:abc")

    def test_the_check_names_a_hand_written_list_s_problems(self):
        d = os.path.join(self.repo, "registry", "access")
        os.makedirs(d)
        with open(os.path.join(d, "acme-signer.yaml"), "w") as fh:
            fh.write(textwrap.dedent("""\
                keeper: other
                kind: signer
                host: h1
                entries:
                  - position: nobody
                    access: "dest:0x12"
                    actions: [send]
                    tier: plain
                    decided: AC-7
                    added: {at: 2026-11-19T10:00:00Z, by: acme_org}
                  - position: acme_payer
                    access: "dest:0x34"
                    actions: [send]
                    tier: money
                    decided: AC-7
                    limit: {per_call: {amount: 1, unit: ETH}, per_period: {amount: 2, unit: ETH, period: P1D}}
                    added: {at: "2026-11-19T10:00:00Z", by: acme_org}
            """))
        with open(os.path.join(d, "Notes.txt"), "w") as fh:
            fh.write("x")
        got = "\n".join(of_access.check_lists(self.orgs, resolve=lambda c: True))
        for why in ("not a keeper's list", "not the file's name", "entry 1: position 'nobody' is no position",
                    "entry 1: a signer entry is money at least", "entry 1: a signer entry needs a limit",
                    "entry 2: a money entry needs approval", "entry 2: a money entry needs the grant"):
            self.assertIn(why, got)
        self.assertNotIn("added is", got)  # an unquoted time, as YAML reads it, is a time
        self.assertTrue(all(ln.startswith("acme:registry/access/") for ln in got.splitlines()))

    def test_decided_is_the_cards_full_event_id_or_its_card_id(self):
        """of_review on PR 357: the documented full event id was refused."""
        full = "01a1f000-0000-7000-8000-000000000123"
        e = self.grant("acme-vault", "acme_obs", "item:abc", actions=["read"], tier="secret", decided=full,
                       approval="passkey:r1", kind="vault", host="h1", resolve=lambda c: c == full)
        self.assertEqual(e["decided"], full)
        with self.assertRaisesRegex(of_access.AccessError, "no ask on this machine"):
            self.grant("acme-vault", "acme_payer", "item:def", actions=["read"], tier="secret",
                       decided="01a1f000-0000-7000-8000-000000000999", approval="passkey:r2",
                       resolve=lambda c: c == full)

    def test_the_request_names_the_floor_and_the_steps(self):
        text = of_access.request_text("acme-signer", {"kind": "signer", "host": "h1"}, "acme_payer", "dest:0x12",
                                      ["send"], LIMIT, "pay the hosting invoice")
        self.assertIn("floor is money", text)
        self.assertIn("pay the hosting invoice", text)
        self.assertIn("of delegate", text)


class DigestTest(unittest.TestCase):
    """Step 5: the Monday review to each structure keeper (docs/access.md §5)."""

    MONDAY = 1795410000.0  # 2026-11-23T05:00:00Z, a Monday

    def setUp(self):
        AccessMapTest.setUp(self)  # the same made-up organization, without AccessMapTest's own tests
        self.state = tempfile.mkdtemp()
        import of_events
        self._sd = of_events.STATE_DIR
        of_events.STATE_DIR = self.state
        self.addCleanup(lambda: setattr(of_events, "STATE_DIR", self._sd))
        d = os.path.join(self.repo, "registry", "access")
        os.makedirs(d)
        with open(os.path.join(d, "acme-vault.yaml"), "w") as fh:
            fh.write(textwrap.dedent("""\
                keeper: acme-vault
                kind: vault
                host: h1
                entries:
                  - position: acme_obs
                    access: "item:old"
                    actions: [read]
                    tier: secret
                    decided: AC-7
                    approval: "passkey:r1"
                    added: {at: "2026-09-01T10:00:00Z", by: acme_org}
            """))
        with open(os.path.join(self.repo, "registry", "grants.yaml"), "w") as fh:
            fh.write("grants:\n  - id: g-soon\n    subject: acme_payer\n    tier: observe\n"
                     "    expires: \"2026-12-03T00:00:00Z\"\n")
        subprocess.run(GIT + ["-C", self.repo, "add", "-A"], check=True)
        subprocess.run(GIT + ["-C", self.repo, "commit", "-q", "-m", "lists"], check=True)
        self.positions = [{"name": "acme_org", "role": "org", "_org": "acme"}]

    def test_the_digest_names_unused_lines_with_their_revoke_and_soon_expiring_grants(self):
        text = of_access.digest_text("acme", self.repo, None, self.MONDAY)
        self.assertIn("never used", text)
        self.assertIn('`of access revoke acme-vault acme_obs item:old --why "unused for 30 days"`', text)
        self.assertIn("g-soon for acme_payer: expires 2026-12-03T00:00:00Z", text)
        self.assertIn("Granted this week", text)  # committed within the week

    def test_it_goes_once_a_week_on_mondays_from_the_keeper_s_own_position(self):
        sent = []
        send = lambda to, text, log: sent.append((to, text)) or True  # noqa: E731
        wednesday = self.MONDAY + 2 * 86400
        self.assertEqual(of_access.send_digests(self.positions, self.orgs, None, wednesday, send=send), 0)
        self.assertEqual(of_access.send_digests(self.positions, self.orgs, None, self.MONDAY, send=send), 1)
        self.assertEqual(of_access.send_digests(self.positions, self.orgs, None, self.MONDAY + 3600, send=send), 0)
        self.assertEqual(sent[0][0], "acme:acme_org")
        self.assertEqual(of_access.send_digests(self.positions, self.orgs, None, self.MONDAY + 7 * 86400, send=send),
                         1)  # the next week

if __name__ == "__main__":
    unittest.main()
