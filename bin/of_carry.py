"""Carry an organization's sessions to another machine and back (docs/capacity.md, "Carrying sessions to another
machine"; organic-framework-build reports/agent-server-migration-plan-2026-11-11.md §3 B1 and §9; owned by
of_capacity).

On the agent host each organization runs as its own OS user, so a session moves both to another machine and into
another home: /Users/alex on the laptop, /Users/of or /Users/acme on the host, and back for a rollback.

- `prepare` (the source, as the organization's user): a bundle directory with `plan.json`, the reboot plan's entries
  for the organization's live sessions plus this machine's name, home, uid and each account's address, and
  `files.txt`, every file those sessions need: their state in each config dir (transcript, subagents, file history,
  session env, tasks, todos), the folder memory, the position's own memory directory, and their scratch folders under
  the agent tmp root. Read-only for the sessions; freeze them afterwards (`of capacity close`) for a final copy.
- `copy` (the laptop, at a moment the founder picks): rsync over SSH with an explicit key file, `SSH_AUTH_SOCK`
  empty and the user's SSH agent refused, at background priority (`taskpolicy -b`, on Linux `nice` and `ionice`) and
  a bandwidth limit. It pushes a
  bundle to the other machine, or pulls one prepared there.
- `restore` (the target, as the organization's user): every path rewritten from the source's home and uid to this
  user's (`Rewriter`), the files put in place (whatever is there is kept aside in the bundle first), then each session
  resumed as it ran by `of_capacity.reboot_restore`, on an account checked to be the same one.

A transcript's conversation (`message`) is never rewritten: its thinking blocks are signed, and an edit there makes
the API refuse the session. Everything around it is (the working folder, file-history paths, tool-result metadata),
and the resume note tells the session its home moved.
"""

import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import time

import of_capacity
import of_guard

SKIP_LARGER = 64 * 1024 * 1024        # a scratch or task-output file larger than this stays behind (named in the plan)
BWLIMIT_KBPS = 25000                  # the copy's default limit, KB/s: the laptop runs light work only
SSH_OPTS = ["-o", "IdentityAgent=none", "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes"]


class CarryError(Exception):
    pass


# ------------------------------------------------------------------ paths


def key_of(path):
    """Claude Code's folder key for a path: every character other than a letter or digit becomes "-"
    (/Users/alex/x/.claude/worktrees/y -> -Users-alex-x--claude-worktrees-y)."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


class Rewriter:
    """One machine's paths to another's: the home, the folder keys built from it, and the agent tmp root's uid."""

    def __init__(self, old_home, new_home, old_uid, new_uid, old_tmp=None, new_tmp=None):
        import of_capacity
        self.old_home, self.new_home = old_home.rstrip("/"), new_home.rstrip("/")
        self.old_key, self.new_key = key_of(self.old_home), key_of(self.new_home)
        # the source's tmp root as its plan recorded it (macOS: /private/tmp/claude-<uid>), the target's by its own
        # platform (Linux: /tmp/claude-<uid>): a carry from the laptop to a Linux host maps one to the other
        self.old_tmp = (old_tmp or f"/private/tmp/claude-{old_uid}").rstrip("/")
        if not new_tmp and self.old_tmp.startswith(self.old_home + "/"):
            new_tmp = self.new_home + self.old_tmp[len(self.old_home):]  # a rehearsal's tmp root stays in its home
        self.new_tmp = (new_tmp or of_capacity.agent_tmp_root(new_uid)).rstrip("/")
        self.old_tmp_key, self.new_tmp_key = key_of(self.old_tmp), key_of(self.new_tmp)
        # one pass, so a replacement is never rewritten again (a new home whose own path holds the old key, as a
        # rehearsal home inside the tmp root does). A key starts a path component or follows the tmp root's own key
        # (-private-tmp-claude-501--Users-alex-x).
        self._all = re.compile("|".join([
            "(?P<tmp>" + re.escape(self.old_tmp) + r")(?=/|$)",
            r"(?<![A-Za-z0-9-])(?P<tmpkey>" + re.escape(self.old_tmp_key) + r")(?=-|$|/)",
            "(?P<home>" + re.escape(self.old_home) + r")(?=/|$|[\"'\s:;,)\]])",
            r"(?<![A-Za-z0-9])(?P<key>" + re.escape(self.old_key) + r")(?=-|$|/)"]))
        self._to = {"tmp": self.new_tmp, "tmpkey": self.new_tmp_key, "home": self.new_home, "key": self.new_key}
        self.identity = (self.old_home == self.new_home and old_uid == new_uid)
        self._needles = [x.encode() for x in (self.old_home, self.old_key, self.old_tmp)]

    def text(self, s):
        """Every mention of the old home, its folder keys and the old tmp root in `s`, rewritten."""
        if self.identity or not isinstance(s, str):
            return s
        return self._all.sub(lambda m: self._to[m.lastgroup], s)

    def touches(self, data):
        """Whether bytes mention anything this rewrites (a line that does not is kept byte for byte)."""
        return not self.identity and any(n in data for n in self._needles)

    def path(self, p):
        return self.text(p)

    def record(self, obj, _top=True):
        """A transcript record with every string rewritten except its conversation (`message`): keys too, since
        file-history snapshots key their backups by path."""
        if isinstance(obj, dict):
            return {self.text(k): (v if (k == "message" and _top) else self.record(v, False)) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.record(v, False) for v in obj]
        return self.text(obj)

    def transcript(self, data):
        """A .jsonl transcript's bytes, rewritten record by record; a line that is not JSON is kept as it is."""
        out = []
        for line in data.split(b"\n"):
            if not line.strip() or not self.touches(line):
                out.append(line)
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                out.append(line)
                continue
            new = self.record(rec)
            try:
                out.append(json.dumps(new, ensure_ascii=False, separators=(",", ":")).encode())
            except UnicodeEncodeError:  # a string cut inside an emoji (a lone \ud83d): the same JSON, escaped
                out.append(json.dumps(new, ensure_ascii=True, separators=(",", ":")).encode())
        return b"\n".join(out)


