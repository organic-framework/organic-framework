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
        if os.path.exists(marker):
            os.remove(marker)                                                       # the test's own commit ran it
        self.g("config", "filter.x.clean", spy)                                     # status runs a clean driver
        self.g("config", "filter.x.process", spy)
        st = os.stat(os.path.join(self.wt, "scripts", "other.sh"))                  # its stat changed: status reads it
        os.utime(os.path.join(self.wt, "scripts", "other.sh"), (st.st_atime, st.st_mtime + 5))
        self.ff(b)
        self.assertEqual(self.head(), b)
        self.assertFalse(os.path.exists(marker))

    def test_no_hook_the_configuration_names_runs(self):
        """git 2.54 and later run a hook a repository's configuration names (hook.<name>.command and .event) whatever
        core.hooksPath says (of_architect, msg 01a202ea-781a, from git-config(1)); hook.<event>.enabled=false on the
        command line turns it off."""
        marker = os.path.join(self.d, "hooked")
        spy = os.path.join(self.d, "hook.sh")
        with open(spy, "w") as fh:
            fh.write(f"#!/bin/sh\necho hook >> {marker}\nexit 0\n")
        os.chmod(spy, 0o755)
        self.g("config", "hook.spy.command", spy)
        for ev in ("post-checkout", "reference-transaction", "post-index-change", "post-merge"):
            self.g("config", "--add", "hook.spy.event", ev)
        self.write("scripts/other.sh", "#!/bin/sh\necho c\n")
        b = self.commit("other")
        ran = os.path.exists(marker)                                                # plain git ran it, from 2.54
        if ran:
            os.remove(marker)
        with self.assertRaises(P.Refused) as cm:                                    # 2.54 has no switch to stop it
            self.ff(b)
        self.assertIn("names a hook", str(cm.exception))
        self.assertEqual(self.head(), self.a)
        self.assertFalse(os.path.exists(marker))
        self.assertIn("hook.post-checkout.enabled=false", P._git_cmd(self.wt))     # 2.55's switch, a second layer
        self.assertEqual(P._env()["GIT_ALLOW_PROTOCOL"], "")                          # no transport at all
        # a hook written while GitHub is asked is seen right before the checkout (of_review on PR 511)
        self.g("config", "--remove-section", "hook.spy")
        asked = green({b: 7})

        def api(path):
            if "/check-runs" in path:
                self.g("config", "hook.late.command", spy)
                self.g("config", "hook.late.event", "post-checkout")
            return asked(path)
        with self.assertRaises(P.Refused) as cm:
            self.ff(b, api=api)
        self.assertIn("hook.late.command", str(cm.exception))
        self.assertEqual(self.head(), self.a)
        self.assertFalse(os.path.exists(marker))
        # a configuration that cannot be read for hooks refuses, never reads as "none"
        from unittest import mock
        real = P._git
        failing = lambda tree, *a, **k: (subprocess.CompletedProcess(a, 128, "", "") if a[:1] == ("config",)
                                         else real(tree, *a, **k))
        with mock.patch.object(P, "_git", failing):
            with self.assertRaises(P.Refused) as cm:
                P._no_config_hooks(self.wt)
        self.assertIn("could not be read", str(cm.exception))

    def test_a_value_does_not_name_a_filter_and_a_forged_blob_is_refused(self):
        """of_architect on organic-framework PR 306: a value holding ` x.clean ` made the parsing name another driver,
        so x's smudge ran; and a loose object overwritten in place put other content under the reviewed id."""
        import zlib
        marker = os.path.join(self.d, "ran")
        spy = os.path.join(self.d, "spy.sh")
        with open(spy, "w") as fh:
            fh.write(f"#!/bin/sh\necho ran >> {marker}\ncat\n")
        os.chmod(spy, 0o755)
        self.write("scripts/other.sh", "#!/bin/sh\necho b\n")
        b = self.commit("other")
        common = self.g("rev-parse", "--path-format=absolute", "--git-common-dir")
        os.makedirs(os.path.join(common, "info"), exist_ok=True)
        with open(os.path.join(common, "info", "attributes"), "w") as fh:
            fh.write("scripts/other.sh filter=x\n")
        self.g("config", "filter.x.smudge", f"{spy} x.clean y")
        self.ff(b)
        self.assertFalse(os.path.exists(marker))
        self.g("config", "--unset", "filter.x.smudge")
        self.g("checkout", "-q", "--detach", b)
        self.write("scripts/other.sh", "#!/bin/sh\necho c\n")
        c = self.commit("other again")
        oid = self.g("rev-parse", f"{c}:scripts/other.sh")
        path = os.path.join(common, "objects", oid[:2], oid[2:])
        evil = b"#!/bin/sh\necho EVIL\n"
        os.chmod(path, 0o644)
        with open(path, "wb") as fh:
            fh.write(zlib.compress(b"blob %d\0" % len(evil) + evil))
        with self.assertRaises(P.Refused) as cm:
            self.ff(c)
        self.assertIn("do not hash", str(cm.exception))
        self.assertEqual(self.head(), b)

    def _overwrite(self, oid, kind, content):
        """Put CONTENT under OID's loose object in the shared store, as a writer of that store could."""
        import zlib
        common = self.g("rev-parse", "--path-format=absolute", "--git-common-dir")
        path = os.path.join(common, "objects", oid[:2], oid[2:])
        os.chmod(path, 0o644)
        with open(path, "wb") as fh:
            fh.write(zlib.compress(b"%s %d\0" % (kind, len(content)) + content))

    def test_a_forged_folder_tree_is_refused(self):
        """of_architect's third pass on organic-framework PR 306: git checks the root tree as it reads it, not the trees
        below it, so a forged tree under the target's genuine `lib` id passed the check of the changed blobs."""
        os.makedirs(os.path.join(self.work, "lib"))
        self.write("lib/a.txt", "a\n")
        b = self.commit("lib")
        self.ff(b)
        self.write("lib/a.txt", "a2\n")
        c = self.commit("lib a2")
        evil = os.path.join(self.d, "evil.txt")
        with open(evil, "w") as fh:
            fh.write("EVIL\n")
        blob = self.g("hash-object", "-w", evil)
        self._overwrite(self.g("rev-parse", f"{c}:lib"), b"tree", b"100644 a.txt\0" + bytes.fromhex(blob))
        with self.assertRaises(P.Refused) as cm:
            self.ff(c)
        self.assertIn("do not hash", str(cm.exception))
        self.assertEqual(self.head(), b)
        with open(os.path.join(self.wt, "lib", "a.txt")) as fh:
            self.assertEqual(fh.read(), "a\n")

    def test_an_object_swapped_between_the_check_and_the_checkout_is_never_read(self):
        """of_architect's third pass on organic-framework PR 306: a blob swapped in the shared store after it was hashed
        and before the checkout. Since its fourth pass the checkout reads a private store of the verified objects, so
        the swap is never read and the tree gets the reviewed content."""
        self.write("scripts/other.sh", "#!/bin/sh\necho d\n")
        d = self.commit("other d")
        oid = self.g("rev-parse", f"{d}:scripts/other.sh")
        real = P._git

        def swapping(tree, *args, **kw):
            if args and args[0] == "checkout" and "--force" not in args:
                self._overwrite(oid, b"blob", b"#!/bin/sh\necho EVIL\n")
            return real(tree, *args, **kw)
        P._git = swapping
        try:
            self.ff(d)
        finally:
            P._git = real
        self.assertEqual(self.head(), d)
        with open(os.path.join(self.wt, "scripts", "other.sh")) as fh:
            self.assertEqual(fh.read(), "#!/bin/sh\necho d\n")

    def _raw(self, *args):
        return subprocess.run(["git", "-C", self.work, *args], capture_output=True, check=True).stdout

    def test_a_tree_swapped_while_github_is_asked_is_never_read(self):
        """of_architect's fourth pass on organic-framework PR 306: a tree swapped in the shared store after it was
        verified (here while GitHub is asked) rewrote a file the change does not touch and added another. git now reads
        only a private store of the verified objects, and the whole index is compared after the checkout."""
        os.makedirs(os.path.join(self.work, "lib"))
        self.write("lib/helper.sh", "#!/bin/sh\necho helper\n")
        b = self.commit("helper")
        self.ff(b)
        self.write("lib/note.txt", "v2\n")
        c = self.commit("a note")
        self.write("lib/helper.sh", "#!/bin/sh\necho EVIL\n")                     # the forged lib folder: helper.sh
        self.write("lib/zz.sh", "#!/bin/sh\necho EVIL\n")                         # rewritten and zz.sh added
        self.g("add", "-A")
        forged = self._raw("cat-file", "tree", self.g("rev-parse", f"{self.g('write-tree')}:lib"))
        self.g("reset", "-q", "--hard", c)
        lib = self.g("rev-parse", f"{c}:lib")
        genuine = self._raw("cat-file", "tree", lib)
        stand_in = green({c: 7})

        def api(path):
            self._overwrite(lib, b"tree", forged)
            return stand_in(path)
        self.ff(c, api=api)
        self.assertEqual(self.head(), c)
        with open(os.path.join(self.wt, "lib", "helper.sh")) as fh:
            self.assertEqual(fh.read(), "#!/bin/sh\necho helper\n")
        self.assertFalse(os.path.exists(os.path.join(self.wt, "lib", "zz.sh")))
        self._overwrite(lib, b"tree", genuine)                                      # to read the index against c
        self.assertEqual(P._index_differs(self.wt, P._snapshot(self.wt, c)), [])

    def test_the_move_back_writes_only_verified_objects(self):
        """of_architect's fourth pass on organic-framework PR 306: the move back read the shared store, so a blob of
        HEAD swapped there was written into the tree, worse than where it started."""
        self.write("scripts/other.sh", "#!/bin/sh\necho d\n")
        d = self.commit("other d")
        old = self.g("rev-parse", f"{self.a}:scripts/other.sh")
        real = P._differs
        calls = []

        def differs(tree, snap, paths, human=()):
            calls.append(1)
            if len(calls) == 1:                                                     # the forward check fails, and
                self._overwrite(old, b"blob", b"#!/bin/sh\necho EVIL\n")         # HEAD's blob is swapped before
                return ["scripts/other.sh"]                                         # the move back
            return real(tree, snap, paths, human)
        P._differs = differs
        try:
            with self.assertRaises(P.Refused) as cm:
                self.ff(d)
        finally:
            P._differs = real
        self.assertIn("HEAD's again", str(cm.exception))
        self.assertEqual(self.head(), self.a)
        with open(os.path.join(self.wt, "scripts", "other.sh")) as fh:
            self.assertEqual(fh.read(), "#!/bin/sh\necho other\n")

    def test_sparse_checkout_never_takes_a_human_file_away(self):
        """of_architect's fifth pass on organic-framework PR 306: with core.sparseCheckout on in the shared config and
        vault.sh outside the worktree's patterns, the move deleted it and passed. The checkouts run with sparse checkout
        off, a human path the index leaves out or marks unchanged is refused, and a missing human file never passes."""
        self.write("scripts/other.sh", "#!/bin/sh\necho b\n")
        b = self.commit("other")
        self.g("config", "core.sparseCheckout", "true")
        gitdir = self.g("rev-parse", "--path-format=absolute", "--git-dir", cwd=self.wt)
        os.makedirs(os.path.join(gitdir, "info"), exist_ok=True)
        with open(os.path.join(gitdir, "info", "sparse-checkout"), "w") as fh:
            fh.write("/*\n!/scripts/vault.sh\n")
        self.ff(b)
        self.assertTrue(os.path.exists(os.path.join(self.wt, "scripts", "vault.sh")))
        self.g("config", "--unset", "core.sparseCheckout")
        for flag in ("--skip-worktree", "--assume-unchanged"):
            self.g("update-index", flag, "scripts/vault.sh", cwd=self.wt)
            self.write("scripts/other.sh", f"#!/bin/sh\necho {flag}\n")
            c = self.commit(flag)
            with self.assertRaises(P.Refused) as cm:
                self.ff(c)
            self.assertIn("scripts/vault.sh", str(cm.exception))
            self.g("update-index", flag.replace("--", "--no-"), "scripts/vault.sh", cwd=self.wt)
        snap = P._snapshot(self.wt, self.head())
        os.remove(os.path.join(self.wt, "scripts", "vault.sh"))
        self.assertEqual(P._differs(self.wt, snap, ["scripts/vault.sh"], {"scripts/vault.sh"}), ["scripts/vault.sh"])

    def test_a_graft_does_not_turn_a_rollback_into_a_fast_forward(self):
        """of_architect's fifth pass on organic-framework PR 306: the ancestry check read parents through the shared
        store, which honours info/grafts, so a graft moved the tree back to an older commit."""
        self.write("scripts/other.sh", "#!/bin/sh\necho b\n")
        b = self.commit("b")
        self.ff(b)
        self.write("scripts/other.sh", "#!/bin/sh\necho c\n")
        c = self.commit("c")
        self.ff(c)
        common = self.g("rev-parse", "--path-format=absolute", "--git-common-dir")
        os.makedirs(os.path.join(common, "info"), exist_ok=True)
        with open(os.path.join(common, "info", "grafts"), "w") as fh:
            fh.write(f"{b} {c}\n")
        with self.assertRaises(P.Refused) as cm:
            self.ff(b)
        self.assertIn("does not fast-forward", str(cm.exception))
        self.assertEqual(self.head(), c)

    def test_no_private_store_is_left_behind(self):
        """of_architect's fifth pass on organic-framework PR 306: a failure while the private store was written left
        it on disk."""
        import glob
        import tempfile
        import zlib
        self.write("scripts/other.sh", "#!/bin/sh\necho b\n")
        b = self.commit("other")
        pattern = os.path.join(tempfile.gettempdir(), "of-protected-ff-*")
        before = set(glob.glob(pattern))
        real = zlib.compress

        def boom(*args, **kw):
            raise OSError("disk full")
        zlib.compress = boom
        try:
            with self.assertRaises(OSError):
                self.ff(b)
        finally:
            zlib.compress = real
        self.assertEqual(set(glob.glob(pattern)) - before, set())
        self.assertEqual(self.head(), self.a)

    def test_the_status_runs_no_filter_a_submodules_own_configuration_names(self):
        """of_architect's third pass on organic-framework PR 306: the status read a submodule's work tree, so a clean
        driver its own config and attributes named ran; the status no longer reads it, and the checkout never
        recurses."""
        marker = os.path.join(self.d, "ran")
        spy = os.path.join(self.d, "spy.sh")
        with open(spy, "w") as fh:
            fh.write(f"#!/bin/sh\necho ran >> {marker}\ncat\n")
        os.chmod(spy, 0o755)
        sub = os.path.join(self.wt, "sub")
        os.makedirs(sub)
        self.g("init", "-q", "-b", "main", cwd=sub)
        with open(os.path.join(sub, "f.txt"), "w") as fh:
            fh.write("f\n")
        self.g("add", "-A", cwd=sub)
        self.g("commit", "-qm", "sub", cwd=sub)
        self.g("update-index", "--add", "--cacheinfo", f"160000,{self.g('rev-parse', 'HEAD', cwd=sub)},sub")
        self.g("commit", "-qm", "a submodule pointer")
        e = self.g("rev-parse", "HEAD")
        self.g("update-ref", "refs/remotes/origin/main", e)
        self.g("checkout", "-q", "--detach", e, cwd=self.wt)
        self.g("config", "filter.y.clean", spy, cwd=sub)
        with open(os.path.join(sub, ".git", "info", "attributes"), "w") as fh:
            fh.write("* filter=y\n")
        st = os.stat(os.path.join(sub, "f.txt"))
        os.utime(os.path.join(sub, "f.txt"), (st.st_atime, st.st_mtime + 5))
        self.write("scripts/other.sh", "#!/bin/sh\necho f\n")
        self.g("add", "scripts/other.sh")                                           # the pointer stays (no sub/ here)
        self.g("commit", "-qm", "other f")
        f = self.g("rev-parse", "HEAD")
        self.g("update-ref", "refs/remotes/origin/main", f)
        self.ff(f)
        self.assertEqual(self.head(), f)
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
        self.assertEqual(P._differs(self.wt, P._snapshot(self.wt, self.a), ["scripts/vault.sh", "scripts/other.sh"]),
                         ["scripts/vault.sh"])

    def test_replace_objects_submodules_and_checks_of_architect_found(self):
        """of_architect on organic-framework PR 306: a `git replace` showed unreviewed content under the vouched id,
        diff.ignoreSubmodules=all hid a gitlink, and a commit status or a skipped check counted as green."""
        self.write("scripts/vault.sh", "#!/bin/sh\ncat \"$TOKEN\"\n")
        evil = self.commit("evil", publish=False)
        self.g("checkout", "-q", "--detach", self.a)
        self.write("scripts/other.sh", "#!/bin/sh\necho b\n")
        b = self.commit("other")
        self.g("replace", b, evil)
        self.ff(b)
        with open(os.path.join(self.wt, "scripts", "vault.sh")) as fh:
            self.assertNotIn("TOKEN", fh.read())                                    # the vouched content, not the replacement
        self.g("replace", "-d", b)
        self.g("config", "diff.ignoreSubmodules", "all")
        self.g("update-index", "--add", "--cacheinfo", f"160000,{self.a},sub")
        self.g("commit", "-qm", "a gitlink")
        sub = self.g("rev-parse", "HEAD")
        self.g("update-ref", "refs/remotes/origin/main", sub)
        with self.assertRaises(P.Refused) as cm:
            self.ff(sub)
        self.assertIn("sub", str(cm.exception))
        for runs in ([{"name": "unit", "status": "completed", "conclusion": "skipped"}], []):
            with self.assertRaises(P.Refused):
                P.github_proof("acme/vault", b, "main", api=green({b: 7}, runs=runs,
                                                                  statuses=[{"context": "unit", "state": "success"}]),
                               checks=["unit"])

    def test_a_list_that_is_a_link_and_the_sudo_mode_are_refused(self):
        os.remove(os.path.join(self.work, "scripts", "run-allow.tsv"))
        os.symlink("other.sh", os.path.join(self.work, "scripts", "run-allow.tsv"))
        self.write("scripts/other.sh", "#!/bin/sh\necho b\n")
        c = self.commit("the list a link")
        with self.assertRaises(P.Refused) as cm:
            self.ff(c)
        self.assertIn("is a link", str(cm.exception))                               # read as its target's name
        old = os.environ.get("SUDO_UID")
        os.environ["SUDO_UID"] = "501"
        try:
            with self.assertRaises(P.Refused) as cm:
                P.run(self.wt, c, orgs_file=os.path.join(self.d, "none.yaml"), api=green({c: 7}))
            self.assertIn("sudo", str(cm.exception))
        finally:
            if old is None:
                os.environ.pop("SUDO_UID", None)
            else:
                os.environ["SUDO_UID"] = old

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

    def test_the_owners_list_in_orgs_yaml_stands_over_the_registry(self):
        """The founder's card OF-266: on the agent host the registry follows what any session pushes, so an
        organization's ff entries come from orgs.yaml alone when the owner wrote a list there."""
        reg = os.path.join(self.d, "org", "registry")
        os.makedirs(reg)
        with open(os.path.join(reg, "positions.yaml"), "w") as fh:   # what a pushed registry could say
            fh.write(f"positions: []\nprotected_code:\n  - path: \"{self.wt}\"\n    ff:\n"
                     "      repo: someone/fork\n      branch: other\n      human: [README]\n")
        orgs = os.path.join(self.d, "orgs.yaml")
        with open(orgs, "w") as fh:
            fh.write(f"orgs:\n  - name: acme\n    path: {os.path.join(self.d, 'org')}\n    protected_code:\n"
                     f"      - path: \"{self.wt}\"\n        ff:\n          repo: acme/vault\n          branch: main\n"
                     "          human: [scripts/vault.sh]\n          checks: [unit]\n")
        got = P.entries(orgs)
        self.assertEqual([(o, f["repo"], f["branch"]) for o, p, f in got], [("acme", "acme/vault", "main")])
        self.write("scripts/other.sh", "#!/bin/sh\necho b\n")
        b = self.commit("b")
        said = []
        P.run(self.wt, b, orgs_file=orgs, api=green({b: 9}), emit=lambda name, data, **k: said.append(data))
        self.assertEqual((said[0]["repo"], said[0]["branch"]), ("acme/vault", "main"))
        import of_credentials                                                # G21 keeps the owner's path too
        self.assertIn(os.path.realpath(self.wt), [os.path.realpath(x) for x in
                                                  of_credentials.load(orgs)["acme"]["protected_code"]])
        with open(os.path.join(reg, "positions.yaml"), "w") as fh:           # a registry that drops it drops nothing
            fh.write("positions: []\n")
        self.assertIn(os.path.realpath(self.wt), [os.path.realpath(x) for x in
                                                  of_credentials.load(orgs)["acme"]["protected_code"]])
        with open(orgs, "w") as fh:                                          # an empty list: no ff entry at all
            fh.write(f"orgs:\n  - name: acme\n    path: {os.path.join(self.d, 'org')}\n    protected_code: []\n")
        self.assertEqual(P.entries(orgs), [])

    def test_the_registry_field_is_checked(self):
        self.assertEqual(P.ff_problems({"path": "~/x/y", "ff": SPEC}), [])
        self.assertTrue(P.ff_problems({"path": "~/x/y", "ff": {k: v for k, v in SPEC.items() if k != "repo"}}))
        self.assertEqual(P.ff_problems("~/x/y"), [])
        for bad in ({"branch": "main"}, {"branch": "../x", "human": ["a/b"]}, {"branch": "main", "human": ["/abs"]},
                    {"branch": "main", "human": ["a/../b"]}, {"branch": "main", "human": ["a"], "extra": 1}, "yes",
                    {"branch": "main", "human": ["a"], "checks": "unit"}):
            self.assertTrue(P.ff_problems({"path": "~/x/y", "ff": bad}), bad)
        got = P._listed("scripts/a.sh\tsha\n# scripts/c.sh\n./scripts/b.sh, ../out/x /abs/y\nscripts/hooks/\ndeploy.sh\n"
                        "scripts/my file.sh\tsha256:x")
        self.assertTrue({"scripts/a.sh", "scripts/b.sh", "scripts/hooks", "deploy.sh", "scripts/my file.sh"} <= got, got)
        self.assertFalse({"scripts/c.sh", "../out/x", "/abs/y"} & got)
        self.assertTrue(P.ff_problems({"path": "~/x/y", "ff": dict(SPEC, human=["scripts/*.sh"])}))   # a glob
        self.assertTrue(P._covered("scripts/cafe\u0301.sh", {"scripts/caf\u00e9.sh"}))         # NFD and NFC
        self.assertTrue(P._covered("scripts/hooks/x", {"scripts/hooks"}))
        links = {"a": ".", "b": "c", "c": "scripts/vault.sh"}
        hits = P._hits([("a", "000000", "120000"), ("b", "000000", "120000")], {"scripts/vault.sh"},
                       lambda path: links.get(path), {"c": "c"})
        self.assertEqual(hits, ["a", "b"])                                          # the root, a chain of links
        through = {"s": "scripts", "y": "s/vault.sh"}                               # a link inside the path
        self.assertEqual(P._hits([("y", "000000", "120000")], {"scripts/vault.sh"}, lambda path: through.get(path),
                                 {"s": "s", "y": "y"}), ["y"])
        self.assertIn("scripts/a#b.sh", P._listed("scripts/a#b.sh\n"))


