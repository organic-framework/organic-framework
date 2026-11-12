"""A diff approved with the person's passkey (docs/remote.md §6; hand-over b4e83a73 from of_architect).

The client's half: the checks `of diff apply` runs (`of diff link` asks of-server for the page's address). of-server (of_server) enrolls the passkey,
serves the approval page and records the approval; nothing here trusts of-server, since every check is on signatures.

The approval event, `of.diff_approved` with `via: passkey` (docs/remote.md §6, item 2), carries what a check needs:
    {id, digest, by: "owner", via: "passkey", org, credential,
     preimage: '["<org>","<diff id>","<digest>","<nonce>","<expires, ISO UTC>"]',   # compact JSON; challenge = SHA-256
     authenticator_data, client_data_json, signature,     # base64url, as WebAuthn returns them
     enrollment: {kind: "passkey_enrollment", org, person, credential, public_key, rp_id, origin, issued_at,
                  expires, signature: "ssh-ed25519:…"}}  # signed by the owner with the grant key (`of grant`)
public_key is the passkey's public key as SubjectPublicKeyInfo DER, base64 (ES256, P-256: the passkeys of phones).

The checks (docs/remote.md §6, item 3; W3C WebAuthn Level 2 §7.2):
- client data of type webauthn.get, its challenge the SHA-256 of the preimage, its origin the enrolled one, which is
  the organization's of-server;
- the relying party's id hash, user presence and user verification; a non-zero signature counter higher than any
  this credential gave before;
- the ES256 signature over authenticator data and the client data's hash, with the enrolled key;
- the enrollment record verifies against the pinned grant public key, names the organization, and has not expired;
- the preimage names this organization, this diff and its current digest, and the approval was recorded before its
  expiry;
- the enrolled person is an approver this diff needs;
- the credential is not revoked: of_grants.passkey_revoked (of_server's reader of the signed revocations). Until that
  reader exists no passkey approval is accepted, since none could be taken back.
The trust anchor is the pinned grant public key file on this machine (of_grants.pin_path()); whoever can write that
file can sign enrollments, as they can sign grants. The guard's protection of it is docs/guard.md's.
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


def preimage(org, diff_id, digest, nonce, expires):
    """The text whose SHA-256 is the challenge: it binds the organization, the diff and its exact digest."""
    return json.dumps([org, diff_id, digest, nonce, expires], separators=(",", ":"), ensure_ascii=False)


def challenge(org, diff_id, digest, nonce, expires):
    return hashlib.sha256(preimage(org, diff_id, digest, nonce, expires).encode()).digest()


def _epoch(iso):
    """Seconds since the epoch of an ISO time; a time without an offset is UTC."""
    import datetime
    t = datetime.datetime.fromisoformat(str(iso).strip().replace("Z", "+00:00"))
    return (t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)).timestamp()


def counter(approval):
    """The authenticator's signature counter in the approval, or None when unreadable."""
    try:
        auth = unb64u(approval.get("authenticator_data"))
    except (ValueError, TypeError):
        return None
    return int.from_bytes(auth[33:37], "big") if len(auth) >= 37 else None


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


def _origin(url):
    from urllib.parse import urlsplit
    u = urlsplit(str(url or ""))
    return f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else None


def enrollment_problem(record, org, keys, at=None, server=None):
    """Why the enrollment record does not stand, or None. `at`: when the approval was recorded (epoch seconds);
    `server`: the organization's of-server address, whose origin the enrolled one must be."""
    import of_grants
    if not isinstance(record, dict) or record.get("kind") != KIND:
        return "the approval carries no passkey enrollment record"
    if record.get("org") != org:
        return f"the passkey is enrolled for {record.get('org')!r}, not for {org!r}"
    for k in ("person", "credential", "public_key", "rp_id", "origin", "expires"):
        if not record.get(k):
            return f"the enrollment record has no {k}"
    if not of_grants.verify(record, keys):
        return "the enrollment record does not verify against the pinned grant public key"
    try:
        if (at if at is not None else time.time()) > _epoch(record["expires"]):
            return f"the passkey's enrollment expired ({record['expires']})"
    except (ValueError, OverflowError):
        return "the enrollment's expiry is not a time"
    if server and _origin(server) != record["origin"]:
        return f"the passkey is enrolled on {record['origin']!r}, not on the organization's of-server {_origin(server)!r}"
    return None


