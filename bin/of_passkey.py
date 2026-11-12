"""A diff approved with the person's passkey (docs/remote.md §6; hand-over b4e83a73 from of_architect).

The client's half: `of diff link` and the three checks `of diff apply` runs. of-server (of_server) enrolls the passkey,
serves the approval page and records the approval; nothing here trusts of-server, since every check is on signatures.

The approval event, `of.diff_approved` with `via: passkey`, carries everything a check needs:
    {id, digest, by: "owner", via: "passkey", org,
     challenge: {nonce, expires},                       # expires: ISO time, UTC
     credential: "<credential id, base64url>",
     enrollment: {kind: "passkey_enrollment", org, person, credential, public_key, rp_id, origin, issued_at,
                  signature: "ssh-ed25519:…"},           # signed by the owner with the grant key (`of grant`)
     assertion: {authenticator_data, client_data_json, signature}}   # base64url, as WebAuthn returns them
public_key is the passkey's public key as SubjectPublicKeyInfo DER, base64 (ES256, P-256: the passkeys of phones).

The three checks (docs/remote.md §6, item 3):
1. the enrollment record verifies against the pinned grant public key and names the diff's organization;
2. the assertion verifies against the enrolled key: client data of type webauthn.get, the expected challenge and
   origin, the relying party's id hash, user presence and user verification, and the ES256 signature over
   authenticator data and the client data's hash;
3. the challenge is the SHA-256 of [organization, diff id, digest, nonce, expiry] with the diff's current digest, and
   the approval was recorded before its expiry.
"""

import base64
import hashlib
import json
import os
import subprocess
import tempfile
import time

KIND = "passkey_enrollment"
FLAG_UP, FLAG_UV = 0x01, 0x04


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text):
    t = str(text or "")
    return base64.urlsafe_b64decode(t + "=" * (-len(t) % 4))


def challenge(org, diff_id, digest, nonce, expires):
    """The 32 bytes the page asks the passkey to sign: they bind the organization, the diff and its exact digest."""
    return hashlib.sha256(json.dumps([org, diff_id, digest, nonce, expires], separators=(",", ":"),
                                     ensure_ascii=False).encode()).digest()


def _epoch(iso):
    import calendar
    return calendar.timegm(time.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S"))


def es256_verify(spki_b64, data, der_signature):
    """True when the DER ECDSA signature over `data` verifies with the SubjectPublicKeyInfo key (openssl)."""
    try:
        spki = base64.b64decode(spki_b64, validate=True)
    except (ValueError, TypeError):
        return False
    pem = "-----BEGIN PUBLIC KEY-----\n" + "\n".join(
        base64.b64encode(spki).decode()[i:i + 64] for i in range(0, len(base64.b64encode(spki)), 64)) + \
        "\n-----END PUBLIC KEY-----\n"
    with tempfile.TemporaryDirectory() as d:
        paths = {n: os.path.join(d, n) for n in ("key.pem", "data", "sig")}
        for n, body in (("key.pem", pem.encode()), ("data", data), ("sig", der_signature)):
            with open(paths[n], "wb") as fh:
                fh.write(body)
        try:
            r = subprocess.run(["openssl", "dgst", "-sha256", "-verify", paths["key.pem"], "-signature", paths["sig"],
                                paths["data"]], capture_output=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return False
    return r.returncode == 0


def enrollment_problem(record, org, keys):
    """Why the enrollment record does not stand, or None (check 1)."""
    import of_grants
    if not isinstance(record, dict) or record.get("kind") != KIND:
        return "the approval carries no passkey enrollment record"
    if record.get("org") != org:
        return f"the passkey is enrolled for {record.get('org')!r}, not for {org!r}"
    for k in ("person", "credential", "public_key", "rp_id", "origin"):
        if not record.get(k):
            return f"the enrollment record has no {k}"
    if not of_grants.verify(record, keys):
        return "the enrollment record does not verify against the pinned grant public key"
    return None


def assertion_problem(assertion, record, expected_challenge):
    """Why the WebAuthn assertion does not verify against the enrolled key, or None (check 2)."""
    try:
        auth = unb64u(assertion["authenticator_data"])
        cdj = unb64u(assertion["client_data_json"])
        sig = unb64u(assertion["signature"])
        client = json.loads(cdj)
    except (KeyError, TypeError, ValueError):
        return "the assertion is not readable (authenticator_data, client_data_json, signature)"
    if client.get("type") != "webauthn.get":
        return f"the client data is of type {client.get('type')!r}, not webauthn.get"
    if client.get("challenge") != b64u(expected_challenge):
        return "the signed challenge is not this diff's: another diff, another digest, or another expiry"
    if client.get("origin") != record["origin"]:
        return f"signed on {client.get('origin')!r}, not on the enrolled origin {record['origin']!r}"
    if len(auth) < 37 or auth[:32] != hashlib.sha256(str(record["rp_id"]).encode()).digest():
        return "the authenticator data names another relying party"
    if not auth[32] & FLAG_UP or not auth[32] & FLAG_UV:
        return "the passkey did not verify the person (no user presence or verification)"
    if not es256_verify(record["public_key"], auth + hashlib.sha256(cdj).digest(), sig):
        return "the signature does not verify against the enrolled passkey"
    return None


def approval_problem(approval, diff_id, org, digest, keys, recorded_at=None):
    """Why a passkey approval does not approve this diff in its current form, or None. `recorded_at`: the
    approval event's time (ISO)."""
    if approval.get("digest") != digest:
        return "the approval is for another form of the diff (its digest changed since)"
    ch = approval.get("challenge") or {}
    if not ch.get("nonce") or not ch.get("expires"):
        return "the approval carries no challenge nonce and expiry"
    try:
        expires = _epoch(ch["expires"])
        at = _epoch(recorded_at) if recorded_at else time.time()
    except (ValueError, OverflowError):
        return "the challenge's expiry is not a time"
    if at > expires:
        return f"the approval was recorded after its challenge expired ({ch['expires']})"
    record = approval.get("enrollment")
    why = enrollment_problem(record, org, keys)
    if why:
        return why
    if approval.get("credential") and approval["credential"] != record["credential"]:
        return "the approval names another credential than the enrolled one"
    return assertion_problem(approval.get("assertion") or {}, record,
                             challenge(org, diff_id, digest, ch["nonce"], ch["expires"]))


def link(server, org, diff_id, digest):
    """The approval page's address on the organization's of-server, or (None, why). WebAuthn needs HTTPS at a stable
    name (docs/remote.md §6, item 4)."""
    from urllib.parse import quote
    s = str(server or "").rstrip("/")
    if not s.startswith("https://"):
        return None, (f"{org}'s of-server is at {s or 'no address'}, not at an HTTPS address a phone can reach; a "
                      "passkey needs one (docs/remote.md §6, item 4). Exposing it is the owner's decision")
    return f"{s}/approve/diff?org={quote(org)}&id={quote(diff_id)}&digest={quote(digest)}", None
