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
        self.assertEqual(reg["code"], S.short_code(self.phone.cred, reg["public_key"]))
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
        for bad in (b"\x5f", b"\x58", b"\xa1\x01", b"\xa1\x80\x01", b"\x81" * 40):   # ... an unhashable key, deep nesting
            with self.assertRaises(S.PasskeyError):
                S.cbor_decode(bad)
        self.assertNotEqual(S.short_code(b"a", b"k"), S.short_code(b"b", b"k"))
        self.assertNotEqual(S.short_code(b"a", b"k1"), S.short_code(b"a", b"k2"))   # a swapped key shows
        self.assertTrue(set(S.short_code(b"a", b"k")) <= set(S.CODE_ALPHABET))

    def test_the_pages_code_is_the_same_function(self):
        """The phone computes its code in the page's JavaScript; it must be short_code exactly (node, when present)."""
        import re
        import shutil
        if not shutil.which("node"):
            self.skipTest("no node to run the page's script")
        js = re.search(r"async function codeOf.*?\n}\n", S._JS, re.S).group(0)
        cred, key = os.urandom(16), os.urandom(91)
        prog = (js + f"codeOf(Uint8Array.from({list(cred)}).buffer, Uint8Array.from({list(key)}).buffer)"
                ".then(c => console.log(c));")
        out = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout.strip(), S.short_code(cred, key), out.stderr)


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

    def test_a_key_swapped_in_the_store_shows_another_code_at_the_terminal(self):
        """of_secrets-review on PR 81: a window row rewritten after the phone's test step to hold another key."""
        w = self.pk.open_window(self.tenant, "acme", "founder")
        att, client = self.phone.create(S.unb64u(self.pk.window_options(w["window"])["challenge"]))
        reg = self.pk.register(w["window"], S.b64u(att), S.b64u(client))
        auth, cdj, sig = self.phone.get(S.unb64u(reg["test_challenge"]))
        phone_code = self.pk.test(w["window"], reg["credential"], S.b64u(auth), S.b64u(cdj), S.b64u(sig))["code"]
        attacker = Phone(self.tmp.name)
        att2, _ = attacker.create(b"x" * 32)
        swapped = S.cose_to_spki({1: 2, 3: -7, -1: 1, -2: attacker.x, -3: attacker.y})
        with self.pk.store.tx() as c:
            c.execute("UPDATE passkey_windows SET public_key = ? WHERE id = ?", (swapped, w["window"]))
        st = self.pk.window_state(self.tenant, w["window"])
        self.assertEqual(st["code"], phone_code)                                  # the server relays the old code...
        self.assertNotEqual(S.short_code(st["credential"], st["public_key"]), phone_code)   # ...the terminal's differs

    def test_a_known_credential_is_not_registered_again(self):
        self.enroll()
        w = self.pk.open_window(self.tenant, "acme", "founder")
        att, client = self.phone.create(S.unb64u(self.pk.window_options(w["window"])["challenge"]))
        with self.assertRaises(S.Refused) as e:
            self.pk.register(w["window"], S.b64u(att), S.b64u(client))
        self.assertEqual(e.exception.typ, "already_registered")

    def test_invisible_characters_are_shown(self):
        page = S.approve_page({"org": "acme", "id": "sd-1", "digest": "0" * 64, "text": 'role: "a\u202eb"',
                               "expires": "t", "nonce": "n", "challenge": "c", "credentials": []}, "N")
        self.assertIn(b"[U+202E]", page)
        self.assertIn(b"invisible or direction-changing", page)
        self.assertNotIn("\u202e".encode(), page)

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


