"""of import github on a fake gh: systems by changed paths, pr-link attribution, idempotent."""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_github  # noqa: E402
import of_store  # noqa: E402

SYSTEMS = {"storefront": {"github": ["M/storefront"], "paths": ["storefront/crates/atomic_bot/**"]},
           "discovery": {"github": ["M/storefront"], "paths": ["storefront/crates/discovery/**"]},
           "forecasting": {"github": ["M/forecasting"], "governed_by": "external:dana"}}
PRS = {"M/storefront": [{"number": 8, "author": {"login": "founder-gh"}, "mergedAt": "2026-10-29T10:00:00Z",
                        "files": [{"path": "README.md"}]},{"number": 7, "author": {"login": "founder-gh"}, "createdAt": "2026-10-30T09:00:00Z",
                        "mergedAt": "2026-10-30T10:00:00Z", "headRefName": "fix", "title": "Fix", "url": "u7",
                        "files": [{"path": "crates/atomic_bot/src/a.rs"}, {"path": "crates/atomic_bot/src/b.rs"},
                                  {"path": "crates/discovery/x.rs"}]}],
       "M/forecasting": [{"number": 3, "author": {"login": "founder-gh"}, "mergedAt": "2026-10-31T10:00:00Z", "files": []}]}


class GithubTest(unittest.TestCase):
    def test_import(self):
        with tempfile.TemporaryDirectory() as root:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = root
            try:
                conn = of_store.connect()
                link = of_events.record("work_record", {"source": "pr-link", "repo": "M/storefront", "number": 7},
                                        position="storefront_operator", org="acme", session="S")
                of_store.add_event(conn, link)
                for _ in range(2):  # idempotent
                    n = of_github.import_org(conn, "acme", SYSTEMS, "2026-10-25",
                                             fetcher=lambda repo, since: PRS.get(repo, []), log=lambda m: None)
                self.assertEqual(n, 3)
                rows = [json.loads(r[0]) for r in conn.execute(
                    "SELECT payload FROM event WHERE name = 'of.work_record' ORDER BY ts")]
                rows = [r for r in rows if r.get("source") == "gh"]
                self.assertEqual(len(rows), 3)
                self.assertEqual((rows[0]["systems"], rows[0]["system_basis"]), ([], "none"))  # README.md: nothing guessed
                rows = rows[1:]
                self.assertEqual(rows[0]["systems"], ["storefront", "discovery"])   # by changed paths, most files first
                self.assertEqual((rows[0]["position"], rows[0]["attribution"]), ("storefront_operator", "pr-link"))
                self.assertEqual((rows[1]["systems"], rows[1]["external"]), (["forecasting"], ["forecasting"]))
                self.assertTrue(rows[0]["paths"])  # of_studio: the changed paths, so works can match merged PRs
                # incremental: the day before the last imported merge; a re-read PR replaces its own record
                last = of_github.last_merged(conn, "acme")
                self.assertEqual(of_github.since_for(conn, "acme"),
                                 (__import__("datetime").date.fromisoformat(last[:10])
                                  - __import__("datetime").timedelta(days=1)).isoformat())
                self.assertEqual(of_github.since_for(conn, "nobody"), "2026-10-01")
                conn.close()
            finally:
                of_events.STATE_DIR = saved

    def test_a_pr_bodys_position_line_names_its_author(self):
        """of_field-analyst, 2026-11-10: 90 of 820 merged Acme PRs had a position (pr-links only)."""
        body = "Summary.\n\nPosition: storefront_search\nCo-Authored-By: Claude <noreply@anthropic.com>"
        prs = {"M/storefront": [dict(PRS["M/storefront"][1], number=9, body=body, reviews=0),
                               dict(PRS["M/storefront"][1], number=10, body="Position: nobody_here"),
                               dict(PRS["M/storefront"][0], number=11, body="Position: disc_keeper")]}
        with tempfile.TemporaryDirectory() as root:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = root
            try:
                conn = of_store.connect()
                of_github.import_org(conn, "mt", SYSTEMS, "2026-10-25", fetcher=lambda r, s: prs.get(r, []),
                                     log=lambda m: None,
                                     position_system={"storefront_search": "storefront", "disc_keeper": "discovery"})
                rows = {r["number"]: (r, pos) for pl, pos in conn.execute(
                    "SELECT payload, position FROM event WHERE name = 'of.work_record'") for r in [json.loads(pl)]}
                self.assertEqual((rows[9][0]["position"], rows[9][0]["attribution"], rows[9][1]),
                                 ("storefront_search", "trailer", "storefront_search"))
                self.assertIsNone(rows[10][0]["position"])  # a line naming no position of the organization
                # ap.unsourced-requirement reads what a PR claimed and whether anyone reviewed it (of_field-analyst)
                self.assertEqual((rows[9][0]["body"], rows[9][0]["reviews"]), (body, 0))
                self.assertIsNone(rows[11][0]["reviews"])  # not read: unknown, never zero
                self.assertEqual((rows[11][0]["systems"], rows[11][0]["system_basis"]), (["discovery"], "position"))
                conn.close()
            finally:
                of_events.STATE_DIR = saved

    def test_shared_paths_fall_back_to_the_authors_system(self):
        with tempfile.TemporaryDirectory() as root:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = root
            try:
                conn = of_store.connect()
                of_store.add_event(conn, of_events.record("work_record", {"source": "pr-link", "repo": "M/storefront",
                                                                          "number": 8}, position="disc_keeper", org="mt"))
                of_github.import_org(conn, "mt", SYSTEMS, "2026-10-25", fetcher=lambda r, s: PRS.get(r, []),
                                     log=lambda m: None, position_system={"disc_keeper": "discovery"})
                rows = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM event WHERE name = 'of.work_record'")]
                pr8 = next(r for r in rows if r.get("source") == "gh" and r["number"] == 8)
                self.assertEqual((pr8["systems"], pr8["system_basis"]), (["discovery"], "position"))
                conn.close()
            finally:
                of_events.STATE_DIR = saved



