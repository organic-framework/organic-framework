"""The owner's terminal side of a passkey (docs/remote.md §6): `of passkey enroll <person>` and `of passkey revoke`.

Both sign with the organization's grant key, so docs/guard.md G13 denies them to agents' sessions, and bin/of runs
them only in the owner's own terminal. `enroll` refuses while the grant key's private half is a plain file: an agent
process that read that file could sign an enrollment for a software authenticator of its own, so a passkey adds
nothing until the key is behind Touch ID (`of grant rekey`; of_server's review of organic-framework PR 64).
"""

import os
import shutil
import subprocess
import sys
import time

import of_grants

ENROLLMENT_DAYS = 365


def _iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if t is None else t))


def _qr(url, out=sys.stdout):
    """A QR code of URL in the terminal when qrencode is installed; the URL itself always."""
    if shutil.which("qrencode"):
        r = subprocess.run(["qrencode", "-t", "ANSIUTF8", url], capture_output=True, text=True)
        if r.returncode == 0:
            out.write(r.stdout)
    out.write(url + "\n")


def enrollment_record(state, org, person, now=None):
    """The record the owner signs: the organization and the person from the owner's own command, the credential and
    key from the window's state (whose code the owner just compared)."""
    now = time.time() if now is None else now
    return {"kind": "passkey_enrollment", "org": org, "person": person,
            "credential": state["credential"], "public_key": state["public_key"], "rp_id": state["rp_id"],
            "origin": state["origin"], "issued_at": _iso(now), "expires": _iso(now + ENROLLMENT_DAYS * 86400)}


def enroll(client, org, person, ttl=600, ask=input, out=sys.stdout, poll=2.0, sign=None):
    """Open a window, show its page, wait for the phone's code, and sign only when the owner confirms the codes match.
    Returns the enrolled credential id, or None when the owner did not confirm."""
    if sign is None:
        if os.path.exists(of_grants.GRANT_KEY):
            raise PermissionError("the grant key's private half is still a plain file on this machine, which any "
                                  "process of this user can read: move it behind Touch ID first (`of grant rekey`), "
                                  "then enroll")
        sign = of_grants.sign_ed25519
    _, w = client._authed("POST", "/v1/passkey/windows", body={"org": org, "person": person, "ttl": ttl})
    out.write(f"Open this on {person}'s phone (the window takes one passkey, until {w['expires']}):\n")
    _qr(w["url"], out)
    deadline = time.time() + ttl
    while True:
        _, state = client._authed("GET", f"/v1/passkey/windows/{w['window']}")
        if state.get("state") == "tested":
            break
        if time.time() > deadline:
            out.write("The window expired before a passkey was tested. Nothing is enrolled.\n")
            return None
        time.sleep(poll)
    if (state.get("org"), state.get("person")) != (org, person):  # of_secrets-review on organic-framework PR 81
        out.write(f"of-server names {state.get('person')!r} of {state.get('org')!r} for this window, not {person!r} of "
                  f"{org!r}. Nothing is signed.\n")
        return None
    import of_server_passkey
    code = of_server_passkey.short_code(state["credential"], state["public_key"])  # computed here, never relayed
    out.write(f"\nThe passkey this terminal would sign has code  {code}\n")
    if ask("Does the phone show exactly this code? Type yes to sign the enrollment: ").strip().lower() != "yes":
        out.write("Not signed. Nothing is enrolled.\n")
        return None
    record = enrollment_record(state, org, person)
    record["signature"] = sign(record)
    client._authed("POST", "/v1/passkey/enrollments", body={"record": record})
    out.write(f"Enrolled the passkey {state['credential'][:12]}… for {person} in {org}, until {record['expires']}.\n")
    return state["credential"]


def revocation_record(org, person, credential, now=None):
    return {"kind": of_grants.PASSKEY_REVOCATION, "org": org, "person": person, "credential": credential,
            "issued_at": _iso(now)}


def revoke(org, person, credential, grants_path, client=None, sign=None, out=sys.stdout):
    """Sign a revocation, add it to the organization's grants file (machines read it there; its structure keeper
    commits it), and tell of-server, which refuses the credential at once. Returns the record."""
    import yaml
    sign = sign or of_grants.sign_ed25519
    record = revocation_record(org, person, credential)
    record["signature"] = sign(record)
    doc = {}
    if os.path.exists(grants_path):
        with open(grants_path) as fh:
            doc = yaml.safe_load(fh) or {}
    doc.setdefault("grants", doc.get("grants") or [])
    revs = doc.setdefault("passkey_revocations", [])
    if not any(isinstance(r, dict) and r.get("credential") == credential for r in revs):
        revs.append(record)
    tmp = f"{grants_path}.{os.getpid()}"
    os.makedirs(os.path.dirname(grants_path), exist_ok=True)
    with open(tmp, "w") as fh:
        fh.write(of_grants.GRANTS_HEADER)
        yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True)
    os.replace(tmp, grants_path)
    out.write(f"Revoked the passkey {credential[:12]}… of {person} in {grants_path} (its structure keeper commits it).\n")
    if client is not None:
        try:
            client._authed("POST", "/v1/passkey/revocations", body={"record": record})
            out.write("of-server refuses it from now on.\n")
        except Exception as e:  # noqa: BLE001 - the file is the record every machine reads; the server follows
            out.write(f"of-server was not told ({type(e).__name__}); machines still refuse it from the grants file.\n")
    return record


if __name__ == "__main__":  # pragma: no cover - bin/of runs these
    sys.exit("run `of passkey enroll|revoke`")