class HttpTest(FlowTest):
    """The same path over HTTP, through of-server's routes: the pages' headers, the rate limit's place, and the
    approval reaching another machine through the organization's coordination feed."""
    # these two work on the store directly; FlowTest runs them
    test_a_key_swapped_in_the_store_shows_another_code_at_the_terminal = None
    test_a_known_credential_is_not_registered_again = None

    def setUp(self):
        import threading
        import urllib.request
        import of_client
        import of_events
        import of_server
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = os.path.join(root, "state")
        ship_cfg = {"unassigned_database": "db_un", "vault_database": "db_vault", "raw_to_org": False, "tenant": "t"}
        cfg = dict(of_server.DEFAULTS, state_dir=os.path.join(root, "server"), port=0, flush_every=3600,
                   orgs={"acme": "db_acme"}, public_url=ORIGIN)

        class CH:
            def insert(self, *a):
                pass
        self.app = of_server.App(cfg, ch=CH(), ship_cfg=ship_cfg, grants_loader=lambda: {}, mask_key=b"m" * 32,
                                 grant_key=lambda: self.keys)
        self.httpd = self.app.serve(host="127.0.0.1", port=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.phone = Phone(root)

        def machine(name, tenant):
            d = os.path.join(root, name)
            os.makedirs(d)
            c = of_client.Client(self.url, key_path=os.path.join(d, "key"), state_dir=d)
            c.machine_id = f"m-{name}"
            of_server.enroll(self.app.store, c.machine_id, of_client.ensure_key(c.key_path), ["acme"], [], tenant=tenant)
            return c
        self.tenant, self.colleague = machine("tenant", True), machine("colleague", False)
        self.urlopen = urllib.request.urlopen

    def tearDown(self):
        import of_events
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.writer.stop.set()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def open(self, method, path, body=None):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(self.url + path, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with self.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def enroll(self):
        import of_grants
        _, w = self.tenant._authed("POST", "/v1/passkey/windows", body={"org": "acme", "person": "founder"})
        wid = w["window"]
        st, hd, page = self.open("GET", f"/passkey/enroll?w={wid}")
        self.assertEqual(st, 200)
        self.assertEqual((hd.get("Cache-Control"), hd.get("Referrer-Policy")), ("no-store", "no-referrer"))
        self.assertIn("frame-ancestors 'none'", hd.get("Content-Security-Policy", ""))
        opts = json.loads(self.open("GET", f"/v1/passkey/windows/{wid}/options")[2])
        att, client = self.phone.create(S.unb64u(opts["challenge"]))
        st, _, raw = self.open("POST", f"/v1/passkey/windows/{wid}/register",
                               {"attestation_object": S.b64u(att), "client_data_json": S.b64u(client)})
        reg = json.loads(raw)
        self.assertEqual(st, 200, reg)
        auth, cdj, sig = self.phone.get(S.unb64u(reg["test_challenge"]))
        st, _, raw = self.open("POST", f"/v1/passkey/windows/{wid}/test",
                               {"credential": reg["credential"], "authenticator_data": S.b64u(auth),
                                "client_data_json": S.b64u(cdj), "signature": S.b64u(sig)})
        code = json.loads(raw)["code"]
        _, state = self.tenant._authed("GET", f"/v1/passkey/windows/{wid}")
        self.assertEqual((state["state"], state["code"]), ("tested", code))
        rec = {"kind": "passkey_enrollment", "org": "acme", "person": "founder", "credential": state["credential"],
               "public_key": state["public_key"], "rp_id": state["rp_id"], "origin": state["origin"],
               "issued_at": "2026-11-12T13:00:00Z", "expires": "2027-11-12T13:00:00Z"}
        rec["signature"] = of_grants.sign_ed25519(rec, self.key)
        self.tenant._authed("POST", "/v1/passkey/enrollments", body={"record": rec})
        return w, reg, rec

    def test_enroll_link_approve_and_the_client_accepts_it(self):
        import of_client
        import of_diff
        w, reg, rec = self.enroll()
        diff = {"id": "sd-9", "kind": "spawn", "org": "acme", "tier": "high", "approvals": ["owner"]}
        dg = of_diff.digest(diff)
        _, link = self.colleague._authed("POST", "/v1/passkey/links",
                                         body={"org": "acme", "id": "sd-9", "diff": diff, "digest": dg})
        path = link["url"][len(ORIGIN):]
        st, hd, page = self.open("GET", path)
        self.assertEqual(st, 200)
        self.assertIn(b"&quot;kind&quot;: &quot;spawn&quot;", page)
        self.assertEqual(hd.get("Cache-Control"), "no-store")
        self.assertEqual(self.open("GET", path.replace(link["nonce"], "guess"))[0], 404)   # no nonce, nothing shown
        data = self.app.passkeys.approval_page("acme", "sd-9", dg, link["nonce"])
        auth, cdj, sig = self.phone.get(S.unb64u(data["challenge"]), count=1)
        st, _, raw = self.open("POST", "/v1/passkey/approve", {"nonce": link["nonce"], "credential": reg["credential"],
                                                                "authenticator_data": S.b64u(auth),
                                                                "client_data_json": S.b64u(cdj),
                                                                "signature": S.b64u(sig)})
        self.assertEqual((st, json.loads(raw)["approved"]), (200, "sd-9"))
        feed = self.colleague._authed("GET", "/v1/coord?org=acme&after=0&wait=0")[1]["events"]
        ev = [e for e in feed if e["name"] == "of.diff_approved"]
        self.assertEqual(len(ev), 1)
        p = ev[0]["record"]["of.payload"]
        self.assertEqual((p["id"], p["digest"], p["via"], p["enrollment"]["person"]), ("sd-9", dg, "passkey", "founder"))
        self.assertIsInstance(of_client, object)

    def test_what_is_refused(self):
        import of_client
        with self.assertRaises(of_client.ServerError) as e:     # a colleague's machine opens no window
            self.colleague._authed("POST", "/v1/passkey/windows", body={"org": "acme", "person": "founder"})
        self.assertEqual(e.exception.status, 403)
        st, _, body = self.open("GET", "/approve/diff?org=acme&id=x&digest=y&n=z")
        self.assertEqual((st, body[:8]), (404, b"no such "))
        for _ in range(50):                                      # random tokens: 404, and nothing kept
            self.assertEqual(self.open("GET", f"/v1/passkey/windows/{os.urandom(8).hex()}/options")[0], 404)
        self.assertEqual(self.app.passkey_rate.seen, {})
        _, w = self.tenant._authed("POST", "/v1/passkey/windows", body={"org": "acme", "person": "founder"})
        self.app.passkey_rate.limit = 2                          # an existing token is rate-limited
        codes = [self.open("GET", f"/v1/passkey/windows/{w['window']}/options")[0] for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])


class OwnerTerminalTest(unittest.TestCase):
    """`of passkey enroll|revoke` (bin/of_passkey_owner.py): the owner signs only after the codes match, never while
    the grant key is a plain file, and a revocation lands in the grants file every machine reads."""

    def setUp(self):
        import of_grants
        self.tmp = tempfile.TemporaryDirectory()
        self.key = os.path.join(self.tmp.name, "grant_ed25519")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", self.key], check=True,
                       env=of_grants.no_agent_env())
        with open(self.key + ".pub") as fh:
            self.keys = of_grants.Keys(ed25519=[fh.read().strip()])
        self.sign = lambda rec: of_grants.sign_ed25519(rec, self.key)

    def tearDown(self):
        self.tmp.cleanup()

    class Client:
        def __init__(self, states):
            self.states, self.calls = list(states), []

        def _authed(self, method, path, body=None):
            self.calls.append((method, path, body))
            if path == "/v1/passkey/windows":
                return 200, {"window": "w1", "url": "https://of.acme.test/passkey/enroll?w=w1", "expires": "t"}
            if path.startswith("/v1/passkey/windows/"):
                return 200, self.states.pop(0) if len(self.states) > 1 else self.states[0]
            return 200, {}

    def state(self, st):
        return {"state": st, "org": "acme", "person": "founder", "code": "ABC234", "credential": "cred-1",
                "public_key": "AAAA", "rp_id": "of.acme.test", "origin": ORIGIN}

    def test_signs_only_after_the_owner_confirms_the_code(self):
        import io
        import of_grants
        import of_passkey_owner as O
        c = self.Client([self.state("open"), self.state("tested")])
        out = io.StringIO()
        self.assertEqual(O.enroll(c, "acme", "founder", ask=lambda q: "yes", out=out, poll=0, sign=self.sign), "cred-1")
        rec = c.calls[-1][2]["record"]
        self.assertEqual((c.calls[-1][1], rec["person"], rec["origin"]), ("/v1/passkey/enrollments", "founder", ORIGIN))
        self.assertTrue(of_grants.verify(rec, self.keys))
        self.assertIn(S.short_code("cred-1", "AAAA"), out.getvalue())   # computed here, not the server's ABC234
        self.assertNotIn("ABC234", out.getvalue())
        c = self.Client([self.state("tested")])
        self.assertIsNone(O.enroll(c, "acme", "founder", ask=lambda q: "no", out=io.StringIO(), poll=0, sign=self.sign))
        self.assertNotIn("/v1/passkey/enrollments", [p for _, p, _ in c.calls])

    def test_a_window_of_another_person_is_not_signed(self):
        """of_secrets-review on PR 81: the record names the person and organization of the owner's command, and a
        window of-server says is someone else's is refused, not signed under that name."""
        import io
        import of_passkey_owner as O
        c = self.Client([dict(self.state("tested"), person="mallory")])
        out = io.StringIO()
        self.assertIsNone(O.enroll(c, "acme", "founder", ask=lambda q: "yes", out=out, poll=0, sign=self.sign))
        self.assertNotIn("/v1/passkey/enrollments", [p for _, p, _ in c.calls])
        self.assertIn("Nothing is signed", out.getvalue())

    def test_refuses_while_the_grant_key_is_a_plain_file(self):
        import of_grants
        import of_passkey_owner as O
        saved = of_grants.GRANT_KEY
        of_grants.GRANT_KEY = self.key            # a private key file exists
        try:
            with self.assertRaises(PermissionError) as e:
                O.enroll(self.Client([self.state("tested")]), "acme", "founder", ask=lambda q: "yes", poll=0)
            self.assertIn("behind Touch ID", str(e.exception))
        finally:
            of_grants.GRANT_KEY = saved

    def test_a_revocation_lands_in_the_grants_file_and_is_read_there(self):
        import io
        import of_grants
        import of_passkey_owner as O
        org_dir = os.path.join(self.tmp.name, "org", "registry")
        orgs = os.path.join(self.tmp.name, "orgs.yaml")
        with open(orgs, "w") as fh:
            fh.write("orgs:\n  - {name: acme, path: org}\n")
        path = os.path.join(org_dir, "grants.yaml")
        c = self.Client([self.state("tested")])
        O.revoke("acme", "founder", "cred-1", path, client=c, sign=self.sign, out=io.StringIO())
        O.revoke("acme", "founder", "cred-1", path, client=None, sign=self.sign, out=io.StringIO())   # kept once
        with open(path) as fh:
            text = fh.read()
        self.assertTrue(text.startswith(of_grants.GRANTS_HEADER))
        self.assertEqual(text.count("credential: cred-1"), 1)
        self.assertEqual(c.calls[-1][1], "/v1/passkey/revocations")
        self.assertTrue(of_grants.passkey_revoked("acme", "cred-1", orgs, self.keys,
                                                  os.path.join(self.tmp.name, "state")))