# ------------------------------------------------------------------ prepare


def _is_transcript(p):
    return p.endswith(".jsonl") and "/projects/" in p


def _files_under(root, skipped):
    """The regular files under `root`. Left behind and named in `skipped` with the reason: a file at a path the guard
    treats as secret (of_server on PR 116: 363 such files in this Mac's scratch and memory folders, kubeconfigs and
    .env files among them; credentials reach another machine only through their own committed process), its contents
    never read; and a scratch or task-output file over SKIP_LARGER. A transcript always goes: it is the session."""
    out = []
    for dirpath, dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if not _is_transcript(p) and of_guard._secret_path(p):
                skipped.append({"path": p, "reason": "secret path"})
                continue
            if st.st_size > SKIP_LARGER and ("/tmp/" in p or "/tasks/" in p):  # macOS /private/tmp, Linux /tmp
                skipped.append({"path": p, "bytes": st.st_size, "reason": "over the size limit"})
                continue
            out.append(p)
    return out


def custody_paths(docs=None):
    """[(organization, credential id, path pattern)] of every credential a listed registry marks `custody: true`: a
    key the agents' home never holds (docs/registry.md, Credentials; hand-over a1bde073 from of_architect, after
    of_server's review of the agent host plan, message 01a1efa4-fddf). Patterns are expanded as the guard's catalogue
    expands them (of_credentials._abs) and matched as it matches them."""
    import of_credentials
    if docs is None:
        import yaml
        registries = []
        for org, path in of_credentials._registries():
            try:
                with open(path) as fh:
                    doc = yaml.safe_load(fh)
            except Exception:  # a parser error can quote the registry's contents
                doc = None
            if not isinstance(doc, dict):
                raise CarryError(f"the registry of {org} ({path}) cannot be read: carry refuses, "
                                 "since its custody rules would be lost")
            registries.append((org, doc))
    else:
        registries = sorted(docs.items())
    out = []
    for org, doc in registries:
        creds = (doc or {}).get("credentials") if isinstance(doc, dict) else None
        for cid, c in sorted((creds or {}).items()) if isinstance(creds, dict) else ():
            if isinstance(c, dict) and c.get("custody") is True:
                paths = c.get("paths") or []
                if isinstance(paths, str):
                    paths = [paths]
                for path in paths:
                    if isinstance(path, str) and path.strip():
                        out.append((org, str(cid), of_credentials._abs(path)))
    return out


def custody_of(path, pats):
    """'<org>/<credential>' when `path` is a custody credential's file (its pattern, or under its folder), else None."""
    import of_credentials
    for org, cid, pat in pats or []:
        if of_credentials._match(pat, path) or (not any(ch in pat for ch in "*?[") and path.startswith(pat.rstrip("/") + "/")):
            return f"{org}/{cid}"
    return None


def drop_custody(files, skipped, pats, log=print):
    """`files` without every custody credential's path, each named in `skipped` and printed."""
    keep = []
    for f in files:
        who = custody_of(f, pats)
        if who:
            skipped.append({"path": f, "reason": f"custody: {who}"})
            log(f"  left out (custody: {who}): {f}")
        else:
            keep.append(f)
    return keep


_CHUNK = 64 * 1024
# The guard's private key headers (of_guard._KEY_HEAD, G1.13) at any offset, without its start anchor, BOM and
# whitespace; tests.test_carry checks the two agree.
_QUOTED_KEY = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----|PuTTY-User-Key-File-\d+:")
_PEM_TYPE = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*[A-Z0-9]*\Z")
_PUTTY_VERSION = re.compile(rb"PuTTY-User-Key-File-\d*\Z")


def _quotes_key(path):
    """Find a quoted header even when a read ends inside it."""
    tail = b""
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            data = tail + chunk
            if _QUOTED_KEY.search(data):
                return True
            # 96 bytes cover the literal parts of either header. A longer match must still be in its
            # type words or version digits 64 bytes before the end. Carry that state in one byte.
            tail = data[-96:]
            cut = max(0, len(data) - 64)
            if _PEM_TYPE.search(data, 0, cut):
                word = b"" if data[cut - 1:cut] == b" " else b"X"
                tail = b"-----BEGIN " + word + data[cut:]
            elif _PUTTY_VERSION.search(data, 0, cut):
                digit = b"0" if data[cut - 1:cut].isdigit() else b""
                tail = b"PuTTY-User-Key-File-" + digit + data[cut:]
    return False


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.digest()


