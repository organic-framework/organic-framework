#!/usr/bin/env python3
"""lab-agent-host-v0 check: rehearse B1 (cross-machine carry with path rewrite into another OS user) between
two OS users on one Linux VM, and assert A1..A6 + A3b of the scenario README.

What this proves, and the two fidelity limits it does not (stated in the findings, not worked around):
  - Proven here, runnable with no credentials: prepare's bundle and its skips (A1); the copy leg's rsync
    contract and a real loopback-SSH transfer (A2); the rewrite and install across two real homes and uids,
    /home/alex <-> /home/of, with the conversation (`message`) kept byte for byte and a conflict kept aside
    (A3); the organization/registry guard (A3b); the account guard (A6); and the resume SELECTION with the
    rewritten, pane-cleared local plan, in both directions (A4/A5, to the launch boundary).
  - NOT here (needs a real signed-in account and the API): the actual Claude Code boot on resume and the
    of.capacity_carry_restored / of.holder_changed events it emits. of_carry.restore reaches that through
    of_capacity.reboot_restore; this check injects a restore_fn in its place and asserts what is selected and
    the local plan handed to it. The boot and its events are validated at the host's S2 with real accounts.
  - NOT here (macOS-shaped): deploy/host/setup.py, the /private/tmp/claude-<uid> scratch root and /Users
    homes. On Linux the home-and-key rewrite runs; the /private/tmp uid-rewrite substring never appears, so a
    scratch folder is carried through the home rule instead (a tmp_root under the home). Validated at S2.

Run inside the VM after setup.sh, as a user with passwordless sudo to alex and of:
    python3 check.py            # forward (alex->of) and reverse (of->alex); exit 1 on any failure

The orchestrator (this process) re-execs itself under alex and of for the phases that must own their files;
state passes on disk through the per-user bundle directories. Each phase prints `RESULT <id> PASS|FAIL ...`.
"""
import json
import os
import subprocess
import sys

TREE = "/usr/local/of-cur/bin"
ORG = "acme-lab"
OTHER_ORG = "other-lab"      # a neutral second org for the guard; never a real user organization's name
POS = "acme_worker"
SID = "11111111-2222-7333-8444-555555555555"
# one claude.ai account travels with the session (the OS user changes, the account does not); resume succeeds
# only when the target config dir is signed in to this same account (A6 guards the mismatch).
SESSION_ACCOUNT = "session-acct@lab"
BIG = 65 * 1024 * 1024   # over of_carry.SKIP_LARGER (64 MiB): a task file left behind and named in the plan

USERS = {"alex": ("/home/alex", 1001), "of": ("/home/of", 1002)}


# --------------------------------------------------------------------------- shared helpers (both phases)

def position(org):
    """The target registry's view of the carried position, as of_carry.planned/restore read it."""
    return {"name": POS, "_org": org, "_orgid": org}


def load_of_carry():
    sys.path.insert(0, TREE)
    import of_carry  # noqa: E402
    return of_carry


def emit(rid, ok, detail=""):
    print(f"RESULT {rid} {'PASS' if ok else 'FAIL'} {detail}")
    return ok


# --------------------------------------------------------------------------- phase: build + prepare (as source)

