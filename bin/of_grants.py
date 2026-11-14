"""Grants: signature, validity, attenuation and matching (docs/registry.md, Grants v0).

Pure functions over grant dicts as they appear in registry/grants.yaml; the guard and
`of registry check` build on them.
"""

import fnmatch
import hashlib
import hmac
import json
import os

TIERS = ("observe", "propose", "act_with_approval", "act_and_report")  # least to most autonomous
DIMENSIONS = ("tools", "repos", "paths", "hosts", "systems", "commands", "to_org", "to_positions")
MASTER_KEY = os.path.expanduser("~/.config/of/tenant.key")


# ------------------------------------------------------------- signature

def purpose_key(label, master=None):
    if master is None:
        with open(MASTER_KEY, "rb") as fh:
            master = fh.read().strip()
    return hmac.new(master, label.encode(), hashlib.sha256).digest()


def canonical(grant):
    return json.dumps({k: v for k, v in grant.items() if k != "signature"}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False, default=str).encode()


GRANT_KEY = os.path.expanduser("~/.config/of/grant_ed25519")
# The grant key in the owner's 1Password (the founder, ask 0fd223f2, 2026-11-11): its private half never touches a
# file; `of grant` signs through 1Password's SSH agent, which asks for Touch ID. The pinned public key stays at
# GRANT_KEY + ".pub", which every verifier on this machine reads; STAGED_PIN is where the owner saves the new public
# key before `of grant rekey` moves every grant to it.
STAGED_PIN = os.path.expanduser("~/.config/of/grant_1password.pub")
AGENT_SOCK = "~/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock"
NAMESPACE = "of-grant"
ED = "ssh-ed25519:"


def no_agent_env():
    """The environment for ssh-keygen: never the SSH agent (the founder's is 1Password), only
    the explicit key file."""
    env = {k: v for k, v in os.environ.items() if k != "SSH_AUTH_SOCK"}
    env["SSH_AUTH_SOCK"] = ""
    env["OF_AUTOMATION"] = "1"
    return env


class Keys:
    """What a verifier trusts. `ed25519`: the tenant's pinned grant public keys (OpenSSH lines);
    a server pins them in server.yaml `trusted_grant_keys`, never takes them from a registry.
    `hmac`: the legacy HMAC key (purpose `of/grant/v1`), or None where the verifier must not
    hold it: whoever can verify an HMAC signature can also forge one."""

    def __init__(self, ed25519=(), hmac_key=None):
        self.ed25519 = tuple(k.strip() for k in ed25519 if k and k.strip())
        self.hmac = hmac_key


def _keys(key):
    return key if isinstance(key, Keys) else Keys(hmac_key=key)


def sign(grant, key):
    """Legacy: an HMAC signature under the purpose key `of/grant/v1`."""
    return "hmac-sha256:" + hmac.new(key, canonical(grant), hashlib.sha256).hexdigest()


def pin_path():
    return GRANT_KEY + ".pub"


def agent_sock():
    return os.path.expanduser(os.environ.get("OF_GRANT_AGENT_SOCK") or AGENT_SOCK)


def owners_agent(sock):
    """Is SOCK the owner's own 1Password SSH agent (not a test's or another agent's socket)?"""
    return os.path.realpath(os.path.expanduser(sock or "")) == os.path.realpath(os.path.expanduser(AGENT_SOCK))


def refuse_outside_owners_terminal(sock, what):
    """AGENTS.md: automation never touches the owner's SSH agent. The one use is the owner's own `of grant` in his
    terminal (ask 0fd223f2); from an agent's session (CLAUDECODE, CLAUDE_CODE_SESSION_ID) or a service
    (OF_AUTOMATION=1) the owner's agent is refused here, whoever calls (of_release's review 2026-11-11, finding #10)."""
    if owners_agent(sock) and (os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_SESSION_ID")
                               or os.environ.get("OF_AUTOMATION") == "1"):
        raise PermissionError(f"{what} uses the owner's 1Password SSH agent: only in his own terminal tab, never from "
                              "an agent's session or a service")


