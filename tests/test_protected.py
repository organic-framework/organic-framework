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

SPEC = {"repo": "acme/vault", "branch": "main", "human": ["scripts/vault.sh"], "human_lists": ["scripts/run-allow.tsv"],
        "checks": ["unit"]}


def green(prs=None, runs=None, statuses=None, required=None, off_branch=()):
    """A GitHub stand-in: PRS {sha: number} merged into main and on it (but OFF_BRANCH); every check run green unless
    RUNS says otherwise."""
    def api(path):
        if "/compare/" in path:
            sha = path.split("/compare/")[1].split("...")[0]
            return 200, {"status": "diverged" if sha in off_branch else "ahead"}
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
        return P.ff(self.wt, spec or SPEC, to, api=api or green({to: 7}))

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

    def test_what_the_owner_always_keeps(self):
        """of_architect on the field (msg 01a1eaa7-2645): .gitmodules, a human path renamed, a link anywhere that points
        at a human path or the folder that holds one, and a list that cannot be read."""
        def gitmodules():
            self.write(".gitmodules", "[submodule \"x\"]\n\tpath = x\n")

        def rename():
            self.g("mv", "scripts/vault.sh", "scripts/vault-old.sh")

        def link_file():
            os.symlink("vault.sh", os.path.join(self.work, "scripts", "v.sh"))

        def link_folder():
            os.symlink("scripts", os.path.join(self.work, "s"))
        for change, want in ((gitmodules, ".gitmodules"), (rename, "scripts/vault.sh"), (link_file, "scripts/v.sh"),
                             (link_folder, "s")):
            self.g("checkout", "-q", "--detach", self.a)
            change()
            c = self.commit(want)
            with self.assertRaises(P.Refused) as cm:
                self.ff(c)
            self.assertIn("stays the owner's", str(cm.exception), want)
            self.assertIn(want, str(cm.exception), want)
            self.assertEqual(self.head(), self.a, want)
        self.g("checkout", "-q", "--detach", self.a)
        self.write("scripts/other.sh", "#!/bin/sh\necho b\n")
        b = self.commit("other only")
        with self.assertRaises(P.Refused) as cm:                                    # a list unread is no answer
            self.ff(b, spec=dict(SPEC, human_lists=["scripts/run-allow.tsv", "scripts/missing.tsv"]))
        self.assertIn("cannot be read", str(cm.exception))

    def test_paths_that_differ_only_in_case_and_folders_without_a_slash(self):
        """of_secrets-review on organic-framework PR 306: on the Mac's case-insensitive volume a target adding
        SCRIPTS/vault.sh beside the human scripts/vault.sh overwrote it on disk, and a human folder named without a
        trailing / covered nothing under it."""
        p = subprocess.run(["git", "-C", self.work, "hash-object", "-w", "--stdin"], input="echo EVIL\n", text=True,
                           capture_output=True, check=True).stdout.strip()
        self.g("update-index", "--add", "--cacheinfo", f"100644,{p},SCRIPTS/vault.sh")
        self.g("commit", "-qm", "a case twin")
        twin = self.g("rev-parse", "HEAD")
        for spec in (SPEC, dict(SPEC, human=["scripts"])):
            with self.assertRaises(P.Refused) as cm:
                self.ff(twin, spec=spec)
            self.assertIn("differ only in case", str(cm.exception))
            self.assertEqual(self.head(), self.a)
        self.assertEqual(P._hits([("SCRIPTS/Vault.sh", "100644", "100644"), ("scripts/lib/run.sh", "100644", "100644"),
                                  ("x/.GitAttributes", "000000", "100644")], {"scripts/vault.sh", "scripts/lib"}),
                         ["SCRIPTS/Vault.sh", "scripts/lib/run.sh", "x/.GitAttributes"])

    def test_the_checkout_runs_no_filter_the_configuration_names(self):
        """of_secrets-review on organic-framework PR 306: a smudge driver set in the main repository's config and named
        in its info/attributes ran during the checkout."""
        marker = os.path.join(self.d, "ran")
        spy = os.path.join(self.d, "spy.sh")
        with open(spy, "w") as fh:
            fh.write(f"#!/bin/sh\necho smudge >> {marker}\ncat\n")
        os.chmod(spy, 0o755)
        self.g("config", "filter.x.smudge", spy)
        common = self.g("rev-parse", "--path-format=absolute", "--git-common-dir")
        os.makedirs(os.path.join(common, "info"), exist_ok=True)
        with open(os.path.join(common, "info", "attributes"), "w") as fh:
            fh.write("scripts/other.sh filter=x\n")
        self.write("scripts/other.sh", "#!/bin/sh\necho b\n")
        b = self.commit("other")
        self.ff(b)
        self.assertEqual(self.head(), b)
        self.assertFalse(os.path.exists(marker))

    def test_the_registry_names_the_repository_and_the_git_folder_is_this_users(self):
        self.write("scripts/other.sh", "#!/bin/sh\necho b\n")
        b = self.commit("other")
        with self.assertRaises(P.Refused) as cm:                                    # origin is another repository
            self.ff(b, spec=dict(SPEC, repo="acme/other"))
        self.assertIn("not acme/other", str(cm.exception))
        common = self.g("rev-parse", "--path-format=absolute", "--git-common-dir")
        os.chmod(os.path.join(common, "config"), 0o666)
        try:
            with self.assertRaises(P.Refused) as cm:                                # others may write its config
                self.ff(b)
            self.assertIn("others may write", str(cm.exception))
        finally:
            os.chmod(os.path.join(common, "config"), 0o644)
        os.remove(os.path.join(self.wt, "scripts", "vault.sh"))                     # what a move back is checked for
        self.assertEqual(P._differs(self.wt, self.a, ["scripts/vault.sh", "scripts/other.sh"]), ["scripts/vault.sh"])

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
                 (b, green({b: 7}, runs=[{"name": "unit", "status": "completed", "conclusion": "failure"}]),
                  "required checks not green"),
                 (b, green({b: 7}, runs=[]), "required checks not green"),
                 (b, green({b: 7}, required=["lint"]), "required checks not green"),
                 (b, lambda path: (502, None), "GitHub did not answer"))           # fails closed
        for to, api, why in cases:
            with self.assertRaises(P.Refused) as cm:
                self.ff(to, api=api or green({to: 7}))
            self.assertIn(why, str(cm.exception), why)
            self.assertEqual(self.head(), self.a, why)
        with self.assertRaises(P.Refused) as cm:                                    # no required check known
            self.ff(b, spec={k: v for k, v in SPEC.items() if k != "checks"})
        self.assertIn("not known", str(cm.exception))
        self.assertEqual(self.ff(b, api=green({b: 7}, required=["unit"]))["to"], b)   # its required check is green

    def test_the_target_must_be_published_and_a_fast_forward(self):
        self.write("scripts/other.sh", "#!/bin/sh\necho side\n")
        side = self.commit("side", publish=False)                                   # GitHub's main does not hold it
        with self.assertRaises(P.Refused) as cm:
            self.ff(side, api=green({side: 7}, off_branch=(side,)))
        self.assertIn("does not hold", str(cm.exception))
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
                     "      repo: acme/vault\n      branch: main\n      human: [scripts/vault.sh]\n      human_lists: [scripts/run-allow.tsv]\n"
                     "      checks: [unit]\n")
        orgs = os.path.join(self.d, "orgs.yaml")
        with open(orgs, "w") as fh:
            fh.write(f"orgs:\n  - {{name: acme, path: {os.path.join(self.d, 'org')}}}\n")
        self.write("scripts/other.sh", "#!/bin/sh\necho b\n")
        b = self.commit("b")
        said = []
        res = P.run(self.wt, b, position="vault_keeper", orgs_file=orgs, api=green({b: 9}),
                    emit=lambda name, data, **k: said.append((name, data, k)))
        self.assertEqual(res["pr"], 9)
        self.assertEqual([(n, d["to"], d["pr"], k["position"], k["org"]) for n, d, k in said],
                         [("protected_ff", b, 9, "vault_keeper", "acme")])
        self.assertEqual((said[0][1]["human_lists"], "scripts/allowed.sh" in said[0][1]["human"]),
                         (["scripts/run-allow.tsv"], True))                         # what the decision rested on
        with self.assertRaises(P.Refused):                                          # a string entry has no ff
            P.run(os.path.join(self.d, "other"), b, orgs_file=orgs, api=green({b: 9}))

    def test_the_registry_field_is_checked(self):
        self.assertEqual(P.ff_problems({"path": "~/x/y", "ff": SPEC}), [])
        self.assertTrue(P.ff_problems({"path": "~/x/y", "ff": {k: v for k, v in SPEC.items() if k != "repo"}}))
        self.assertEqual(P.ff_problems("~/x/y"), [])
        for bad in ({"branch": "main"}, {"branch": "../x", "human": ["a/b"]}, {"branch": "main", "human": ["/abs"]},
                    {"branch": "main", "human": ["a/../b"]}, {"branch": "main", "human": ["a"], "extra": 1}, "yes",
                    {"branch": "main", "human": ["a"], "checks": "unit"}):
            self.assertTrue(P.ff_problems({"path": "~/x/y", "ff": bad}), bad)
        self.assertEqual(P._listed("scripts/a.sh\tsha\n# scripts/c.sh\n./scripts/b.sh, ../out/x /abs/y"),
                         {"scripts/a.sh", "scripts/b.sh"})


if __name__ == "__main__":
    unittest.main()