def _custody_files(pattern):
    """Use the registry's glob rules, and include descendants of a named folder."""
    import of_credentials
    if glob.has_magic(pattern):
        yield from (p for p in glob.glob(pattern, recursive=True) if of_credentials._match(pattern, p))
    elif os.path.isdir(pattern):
        for directory, _, names in os.walk(pattern):
            for name in names:
                yield os.path.join(directory, name)
    else:
        yield pattern


def _custody_index(pats):
    """Look up local custody files by inode, or by size and digest."""
    inodes, digests = {}, {}
    for org, cid, pattern in pats:
        for path in _custody_files(pattern):
            try:
                st = os.stat(path)
            except (FileNotFoundError, NotADirectoryError):
                continue
            inode = (st.st_dev, st.st_ino)
            if stat.S_ISREG(st.st_mode) and inode not in inodes:
                who = f"{org}/{cid}"
                inodes[inode] = who
                digests.setdefault(st.st_size, {}).setdefault(_sha256(path), who)
    return inodes, digests


def _content_matches(files, pats, log, named=None, root=None):
    """Yield files to withhold. Name transcripts that quote a header and keep them intact."""
    inodes, digests = _custody_index(pats)
    for path in files:
        try:  # a scratch file removed between the listing and this check has nothing left to send
            st = os.lstat(path) if root else os.stat(path)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        source = "/" + os.path.relpath(path, root) if root else path
        who = inodes.get((st.st_dev, st.st_ino))
        if not who and st.st_size in digests:
            who = digests[st.st_size].get(_sha256(path))
        if who:
            yield path, f"custody copy: {who}"
        elif _is_transcript(source):
            if _quotes_key(path):
                if named is not None:
                    named.append({"path": source, "reason": "transcript quotes a private key"})
                log(f"  transcript quotes a private key: {source}")
        elif of_guard._key_file(path):
            yield path, "private key header"


def _drop_copies(files, pats, skipped, log, named=None):
    matches = dict(_content_matches(files, pats, log, named=named))
    for path, reason in matches.items():
        skipped.append({"path": path, "reason": reason})
        log(f"  left out ({reason}): {path}")
    return [p for p in files if p not in matches]


def filter_files_list(path, pats, log=print, check_contents=False):
    """Rewrite a bundle's files.txt without the custody credentials' paths, before a copy sends any file (a
    credential marked after prepare, or a bundle another user prepared). Returns the paths left out."""
    try:
        with open(path) as fh:
            lines = [x.rstrip("\n") for x in fh if x.strip()]
    except FileNotFoundError:
        return []
    skipped = []
    keep = drop_custody(lines, skipped, pats, log=log)
    if check_contents:
        keep = _drop_copies(keep, pats, skipped, log)
    if skipped:
        _write_private(path, "".join(f + "\n" for f in keep))
    return [x["path"] for x in skipped]


def session_files(entry, tmp_root, memory_dir=None):
    """Every file one plan entry needs on the other machine, and the large scratch files left behind."""
    d, sid = entry["config_dir"], entry["session"]
    files, skipped = [], []
    for rel in of_capacity.session_paths(d, sid):
        p = os.path.join(d, rel)
        if os.path.isdir(p):
            files += _files_under(p, skipped)
        elif _is_transcript(p) or not of_guard._secret_path(p):
            files.append(p)
        else:
            skipped.append({"path": p, "reason": "secret path"})
    # a project's folder memory stays: sessions run on their position's own memory (capacity.yaml position_memory),
    # and a folder's index is loaded into every session that starts there, of any organization (of_review, PR 116)
    mem = memory_dir or entry.get("memory_dir")
    if mem and os.path.isdir(mem):
        files += _files_under(mem, skipped)
    if tmp_root and os.path.isdir(tmp_root):
        for proj in os.listdir(tmp_root):
            scratch = os.path.join(tmp_root, proj, sid)
            if os.path.isdir(scratch):
                files += _files_under(scratch, skipped)
    return sorted(set(files)), skipped


def selected(entries, only):
    """The entries of the organizations or positions named in `only`."""
    return [e for e in entries if e.get("org") in only or e["name"] in only or (e.get("position") or "") in only]


def _bypass(d):
    try:
        with open(os.path.join(d, "settings.json")) as fh:
            return bool((json.load(fh) or {}).get("skipDangerousModePermissionPrompt"))
    except (OSError, ValueError):
        return False


def consents(entries):
    """{config dir: {"bypass": bool, "trusted": [folder keys]}}: the owner's earlier decisions on this machine that a
    carried session needs to start in its folder on its account: the Bypass Permissions acceptance (settings'
    skipDangerousModePermissionPrompt) and the trust of each session's folder (its trusted key in the account file,
    or in the default dir's). The agent host is this machine's continuation (the founder, of_conductor message
    01a1ef95-1685; of_agent-server's yes, message 01a1ef9c-3fb0), so restore carries them with the paths rewritten,
    as a move within one machine carries them (ensure_trust, ensure_settings)."""
    out = {}
    for e in entries:
        d, cwd = e.get("config_dir"), e.get("cwd")
        if not d:
            continue
        c = out.setdefault(d, {"bypass": False, "trusted": []})
        c["bypass"] = c["bypass"] or _bypass(d)
        if cwd and os.path.isdir(cwd):
            cands = of_capacity.trust_candidates(cwd)
            k = of_capacity.trusted_key(d, cwd, cands) or of_capacity.trusted_key(os.path.expanduser("~/.claude"),
                                                                                 cwd, cands)
            if k and k not in c["trusted"]:
                c["trusted"].append(k)
    return out