def signs_with_agent():
    """True once the private key file is gone and the pinned key is the one in the owner's 1Password."""
    return not os.path.exists(GRANT_KEY) and os.path.exists(pin_path())


def sign_ed25519(grant, key_path=None):
    """`ssh-ed25519:<base64 of the armored SSHSIG>`, made with ssh-keygen: with the private key file while one exists,
    else through the owner's SSH agent with the pinned public key (sign_with_agent)."""
    key_path = key_path or GRANT_KEY
    if not os.path.exists(key_path) and key_path == GRANT_KEY and os.path.exists(pin_path()):
        return sign_with_agent(grant)
    return _ssh_sign(grant, key_path, no_agent_env())


def sign_with_agent(grant, pub_path=None, sock=None):
    """Sign through the owner's SSH agent: `ssh-keygen -Y sign -f <public key>`, whose private half the agent holds
    ("may refer to … a public key with the private half available via ssh-agent", ssh-keygen(1)). 1Password asks
    for Touch ID. Only `of grant` in the owner's own terminal calls this (bin/of refuses an agent session)."""
    sock = sock or agent_sock()
    refuse_outside_owners_terminal(sock, "signing a grant")
    return _ssh_sign(grant, pub_path or pin_path(), dict(os.environ, SSH_AUTH_SOCK=sock))


def _ssh_sign(grant, key_path, env):
    import base64
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        msg = os.path.join(d, "grant")
        with open(msg, "wb") as fh:
            fh.write(canonical(grant))
        r = subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", key_path, "-n", NAMESPACE, msg],
                           capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise RuntimeError(f"ssh-keygen sign failed: {r.stderr.strip()[:200]}")
        with open(msg + ".sig", "rb") as fh:
            return ED + base64.b64encode(fh.read()).decode()


_ED_CACHE = {}


def _verify_ed25519(data, signature, pubkeys):
    import base64
    import subprocess
    import tempfile
    ck = (hashlib.sha256(data).hexdigest(), signature, pubkeys)
    if ck in _ED_CACHE:
        return _ED_CACHE[ck]
    ok = False
    try:
        sig = base64.b64decode(signature[len(ED):], validate=True)
    except ValueError:
        sig = None
    if sig and pubkeys:
        with tempfile.TemporaryDirectory() as d:
            allowed, sigf = os.path.join(d, "allowed"), os.path.join(d, "sig")
            with open(allowed, "w") as fh:
                for k in pubkeys:
                    fh.write(f'tenant namespaces="{NAMESPACE}" {k}\n')
            with open(sigf, "wb") as fh:
                fh.write(sig)
            r = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", allowed, "-I", "tenant", "-n", NAMESPACE,
                                "-s", sigf], input=data, capture_output=True,
                               env=no_agent_env())
            ok = r.returncode == 0
    _ED_CACHE[ck] = ok
    return ok


def verify(grant, key):
    keys = _keys(key)
    sig = str(grant.get("signature") or "")
    if sig.startswith(ED):
        return _verify_ed25519(canonical(grant), sig, keys.ed25519)
    if sig.startswith("hmac-sha256:") and keys.hmac:
        return hmac.compare_digest(sig, sign(grant, keys.hmac))
    return False


def valid(grant, key, now_iso, uses=0):
    """Return (ok, reason)."""
    if not verify(grant, key):
        return False, "bad or missing signature"
    if grant.get("revoked"):
        return False, "revoked"
    if grant.get("expires") and str(grant["expires"]) <= now_iso:
        return False, "expired"
    if grant.get("counter") is not None and uses >= int(grant["counter"]):
        return False, "counter exhausted"
    if grant.get("tier") not in TIERS:
        return False, f"unknown tier {grant.get('tier')!r}"
    return True, "ok"


# ------------------------------------------------------------ attenuation

