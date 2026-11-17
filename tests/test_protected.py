"""`of protected ff` (bin/of_protected.py; docs/guard.md G21): an agent fast-forwards a protected worktree to a reviewed
commit only when nothing that can reach the secret changed (a user organization's structure keeper, msgs 01a1ea84-a0f4
and 01a1ea97-b59d). Local repositories only; GitHub is a stand-in."""

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_protected as P  # noqa: E402

SPEC = {"branch": "main", "human": ["scripts/vault.sh"], "human_lists": ["scripts/run-allow.tsv"]}


def green(prs=None, runs=None, statuses=None, required=None):
    """A GitHub stand-in: PRS {sha: number} merged into main; every check run green unless RUNS says otherwise."""
    def api(path):
        sha = path.split("/commits/")[1].split("/")[0] if "/commits/" in path else None
        if path.endswith("/pulls"):
            n = (prs or {}).get(sha)
            return 200, ([{"number": n, "merged_at": "2026-11-17T00:00:00Z", "base": {"ref": "main"},
                           "merge_commit_sha": sha}] if n else [])
        if "/check-runs" in path:
            return 200, {"check_runs": runs if runs is not None else
                         [{"name": "unit", "status": "completed", "conclusion": "success"}]}
        if path.endswith("/status"):
            return 200, {"statuses": statuses or []}
        if "required_status_checks" in path:
            return (200, {"contexts": required}) if required else (404, None)
        return 404, None
    return api


