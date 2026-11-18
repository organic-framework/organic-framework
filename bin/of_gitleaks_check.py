#!/usr/bin/env python3
"""A pinned gitleaks run over a folder, failing closed (RFC 0008 §3.1; an organization's subagent_harnesses entry
names it as `of:gitleaks`, for its prompt_check and its snapshot_check).

    python3 bin/of_gitleaks_check.py FOLDER
    python3 bin/of_gitleaks_check.py --repo CHECKOUT --at COMMIT [--registry positions.yaml]   (a dry run)

It runs gitleaks 8.30.1 at the path pinned for the platform (macOS arm64: /opt/homebrew/bin/gitleaks; Linux x86_64 and
aarch64: /usr/local/bin/gitleaks), checked by its SHA-256 (`gitleaks dir`: no git history) with the rules pinned in bin/rules/gitleaks-8.30.1.toml,
--redact and the tree's own allow comments and ignore files not honoured, archives opened. Exit 0 only when gitleaks
is that version, ran, wrote its report and found nothing. A finding, a scanner error, a missing report or another
version exits 1. It prints each finding as its file, line and rule id, never the match (a user organization's security
keeper's terms through its structure keeper, 2026-11-18: "a pinned gitleaks run, --no-git --redact, ... failing
closed on any finding or scanner error").
"""

import json
import os
import subprocess
import sys
import tempfile

PINNED = "8.30.1"
# the program itself, not whatever PATH finds first (~/.rvm and ~/.bun/bin come before /opt/homebrew/bin on this
# Mac, so a fake that prints 8.30.1 would pass; a user organization's security keeper, 2026-11-18): its path and the
# SHA-256 of the file it resolves to
# Pinned per platform; any other platform fails closed. The Linux binaries are the release's own: their tarballs'
# SHA-256 equal gitleaks_8.30.1_checksums.txt (linux_x64 551f6fc8..., linux_arm64 e4a487ee...), checked by of_platform
# on 2026-11-18, and the hashes below are of the gitleaks file inside each. A Linux runner (a user organization's pods)
# installs that file at /usr/local/bin/gitleaks.
PINS = {
    ("darwin", "arm64"): ("/opt/homebrew/bin/gitleaks",
                          "f414bc2fb952be6c9072b75cb411e3368614ef4b16d48dbd9ad238034afd2302"),   # Homebrew 8.30.1
    ("linux", "x86_64"): ("/usr/local/bin/gitleaks",
                          "88f91962aa2f93ac6ab281d553b9e125f5197bbbce38f9f2437f7299c32e5509"),
    ("linux", "aarch64"): ("/usr/local/bin/gitleaks",
                           "00e91bbe655bd7c47753e8cfe61cb76ea1a5d7e7702fe161ee40102b46b3823b"),
}


def pin(system=None, machine=None):
    """(path, SHA-256) of the pinned gitleaks for this platform, or None."""
    import platform
    system = system or sys.platform
    machine = machine or platform.machine()
    if system.startswith("linux"):
        return PINS.get(("linux", {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)))
    return PINS.get((system, machine))


PINNED_PATH, PINNED_SHA256 = pin() or (None, None)
# the one allow rule on top of the pinned rules: a bare EVM address (0x and 40 hex digits) is public, and gitleaks'
# generic-api-key takes every one of them for a key, so a trading repository always failed (12 false findings in a user
# organization's first run, its security keeper's reading, 2026-11-18). Only that rule, only the whole value: 64 hex
# digits (which can be a private key) still fail. Checked live on 8.30.1: two addresses pass, a 64-hex value is caught.
ALLOW = """
[[allowlists]]
description = "a bare EVM address (0x and 40 hex digits) is public"
targetRules = ["generic-api-key"]
regexTarget = "secret"
regexes = ['^0x[0-9a-fA-F]{40}$']
"""
RULES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules", f"gitleaks-{PINNED}.toml")


def pinned_why(exe):
    """None when EXE resolves to the pinned gitleaks program (by its SHA-256), else why not."""
    import hashlib
    real = os.path.realpath(exe)
    try:
        with open(real, "rb") as fh:
            h = hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return f"gitleaks is not at {exe}"
    return None if h == PINNED_SHA256 else f"{real} is not the pinned gitleaks (SHA-256 {h[:16]}…)"