def phase_build(home, uid, bundle):
    """Create a synthetic resumable session under this user's home, then prepare the carry bundle. Asserts A1."""
    oc = load_of_carry()
    cfg = os.path.join(home, ".claude")
    cwd = os.path.join(home, "proj")
    key = oc.key_of(cwd)
    scratch_root = os.path.join(home, "scratch")        # a Linux stand-in for the macOS /private/tmp root
    mem_dir = os.path.join(home, ".local", "state", "of", "memory", ORG, POS)
    user = os.path.basename(home)

    # the project folder (the session's cwd), and a transcript that mentions this home in a rewritten field
    # (cwd) and in an un-rewritten one (the conversation `message`), so A3 can prove `message` is preserved.
    os.makedirs(cwd, exist_ok=True)
    tdir = os.path.join(cfg, "projects", key)
    os.makedirs(tdir, exist_ok=True)
    recs = [
        {"type": "summary", "cwd": cwd, "sessionId": SID},
        {"type": "user", "cwd": cwd,
         "message": {"role": "user", "content": f"my home is {home} and it stays {home}"},
         "toolUseResult": {"filePath": os.path.join(cwd, "a.txt")}},
    ]
    transcript = os.path.join(tdir, f"{SID}.jsonl")
    with open(transcript, "w") as fh:
        fh.write("\n".join(json.dumps(r) for r in recs) + "\n")
    for sub in ("file-history", "session-env", "todos"):
        os.makedirs(os.path.join(cfg, sub), exist_ok=True)
        with open(os.path.join(cfg, sub, SID), "w") as fh:
            fh.write(json.dumps({"path": os.path.join(cwd, "a.txt"), "home": home}))
    # tasks/<sid>/ is a directory so a >64 MiB task file is found by _files_under and left behind (A1 skip)
    tdirs = os.path.join(cfg, "tasks", SID)
    os.makedirs(tdirs, exist_ok=True)
    with open(os.path.join(tdirs, "small.out"), "w") as fh:
        fh.write("task output under " + home)
    with open(os.path.join(tdirs, "big.out"), "wb") as fh:
        fh.truncate(BIG)
    # the account marker (account_of) and the folder trust (for a later herdr layer; the account guard here
    # is driven by an injected account_of, not this file)
    with open(os.path.join(home, ".claude.json"), "w") as fh:
        json.dump({"oauthAccount": {"emailAddress": SESSION_ACCOUNT},
                   "projects": {key: {"hasTrustDialogAccepted": True}}}, fh)
    # the position's own memory directory (carried; a project's folder memory is not)
    os.makedirs(mem_dir, exist_ok=True)
    with open(os.path.join(mem_dir, "MEMORY.md"), "w") as fh:
        fh.write(f"- [note](n.md) memory of {POS}, written under {home}\n")
    os.makedirs(scratch_root, exist_ok=True)
    sdir = os.path.join(scratch_root, key, SID)
    os.makedirs(sdir, exist_ok=True)
    with open(os.path.join(sdir, "scratch.txt"), "w") as fh:
        fh.write("scratch under " + home)

    entry = {"name": POS, "session": SID, "config_dir": cfg, "org": ORG, "position": POS,
             "model": "claude-opus-4-8", "cwd": cwd, "workspace": "acme", "tab": "worker",
             "bypass": True, "memory_dir": mem_dir}
    plan = oc.prepare([position(ORG)], {ORG}, bundle, log=lambda *_: None, entries=[entry],
                      tmp_root=scratch_root)

    files = open(os.path.join(bundle, "files.txt")).read().splitlines()
    skipped = {s.get("path", ""): s for s in plan.get("skipped", [])}
    ok = True
    ok &= emit("A1-plan", plan.get("kind") == "carry" and plan.get("org") == ORG
               and os.path.normpath(plan.get("home")) == os.path.normpath(home) and plan.get("uid") == uid,
               f"kind/org/home/uid: {plan.get('kind')},{plan.get('org')},{plan.get('home')},{plan.get('uid')}")
    ok &= emit("A1-files", transcript in files and any(mem_dir in f for f in files)
               and any(sdir in f for f in files) and any("small.out" in f for f in files),
               f"{len(files)} files incl transcript+memory+scratch")
    big = os.path.join(tdirs, "big.out")
    ok &= emit("A1-skip-big", big in skipped and big not in files
               and skipped[big].get("reason") == "over the size limit",
               f"big task file left behind: {big in skipped}")
    return 0 if ok else 1


# --------------------------------------------------------------------------- phase: restore (as target)

