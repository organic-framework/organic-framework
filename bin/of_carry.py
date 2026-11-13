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
  empty and the user's SSH agent refused, at background priority (`taskpolicy -b`) and a bandwidth limit. It pushes a
  bundle to the other machine, or pulls one prepared there.
- `restore` (the target, as the organization's user): every path rewritten from the source's home and uid to this
  user's (`Rewriter`), the files put in place (whatever is there is kept aside in the bundle first), then each session
  resumed as it ran by `of_capacity.reboot_restore`, on an account checked to be the same one.

A transcript's conversation (`message`) is never rewritten: its thinking blocks are signed, and an edit there makes
the API refuse the session. Everything around it is (the working folder, file-history paths, tool-result metadata),
and the resume note tells the session its home moved.
"""

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

    def __init__(self, old_home, new_home, old_uid, new_uid):
        self.old_home, self.new_home = old_home.rstrip("/"), new_home.rstrip("/")
        self.old_key, self.new_key = key_of(self.old_home), key_of(self.new_home)
        self.old_tmp, self.new_tmp = f"/private/tmp/claude-{old_uid}", f"/private/tmp/claude-{new_uid}"
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
            if st.st_size > SKIP_LARGER and ("/private/tmp/" in p or "/tasks/" in p):
                skipped.append({"path": p, "bytes": st.st_size, "reason": "over the size limit"})
                continue
            out.append(p)
    return out


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
    files = sorted(set(files))
    size = sum(os.path.getsize(f) for f in files if os.path.exists(f))
    plan = {"kind": "carry", "v": 1, "org": org, "prepared_at": of_capacity._iso(time.time()),
            "machine": socket.gethostname(),
            "home": os.path.expanduser("~"), "uid": os.getuid(), "tmp_root": tmp_root, "only": sorted(only),
            "entries": entries, "files": len(files), "bytes": size, "skipped": skipped}
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    _write_private(os.path.join(out_dir, "plan.json"), json.dumps(plan, indent=1, ensure_ascii=False))
    _write_private(os.path.join(out_dir, "files.txt"), "".join(f + "\n" for f in files))
    for e in entries:
        log(f"{e['name']:28} {of_capacity.short(e['config_dir']):26} {e.get('account') or '?':30} {e['model'] or '?'}")
    secret = sum(1 for x in skipped if x.get("reason") == "secret path")
    log(f"{len(entries)} session(s), {len(files)} file(s), {size / 1e6:.0f} MB in {out_dir}"
        + (f"; left behind: {secret} at secret paths, {len(skipped) - secret} over {SKIP_LARGER >> 20} MB "
           f"(named in plan.json)" if skipped else ""))
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


def copy_commands(bundle, key, to=None, frm=None, bwlimit=BWLIMIT_KBPS):
    """Pure: the rsync commands of a copy, push (`to`, user@host:DIR) or pull (`frm`, user@host:DIR prepared there),
    each at background priority with the bandwidth limit, over ssh with only `key`."""
    if bool(to) == bool(frm):
        raise CarryError("carry copy needs exactly one of --to user@host:DIR (push) or --from user@host:DIR (pull)")
    ssh = " ".join(["ssh", "-i", shlex.quote(key)] + SSH_OPTS)
    # --chmod=go-rwx: on a host of one OS user per organization nothing arrives readable by another user; the
    # owner's bits stay, so a script stays executable (of_server on PR 116)
    base = ["taskpolicy", "-b", "rsync", "-a", "--chmod=go-rwx", "--partial", f"--bwlimit={int(bwlimit)}", "-e", ssh]
    b = bundle.rstrip("/")
    if to:
        dest = to.rstrip("/")
        return [base + [f"{b}/plan.json", f"{b}/files.txt", f"{dest}/"],
                base + ["-R", f"--files-from={b}/files.txt", "/", f"{dest}/files/"]]
    src = frm.rstrip("/")
    return [base + [f"{src}/plan.json", f"{b}/"], base + [f"{src}/files.txt", f"{b}/"],
            base + ["-R", f"--files-from={b}/files.txt", f"{src.split(':', 1)[0]}:/", f"{b}/files/"]]


def copy(bundle, key, to=None, frm=None, bwlimit=BWLIMIT_KBPS, dry_run=False, log=print, run=subprocess.run):
    key = _key_ok(key)
    cmds = copy_commands(bundle, key, to=to, frm=frm, bwlimit=bwlimit)
    env = dict(os.environ, SSH_AUTH_SOCK="", OF_AUTOMATION="1")
    os.makedirs(bundle, mode=0o700, exist_ok=True)
    for c in cmds:
        log("$ " + " ".join(c))
        if dry_run:
            continue
        r = run(c, env=env)
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
    if tmp != f"/private/tmp/claude-{plan.get('uid')}" and not tmp.startswith(home + "/"):
        tmp = None
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


def install(bundle, rw, dry_run=False, log=print, allowed=None):
    """Put the bundle's files that `allowed` accounts for at their rewritten paths; refuse and name the rest. A file
    already there with other contents is kept aside under the bundle's `kept/` first. Transcripts (.jsonl under
    projects/) are rewritten record by record. A link in a bundle is never followed."""
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
            home=None, uid=None, account_of=None, restore_fn=None):
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
    rw = Rewriter(plan["home"], home or os.path.expanduser("~"), plan["uid"], os.getuid() if uid is None else uid)
    log(f"from {plan.get('machine')} ({plan['home']}, uid {plan['uid']}) to {rw.new_home} (uid "
        f"{os.getuid() if uid is None else uid}); prepared {plan.get('prepared_at')}")
    install(bundle, rw, dry_run=dry_run, log=log,
            allowed=planned(plan, os.path.join(bundle, "files"), org, positions, only))
    lp = local_plan(plan, rw)
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
    path = os.path.join(bundle, "plan.local.dry-run.json" if dry_run else "plan.local.json")
    _write_private(path, json.dumps(lp, indent=1, ensure_ascii=False))
    if not ok:
        return [(n, "failed", "another account") for n in wrong]
    done = (restore_fn or of_capacity.reboot_restore)(positions, only=ok, dry_run=dry_run, log=log, of_bin=of_bin,
                                                       plan_path=path, place_of=place_of)
    return list(done) + [(n, "failed", "another account") for n in wrong]