class WindowsTest(unittest.TestCase):
    def test_windows_cover_range(self):
        import datetime as dt
        w = of_github.windows("2026-10-25", dt.date(2026, 11, 6))
        self.assertEqual(w[0], ("2026-10-25", "2026-10-27"))
        self.assertEqual(w[-1][1], "2026-11-06")
        self.assertEqual(len(w), 5)


class PathRepoTest(unittest.TestCase):
    def test_paths_make_a_candidate_without_github(self):
        systems = {"bot": {"github": ["O/storefront"], "paths": ["storefront/crates/**"]},
                   "hosts": {"github": ["O/avalanchego"], "paths": ["storefront/scripts/ops/shop_*/**"]}}
        by = of_github.systems_by_repo(systems)
        self.assertEqual(of_github.assign("O/storefront", ["scripts/ops/shop_x/a.py"], by, "storefront"),
                         ["hosts"])


WORKS = {"mt-nonces": {"title": "n", "kind": "rfc", "doc": "storefront:docs/rfc/nonces.md (PR #20)", "systems": ["storefront"],
                       "match": {"prs": [20], "branches": ["claude/wallet-*"], "doc": "storefront:docs/rfc/nonces.md"}},
         "mt-core": {"title": "c", "kind": "rfc", "systems": ["contracts"],
                     "match": {"paths": ["storefront:contracts/**", "arb-contracts/**"]}},
         "mt-cbx": {"title": "x", "kind": "project", "systems": ["runners"],
                    "match": {"branches": ["claude/*cbx*"], "paths": ["devops-k8s:k8s/runners/**"]}},
         "mt-old": {"title": "o", "kind": "rfc", "state": "parked"}}


