"""of-server's half of a passkey approval (bin/of_server_passkey.py; docs/remote.md §6): a registration and an assertion
made by a virtual authenticator, as a phone makes them. openssl holds its P-256 key and signs; the attestation object
is built here in CBOR with the "none" format phones send for a cross-device passkey."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_server_passkey as S  # noqa: E402

RP, ORIGIN = "of.acme.test", "https://of.acme.test"


def cbor(v):
    """A minimal CBOR encoder for the test's attestation objects."""
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size):
                return bytes([major << 5 | info]) + n.to_bytes(size, "big")
        raise ValueError(n)
    if v is None:
        return bytes([0xF6])
    if isinstance(v, bool):
        return bytes([0xF5 if v else 0xF4])
    if isinstance(v, int):
        return head(0, v) if v >= 0 else head(1, -1 - v)
    if isinstance(v, bytes):
        return head(2, len(v)) + v
    if isinstance(v, str):
        return head(3, len(v.encode())) + v.encode()
    if isinstance(v, list):
        return head(4, len(v)) + b"".join(cbor(x) for x in v)
    if isinstance(v, dict):
        return head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    raise TypeError(type(v))


class Phone:
    def __init__(self, tmp):
        self.key = os.path.join(tmp, f"phone-{len(os.listdir(tmp))}.pem")
        subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", self.key],
                       check=True, capture_output=True)
        der = subprocess.run(["openssl", "ec", "-in", self.key, "-pubout", "-outform", "DER"], check=True,
                             capture_output=True).stdout
        self.x, self.y = der[-64:-32], der[-32:]
        self.cred = os.urandom(16)
        self.tmp = tmp

    def create(self, chal, origin=ORIGIN, rp=RP, flags=S.FLAG_UP | S.FLAG_UV | S.FLAG_AT, cose=None):
        cose = cose or {1: 2, 3: -7, -1: 1, -2: self.x, -3: self.y}
        auth = (hashlib.sha256(rp.encode()).digest() + bytes([flags]) + (0).to_bytes(4, "big") + bytes(16)
                + len(self.cred).to_bytes(2, "big") + self.cred + cbor(cose))
        client = json.dumps({"type": "webauthn.create", "challenge": S.b64u(chal), "origin": origin}).encode()
        return cbor({"fmt": "none", "attStmt": {}, "authData": auth}), client

    def get(self, chal, origin=ORIGIN, rp=RP, flags=S.FLAG_UP | S.FLAG_UV, count=0, kind="webauthn.get", **extra):
        auth = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + count.to_bytes(4, "big")
        client = json.dumps({"type": kind, "challenge": S.b64u(chal), "origin": origin, **extra}).encode()
        data = os.path.join(self.tmp, "signed")
        with open(data, "wb") as fh:
            fh.write(auth + hashlib.sha256(client).digest())
        sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", self.key, data], check=True,
                             capture_output=True).stdout
        return auth, client, sig


class RegistrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.phone = Phone(self.tmp.name)
        self.chal = os.urandom(32)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_phone_registers_and_its_test_assertion_verifies(self):
        att, client = self.phone.create(self.chal)
        reg = S.verify_registration(att, client, self.chal, RP, ORIGIN)
        self.assertEqual(reg["credential_id"], S.b64u(self.phone.cred))
        self.assertEqual(reg["code"], S.short_code(self.phone.cred))
        self.assertEqual(len(reg["code"]), 6)
        test_chal = os.urandom(32)
        auth, cdj, sig = self.phone.get(test_chal)
        self.assertEqual(S.verify_assertion(auth, cdj, sig, test_chal, RP, ORIGIN, reg["public_key"]), 0)
        other = Phone(self.tmp.name)
        auth, cdj, sig = other.get(test_chal)
        with self.assertRaises(S.PasskeyError) as e:
            S.verify_assertion(auth, cdj, sig, test_chal, RP, ORIGIN, reg["public_key"])
        self.assertIn("does not verify", str(e.exception))

    def test_a_registration_that_does_not_stand_is_refused_with_its_reason(self):
        cases = {
            "answers another challenge": self.phone.create(os.urandom(32)),
            "not on 'https://of.acme.test'": self.phone.create(self.chal, origin="https://evil.test"),
            "another relying party": self.phone.create(self.chal, rp="evil.test"),
            "did not verify the person": self.phone.create(self.chal, flags=S.FLAG_UP | S.FLAG_AT),
            "only an ES256 key on P-256": self.phone.create(self.chal, cose={1: 3, 3: -257, -1: b"n", -2: b"e"}),
            "carries no credential": self.phone.create(self.chal, flags=S.FLAG_UP | S.FLAG_UV),
        }
        for why, (att, client) in cases.items():
            with self.assertRaises(S.PasskeyError, msg=why) as e:
                S.verify_registration(att, client, self.chal, RP, ORIGIN)
            self.assertIn(why, str(e.exception))

    def test_an_assertion_that_does_not_stand_is_refused(self):
        att, client = self.phone.create(self.chal)
        key = S.verify_registration(att, client, self.chal, RP, ORIGIN)["public_key"]
        c = os.urandom(32)
        cases = {
            "not webauthn.get": self.phone.get(c, kind="webauthn.create"),
            "inside a page of another origin": self.phone.get(c, crossOrigin=True),
            "did not verify the person": self.phone.get(c, flags=S.FLAG_UP),
            "is not above 7": self.phone.get(c, count=5),
        }
        for why, (auth, cdj, sig) in cases.items():
            with self.assertRaises(S.PasskeyError, msg=why) as e:
                S.verify_assertion(auth, cdj, sig, c, RP, ORIGIN, key, last_counter=7)
            self.assertIn(why, str(e.exception))
        auth, cdj, sig = self.phone.get(c, count=8)
        self.assertEqual(S.verify_assertion(auth, cdj, sig, c, RP, ORIGIN, key, last_counter=7), 8)

    def test_the_pure_python_es256_check_agrees_with_openssl(self):
        att, client = self.phone.create(self.chal)
        key = S.verify_registration(att, client, self.chal, RP, ORIGIN)["public_key"]
        auth, cdj, sig = self.phone.get(os.urandom(32))
        data = auth + hashlib.sha256(cdj).digest()
        self.assertTrue(S.es256_verify(key, data, sig))
        self.assertFalse(S.es256_verify(key, data + b"x", sig))
        self.assertFalse(S.es256_verify(key, data, sig[:-1] + bytes([sig[-1] ^ 1])))
        self.assertFalse(S.es256_verify(key, data, b"\x30\x00"))
        import base64
        bad = base64.b64decode(key)[:-1] + bytes([base64.b64decode(key)[-1] ^ 1])   # off the curve
        self.assertFalse(S.es256_verify(base64.b64encode(bad).decode(), data, sig))

    def test_cbor_and_short_codes(self):
        self.assertEqual(S.cbor_decode(cbor({"a": [1, -2, b"\x00", True, None], 3: "x"}))[0],
                         {"a": [1, -2, b"\x00", True, None], 3: "x"})
        for bad in (b"\x5f", b"\x58", b"\xa1\x01"):   # indefinite length, a length past the end, a map cut short
            with self.assertRaises(S.PasskeyError):
                S.cbor_decode(bad)
        self.assertNotEqual(S.short_code(b"a"), S.short_code(b"b"))
        self.assertTrue(set(S.short_code(b"a")) <= set("ABCDEFGHJKMNPQRSTUVWXYZ23456789"))


if __name__ == "__main__":
    unittest.main()


