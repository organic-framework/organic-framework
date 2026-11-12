"""of-server's half of a diff approved with a passkey (docs/remote.md §6; hand-over e15207eb from of_architect).

This module holds the pure logic; the routes call it. The client half (bin/of_passkey.py, of_platform) checks every
approval again from its record, so nothing a machine applies rests on of-server's word.

- Enrollment: the owner opens a window from a plain terminal (`of passkey enroll <person>`, denied to agents by
  docs/guard.md G13). The window accepts exactly one credential: a WebAuthn registration (navigator.credentials.create)
  with no attestation statement checked ("none" is what phones send for a cross-device passkey), then a test assertion
  with the new key over a fresh challenge. The phone's page and the owner's terminal show the same short code, made
  from the credential id; the owner compares them and only then signs the enrollment record with the grant key.
- Approval: the page shows a diff's exact text, uploaded by `of diff link` and checked against its digest, and asks
  the phone to sign the SHA-256 of the preimage [org, diff id, digest, nonce, expires]. of-server verifies the assertion
  with of_passkey's checks and appends of.diff_approved to the organization's coordination feed.

The public key arrives as a COSE key inside the CBOR attestation object; of_passkey verifies with a
SubjectPublicKeyInfo, so registration converts it here. Only ES256 on P-256 (COSE kty 2, alg -7, crv 1) is accepted.
"""

import base64
import hashlib
import json

FLAG_UP, FLAG_UV, FLAG_AT = 0x01, 0x04, 0x40
# SubjectPublicKeyInfo of an uncompressed P-256 key: SEQUENCE { SEQUENCE { id-ecPublicKey, prime256v1 }, BIT STRING }
P256_SPKI_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")


class PasskeyError(ValueError):
    """A registration or assertion that does not stand; the text says why, for the page and the terminal."""


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text):
    t = str(text or "")
    return base64.urlsafe_b64decode(t + "=" * (-len(t) % 4))


# ------------------------------------------------------------------------------------------------ CBOR (RFC 8949)
def cbor_decode(data):
    """The first CBOR item of DATA and the bytes after it. Enough of RFC 8949 for WebAuthn: unsigned and negative
    integers, byte and text strings, arrays, maps, false, true and null; definite lengths only."""
    def item(i, depth=0):
        if depth > 16:
            raise PasskeyError("the CBOR data nests too deep")
        if i >= len(data):
            raise PasskeyError("the CBOR data ends early")
        major, info = data[i] >> 5, data[i] & 0x1F
        i += 1
        if info < 24:
            arg = info
        elif info in (24, 25, 26, 27):
            n = 1 << (info - 24)
            if i + n > len(data):
                raise PasskeyError("the CBOR data ends early")
            arg, i = int.from_bytes(data[i:i + n], "big"), i + n
        else:
            raise PasskeyError("indefinite or reserved CBOR lengths are not accepted")
        if major == 0:
            return arg, i
        if major == 1:
            return -1 - arg, i
        if major in (2, 3):
            if i + arg > len(data):
                raise PasskeyError("the CBOR data ends early")
            raw = data[i:i + arg]
            return (bytes(raw) if major == 2 else raw.decode("utf-8")), i + arg
        if major == 4:
            out = []
            for _ in range(arg):
                v, i = item(i, depth + 1)
                out.append(v)
            return out, i
        if major == 5:
            out = {}
            for _ in range(arg):
                k, i = item(i, depth + 1)
                v, i = item(i, depth + 1)
                if isinstance(k, (list, dict)):
                    raise PasskeyError("a CBOR map key must be a number or a string")
                out[k] = v
            return out, i
        if major == 7 and info in (20, 21, 22):
            return {20: False, 21: True, 22: None}[info], i
        raise PasskeyError(f"CBOR major type {major} is not accepted here")
    value, end = item(0)
    return value, data[end:]


# ------------------------------------------------------------------------------- authenticator data (WebAuthn §6.1)
def parse_auth_data(auth):
    """{rp_id_hash, flags, counter, credential_id, public_key (COSE map) or None}."""
    if len(auth) < 37:
        raise PasskeyError("the authenticator data is too short")
    out = {"rp_id_hash": auth[:32], "flags": auth[32], "counter": int.from_bytes(auth[33:37], "big"),
           "credential_id": None, "public_key": None}
    if auth[32] & FLAG_AT:
        if len(auth) < 55:
            raise PasskeyError("the attested credential data is too short")
        n = int.from_bytes(auth[53:55], "big")
        if len(auth) < 55 + n:
            raise PasskeyError("the credential id runs past the authenticator data")
        out["credential_id"] = bytes(auth[55:55 + n])
        out["public_key"], _ = cbor_decode(auth[55 + n:])
    return out


