"""The mandate router (observe): path -> system -> keeper -> verdict."""

import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_router  # noqa: E402


class RouterTest(unittest.TestCase):
    def test_verdicts(self):
        with tempfile.TemporaryDirectory() as root:
            systems = {"bot": {"keeper": "bot_operator", "paths": ["storefront/crates/bot/**"]},
                       "ci": {"keeper": "ci_keeper", "repos": ["devops"]},
                       "docs": {"paths": ["storefront/docs/**"]}}
            positions = [{"name": "bot_operator", "_org": "mt", "_systems": systems}]
            idx = of_router.build_index(positions, {"mt": [root]})
            r = os.path.realpath(root)
            cases = {f"{r}/storefront/crates/bot/src/a.rs": ("bot", "bot_operator"),
                     f"{r}/devops/x.yaml": ("ci", "ci_keeper"),
                     f"{r}/storefront/docs/rfc.md": ("docs", None),
                     f"{r}/storefront/README.md": (None, None)}
            for path, (system, keeper) in cases.items():
                self.assertEqual(of_router.resolve(path, idx)[1:], (system, keeper), path)
            self.assertEqual(of_router.verdict("bot_operator", "bot", "bot", "bot_operator"), "in_mandate")
            self.assertEqual(of_router.verdict("liq_x", "liq", "bot", "bot_operator"), "owned_by:bot_operator")
            self.assertEqual(of_router.verdict("liq_x", "liq", "docs", None), "unowned")
            self.assertEqual(of_router.verdict("liq_x", "liq", None, None), "unowned")
            self.assertEqual(of_router.target_path("Read", {"file_path": "a/b"}, "/w"), "/w/a/b")
            self.assertEqual(of_router.target_path("Bash", {"command": "ls"}, "/w"), "/w")
            self.assertIsNone(of_router.target_path("WebFetch", {}, "/w"))

    def test_a_character_class_in_a_paths_pattern_matches(self):
        # of_studio, 2026-11-12: '[' was escaped, so a pattern with a class matched nothing (rt-v6)
        with tempfile.TemporaryDirectory() as root:
            systems = {"discovery": {"keeper": "disc_keeper", "paths": ["devops/k8s/workers/shop-[afg]*"]},
                       "rest": {"keeper": "rest_keeper", "paths": ["devops/k8s/workers/shop-[!afg]*"]}}
            idx = of_router.build_index([{"name": "disc_keeper", "_org": "o", "_systems": systems}], {"o": [root]})
            r = os.path.realpath(root)
            for name, system in (("shop-fetch.yaml", "discovery"), ("shop-zeta.yaml", "rest"),
                                 ("shop-!x.yaml", "rest"), ("shop-", None)):
                self.assertEqual(of_router.resolve(f"{r}/devops/k8s/workers/{name}", idx)[1], system, name)
            self.assertIsNone(of_router.resolve(f"{r}/devops/k8s/workers/shop-a/b.yaml", idx)[1])
            self.assertIsNone(re.match(of_router.glob_rx("a[!b]c"), "a/c"))


if __name__ == "__main__":
    unittest.main()


class ReadsTest(unittest.TestCase):
    def test_cross_position_reads_only(self):
        import of_reads
        with tempfile.TemporaryDirectory() as root:
            systems = {"bot": {"keeper": "bot_operator", "paths": ["storefront/crates/bot/**"]},
                       "liq": {"keeper": "liq_x", "paths": ["storefront/crates/liq/**"]}}
            idx = of_router.build_index([{"name": "x", "_org": "mt", "_systems": systems}], {"mt": [root]})
            r = os.path.realpath(root)
            rec = {"type": "assistant", "cwd": r, "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": f"{r}/storefront/crates/bot/a.rs"}},
                {"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": f"{r}/storefront/crates/liq/b.rs"}},
                {"type": "tool_use", "id": "t3", "name": "Grep", "input": {"pattern": "x", "path": f"{r}/README.md"}},
                {"type": "tool_use", "id": "t4", "name": "Edit", "input": {"file_path": f"{r}/storefront/crates/bot/a.rs"}}]}}
            got = of_reads.detect(rec, "liq_x", "liq", idx)
            self.assertEqual([(p["keeper"], p["tool"], t) for p, t in got], [("bot_operator", "Read", "t1")])
            self.assertEqual(of_reads.event_id("S", "t1"), of_reads.event_id("S", "t1"))


class WorktreeTest(unittest.TestCase):
    def test_worktrees_map_to_their_repository(self):
        self.assertEqual(of_router.unworktree("storefront/.claude/worktrees/feature-x/crates/bot/a.rs"), "storefront/crates/bot/a.rs")
        self.assertEqual(of_router.unworktree(".worktrees/devops-k8s-logging-v2/k8s/x.yaml", {"devops-k8s", "devops"}),
                         "devops-k8s/k8s/x.yaml")
        self.assertEqual(of_router.verdict("p", "s", None, None, "Bash"), "unscoped")
        self.assertEqual(of_router.verdict("p", "s", None, None, "Edit"), "unowned")
        with tempfile.TemporaryDirectory() as root:
            systems = {"bot": {"keeper": "bot_operator", "paths": ["storefront/crates/bot/**"]}}
            idx = of_router.build_index([{"name": "x", "_org": "mt", "_systems": systems}], {"mt": [root]})
            r = os.path.realpath(root)
            self.assertEqual(of_router.resolve(f"{r}/storefront/.claude/worktrees/w1/crates/bot/a.rs", idx)[1], "bot")