def _write_json_600(path, doc):
    tmp = f"{path}.of-carry.{os.getpid()}.tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(doc, fh, indent=2)
    os.replace(tmp, path)


def owner_yes(card, cards=None, conn=None, host=None, keeper=None):
    """The owner's card `card` (a card id) when he himself answered it with its first option, the yes by the filer's
    convention ("yes …|no …"), on a card never amended; else None. Read by event id from the listener's asks view and
    the card's own events, never the whole log. The filer is an OF position, or `keeper`: the structure keeper of the
    organization being restored, whose card lives in that organization's own store (2026-11-18, of_agent-server: on a
    host of one OS user per organization, the user organization's restore reads its own store, which holds no OF
    card, and its decisions stay in its own contour)."""
    if cards is None:
        import of_asks
        cards = of_asks.current_asks().get("asks") or []
    a = next((x for x in cards if str(x.get("ref") or "").upper() == str(card or "").strip().upper()), None)
    if not a or a.get("state") != "answered" or not a.get("closed_by"):
        return None
    if conn is None:
        import of_events
        conn = of_events._conn()

    def payload(eid):
        row = conn.execute("SELECT payload FROM event WHERE event_id = ?", (eid,)).fetchone()
        try:
            return json.loads(row[0]) if row else {}
        except ValueError:
            return {}
    if a.get("amended") or int(a.get("version") or 1) > 1:
        return None  # an amended card's options are read from its amendments, a log read this check does not make
    filed, closed = payload(a["event_id"]), payload(a["closed_by"])
    q = (filed.get("questions") or [{}])[0] or {}
    opts = q.get("options") or []
    first = str(opts[0]).strip() if opts else ""
    # and about this machine: its question or its yes names the host the restore runs on (of_server, PR 339)
    here = str(host or socket.gethostname()).split(".")[0].strip().lower()
    names_host = bool(here) and re.search(r"(?<![a-z0-9-])" + re.escape(here) + r"(?![a-z0-9-])",
                                          (str(q.get("question") or "") + " " + first).lower()) is not None
    # the card is about these consents (of_review, PR 339: an unrelated yes card unlocked them): its yes names the
    # Bypass Permissions acceptance (the product's own term, kept in any language), an OF position filed it, and the
    # answer names this ask. Card tags are a fixed set with no tag for it.
    about = bool(_CONSENT_BYPASS.search(first))
    filer = str(filed.get("from_position") or a.get("position") or "")
    ok = (closed.get("by") in ("owner", "founder") and bool(first) and about and names_host
          and str(closed.get("answer") or "").strip() == first
          and a["event_id"] in ([closed.get("ask_id")] + list(closed.get("ask_ids") or []))
          and (filer.startswith("of_") or (bool(keeper) and filer == keeper)))
    return a if ok else None


_CONSENT_BYPASS = re.compile(r"(?i)\bbypass permissions\b")


def carry_consents(plan, rw, entries, dry_run=False, log=print):
    """Put the plan's consents in place under this user, paths rewritten, for `entries` only: the plan's entries restore
    accepted (its organization, a position of it in this machine's registry, a well-formed session, a config dir
    that is the home's own .claude or .claude-*, --only, and the account check: of_review, PR 339, a crafted plan
    trusted the new home and another organization's folders). Only with the owner's card that says yes to it
    (restore --consents <card>; of_server, PR 339). Per entry: the Bypass Permissions acceptance into its config dir's
    settings.json when the plan's consents say so and the dir lacks it; and the trust of the plan's trusted folder
    for that dir that holds the session's own folder, strictly inside the home, when it exists here. A config dir
    not here, or not signed in yet, is named and left: run restore again after the sign-in. Existing values are
    never overwritten. Returns [(config dir, what)]."""
    done, consents = [], plan.get("consents") or {}
    home = os.path.normpath(plan["home"])
    wanted = {}
    for e in entries:
        src_d, src_cwd = os.path.normpath(e.get("config_dir") or "/"), os.path.normpath(e.get("cwd") or "/")
        c = consents.get(e.get("config_dir")) or consents.get(src_d) or {}
        w = wanted.setdefault(src_d, {"bypass": False, "trusted": set()})
        w["bypass"] = w["bypass"] or bool(c.get("bypass"))
        for k in c.get("trusted") or []:
            k = os.path.normpath(k)
            if k.startswith(home + "/") and (src_cwd == k or src_cwd.startswith(k + "/")):
                w["trusted"].add(k)
    for src_d, w in sorted(wanted.items()):
        td = rw.path(src_d)
        if not os.path.isdir(td):
            log(f"  consents: {td} does not exist here; they wait for it")
            continue
        if w["bypass"]:
            path = os.path.join(td, "settings.json")
            try:
                with open(path) as fh:
                    doc = json.load(fh) or {}
            except FileNotFoundError:
                doc = {}
            bypass = "skipDangerousModePermissionPrompt"
            if doc.get(bypass) is False:
                log(f"  kept the owner's false for {bypass} in {td}")
            elif bypass not in doc:
                done.append((td, "bypass"))
                if not dry_run:
                    doc[bypass] = True
                    _write_json_600(path, doc)
        trusted = [rw.path(k) for k in sorted(w["trusted"])]
        if not trusted:
            continue
        path = of_capacity.account_file(td, home=rw.new_home)
        try:
            with open(path) as fh:
                conf = json.load(fh) or {}
        except FileNotFoundError:
            log(f"  consents: {td} is not signed in yet; folder trust waits (run restore again after the sign-in)")
            continue
        changed = False
        for tk in trusted:
            if not os.path.isdir(tk):
                log(f"  consents: {tk} does not exist here; its trust is not carried")
                continue
            entry = conf.setdefault("projects", {}).setdefault(tk, {})
            if entry.get("hasTrustDialogAccepted") is False:
                log(f"  kept the owner's false for hasTrustDialogAccepted in {tk}")
            elif "hasTrustDialogAccepted" not in entry:
                entry["hasTrustDialogAccepted"] = True
                changed = True
                done.append((td, "trust " + tk))
        if changed and not dry_run:
            _write_json_600(path, conf)
    for td, what in done:
        log(f"  consents{' (dry run)' if dry_run else ''}: {td}: {what}")
    return done


