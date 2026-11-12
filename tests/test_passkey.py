"""A diff approved with the person's passkey (docs/remote.md §6; bin/of_passkey.py): a virtual authenticator made with
openssl signs as a phone's passkey does, and the grant key made with ssh-keygen signs the enrollment record."""

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_grants  # noqa: E402
import of_passkey as P  # noqa: E402

ORG, DIFF, DIGEST, ORIGIN, RP = "acme", "d-2026-11-12-spawn", "0123456789abcdef", "https://of.acme.example", \
    "of.acme.example"
NONCE, EXPIRES = "n-1", "2026-11-12T12:15:00Z"


class Authenticator:
    """A P-256 passkey: its SubjectPublicKeyInfo and its signatures, as WebAuthn makes them."""

    def __init__(self, folder, name):
        self.key = os.path.join(folder, name + ".pem")
        subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", self.key],
                       check=True, capture_output=True)
        der = subprocess.run(["openssl", "ec", "-in", self.key, "-pubout", "-outform", "DER"], check=True,
                             capture_output=True).stdout
        import base64
        self.spki = base64.b64encode(der).decode()
        self.folder = folder

    def assertion(self, chal, origin=ORIGIN, rp=RP, flags=P.FLAG_UP | P.FLAG_UV, kind="webauthn.get", count=1):
        auth = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + count.to_bytes(4, "big")
        cdj = json.dumps({"type": kind, "challenge": P.b64u(chal), "origin": origin}).encode()
        data = os.path.join(self.folder, "signed")
        with open(data, "wb") as fh:
            fh.write(auth + hashlib.sha256(cdj).digest())
        sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", self.key, data], check=True,
                             capture_output=True).stdout
        return {"authenticator_data": P.b64u(auth), "client_data_json": P.b64u(cdj), "signature": P.b64u(sig)}


class PasskeyApprovalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.grant, cls.intruder = (os.path.join(cls.tmp.name, n) for n in ("grant", "intruder"))
        for k in (cls.grant, cls.intruder):
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", k], check=True,
                           env=of_grants.no_agent_env())
        with open(cls.grant + ".pub") as fh:
            cls.keys = of_grants.Keys(ed25519=[fh.read().strip()])
        cls.phone = Authenticator(cls.tmp.name, "phone")
        cls.other_phone = Authenticator(cls.tmp.name, "other")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):  # of_server's revocation reader: nothing revoked unless a test says so
        self.revoked = set()
        saved = getattr(of_grants, "passkey_revoked", None)
        of_grants.passkey_revoked = lambda org, credential: (org, credential) in self.revoked
        self.addCleanup(lambda: setattr(of_grants, "passkey_revoked", saved) if saved else
                        vars(of_grants).pop("passkey_revoked", None))   # a test may have removed it already

    def enrollment(self, signer=None, **over):
        rec = {"kind": P.KIND, "org": ORG, "person": "founder", "credential": "cred-1", "public_key": self.phone.spki,
               "rp_id": RP, "origin": ORIGIN, "issued_at": "2026-11-12T10:00:00Z", "expires": "2027-11-12T10:00:00Z"}
        rec.update(over)
        rec["signature"] = of_grants.sign_ed25519(rec, signer or self.grant)
        return rec

    def approval(self, assertion=None, enrollment=None, digest=DIGEST, expires=EXPIRES, preimage=None):
        pre = preimage or P.preimage(ORG, DIFF, digest, NONCE, expires)
        a = assertion or self.phone.assertion(hashlib.sha256(pre.encode()).digest())
        return dict({"id": DIFF, "digest": digest, "by": "owner", "via": "passkey", "org": ORG, "credential": "cred-1",
                     "preimage": pre, "enrollment": enrollment or self.enrollment()}, **a)

    def check(self, approval, digest=DIGEST, at="2026-11-12T12:05:00Z", approvers=("founder",), last=0):
        return P.approval_problem(approval, DIFF, ORG, digest, self.keys, recorded_at=at, approvers=approvers,
                                  server=ORIGIN + "/", last_counter=last)

    def test_a_good_approval_passes(self):
        self.assertIsNone(self.check(self.approval()))
        self.assertEqual(P.counter(self.approval()), 1)

    def test_each_forgery_or_staleness_is_refused_with_its_reason(self):
        chal = P.challenge(ORG, DIFF, DIGEST, NONCE, EXPIRES)
        other_chal = P.challenge(ORG, "d-other", DIGEST, NONCE, EXPIRES)
        at, late = "2026-11-12T12:05:00Z", "2026-11-12T16:16:00+04:00"   # 12:16Z: an offset is honoured
        A = lambda **k: self.phone.assertion(chal, **k)  # noqa: E731
        cases = {  # the reason: (approval, the diff's current digest, when the approval was recorded)
            "another form of the diff": (self.approval(), "fedcba9876543210", at),
            "after its challenge expired": (self.approval(), DIGEST, late),
            "names another organization, diff or digest": (
                self.approval(preimage=P.preimage(ORG, "d-other", DIGEST, NONCE, EXPIRES)), DIGEST, at),
            "no readable preimage": (dict(self.approval(), preimage="x"), DIGEST, at),
            "pinned grant public key": (self.approval(enrollment=self.enrollment(signer=self.intruder)), DIGEST, at),
            "not for 'acme'": (self.approval(enrollment=self.enrollment(org="other")), DIGEST, at),
            "enrollment expired": (self.approval(enrollment=self.enrollment(expires="2026-11-12T11:00:00Z")), DIGEST, at),
            "not on the organization's of-server": (
                self.approval(enrollment=self.enrollment(origin="https://elsewhere.example"),
                              assertion=A(origin="https://elsewhere.example")), DIGEST, at),
            "not this approval's preimage": (self.approval(assertion=self.phone.assertion(other_chal)), DIGEST, at),
            "not on the enrolled origin": (self.approval(assertion=A(origin="https://evil")), DIGEST, at),
            "another relying party": (self.approval(assertion=A(rp="evil")), DIGEST, at),
            "did not verify the person": (self.approval(assertion=A(flags=P.FLAG_UP)), DIGEST, at),
            "not webauthn.get": (self.approval(assertion=A(kind="webauthn.create")), DIGEST, at),
            "against the enrolled passkey": (self.approval(assertion=self.other_phone.assertion(chal)), DIGEST, at),
        }
        for want, (approval, digest, when) in cases.items():
            why = self.check(approval, digest, when)
            self.assertIsNotNone(why, want)
            self.assertIn(want, why)
        tampered = self.approval()
        tampered["enrollment"] = dict(tampered["enrollment"], public_key=self.other_phone.spki)
        self.assertIn("pinned grant public key", self.check(tampered))   # a swapped key breaks the owner's signature
        self.assertIn("not readable", self.check({k: v for k, v in self.approval().items() if k != "signature"}))

    def test_only_an_approver_the_diff_needs(self):
        """of_review on PR 64: any enrolled passkey of the organization approved any diff."""
        why = self.check(self.approval(enrollment=self.enrollment(person="alex")))
        self.assertIn("enrolled for 'alex', who is not an approver this diff needs (founder)", why)
        self.assertIn("none known", self.check(self.approval(), approvers=()))

    def test_a_replayed_or_cloned_counter_is_refused(self):
        self.assertIn("counter 1 is not above 1", self.check(self.approval(), last=1))
        chal = P.challenge(ORG, DIFF, DIGEST, NONCE, EXPIRES)
        self.assertIsNone(self.check(self.approval(assertion=self.phone.assertion(chal, count=0)), last=5))  # 0: none

    def test_revocation(self):
        self.revoked.add((ORG, "cred-1"))
        self.assertIn("'cred-1' is revoked", self.check(self.approval()))
        del of_grants.passkey_revoked  # no reader yet: nothing could be taken back, so nothing is accepted
        self.assertIn("wait for revocation to exist", self.check(self.approval()))

    def test_the_link_needs_https(self):
        url, why = P.link("https://of.acme.example/", ORG, DIFF, DIGEST)
        self.assertEqual((url, why), (f"https://of.acme.example/approve/diff?org=acme&id={DIFF}&digest={DIGEST}", None))
        url, why = P.link("http://127.0.0.1:18200", ORG, DIFF, DIGEST)
        self.assertIsNone(url)
        self.assertIn("not at an HTTPS address", why)

    def test_apply_takes_a_checked_passkey_approval_of_any_tier(self):
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
        spec = importlib.util.spec_from_loader("of_cli", loader)
        of = importlib.util.module_from_spec(spec)
        loader.exec_module(of)
        saved = of_grants.local_keys
        of_grants.local_keys = lambda: self.keys
        self.addCleanup(setattr, of_grants, "local_keys", saved)
        of._org_server = lambda org, orgs_file=None: ORIGIN
        positions = [{"name": "acme_owner", "role": "owner", "_org": ORG, "holder": {"runtime": "human",
                                                                                     "person": "founder"}}]
        d = {"id": DIFF, "org": ORG, "tier": "high"}
        good, forged = self.approval(), self.approval(enrollment=self.enrollment(signer=self.intruder))
        e = {"id": DIFF, "passkey": [("2026-11-12T12:05:00Z", good)]}
        self.assertEqual(of._passkey_approval(e, d, DIGEST, positions=positions), (True, None))
        # an earlier forged event with a huge counter does not block the phone: only verified counters count
        chal = P.challenge(ORG, DIFF, DIGEST, NONCE, EXPIRES)
        huge = dict(self.approval(assertion=self.phone.assertion(chal, count=0xFFFFFFFF)), signature=P.b64u(b"x"))
        blocked = {"id": DIFF, "passkey": [("2026-11-12T12:04:00Z", huge), ("2026-11-12T12:05:00Z", good)]}
        self.assertEqual(of._passkey_approval(blocked, d, DIGEST, positions=positions), (True, None))
        replay = {"id": DIFF, "passkey": [("2026-11-12T12:05:00Z", good), ("2026-11-12T12:06:00Z", good)]}
        self.assertEqual(of._passkey_approval(replay, d, DIGEST, positions=positions), (True, None))  # the first holds
        e2 = {"id": DIFF, "passkey": [("2026-11-12T12:04:00Z", self.approval(enrollment=self.enrollment(person="x"))),
                                      ("2026-11-12T12:05:00Z", forged)]}
        ok, why = of._passkey_approval(e2, d, DIGEST, positions=positions)
        self.assertFalse(ok)
        self.assertIn("pinned grant public key", why)   # the newest one's reason
        self.assertEqual(of._passkey_approval({}, d, DIGEST, positions=positions), (False, None))


if __name__ == "__main__":
    unittest.main()