class GitdirWorktreeTest(unittest.TestCase):
    """of_studio, 2026-11-07: worktrees named <repo>-wt/<name>, <repo>-wt-<name> or .worktrees/<other> were unowned."""

    def test_a_worktree_resolves_by_its_git_file(self):
        with tempfile.TemporaryDirectory() as root:
            r = os.path.realpath(root)
            systems = {"journals": {"keeper": "journals_keeper", "paths": ["storefront/crates/journals/**"]},
                       "k8s": {"keeper": "k8s_keeper", "repos": ["devops-k8s"]}}
            idx = of_router.build_index([{"name": "x", "_org": "mt", "_systems": systems}], {"mt": [root]})
            for repo in ("storefront", "devops-k8s"):
                os.makedirs(f"{r}/{repo}/.git/worktrees")

            def wt(folder, repo, name):
                os.makedirs(f"{r}/{folder}", exist_ok=True)
                with open(f"{r}/{folder}/.git", "w") as fh:
                    fh.write(f"gitdir: {r}/{repo}/.git/worktrees/{name}\n")
            wt("storefront-wt/journals-c3", "storefront", "journals-c3")
            wt("devops-k8s-wt-arc-key", "devops-k8s", "arc-key")
            wt(".worktrees/shop-manifest-order-log", "storefront", "order-log")
            of_router._WT_CACHE.clear()
            self.assertEqual(of_router.resolve(f"{r}/storefront-wt/journals-c3/crates/journals/a.rs", idx)[1], "journals")
            self.assertEqual(of_router.resolve(f"{r}/devops-k8s-wt-arc-key/k8s/x.yaml", idx)[1], "k8s")
            self.assertEqual(of_router.resolve(f"{r}/.worktrees/shop-manifest-order-log/crates/journals/b.rs", idx)[1],
                             "journals")
            # a worktree root as the cwd is its repository's root: unscoped for a shell, as a repository root is
            sys_ = of_router.resolve(f"{r}/.worktrees/shop-manifest-order-log", idx)[1]
            self.assertEqual(of_router.verdict("p", "s", sys_, None, "Bash"), "unscoped")
            # removed worktrees in history: the name fallbacks
            self.assertEqual(of_router.unworktree("storefront-wt/old/crates/x.rs", {"storefront"}), "storefront/crates/x.rs")
            self.assertEqual(of_router.unworktree("devops-k8s-wt-nitro-l1/k8s/a", {"devops-k8s", "devops"}), "devops-k8s/k8s/a")
            self.assertTrue(of_router.VERSION >= "rt-v4")


class LifecycleTest(unittest.TestCase):
    def test_a_keeperless_system_that_is_not_live_is_shown_not_flagged(self):
        """docs/registry.md (5523cb6): only a live system asks for a keeper."""
        with tempfile.TemporaryDirectory() as root:
            systems = {"old-bot": {"paths": ["storefront/crates/old/**"], "lifecycle": "retired"},
                       "docs": {"paths": ["storefront/docs/**"]}}
            idx = of_router.build_index([{"name": "x", "_org": "mt", "_systems": systems}], {"mt": [root]})
            r = os.path.realpath(root)
            org, sid, keeper = of_router.resolve(f"{r}/storefront/crates/old/a.rs", idx)
            self.assertEqual(of_router.verdict("p", "s", sid, keeper, "Edit", lifecycle=of_router.lifecycle_of(idx, org, sid)),
                             "non_live:retired")
            org, sid, keeper = of_router.resolve(f"{r}/storefront/docs/a.md", idx)
            self.assertEqual(of_router.verdict("p", "s", sid, keeper, "Edit", lifecycle=of_router.lifecycle_of(idx, org, sid)),
                             "unowned")
            self.assertEqual(of_router.lifecycle_of([["r", "mt", "a", None, []]], "mt", "a"), "live")  # an old cache


class OutsideTest(unittest.TestCase):
    def test_outside_every_org(self):
        with tempfile.TemporaryDirectory() as root:
            idx = of_router.build_index([{"name": "x", "_org": "mt", "_systems": {"bot": {"keeper": "k", "paths": ["a/**"]}}}],
                                        {"mt": [root]})
            self.assertFalse(of_router.inside("/private/tmp/x/scratchpad/f.md", idx))
            self.assertTrue(of_router.inside(os.path.join(root, "b", "c.md"), idx))
        self.assertEqual(of_router.verdict("p", "s", None, None, "Write", outside=True), "outside")
        self.assertEqual(of_router.verdict("p", "s", None, None, "Write", outside=False), "unowned")
