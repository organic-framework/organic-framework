"""The keeper's check before it serves a line (docs/access.md §4; bin/of_keeper.py): a passkey approval of the exact
line, made by a virtual authenticator (tests/test_passkey.py), and for money a grant signed with a grant key made here
by ssh-keygen, bounding the line's limit, not revoked. A made-up organization; nothing here holds a value."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
sys.path.insert(0, os.path.join(HERE, ".."))
import of_access  # noqa: E402
import of_grants  # noqa: E402
import of_keeper  # noqa: E402
import of_passkey as P  # noqa: E402
from tests.test_passkey import Authenticator  # noqa: E402

ORG, ORIGIN, RP = "acme", "https://of.acme.example", "of.acme.example"
NOW = "2026-11-19T12:00:00Z"
LIMIT = {"per_call": {"amount": 0.5, "unit": "ETH"}, "per_period": {"amount": 2, "unit": "ETH", "period": "P1D"}}
GIT = ["git", "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.com"]


class KeeperCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.grant_key = os.path.join(cls.tmp.name, "grant")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", cls.grant_key], check=True,
                       env=of_grants.no_agent_env())
        with open(cls.grant_key + ".pub") as fh:
            cls.keys = of_grants.Keys(ed25519=[fh.read().strip()])
        cls.phone = Authenticator(cls.tmp.name, "phone")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        saved = getattr(of_grants, "passkey_revoked", None)
        of_grants.passkey_revoked = lambda org, credential: False
        self.addCleanup(lambda: setattr(of_grants, "passkey_revoked", saved))
        self.root = tempfile.mkdtemp(dir=self.tmp.name)
        os.makedirs(os.path.join(self.root, "registry", "access", "approvals"))
        self.pinned = {"cred-1": "founder"}

    def enrollment(self):
        rec = {"kind": P.KIND, "org": ORG, "person": "founder", "credential": "cred-1", "public_key": self.phone.spki,
               "rp_id": RP, "origin": ORIGIN, "issued_at": "2026-11-19T10:00:00Z", "expires": "2027-11-19T10:00:00Z"}
        rec["signature"] = of_grants.sign_ed25519(rec, self.grant_key)
        return rec

    def approve(self, keeper, entry, rid="r1", credential="cred-1"):
        """The owner's passkey approval of ENTRY as the approval page shows it, saved beside the list."""
        payload = of_keeper.line_payload(ORG, keeper, entry)
        digest = of_keeper.line_digest(payload)
        pre = P.preimage(ORG, payload["id"], digest, "n-" + rid, "2026-11-19T12:15:00Z")
        import hashlib
        rec = dict({"id": payload["id"], "digest": digest, "by": "owner", "via": "passkey", "org": ORG,
                    "credential": credential, "preimage": pre, "enrollment": self.enrollment(),
                    "recorded_at": "2026-11-19T12:05:00Z"},
                   **self.phone.assertion(hashlib.sha256(pre.encode()).digest()))
        with open(os.path.join(self.root, "registry", "access", "approvals", f"{rid}.json"), "w") as fh:
            json.dump(rec, fh)
        return dict(entry, approval=f"passkey:{rid}")

    def grant(self, **over):
        g = {"id": "g-1", "subject": "acme_payer", "issuer": "tenant", "tier": "act_and_report",
             "scope": {"keepers": ["acme-signer:dest:0x12"], "limit": LIMIT}, "expires": "2026-12-19T00:00:00Z"}
        g.update(over)
        g["signature"] = of_grants.sign_ed25519(g, self.grant_key)
        return g

    def check(self, keeper, kind, entry, grants=(), **kw):
        return of_keeper.line_problem(ORG, keeper, kind, entry, root=self.root, positions={"acme_obs", "acme_payer"},
                                      keys=self.keys, pinned=kw.pop("pinned", self.pinned), grants=list(grants),
                                      now_iso=NOW, server=ORIGIN + "/", **kw)

    def vault_line(self, **over):
        e = {"position": "acme_obs", "access": "item:abc", "actions": ["read"], "limit": None, "tier": "secret",
             "decided": "AC-7", "added": {"at": "2026-11-19T11:00:00Z", "by": "acme_org"}, "expires": None}
        e.update(over)
        return e

    def signer_line(self, **over):
        e = {"position": "acme_payer", "access": "dest:0x12", "actions": ["send"], "limit": LIMIT, "tier": "money",
             "decided": "AC-8", "grant": "g-1", "added": {"at": "2026-11-19T11:00:00Z", "by": "acme_org"},
             "expires": None}
        e.update(over)
        return e

    def test_a_secret_line_is_served_only_with_the_owner_s_approval_of_that_exact_line(self):
        line = self.approve("acme-vault", self.vault_line())
        self.assertIsNone(self.check("acme-vault", "vault", line))
        self.assertIsNone(self.check("acme-vault", "vault", dict(line, added={"at": "2026-11-20T00:00:00Z",
                                                                              "by": "owner"})))  # not signed
        edited = dict(line, access="item:other")  # a line changed after his yes
        self.assertIn("another form", self.check("acme-vault", "vault", edited))
        self.assertIn("no passkey record", self.check("acme-vault", "vault", dict(line, approval="passkey:none")))
        self.assertIn("not one this keeper pins", self.check("acme-vault", "vault", line, pinned={"cred-9": "x"}))
        self.assertIn("expired", self.check("acme-vault", "vault", dict(line, expires="2026-11-19T11:59:00Z")))

    def test_a_money_line_needs_a_signed_grant_that_bounds_it(self):
        line = self.approve("acme-signer", self.signer_line())
        g = self.grant()
        self.assertIsNone(self.check("acme-signer", "signer", line, [g]))
        wider = self.approve("acme-signer", self.signer_line(
            limit={**LIMIT, "per_call": {"amount": 1, "unit": "ETH"}}), rid="r2")
        self.assertIn("above the grant", self.check("acme-signer", "signer", wider, [g]))
        cases = {
            "not in the committed": [],
            "bad or missing signature": [dict(g, scope={**g["scope"], "limit": {"per_call": {"amount": 9, "unit": "ETH"},
                                                                            "per_period": LIMIT["per_period"]}})],
            "expired": [self.grant(expires="2026-11-19T00:00:00Z")],
            "does not name": [self.grant(scope={"keepers": ["acme-signer:dest:0x99"], "limit": LIMIT})],
            "not acme_payer": [self.grant(subject="acme_obs")],
            "no expiry": [self.grant(expires=None)],
        }
        for why, grants in cases.items():
            got = self.check("acme-signer", "signer", line, grants)
            self.assertTrue(got and why in got, (why, got))
        self.assertIn("revoked", self.check("acme-signer", "signer", line, [g], revoked={"g-1"}))
        self.assertIn("cannot read its sources", self.check("acme-signer", "signer", line, [g], sources_ok=False))
        self.assertIn("money at least", self.check("acme-signer", "signer", dict(line, tier="secret"), [g]))

    def test_the_revocation_log_never_takes_one_back(self):
        log = of_keeper.RevocationLog(os.path.join(self.root, "keeper", "revocations.jsonl"))
        a, b = self.grant(), self.grant(id="g-2")
        self.assertEqual(log.observe([a, b]), set())
        self.assertEqual(log.observe([a]), {"g-2"})                     # gone from the committed file
        self.assertEqual(log.observe([a, b]), {"g-2"})                  # an older file restored: still revoked
        self.assertEqual(log.observe([a], revoked_events=["g-1"]), {"g-1", "g-2"})  # an event, signed or not
        self.assertEqual(of_keeper.RevocationLog(log.path).revoked(), {"g-1", "g-2"})

    def test_sources_are_read_forward_only(self):
        repo = os.path.join(self.root, "repo")
        subprocess.run(GIT + ["init", "-q", repo], check=True)
        shas = []
        for i in range(2):
            subprocess.run(GIT + ["-C", repo, "commit", "-q", "--allow-empty", "-m", f"c{i}"], check=True)
            shas.append(subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True,
                                       check=True).stdout.strip())
        self.assertTrue(of_keeper.descends(repo, None, shas[0]))
        self.assertTrue(of_keeper.descends(repo, shas[0], shas[1]))
        self.assertFalse(of_keeper.descends(repo, shas[1], shas[0]))  # an older commit: not read

    def test_a_keepers_grant_has_its_shape_and_a_narrower_limit_attenuates(self):
        good = self.grant()
        self.assertEqual(of_grants.shape_problems(good), [])
        for over, why in (({"expires": None}, "expiry"), ({"effect": "deny"}, "only on an allow"),
                          ({"scope": {"keepers": ["a:x", "b:y"], "limit": LIMIT}}, "exactly one line"),
                          ({"scope": {"keepers": ["a:x"]}}, "per_call or per_period"),
                          ({"scope": {"to_org": ["x"], "limit": LIMIT}}, "only with keepers")):
            got = " ".join(of_grants.shape_problems(dict(good, **over)))
            self.assertIn(why, got, over)
        self.assertIsNone(of_access.limit_within({"per_call": {"amount": 0.1, "unit": "ETH"}},
                                                 {"per_call": {"amount": 0.5, "unit": "ETH"}}))
        issuer = dict(good, scope={**good["scope"], "grants": {"depth": 2}})
        wide = dict(good, scope={"keepers": good["scope"]["keepers"], "limit": {**LIMIT, "per_call": {"amount": 5,
                                                                                                   "unit": "ETH"}}})
        self.assertTrue(any(v.startswith("limit:") for v in of_grants.attenuates(wide, issuer)))
        self.assertFalse(any(v.startswith("limit:") for v in of_grants.attenuates(good, issuer)))

    def test_the_registry_check_names_a_line_its_record_does_not_approve(self):
        line = self.approve("acme-vault", self.vault_line())
        doc = {"keeper": "acme-vault", "kind": "vault", "host": "h1", "entries": [line, dict(line, access="item:x")]}
        notes = of_access._approval_notes(ORG, self.root, "acme-vault", doc, keys=self.keys)
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("entry 2: its approval does not approve the line"), notes)


    def test_recording_an_approval_names_it_on_the_line(self):
        os.makedirs(os.path.join(self.root, "registry"), exist_ok=True)
        with open(os.path.join(self.root, "registry", "positions.yaml"), "w") as fh:
            fh.write("positions:\n  - name: acme_obs\n  - name: acme_org\n")
        e = of_access.grant(self.root, "acme-vault", "acme_obs", "item:abc", actions=["read"], tier="secret",
                            decided="AC-7", approval="passkey:pending", by="acme_org", kind="vault", host="h1",
                            now_iso=NOW, resolve=lambda c: True)
        self.approve("acme-vault", e, rid="ev-1")
        with open(os.path.join(self.root, "registry", "access", "approvals", "ev-1.json")) as fh:
            rec = json.load(fh)
        got = of_access.record_approval(self.root, "acme-vault", "acme_obs", "item:abc", "ev-2", rec)
        self.assertEqual(got["approval"], "passkey:ev-2")
        _, line = of_access.find_line(self.root, "acme-vault", "acme_obs", "item:abc")
        self.assertIsNone(self.check("acme-vault", "vault", line))
        with self.assertRaises(of_access.AccessError):
            of_access.record_approval(self.root, "acme-vault", "acme_obs", "item:abc", "../x", rec)

    def test_the_five_points_of_the_review_of_pr_357(self):
        """of_secrets-review on organic-framework PR 357 at 6e867cf."""
        line = self.approve("acme-vault", self.vault_line())
        # (1) an unknown kind serves nothing
        self.assertIn("no keeper kind", self.check("acme-vault", "vaults", line))
        # (2) a browser keeper with no money-site list serves nothing; a payment site is money at least
        site = self.approve("acme-web", self.vault_line(access="site:pay.example.com", actions=["download"],
                                                         limit={"per_period": {"amount": 20, "unit": "downloads",
                                                                               "period": "P1D"}}), rid="w1")
        self.assertIn("pins no money-site list", self.check("acme-web", "browser", site))
        self.assertIn("payment or banking site", self.check("acme-web", "browser", site, money_sites={"example.com"}))
        self.assertIsNone(self.check("acme-web", "browser", site, money_sites={"bank.example.org"}))
        # (3) a revocation log that cannot be read whole raises, so the keeper's sources count as unreadable
        log = of_keeper.RevocationLog(os.path.join(self.root, "k", "revocations.jsonl"))
        log.observe([self.grant()])
        with open(log.path, "a") as fh:
            fh.write('{"grant": "g-2", "rev')                          # a torn record
        with self.assertRaises(OSError):
            log.revoked()
        with open(log.path, "w") as fh:
            fh.write("")                                                # truncated below its count
        with self.assertRaises(OSError):
            log.revoked()
        # (4) a line removed and added again does not come back on its old approval
        alog = of_keeper.ApprovalLog(os.path.join(self.root, "k", "approvals.jsonl"))
        nonce = of_keeper.signed_nonce(of_keeper.approval_record(self.root, line["approval"]))
        self.assertEqual(nonce, "n-r1")
        self.assertEqual(alog.observe_lines("acme-vault", [line], self.root), set())
        self.assertEqual(alog.observe_lines("acme-vault", [], self.root), {nonce})       # the line is gone
        self.assertEqual(alog.observe_lines("acme-vault", [line], self.root), {nonce})   # added again: withdrawn
        self.assertIn("removed since", self.check("acme-vault", "vault", line, withdrawn={nonce}))
        import shutil  # the same record copied under a new file name: the same signed nonce, still refused
        d = os.path.join(self.root, "registry", "access", "approvals")
        shutil.copy(os.path.join(d, "r1.json"), os.path.join(d, "r9.json"))
        self.assertIn("removed since", self.check("acme-vault", "vault", dict(line, approval="passkey:r9"),
                                                  withdrawn={nonce}))
        # (5) amounts are exact and finite
        for bad in (float("nan"), float("inf"), "1e3", True, -1, 0):
            self.assertTrue(of_access.limit_shape({"per_call": {"amount": bad, "unit": "wei"}}), bad)
        wei = {"per_call": {"amount": 10 ** 18, "unit": "wei"}}
        self.assertIn("above the grant", of_access.limit_within({"per_call": {"amount": 10 ** 18 + 1, "unit": "wei"}}, wei))
        self.assertIsNone(of_access.limit_within({"per_call": {"amount": "0.5", "unit": "ETH"}},
                                                 {"per_call": {"amount": 0.5, "unit": "ETH"}}))
        self.assertIn("not a finite number", of_access.limit_within({"per_call": {"amount": float("nan"),
                                                                                  "unit": "wei"}}, wei))

if __name__ == "__main__":
    unittest.main()