def prepare(positions, only, out_dir, log=print, entries=None, tmp_root=None):
    """Write the bundle for the live sessions of the organizations or positions in `only`."""
    if not only:
        raise CarryError("carry prepare needs --only <organization|position,…>: a machine carries one organization")
    entries = selected(entries if entries is not None else of_capacity.live_entries(positions, log=log), only)
    if not entries:
        raise CarryError(f"no live session of {', '.join(sorted(only))} on this machine")
    orgs = {e.get("org") for e in entries}
    if len(orgs) != 1 or None in orgs:
        raise CarryError(f"one organization per bundle, and only positions' sessions; these are of "
                         f"{', '.join(sorted(str(o) for o in orgs))}")
    org = orgs.pop()
    tmp_root = tmp_root or of_capacity.agent_tmp_root()
    import of_memory
    by_name = {p["name"]: p for p in positions}
    files, skipped = [], []
    for e in entries:
        p = by_name.get(e.get("position") or "")
        mem = e.get("memory_dir") or (of_memory.directory(p) if p else None)
        e["memory_dir"] = mem if mem and os.path.isdir(mem) else e.get("memory_dir")
        e["account"] = of_capacity.account_of(e["config_dir"])
        f, s = session_files(e, tmp_root, e["memory_dir"])
        files += f
        skipped += s
    pats = custody_paths()
    named = []
    files = drop_custody(sorted(set(files)), skipped, pats, log=log)
    files = _drop_copies(files, pats, skipped, log, named=named)
    size = sum(os.path.getsize(f) for f in files if os.path.exists(f))
    plan = {"kind": "carry", "v": 1, "org": org, "prepared_at": of_capacity._iso(time.time()),
            "machine": socket.gethostname(),
            "home": os.path.expanduser("~"), "uid": os.getuid(), "tmp_root": tmp_root, "only": sorted(only),
            "entries": entries, "files": len(files), "bytes": size, "skipped": skipped, "named": named,
            "consents": consents(entries)}
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    _write_private(os.path.join(out_dir, "plan.json"), json.dumps(plan, indent=1, ensure_ascii=False))
    _write_private(os.path.join(out_dir, "files.txt"), "".join(f + "\n" for f in files))
    for e in entries:
        log(f"{e['name']:28} {of_capacity.short(e['config_dir']):26} {e.get('account') or '?':30} {e['model'] or '?'}")
    log(f"{len(entries)} session(s), {len(files)} file(s), {size / 1e6:.0f} MB in {out_dir}"
        + (f", {len(skipped)} left behind (named in plan.json)" if skipped else ""))
    return plan