def _covered(pattern, by):
    """Is every value that `pattern` admits also admitted by `by`? (v0: equal, or a narrower prefix.)"""
    if pattern == by or by in ("*", "**"):
        return True
    stem = by.rstrip("*")
    return by.endswith("*") and pattern.startswith(stem) and len(pattern.rstrip("*")) >= len(stem)


def _as_list(v):
    if v is None:
        return None
    return v if isinstance(v, list) else [v]


def attenuates(derived, issuer):
    """Return a list of violations; empty means `derived` stays within `issuer` (attenuation only)."""
    out = []
    ds, iss = derived.get("scope") or {}, issuer.get("scope") or {}
    for dim in DIMENSIONS:
        allowed = _as_list(iss.get(dim))
        if allowed is None:          # the issuer is unrestricted in this dimension
            continue
        asked = _as_list(ds.get(dim))
        if asked is None:
            out.append(f"{dim}: unrestricted, but the issuer limits it to {allowed}")
            continue
        for p in asked:
            if not any(_covered(str(p), str(a)) for a in allowed):
                out.append(f"{dim}: {p!r} is outside the issuer's {allowed}")
    if TIERS.index(derived.get("tier", "observe")) > TIERS.index(issuer.get("tier", "observe")):
        out.append(f"tier {derived.get('tier')} is above the issuer's {issuer.get('tier')}")
    risk = ("low", "medium", "high")
    ri, rd = issuer.get("risk_appetite"), derived.get("risk_appetite")
    for r_ in (ri, rd):
        if r_ is not None and r_ not in risk:
            out.append(f"risk_appetite {r_!r} is not one of low, medium, high")
    if ri in risk and (rd is None or (rd in risk and risk.index(rd) > risk.index(ri))):
        out.append(f"risk_appetite {rd or 'unset'} is above the issuer's {ri}")
    if issuer.get("expires") and (not derived.get("expires") or str(derived["expires"]) > str(issuer["expires"])):
        out.append("expires later than the issuer's grant")
    rights = (iss.get("grants") or {}) if isinstance(iss.get("grants"), dict) else {}
    if not rights:
        out.append("the issuer holds no right to issue grants (scope `grants`)")
    elif (ds.get("grants") or None) and int(rights.get("depth", 1)) <= 1:
        out.append("the issue right may not be delegated further (depth 1)")
    elif isinstance(ds.get("grants"), dict) and int(ds["grants"].get("depth", 1)) >= int(rights.get("depth", 1)):
        out.append("a delegated issue right must have a smaller depth than the issuer's")
    return out


# --------------------------------------------------------------- matching

def _match_len(dim_patterns, value):
    """Length of the longest literal stem among patterns that match `value`; None if none match."""
    best = None
    for p in dim_patterns:
        if fnmatch.fnmatchcase(str(value), str(p)) or str(p) == "**":
            stem = len(str(p).split("*")[0])
            best = stem if best is None else max(best, stem)
    return best


def specificity(grant, action):
    """None if the grant does not apply to the action; else how specific its match is."""
    total = 0
    for dim, value in action.items():
        pats = _as_list((grant.get("scope") or {}).get(dim))
        if pats is None:
            continue                 # unrestricted: matches, adds nothing
        n = _match_len(pats, value)
        if n is None:
            return None
        total += n
    return total


def _rank(grant):
    """Restrictiveness: a deny is the most restrictive, then the lower tiers."""
    return -1 if grant.get("effect") == "deny" else TIERS.index(grant.get("tier", "observe"))


def decide(grants, action):
    """Pick the deciding grant for an action, or None (missing grant).

    1. A critical deny that applies always wins.
    2. Otherwise the most specific applicable grant wins (longest match, as in routing).
    3. Between equally specific grants, the more restrictive wins.
    """
    applicable = [(specificity(g, action), g) for g in grants]
    applicable = [(s, g) for s, g in applicable if s is not None]
    if not applicable:
        return None
    critical = [g for s, g in applicable if g.get("effect") == "deny" and g.get("critical")]
    if critical:
        return critical[0]
    top = max(s for s, _ in applicable)
    return min((g for s, g in applicable if s == top), key=_rank)