class WorksMatchTest(unittest.TestCase):
    def test_match_by_number_branch_path_and_doc(self):
        m = lambda repo, n, br, files, rs=("storefront",): of_github.match_works(WORKS, repo, n, br, files, rs)
        self.assertEqual(m("M/storefront", 20, "x", []), ["mt-nonces"])
        self.assertEqual(m("M/other", 20, "x", [], ()), [])                  # PR numbers are the doc repository's
        self.assertEqual(m("M/storefront", 5, "claude/wallet-fix", []), ["mt-nonces"])
        self.assertEqual(m("M/storefront", 5, "claude/wallet-fix", [], ()), ["mt-nonces"])  # the doc's repository
        self.assertEqual(m("M/unrelated", 5, "claude/wallet-fix", [], ()), [])  # a branch glob in another repo
        self.assertEqual(m("M/storefront", 5, "b", ["docs/rfc/nonces.md"]), ["mt-nonces"])
        self.assertEqual(m("M/storefront", 5, "b", ["contracts/src/A.sol"]), ["mt-core"])
        self.assertEqual(m("M/arb-contracts", 5, "b", ["src/A.sol"], ()), ["mt-core"])   # a plain glob: '<repo dir>/<path>'
        self.assertEqual(m("O/devops-k8s", 5, "b", ["k8s/runners/a.yaml"], ()), ["mt-cbx"])
        self.assertEqual(m("O/devops-k8s", 5, "claude/cbx-guard", [], ("runners",)), ["mt-cbx"])
        self.assertEqual(m("M/storefront", 5, "main", ["README.md"]), [])
        w = {"mt-core-v2": {"systems": ["contracts"], "match": {"branches": ["devops-k8s:claude/*core-v2*", "claude/*hop*"]}}}
        mw = lambda repo, br, rs=(): of_github.match_works(w, repo, 9, br, [], rs)
        self.assertEqual(mw("O/devops-k8s", "claude/deploy-core-v2-fra"), ["mt-core-v2"])   # repo-scoped branch rule
        self.assertEqual(mw("M/storefront", "claude/deploy-core-v2-fra"), [])                # only in that repository
        self.assertEqual(mw("O/devops-k8s", "claude/two-hop"), [])                          # a bare glob: own repos only
        self.assertEqual(mw("M/arb-contracts", "claude/two-hop", ("contracts",)), ["mt-core-v2"])
        self.assertEqual(m("M/storefront", 20, "mergify/merge-queue/main/abc", []), [])     # a queue batch: never
        self.assertEqual(m("M/storefront", 5, "gh-readonly-queue/main/pr-20-abc", ["docs/rfc/nonces.md"]), [])

    def test_records_and_pr_states_name_their_works(self):
        with tempfile.TemporaryDirectory() as root:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = root
            try:
                conn = of_store.connect()
                merged = {"M/storefront": [{"number": 20, "author": {"login": "a"}, "mergedAt": "2026-10-30T10:00:00Z",
                                           "headRefName": "claude/wallet-1", "files": [{"path": "docs/rfc/nonces.md"}]}]}
                open_ = [{"number": 31, "author": {"login": "b"}, "updatedAt": "2026-10-31T10:00:00Z", "isDraft": True,
                          "headRefName": "claude/wallet-2",
                          "latestReviews": [{"submittedAt": "2026-10-31T09:00:00Z"}, {"submittedAt": "2026-10-31T08:00:00Z"}]}]
                calls = []
                detail = lambda r, n: (calls.append(n), {"files": [{"path": "crates/atomic_bot/x.rs"}],
                                                         "commits": [{"committedDate": "2026-10-31T07:00:00Z"}]})[1]
                closed = [{"number": 20, "mergedAt": "2026-10-30T10:00:00Z", "updatedAt": "2026-10-30T10:00:00Z"},
                          {"number": 32, "mergedAt": None, "updatedAt": "2026-10-31T11:00:00Z", "closedAt": "2026-10-31T11:00:00Z",
                           "headRefName": "other"}]
                for _ in range(2):  # an unchanged PR writes nothing on the next run
                    of_github.import_org(conn, "mt", SYSTEMS, "2026-10-25", fetcher=lambda r, s: merged.get(r, []),
                                         log=lambda m: None, works=WORKS,
                                         open_fetcher=lambda r: open_ if r == "M/storefront" else [],
                                         closed_fetcher=lambda r, s: closed if r == "M/storefront" else [],
                                         detail_fetcher=detail)
                wr = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM event WHERE name = 'of.work_record'")]
                self.assertEqual(wr[0]["works"], ["mt-nonces"])
                st = {p["number"]: p for p in (json.loads(r[0]) for r in conn.execute(
                    "SELECT payload FROM event WHERE name = 'of.pr_state'"))}
                self.assertEqual(sorted(st), [31, 32])  # a merged PR is a work record, not a pr_state
                self.assertEqual((st[31]["state"], st[31]["draft"], st[31]["works"], st[31]["systems"]),
                                 ("open", True, ["mt-nonces"], ["storefront"]))
                self.assertEqual((st[31]["last_review_at"], st[31]["last_push_at"]),
                                 ("2026-10-31T09:00:00Z", "2026-10-31T07:00:00Z"))
                self.assertEqual((st[32]["state"], st[32]["works"]), ("closed", []))
                self.assertEqual(calls, [31])  # details only for a changed open PR, once
                open_[0]["updatedAt"] = "2026-11-01T10:00:00Z"  # a new push or review: one more state
                of_github.import_org(conn, "mt", SYSTEMS, "2026-10-25", fetcher=lambda r, s: [], log=lambda m: None,
                                     works=WORKS, open_fetcher=lambda r: open_ if r == "M/storefront" else [],
                                     detail_fetcher=lambda r, n: {"files": [], "commits": []})
                bad = of_github.import_org(conn, "mt", SYSTEMS, "2026-10-25", fetcher=lambda r, s: [], log=lambda m: None,
                                           works=WORKS, open_fetcher=lambda r: 1 / 0)
                self.assertEqual(bad, 0)  # a PR-state failure never stops the import
                self.assertEqual(conn.execute("SELECT count(*) FROM event WHERE name = 'of.pr_state'").fetchone()[0], 3)
            finally:
                of_events.STATE_DIR = saved


