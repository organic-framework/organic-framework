"""The check every keeper runs before it serves a line of its list (docs/access.md §4; step 3 of §6, hand-over 64ece894
from of_architect). Owned by of_server.

A keeper (a vault, signer or browser keeper; docs/access.md §1) calls `line_problem` for each line it is about to serve,
with what it read from the committed tip of its organization's default branch (its list, the approvals beside it,
`registry/grants.yaml`) and what it keeps in its own root-owned folder, which no agent can write:
- `pins/grant.pub`: the organization's grant public key, pinned by the host's setup script the owner runs once;
- `pins/passkeys/*.json`: the owner's passkey enrollment records, pinned the same way;
- `revocations.jsonl`: every grant revocation it has seen, append-only (RevocationLog).

What it checks, by the line's tier (docs/access.md §3):
- every line: its shape and the kind's floor (of_access.entry_problems), and that it has not expired;
- secret and up: the passkey record named by `approval: passkey:<id>`, at `registry/access/approvals/<id>.json`,
  approves this exact line: of_passkey.approval_problem, the checks `of diff apply` runs, with the line in the diff's
  place (line_payload); the enrollment is one of the pinned ones and verifies against the pinned grant key;
- money and production: the grant the line names verifies against the pinned key, is the tenant's allow for this
  position with `scope: {keepers: ["<keeper>:<access>"], limit: …}` and an expiry, bounds the line's limit in every
  part (of_access.limit_within), has not expired, and is not revoked by any source the keeper has seen. When the
  keeper cannot read its sources, it serves no money or production line.
Returns None when the line may be served, else the reason, which the keeper tells the structure keeper. It never sees
a value: the line names an item, a destination or a site.
"""
import json
import os
import subprocess
import time

APPROVALS = "approvals"
MONEY = ("money", "production")


def line_id(keeper, position, access):
    return f"access:{keeper}:{position}:{access}"