# ------------------------------------------------------------ store, chain

def load(orgs_file=None):
    """All grants of the organizations listed in orgs.yaml: {org: [grant]}."""
    import yaml
    orgs_file = orgs_file or os.environ.get("OF_ORGS") or os.path.expanduser("~/.config/of/orgs.yaml")
    with open(orgs_file) as fh:
        doc = yaml.safe_load(fh) or {}
    base = os.path.dirname(os.path.abspath(orgs_file))
    out = {}
    for o in doc.get("orgs") or []:
        path = os.path.join(base, os.path.expanduser(str(o["path"])), "registry", "grants.yaml")
        if os.path.exists(path):
            with open(path) as fh:
                out[o["name"]] = (yaml.safe_load(fh) or {}).get("grants") or []
        else:
            out[o["name"]] = []
    return out


def grants_path(org, orgs_file=None):
    import yaml
    orgs_file = orgs_file or os.environ.get("OF_ORGS") or os.path.expanduser("~/.config/of/orgs.yaml")
    with open(orgs_file) as fh:
        doc = yaml.safe_load(fh) or {}
    base = os.path.dirname(os.path.abspath(orgs_file))
    for o in doc.get("orgs") or []:
        if o["name"] == org:
            return os.path.join(base, os.path.expanduser(str(o["path"])), "registry", "grants.yaml")
    raise KeyError(f"organization {org!r} is not in orgs.yaml")


def verify_chain(grant, by_subject, key, now_iso, depth=0):
    """A grant is authority only if it is valid and, when a position issued it, it attenuates
    a valid issue-right grant of that position, recursively back to the tenant."""
    ok, why = valid(grant, key, now_iso)
    if not ok:
        return False, why
    issuer = grant.get("issuer")
    if issuer == "tenant":
        return True, "ok"
    if depth > 8:
        return False, "delegation chain too long"
    last = "the issuer holds no valid issue right"
    for parent in by_subject.get(issuer, []):
        if not isinstance((parent.get("scope") or {}).get("grants"), dict):
            continue
        if attenuates(grant, parent):
            last = "outside the issuer's grant: " + "; ".join(attenuates(grant, parent))
            continue
        ok, why = verify_chain(parent, by_subject, key, now_iso, depth + 1)
        if ok:
            return True, "ok"
        last = f"issuer's grant {parent.get('id')}: {why}"
    return False, last


def ended(why):
    """Is a check's reason a grant's own normal end rather than a fault? Expiry is: the grant did what it was issued
    for, and `of registry check` only says it may leave the file."""
    return why == "expired"


def check_all(grants_by_org, key, now_iso):
    """[(org, id, ok, reason)] for every grant: what `of registry check` reports."""
    by_subject = {}
    for gs in grants_by_org.values():
        for g in gs:
            by_subject.setdefault(g.get("subject"), []).append(g)
    out = []
    for org, gs in grants_by_org.items():
        seen = set()
        for g in gs:
            if g.get("id") in seen:
                out.append((org, g.get("id"), False, "duplicate id"))
                continue
            seen.add(g.get("id"))
            ok, why = verify_chain(g, by_subject, key, now_iso)
            shape = shape_problems(g)
            if shape:
                ok, why = False, "; ".join(shape)
            out.append((org, g.get("id"), ok, why))
    return out