def assertion_problem(approval, record, expected_challenge, last_counter=0):
    """Why the WebAuthn assertion does not verify against the enrolled key, or None. `last_counter`: the highest
    signature counter this credential gave before."""
    try:
        auth = unb64u(approval["authenticator_data"])
        cdj = unb64u(approval["client_data_json"])
        sig = unb64u(approval["signature"])
        client = json.loads(cdj)
    except (KeyError, TypeError, ValueError):
        return "the assertion is not readable (authenticator_data, client_data_json, signature)"
    if client.get("type") != "webauthn.get":
        return f"the client data is of type {client.get('type')!r}, not webauthn.get"
    if client.get("challenge") != b64u(expected_challenge):
        return "the signed challenge is not this approval's preimage"
    if client.get("origin") != record["origin"]:
        return f"signed on {client.get('origin')!r}, not on the enrolled origin {record['origin']!r}"
    if len(auth) < 37 or auth[:32] != hashlib.sha256(str(record["rp_id"]).encode()).digest():
        return "the authenticator data names another relying party"
    if not auth[32] & FLAG_UP or not auth[32] & FLAG_UV:
        return "the passkey did not verify the person (no user presence or verification)"
    n = int.from_bytes(auth[33:37], "big")
    if n and n <= (last_counter or 0):
        return f"the signature counter {n} is not above {last_counter}, which this passkey gave before (a cloned key?)"
    if not es256_verify(record["public_key"], auth + hashlib.sha256(cdj).digest(), sig):
        return "the signature does not verify against the enrolled passkey"
    return None


def approval_problem(approval, diff_id, org, digest, keys, recorded_at=None, approvers=(), server=None,
                     last_counter=0):
    """Why a passkey approval does not approve this diff in its current form, or None. `recorded_at`: the approval
    event's time (ISO); `approvers`: the people this diff needs; `server`: the organization's of-server address;
    `last_counter`: the highest signature counter its credential gave in earlier approvals."""
    import of_grants
    if approval.get("digest") != digest:
        return "the approval is for another form of the diff (its digest changed since)"
    try:
        org_, id_, digest_, nonce, expires = json.loads(approval.get("preimage") or "")
    except (ValueError, TypeError):
        return "the approval carries no readable preimage [organization, diff id, digest, nonce, expiry]"
    if (org_, id_, digest_) != (org, diff_id, digest) or not nonce:
        return "the preimage names another organization, diff or digest"
    try:
        at = _epoch(recorded_at) if recorded_at else time.time()
        if at > _epoch(expires):
            return f"the approval was recorded after its challenge expired ({expires})"
    except (ValueError, OverflowError, TypeError):
        return "the preimage's expiry is not a time"
    record = approval.get("enrollment")
    why = enrollment_problem(record, org, keys, at, server)
    if why:
        return why
    if approval.get("credential") != record["credential"]:
        return "the approval names another credential than the enrolled one"
    if record["person"] not in set(approvers):
        return (f"the passkey is enrolled for {record['person']!r}, who is not an approver this diff needs "
                f"({', '.join(sorted(approvers)) or 'none known'})")
    revoked = getattr(of_grants, "passkey_revoked", None)  # of_server's reader of the signed revocations (§6, step 6)
    if revoked is None:
        return "passkey approvals wait for revocation to exist (of_grants.passkey_revoked, of_server): none could be taken back"
    if revoked(org, record["credential"]):
        return f"the passkey {record['credential']!r} is revoked"
    return assertion_problem(approval, record, hashlib.sha256(approval["preimage"].encode()).digest(), last_counter)


def verified_counter(approval, org, keys):
    """The signature counter of an earlier approval, counted only when that approval's enrollment and signature
    verify: a forged event with a high counter must not block the phone (of_review on PR 64)."""
    record = approval.get("enrollment")
    if enrollment_problem(record, org, keys, at=0) is not None:
        return 0
    try:
        pre = hashlib.sha256(str(approval.get("preimage") or "").encode()).digest()
    except (TypeError, ValueError):
        return 0
    return (counter(approval) or 0) if assertion_problem(approval, record, pre) is None else 0
