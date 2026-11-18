"""`of access map`: who reaches what, through what, with which limit, approved how, and when each entry was last used
(docs/access.md §2). Owned by of_server.

Steps 1 and 2 of docs/access.md §6 (hand-over 64ece894 from of_architect). The map reads the credentials catalogue
(docs/registry.md, Credentials), the grants (docs/registry.md, Grants) and each keeper's list
(`registry/access/<keeper>.yaml`, docs/access.md §2) of every organization listed in orgs.yaml; the keepers'
`of.access_used` records come with step 4. Names, paths and references only: it never opens a credential file and
never reads a value.

`of access request`, `grant` and `revoke` (docs/access.md §3) are the only writers of a keeper's list; the structure
keeper commits it, and a keeper reads it from the committed tip. `check_lists` is their part of `of registry check`.

- A credential is reached by each position that lists it and by its keeper, which keeps it (docs/registry.md).
- "Approved" is the organization repository's commit that last changed the line giving that reach (the position's
  listing, or the credential's `keeper:`), with its `Position:` trailer: for the catalogue the structure keeper's
  commit is the record (docs/access.md §3). A line not committed yet reads "uncommitted".
- "Last used" comes from the secrets broker's records (`secret_uses` in the server's store) on a machine that runs the
  server, for credentials it serves by their `ref`: "none here" when that broker never served it, "no broker here" on
  a machine without one. A use of a file is not recorded today; it reads "not recorded".
- A grant shows its tier, scope, expiry, whether its signature verifies against this machine's pinned grant key, and
  its source.
"""
import datetime
import glob
import json
import os
import re
import sqlite3
import subprocess
import time

UNCOMMITTED = "0" * 40
STORES = {"op": "1Password", "keychain": "keychain", "envfile": "env file"}


class AccessError(Exception):
    pass


# ---------------------------------------------------------------- keeper lists (docs/access.md §2)

TIERS = ("plain", "secret", "money", "production")
RANK = {t: i for i, t in enumerate(TIERS)}
KINDS = {  # an entry's access form, its actions, and the kind's floor (docs/access.md §2, of_secrets-review on PR 349)
    "vault": {"access": "item:", "actions": ("read",), "floor": "secret", "limit": ()},
    "signer": {"access": "dest:", "actions": ("send",), "floor": "money", "limit": ("per_call", "per_period")},
    "browser": {"access": "site:", "actions": None, "floor": "secret", "limit": ("per_period",)},
}
KEEPER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ACTION_RE = re.compile(r"^[a-z][a-z_-]{0,31}$")
PERIOD_RE = re.compile(r"^P(?!$)(?:\d+Y)?(?:\d+M)?(?:\d+W)?(?:\d+D)?(?:T(?=\d)(?:\d+H)?(?:\d+M)?(?:\d+S)?)?$")
ISO_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?Z$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
HEADER = ("# A keeper's list (docs/access.md §2). Written only by `of access grant` and `of access revoke`; the structure\n"
          "# keeper commits it, and the keeper reads it from the committed tip of the default branch.\n")


def list_path(root, keeper):
    if not KEEPER_RE.match(str(keeper or "")):
        raise AccessError(f"a keeper's name matches {KEEPER_RE.pattern}: {keeper!r}")
    return os.path.join(root, "registry", "access", f"{keeper}.yaml")


def load_list(path):
    """The list's document, or None when there is no list; AccessError on a list that cannot be read."""
    import yaml
    if not os.path.exists(path):
        return None
    try:
        with open(path) as fh:
            doc = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as e:
        raise AccessError(f"{path}: cannot be read ({str(e).splitlines()[0]})") from None
    if not isinstance(doc, dict):
        raise AccessError(f"{path}: not a mapping")
    return doc


def _write_list(path, doc):
    import yaml
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(HEADER + body)
    os.replace(tmp, path)


def amount(v):
    """An amount as an exact Decimal, or None: an integer, a decimal string, or a finite float as written; never a
    bool, NaN or an infinity (of_secrets-review on PR 357: as floats NaN passed every bound, and 1e18 + 1 wei equalled
    1e18). Write large amounts as integers or strings: YAML reads 1.5e18 as a float, exact only as far as a float is."""
    import decimal
    if isinstance(v, bool):
        return None
    try:
        d = decimal.Decimal(v) if isinstance(v, int) else decimal.Decimal(repr(v)) if isinstance(v, float) else \
            decimal.Decimal(str(v).strip()) if isinstance(v, str) and re.fullmatch(r"\s*\d+(?:\.\d+)?\s*", v) else None
    except decimal.InvalidOperation:
        return None
    return d if d is not None and d.is_finite() else None


def _limit_problems(limit, kind):
    out = []
    need = KINDS[kind]["limit"] if kind in KINDS else ()
    if limit in (None, {}):
        return [f"a {kind} entry needs a limit with {' and '.join(need)}"] if need else []
    if not isinstance(limit, dict):
        return ["limit is a mapping: {per_call: {amount, unit}, per_period: {amount, unit, period}}"]
    for k, v in limit.items():
        if k not in ("per_call", "per_period"):
            out.append(f"limit has an unknown part {k!r} (per_call, per_period)")
            continue
        if not isinstance(v, dict):
            out.append(f"limit.{k} is a mapping with amount and unit")
            continue
        a = amount(v.get("amount"))
        if a is None or a <= 0:
            out.append(f"limit.{k}.amount is a finite number above 0: an integer or a decimal string")
        if not isinstance(v.get("unit"), str) or not v["unit"].strip():
            out.append(f"limit.{k}.unit is a word, such as ETH or calls")
        if k == "per_period" and not (isinstance(v.get("period"), str) and PERIOD_RE.match(v["period"])):
            out.append("limit.per_period.period is an ISO 8601 period, such as P1D or PT1H")
    for k in need:
        if k not in limit:
            out.append(f"a {kind} entry's limit needs {k}")
    return out