def cose_to_spki(cose):
    """The SubjectPublicKeyInfo (DER, base64) of an ES256 P-256 COSE key; any other key is refused."""
    if not isinstance(cose, dict) or cose.get(1) != 2 or cose.get(3) != -7 or cose.get(-1) != 1:
        raise PasskeyError("only an ES256 key on P-256 is accepted (COSE kty 2, alg -7, crv 1)")
    x, y = cose.get(-2), cose.get(-3)
    if not (isinstance(x, bytes) and isinstance(y, bytes) and len(x) == 32 and len(y) == 32):
        raise PasskeyError("the P-256 key's coordinates are not 32 bytes each")
    return base64.b64encode(P256_SPKI_PREFIX + b"\x04" + x + y).decode()


# ------------------------------------------------------------------------- ES256 verification (FIPS 186-4, P-256)
# Verification only, so no secret is handled and timing does not matter. Pure Python: the server image has no
# openssl command, which the client half (of_passkey.es256_verify) calls.
_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_A = _P - 3
_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_G = (0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
      0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5)


def _add(p, q):
    if p is None:
        return q
    if q is None:
        return p
    if p[0] == q[0] and (p[1] + q[1]) % _P == 0:
        return None
    if p == q:
        lam = (3 * p[0] * p[0] + _A) * pow(2 * p[1], -1, _P) % _P
    else:
        lam = (q[1] - p[1]) * pow(q[0] - p[0], -1, _P) % _P
    x = (lam * lam - p[0] - q[0]) % _P
    return x, (lam * (p[0] - x) - p[1]) % _P


def _mul(k, p):
    out = None
    while k:
        if k & 1:
            out = _add(out, p)
        p, k = _add(p, p), k >> 1
    return out


def _der_ints(sig):
    """(r, s) of a DER ECDSA signature, or None."""
    try:
        if sig[0] != 0x30 or sig[1] != len(sig) - 2:
            return None
        out, i = [], 2
        for _ in range(2):
            if sig[i] != 0x02:
                return None
            n = sig[i + 1]
            out.append(int.from_bytes(sig[i + 2:i + 2 + n], "big"))
            i += 2 + n
        return tuple(out) if i == len(sig) else None
    except IndexError:
        return None


def es256_verify(spki_b64, data, der_signature):
    """True when the DER ECDSA P-256 signature over SHA-256(data) verifies with the SubjectPublicKeyInfo key."""
    try:
        spki = base64.b64decode(spki_b64, validate=True)
    except (ValueError, TypeError):
        return False
    if len(spki) != 91 or not spki.startswith(P256_SPKI_PREFIX + b"\x04"):
        return False
    q = (int.from_bytes(spki[27:59], "big"), int.from_bytes(spki[59:91], "big"))
    if (q[1] * q[1] - (q[0] ** 3 + _A * q[0] + _B)) % _P:
        return False  # not a point of the curve
    rs = _der_ints(bytes(der_signature))
    if not rs or not (0 < rs[0] < _N and 0 < rs[1] < _N):
        return False
    r, s_ = rs
    e = int.from_bytes(hashlib.sha256(data).digest(), "big")
    w = pow(s_, -1, _N)
    pt = _add(_mul(e * w % _N, _G), _mul(r * w % _N, q))
    return pt is not None and pt[0] % _N == r


CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def short_code(credential_id, public_key):
    """The code the owner compares: six characters, from an alphabet with no look-alike characters, of SHA-256 over
    the credential id followed by the public key (SubjectPublicKeyInfo DER; base64 accepted). Each side computes it
    itself: the phone from the key it just made, the owner's terminal from the record it is about to sign. A store
    or a server that swapped the public key therefore shows a different code at the terminal (of_secrets-review on
    organic-framework PR 81: a code of the credential id alone, relayed by of-server, let a swapped key pass)."""
    if isinstance(credential_id, str):
        credential_id = unb64u(credential_id)
    if isinstance(public_key, str):
        public_key = base64.b64decode(public_key)
    alphabet = CODE_ALPHABET
    n = int.from_bytes(hashlib.sha256(bytes(credential_id) + bytes(public_key)).digest()[:8], "big")
    out = ""
    for _ in range(6):
        n, r = divmod(n, len(alphabet))
        out += alphabet[r]
    return out


