"""Tests for grants: signatures, attenuation-only delegation, specificity-first matching."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_grants as G  # noqa: E402

KEY = G.purpose_key("of/grant/v1", master=b"test-master")
NOW = "2026-11-05T12:00:00Z"


def grant(**kw):
    g = {"id": "g", "subject": "s", "issuer": "tenant", "tier": "act_and_report", "scope": {}}
    g.update(kw)
    return g


class SignatureTest(unittest.TestCase):
    def test_sign_verify_and_tamper(self):
        g = grant(scope={"repos": ["~/a"]})
        g["signature"] = G.sign(g, KEY)
        self.assertEqual(G.valid(g, KEY, NOW), (True, "ok"))
        g["scope"]["repos"] = ["~"]  # widened after signing
        self.assertEqual(G.valid(g, KEY, NOW)[1], "bad or missing signature")

    def test_expiry_revocation_counter(self):
        for extra, reason in (({"expires": "2026-11-01T00:00:00Z"}, "expired"),
                              ({"revoked": {"at": NOW, "by": "tenant"}}, "revoked")):
            g = grant(**extra)
            g["signature"] = G.sign(g, KEY)
            self.assertEqual(G.valid(g, KEY, NOW)[1], reason)
        g = grant(counter=2)
        g["signature"] = G.sign(g, KEY)
        self.assertEqual(G.valid(g, KEY, NOW, uses=2)[1], "counter exhausted")

    def test_purpose_keys_are_separated(self):
        self.assertNotEqual(G.purpose_key("of/grant/v1", b"m"), G.purpose_key("of/secret/v1", b"m"))


class Ed25519Test(unittest.TestCase):
    """Asymmetric grant signatures: servers verify with pinned public keys and cannot forge."""

    @classmethod
    def setUpClass(cls):
        import subprocess
        import tempfile
        cls.tmp = tempfile.TemporaryDirectory()
        cls.key, cls.other = (os.path.join(cls.tmp.name, n) for n in ("tenant", "intruder"))
        for k in (cls.key, cls.other):
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", k], check=True,
                           env=G.no_agent_env())
        cls.pub = open(cls.key + ".pub").read().strip()
        cls.other_pub = open(cls.other + ".pub").read().strip()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_pinned_key_verifies_and_tamper_fails(self):
        g = grant(scope={"repos": ["~/a"]})
        g["signature"] = G.sign_ed25519(g, self.key)
        self.assertTrue(g["signature"].startswith("ssh-ed25519:"))
        keys = G.Keys(ed25519=[self.pub])
        self.assertEqual(G.valid(g, keys, NOW), (True, "ok"))
        self.assertFalse(G.verify(dict(g, tier="act_and_report", scope={"repos": ["~"]}), keys))
        self.assertFalse(G.verify(g, G.Keys(ed25519=[self.other_pub])))   # not the pinned key
        self.assertFalse(G.verify(g, G.Keys()))                            # nothing pinned

    def test_an_unpinned_signer_cannot_issue(self):
        g = grant(id="evil")
        g["signature"] = G.sign_ed25519(g, self.other)
        self.assertFalse(G.verify(g, G.Keys(ed25519=[self.pub], hmac_key=KEY)))

    def test_legacy_hmac_only_where_the_hmac_key_is_held(self):
        g = grant()
        g["signature"] = G.sign(g, KEY)
        self.assertTrue(G.verify(g, G.Keys(ed25519=[self.pub], hmac_key=KEY)))
        self.assertFalse(G.verify(g, G.Keys(ed25519=[self.pub])))          # e.g. Acme's server
        self.assertTrue(G.verify(g, KEY))                                  # bytes still mean the HMAC key

    def test_chain_mixes_ed25519_and_legacy(self):
        parent = grant(id="p", subject="lead", tier="act_and_report", scope={"repos": ["~/x/*"], "grants": {"depth": 1}})
        parent["signature"] = G.sign_ed25519(parent, self.key)
        child = grant(id="c", subject="dev", issuer="lead", tier="propose", scope={"repos": ["~/x/a"]})
        child["signature"] = G.sign_ed25519(child, self.key)
        r = G.verify_request({"o": [parent, child]}, G.Keys(ed25519=[self.pub]), NOW, "c", "o:dev")
        self.assertEqual((r["valid"], r["decision"]), (True, "admit"))


class DelegationTest(unittest.TestCase):
    ISSUER = grant(subject="acme_owner", tier="act_with_approval", expires="2026-12-01T00:00:00Z",
                   scope={"repos": ["~/acme/*"], "commands": ["git *"], "grants": {"depth": 1}})

    def test_narrower_grant_is_allowed(self):
        d = grant(issuer="acme_owner", tier="propose", expires="2026-11-20T00:00:00Z",
                  scope={"repos": ["~/acme/storefront"], "commands": ["git status*"]})
        self.assertEqual(G.attenuates(d, self.ISSUER), [])

    def test_escalation_through_delegation_fails(self):
        cases = {
            "wider repo": grant(tier="propose", expires="2026-11-20T00:00:00Z",
                                scope={"repos": ["~/*"], "commands": ["git *"]}),
            "dropped limit": grant(tier="propose", expires="2026-11-20T00:00:00Z", scope={"repos": ["~/acme/x"]}),
            "higher tier": grant(tier="act_and_report", expires="2026-11-20T00:00:00Z",
                                 scope={"repos": ["~/acme/x"], "commands": ["git *"]}),
            "later expiry": grant(tier="propose", expires="2027-01-01T00:00:00Z",
                                  scope={"repos": ["~/acme/x"], "commands": ["git *"]}),
            "no expiry": grant(tier="propose", scope={"repos": ["~/acme/x"], "commands": ["git *"]}),
            "re-delegation": grant(tier="propose", expires="2026-11-20T00:00:00Z",
                                   scope={"repos": ["~/acme/x"], "commands": ["git *"], "grants": {"depth": 1}}),
        }
        for name, d in cases.items():
            self.assertTrue(G.attenuates(d, self.ISSUER), name)

    def test_issuer_without_issue_right_cannot_delegate(self):
        issuer = grant(tier="act_and_report", scope={"repos": ["~/a/*"]})
        d = grant(tier="observe", scope={"repos": ["~/a/b"]})
        self.assertIn("no right to issue", " ".join(G.attenuates(d, issuer)))


def signed(**kw):
    g = grant(**kw)
    g["signature"] = G.sign(g, KEY)
    return g


class ChainTest(unittest.TestCase):
    def setUp(self):
        self.root = signed(id="root", subject="acme_owner", issuer="tenant", tier="act_with_approval",
                           expires="2026-12-01T00:00:00Z",
                           scope={"repos": ["~/acme/*"], "grants": {"depth": 1}})
        self.child = signed(id="child", subject="acme_org", issuer="acme_owner", tier="propose",
                            expires="2026-11-20T00:00:00Z", scope={"repos": ["~/acme/org"]})
        self.by = {"acme_owner": [self.root], "acme_org": [self.child]}

    def test_valid_chain_back_to_the_tenant(self):
        self.assertEqual(G.verify_chain(self.child, self.by, KEY, NOW), (True, "ok"))

    def test_child_falls_with_its_parent(self):
        self.root["revoked"] = {"at": NOW, "by": "tenant"}
        self.root["signature"] = G.sign(self.root, KEY)
        ok, why = G.verify_chain(self.child, self.by, KEY, NOW)
        self.assertFalse(ok)
        self.assertIn("revoked", why)

    def test_escalated_child_is_not_authority_even_if_signed(self):
        wide = signed(id="wide", subject="acme_org", issuer="acme_owner", tier="propose",
                      expires="2026-11-20T00:00:00Z", scope={"repos": ["~/*"]})
        ok, why = G.verify_chain(wide, self.by, KEY, NOW)
        self.assertFalse(ok)
        self.assertIn("outside the issuer", why)

    def test_verify_request_and_decision(self):
        by_org = {"acme": [self.root, self.child]}
        r = G.verify_request(by_org, KEY, NOW, "child", "acme:acme_org", {"repos": "~/acme/org"})
        self.assertEqual((r["valid"], r["decision"], r["deciding_grant"]), (True, "admit", "child"))
        r = G.verify_request(by_org, KEY, NOW, "child", "acme:acme_org", {"repos": "~/organic-framework"})
        self.assertEqual(r["decision"], "escalate")
        r = G.verify_request(by_org, KEY, NOW, "child", "acme:someone_else")
        self.assertFalse(r["valid"])
        r = G.verify_request(by_org, KEY, NOW, "nope", "acme:acme_org")
        self.assertEqual(r["reason"], "unknown grant")


class MatchingTest(unittest.TestCase):
    ACTION = {"tools": "Bash", "repos": "~/acme/storefront", "commands": "kubectl apply -f prod.yaml"}

    def test_broad_grant_does_not_override_narrow_restrictive_one(self):
        broad = grant(id="broad", tier="act_and_report", scope={"repos": ["~/acme/*"]})
        narrow = grant(id="narrow", tier="propose", scope={"repos": ["~/acme/storefront"], "commands": ["kubectl *"]})
        self.assertEqual(G.decide([broad, narrow], self.ACTION)["id"], "narrow")
        self.assertEqual(G.decide([narrow, broad], self.ACTION)["id"], "narrow")

    def test_specific_deny_beats_broad_allow_and_critical_deny_beats_all(self):
        broad = grant(id="broad", scope={"repos": ["~/acme/*"], "commands": ["kubectl apply*"]})
        deny = grant(id="deny", effect="deny",
                     scope={"repos": ["~/acme/storefront"], "commands": ["kubectl apply -f prod*"]})
        self.assertEqual(G.decide([broad, deny], self.ACTION)["id"], "deny")
        crit = grant(id="crit", effect="deny", critical=True, scope={"tools": ["Bash"]})
        very_specific = grant(id="exact", scope={"repos": ["~/acme/storefront"],
                                                 "commands": ["kubectl apply -f prod.yaml"]})
        self.assertEqual(G.decide([very_specific, crit], self.ACTION)["id"], "crit")

    def test_equal_specificity_takes_the_more_restrictive(self):
        a = grant(id="a", tier="act_and_report", scope={"repos": ["~/acme/storefront"]})
        b = grant(id="b", tier="observe", scope={"repos": ["~/acme/storefront"]})
        self.assertEqual(G.decide([a, b], self.ACTION)["id"], "b")

    def test_no_match_is_missing_grant(self):
        self.assertIsNone(G.decide([grant(scope={"repos": ["~/organic-framework"]})], self.ACTION))


if __name__ == "__main__":
    unittest.main()


import of_asks  # noqa: E402


def ev(i, name, sid="S", **payload):
    return {"event_id": f"e{i}", "ts": f"2026-11-05T10:{i:02d}:00Z", "name": name, "session_id": sid,
            "position": "p", "payload": payload}


class AsksTest(unittest.TestCase):
    def test_lifecycle(self):
        rows = [ev(1, "of.agent_question", detected_by="heuristic", questions=[{"question": "Commit?"}]),
                ev(2, "of.agent_message_in"),                       # neutral
                ev(3, "of.human_prompt"),                           # answers e1
                ev(4, "of.permission_request", tool_name="Bash", summary="kubectl apply"),
                ev(5, "of.turn_end"),                               # the tool ran: approved
                ev(6, "of.agent_question", questions=[{"question": "A or B?"}]),
                ev(7, "of.agent_question", questions=[{"question": "A, B or C?"}]),   # supersedes e6
                ev(8, "of.permission_request", sid="T", tool_name="Edit"),
                ev(9, "of.permission_denied", sid="T"),
                ev(10, "of.agent_question", sid="U", questions=[{"question": "?"}]),
                ev(11, "of.session_end", sid="U")]
        st = {a["event_id"]: a["state"] for a in of_asks.project(rows)}
        self.assertEqual(st, {"e1": "answered", "e4": "answered", "e6": "superseded", "e7": "open",
                              "e8": "denied", "e10": "expired"})
        open_ = [a for a in of_asks.project(rows) if a["state"] == "open"][0]
        self.assertEqual((open_["kind"], open_["text"]), ("question", "A, B or C?"))

    def test_withdraw_and_age_labels(self):
        rows = [ev(1, "of.agent_question", questions=[{"question": "?"}]),
                {"event_id": "w", "ts": "2026-11-05T10:05:00Z", "name": "of.ask_withdrawn", "session_id": "S",
                 "position": "p", "payload": {"ask_id": "e1"}}]
        self.assertEqual(of_asks.project(rows)[0]["state"], "withdrawn")
        cfg = dict(of_asks.DEFAULTS)
        t0 = of_asks.seconds_since("2026-11-05T10:00:00Z", 0)  # negative offset of that time from epoch
        base = -t0
        self.assertEqual(of_asks.label("2026-11-05T10:00:00Z", cfg, base + 3 * 3600), "")
        self.assertEqual(of_asks.label("2026-11-05T10:00:00Z", cfg, base + 5 * 3600), "stale")
        self.assertEqual(of_asks.label("2026-11-05T10:00:00Z", cfg, base + 25 * 3600), "dead-end")