def check(folder, exe=None, run=subprocess.run, out=sys.stdout):
    """0 when FOLDER is clean by the pinned gitleaks, else 1 (printed: why, or each finding's file, line, rule)."""
    if exe is None:
        if not PINNED_PATH:
            import platform
            print(f"no gitleaks is pinned for {sys.platform}/{platform.machine()}: fail closed", file=out)
            return 1
        why = pinned_why(PINNED_PATH)
        if why:
            print(f"{why}: fail closed", file=out)
            return 1
        # the file the hash was taken of, not the link: the shared user can write /opt/homebrew (re-review of
        # dbaf2d5d)
        exe = os.path.realpath(PINNED_PATH)
    if not os.path.isfile(RULES):
        print(f"the pinned rules {os.path.basename(RULES)} are missing: fail closed", file=out)
        return 1
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITLEAKS_")}
    try:
        v = run([exe, "version"], capture_output=True, text=True, env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"gitleaks version: {e}: fail closed", file=out)
        return 1
    got = (v.stdout or "").strip().lstrip("v")
    if v.returncode != 0 or got != PINNED:
        print(f"gitleaks is {got or 'unknown'}, pinned {PINNED}: fail closed", file=out)
        return 1
    root = os.path.abspath(folder)
    if not os.path.isdir(root):
        print("no such folder: fail closed", file=out)
        return 1
    # gitleaks 8.30.1 reads <source>/.gitleaksignore even with --gitleaks-ignore-path set: one silenced a key on
    # 2026-11-18 (of_platform's check), so a folder holding one is not scanned
    ign = [os.path.relpath(os.path.join(d, f), root) for d, _, fs in os.walk(root) for f in fs
           if f.lower() == ".gitleaksignore"]   # the Mac's disk ignores case, and so does gitleaks' lookup there
    if ign:
        print(f"{ign[0]}: an ignore file in the scanned folder: fail closed", file=out)
        return 1
    with tempfile.TemporaryDirectory(prefix="of-gitleaks-") as t:
        report, cfg = os.path.join(t, "report.json"), os.path.join(t, "gitleaks.toml")
        with open(cfg, "w") as fh:
            fh.write(f"[extend]\npath = {json.dumps(RULES)}\n" + ALLOW)
        try:
            r = run([exe, "dir", root, "--config", cfg, "--gitleaks-ignore-path", t, "--ignore-gitleaks-allow",
                     "--max-archive-depth", "3", "--no-banner", "--redact", "--log-level", "error",
                     "--report-format", "json", "--report-path", report, "--exit-code", "0"],
                    cwd=t, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            print(f"gitleaks: {e}: fail closed", file=out)
            return 1
        if r.returncode != 0 or not os.path.exists(report):
            print(f"gitleaks failed (exit {r.returncode}): fail closed", file=out)
            return 1
        try:
            with open(report) as fh:
                findings = json.load(fh) or []
        except ValueError:
            print("gitleaks' report is unreadable: fail closed", file=out)
            return 1
    for f in findings:
        print(f"{os.path.relpath(f.get('File', '?'), root)}:{f.get('StartLine') or 0}:{f.get('RuleID') or '?'}",
              file=out)
    return 1 if findings else 0


def dry_run(repo, at, registry=None, out=sys.stdout):
    """The snapshot check over REPO at commit AT, as a run would make its snapshot (tracked files only, no content
    filter, the built-in excludes and REGISTRY's for that repository): so an organization clears its repositories
    before its positions hit them. Plain: no event store, no relay, nothing recorded. Prints path:line:rule only."""
    import of_subagent
    reg = None
    if registry:
        import yaml
        with open(registry) as fh:
            reg = yaml.safe_load(fh) or {}
    root = os.path.realpath(of_subagent._git(repo, "rev-parse", "--show-toplevel").strip())
    head = of_subagent.resolve(root, at=at)[0]
    with tempfile.TemporaryDirectory(prefix="of-gitleaks-dry-") as t:
        dest = of_subagent.snapshot(root, os.path.join(t, "snapshot"), head,
                                    rxs=of_subagent.excludes_for(reg, os.path.basename(root)))
        return check(dest, out=out)


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) == 1:
        sys.exit(check(a[0]))
    if len(a) in (4, 6) and a[0] == "--repo" and a[2] == "--at" and (len(a) == 4 or a[4] == "--registry"):
        try:
            sys.exit(dry_run(a[1], a[3], a[5] if len(a) == 6 else None))
        except Exception as e:  # noqa: BLE001 - a dry run that cannot run fails closed, with why
            print(f"{e}: fail closed")
            sys.exit(1)
    print("usage: of_gitleaks_check.py FOLDER | --repo CHECKOUT --at COMMIT [--registry positions.yaml]",
          file=sys.stderr)
    sys.exit(2)
