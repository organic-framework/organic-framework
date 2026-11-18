#!/usr/bin/env python3
"""A pinned gitleaks run over a folder, failing closed (RFC 0008 §3.1; an organization's subagent_harnesses entry
names it as `of:gitleaks`, for its prompt_check and its snapshot_check).

    python3 bin/of_gitleaks_check.py FOLDER
    python3 bin/of_gitleaks_check.py --repo CHECKOUT --at COMMIT [--registry positions.yaml]   (a dry run)

It runs gitleaks 8.30.1 from OF_GITLEAKS when that is set, else from the path pinned for the platform (macOS arm64:
/opt/homebrew/bin/gitleaks; Linux x86_64 and aarch64: /usr/local/bin/gitleaks), copied into a fresh 0700 folder and
checked there by the platform's pinned SHA-256 (`gitleaks dir`: no git history) with the rules pinned in bin/rules/gitleaks-8.30.1.toml,
--redact and the tree's own allow comments and ignore files not honoured, archives opened. Exit 0 only when gitleaks
is that version, ran, wrote its report and found nothing. A finding, a scanner error, a missing report or another
version exits 1. It prints each finding as its file, line and rule id, never the match (a user organization's security
keeper's terms through its structure keeper, 2026-11-18: "a pinned gitleaks run, --no-git --redact, ... failing
closed on any finding or scanner error").
"""

import json
import os
import re
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
# A dotted-identifier rule was tried and dropped (of_secrets-review on PR 337: it let quoted namespaced and two-part
# tokens through, while the case it was for drew no finding without it); one waits for the organization's dry run
# to show its case.
#
# The second (the same keeper): a bare 40-hex address with no 0x, as research notes write one. 40 hex digits is also
# the shape of a legacy GitHub OAuth token and other API keys, so it is allowed only in documentation files (*.md)
# and only when it carries an EIP-55 checksum that verifies (mixed case, keccak-256 of the lower-case address); an
# all-lower or all-upper value, which has none, stays caught, and one in code stays a per-path exclude after the
# keeper's verdict, never a rule (the keeper's exact form, 2026-11-18). gitleaks cannot verify a checksum, so the check reads the value back from the file at the
# finding's position, in this process, never printed.
RULES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules", f"gitleaks-{PINNED}.toml")


_RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000, 0x000000000000808B,
      0x0000000080000001, 0x8000000080008081, 0x8000000000008009, 0x000000000000008A, 0x0000000000000088,
      0x0000000080008009, 0x000000008000000A, 0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
      0x8000000000008003, 0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
      0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
_M = (1 << 64) - 1


def _f(a):
    for rc in _RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ (((c[(x + 1) % 5] << 1) | (c[(x + 1) % 5] >> 63)) & _M) for x in range(5)]
        a = [[a[x][y] ^ d[x] for y in range(5)] for x in range(5)]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                r = _ROT[x][y]
                b[y][(2 * x + 3 * y) % 5] = ((a[x][y] << r) | (a[x][y] >> (64 - r))) & _M if r else a[x][y]
        a = [[b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y]) for y in range(5)] for x in range(5)]
        a[0][0] ^= rc
    return a


def keccak256(data):
    rate = 136
    msg = bytearray(data) + b"\x01"
    msg += b"\x00" * ((-len(msg)) % rate)
    msg[-1] |= 0x80
    a = [[0] * 5 for _ in range(5)]
    for off in range(0, len(msg), rate):
        block = msg[off:off + rate]
        for i in range(rate // 8):
            x, y = i % 5, i // 5
            a[x][y] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        a = _f(a)
    out = b"".join(a[i % 5][i // 5].to_bytes(8, "little") for i in range(25))
    return out[:32]


if False and __name__ == "__main__":
    print(keccak256(b"").hex())
    print(keccak256(b"abc").hex())


def eip55(h):
    """Does the 40-hex address H carry an EIP-55 checksum that verifies? Mixed case only: a value in one case has
    none."""
    if len(h) != 40 or h.lower() == h or h.upper() == h:
        return False
    d = keccak256(h.lower().encode()).hex()
    return all((c.upper() if int(d[i], 16) >= 8 else c.lower()) == c for i, c in enumerate(h) if c.isalpha())


_HEX40 = re.compile(r"(?<![0-9A-Za-z])([0-9a-fA-F]{40})(?![0-9A-Za-z])")
_LONG = re.compile(r"[A-Za-z0-9+/_=-]{16,}")


def checksummed_address(path, f):
    """Is the generic-api-key finding F (a gitleaks report entry), in a documentation file (*.md), a bare 40-hex
    address with a verified EIP-55 checksum and nothing else secret-shaped in its match? Reads the match from PATH at the finding's position."""
    if f.get("RuleID") != "generic-api-key" or not str(path).lower().endswith(".md"):
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
        line = lines[int(f.get("StartLine") or 0) - 1]
    except (OSError, IndexError, ValueError):
        return False
    if int(f.get("EndLine") or f.get("StartLine") or 0) != int(f.get("StartLine") or 0):
        return False
    m = line[max(int(f.get("StartColumn") or 1) - 1, 0):int(f.get("EndColumn") or len(line))]
    hexes = _HEX40.findall(m)
    if len(hexes) != 1 or not eip55(hexes[0]):
        return False
    rest = m.replace(hexes[0], " ")
    return not _LONG.search(rest)


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


def private_copy(src, folder):
    """Copy the gitleaks file SRC into FOLDER (a fresh 0700 folder) and return the copy's path when the copy has the
    pinned SHA-256, else (None, why). The hash and the exec are then of one file nobody else can write: a link or a
    path another user can replace cannot be swapped between the check and the run (a user organization's security
    keeper, 2026-11-18)."""
    import hashlib
    real = os.path.realpath(src)
    dest = os.path.join(folder, "gitleaks")
    try:
        with open(real, "rb") as fin, open(dest, "wb") as fout:
            data = fin.read()
            fout.write(data)
        os.chmod(dest, 0o700)
    except OSError:
        return None, f"gitleaks is not at {src}"
    with open(dest, "rb") as fh:
        h = hashlib.sha256(fh.read()).hexdigest()
    if h != PINNED_SHA256:
        return None, f"{real} is not the pinned gitleaks (SHA-256 {h[:16]}…)"
    return dest, None


def check(folder, exe=None, run=subprocess.run, out=sys.stdout):
    """0 when FOLDER is clean by the pinned gitleaks, else 1 (printed: why, or each finding's file, line, rule). The
    program comes from OF_GITLEAKS when set (a runner whose root filesystem is read-only puts it in its own folder),
    else from the platform's pinned path; either way it is copied into a fresh 0700 folder, hashed there and run
    from there."""
    if exe is not None:
        return _check(folder, exe, run, out)
    if not PINNED_PATH:
        import platform
        print(f"no gitleaks is pinned for {sys.platform}/{platform.machine()}: fail closed", file=out)
        return 1
    with tempfile.TemporaryDirectory(prefix="of-gitleaks-bin-") as d:
        os.chmod(d, 0o700)
        copy, why = private_copy(os.environ.get("OF_GITLEAKS") or PINNED_PATH, d)
        if why:
            print(f"{why}: fail closed", file=out)
            return 1
        return _check(folder, copy, run, out)


def _check(folder, exe, run=subprocess.run, out=sys.stdout):
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
    findings = [f for f in findings if not checksummed_address(f.get("File") or "", f)]
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
