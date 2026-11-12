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

    def assertion(self, chal, origin=ORIGIN, rp=RP, flags=P.FLAG_UP | P.FLAG_UV, kind="webauthn.get"):
        auth = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + (1).to_bytes(4, "big")
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

    def enrollment(self, signer=None, **over):
        rec = {"kind": P.KIND, "org": ORG, "person": "founder", "credential": "cred-1", "public_key": self.phone.spki,
               "rp_id": RP, "origin": ORIGIN, "issued_at": "2026-11-12T10:00:00Z"}
        rec.update(over)
        rec["signature"] = of_grants.sign_ed25519(rec, signer or self.grant)
        return rec

    def approval(self, assertion=None, enrollment=None, digest=DIGEST, expires=EXPIRES):
        chal = P.challenge(ORG, DIFF, digest, NONCE, expires)
        return {"id": DIFF, "digest": digest, "by": "owner", "via": "passkey", "org": ORG,
                "challenge": {"nonce": NONCE, "expires": expires}, "credential": "cred-1",
                "enrollment": enrollment or self.enrollment(),
                "assertion": assertion or self.phone.assertion(chal)}

    def check(self, approval, digest=DIGEST, at="2026-11-12T12:05:00Z"):
        return P.approval_problem(approval, DIFF, ORG, digest, self.keys, recorded_at=at)

    def test_a_good_approval_passes(self):
        self.assertIsNone(self.check(self.approval()))

    def test_each_forgery_or_staleness_is_refused_with_its_reason(self):
        chal = P.challenge(ORG, DIFF, DIGEST, NONCE, EXPIRES)
        other_chal = P.challenge(ORG, "d-other", DIGEST, NONCE, EXPIRES)
        at, late = "2026-11-12T12:05:00Z", "2026-11-12T12:16:00Z"
        cases = {  # the reason: (approval, the diff's current digest, when the approval was recorded)
            "another form of the diff": (self.approval(), "fedcba9876543210", at),
            "after its challenge expired": (self.approval(), DIGEST, late),
            "pinned grant public key": (self.approval(enrollment=self.enrollment(signer=self.intruder)), DIGEST, at),
            "not for 'acme'": (self.approval(enrollment=self.enrollment(org="other")), DIGEST, at),
            "not this diff's": (self.approval(assertion=self.phone.assertion(other_chal)), DIGEST, at),
            "not on the enrolled origin": (self.approval(assertion=self.phone.assertion(chal, origin="https://evil")),
                                           DIGEST, at),
            "another relying party": (self.approval(assertion=self.phone.assertion(chal, rp="evil")), DIGEST, at),
            "did not verify the person": (self.approval(assertion=self.phone.assertion(chal, flags=P.FLAG_UP)),
                                          DIGEST, at),
            "not webauthn.get": (self.approval(assertion=self.phone.assertion(chal, kind="webauthn.create")),
                                 DIGEST, at),
            "against the enrolled passkey": (self.approval(assertion=self.other_phone.assertion(chal)), DIGEST, at),
        }
        for want, (approval, digest, when) in cases.items():
            why = self.check(approval, digest, when)
            self.assertIsNotNone(why, want)
            self.assertIn(want, why)
        tampered = self.approval()
        tampered["enrollment"] = dict(tampered["enrollment"], public_key=self.other_phone.spki)
        self.assertIn("pinned grant public key", self.check(tampered))   # a swapped key breaks the owner's signature
        broken = self.approval()
        broken["assertion"] = {"signature": "x"}
        self.assertIn("not readable", self.check(broken))

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
        d = {"id": DIFF, "org": ORG, "tier": "high"}
        good, forged = self.approval(), self.approval(enrollment=self.enrollment(signer=self.intruder))
        self.assertEqual(of._passkey_approval({"passkey": [("2026-11-12T12:05:00Z", good)]}, d, DIGEST), (True, None))
        ok, why = of._passkey_approval({"passkey": [("2026-11-12T12:05:00Z", forged)]}, d, DIGEST)
        self.assertFalse(ok)
        self.assertIn("pinned grant public key", why)
        self.assertEqual(of._passkey_approval({}, d, DIGEST), (False, None))


if __name__ == "__main__":
    unittest.main()