class ReaderAndLoginTest(unittest.TestCase):
    """A machine with no gh reads a public repository without credentials, and a run another user's login started
    is refused even when sudo's variables were cleared (of_agent-server, msg 01a202d5-1eeb)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.d]))
        self.loginuid = P.LOGINUID
        self.addCleanup(lambda: setattr(P, "LOGINUID", self.loginuid))

    def login(self, value):
        path = os.path.join(self.d, "loginuid")
        with open(path, "w") as fh:
            fh.write(value)
        P.LOGINUID = path

    def test_a_login_of_another_user_is_the_sudo_mode(self):
        old = os.environ.pop("SUDO_UID", None)
        self.addCleanup(lambda: old is not None and os.environ.__setitem__("SUDO_UID", old))
        if os.geteuid() == 0:
            self.skipTest("runs as root, which is refused anyway")
        self.login(str(os.getuid() + 1))                                           # sudo -u <user> env -i …
        self.assertTrue(P._under_sudo())
        with self.assertRaises(P.Refused) as cm:
            P.run(self.d, "0" * 40, orgs_file=os.path.join(self.d, "none.yaml"), api=green())
        self.assertIn("sudo", str(cm.exception))
        for own in (str(os.getuid()), "4294967295", "-1", "", "x"):                # this user's, unset, unreadable
            self.login(own)
            self.assertFalse(P._under_sudo(), own)
        P.LOGINUID = os.path.join(self.d, "missing")                               # no such file: macOS
        self.assertFalse(P._under_sudo())

    def test_a_mac_without_developer_tools_runs_no_git(self):
        """/usr/bin/git on such a Mac is the stub that opens an install dialog: never run in the background."""
        from unittest import mock
        none = os.path.join(self.d, "nothing")
        with mock.patch.object(P, "DEVELOPER_DIRS", (none,)), mock.patch("sys.platform", "darwin"), \
                mock.patch.object(P.os.path, "isfile", lambda p: p == P.SYSTEM_GIT):
            self.assertTrue(P._stub_git())
            with self.assertRaises(P.Refused) as cm:
                P._git_cmd(self.d)
            self.assertIn("stub", str(cm.exception))                                # its own text, not "no git"
        with mock.patch.object(P, "DEVELOPER_DIRS", (self.d,)), mock.patch("sys.platform", "darwin"):
            os.makedirs(os.path.join(self.d, "usr", "bin"), exist_ok=True)
            open(os.path.join(self.d, "usr", "bin", "git"), "w").close()
            self.assertFalse(P._stub_git())                                          # the tools are there
        selected = os.path.join(self.d, "Xcode-beta", "Contents", "Developer")       # chosen by xcode-select -s
        os.makedirs(os.path.join(selected, "usr", "bin"))
        open(os.path.join(selected, "usr", "bin", "git"), "w").close()
        link = os.path.join(self.d, "xcode_select_link")
        os.symlink(selected, link)
        with mock.patch.object(P, "DEVELOPER_DIRS", (link, none)), mock.patch("sys.platform", "darwin"):
            self.assertFalse(P._stub_git())                                          # the selected Xcode's git
        with mock.patch("sys.platform", "linux"):
            self.assertFalse(P._stub_git())

    def test_github_without_credentials(self):
        import io
        import urllib.error
        import of_route
        seen = []

        class Answer(io.BytesIO):
            status = 200

        def opener(req, timeout, context=None):
            seen.append(req)
            if "protection" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
            if "broken" in req.full_url:
                raise OSError("no route")
            return Answer(b'[{"number": 7}]')
        real = of_route.open_url
        of_route.open_url = opener
        self.addCleanup(lambda: setattr(of_route, "open_url", real))
        self.assertEqual(P.public_api("repos/acme/vault/commits/abc/pulls"), (200, [{"number": 7}]))
        self.assertEqual(seen[0].full_url, "https://api.github.com/repos/acme/vault/commits/abc/pulls")
        self.assertEqual({k.lower() for k in seen[0].headers}, {"accept", "x-github-api-version", "user-agent"})
        self.assertEqual(P.public_api("repos/acme/vault/branches/main/protection/required_status_checks"), (401, None))
        self.assertEqual(P.public_api("repos/acme/broken"), (1, None))
        import shutil
        which = shutil.which
        self.addCleanup(lambda: setattr(shutil, "which", which))
        calls = []
        real_gh, real_public = P.gh_api, P.public_api
        self.addCleanup(lambda: (setattr(P, "gh_api", real_gh), setattr(P, "public_api", real_public)))
        P.gh_api = lambda path: calls.append(("gh", path)) or (200, None)
        P.public_api = lambda path: calls.append(("public", path)) or (200, None)
        shutil.which = lambda name: None
        P.default_api("x")
        shutil.which = lambda name: "/usr/bin/gh" if name == "gh" else None
        P.default_api("y")
        self.assertEqual(calls, [("public", "x"), ("gh", "y")])

    def test_a_registry_ff_with_no_valid_repo_is_refused_before_github(self):
        for spec in (dict(SPEC, repo="acme/vault?x=1"), dict(SPEC, branch="../main"), dict(SPEC, repo=None)):
            with self.assertRaises(P.Refused) as cm:
                P._ff(self.d, spec, "0" * 40, api=lambda path: self.fail("GitHub asked"))
            self.assertIn("no valid repo", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
