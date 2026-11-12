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