def verify_request(grants_by_org, key, now_iso, grant_id, subject, action=None):
    """The of-server /v1/grants/verify answer."""
    by_subject = {}
    for gs in grants_by_org.values():
        for g in gs:
            by_subject.setdefault(g.get("subject"), []).append(g)
    org, _, position = (subject or "").rpartition(":")
    pool = grants_by_org.get(org, []) if org else [g for gs in grants_by_org.values() for g in gs]
    g = next((x for x in pool if x.get("id") == grant_id), None)
    if g is None:
        return {"valid": False, "reason": "unknown grant", "decision": "escalate"}
    if g.get("subject") != position:
        return {"valid": False, "reason": f"grant is for {g.get('subject')}, not {position}", "decision": "escalate"}
    ok, why = verify_chain(g, by_subject, key, now_iso)
    out = {"valid": ok, "reason": why, "tier": g.get("tier"), "grant_id": grant_id}
    if not ok:
        out["decision"] = "escalate"
    elif action:
        usable = [x for x in by_subject.get(position, []) if verify_chain(x, by_subject, key, now_iso)[0]]
        d = decide(usable, action)
        out["deciding_grant"] = d.get("id") if d else None
        out["decision"] = "escalate" if d is None else ("deny" if d.get("effect") == "deny" else "admit")
    else:
        out["decision"] = "admit"
    return out


# ------------------------------------------------- organization rules (deny grants; docs/registry.md)

RULE_KEYS = ("verbs", "pods", "namespaces", "admin", "allow_exec")
ADMIN_KEYS = ("configs", "contexts", "users")
GUARD_STATE = "guard-grants.json"


def applies_to(grant, position):
    """Is `position` (None: a session without a position) a subject of this grant? `subject: "*"` covers every
    position of the organization except those in `except`, and its sessions without a position."""
    if grant.get("subject") == "*":
        return position is None or position not in (grant.get("except") or [])
    return position is not None and grant.get("subject") == position


def _str_list(v):
    return isinstance(v, list) and bool(v) and all(isinstance(x, str) and x for x in v)


def shape_problems(grant):
    """What makes a grant malformed beyond its signature (docs/registry.md, Organization rules): `subject: "*"`
    only on a deny the tenant issued, `except` only with it, a `kubectl` scope of the O1 matcher's keys, and a
    `contacts` scope of exactly one contact on an allow the tenant issued to one position (docs/guard.md G16)."""
    out = []
    sub, exc = grant.get("subject"), grant.get("except")
    scope = grant.get("scope") or {}
    if sub == "*":
        if grant.get("effect") != "deny":
            out.append('subject "*" is only for a deny')
        if grant.get("issuer") != "tenant":
            out.append('subject "*" is issued by the tenant only')
        if not isinstance(scope.get("kubectl"), dict):
            out.append('a subject "*" deny needs a scope the guard applies (kubectl)')
    if exc is not None and sub != "*":
        out.append('except is only for subject "*"')
    if exc is not None and not (isinstance(exc, list) and all(isinstance(x, str) and x for x in exc)):
        out.append("except is a list of position names")
    c = scope.get("contacts")
    if c is not None:
        if not (_str_list(c) and len(c) == 1):
            out.append("scope contacts names exactly one contact: an e-mail address or a host")
        if grant.get("effect", "allow") != "allow" or sub == "*" or grant.get("issuer") != "tenant":
            out.append("scope contacts is only on an allow the tenant issues to one position")
    k = scope.get("kubectl")
    if k is not None:
        if grant.get("effect") != "deny":
            out.append("scope kubectl is only for a deny")
        if not isinstance(k, dict) or not k or set(k) - set(RULE_KEYS):
            out.append(f"scope kubectl takes only {', '.join(RULE_KEYS)}")
        else:
            for key in ("verbs", "pods", "namespaces"):
                if key in k and not _str_list(k[key]):
                    out.append(f"scope kubectl.{key} is a list of strings")
            ax = k.get("allow_exec")
            if "allow_exec" in k and not (isinstance(ax, list) and ax and all(
                    isinstance(a, dict) and _str_list(a.get("argv")) and not set(a) - {"argv", "pods"}
                    and ("pods" not in a or _str_list(a["pods"])) for a in ax)):
                out.append("scope kubectl.allow_exec is a list of {argv: [strings], pods?: [globs]}")
            adm = k.get("admin")
            if "admin" in k and (not isinstance(adm, dict) or not adm or set(adm) - set(ADMIN_KEYS)
                                 or not all(_str_list(v) for v in adm.values())):
                out.append(f"scope kubectl.admin takes lists of strings under {', '.join(ADMIN_KEYS)}")
    return out