def _client_data(client_data_json, kind, challenge, origin):
    try:
        client = json.loads(client_data_json)
    except (TypeError, ValueError):
        raise PasskeyError("the client data is not JSON") from None
    if client.get("type") != kind:
        raise PasskeyError(f"the client data is of type {client.get('type')!r}, not {kind}")
    if client.get("challenge") != b64u(challenge):
        raise PasskeyError("the client data answers another challenge")
    if client.get("origin") != origin:
        raise PasskeyError(f"made on {client.get('origin')!r}, not on {origin!r}")
    if client.get("crossOrigin") is True:
        raise PasskeyError("made inside a page of another origin")
    return client


def verify_registration(attestation_object, client_data_json, challenge, rp_id, origin):
    """{credential_id (base64url), public_key (SPKI base64), counter, code} of a registration that answers CHALLENGE
    on ORIGIN for RP_ID with the person present and verified, else PasskeyError. The attestation statement is not
    checked: the owner's comparison of the two codes, and their signature over the enrollment, bind the key instead."""
    _client_data(client_data_json, "webauthn.create", challenge, origin)
    obj, rest = cbor_decode(attestation_object)
    if rest or not isinstance(obj, dict) or not isinstance(obj.get("authData"), bytes):
        raise PasskeyError("the attestation object is not one CBOR map with authData")
    auth = parse_auth_data(obj["authData"])
    if auth["rp_id_hash"] != hashlib.sha256(rp_id.encode()).digest():
        raise PasskeyError("the authenticator data names another relying party")
    if not auth["flags"] & FLAG_UP or not auth["flags"] & FLAG_UV:
        raise PasskeyError("the passkey did not verify the person (no user presence or verification)")
    if not auth["credential_id"] or auth["public_key"] is None:
        raise PasskeyError("the registration carries no credential")
    spki = cose_to_spki(auth["public_key"])
    return {"credential_id": b64u(auth["credential_id"]), "public_key": spki, "counter": auth["counter"],
            "code": short_code(auth["credential_id"], spki)}


def verify_assertion(authenticator_data, client_data_json, signature, challenge, rp_id, origin, public_key,
                     last_counter=0):
    """The counter of an assertion by PUBLIC_KEY over CHALLENGE, else PasskeyError (WebAuthn §7.2)."""
    _client_data(client_data_json, "webauthn.get", challenge, origin)
    auth = parse_auth_data(authenticator_data)
    if auth["rp_id_hash"] != hashlib.sha256(rp_id.encode()).digest():
        raise PasskeyError("the authenticator data names another relying party")
    if not auth["flags"] & FLAG_UP or not auth["flags"] & FLAG_UV:
        raise PasskeyError("the passkey did not verify the person (no user presence or verification)")
    if auth["counter"] and auth["counter"] <= (last_counter or 0):
        raise PasskeyError(f"the signature counter {auth['counter']} is not above {last_counter} (a cloned key?)")
    if not es256_verify(public_key, bytes(authenticator_data) + hashlib.sha256(client_data_json).digest(), signature):
        raise PasskeyError("the signature does not verify against the enrolled passkey")
    return auth["counter"]


# ------------------------------------------------------------------------------------------------ the store
DDL = """
CREATE TABLE IF NOT EXISTS passkey_windows (id TEXT PRIMARY KEY, org TEXT NOT NULL, person TEXT NOT NULL,
  by_machine TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL,
  reg_challenge BLOB NOT NULL, test_challenge BLOB, credential_id TEXT, public_key TEXT, code TEXT);
CREATE TABLE IF NOT EXISTS passkey_enrollments (credential_id TEXT PRIMARY KEY, org TEXT NOT NULL,
  person TEXT NOT NULL, public_key TEXT NOT NULL, record TEXT NOT NULL, counter INTEGER NOT NULL DEFAULT 0,
  enrolled_at REAL NOT NULL, revoked_at REAL);
CREATE TABLE IF NOT EXISTS passkey_links (nonce TEXT PRIMARY KEY, org TEXT NOT NULL, diff_id TEXT NOT NULL,
  digest TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, used REAL);
"""
WINDOW_TTL_MAX = 900        # an enrollment window: one credential, then it closes
LINK_TTL_MAX = 3600         # an approval link
ENROLLMENT_DAYS = 365       # the signed enrollment record's own expiry, written by the owner's terminal


class Refused(Exception):
    """A refusal the routes answer with: (status, type, title). The title never carries a key or a value."""
    def __init__(self, status, typ, title):
        super().__init__(title)
        self.status, self.typ, self.title = status, typ, title


def _iso(t):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