def _write_private(path, text):
    """A file only its owner reads (0600), whatever the umask: the plan travels to a host of several users."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


# ------------------------------------------------------------------ copy


def _key_ok(key):
    if not key:
        raise CarryError("carry copy needs --key <file>: an explicit key file, never the owner's SSH agent")
    key = os.path.expanduser(key)
    try:
        st = os.stat(key)
    except OSError:
        raise CarryError(f"no key file {key}")
    if st.st_mode & 0o077:
        raise CarryError(f"{key} is readable by others (mode {oct(st.st_mode & 0o777)}); chmod 600 it")
    return key


def background(which=shutil.which):
    """The wrapper that runs a copy at background priority on this machine: `taskpolicy -b` on macOS, `nice` with
    `ionice -c3` (idle I/O) on Linux, `nice` alone elsewhere. of_lab's rehearsal on a Linux VM, 2026-11-15: a fixed
    `taskpolicy -b` failed at any Linux end, since the copy runs on whichever machine pulls or pushes."""
    if which("taskpolicy"):
        return ["taskpolicy", "-b"]
    if which("ionice"):
        return ["nice", "-n", "19", "ionice", "-c3"]
    return ["nice", "-n", "19"]


def copy_commands(bundle, key, to=None, frm=None, bwlimit=BWLIMIT_KBPS, wrapper=None):
    """Pure: the rsync commands of a copy, push (`to`, user@host:DIR) or pull (`frm`, user@host:DIR prepared there),
    each at background priority (`wrapper`, else this machine's `background()`) with the bandwidth limit, over ssh
    with only `key`."""
    if bool(to) == bool(frm):
        raise CarryError("carry copy needs exactly one of --to user@host:DIR (push) or --from user@host:DIR (pull)")
    ssh = " ".join(["ssh", "-i", shlex.quote(key)] + SSH_OPTS)
    # --chmod=go-rwx: on a host of one OS user per organization nothing arrives readable by another user; the
    # owner's bits stay, so a script stays executable (of_server on PR 116)
    base = list(background() if wrapper is None else wrapper) + [
        "rsync", "-a", "--chmod=go-rwx", "--partial", f"--bwlimit={int(bwlimit)}", "-e", ssh]
    b = bundle.rstrip("/")
    if to:
        dest = to.rstrip("/")
        return [base + [f"{b}/plan.json", f"{b}/files.txt", f"{dest}/"],
                base + ["-R", f"--files-from={b}/files.txt", "/", f"{dest}/files/"]]
    src = frm.rstrip("/")
    return [base + [f"{src}/plan.json", f"{b}/"], base + [f"{src}/files.txt", f"{b}/"],
            base + ["-R", f"--files-from={b}/files.txt", f"{src.split(':', 1)[0]}:/", f"{b}/files/"]]


def _source_patterns(bundle, pats):
    """Match a pulled list in the home where it was prepared."""
    plan = os.path.join(bundle, "plan.json")
    try:
        with open(plan) as fh:
            source = json.load(fh)["home"]
        if not isinstance(source, str) or not os.path.isabs(source):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError):
        raise CarryError(f"cannot read the source home from {plan}") from None
    home = os.path.expanduser("~")
    result = []
    for org, cid, pattern in pats:
        if pattern == home:
            pattern = source
        elif pattern.startswith(home.rstrip("/") + "/"):
            pattern = os.path.join(source, os.path.relpath(pattern, home))
        result.append((org, cid, pattern))
    return result


def _check_received(bundle, pats, log):
    root = os.path.join(bundle, "files")
    if os.path.islink(root):
        raise CarryError(f"cannot check received files through a link: {root}")
    files = (os.path.join(directory, name) for directory, _, names in os.walk(root) for name in names)
    removed = 0
    for path, reason in _content_matches(files, pats, log, root=root):
        os.remove(path)
        removed += 1
        log(f"  removed ({reason}): {path}")
    if removed:
        raise CarryError(f"carry removed {removed} received file(s) after checking their contents")


def copy(bundle, key, to=None, frm=None, bwlimit=BWLIMIT_KBPS, dry_run=False, log=print, run=subprocess.run,
         pats=None):
    key = _key_ok(key)
    cmds = copy_commands(bundle, key, to=to, frm=frm, bwlimit=bwlimit)
    env = dict(os.environ, SSH_AUTH_SOCK="", OF_AUTOMATION="1")
    os.makedirs(bundle, mode=0o700, exist_ok=True)
    pats = custody_paths() if pats is None else pats
    files_txt = os.path.join(bundle, "files.txt")
    for i, c in enumerate(cmds):
        # the list the file copy reads is filtered first: before a push's first command, after a pull fetched it
        if to and i == 0:
            filter_files_list(files_txt, pats, log=log, check_contents=True)
        if frm and i == len(cmds) - 1 and (not dry_run or os.path.isfile(files_txt)):
            filter_files_list(files_txt, _source_patterns(bundle, pats), log=log)
        log("$ " + " ".join(c))
        if dry_run:
            continue
        r = run(c, env=env)
        if frm and i == len(cmds) - 1:
            _check_received(bundle, pats, log)
        if getattr(r, "returncode", 0) != 0:
            raise CarryError(f"rsync exited {r.returncode}; run the same copy again to go on (--partial keeps what came)")
    return cmds


# ------------------------------------------------------------------ restore

_SID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_CONFIG_NAME = re.compile(r"\.claude(-[A-Za-z0-9._-]+)?")


def planned(plan, root, org, positions, only=None):
    """Pure but for listing the bundle: a test of a source path, true only for what the plan's entries account for
    (of_review on PR 116: a bundle holding a shell profile, a LaunchAgent, Claude settings and the guard's cache, with
    no entries, had all four installed; a rollback pulls bundles an organization's own OS user prepared). Per entry
    of organization `org` (named by whoever restores) and of a well-formed session id, whose position this machine's
    registry (`positions`) has in `org`: its state in its config dir (a direct child of the home named .claude or
    .claude-*); its memory directory, only when it is exactly that position's (of_memory.directory), so a crafted
    entry cannot write into another organization's or position's memory; and its scratch folder
    <tmp root>/<one folder>/<session>/. A project's folder memory never. `only` narrows it to the named entries."""
    import of_memory
    home = os.path.normpath(plan["home"])
    by_name = {p["name"]: p for p in positions or []}
    tmp = os.path.normpath(plan.get("tmp_root") or f"/private/tmp/claude-{plan.get('uid')}")
    if tmp not in (f"/private/tmp/claude-{plan.get('uid')}", f"/tmp/claude-{plan.get('uid')}") \
            and not tmp.startswith(home + "/"):
        tmp = None  # the source's own agent tmp root on macOS or Linux, or a rehearsal's inside its home
    exact, prefixes, sids = set(), [], set()
    for e in plan.get("entries") or []:
        if only and e.get("name") not in only and (e.get("position") or "") not in only:
            continue
        sid, p = e.get("session") or "", by_name.get(e.get("position") or "")
        if not _SID.fullmatch(sid) or e.get("org") != org or not p or p.get("_org") != org:
            continue
        sids.add(sid)
        cfg = os.path.normpath(e.get("config_dir") or "/")
        if os.path.dirname(cfg) == home and _CONFIG_NAME.fullmatch(os.path.basename(cfg)):
            pdir = os.path.join(root, cfg.lstrip("/"), "projects")
            keys = [k for k in (os.listdir(pdir) if os.path.isdir(pdir) else [])
                    if os.path.isfile(os.path.join(pdir, k, f"{sid}.jsonl"))]
            for k in keys:
                exact.add(f"{cfg}/projects/{k}/{sid}.jsonl")
                prefixes.append(f"{cfg}/projects/{k}/{sid}/")
            for sub in ("file-history", "session-env", "tasks", "todos"):
                exact.add(f"{cfg}/{sub}/{sid}")
                prefixes.append(f"{cfg}/{sub}/{sid}/")
        mem = os.path.normpath(e.get("memory_dir") or "/")
        if mem == os.path.normpath(of_memory.directory(p, base=os.path.join(home, ".local", "state", "of", "memory"))):
            prefixes.append(mem + "/")
    scratch = re.compile(re.escape(tmp) + r"/[^/]+/(" + "|".join(sorted(sids)) + r")/") if tmp and sids else None
    return lambda p: p in exact or any(p.startswith(x) for x in prefixes) or bool(scratch and scratch.match(p))


# A session's own state in a config dir whose text names paths: session-env holds the shell exports its Bash tool
# sources, tasks and todos the lists it keeps. They are rewritten as text. file-history is not: each file there is a
# snapshot of an edited file's contents, which /rewind writes back as it was; its index, path by path, is in the
# transcript's file-history-snapshot records (their keys and realParentDir), which `record` rewrites. Nor are scratch
# files and memory, which are the session's own work (of_lab's rehearsal, 2026-11-15).
_STATE_TEXT = re.compile(r"/\.claude[^/]*/(session-env|tasks|todos)/")


def _state_text(path):
    return bool(_STATE_TEXT.search(path))


def install(bundle, rw, dry_run=False, log=print, allowed=None):
    """Put the bundle's files that `allowed` accounts for at their rewritten paths; refuse and name the rest. A file
    already there with other contents is kept aside under the bundle's `kept/` first. Transcripts (.jsonl under
    projects/) are rewritten record by record, and the session's state text (_STATE_TEXT) as text. A link in a bundle
    is never followed."""
    root = os.path.join(bundle, "files")
    done = {"installed": 0, "same": 0, "kept_aside": 0}
    for dirpath, dirs, files in os.walk(root):
        for f in files:
            src = os.path.join(dirpath, f)
            old = "/" + os.path.relpath(src, root)
            if not stat.S_ISREG(os.lstat(src).st_mode) or (allowed is not None and not allowed(old)):
                done.setdefault("refused", []).append(old)
                continue
            new = rw.path(old)
            if not (new.startswith(rw.new_home + "/") or new.startswith(rw.new_tmp + "/")):
                done.setdefault("refused", []).append(old)  # only into this user's home and tmp root
                continue
            with open(src, "rb") as fh:
                data = fh.read()
            if new.endswith(".jsonl") and "/projects/" in new:
                data = rw.transcript(data)
            elif _state_text(new) and rw.touches(data):
                try:
                    data = rw.text(data.decode("utf-8")).encode("utf-8")
                except UnicodeDecodeError:
                    pass  # not text: as it is
            try:
                with open(new, "rb") as fh:
                    there = fh.read()
            except OSError:
                there = None
            if there == data:
                done["same"] += 1
                continue
            if dry_run:
                done["installed"] += 1
                continue
            if there is not None:
                aside = os.path.join(bundle, "kept", new.lstrip("/"))
                os.makedirs(os.path.dirname(aside), mode=0o700, exist_ok=True)
                shutil.copy2(new, aside)
                done["kept_aside"] += 1
            os.makedirs(os.path.dirname(new), mode=0o700, exist_ok=True)
            with open(new, "wb") as fh:
                fh.write(data)
            shutil.copystat(src, new)
            os.chmod(new, stat.S_IMODE(os.lstat(src).st_mode) & 0o700)  # the owner's bits only
            done["installed"] += 1
    log(f"files: {done['installed']} put in place{' (dry run)' if dry_run else ''}, {done['same']} already there, "
        f"{done['kept_aside']} earlier copies kept aside in {os.path.join(bundle, 'kept')}"
        + (f"; {len(done['refused'])} NOT put in place (no entry of the plan accounts for them, a link, or outside "
           f"this user's home and tmp root):" if done.get("refused") else ""))
    for p in (done.get("refused") or [])[:20]:
        log(f"  refused: {p}")
    return done


def local_plan(plan, rw):
    """The plan with this machine's paths, and no pane ids: another machine's herdr numbers its panes its own way."""
    out = dict(plan, home=plan.get("home"), target_home=rw.new_home, tmp_root=rw.path(plan.get("tmp_root") or ""))
    out["entries"] = [dict(e, config_dir=rw.path(e["config_dir"]), cwd=rw.path(e.get("cwd") or "") or None,
                           memory_dir=rw.path(e["memory_dir"]) if e.get("memory_dir") else None, pane=None)
                      for e in plan["entries"]]
    return out


def restore(bundle, positions, org, only=None, dry_run=False, log=print, of_bin=None, place_of=None,
            home=None, uid=None, account_of=None, restore_fn=None, consents_card=None, cards=None, conn=None,
            tmp_root=None):
    """Rewrite and install the bundle's files for this user, then resume its sessions (of_capacity.reboot_restore).
    `org` is the organization the caller expects the bundle to carry; entries of any other are neither put in place
    nor resumed. A session whose config dir here is signed in to another account than it ran on is not resumed."""
    if not org:
        raise CarryError("carry restore needs --org <organization>: the one the bundle is expected to carry")
    try:
        with open(os.path.join(bundle, "plan.json")) as fh:
            plan = json.load(fh)
    except (OSError, ValueError) as err:
        raise CarryError(f"no carry plan in {bundle}: {err}")
    if plan.get("kind") != "carry":
        raise CarryError(f"{bundle}/plan.json is not a carry plan")
    if plan.get("org") != org:
        raise CarryError(f"{bundle} carries {plan.get('org') or 'no organization'}, not {org}")
    rw = Rewriter(plan["home"], home or os.path.expanduser("~"), plan["uid"], os.getuid() if uid is None else uid,
                  old_tmp=plan.get("tmp_root"), new_tmp=tmp_root)
    log(f"from {plan.get('machine')} ({plan['home']}, uid {plan['uid']}) to {rw.new_home} (uid "
        f"{os.getuid() if uid is None else uid}); prepared {plan.get('prepared_at')}")
    install(bundle, rw, dry_run=dry_run, log=log,
            allowed=planned(plan, os.path.join(bundle, "files"), org, positions, only))
    card = None
    if consents_card:  # the folders' trust and the bypass, on the owner's yes only; applied below, before any resume
        import of_pulse
        keeper = of_pulse.structure_keeper(positions or [], org)
        card = owner_yes(consents_card, cards, conn, keeper=keeper)
        if not card:
            raise CarryError(f"--consents {consents_card}: not the owner's own yes (its first option) to carrying folder "
                             f"trust and the Bypass Permissions acceptance, on an unamended card an OF position or "
                             f"{org}'s structure keeper ({keeper or 'none in the registry'}) filed; nothing carried")
    elif plan.get("consents"):
        log("  consents: not carried (no --consents <card>): a folder no config dir here trusts is refused and named")
    lp = local_plan(plan, rw)
    if card:
        lp["consents_card"] = {"card": card.get("ref"), "event": card.get("event_id"), "answer": card.get("closed_by")}
    account_of = account_of or of_capacity.account_of
    ok, wrong = set(), []
    for e in lp["entries"]:
        if only and e["name"] not in only and (e.get("position") or "") not in only:
            continue
        if e.get("org") != org:
            log(f"{e['name']}: NOT resumed: of {e.get('org')}, not {org}")
            continue
        here = account_of(e["config_dir"])
        if e.get("account") and here != e["account"]:
            wrong.append(e["name"])
            log(f"{e['name']}: NOT resumed: {of_capacity.short(e['config_dir'])} here is "
                f"{here or 'signed in to no account'}, it ran on {e['account']}")
            continue
        ok.add(e["name"])
    if card:  # only the entries accepted here: planned()'s checks, --only, the account (of_review, PR 339)
        by_name = {p["name"]: p for p in positions or []}
        home_src = os.path.normpath(plan["home"])
        accepted = [e for e in plan.get("entries") or [] if e.get("name") in ok and _SID.fullmatch(e.get("session") or "")
                    and e.get("org") == org and (by_name.get(e.get("position") or "") or {}).get("_org") == org
                    and os.path.dirname(os.path.normpath(e.get("config_dir") or "/")) == home_src
                    and _CONFIG_NAME.fullmatch(os.path.basename(os.path.normpath(e.get("config_dir") or "/")))]
        carry_consents(plan, rw, accepted, dry_run=dry_run, log=log)
    path = os.path.join(bundle, "plan.local.dry-run.json" if dry_run else "plan.local.json")
    _write_private(path, json.dumps(lp, indent=1, ensure_ascii=False))
    if not ok:
        return [(n, "failed", "another account") for n in wrong]
    done = (restore_fn or of_capacity.reboot_restore)(positions, only=ok, dry_run=dry_run, log=log, of_bin=of_bin,
                                                       plan_path=path, place_of=place_of)
    return list(done) + [(n, "failed", "another account") for n in wrong]