def phase_restore(home, uid, bundle, src_home):
    """Rewrite+install and resume-select the bundle for this user; drive the org and account guards. A3/A3b/A6/A4."""
    oc = load_of_carry()
    user = os.path.basename(home)
    cfg = os.path.join(home, ".claude")
    key_src = oc.key_of(os.path.join(src_home, "proj"))
    key_dst = oc.key_of(os.path.join(home, "proj"))
    transcript = os.path.join(cfg, "projects", key_dst, f"{SID}.jsonl")

    calls = {}

    def restore_fn(positions, only=None, dry_run=False, log=None, of_bin=None, plan_path=None, place_of=None):
        calls["only"] = sorted(only or [])
        calls["plan_path"] = plan_path
        return [(n, "resumed", None) for n in (only or [])]

    def account_is(value):
        return lambda d, home=None: value

    ok = True

    # The variants run in an order that lets each prove one thing cleanly: the two guards that must fire BEFORE
    # any successful install come first, then a planted conflict, then the account-match install+resume, then
    # the account mismatch. (restore() installs before it checks the account, so a later successful install
    # would hide the registry guard and the kept-aside.)

    # A3b part 1: a bundle restored under the wrong --org is refused outright, before any install.
    try:
        oc.restore(bundle, [position(OTHER_ORG)], OTHER_ORG, home=home, uid=uid,
                   account_of=account_is(SESSION_ACCOUNT), restore_fn=restore_fn, log=lambda *_: None)
        ok &= emit("A3b-org", False, "wrong --org was NOT refused")
    except oc.CarryError as e:
        ok &= emit("A3b-org", ("not " + OTHER_ORG) in str(e) or ORG in str(e), f"refused: {e}")

    # A3b part 2: the right --org but a target registry that lacks the position -> its files are not installed
    # (the real guard is on the install side: planned() accounts only for positions the registry has in org).
    oc.restore(bundle, [], ORG, home=home, uid=uid,
               account_of=account_is(SESSION_ACCOUNT), restore_fn=restore_fn, log=lambda *_: None)
    ok &= emit("A3b-registry", not os.path.exists(transcript),
               f"position absent from registry -> installed={os.path.exists(transcript)}")

    # plant a different file where the rewritten transcript will land, so install keeps it aside (A3-kept-aside).
    os.makedirs(os.path.dirname(transcript), exist_ok=True)
    with open(transcript, "w") as fh:
        fh.write("an older copy that was already here\n")

    # A3 + A4: the matching account -> rewrite, install, and resume-select with the rewritten local plan.
    calls.clear()
    res = oc.restore(bundle, [position(ORG)], ORG, home=home, uid=uid,
                     account_of=account_is(SESSION_ACCOUNT), restore_fn=restore_fn, log=lambda *_: None)

    # A3: the transcript landed under THIS home's key, the conversation is byte-for-byte, paths rewritten,
    # nothing left under the source home, and a different file already there was kept aside.
    installed = os.path.exists(transcript)
    body = open(transcript).read() if installed else ""
    recs = [json.loads(l) for l in body.splitlines() if l.strip()]
    msg_kept = any(r.get("message", {}).get("content") == f"my home is {src_home} and it stays {src_home}"
                   for r in recs)                                   # `message` NOT rewritten
    cwd_rewritten = all(r.get("cwd", "").startswith(home) for r in recs if r.get("cwd"))
    # every rewritable field of the transcript moves to the new home; the only src_home left is inside the
    # preserved `message` (msg_kept asserts that). So scan the records with `message` removed.
    non_msg = "\n".join(json.dumps({k: v for k, v in r.items() if k != "message"}) for r in recs)
    tstray = src_home in non_msg
    ok &= emit("A3-rewrite", installed and msg_kept and cwd_rewritten and not tstray,
               f"installed={installed} msg_kept={msg_kept} cwd_rewritten={cwd_rewritten} nonmsg_stray={tstray}")
    # FINDING (not a gate): install rewrites the transcript's content and every file's PATH, but not the
    # CONTENT of the other session state (file-history, session-env, todos, tasks, scratch). Those files still
    # carry the source home inside them after a carry. For of_capacity to judge whether a resumed session reads
    # any of them by their embedded paths (file-history drives /rewind); the lab cannot tell from here.
    others = subprocess.run(["grep", "-rIl", src_home, cfg], capture_output=True, text=True).stdout.split()
    others = [os.path.relpath(f, cfg) for f in others if f != transcript]
    print(f"FINDING nontranscript-content {len(others)} file(s) under the config dir still contain {src_home}: "
          + ",".join(others))
    ok &= emit("A3-key", os.path.isdir(os.path.join(cfg, "projects", key_dst))
               and not os.path.isdir(os.path.join(cfg, "projects", key_src)),
               f"folder key {key_src} -> {key_dst}")
    mem = os.path.join(home, ".local", "state", "of", "memory", ORG, POS, "MEMORY.md")
    ok &= emit("A3-memory", os.path.exists(mem), f"position memory carried: {os.path.exists(mem)}")
    kept = os.path.join(bundle, "kept")
    ok &= emit("A3-kept-aside", os.path.isdir(kept) and bool(os.listdir(kept)),
               f"earlier copy kept aside: {os.path.isdir(kept)}")

    # A4: the right session selected for resume, and the local plan rewritten with no source pane id.
    lp = json.load(open(os.path.join(bundle, "plan.local.json")))
    e0 = lp["entries"][0]
    ok &= emit("A4-select", calls.get("only") == [POS], f"resume selected: {calls.get('only')}")
    ok &= emit("A4-local-plan", e0["config_dir"].startswith(home) and (e0.get("cwd") or "").startswith(home)
               and (e0.get("memory_dir") or "").startswith(home) and e0.get("pane") is None,
               f"rewritten config/cwd/memory, pane={e0.get('pane')}")

    # A6: the target config dir signed in to another account than the session ran on -> not resumed, named.
    calls.clear()
    res = oc.restore(bundle, [position(ORG)], ORG, home=home, uid=uid,
                     account_of=account_is("stranger@lab"), restore_fn=restore_fn, log=lambda *_: None)
    guard = [r for r in res if r[1] == "failed" and r[2] == "another account"]
    ok &= emit("A6-account", bool(guard) and "only" not in calls,
               f"guard={res}, resume_called={'only' in calls}")
    return 0 if ok else 1