def limit_within(line, bound):
    """Why LINE's limit is not within BOUND's in every part (amount, unit, period alike), or None (docs/access.md §4:
    the grant bounds the line, never the other way). A part the line leaves open while BOUND caps it is wider."""
    bound = bound if isinstance(bound, dict) else {}
    line = line if isinstance(line, dict) else {}
    if not bound:
        return "the grant names no limit"
    for k, b in bound.items():
        v = line.get(k)
        if not isinstance(v, dict):
            return f"the line has no {k}, which the grant caps"
        if not isinstance(b, dict) or v.get("unit") != b.get("unit") or (k == "per_period" and v.get("period")
                                                                          != b.get("period")):
            return f"the line's {k} is in another unit or period than the grant's"
        la, ba = amount(v.get("amount")), amount(b.get("amount"))
        if la is None or ba is None:
            return f"the line's or the grant's {k} amount is not a finite number"
        if la > ba:
            return f"the line's {k} {v.get('amount')} {v.get('unit')} is above the grant's {b.get('amount')}"
    for k in line:
        if k not in bound:
            return f"the line's {k} is a part the grant does not cap"
    return None


def limit_shape(limit):
    """The shape problems of a limit, kind aside (a grant's `scope.limit`)."""
    return _limit_problems(limit, None) if limit not in (None, {}) else ["a limit names per_call or per_period"]


def entry_problems(kind, e, positions, grant_ids=None, resolve=None, commit_ok=None):
    """What is wrong with one entry of a list of KIND. POSITIONS: the organization's position names. GRANT_IDS: its
    grants' ids (None: not checked). RESOLVE(card) -> bool: a card id is an ask; COMMIT_OK(sha) -> bool: a commit of
    the organization's repository (None: not checked)."""
    import of_events
    if not isinstance(e, dict):
        return ["an entry is a mapping"]
    out = []
    spec = KINDS.get(kind)
    if spec is None:  # of_secrets-review on PR 357: an unknown kind had no floor at all
        return [f"kind {kind!r} is no keeper kind ({', '.join(KINDS)}): nothing is served from it"]
    if str(e.get("position") or "") not in positions:
        out.append(f"position {e.get('position')!r} is no position of the organization")
    access = e.get("access")
    if not isinstance(access, str) or (spec and not (access.startswith(spec["access"]) and len(access) > len(spec["access"]))):
        out.append(f"access is {spec['access'] if spec else '<kind>:'}<what> for a {kind} keeper: {access!r}")
    acts = e.get("actions")
    if not isinstance(acts, list) or not acts or not all(isinstance(a, str) and ACTION_RE.match(a) for a in acts):
        out.append("actions is a list of words")
    elif spec and spec["actions"] and not set(acts) <= set(spec["actions"]):
        out.append(f"a {kind} entry's actions are {', '.join(spec['actions'])}")
    tier = e.get("tier")
    if tier not in RANK:
        out.append(f"tier is one of {', '.join(TIERS)}")
    elif spec and RANK[tier] < RANK[spec["floor"]]:
        out.append(f"a {kind} entry is {spec['floor']} at least (docs/access.md §2), not {tier}")
    out += _limit_problems(e.get("limit"), kind)
    decided = str(e.get("decided") or "").strip()
    card = bool(of_events.REF_RE.match(decided))
    if not decided:
        out.append("decided names the founder's card, or the structure keeper's commit for a plain entry")
    elif tier in RANK and RANK[tier] >= RANK["secret"] and not card:
        out.append(f"a {tier} entry is decided by the founder's card (docs/access.md §3), not {decided!r}")
    elif card and resolve is not None and not resolve(decided):
        out.append(f"decided {decided} is no ask on this machine")
    elif not card and not COMMIT_RE.match(decided):
        out.append(f"decided {decided!r} is neither a card id nor a commit")
    elif not card and commit_ok is not None and not commit_ok(decided):
        out.append(f"decided {decided} is no commit of the organization's repository")
    if tier in RANK and RANK[tier] >= RANK["secret"]:
        if not (isinstance(e.get("approval"), str) and e["approval"].startswith("passkey:") and len(e["approval"]) > 8):
            out.append(f"a {tier} entry needs approval: passkey:<record id> (docs/access.md §4)")
    if tier in ("money", "production"):
        g = e.get("grant")
        if not isinstance(g, str) or not g.strip():
            out.append(f"a {tier} entry needs the grant the founder signed (docs/access.md §4)")
        elif grant_ids is not None and g not in grant_ids:
            out.append(f"grant {g} is not in the organization's registry/grants.yaml")
    added = e.get("added")
    if not (isinstance(added, dict) and ISO_RE.match(_when(added.get("at")))
            and isinstance(added.get("by"), str) and added["by"].strip()):
        out.append("added is {at: <UTC time>, by: <position>}")
    if e.get("expires") is not None and not ISO_RE.match(_when(e["expires"])):
        out.append("expires is a UTC time such as 2026-12-01T00:00:00Z, or absent")
    return out