def _norm(v):
    import datetime
    if isinstance(v, datetime.datetime):
        v = v if v.tzinfo else v.replace(tzinfo=datetime.timezone.utc)
        return v.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, dict):
        return {str(k): _norm(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_norm(x) for x in v]
    return v


def line_payload(org, keeper, entry):
    """What the owner's passkey signs for a line: the line without its approval, grant and added fields (docs/access.md
    §4), with its organization, keeper and id, in the shape `of diff link` sends (`diff` with `id` and `org`), so the
    approval page and of_passkey's checks serve it as they serve a diff."""
    line = {k: _norm(v) for k, v in (entry or {}).items() if k not in ("approval", "grant", "added")}
    return {"id": line_id(keeper, entry.get("position"), entry.get("access")), "org": org, "keeper": keeper,
            "line": line}


def line_digest(payload):
    import of_diff
    return of_diff.digest(payload)


def approval_record(root, approval):
    """The passkey record `approval: passkey:<id>` names, from registry/access/approvals/<id>.json, or None."""
    if not (isinstance(approval, str) and approval.startswith("passkey:")):
        return None
    rid = approval[len("passkey:"):]
    if not rid or "/" in rid or rid.startswith("."):
        return None
    try:
        with open(os.path.join(root, "registry", "access", APPROVALS, f"{rid}.json")) as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def load_pins(folder):
    """(keys, {credential: person}) from the keeper's pinned folder; the enrollments counted are those that verify
    against the pinned grant key."""
    import of_grants
    try:
        with open(os.path.join(folder, "grant.pub")) as fh:
            keys = of_grants.Keys(ed25519=[ln for ln in fh.read().splitlines() if ln.strip()])
    except OSError:
        keys = of_grants.Keys()
    creds = {}
    d = os.path.join(folder, "passkeys")
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else ():
        try:
            with open(os.path.join(d, fn)) as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("credential") and of_grants.verify(rec, keys):
            creds[str(rec["credential"])] = str(rec.get("person") or "")
    return keys, creds


def approval_problem(org, keeper, entry, record, keys, pinned, server=None):
    """Why RECORD does not approve this line, or None. PINNED: {credential: person} of the pinned enrollments; the
    people who approve are theirs."""
    import of_passkey
    if not isinstance(record, dict):
        return "the line names no passkey record beside the list (registry/access/approvals/<id>.json)"
    cred = str(record.get("credential") or "")
    if cred not in pinned:
        return "the approval's passkey is not one this keeper pins"
    payload = line_payload(org, keeper, entry)
    return of_passkey.approval_problem(record, payload["id"], org, line_digest(payload), keys,
                                       recorded_at=record.get("recorded_at"), approvers=set(pinned.values()),
                                       server=server, last_counter=0)


def grant_problem(grant, keeper, entry, keys, now_iso, revoked=()):
    """Why GRANT does not bound this money or production line, or None."""
    import of_access
    import of_grants
    if not isinstance(grant, dict):
        return f"grant {entry.get('grant')} is not in the committed registry/grants.yaml"
    gid = str(grant.get("id") or "")
    if gid in set(revoked):
        return f"grant {gid} is revoked (the keeper's revocation log)"
    ok, why = of_grants.valid(grant, keys, now_iso)
    if not ok:
        return f"grant {gid}: {why}"
    if grant.get("issuer") != "tenant" or (grant.get("effect") or "allow") != "allow":
        return f"grant {gid} is not the tenant's allow"
    if grant.get("subject") != entry.get("position"):
        return f"grant {gid} is for {grant.get('subject')}, not {entry.get('position')}"
    scope = grant.get("scope") if isinstance(grant.get("scope"), dict) else {}
    if scope.get("keepers") != [f"{keeper}:{entry.get('access')}"]:
        return f"grant {gid}'s scope does not name {keeper}:{entry.get('access')} alone"
    if not grant.get("expires"):
        return f"grant {gid} has no expiry"
    return of_access.limit_within(entry.get("limit"), scope.get("limit"))


def line_problem(org, keeper, kind, entry, *, root, positions, keys, pinned, grants, now_iso=None, revoked=(),
                 sources_ok=True, server=None):
    """Why this line must not be served now, or None (the module's docstring)."""
    import of_access
    now_iso = now_iso or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if not isinstance(entry, dict):
        return "the line is not a mapping"
    shape = of_access.entry_problems(kind, entry, positions)
    if shape:
        return "; ".join(shape)
    if entry.get("expires") and _norm(entry["expires"]) <= now_iso:
        return f"the line expired ({_norm(entry['expires'])})"
    tier = entry.get("tier")
    if tier in MONEY and not sources_ok:
        return "the keeper cannot read its sources: it serves no money or production line"
    if of_access.RANK.get(tier, 0) >= of_access.RANK["secret"]:
        why = approval_problem(org, keeper, entry, approval_record(root, entry.get("approval")), keys, pinned, server)
        if why:
            return f"approval: {why}"
    if tier in MONEY:
        g = next((x for x in grants or [] if isinstance(x, dict) and x.get("id") == entry.get("grant")), None)
        why = grant_problem(g, keeper, entry, keys, now_iso, revoked)
        if why:
            return why
    return None


class RevocationLog:
    """Every grant revocation the keeper has seen, kept in its own folder and never taken back (docs/access.md §4,
    "Revocation fails closed"): a signed `revoked` in the grants file, an `of.grant_revoked` event signed or not, and
    a grant it saw before that is gone from the committed file. Restoring an older grants file brings nothing back."""

    def __init__(self, path):
        self.path = path

    def _read(self):
        seen, revoked = set(), set()
        try:
            with open(self.path) as fh:
                for ln in fh:
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    (revoked if r.get("revoked") else seen).add(str(r.get("grant")))
        except OSError:
            pass
        return seen, revoked

    def revoked(self):
        return self._read()[1]

    def observe(self, grants, revoked_events=(), now=None):
        """Record what this read shows; returns every revoked grant id."""
        seen, revoked = self._read()
        now = now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        ids = {str(g.get("id")) for g in grants or [] if isinstance(g, dict) and g.get("id")}
        new = []
        for g in grants or []:
            if isinstance(g, dict) and g.get("id") and str(g["id"]) not in seen:
                new.append({"grant": str(g["id"]), "at": now})
            if isinstance(g, dict) and g.get("revoked") and str(g.get("id")) not in revoked:
                new.append({"grant": str(g["id"]), "revoked": True, "at": now, "source": "grants file"})
                revoked.add(str(g["id"]))
        for gid in sorted(seen - ids - revoked):
            new.append({"grant": gid, "revoked": True, "at": now, "source": "gone from the committed file"})
            revoked.add(gid)
        for gid in sorted({str(x) for x in revoked_events} - revoked):
            new.append({"grant": gid, "revoked": True, "at": now, "source": "of.grant_revoked"})
            revoked.add(gid)
        if new:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a") as fh:
                for r in new:
                    fh.write(json.dumps(r, sort_keys=True) + "\n")
        return revoked


def descends(root, last, new, run=subprocess.run):
    """True when commit NEW descends from LAST (or LAST is None): a keeper reads its sources only forward."""
    if not last:
        return True
    try:
        r = run(["git", "-c", "core.fsmonitor=false", "-C", root, "merge-base", "--is-ancestor", last, new],
                capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0