def org_rule(grant):
    """The guard's rule (of_guard.org_rule_hits) for a deny grant with a kubectl scope: a non-critical deny warns,
    a critical one denies; `note` is the reason shown to the agent."""
    return {"id": grant.get("id"), "mode": "deny" if grant.get("critical") else "warn",
            "reason": grant.get("note") or "", "subject": grant.get("subject"),
            "except": list(grant.get("except") or []), "expires": grant.get("expires"),
            "kubectl": (grant.get("scope") or {}).get("kubectl")}


def _is_rule(grant):
    return grant.get("effect") == "deny" and isinstance((grant.get("scope") or {}).get("kubectl"), dict) \
        and not shape_problems(grant)


def fold_kept(kept, grants, keys, now_iso):
    """The verified deny rules after reading the file, as (kept, notes). `kept` ({id: grant}) holds every deny rule
    once verified. It ends only by a signed revocation of the same id in the file, or by its signed expiry. An entry
    deleted, or edited so that its signature no longer verifies, leaves the kept grant applied, and `notes` says so
    (docs/registry.md, Organization rules: a deny ends only by a signed revocation)."""
    kept, notes, seen = dict(kept or {}), [], set()
    for g in grants or []:
        gid = g.get("id")
        if not gid or g.get("effect") != "deny":
            continue
        seen.add(gid)
        signed = verify(g, keys)
        if signed and g.get("revoked"):
            kept.pop(gid, None)
        elif signed and _is_rule(g) and valid(g, keys, now_iso)[0]:
            kept[gid] = g
        elif gid in kept and not signed:
            notes.append(f"{gid}: the entry in the file does not verify; the signed deny stays applied")
    for gid in list(kept):
        g = kept[gid]
        if g.get("expires") and str(g["expires"]) <= now_iso:
            kept.pop(gid)
        elif gid not in seen:
            notes.append(f"{gid}: missing from the file without a signed revocation; the deny stays applied")
    return kept, notes


def local_keys():
    """The pinned tenant grant key of this machine (ed25519 only: organization rules are never legacy HMAC)."""
    try:
        with open(GRANT_KEY + ".pub") as fh:
            return Keys(ed25519=[line for line in fh.read().splitlines() if line.strip()])
    except OSError:
        return Keys()


def _org_grants_paths(orgs_file):
    import yaml
    with open(orgs_file) as fh:
        doc = yaml.safe_load(fh) or {}
    base = os.path.dirname(os.path.abspath(orgs_file))
    return {o["name"]: os.path.join(base, os.path.expanduser(str(o["path"])), "registry", "grants.yaml")
            for o in doc.get("orgs") or [] if o.get("name") and o.get("path")}


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return None


def guard_rules(org, state_dir, orgs_file=None, keys=None, now_iso=None):
    """(rules, notes) the guard applies for an organization: its verified deny grants with a kubectl scope, as
    of_guard rules, folded with the ones kept from earlier reads (fold_kept). Cached in state_dir/guard-grants.json
    by the mtimes of orgs.yaml, the grants file and the pinned key, so a hook call reads one JSON file; PyYAML and
    ssh-keygen run only when a file changed."""
    import time
    now_iso = now_iso or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    orgs_file = orgs_file or os.environ.get("OF_ORGS") or os.path.expanduser("~/.config/of/orgs.yaml")
    state_path = os.path.join(state_dir, GUARD_STATE)
    try:
        with open(state_path) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    changed = False
    if state.get("orgs_mtime") != _mtime(orgs_file) or "paths" not in state:
        state["paths"], state["orgs_mtime"], changed = _org_grants_paths(orgs_file), _mtime(orgs_file), True
    path = state["paths"].get(org)
    if not path:
        return [], []
    stamp = [_mtime(path), _mtime(GRANT_KEY + ".pub") if keys is None else "given"]
    ent = (state.get("orgs") or {}).get(org) or {}
    if ent.get("stamp") != stamp:
        try:
            import yaml
            with open(path) as fh:
                grants = (yaml.safe_load(fh) or {}).get("grants") or []
        except FileNotFoundError:
            grants = []
        except Exception:  # noqa: BLE001 - an unreadable file lifts no kept deny
            grants = None
        if grants is None:
            kept, notes = dict(ent.get("kept") or {}), ["the grants file could not be read; the kept denies stay applied"]
        else:
            kept, notes = fold_kept(ent.get("kept"), grants, keys or local_keys(), now_iso)
        state.setdefault("orgs", {})[org] = ent = {"stamp": stamp, "kept": kept, "notes": notes}
        changed = True
    if changed:
        tmp = f"{state_path}.{os.getpid()}"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, state_path)
    rules = [org_rule(g) for g in ent["kept"].values() if not (g.get("expires") and str(g["expires"]) <= now_iso)]
    return rules, list(ent.get("notes") or [])