def list_problems(doc, keeper, positions, grant_ids=None, resolve=None, commit_ok=None):
    if not isinstance(doc, dict):
        return ["a keeper's list is a mapping"]
    out = []
    if doc.get("keeper") != keeper:
        out.append(f"keeper is {doc.get('keeper')!r}, not the file's name {keeper!r}")
    kind = doc.get("kind")
    if kind not in KINDS:
        out.append(f"kind is one of {', '.join(KINDS)}")
    if not isinstance(doc.get("host"), str) or not doc["host"].strip():
        out.append("host names the machine the keeper runs on")
    entries = doc.get("entries")
    if not isinstance(entries, list):
        return out + ["entries is a list"]
    seen = set()
    for i, e in enumerate(entries):
        key = (e.get("position"), e.get("access")) if isinstance(e, dict) else None
        if key in seen:
            out.append(f"entry {i + 1}: {key[0]} {key[1]} is listed twice")
        seen.add(key)
        out += [f"entry {i + 1}: {p}" for p in entry_problems(kind, e, positions, grant_ids, resolve, commit_ok)]
    return out


def _position_names(root):
    import yaml
    try:
        with open(os.path.join(root, "registry", "positions.yaml")) as fh:
            doc = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return set()
    return {str(p["name"]) for p in doc.get("positions") or [] if isinstance(p, dict) and p.get("name")}


def _grant_ids(root):
    import yaml
    try:
        with open(os.path.join(root, "registry", "grants.yaml")) as fh:
            doc = yaml.safe_load(fh) or {}
    except OSError:
        return set()
    except yaml.YAMLError:
        return None
    return {str(g.get("id")) for g in doc.get("grants") or [] if isinstance(g, dict) and g.get("id")}


def _card_resolver():
    """RESOLVE(card) -> bool from this machine's event store (of_asks.ref_id); None when the store cannot be read."""
    try:
        import of_asks
        import of_store
        conn = of_store.connect()
    except Exception:  # noqa: BLE001 - no store here: cards are not checked, and the check says nothing about them
        return None

    def resolve(card):
        try:
            return bool(of_asks.ref_id(conn, card))
        except Exception:  # noqa: BLE001
            return False
    return resolve


def _commit_checker(root, run=subprocess.run):
    def ok(sha):
        return _git(root, ["cat-file", "-e", f"{sha}^{{commit}}"], run) is not None
    return ok


def check_lists(orgs_file=None, resolve=None, run=subprocess.run):
    """`of registry check`'s part: every keeper list of every organization with a local checkout."""
    out = []
    resolve = _card_resolver() if resolve is None else resolve
    for org, root in _orgs(orgs_file):
        d = os.path.join(root, "registry", "access")
        if not os.path.isdir(d):
            continue
        positions, grant_ids = _position_names(root), _grant_ids(root)
        for fn in sorted(os.listdir(d)):
            rel = f"{org}:registry/access/{fn}"
            if fn == "approvals" and os.path.isdir(os.path.join(d, fn)):
                continue  # the passkey records the lines name (of_keeper.approval_record)
            if not fn.endswith(".yaml") or not KEEPER_RE.match(fn[:-5]):
                out.append(f"{rel}: not a keeper's list (registry/access/<keeper>.yaml)")
                continue
            try:
                doc = load_list(os.path.join(d, fn))
            except AccessError as e:
                out.append(f"{rel}: {str(e).split(': ', 1)[-1]}")
                continue
            out += [f"{rel}: {p}" for p in list_problems(doc, fn[:-5], positions, grant_ids, resolve,
                                                          _commit_checker(root, run))]
            out += [f"{rel}: {p}" for p in _approval_notes(org, root, fn[:-5], doc)]
    return out


def _approval_notes(org, root, keeper, doc, keys=None):
    """Lines of secret and up whose passkey record is missing or does not approve the line as it stands, checked as
    the keeper checks it (of_keeper.approval_problem) against this machine's pinned grant key; the enrollments are
    taken as the records carry them, since the keeper's own pins are on its host."""
    import of_grants
    import of_keeper
    out = []
    for i, e in enumerate(doc.get("entries") or [] if isinstance(doc, dict) else []):
        if not isinstance(e, dict) or RANK.get(e.get("tier"), 0) < RANK["secret"] or not e.get("approval"):
            continue
        rec = of_keeper.approval_record(root, e.get("approval"))
        if rec is None:
            out.append(f"entry {i + 1}: its approval {e['approval']} has no record in registry/access/approvals/")
            continue
        cred = str(rec.get("credential") or "")
        person = str((rec.get("enrollment") or {}).get("person") or "")
        why = of_keeper.approval_problem(org, keeper, e, rec, keys or of_grants.local_keys(), {cred: person})
        if why:
            out.append(f"entry {i + 1}: its approval does not approve the line: {why}")
    return out


def org_root(org, orgs_file=None):
    for o, root in _orgs(orgs_file):
        if o == org:
            return root
    raise AccessError(f"no organization {org!r} with a local checkout in orgs.yaml")