@unittest.skipUnless(os.path.isfile(P.SYSTEM_GIT), "no system git")
class ProtectedFfTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self.tmp.name)
        self.work, self.wt = os.path.join(self.d, "work"), os.path.join(self.d, "wt")
        os.makedirs(os.path.join(self.work, "scripts"))
        self.g("init", "-q", "-b", "main")
        self.write("scripts/vault.sh", "#!/bin/sh\nexec op run -- \"$@\"\n")
        self.write("scripts/allowed.sh", "#!/bin/sh\necho allowed\n")
        self.write("scripts/other.sh", "#!/bin/sh\necho other\n")
        self.write("scripts/run-allow.tsv", "scripts/allowed.sh\tsha256:abc\n")
        self.a = self.commit("reviewed base")
        self.g("config", "remote.origin.url", "https://github.com/acme/vault.git")
        self.g("worktree", "add", "-q", "--detach", self.wt, self.a)

    def tearDown(self):
        self.tmp.cleanup()

    def g(self, *args, cwd=None):
        return subprocess.run(["git", "-C", cwd or self.work, "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
                               "-c", "user.email=t@t", "-c", "user.name=t", *args], capture_output=True, text=True,
                              check=True).stdout.strip()

    def write(self, rel, text):
        with open(os.path.join(self.work, rel), "w") as fh:
            fh.write(text)

    def commit(self, msg, publish=True):
        self.g("add", "-A")
        self.g("commit", "-qm", msg)
        sha = self.g("rev-parse", "HEAD")
        if publish:
            self.g("update-ref", "refs/remotes/origin/main", sha)
        return sha

    def head(self):
        return self.g("rev-parse", "HEAD", cwd=self.wt)

    def ff(self, to, api=None, spec=None):
        return P.ff(self.wt, spec or SPEC, to, api=api or green({to: 7}), fetch=False)

    def test_a_change_that_cannot_reach_the_secret_moves(self):
        self.write("scripts/other.sh", "#!/bin/sh\necho other v2\n")
        b = self.commit("other only")
        res = self.ff(b)
        self.assertEqual((self.head(), res["to"], res["pr"], res["changed"]), (b, b, 7, 1))

    def test_a_change_to_what_reaches_the_secret_stays_the_owners(self):
        for rel, text in (("scripts/vault.sh", "#!/bin/sh\ncat \"$TOKEN\"\n"),
                          ("scripts/allowed.sh", "#!/bin/sh\necho changed\n"),      # named by the list, by content
                          ("scripts/run-allow.tsv", "scripts/other.sh\n"),           # the list itself
                          (".gitattributes", "*.sh filter=x\n")):                    # always
            self.g("checkout", "-q", "--detach", self.a)
            self.write(rel, text) if os.path.dirname(rel) else open(os.path.join(self.work, rel), "w").write(text)
            c = self.commit(f"touch {rel}")
            with self.assertRaises(P.Refused) as cm:
                self.ff(c)
            self.assertIn("stays the owner's", str(cm.exception), rel)
            self.assertIn(rel, str(cm.exception))
            self.assertEqual(self.head(), self.a, rel)

    def test_a_list_that_drops_a_script_cannot_free_it(self):
        """The set is read at HEAD and at the target and united: a target whose list no longer names allowed.sh still
        counts its change (the list's own change counts too)."""
        self.write("scripts/run-allow.tsv", "")
        self.write("scripts/allowed.sh", "#!/bin/sh\ncat \"$TOKEN\"\n")
        c = self.commit("drop and change")
        with self.assertRaises(P.Refused) as cm:
            self.ff(c)
        self.assertIn("scripts/allowed.sh", str(cm.exception))

    def test_every_check_must_hold(self):
        self.write("scripts/other.sh", "#!/bin/sh\necho v3\n")
        b = self.commit("other")
        cases = (("main", None, "commit id"),                                       # a branch name
                 (b, green({}), "no merged PR"),                                    # not a PR's merge commit
                 (b, green({b: 7}, runs=[{"name": "unit", "status": "completed", "conclusion": "failure"}]), "not all green"),
                 (b, green({b: 7}, runs=[]), "none ran"),
                 (b, green({b: 7}, required=["lint"]), "required checks not green"))
        for to, api, why in cases:
            with self.assertRaises(P.Refused) as cm:
                self.ff(to, api=api or green({to: 7}))
            self.assertIn(why, str(cm.exception), why)
            self.assertEqual(self.head(), self.a, why)
        self.assertEqual(self.ff(b, api=green({b: 7}, required=["unit"]))["to"], b)   # its required check is green

    def test_the_target_must_be_published_and_a_fast_forward(self):
        self.write("scripts/other.sh", "#!/bin/sh\necho side\n")
        side = self.commit("side", publish=False)                                   # not on origin/main
        with self.assertRaises(P.Refused) as cm:
            self.ff(side)
        self.assertIn("not on origin/main", str(cm.exception))
        self.g("checkout", "-q", "--detach", self.a)
        self.write("scripts/other.sh", "#!/bin/sh\necho b\n")
        b = self.commit("b")
        self.ff(b)
        with self.assertRaises(P.Refused) as cm:                                    # back to an ancestor
            self.ff(self.a)
        self.assertIn("does not fast-forward", str(cm.exception))

    def test_a_tree_with_changes_of_its_own_is_not_moved(self):
        self.write("scripts/other.sh", "#!/bin/sh\necho b\n")
        b = self.commit("b")
        with open(os.path.join(self.wt, "scripts", "other.sh"), "a") as fh:
            fh.write("echo local\n")
        with self.assertRaises(P.Refused) as cm:
            self.ff(b)
        self.assertIn("changes of its own", str(cm.exception))

    def test_run_finds_the_entry_in_a_registry_and_records_the_move(self):
        reg = os.path.join(self.d, "org", "registry")
        os.makedirs(reg)
        with open(os.path.join(reg, "positions.yaml"), "w") as fh:
            fh.write(f"positions: []\nprotected_code:\n  - \"{self.d}/other\"\n  - path: \"{self.wt}\"\n    ff:\n"
                     "      branch: main\n      human: [scripts/vault.sh]\n      human_lists: [scripts/run-allow.tsv]\n")
        orgs = os.path.join(self.d, "orgs.yaml")
        with open(orgs, "w") as fh:
            fh.write(f"orgs:\n  - {{name: acme, path: {os.path.join(self.d, 'org')}}}\n")
        self.write("scripts/other.sh", "#!/bin/sh\necho b\n")
        b = self.commit("b")
        said = []
        res = P.run(self.wt, b, position="vault_keeper", orgs_file=orgs, api=green({b: 9}), fetch=False,
                    emit=lambda name, data, **k: said.append((name, data, k)))
        self.assertEqual(res["pr"], 9)
        self.assertEqual([(n, d["to"], d["pr"], k["position"], k["org"]) for n, d, k in said],
                         [("protected_ff", b, 9, "vault_keeper", "acme")])
        with self.assertRaises(P.Refused):                                          # a string entry has no ff
            P.run(os.path.join(self.d, "other"), b, orgs_file=orgs, api=green({b: 9}), fetch=False)

    def test_the_registry_field_is_checked(self):
        self.assertEqual(P.ff_problems({"path": "~/x/y", "ff": SPEC}), [])
        self.assertEqual(P.ff_problems("~/x/y"), [])
        for bad in ({"branch": "main"}, {"branch": "../x", "human": ["a/b"]}, {"branch": "main", "human": ["/abs"]},
                    {"branch": "main", "human": ["a/../b"]}, {"branch": "main", "human": ["a"], "extra": 1}, "yes"):
            self.assertTrue(P.ff_problems({"path": "~/x/y", "ff": bad}), bad)
        self.assertEqual(P._listed("scripts/a.sh\tsha\n# scripts/c.sh\n./scripts/b.sh, ../out/x /abs/y"),
                         {"scripts/a.sh", "scripts/b.sh"})


if __name__ == "__main__":
    unittest.main()