def contact_grants(org, position, orgs_file=None, keys=None, now_iso=None):
    """{contact: grant id} that the tenant allowed this position to reach at a regulator or an authority
    (docs/guard.md G16): its valid allow grants, signed with the pinned key, whose scope names exactly one contact.
    Read only when a call would make such contact; an unreadable file allows nothing."""
    import time
    if not position or not org:
        return {}
    now_iso = now_iso or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        import yaml
        path = _org_grants_paths(orgs_file or os.environ.get("OF_ORGS")
                                 or os.path.expanduser("~/.config/of/orgs.yaml")).get(org)
        with open(path) as fh:
            grants = (yaml.safe_load(fh) or {}).get("grants") or []
    except Exception:  # noqa: BLE001 - no file, no grant
        return {}
    keys, out = keys or local_keys(), {}
    for g in grants:
        c = (g.get("scope") or {}).get("contacts") if isinstance(g, dict) else None
        if c is None or shape_problems(g) or g.get("subject") != position:
            continue
        if valid(g, keys, now_iso)[0]:
            out[c[0].strip().lower()] = g.get("id")
    return out


PASSKEY_STATE = "passkey-revoked.json"
PASSKEY_REVOCATION = "passkey_revocation"


def passkey_revoked(org, credential, orgs_file=None, keys=None, state_dir=None):
    """True when the owner revoked this passkey credential for the organization (docs/remote.md §6; of diff apply
    refuses its approvals). A revocation is a record in the organization's grants file, under `passkey_revocations:`,
    {kind: passkey_revocation, org, credential, issued_at, signature}, signed with the tenant's grant key over
    canonical() like a grant. A verified one is kept in state_dir/passkey-revoked.json (mode 600), the way O2 keeps a
    verified deny: deleting or editing the entry, or a grants file that cannot be read, never brings the key back."""
    if not org or not credential:
        return False
    if state_dir is None:
        import of_events
        state_dir = of_events.STATE_DIR
    state_path = os.path.join(state_dir, PASSKEY_STATE)
    try:
        with open(state_path) as fh:
            kept = json.load(fh) or {}
    except (OSError, ValueError):
        kept = {}
    mine = dict(kept.get(org) or {})
    try:
        import yaml
        path = _org_grants_paths(orgs_file or os.environ.get("OF_ORGS")
                                 or os.path.expanduser("~/.config/of/orgs.yaml")).get(org)
        with open(path) as fh:
            records = (yaml.safe_load(fh) or {}).get("passkey_revocations") or []
    except Exception:  # noqa: BLE001 - no file or a broken one: the kept revocations still stand
        records = []
    keys, added = keys or local_keys(), False
    for r in records if isinstance(records, list) else []:
        if isinstance(r, dict) and r.get("kind") == PASSKEY_REVOCATION and r.get("org") == org \
                and isinstance(r.get("credential"), str) and r["credential"] not in mine and verify(r, keys):
            mine[r["credential"]] = str(r.get("issued_at") or "")
            added = True
    if added:
        kept[org] = mine
        try:
            os.makedirs(state_dir, exist_ok=True)
            tmp = f"{state_path}.{os.getpid()}"
            with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
                json.dump(kept, fh)
            os.replace(tmp, state_path)
        except OSError:
            pass  # not kept this time; the signed record still answers while it is in the file
    return credential in mine