def grant(root, keeper, position, access, *, actions, tier, decided, by, limit=None, approval=None, grant_id=None,
          expires=None, kind=None, host=None, now_iso=None, resolve=None, run=subprocess.run):
    """Add one line to KEEPER's list (docs/access.md §3, step 5), checked as `of registry check` checks it; the list is
    started when it does not exist and KIND and HOST are given. Returns the entry."""
    path = list_path(root, keeper)
    doc = load_list(path)
    if doc is None:
        if not (kind and host):
            raise AccessError(f"no list for keeper {keeper} yet: give --kind ({', '.join(KINDS)}) and --host to start it")
        doc = {"keeper": keeper, "kind": kind, "host": host, "entries": []}
    elif (kind and kind != doc.get("kind")) or (host and host != doc.get("host")):
        raise AccessError(f"keeper {keeper} is a {doc.get('kind')} on {doc.get('host')}; its list says so already")
    doc.setdefault("entries", [])
    if not isinstance(doc["entries"], list):
        raise AccessError(f"{path}: entries is not a list")
    if any(isinstance(e, dict) and e.get("position") == position and e.get("access") == access for e in doc["entries"]):
        raise AccessError(f"{position} {access} is on {keeper}'s list already; revoke it before a new line")
    e = {"position": position, "access": access, "actions": list(actions or KINDS.get(doc.get("kind"), {}).get("actions")
                                                                 or []),
         "limit": limit or None, "tier": tier, "decided": decided}
    if approval:
        e["approval"] = approval
    if grant_id:
        e["grant"] = grant_id
    e["added"] = {"at": now_iso or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "by": by}
    e["expires"] = expires or None
    problems = list_problems({**doc, "entries": doc["entries"] + [e]}, keeper, _position_names(root),
                             _grant_ids(root), _card_resolver() if resolve is None else resolve,
                             _commit_checker(root, run))
    if problems:
        raise AccessError("; ".join(problems))
    doc["entries"].append(e)
    _write_list(path, doc)
    return e


def find_line(root, keeper, position, access):
    """(the list's document, the entry) for one line; AccessError when it is not on the list."""
    doc = load_list(list_path(root, keeper))
    for e in (doc or {}).get("entries") or []:
        if isinstance(e, dict) and e.get("position") == position and e.get("access") == access:
            return doc, e
    raise AccessError(f"{position} {access} is not on {keeper}'s list")


def record_approval(root, keeper, position, access, rid, record):
    """Save the owner's passkey record beside the list (approvals/<rid>.json) and name it on the line."""
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$", str(rid or "")):
        raise AccessError(f"an approval's id is letters, digits, - and _: {rid!r}")
    doc, e = find_line(root, keeper, position, access)
    d = os.path.join(root, "registry", "access", "approvals")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{rid}.json"), "w") as fh:
        json.dump(record, fh, indent=1, sort_keys=True)
        fh.write("\n")
    e["approval"] = f"passkey:{rid}"
    _write_list(list_path(root, keeper), doc)
    return e


def revoke(root, keeper, position, access):
    """Remove one line from KEEPER's list (docs/access.md §3, step 6): it only narrows, so it needs nobody's yes."""
    path = list_path(root, keeper)
    doc = load_list(path)
    entries = (doc or {}).get("entries") if isinstance((doc or {}).get("entries"), list) else []
    keep = [e for e in entries if not (isinstance(e, dict) and e.get("position") == position
                                       and e.get("access") == access)]
    if doc is None or len(keep) == len(entries):
        raise AccessError(f"{position} {access} is not on {keeper}'s list")
    removed = [e for e in entries if e not in keep]
    doc["entries"] = keep
    _write_list(path, doc)
    return removed[0]


def request_text(keeper, doc, position, access, actions, limit, why):
    """The routed ask to the structure keeper (docs/access.md §3, step 1), standing on its own."""
    kind = doc.get("kind")
    floor = KINDS.get(kind, {}).get("floor", "secret")
    lim = json.dumps(limit, sort_keys=True) if limit else "none"
    return (f"Give {position} {access} on keeper {keeper} ({kind} on {doc.get('host')}), actions "
            f"{', '.join(actions)}, limit {lim}? The work it is for: {why.strip()}. Per docs/access.md §3: check the "
            f"mandate with `of delegate`, then set the tier; this keeper's floor is {floor}. A plain entry is your "
            f"`of access grant`; a secret, money or production entry needs one founder card first, then his passkey "
            f"approval, and for money or production his signed grant.")


# ---------------------------------------------------------------- reading

def _orgs(orgs_file=None):
    """[(org, repository root)] for the organizations with a local checkout, in orgs.yaml's order."""
    import of_credentials
    return [(org, os.path.dirname(os.path.dirname(path))) for org, path in of_credentials._registries(orgs_file)]


def _read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def _lines(text):
    """{("listing", position, credential) | ("keeper", credential) | ("credential", credential): line (1-based)},
    from the YAML's nodes, so a flow list and a block list read alike."""
    import yaml
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return {}
    out = {}
    if not isinstance(root, yaml.MappingNode):
        return out

    def scalar(n):
        return n.value if isinstance(n, yaml.ScalarNode) else None

    for k, v in root.value:
        if scalar(k) == "credentials" and isinstance(v, yaml.MappingNode):
            for ck, cv in v.value:
                cid = scalar(ck)
                if cid is None:
                    continue
                out[("credential", cid)] = ck.start_mark.line + 1
                if isinstance(cv, yaml.MappingNode):
                    for fk, _ in cv.value:
                        if scalar(fk) == "keeper":
                            out[("keeper", cid)] = fk.start_mark.line + 1
        if scalar(k) == "positions" and isinstance(v, yaml.SequenceNode):
            for pn in v.value:
                if not isinstance(pn, yaml.MappingNode):
                    continue
                fields = {scalar(fk): fv for fk, fv in pn.value}
                name, creds = scalar(fields.get("name")) if fields.get("name") else None, fields.get("credentials")
                if name and isinstance(creds, yaml.SequenceNode):
                    for item in creds.value:
                        if scalar(item) is not None:
                            out[("listing", name, scalar(item))] = item.start_mark.line + 1
    return out


