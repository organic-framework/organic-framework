#!/usr/bin/env python3
"""A pinned gitleaks run over a folder, failing closed (RFC 0008 §3.1; an organization's subagent_harnesses entry
names it as `of:gitleaks`, for its prompt_check and its snapshot_check).

    python3 bin/of_gitleaks_check.py FOLDER

It runs gitleaks 8.30.1 (`gitleaks dir`: no git history) with the rules pinned in bin/rules/gitleaks-8.30.1.toml,
--redact and the tree's own allow comments and ignore files not honoured, archives opened. Exit 0 only when gitleaks
is that version, ran, wrote its report and found nothing. A finding, a scanner error, a missing report or another
version exits 1. It prints each finding as its file, line and rule id, never the match (a user organization's security
keeper's terms through its structure keeper, 2026-11-18: "a pinned gitleaks run, --no-git --redact, ... failing
closed on any finding or scanner error").
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

PINNED = "8.30.1"
RULES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules", f"gitleaks-{PINNED}.toml")


def check(folder, exe=None, run=subprocess.run, out=sys.stdout):
    """0 when FOLDER is clean by the pinned gitleaks, else 1 (printed: why, or each finding's file, line, rule)."""
    exe = exe or shutil.which("gitleaks")
    if not exe:
        print("gitleaks is not installed: fail closed", file=out)
        return 1
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
    with tempfile.TemporaryDirectory(prefix="of-gitleaks-") as t:
        report = os.path.join(t, "report.json")
        try:
            r = run([exe, "dir", root, "--config", RULES, "--gitleaks-ignore-path", t, "--ignore-gitleaks-allow",
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
        print(f"{os.path.relpath(f.get('File', '?'), root)}:{f.get('StartLine') or 0} {f.get('RuleID') or '?'}",
              file=out)
    return 1 if findings else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        sys.exit(2)
    sys.exit(check(sys.argv[1]))