class Passkeys:
    """of-server's passkey state for every organization it serves. cfg: `public_url` (https://<host>, the page's
    origin; its host is the relying party id). grant_keys: () -> the tenant's pinned keys (of_grants.Keys)."""

    def __init__(self, store, cfg, grant_keys, clock=None):
        import time
        self.store, self.cfg, self.grant_keys, self.clock = store, cfg, grant_keys, clock or time.time
        url = str(cfg.get("public_url") or "").rstrip("/")
        self.origin = url if url.startswith("https://") else None
        self.rp_id = url[len("https://"):].split("/")[0].split(":")[0] if self.origin else None
        with store.conn() as c:
            c.executescript(DDL)

    def _need_origin(self):
        if not self.origin:
            raise Refused(409, "not_configured", "passkeys need of-server's public_url, an https address a phone "
                                                 "reaches (docs/remote.md §6, item 4)")

    @staticmethod
    def _tenant_of(m, org):
        orgs = m.get("orgs")
        orgs = json.loads(orgs) if isinstance(orgs, str) else (orgs or [])
        if not m.get("tenant") or org not in orgs:
            raise Refused(403, "forbidden", f"only the tenant's machine, joined to {org}, may do this")

    # -- enrollment ------------------------------------------------------------------------------------------
    def open_window(self, m, org, person, ttl=600):
        """A one-credential window for PERSON of ORG. It enrolls nothing by itself: the owner's grant-key signature
        after the code match does (of_architect, msg 01a1cfbd-2805, point 1)."""
        import os
        import secrets
        self._need_origin()
        self._tenant_of(m, org)
        if not person or not isinstance(person, str) or len(person) > 64:
            raise Refused(400, "bad_request", "person is the person's id in the organization's registry")
        now, wid = self.clock(), secrets.token_urlsafe(24)
        ttl = max(60, min(int(ttl or 600), WINDOW_TTL_MAX))
        with self.store.tx() as c:
            c.execute("UPDATE passkey_windows SET state = 'closed' WHERE org = ? AND person = ? AND state != 'closed'"
                      " AND state != 'enrolled'", (org, person))  # one open window per person
            c.execute("INSERT INTO passkey_windows (id, org, person, by_machine, created, expires, state, reg_challenge)"
                      " VALUES (?, ?, ?, ?, ?, ?, 'open', ?)", (wid, org, person, m["machine_id"], now, now + ttl,
                                                                 os.urandom(32)))
        return {"window": wid, "url": f"{self.origin}/passkey/enroll?w={wid}", "expires": _iso(now + ttl)}

    def _window(self, wid, *states):
        with self.store.conn() as c:
            row = c.execute("SELECT id, org, person, by_machine, created, expires, state, reg_challenge, test_challenge,"
                            " credential_id, public_key, code FROM passkey_windows WHERE id = ?", (str(wid),)).fetchone()
        keys = ("id", "org", "person", "by_machine", "created", "expires", "state", "reg_challenge", "test_challenge",
                "credential_id", "public_key", "code")
        w = dict(zip(keys, row)) if row else None
        if not w or w["expires"] < self.clock() or (states and w["state"] not in states):
            raise Refused(404, "not_found", "no such open enrollment window (it expired, closed or took its credential)")
        return w

    def token_known(self, token):
        """Is TOKEN an open window's id or an unused link's nonce? The unauthenticated routes count requests only
        under such a token, so random tokens leave nothing behind (of_review's probe on organic-framework PR 81)."""
        t, now = str(token or "")[:64], self.clock()
        with self.store.conn() as c:
            return bool(c.execute("SELECT 1 FROM passkey_windows WHERE id = ? AND expires > ? AND state != 'closed'"
                                  " UNION ALL SELECT 1 FROM passkey_links WHERE nonce = ? AND used IS NULL AND"
                                  " expires > ? LIMIT 1", (t, now, t, now)).fetchone())

    def window_options(self, wid):
        """What the phone's page passes to navigator.credentials.create."""
        self._need_origin()
        w = self._window(wid, "open")
        return {"challenge": b64u(w["reg_challenge"]), "rp": {"id": self.rp_id, "name": "Organic Framework"},
                "user": {"id": b64u(hashlib.sha256(f"{w['org']}:{w['person']}".encode()).digest()),
                         "name": f"{w['person']} ({w['org']})", "displayName": w["person"]},
                "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
                "authenticatorSelection": {"userVerification": "required", "residentKey": "preferred"},
                "attestation": "none", "timeout": 120000}

    def register(self, wid, attestation_object, client_data_json):
        """The phone's registration: verified, kept on the window, and answered with the test challenge."""
        import os
        self._need_origin()
        w = self._window(wid, "open")
        try:
            reg = verify_registration(unb64u(attestation_object), unb64u(client_data_json), w["reg_challenge"],
                                      self.rp_id, self.origin)
        except (PasskeyError, ValueError) as e:
            raise Refused(400, "bad_registration", str(e)) from None
        test = os.urandom(32)
        with self.store.conn() as c:  # WebAuthn L2 §7.1 step 22: a credential id registered already is refused
            if c.execute("SELECT 1 FROM passkey_enrollments WHERE credential_id = ?",
                         (reg["credential_id"],)).fetchone():
                raise Refused(409, "already_registered", "this passkey is registered already (or was revoked)")
        with self.store.tx() as c:
            n = c.execute("UPDATE passkey_windows SET state = 'registered', credential_id = ?, public_key = ?, code = ?,"
                          " test_challenge = ? WHERE id = ? AND state = 'open'",
                          (reg["credential_id"], reg["public_key"], reg["code"], test, wid)).rowcount
        if n != 1:  # another registration took the window first: this one enrolls nothing
            raise Refused(409, "window_taken", "the window already took a credential")
        return {"credential": reg["credential_id"], "test_challenge": b64u(test)}

    def test(self, wid, credential, authenticator_data, client_data_json, signature):
        """The new key's test assertion; the answer is the code the phone shows."""
        self._need_origin()
        w = self._window(wid, "registered")
        if credential != w["credential_id"]:
            raise Refused(400, "bad_assertion", "the test assertion is not by the window's credential")
        try:
            verify_assertion(unb64u(authenticator_data), unb64u(client_data_json), unb64u(signature),
                             w["test_challenge"], self.rp_id, self.origin, w["public_key"])
        except (PasskeyError, ValueError) as e:
            raise Refused(400, "bad_assertion", str(e)) from None
        with self.store.tx() as c:
            c.execute("UPDATE passkey_windows SET state = 'tested' WHERE id = ? AND state = 'registered'", (wid,))
        return {"code": w["code"]}

    def window_state(self, m, wid):
        """For the owner's terminal: the window's state, and once tested, what the owner signs."""
        w = self._window(wid)
        self._tenant_of(m, w["org"])
        out = {"window": wid, "org": w["org"], "person": w["person"], "state": w["state"], "expires": _iso(w["expires"])}
        if w["state"] in ("tested", "enrolled"):
            out.update(code=w["code"], credential=w["credential_id"], public_key=w["public_key"], rp_id=self.rp_id,
                       origin=self.origin)
        return out

    def enroll(self, m, record):
        """Keep an enrollment record the owner signed with the grant key, for a tested window's credential."""
        import of_grants
        self._need_origin()
        if not isinstance(record, dict) or record.get("kind") != "passkey_enrollment":
            raise Refused(400, "bad_request", "record is a passkey_enrollment record")
        self._tenant_of(m, str(record.get("org")))
        with self.store.conn() as c:
            row = c.execute("SELECT id FROM passkey_windows WHERE org = ? AND person = ? AND credential_id = ?"
                            " AND state = 'tested' AND expires > ?", (record.get("org"), record.get("person"),
                                                                       record.get("credential"), self.clock())).fetchone()
        if not row:
            raise Refused(409, "no_window", "no tested window holds this credential for this person")
        w = self._window(row[0], "tested")
        for k, want in (("public_key", w["public_key"]), ("rp_id", self.rp_id), ("origin", self.origin)):
            if record.get(k) != want:
                raise Refused(400, "bad_record", f"the record's {k} is not the window's")
        if not of_grants.verify(record, self.grant_keys()):
            raise Refused(403, "bad_signature", "the record does not verify against the tenant's pinned grant key")
        with self.store.tx() as c:
            if c.execute("SELECT 1 FROM passkey_enrollments WHERE credential_id = ?",
                         (w["credential_id"],)).fetchone():
                raise Refused(409, "already_registered", "this passkey is enrolled already (or was revoked)")
            c.execute("INSERT INTO passkey_enrollments (credential_id, org, person, public_key, record,"
                      " enrolled_at) VALUES (?, ?, ?, ?, ?, ?)", (w["credential_id"], w["org"], w["person"],
                                                                 w["public_key"], json.dumps(record), self.clock()))
            c.execute("UPDATE passkey_windows SET state = 'enrolled' WHERE id = ?", (w["id"],))
        return {"enrolled": w["credential_id"], "person": w["person"], "org": w["org"]}

    # -- approval ----------------------------------------------------------------------------------------------
    def put_link(self, m, org, diff_id, diff, digest, ttl=900):
        """`of diff link`: the diff itself, checked against its digest with the shared code, and a single-use link."""
        import secrets
        import of_diff
        self._need_origin()
        orgs = m.get("orgs")
        orgs = json.loads(orgs) if isinstance(orgs, str) else (orgs or [])
        if org not in orgs:
            raise Refused(403, "forbidden", f"this machine has not joined {org}")
        if not isinstance(diff, dict) or diff.get("id") != diff_id or diff.get("org") not in (None, org):
            raise Refused(400, "bad_request", "diff is the proposed diff, with this id and organization")
        if of_diff.digest(diff) != digest:
            raise Refused(409, "digest_mismatch", "the digest is not the diff's (of_diff.digest over its canonical text)")
        text = json.dumps(diff, indent=2, sort_keys=True, ensure_ascii=False, default=str)
        now, nonce = self.clock(), secrets.token_urlsafe(24)
        ttl = max(60, min(int(ttl or 900), LINK_TTL_MAX))
        with self.store.tx() as c:
            c.execute("INSERT INTO passkey_links (nonce, org, diff_id, digest, text, created, expires) VALUES"
                      " (?, ?, ?, ?, ?, ?, ?)", (nonce, org, diff_id, digest, text, now, now + ttl))
        from urllib.parse import quote
        return {"url": f"{self.origin}/approve/diff?org={quote(org)}&id={quote(diff_id)}&digest={digest}&n={nonce}",
                "nonce": nonce, "expires": _iso(now + ttl)}

    def _link(self, nonce):
        with self.store.conn() as c:
            row = c.execute("SELECT nonce, org, diff_id, digest, text, expires, used FROM passkey_links WHERE nonce = ?",
                            (str(nonce or ""),)).fetchone()
        link = dict(zip(("nonce", "org", "diff_id", "digest", "text", "expires", "used"), row)) if row else None
        if not link or link["used"] or link["expires"] < self.clock():
            raise Refused(404, "not_found", "no such approval link (it expired or was used)")
        return link

    def approval_page(self, org, diff_id, digest, nonce):
        """What the approval page shows and asks the phone to sign; nothing without a valid nonce."""
        import of_passkey
        self._need_origin()
        link = self._link(nonce)
        if (link["org"], link["diff_id"], link["digest"]) != (org, diff_id, digest):
            raise Refused(404, "not_found", "no such approval link (it expired or was used)")
        pre = of_passkey.preimage(org, diff_id, digest, nonce, _iso(link["expires"]))
        with self.store.conn() as c:
            creds = [r[0] for r in c.execute("SELECT credential_id FROM passkey_enrollments WHERE org = ? AND"
                                             " revoked_at IS NULL", (org,))]
        return {"org": org, "id": diff_id, "digest": digest, "text": link["text"], "preimage": pre,
                "challenge": b64u(hashlib.sha256(pre.encode()).digest()), "credentials": creds,
                "rp_id": self.rp_id, "expires": _iso(link["expires"])}

    def approve(self, nonce, credential, authenticator_data, client_data_json, signature):
        """Verify the assertion over the link's preimage with the enrolled key, use the nonce, and return the
        of.diff_approved payload (of_passkey's record shape) for the organization's feed."""
        import of_passkey
        self._need_origin()
        link = self._link(nonce)
        with self.store.conn() as c:
            row = c.execute("SELECT org, person, public_key, record, counter, revoked_at FROM passkey_enrollments"
                            " WHERE credential_id = ?", (str(credential or ""),)).fetchone()
        if not row or row[0] != link["org"]:
            raise Refused(403, "not_enrolled", "this passkey is not enrolled for the organization")
        if row[5] is not None:
            raise Refused(403, "revoked", "this passkey was revoked")
        pre = of_passkey.preimage(link["org"], link["diff_id"], link["digest"], nonce, _iso(link["expires"]))
        try:
            count = verify_assertion(unb64u(authenticator_data), unb64u(client_data_json), unb64u(signature),
                                     hashlib.sha256(pre.encode()).digest(), self.rp_id, self.origin, row[2],
                                     last_counter=row[4])
        except (PasskeyError, ValueError) as e:
            raise Refused(400, "bad_assertion", str(e)) from None
        with self.store.tx() as c:
            if c.execute("UPDATE passkey_links SET used = ? WHERE nonce = ? AND used IS NULL",
                         (self.clock(), nonce)).rowcount != 1:
                raise Refused(409, "used", "the approval link was used already")
            if count:
                c.execute("UPDATE passkey_enrollments SET counter = ? WHERE credential_id = ?", (count, credential))
        return {"id": link["diff_id"], "digest": link["digest"], "by": "owner", "via": "passkey", "org": link["org"],
                "credential": credential, "preimage": pre, "authenticator_data": authenticator_data,
                "client_data_json": client_data_json, "signature": signature, "enrollment": json.loads(row[3])}

    def revoke(self, m, record):
        """An owner-signed passkey_revocation (docs/registry.md, Passkey revocations): of-server refuses the
        credential at once. Machines learn it from the organization's grants file (of_grants.passkey_revoked)."""
        import of_grants
        if not isinstance(record, dict) or record.get("kind") != "passkey_revocation":
            raise Refused(400, "bad_request", "record is a passkey_revocation record")
        self._tenant_of(m, str(record.get("org")))
        if not of_grants.verify(record, self.grant_keys()):
            raise Refused(403, "bad_signature", "the record does not verify against the tenant's pinned grant key")
        with self.store.tx() as c:
            n = c.execute("UPDATE passkey_enrollments SET revoked_at = ? WHERE credential_id = ? AND org = ?",
                          (self.clock(), record.get("credential"), record.get("org"))).rowcount
        return {"revoked": record.get("credential"), "known": bool(n)}