def _git(root, args, run=subprocess.run):
    try:
        r = run(["git", "-c", "core.fsmonitor=false", "-C", root, *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _blame(root, rel, run=subprocess.run):
    """{line: commit} for one file of a repository; an uncommitted line's commit is UNCOMMITTED; {} when git cannot
    tell (no repository, the file not tracked)."""
    out = {}
    for ln in (_git(root, ["blame", "--porcelain", "--", rel], run) or "").splitlines():
        parts = ln.split(" ")
        if len(parts) >= 3 and len(parts[0]) == 40 and all(ch in "0123456789abcdef" for ch in parts[0]):
            try:
                out[int(parts[2])] = parts[0]
            except ValueError:
                pass
    return out


def _commits(root, shas, run=subprocess.run):
    """{commit: (short id, date, position from its `Position:` trailer or None)}."""
    shas = sorted({s for s in shas if s and s != UNCOMMITTED})
    if not shas:
        return {}
    fmt = "%H%x1f%h%x1f%ad%x1f%(trailers:key=Position,valueonly,separator=%x2c)%x1e"
    out = {}
    for rec in (_git(root, ["log", "--no-walk=unsorted", "--date=short", f"--format={fmt}", *shas], run) or "").split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) == 4:
            out[parts[0]] = (parts[1], parts[2], parts[3].strip() or None)
    return out


def _approved(commit, info, in_git=False):
    if commit is None:
        return "uncommitted" if in_git else "not in git"
    if commit == UNCOMMITTED:
        return "uncommitted"
    short, date, position = info.get(commit) or (commit[:9], "?", None)
    return f"{short} {date}" + (f" {position}" if position else "")


def _state_dir(server_yaml=None):
    """The server's state folder, as of_server.load_config reads it (server.yaml's state_dir, else its default)."""
    import yaml
    path = server_yaml or os.path.expanduser("~/.config/of/server.yaml")
    try:
        with open(path) as fh:
            cfg = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        cfg = {}
    return os.path.expanduser(str(cfg.get("state_dir") or "~/.local/state/of/server"))


def broker_uses(state_dir=None):
    """{(org, position, credential): the last allowed use, epoch seconds} from the broker's records (server.db
    `secret_uses`, docs/agent-secrets.md §5.3) on a machine that runs the server; None where no server runs, so
    nothing was observed here. Read-only."""
    path = os.path.join(state_dir or _state_dir(), "server.db")
    if not os.path.exists(path):
        return None
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return {}
    try:
        rows = c.execute("SELECT org, position, credential, max(at) FROM secret_uses WHERE decision = 'allow'"
                         " GROUP BY org, position, credential").fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        c.close()
    return {(o, p, cr): at for o, p, cr, at in rows if at is not None}


def _pinned_keys():
    """What this machine trusts for grants: its pinned grant public key (as `of registry check` reads it)."""
    import of_grants
    try:
        with open(of_grants.pin_path()) as fh:
            return of_grants.Keys(ed25519=[ln for ln in fh.read().splitlines() if ln.strip()])
    except OSError:
        return of_grants.Keys()


# ---------------------------------------------------------------- the map

def _through(c):
    ref = c.get("ref") if isinstance(c.get("ref"), str) else None
    paths = [p for p in c.get("paths") or [] if isinstance(p, str)] if isinstance(c.get("paths"), list) else []
    chain = [k for k in c.get("keychain") or [] if isinstance(k, str)] if isinstance(c.get("keychain"), list) else []
    parts = []
    if ref:
        scheme = ref.split("://", 1)[0]
        parts.append(f"{STORES.get(scheme, scheme)} via the broker")
    if paths:
        parts.append("file" if len(paths) == 1 else f"{len(paths)} files")
    if chain:
        parts.append("keychain, the owner's programs only")
    return ", ".join(parts) or "nothing named"


def _iso(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _when(v):
    """A time as the registry wrote it; YAML reads an unquoted one as a datetime, shown back in UTC with a Z."""
    if isinstance(v, datetime.datetime):
        v = v if v.tzinfo else v.replace(tzinfo=datetime.timezone.utc)
        return v.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(v)


def _limit_text(limit):
    if not isinstance(limit, dict) or not limit:
        return None
    parts = []
    for k in ("per_call", "per_period"):
        v = limit.get(k)
        if isinstance(v, dict):
            parts.append(f"{v.get('amount')} {v.get('unit')}" + (f"/{v.get('period')}" if k == "per_period" else "/call"))
    return ", ".join(parts) or None


def _entry_lines(text):
    """{entry index: line (1-based)} of a keeper's list, from the YAML's nodes."""
    import yaml
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return {}
    if not isinstance(root, yaml.MappingNode):
        return {}
    for k, v in root.value:
        if isinstance(k, yaml.ScalarNode) and k.value == "entries" and isinstance(v, yaml.SequenceNode):
            return {i: n.start_mark.line + 1 for i, n in enumerate(v.value)}
    return {}


def _scope_text(scope):
    parts = []
    for k in sorted(scope or {}):
        v = scope[k]
        if v in (None, [], "", {}):
            continue
        vals = v if isinstance(v, list) else [v]
        shown = ", ".join(str(x) for x in vals[:3]) + (f" and {len(vals) - 3} more" if len(vals) > 3 else "")
        parts.append(f"{k}: {shown}")
    return "; ".join(parts) or "unrestricted"


def org_map(org, root, grants, keys, now_iso, uses, run=subprocess.run):
    """The map of one organization: {org, rows: [credential reach], grants: [grant rows], unreached: [ids],
    problems: [text]}."""
    import yaml
    rel = os.path.join("registry", "positions.yaml")
    text = _read(os.path.join(root, rel))
    if text is None:
        raise AccessError(f"{org}: cannot read {os.path.join(root, rel)}")
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise AccessError(f"{org}: {rel} is not valid YAML ({str(e).splitlines()[0]})") from None
    creds = doc.get("credentials") if isinstance(doc.get("credentials"), dict) else {}
    positions = [p for p in doc.get("positions") or [] if isinstance(p, dict) and p.get("name")]
    lines = _lines(text)
    blame = _blame(root, rel, run)
    in_git = _git(root, ["rev-parse", "--git-dir"], run) is not None  # a file git does not track is uncommitted
    reach = []  # (position, credential, how, line)
    for p in positions:
        for cid in p.get("credentials") or [] if isinstance(p.get("credentials"), list) else []:
            reach.append((str(p["name"]), str(cid), "lists", lines.get(("listing", str(p["name"]), str(cid)))))
    for cid, c in creds.items():
        keeper = c.get("keeper") if isinstance(c, dict) else None
        if keeper:
            reach.append((str(keeper), str(cid), "keeps", lines.get(("keeper", str(cid)))))
    commits = {ln: blame.get(ln) for _, _, _, ln in reach if ln}
    info = _commits(root, commits.values(), run)
    names = {str(p["name"]) for p in positions}
    rows, problems, seen = [], [], set()
    for position, cid, how, ln in reach:
        if (position, cid) in seen:
            continue  # a keeper that also lists it: one row, as it lists it
        seen.add((position, cid))
        c = creds.get(cid)
        if not isinstance(c, dict):
            problems.append(f"{position} lists {cid}, which the catalogue does not define")
            c = {}
        if position not in names:
            problems.append(f"{cid}'s keeper {position} is not a position of {org}")
        at = (uses or {}).get((org, position, cid))
        if at:
            last = _iso(at)
        elif not c.get("ref"):
            last = "not recorded"  # a file's use is not recorded today (docs/access.md §5 brings of.access_used)
        else:
            last = "none here" if uses is not None else "no broker here"
        rows.append({"org": org, "position": position, "access": cid, "how": how, "through": _through(c),
                     "custody": c.get("custody") is True, "limit": None,
                     "approved": _approved(commits.get(ln) if blame else None, info, in_git),
                     "last_used": last,
                     "acts_as": c.get("acts_as"),
                     "paths": [p for p in c.get("paths") or [] if isinstance(p, str)]
                     if isinstance(c.get("paths"), list) else [],
                     "ref": c.get("ref") if isinstance(c.get("ref"), str) else None})
    for kpath in sorted(glob.glob(os.path.join(root, "registry", "access", "*.yaml"))):
        keeper = os.path.basename(kpath)[:-5]
        try:
            kdoc = load_list(kpath) or {}
        except AccessError as e:
            problems.append(str(e))
            continue
        krel = os.path.relpath(kpath, root)
        kblame, klines = _blame(root, krel, run), _entry_lines(_read(kpath) or "")
        kinfo = _commits(root, kblame.values(), run)
        for i, e in enumerate(kdoc.get("entries") or []):
            if not isinstance(e, dict):
                continue
            ln = klines.get(i)
            committed = _approved(kblame.get(ln) if kblame else None, kinfo, in_git)
            rows.append({"org": org, "position": str(e.get("position") or "?"), "access": str(e.get("access") or "?"),
                         "how": f"{e.get('tier') or '?'} line", "through": f"{keeper} ({kdoc.get('kind')}, "
                                                                          f"{kdoc.get('host')})",
                         "custody": False, "limit": _limit_text(e.get("limit")),
                         "approved": f"{e.get('decided') or '?'}; " + (committed if committed != "uncommitted"
                                                                       else "uncommitted: serves nothing"),
                         "last_used": "not recorded", "acts_as": ", ".join(e.get("actions") or []),
                         "paths": [], "ref": None, "keeper": keeper})
    reached = {cid for _, cid, _, _ in reach}
    unreached = sorted(str(cid) for cid in creds if str(cid) not in reached)
    import of_grants
    grows = []
    for g in grants:
        if not isinstance(g, dict):
            continue
        ok, why = of_grants.valid(g, keys, now_iso)
        limit = []
        if g.get("expires"):
            limit.append(f"until {_when(g['expires'])}")
        if g.get("counter") is not None:
            limit.append(f"{g['counter']} uses")
        prov = g.get("provenance") if isinstance(g.get("provenance"), dict) else {}
        grows.append({"org": org, "position": str(g.get("subject") or "?"), "grant": str(g.get("id") or "?"),
                      "tier": g.get("tier"), "effect": g.get("effect") or "allow",
                      "scope": _scope_text(g.get("scope") if isinstance(g.get("scope"), dict) else {}),
                      "limit": ", ".join(limit) or None, "state": "in force" if ok else why,
                      "approved": f"issued by {g.get('issuer') or '?'}"
                                  + (f", {prov['source']}" if prov.get("source") else ""),
                      "note": g.get("note")})
    rows.sort(key=lambda r: (r["position"], r["access"]))
    grows.sort(key=lambda r: (r["position"], r["grant"]))
    return {"org": org, "rows": rows, "grants": grows, "unreached": unreached, "problems": problems}


def build(org=None, position=None, orgs_file=None, uses=None, keys=None, now_iso=None, run=subprocess.run):
    """[org_map] for every organization with a local checkout, or the one named; rows narrowed to one position."""
    import of_grants
    known = _orgs(orgs_file)
    if org and org not in {o for o, _ in known}:
        raise AccessError(f"no organization {org!r} with a local checkout in orgs.yaml (known: "
                          f"{', '.join(o for o, _ in known) or 'none'})")
    of_file = orgs_file or os.environ.get("OF_ORGS") or os.path.expanduser("~/.config/of/orgs.yaml")
    try:
        paths = of_grants._org_grants_paths(of_file)
    except OSError:
        paths = {}
    uses = broker_uses() if uses is None else (uses or None)
    keys = _pinned_keys() if keys is None else keys
    now_iso = now_iso or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out = []
    for o, root in known:
        if org and o != org:
            continue
        grants = []
        gp = paths.get(o)
        if gp and os.path.exists(gp):
            import yaml
            try:
                with open(gp) as fh:
                    grants = (yaml.safe_load(fh) or {}).get("grants") or []
            except (OSError, yaml.YAMLError) as e:
                raise AccessError(f"{o}: cannot read its grants ({str(e).splitlines()[0]})") from None
        m = org_map(o, root, grants, keys, now_iso, uses, run)
        if position:
            m["rows"] = [r for r in m["rows"] if r["position"] == position]
            m["grants"] = [g for g in m["grants"] if g["position"] in (position, "*")]
        out.append(m)
    return out


# ---------------------------------------------------------------- the weekly review (docs/access.md §5)

DIGEST_HOUR = 5        # UTC, on Mondays: the first run at or after it sends the week's digest
UNUSED_DAYS = 30       # a proposal (docs/access.md §5); an organization sets access_review: {unused_days: N}
EXPIRING_DAYS = 14


def _epoch(iso):
    import calendar
    return calendar.timegm(time.strptime(_when(iso)[:19], "%Y-%m-%dT%H:%M:%S"))


def access_events(conn, org, since_iso):
    """[(name, ts, payload)] of the keepers' records for ORG since SINCE_ISO: of.access_used, of.access_refused."""
    out = []
    try:
        rows = conn.execute("SELECT name, ts, payload FROM event WHERE name IN ('of.access_used', 'of.access_refused')"
                            " AND ts >= ? ORDER BY ts", (since_iso,)).fetchall()
    except sqlite3.Error:
        return out
    for name, ts, pl in rows:
        try:
            p = json.loads(pl) if isinstance(pl, str) else (pl or {})
        except ValueError:
            continue
        if p.get("org") == org:
            out.append((name, ts, p))
    return out


def _lists_at(root, rev, run=subprocess.run):
    """{(keeper, position, access): entry} of the lists committed at REV (None: the working copy's)."""
    import yaml
    out = {}
    if rev is None:
        for path in sorted(glob.glob(os.path.join(root, "registry", "access", "*.yaml"))):
            try:
                doc = load_list(path) or {}
            except AccessError:
                continue
            for e in doc.get("entries") or []:
                if isinstance(e, dict):
                    out[(os.path.basename(path)[:-5], e.get("position"), e.get("access"))] = e
        return out
    names = (_git(root, ["ls-tree", "--name-only", rev, "registry/access/"], run) or "").split()
    for n in names:
        if not n.endswith(".yaml"):
            continue
        try:
            doc = yaml.safe_load(_git(root, ["show", f"{rev}:{n}"], run) or "") or {}
        except yaml.YAMLError:
            continue
        for e in doc.get("entries") or [] if isinstance(doc, dict) else []:
            if isinstance(e, dict):
                out[(os.path.basename(n)[:-5], e.get("position"), e.get("access"))] = e
    return out


def digest_text(org, root, conn=None, now=None, run=subprocess.run):
    """The week's access review for ORG's structure keeper (docs/access.md §5), or None when there is nothing in it."""
    import yaml
    now = now or time.time()
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))  # noqa: E731
    try:
        with open(os.path.join(root, "registry", "positions.yaml")) as fh:
            reg = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        reg = {}
    review = reg.get("access_review") if isinstance(reg.get("access_review"), dict) else {}
    days = review.get("unused_days") if isinstance(review.get("unused_days"), int) else UNUSED_DAYS
    events = access_events(conn, org, iso(now - max(days, 7) * 86400)) if conn is not None else []
    used = {}
    for name, ts, p in events:
        if name == "of.access_used":
            k = (p.get("keeper"), p.get("position"), p.get("access"))
            used[k] = max(used.get(k, ""), str(ts))
    lines = _lists_at(root, "HEAD", run) if _git(root, ["rev-parse", "--verify", "-q", "HEAD"], run) else {}
    unused = []
    for (keeper, position, access), e in sorted(lines.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        added = (e.get("added") or {}).get("at") if isinstance(e.get("added"), dict) else None
        last = used.get((keeper, position, access))
        since = last or (_when(added) if added else None)
        try:
            idle = since is None or now - _epoch(since) >= days * 86400
        except ValueError:
            idle = True
        if idle:
            unused.append(f"- {keeper}: {position} {access} ({e.get('tier')}), "
                          f"{'last used ' + last if last else 'never used'}: "
                          f"`of access revoke {keeper} {position} {access} --why \"unused for {days} days\"`")
    expiring = []
    try:
        with open(os.path.join(root, "registry", "grants.yaml")) as fh:
            grants = (yaml.safe_load(fh) or {}).get("grants") or []
    except (OSError, yaml.YAMLError):
        grants = []
    for g in grants:
        if not isinstance(g, dict) or g.get("revoked") or not g.get("expires"):
            continue
        try:
            left = _epoch(g["expires"]) - now
        except ValueError:
            continue
        if 0 < left <= EXPIRING_DAYS * 86400:
            expiring.append(f"- {g.get('id')} for {g.get('subject')}: expires {_when(g['expires'])}")
    refused = [f"- {p.get('keeper')}: {p.get('position')} {p.get('access')} at {ts}: {p.get('reason')}"
               for name, ts, p in events if name == "of.access_refused" and str(ts) >= iso(now - 7 * 86400)]
    week_ago = _git(root, ["rev-list", "-1", f"--before={iso(now - 7 * 86400)}", "HEAD"], run)
    before = _lists_at(root, week_ago.strip(), run) if week_ago and week_ago.strip() else {}
    added = [f"- {k[0]}: {k[1]} {k[2]} ({lines[k].get('tier')})" for k in sorted(set(lines) - set(before), key=str)]
    removed = [f"- {k[0]}: {k[1]} {k[2]}" for k in sorted(set(before) - set(lines), key=str)]
    parts = []
    if unused:
        parts.append(f"Unused for {days} days or more, each with its revoke line:\n" + "\n".join(unused))
    if expiring:
        parts.append(f"Grants that expire within {EXPIRING_DAYS} days:\n" + "\n".join(expiring))
    if refused:
        parts.append("Lines a keeper refused this week:\n" + "\n".join(refused))
    if added:
        parts.append("Granted this week (committed):\n" + "\n".join(added))
    if removed:
        parts.append("Removed this week (committed):\n" + "\n".join(removed))
    if not parts:
        return None
    return (f"Access review of {org} (docs/access.md §5; `of access map --org {org}` shows it all). You decide; a "
            "money or production line you keep past its unused days stays on next week's review.\n\n"
            + "\n\n".join(parts))


def digest_state_path():
    import of_events
    return os.path.join(of_events.STATE_DIR, "access-digest.json")


def due(now):
    """True from DIGEST_HOUR UTC on a Monday."""
    t = time.gmtime(now)
    return t.tm_wday == 0 and t.tm_hour >= DIGEST_HOUR


def send_digests(positions, orgs_file=None, conn=None, now=None, force=False, dry_run=False, send=None, log=print):
    """Once per ISO week per organization, its structure keeper's digest as an --fyi message from that keeper's own
    position, within its organization; an organization with nothing in it gets nothing, and its week still counts."""
    import of_pulse
    now = now or time.time()
    if not force and not due(now):
        return 0
    week = time.strftime("%G-W%V", time.gmtime(now))
    try:
        with open(digest_state_path()) as fh:
            st = json.load(fh) or {}
    except (OSError, ValueError):
        st = {}
    sent = 0
    for org, root in _orgs(orgs_file):
        if not force and st.get(org) == week:
            continue
        keeper = of_pulse.structure_keeper(positions, org)
        text = digest_text(org, root, conn, now) if keeper else None
        if text:
            if dry_run:
                log(f"to {org}:{keeper}:\n{text}\n")
                continue
            if not (send or _send)(f"{org}:{keeper}", text, log):
                continue  # tried again at the next run
            sent += 1
        if not dry_run:
            st[org] = week
    if not dry_run:
        tmp = digest_state_path() + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(st, fh)
        os.replace(tmp, digest_state_path())
    return sent


def _send(to, text, log):
    import sys
    here = os.path.dirname(os.path.realpath(__file__))
    r = subprocess.run([sys.executable, os.path.join(here, "of"), "send", to, "--from", to.split(":", 1)[1], "--fyi",
                        text], capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
    if r.returncode != 0:
        log(f"access digest to {to}: not sent ({(r.stderr or r.stdout).strip()[:200]})")
    return r.returncode == 0


# ---------------------------------------------------------------- printing

def _table(header, rows, widths):
    def cut(s, w):
        s = "" if s is None else str(s)
        return s if len(s) <= w else s[:w - 1] + "…"
    cols = [max(len(h), *(len(cut(r[i], widths[i])) for r in rows)) if rows else len(h) for i, h in enumerate(header)]
    lines = ["  ".join(h.ljust(cols[i]) for i, h in enumerate(header)).rstrip()]
    for r in rows:
        lines.append("  ".join(cut(r[i], widths[i]).ljust(cols[i]) for i in range(len(header))).rstrip())
    return lines


def render(maps, long=False):
    out = []
    for m in maps:
        n_pos = len({r["position"] for r in m["rows"]})
        out.append(f"{m['org']}: {len(m['rows'])} reaches by {n_pos} positions, {len(m['grants'])} grants"
                   " (from the catalogue, the keepers' lists and the grants)")
        if m["rows"]:
            body = [(r["position"], r["access"], r["how"] + (", custody" if r["custody"] else ""), r["through"],
                     r["limit"] or "-", r["approved"], r["last_used"]) for r in m["rows"]]
            out += ["  " + ln for ln in _table(("POSITION", "REACHES", "HOW", "THROUGH", "LIMIT", "APPROVED",
                                                "LAST USED"), body, (32, 36, 14, 40, 20, 36, 18))]
            if long:
                for r in m["rows"]:
                    where = r["ref"] or ", ".join(r["paths"]) or "-"
                    out.append(f"    {r['position']} / {r['access']}: {r['acts_as'] or 'acts as: not stated'}; {where}")
        if m["grants"]:
            body = [(g["position"], g["grant"], g["tier"], g["effect"], g["scope"], g["limit"] or "-", g["state"],
                     g["approved"]) for g in m["grants"]]
            out += ["  " + ln for ln in _table(("SUBJECT", "GRANT", "TIER", "EFFECT", "SCOPE", "LIMIT", "STATE",
                                                "APPROVED"), body, (32, 24, 18, 6, 60, 26, 26, 40))]
        if m["unreached"]:
            out.append(f"  reach no position ({len(m['unreached'])}): {', '.join(m['unreached'])}")
        for p in m["problems"]:
            out.append(f"  problem: {p}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def cli(args, out=print):
    if args.action != "map":
        raise AccessError(f"unknown action {args.action!r}")
    maps = build(org=args.org, position=args.position)
    if args.json:
        out(json.dumps(maps, indent=1, ensure_ascii=False))
    else:
        out(render(maps, long=getattr(args, "long", False)).rstrip("\n"))
    return 0