class FlowTest(unittest.TestCase):
    """The whole of-server side: a window, registration, the test assertion, the owner's signed record, a single-use
    link, the approval, and the client half's own checks (of_passkey) passing on what of-server records."""

    @classmethod
    def setUpClass(cls):
        import of_grants
        cls.keydir = tempfile.TemporaryDirectory()
        cls.key = os.path.join(cls.keydir.name, "grant_ed25519")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", cls.key], check=True,
                       env=of_grants.no_agent_env())
        with open(cls.key + ".pub") as fh:
            cls.keys = of_grants.Keys(ed25519=[fh.read().strip()])

    @classmethod
    def tearDownClass(cls):
        cls.keydir.cleanup()

    def setUp(self):
        import of_server
        self.tmp = tempfile.TemporaryDirectory()
        self.now = [1_800_000_000.0]
        self.pk = S.Passkeys(of_server.Store(os.path.join(self.tmp.name, "srv")), {"public_url": ORIGIN},
                             lambda: self.keys, clock=lambda: self.now[0])
        self.tenant = {"machine_id": "m-tenant", "orgs": json.dumps(["acme"]), "tenant": 1}
        self.colleague = {"machine_id": "m-other", "orgs": json.dumps(["acme"]), "tenant": 0}
        self.phone = Phone(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def enroll(self):
        import of_grants
        w = self.pk.open_window(self.tenant, "acme", "founder")
        opts = self.pk.window_options(w["window"])
        att, client = self.phone.create(S.unb64u(opts["challenge"]))
        reg = self.pk.register(w["window"], S.b64u(att), S.b64u(client))
        auth, cdj, sig = self.phone.get(S.unb64u(reg["test_challenge"]))
        code = self.pk.test(w["window"], reg["credential"], S.b64u(auth), S.b64u(cdj), S.b64u(sig))["code"]
        st = self.pk.window_state(self.tenant, w["window"])
        self.assertEqual((st["state"], st["code"]), ("tested", code))
        rec = {"kind": "passkey_enrollment", "org": "acme", "person": "founder", "credential": st["credential"],
               "public_key": st["public_key"], "rp_id": st["rp_id"], "origin": st["origin"],
               "issued_at": "2027-01-15T08:00:00Z", "expires": "2028-01-15T08:00:00Z"}
        rec["signature"] = of_grants.sign_ed25519(rec, self.key)
        self.pk.enroll(self.tenant, rec)
        return w, reg, rec

    def test_enroll_link_approve_and_the_client_accepts_it(self):
        import of_diff
        import of_grants
        import of_passkey
        w, reg, rec = self.enroll()
        diff = {"id": "sd-1", "kind": "goal_change", "org": "acme", "tier": "high", "approvals": ["owner"]}
        dg = of_diff.digest(diff)
        link = self.pk.put_link(self.colleague, "acme", "sd-1", diff, dg)
        self.assertIn(f"&n={link['nonce']}", link["url"])
        page = self.pk.approval_page("acme", "sd-1", dg, link["nonce"])
        self.assertIn('"kind": "goal_change"', page["text"])
        self.assertEqual(page["credentials"], [reg["credential"]])
        auth, cdj, sig = self.phone.get(S.unb64u(page["challenge"]), count=3)
        rec_ = self.pk.approve(link["nonce"], reg["credential"], S.b64u(auth), S.b64u(cdj), S.b64u(sig))
        saved = getattr(of_grants, "passkey_revoked", None)
        of_grants.passkey_revoked = lambda org, cred: False
        try:
            why = of_passkey.approval_problem(rec_, "sd-1", "acme", dg, self.keys, recorded_at="2027-01-15T08:00:00Z",
                                              approvers={"founder"}, server=ORIGIN)
        finally:
            if saved:
                of_grants.passkey_revoked = saved
        self.assertIsNone(why)
        with self.assertRaises(S.Refused) as e:   # single use
            self.pk.approve(link["nonce"], reg["credential"], S.b64u(auth), S.b64u(cdj), S.b64u(sig))
        self.assertEqual(e.exception.status, 404)

    def test_what_is_refused(self):
        import of_diff
        import of_grants
        with self.assertRaises(S.Refused) as e:
            self.pk.open_window(self.colleague, "acme", "founder")       # only the tenant's machine
        self.assertEqual(e.exception.status, 403)
        w = self.pk.open_window(self.tenant, "acme", "founder")
        opts = self.pk.window_options(w["window"])
        att, client = self.phone.create(S.unb64u(opts["challenge"]))
        self.pk.register(w["window"], S.b64u(att), S.b64u(client))
        intruder = Phone(self.tmp.name)                                   # the window took one credential
        att2, client2 = intruder.create(S.unb64u(opts["challenge"]))
        with self.assertRaises(S.Refused):
            self.pk.register(w["window"], S.b64u(att2), S.b64u(client2))
        w2, reg, rec = self.enroll()
        forged = dict(rec, person="mallory")                              # the owner's signature covers the person
        with self.assertRaises(S.Refused):
            self.pk.enroll(self.tenant, forged)
        diff = {"id": "sd-2", "org": "acme", "kind": "spawn"}
        with self.assertRaises(S.Refused) as e:
            self.pk.put_link(self.colleague, "acme", "sd-2", diff, "0" * 64)
        self.assertEqual(e.exception.typ, "digest_mismatch")
        link = self.pk.put_link(self.colleague, "acme", "sd-2", diff, of_diff.digest(diff))
        with self.assertRaises(S.Refused):                                # without the nonce, nothing
            self.pk.approval_page("acme", "sd-2", of_diff.digest(diff), "guess")
        rev = {"kind": "passkey_revocation", "org": "acme", "person": "founder", "credential": reg["credential"],
               "issued_at": "2027-01-15T09:00:00Z"}
        rev["signature"] = of_grants.sign_ed25519(rev, self.key)
        self.pk.revoke(self.tenant, rev)
        page = self.pk.approval_page("acme", "sd-2", of_diff.digest(diff), link["nonce"])
        self.assertEqual(page["credentials"], [])
        auth, cdj, sig = self.phone.get(S.unb64u(page["challenge"]))
        with self.assertRaises(S.Refused) as e:
            self.pk.approve(link["nonce"], reg["credential"], S.b64u(auth), S.b64u(cdj), S.b64u(sig))
        self.assertEqual(e.exception.typ, "revoked")
        self.now[0] += 4000                                               # links expire
        with self.assertRaises(S.Refused):
            self.pk.approval_page("acme", "sd-2", of_diff.digest(diff), link["nonce"])