# ------------------------------------------------------------------------------------------------ the pages
# Served with no cache, no referrer, no framing and a script nonce (headers()). Text is English: the locale layer runs
# on the person's machine, not on of-server; the page says only what to check and what was done.
_JS = r"""
const b64u = b => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
const unb64u = s => Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/') + '='.repeat((4 - s.length % 4) % 4)), c => c.charCodeAt(0));
const say = t => { document.getElementById('status').textContent = t; };
async function post(path, body) {
  const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
                               credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer'});
  const j = await r.json(); if (!r.ok) throw new Error(j.title || r.status); return j;
}
async function codeOf(rawId, spki) {
  const both = new Uint8Array(rawId.byteLength + spki.byteLength);
  both.set(new Uint8Array(rawId), 0); both.set(new Uint8Array(spki), rawId.byteLength);
  const h = new Uint8Array(await crypto.subtle.digest('SHA-256', both));
  let n = 0n; for (let i = 0; i < 8; i++) n = (n << 8n) | BigInt(h[i]);
  const A = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'; let out = '';
  for (let i = 0; i < 6; i++) { out += A[Number(n % 31n)]; n = n / 31n; }
  return out;
}
async function assertWith(challenge, ids) {
  const a = await navigator.credentials.get({publicKey: {challenge: unb64u(challenge), userVerification: 'required',
    allowCredentials: ids.map(id => ({type: 'public-key', id: unb64u(id)})), timeout: 120000}});
  return {credential: b64u(a.rawId), authenticator_data: b64u(a.response.authenticatorData),
          client_data_json: b64u(a.response.clientDataJSON), signature: b64u(a.response.signature)};
}
"""

