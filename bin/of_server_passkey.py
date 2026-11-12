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
    def item(i):
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
                v, i = item(i)
                out.append(v)
            return out, i
        if major == 5:
            out = {}
            for _ in range(arg):
                k, i = item(i)
                v, i = item(i)
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


def short_code(credential_id):
    """The code the phone's page and the owner's terminal both show: six characters from the credential id's hash,
    from an alphabet with no look-alike characters."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    n = int.from_bytes(hashlib.sha256(credential_id).digest()[:8], "big")
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
    return {"credential_id": b64u(auth["credential_id"]), "public_key": cose_to_spki(auth["public_key"]),
            "counter": auth["counter"], "code": short_code(auth["credential_id"])}


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
