"""Only of_release writes to the public repository on GitHub (docs/guard.md G18; AGENTS.md, the founder 2026-11-11):
a push, a merge or another write to a repository of the organic-framework owner is denied to every other position;
reading, comments and reviews stay open, and a user organization's own repositories are never matched. No git or gh
runs here: the repositories are folders with a git config."""

import contextlib
import io
import json
import os
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_guard  # noqa: E402
import of_hook  # noqa: E402

FW = "organic-framework/organic-framework"


def repo(root, name, url):
    path = os.path.join(root, name)
    os.makedirs(os.path.join(path, ".git"))
    with open(os.path.join(path, ".git", "config"), "w") as fh:
        fh.write(f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {url}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
    return path


class WritesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fw = repo(self.tmp.name, "framework", f"git@github.com:{FW}.git")
        self.other = repo(self.tmp.name, "other", "https://github.com/acme/storefront.git")

    def tearDown(self):
        self.tmp.cleanup()

    def writes(self, cmd, cwd=None):
        return of_guard.framework_writes("Bash", {"command": cmd}, cwd or self.fw)

    def test_pushes_and_merges_on_the_framework_are_writes(self):
        for cmd in ("git push", "git push origin main", "git push origin --delete feature", "git push --tags",
                    f"git push https://github.com/{FW}.git HEAD:main", "gh pr merge 12 --squash",
                    f"gh pr merge 12 -R {FW}", f"gh api repos/{FW}/pulls/3/merge -X PUT",
                    f"gh api repos/{FW}/statuses/abc123 -f state=success", f"gh api -X DELETE repos/{FW}/rulesets/5",
                    f"gh api --method PUT repos/{FW}/branches/main/protection --input p.json",
                    f"gh api repos/{FW}/contents/README.md -X PUT -f message=x -f content=eA==",
                    "gh api repos/{owner}/{repo}/pulls/1/merge -X PUT", "gh workflow run image.yml",
                    "gh workflow run image.yml -R organic-framework/core", "gh release create v1 -n notes",
                    "gh secret set TOKEN", "gh repo edit --visibility public",
                    f"gh api graphql -f query='mutation {{ updateRef(input: {{}}) }}' -F owner={FW}"):
            self.assertTrue(self.writes(cmd), cmd)

    def test_the_bypasses_found_in_review(self):
        """of_architect's review of PR 6: a push option's value, a remote added on the line, a remote set for the
        call, and a GraphQL merge by node id."""
        fw_url = f"https://github.com/{FW}.git"
        for cmd, cwd in (("git push -o x origin main", self.fw), ("git push --push-option=ci.skip origin main", self.fw),
                         (f"git remote add fw {fw_url} && git push fw main", self.other),
                         (f"git remote set-url origin {fw_url}; git push origin main", self.other),
                         (f"git config remote.up.url {fw_url} && git push up HEAD:main", self.other),
                         (f"git -c remote.up.url={fw_url} push up main", self.other),
                         ("git push nowhere main", self.fw),
                         ("gh api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: \"PR_k\"}) "
                          "{ clientMutationId } }'", self.other),
                         ("gh api graphql -F query=@m.graphql -f q='mutation{updateRef(input:{refId:\"R\"}){ref{id}}}'",
                          self.other)):
            self.assertTrue(self.writes(cmd, cwd=cwd), cmd)
        for cmd in ("git push -o ci.skip origin main", "gh api graphql -f query='mutation { addComment(input: {}) "
                    "{ clientMutationId } }'"):
            self.assertEqual(self.writes(cmd, cwd=self.other), [], cmd)

    def test_reading_comments_and_reviews_are_not(self):
        for cmd in ("git fetch", "git pull", "git log origin/main", "gh pr view 12", "gh pr diff 12",
                    "gh pr comment 12 -b 'Verdict: approve'", "gh pr list", f"gh api repos/{FW}/rulesets",
                    f"gh api repos/{FW}/commits/main --jq .sha", f"gh api repos/{FW}/issues/3/comments -f body=ok",
                    f"gh api repos/{FW}/pulls/3/reviews -f event=COMMENT -f body=ok", "gh run view 4",
                    "git commit -m 'git push later'", "gh api graphql -f query='{ viewer { login } }'"):
            self.assertEqual(self.writes(cmd), [], cmd)

    def test_a_user_organizations_repository_is_never_matched(self):
        for cmd in ("git push", "gh pr merge 7 --merge", "gh workflow run ci.yml", "gh api repos/acme/storefront/"
                    "pulls/7/merge -X PUT", "gh api repos/{owner}/{repo}/pulls/1/merge -X PUT"):
            self.assertEqual(self.writes(cmd, cwd=self.other), [], cmd)
        self.assertTrue(self.writes(f"gh pr merge 7 -R {FW}", cwd=self.other))

    def test_the_repository_is_where_the_command_runs(self):
        self.assertTrue(self.writes(f"cd {self.fw} && git push", cwd=self.other))
        self.assertTrue(self.writes(f"git -C {self.fw} push origin main", cwd=self.other))
        self.assertTrue(self.writes("bash -c 'git push origin main'"))
        self.assertEqual(self.writes(f"cd {self.other} && git push"), [])


class HookTest(unittest.TestCase):
    """The hook denies a framework write unless the session holds of_release (of_hook._release_guard)."""

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        r = self.root.name
        os.makedirs(os.path.join(r, "org", "registry"))
        with open(os.path.join(r, "org", "registry", "positions.yaml"), "w") as fh:
            fh.write(textwrap.dedent("""
                organization: t
                positions:
                  - {name: of_release, system: of, role: release, holder: {session: S1}}
                  - {name: of_docs, system: of, role: docs, holder: {session: S2}}
                """))
        with open(os.path.join(r, "orgs.yaml"), "w") as fh:
            fh.write("orgs:\n  - {name: t, path: org}\n")
        self.fw = repo(r, "framework", f"https://github.com/{FW}.git")
        state = os.path.join(r, "state")
        os.makedirs(state)
        self.saved = (of_events.STATE_DIR, of_hook.STATE, os.environ.get("OF_ORGS"))
        of_events.STATE_DIR = of_hook.STATE = state
        os.environ["OF_ORGS"] = os.path.join(r, "orgs.yaml")
        of_hook.OUTPUT = None

    def tearDown(self):
        of_events.STATE_DIR, of_hook.STATE = self.saved[0], self.saved[1]
        if self.saved[2] is None:
            os.environ.pop("OF_ORGS", None)
        else:
            os.environ["OF_ORGS"] = self.saved[2]
        of_hook.OUTPUT = None
        self.root.cleanup()

    def fire(self, cmd, sid):
        of_hook.OUTPUT = None
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            of_hook.handle("PreToolUse", {"hook_event_name": "PreToolUse", "session_id": sid, "cwd": self.fw,
                                          "tool_name": "Bash", "tool_input": {"command": cmd}})
        printed = out.getvalue()
        return (json.loads(printed) if printed.strip() else (of_hook.OUTPUT or {})).get("hookSpecificOutput") or {}

    def test_only_of_release_pushes_and_merges(self):
        self.assertNotEqual(self.fire("gh pr merge 12 --squash", "S1").get("permissionDecision"), "deny")
        for sid in ("S2", "S-no-position"):
            got = self.fire("gh pr merge 12 --squash", sid)
            self.assertEqual(got.get("permissionDecision"), "deny", sid)
            self.assertIn("only of_release pushes", got.get("permissionDecisionReason", ""))
        self.assertNotEqual(self.fire("gh pr comment 12 -b 'Verdict: approve'", "S2").get("permissionDecision"),
                            "deny")


if __name__ == "__main__":
    unittest.main()