_STYLE = ("body{font:16px/1.45 -apple-system,system-ui,sans-serif;max-width:40rem;margin:1.5rem auto;padding:0 1rem}"
          "pre{white-space:pre-wrap;word-break:break-word;background:#f4f4f4;padding:.75rem;border-radius:6px}"
          "button{font-size:1.1rem;padding:.6rem 1.2rem}#code{font:700 2rem ui-monospace,monospace;letter-spacing:.2em}")


def _page(title, body, script, csp_nonce):
    import html
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title>"
            f"<style nonce='{csp_nonce}'>{_STYLE}</style></head><body>{body}"
            f"<p id='status' role='status'></p><script nonce='{csp_nonce}'>{_JS}{script}</script></body></html>"
            ).encode()


def headers(csp_nonce):
    """The headers both pages carry (of_architect, msg 01a1cfbd-2805, point 2)."""
    return (("Cache-Control", "no-store"), ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY"),
            ("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{csp_nonce}'; "
                                        f"style-src 'nonce-{csp_nonce}'; connect-src 'self'; frame-ancestors 'none'; "
                                        "base-uri 'none'; form-action 'none'"),
            ("X-Content-Type-Options", "nosniff"))


def enroll_page(wid, csp_nonce):
    w = json.dumps(str(wid)).replace("<", "\\u003c")
    body = ("<h1>Enroll this phone's passkey</h1><p>Your organization's owner opened this window at their machine. "
            "Create the passkey, then read the code shown here to the owner. This phone computes it from the key it "
            "made; their terminal computes its own from the key it would sign, and they sign only when the two "
            "match.</p><button id='go'>Create the passkey</button><p id='code'></p>")
    script = (f"const W = {w};" + r"""
document.getElementById('go').onclick = async () => {
  try {
    const o = await (await fetch('/v1/passkey/windows/' + encodeURIComponent(W) + '/options', {cache: 'no-store'})).json();
    if (!o.challenge) throw new Error(o.title || 'the window is closed');
    const c = await navigator.credentials.create({publicKey: {challenge: unb64u(o.challenge), rp: o.rp,
      user: {id: unb64u(o.user.id), name: o.user.name, displayName: o.user.displayName},
      pubKeyCredParams: o.pubKeyCredParams, authenticatorSelection: o.authenticatorSelection,
      attestation: o.attestation, timeout: o.timeout}});
    const spki = c.response.getPublicKey ? c.response.getPublicKey() : null;
    if (!spki) throw new Error('this browser does not give the new public key to the page; use another browser');
    const mine = await codeOf(c.rawId, spki);   // computed here, from the key this phone made
    const reg = await post('/v1/passkey/windows/' + encodeURIComponent(W) + '/register',
      {attestation_object: b64u(c.response.attestationObject), client_data_json: b64u(c.response.clientDataJSON)});
    say('Now confirm once more, to test the new passkey.');
    const t = await post('/v1/passkey/windows/' + encodeURIComponent(W) + '/test',
      await assertWith(reg.test_challenge, [reg.credential]));
    document.getElementById('code').textContent = mine;
    say(mine === t.code ? 'Read this code to the owner. Nothing is enrolled until they sign at their machine.'
                        : 'Warning: the server holds another key than this phone made. Tell the owner not to sign.');
    document.getElementById('go').disabled = true;
  } catch (e) { say('Not enrolled: ' + e.message); }
};""")
    return _page("Enroll a passkey", body, script, csp_nonce)