class RematchTest(unittest.TestCase):
    def test_a_changed_works_map_rematches_stored_records_without_github(self):
        with tempfile.TemporaryDirectory() as root:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = root
            try:
                conn = of_store.connect()
                merged = {"M/storefront": [{"number": 40, "author": {"login": "a"}, "mergedAt": "2026-10-30T10:00:00Z",
                                           "headRefName": "claude/spam-1", "files": [{"path": "crates/x.rs"}]}]}
                works = {"w1": {"title": "t", "kind": "rfc", "systems": ["storefront"], "match": {"branches": ["claude/wallet-*"]}}}
                of_github.import_org(conn, "mt", SYSTEMS, "2026-10-25", fetcher=lambda r, s: merged.get(r, []),
                                     log=lambda m: None, works=works)
                get = lambda: json.loads(conn.execute("SELECT payload FROM event WHERE name = 'of.work_record'").fetchone()[0])["works"]
                self.assertEqual(get(), [])
                works["w1"]["match"]["branches"].append("claude/spam-*")   # the keeper sharpens the match
                n = of_github.import_org(conn, "mt", SYSTEMS, "2026-10-25", fetcher=lambda r, s: [], log=lambda m: None,
                                         works=works)
                self.assertEqual((n, get()), (0, ["w1"]))                 # no PR fetched, the stored record re-matched
                rec = json.loads(conn.execute("SELECT record FROM event WHERE name = 'of.work_record'").fetchone()[0])
                self.assertEqual(rec["of.payload"]["works"], ["w1"])
                self.assertEqual(of_github.rematch(conn, "mt", SYSTEMS, works, log=lambda m: None), 0)  # unchanged map
            finally:
                of_events.STATE_DIR = saved


class LocalCommitsTest(unittest.TestCase):
    """of_studio, 2026-11-07: the OF team commits on main and never pushes; Studio needs its commits as work records."""

    def test_commits_on_main_become_work_records(self):
        import subprocess
        with tempfile.TemporaryDirectory() as root:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = os.path.join(root, "state")
            try:
                repo = os.path.join(root, "organic-framework")
                git = lambda *a: subprocess.run(["git", "-C", repo, "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
                                                 "-c", "user.email=t@t", "-c", "user.name=t", *a],
                                                capture_output=True, text=True, check=True)
                os.makedirs(os.path.join(repo, "bin"))
                git("init", "-q", "-b", "main")
                for i, (path, msg) in enumerate([("bin/of_a.py", "First\n\nBody.\n\nPosition: of_platform\n\nCo-Authored-By: X <x@y>"),
                                                 ("docs/b.md", "Second, no trailer")]):
                    os.makedirs(os.path.dirname(os.path.join(repo, path)), exist_ok=True)
                    with open(os.path.join(repo, path), "w") as fh:
                        fh.write(str(i))
                    git("add", path)
                    git("commit", "-qm", msg)
                systems = {"of-client": {"paths": ["organic-framework/bin/**"]}}
                conn = of_store.connect()
                for _ in range(2):  # deterministic ids: a re-run adds nothing
                    of_github.import_local(conn, "ofb", systems, [repo], "2020-01-01", works={}, log=lambda m: None)
                rows = [json.loads(r[0]) for r in conn.execute(
                    "SELECT payload FROM event WHERE name = 'of.work_record' ORDER BY ts")]
                self.assertEqual(len(rows), 2)
                a, b = rows
                self.assertEqual((a["source"], a["unit"], a["repo"], a["position"], a["paths"], a["systems"]),
                                 ("git", "commit", "organic-framework", "of_platform", ["bin/of_a.py"], ["of-client"]))
                self.assertEqual((b["position"], b["paths"], b["systems"], b["title"]), (None, ["docs/b.md"], [], "Second, no trailer"))
                self.assertIn("Body.", a["message"])  # the commit message's body, masked and clipped
                self.assertIsNone(b["message"])
                self.assertTrue(a["sha"] and a["merged_at"].endswith("Z"))  # UTC, comparable with every event's ts
            finally:
                of_events.STATE_DIR = saved