GRANTS_HEADER = "# Signed grants (docs/registry.md, Grants). Edit only with `of grant`; a hand edit breaks the signature.\n"


def rekey(staged=None, orgs_file=None, sock=None):
    """Move every grant to the key in the owner's 1Password (ask 0fd223f2): check that his SSH agent offers the staged
    public key, re-sign every grant of every listed organization with it and check each signature, and only then
    write the files, pin the new key in place of the old and remove the old private key file. Returns
    ([changed grants files], number of grants, the new key's line). Nothing is written when any step fails."""
    import subprocess
    import yaml
    staged = staged or STAGED_PIN
    sock = sock or agent_sock()
    refuse_outside_owners_terminal(sock, "of grant rekey")
    with open(staged) as fh:
        line = next((x.strip() for x in fh if x.strip()), "")
    parts = line.split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise ValueError(f"{staged} must hold one ssh-ed25519 public key line")
    listed = subprocess.run(["ssh-add", "-L"], env=dict(os.environ, SSH_AUTH_SOCK=sock), capture_output=True,
                            text=True)
    if parts[1] not in listed.stdout:
        raise RuntimeError("1Password's SSH agent does not offer this key: turn on its SSH agent (Settings, Developer) "
                           "and keep the key in a vault the agent serves")
    keys, docs, n = Keys(ed25519=[" ".join(parts[:2])]), {}, 0
    for org, path in sorted(_org_grants_paths(orgs_file or os.environ.get("OF_ORGS")
                                              or os.path.expanduser("~/.config/of/orgs.yaml")).items()):
        if not os.path.exists(path):
            continue
        with open(path) as fh:
            doc = yaml.safe_load(fh) or {}
        for g in (doc.get("grants") or []) + (doc.get("passkey_revocations") or []):
            g.pop("signature", None)
            g["signature"] = sign_with_agent(g, staged, sock)
            if not verify(g, keys):
                raise RuntimeError(f"{org}: {g.get('id') or g.get('credential')}: the new signature does not verify")
            n += 1
        docs[path] = doc
    for path, doc in docs.items():
        tmp = f"{path}.{os.getpid()}"
        with open(tmp, "w") as fh:
            fh.write(GRANTS_HEADER)
            yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True)
        os.replace(tmp, path)
    tmp = f"{pin_path()}.{os.getpid()}"
    with open(tmp, "w") as fh:
        fh.write(line + "\n")
    os.replace(tmp, pin_path())
    for old in (GRANT_KEY, staged):
        try:
            os.remove(old)
        except OSError:
            pass
    return sorted(docs), n, line


def missing_issued(grants_by_org, events, now_iso=None):
    """[(org, grant id)] issued (of.grant_issued) and never revoked (of.grant_revoked) that are missing from the
    organization's file: `of registry check` reports them. `events` are (name, org, payload) tuples. A grant whose
    `expires` has passed (NOW_ISO) ended by itself and may leave the file (a user organization, 2026-11-14: 13
    time-boxed grants expired, and taking them out of the file failed the check)."""
    issued, revoked = {}, set()
    for name, org, payload in events:
        gid = (payload or {}).get("grant_id")
        if not gid:
            continue
        if name == "of.grant_issued":
            ends = (payload or {}).get("expires")
            if now_iso and ends and str(ends) <= now_iso:
                issued.pop((org, gid), None)  # ended at its own expiry: a normal end
                continue
            issued[(org, gid)] = True
        elif name == "of.grant_revoked":
            revoked.add((org, gid))
    have = {(org, g.get("id")) for org, gs in grants_by_org.items() for g in gs or []}
    return sorted(k for k in issued if k not in revoked and k not in have)