def visible(text):
    """TEXT with every control, format, bidi, private-use and separator character other than newline and tab shown as
    a visible marker, so the page shows what the text is, not how such characters make it read (of_secrets-review
    on organic-framework PR 81: an agent writes the diff, and U+202E reorders what follows it). Returns (text, how
    many were marked)."""
    import unicodedata
    out, n = [], 0
    for ch in str(text):
        if ch not in "\n\t" and unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs", "Zl", "Zp"):
            out.append(f"[U+{ord(ch):04X}]")
            n += 1
        else:
            out.append(ch)
    return "".join(out), n


def approve_page(data, csp_nonce):
    """DATA: Passkeys.approval_page(...) plus the nonce. The diff's exact text is shown escaped; the phone signs the
    SHA-256 of the preimage that names its full digest."""
    import html
    payload = json.dumps({"nonce": data["nonce"], "challenge": data["challenge"], "credentials": data["credentials"]})
    payload = payload.replace("<", "\\u003c")  # never closes the script element
    text, marked = visible(data["text"])
    warn = (f"<p><b>Warning:</b> {marked} invisible or direction-changing character(s) in this text are shown as "
            "[U+XXXX]. Ask why before you approve.</p>") if marked else ""
    body = (f"<h1>Approve a change to {html.escape(visible(data['org'])[0])}</h1>"
            f"<p>Diff <b>{html.escape(visible(data['id'])[0])}</b>. Read its exact text. Your passkey signs this "
            f"text's digest, and nothing else can be applied with your approval.</p>{warn}<pre>{html.escape(text)}</pre>"
            f"<p>Digest: <code>{html.escape(data['digest'][:16])}</code>… (all 64 characters are signed). "
            f"The link expires at {html.escape(data['expires'])}.</p><button id='go'>Approve with my passkey</button>")
    script = (f"const D = {payload};" + r"""
document.getElementById('go').onclick = async () => {
  try {
    if (!D.credentials.length) throw new Error('no passkey is enrolled for this organization');
    const a = await assertWith(D.challenge, D.credentials);
    await post('/v1/passkey/approve', Object.assign({nonce: D.nonce}, a));
    say('Approved. The machine checks your signature again before it applies the diff.');
    document.getElementById('go').disabled = true;
  } catch (e) { say('Not approved: ' + e.message); }
};""")
    return _page("Approve a change", body, script, csp_nonce)


def feed_row(org, payload, clock=None):
    """The of.diff_approved event of-server writes into the organization's coordination feed (of_server_coord.copy):
    every machine's relay stores it, and `of diff apply` checks it again there (docs/remote.md §6, step 3)."""
    import time
    import uuid
    t = (clock or time.time)()
    eid = str(uuid.uuid7() if hasattr(uuid, "uuid7") else uuid.uuid4())
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000) % 1000:03d}Z"
    rec = {"of.schema": 1, "of.event.id": eid, "timestamp": ts, "event.name": "of.diff_approved",
           "host.id": "of-server", "host.name": "of-server", "of.org": org, "of.source": "of-server",
           "of.payload": payload}
    return {"event_id": eid, "name": "of.diff_approved", "ts": ts, "host_id": "of-server", "record": json.dumps(rec)}