class DefaultBranchTest(unittest.TestCase):
    """The founder, 2026-11-11: of import git read only main; a repository's default may be another branch."""

    def repo(self, root, name, branch):
        import subprocess
        path = os.path.join(root, name)
        os.makedirs(path)
        git = lambda *a: subprocess.run(["git", "-C", path, "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
                                         "-c", "user.email=t@t",
                                         "-c", "user.name=t", *a], capture_output=True, text=True, check=True)
        git("init", "-q", "-b", branch)
        with open(os.path.join(path, "a"), "w") as fh:
            fh.write("x")
        git("add", "a")
        git("commit", "-qm", "first")
        return path, git

    def test_resolution_order_and_a_plain_refusal(self):
        with tempfile.TemporaryDirectory() as root:
            m, _ = self.repo(root, "m", "master")
            self.assertEqual(of_github.default_branch(m)[0], "master")
            d, git = self.repo(root, "d", "develop")
            git("branch", "master")  # two candidates, no origin/HEAD: not guessed
            ref, why = of_github.default_branch(d)
            self.assertIsNone(ref)
            self.assertIn("name the default in orgs.yaml", why)
            self.assertEqual(of_github.default_branch(d, "develop"), ("develop", "orgs.yaml"))
            self.assertIsNone(of_github.default_branch(d, "nope")[0])
            git("update-ref", "refs/remotes/origin/develop", "HEAD")
            git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/develop")
            self.assertEqual(of_github.default_branch(d), ("develop", "origin/HEAD"))

    def test_import_reads_the_default_and_says_when_it_cannot(self):
        with tempfile.TemporaryDirectory() as root:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = os.path.join(root, "state")
            try:
                m, _ = self.repo(root, "m", "trunk")
                d, git = self.repo(root, "d", "develop")
                git("branch", "master")
                said = []
                conn = of_store.connect()
                n = of_github.import_local(conn, "o", {}, [m, d], "2020-01-01", works={}, log=said.append)
                self.assertEqual(n, 1)  # trunk's one commit; d is not guessed
                self.assertTrue(any("m: 1 commits on trunk" in x for x in said), said)
                self.assertTrue(any(x.startswith("  d: not read:") for x in said), said)
                n = of_github.import_local(conn, "o", {}, [m, d], "2020-01-01", works={}, log=said.append,
                                           branches={"d": "develop"})
                self.assertEqual(n, 1)  # d now, on the branch orgs.yaml names
                home = os.path.expanduser("~")
                under_home = tempfile.mkdtemp(dir=home)  # the repository under HOME, so its key can start with ~/
                e, git_e = self.repo(under_home, "e", "develop")
                git_e("branch", "master")
                key = "~" + e[len(home):]  # a path as orgs.yaml writes it
                self.assertTrue(key.startswith("~/"), key)
                n = of_github.import_local(conn, "o", {}, [e], "2020-01-01", works={}, log=said.append,
                                           branches={key: "develop"})
                self.assertEqual(n, 1)  # of_studio's review of PR 12: a ~ path key matches the expanded root
                self.assertEqual(of_github.import_local(conn, "o", {}, [e], "2020-01-01", works={}, log=said.append,
                                                        branches=["develop"]), 0)  # not a mapping: ignored, said
                self.assertTrue(any("is not a mapping" in x for x in said))
                conn.close()
            finally:
                of_events.STATE_DIR = saved


if __name__ == "__main__":
    unittest.main()