# --------------------------------------------------------------------------- orchestrator

def sudo_phase(user, *args):
    home, uid = USERS[user]
    cmd = ["sudo", "-n", "-u", user, "-H", sys.executable, os.path.abspath(__file__), *map(str, args)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0 and r.stderr:
        sys.stderr.write(r.stderr)
    return r


def reset_user(user):
    home = USERS[user][0]
    for p in (".claude", ".claude.json", "proj", "scratch", "bundle", ".local"):
        subprocess.run(["sudo", "-n", "-u", user, "-H", "rm", "-rf", os.path.join(home, p)])
    subprocess.run(["sudo", "-n", "-u", user, "-H", "mkdir", "-p", os.path.join(home, "bundle")])


def copy_leg(src, dst):
    """A2: assert copy_commands' contract, then move the bundle over loopback SSH (taskpolicy stripped: it is a
    macOS-only wrapper, a B1 finding). Returns (ok, detail)."""
    oc = load_of_carry()
    src_home = USERS[src][0]
    key = os.path.join(src_home, ".ssh", "carry_ed25519")
    dest = f"{dst}@localhost:{USERS[dst][0]}/bundle"
    cmds = oc.copy_commands(os.path.join(src_home, "bundle"), key, to=dest)
    flat = " ".join(cmds[0] + cmds[1])
    contract = ("IdentityAgent=none" in flat and "--chmod=go-rwx" in flat and "--bwlimit=" in flat
                and key in flat and "--files-from=" in flat and cmds[0][0] == "taskpolicy")
    # run the equivalent rsync as the source user, without taskpolicy and with SSH_AUTH_SOCK empty
    ssh = f"ssh -i {key} -o IdentityAgent=none -o IdentitiesOnly=yes -o BatchMode=yes"
    b = os.path.join(src_home, "bundle")
    sh = (f"export SSH_AUTH_SOCK=; "
          f"rsync -a --chmod=go-rwx --partial -e '{ssh}' {b}/plan.json {b}/files.txt {dest}/ && "
          f"rsync -aR --chmod=go-rwx --partial --files-from={b}/files.txt -e '{ssh}' / {dest}/files/")
    r = subprocess.run(["sudo", "-n", "-u", src, "-H", "bash", "-lc", sh], capture_output=True, text=True)
    arrived = subprocess.run(["sudo", "-n", "-u", dst, "-H", "test", "-f",
                              f"{USERS[dst][0]}/bundle/plan.json"]).returncode == 0
    return contract and r.returncode == 0 and arrived, f"contract={contract} rsync_rc={r.returncode} arrived={arrived}"


def direction(src, dst):
    print(f"\n== {src} -> {dst} ==")
    reset_user(src)
    reset_user(dst)
    rc = 0
    src_home, src_uid = USERS[src]
    dst_home, dst_uid = USERS[dst]
    rc |= sudo_phase(src, "--phase", "build", "--home", src_home, "--uid", src_uid,
                     "--bundle", f"{src_home}/bundle").returncode
    ok, detail = copy_leg(src, dst)
    emit("A2-copy", ok, detail)
    rc |= 0 if ok else 1
    rc |= sudo_phase(dst, "--phase", "restore", "--home", dst_home, "--uid", dst_uid,
                     "--bundle", f"{dst_home}/bundle", "--src-home", src_home).returncode
    return rc


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase")
    ap.add_argument("--home")
    ap.add_argument("--uid", type=int)
    ap.add_argument("--bundle")
    ap.add_argument("--src-home")
    a = ap.parse_args()
    if a.phase == "build":
        return phase_build(a.home, a.uid, a.bundle)
    if a.phase == "restore":
        return phase_restore(a.home, a.uid, a.bundle, a.src_home)
    rc = 0
    rc |= direction("alex", "of")      # forward
    rc |= direction("of", "alex")      # reverse (rollback symmetry, A5)
    print("\nPASS" if rc == 0 else "\nFAIL")
    return rc


if __name__ == "__main__":
    sys.exit(main())
