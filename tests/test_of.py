"""Unit tests for bin/of. Pure logic only: no herdr calls.

Run: python3 -m unittest discover -s tests
"""

import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
_spec = importlib.util.spec_from_loader("of_cli", _loader)
of = importlib.util.module_from_spec(_spec)
_loader.exec_module(of)


def pos(name, system="sys", role="r", session=None, **kw):
    p = {"name": name, "system": system, "role": role, "holder": {"session": session}}
    p.update(kw)
    return p


def agent(pane, session, name=None, tokens=None, status="idle", ws="w1", tab=None):
    return {"pane_id": pane, "agent": "claude", "name": name, "tokens": tokens or {},
            "agent_status": status, "workspace_id": ws, "tab_id": tab or pane.replace(":p", ":t"),
            "agent_session": {"value": session} if session else None, "cwd": "/x"}


class RegistryTest(unittest.TestCase):
    def write(self, root, rel, text):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(textwrap.dedent(text))

    def test_reads_listed_org_registries_and_flags_problems(self):
        with tempfile.TemporaryDirectory() as root:
            self.write(root, "of-build/registry/positions.yaml", """
                positions:
                  - {name: of_a, system: of, role: a, aliases: [old-a], status: active}
                  - {name: Bad.Name, system: of, role: b}
                """)
            self.write(root, "mt/org/registry/positions.yaml", """
                positions:
                  - {name: acme_x, system: mt, role: x, aliases: [old-a], summary: "%s", status: gone}
                """ % ("y" * 41))
            self.write(root, "conf/orgs.yaml", """
                orgs:
                  - {name: build, path: ../of-build}
                  - {name: mt, path: %s/mt/org}
                  - {name: ghost, path: ../nowhere}
                """ % root)
            positions, problems = of.load_positions(os.path.join(root, "conf/orgs.yaml"))
            self.assertEqual([p["name"] for p in positions], ["of_a", "acme_x"])
            self.assertEqual(positions[1]["_org"], "mt")
            text = "\n".join(problems)
            self.assertIn("invalid name 'Bad.Name'", text)
            self.assertIn("'old-a' is used by both of_a and acme_x", text)
            self.assertIn("longer than 40", text)
            self.assertIn("status 'gone'", text)
            self.assertIn("ghost:registry/positions.yaml: missing", text)

    def test_works_warnings_are_the_registry_checks_only(self):
        """2026-11-12: the loop check's new warning printed 14 lines on every `of` call."""
        with tempfile.TemporaryDirectory() as root:
            self.write(root, "org/registry/positions.yaml", """
                positions:
                  - {name: acme_x, system: mt, role: x, status: active}
                works:
                  w-a:
                    title: A
                    kind: speed
                    owner: acme_x
                    loop:
                      target: {metric: m, money: n}
                      ladder: [{rung: oracle, accept: ok}]
                  w-b: {title: B, kind: speed, owner: nobody_here}
                """)
            self.write(root, "conf/orgs.yaml", "orgs:\n  - {name: wk, path: %s/org}\n" % root)
            of.BROKEN.discard("wk")  # another test may leave its organization marked broken
            orgs = os.path.join(root, "conf/orgs.yaml")
            quick, deep = ("\n".join(of.load_positions(orgs, deep=d)[1]) for d in (False, True))
            self.assertNotIn("has no segment", quick)
            self.assertIn("has no segment", deep)
            self.assertIn("nobody_here", quick)  # a problem still prints everywhere

    def test_a_broken_working_copy_falls_back_to_the_last_commit(self):
        """A user organization, 2026-11-11: one minute of invalid YAML mid-edit failed every session's of send and of inbox."""
        with tempfile.TemporaryDirectory() as root:
            reg = "org/registry/positions.yaml"
            self.write(root, reg, """
                positions:
                  - {name: acme_x, system: mt, role: x, status: active}
                """)
            self.write(root, "conf/orgs.yaml", "orgs:\n  - {name: mt, path: %s/org}\n" % root)
            git = ["git", "-C", os.path.join(root, "org"), "-c", "commit.gpgsign=false", "-c", "user.name=t",
                   "-c", "user.email=t@example.com"]
            subprocess.run(git + ["init", "-q"], check=True)
            subprocess.run(git + ["add", "-A"], check=True)
            subprocess.run(git + ["commit", "-qm", "r"], check=True)
            with open(os.path.join(root, reg), "a") as fh:
                fh.write("  - {name: acme_y, summary: 'it's broken'}\n")  # an unescaped apostrophe
            of.BROKEN.discard("mt")
            positions, problems = of.load_positions(os.path.join(root, "conf/orgs.yaml"))
            self.assertEqual([p["name"] for p in positions], ["acme_x"])
            self.assertIn("using its last committed version (HEAD)", "\n".join(problems))  # registry check still fails
            os.rename(os.path.join(root, "org", ".git"), os.path.join(root, "org", "nogit"))
            positions, problems = of.load_positions(os.path.join(root, "conf/orgs.yaml"))
            self.assertEqual(positions, [])  # no commit to stand in: skipped, as before
            self.assertIn("its positions are skipped", "\n".join(problems))

    def test_org_agents_reads_the_entry(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "orgs.yaml")
            with open(path, "w") as fh:
                fh.write("orgs:\n  - {name: a, path: x, agents: {model: opus, effort: xhigh}}\n  - {name: b, path: y}\n")
            self.assertEqual(of.org_agents("a", path), {"model": "opus", "effort": "xhigh"})
            self.assertEqual(of.org_agents("b", path), {})

    def test_a_dead_relay_is_said(self):
        """A new organization's install, 2026-11-11: no relay ran and a conductor's first message never arrived, silently."""
        import subprocess as sp
        saved = sp.run
        try:
            sp.run = lambda *a, **k: type("R", (), {"stdout": "", "returncode": 1})()
            self.assertFalse(of.relay_alive())
            self.assertIn("of relay on", of._relay_warning())
            sp.run = lambda *a, **k: type("R", (), {"stdout": "99999999\n", "returncode": 0})()
            self.assertTrue(of.relay_alive())
            self.assertIsNone(of._relay_warning())
        finally:
            sp.run = saved

    def test_prompts_mine_lists_the_persons_prompts_with_ids(self):
        """of_onboarding, 2026-11-11: a provisional position cites the event id of the head's typed yes."""
        import argparse
        import contextlib
        import io
        import of_events
        with tempfile.TemporaryDirectory() as d:
            saved, of_events.STATE_DIR = of_events.STATE_DIR, d
            os.environ["CLAUDE_CODE_SESSION_ID"] = "S-head"
            try:
                yes = of_events.emit("human_prompt", {"origin": "human", "text": "yes, start it\nthanks"}, session="S-head")
                of_events.emit("human_prompt", {"origin": "relay", "text": "a peer's words"}, session="S-head")
                of_events.emit("human_prompt", {"origin": "human", "text": "another session"}, session="S-other")
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    of.cmd_prompts(argparse.Namespace(mine=True, last=10, all=False))
                lines = out.getvalue().strip().splitlines()
                self.assertEqual(len(lines), 1)
                self.assertTrue(lines[0].startswith(yes) and lines[0].endswith("yes, start it"))
            finally:
                of_events.STATE_DIR = saved
                os.environ.pop("CLAUDE_CODE_SESSION_ID", None)

    def test_capacity_asks_give_every_field_of_ask_reads(self):
        """2026-11-11: of ask gained --where and --escalates, and of_capacity's asks (built as a Namespace) failed with
        AttributeError until fixed; the same happened on 2026-11-06 with task."""
        import inspect
        import re as _re
        reads = set(_re.findall(r"(?<!getattr\()args\.([a-z_]+)", inspect.getsource(of.cmd_ask)))
        given = set(_re.findall(r"([a-z_]+)=", inspect.getsource(of._capacity_ask).split("Namespace(", 1)[1]))
        self.assertEqual(sorted(reads - given), [])

    def test_capacity_ask_with_no_capacity_position_is_a_machine_card(self):
        """2026-11-12 (of_capacity): on a machine whose registries have no capacity position, cmd_ask refused the
        sender and the balancer's ask was lost; it is filed as a machine card to the owner instead."""
        import of_events
        evs, saved = [], of_events.emit
        of_events.emit = lambda kind, payload, **kw: evs.append({"kind": kind, "payload": payload, **kw}) or "e1"
        try:
            of._capacity_ask("Move the positions?", "move now|wait for the reset", positions=[])
            of._capacity_ask(None, task="Log in to the account a\n1. run it", positions=[])
            of._capacity_ask("Sign in to the account b", task="1. open it\n2. sign in", positions=[])
        finally:
            of_events.emit = saved
        qs = [e for e in evs if e.get("kind") == "agent_question"]
        self.assertEqual(len(qs), 3)
        card, task, titled = (q["payload"] for q in qs)
        self.assertEqual((card["to"], card["source"]), ("founder", "capacity"))
        self.assertEqual(card["questions"][0]["options"], ["move now", "wait for the reset"])
        self.assertTrue(task["task"])
        self.assertEqual(task["questions"][0]["question"], "Log in to the account a")
        self.assertEqual(titled["questions"][0]["question"], "Sign in to the account b")  # the title is kept
        self.assertEqual(titled["steps"], "1. open it\n2. sign in")

    def test_the_ingest_worker_drains_the_spool_after_each_transcript(self):
        """2026-11-11 (of_server 3f2d224): under load the hook spooled its records, and the worker drained the spool
        only after the whole batch of transcripts, minutes later; the relay took a typed message for lost."""
        import argparse
        import of_events
        import of_ship
        import of_store
        calls = []
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "pending"))
            for i in range(3):
                with open(os.path.join(d, "pending", f"m{i}"), "w") as fh:
                    fh.write(f"/t/{i}.jsonl")
            saved = (of_events.STATE_DIR, of_store.drain_spool, of_store.with_subagents, of._ingest_with_reads,
                     of_ship.ship)
            of_events.STATE_DIR = d
            of_store.drain_spool = lambda conn: calls.append("drain") or 0
            of_store.with_subagents = lambda p: [p]
            of._ingest_with_reads = lambda conn, p: calls.append(p) or 1
            of_ship.ship = lambda log=None: None
            try:
                of._ingest_worker(None, argparse.Namespace(quiet=True))
            finally:
                (of_events.STATE_DIR, of_store.drain_spool, of_store.with_subagents, of._ingest_with_reads,
                 of_ship.ship) = saved
        self.assertEqual(calls, ["drain", "/t/0.jsonl", "drain", "/t/1.jsonl", "drain", "/t/2.jsonl", "drain"])

    def test_yaml_traps(self):
        p = {"name": "x_y", "status": {"work": "active"}, "aliases": "old",
             "notes": {"Waits for runners_coordinator after the CA window.": None}}
        traps = "\n".join(of.yaml_traps(p))
        self.assertIn("status must be a string", traps)
        self.assertIn("aliases must be a list", traps)
        self.assertIn("has no value", traps)
        self.assertEqual(of.yaml_traps({"name": "x_y", "status": "active", "holder": {"session": "s"}}), [])

    def test_systems_map_checks(self):
        systems = {"storefront": {"kind": "target", "keeper": "storefront_operator", "repos": ["storefront"],
                                "paths": ["storefront/crates/atomic_bot/**"],
                                "depends_on": [{"system": "k8s", "kind": "enabled_by"}]},
                   "k8s": {"kind": "enabling", "governed_by": "self"},
                   "search": {"keeper": "nobody", "paths": ["storefront/crates/atomic_bot/**"],
                              "depends_on": [{"system": "ghost"}, {"system": "k8s", "kind": "loves"}]},
                   "a": {"parent": "b"}, "b": {"parent": "a"},
                   "x": {"repos": ["shared"]}, "y": {"repos": ["shared"], "governed_by": "them"},
                   "Bad.Id": {}}
        text = "\n".join(of.check_systems(systems, {"storefront_operator"}))
        for expected in ("keeper 'nobody'", "'ghost' is not a system", "kind 'loves'", "storefront (storefront/crates/atomic_bot/**) and search",
                         "claimed whole by x, y", "governed_by 'them'", "invalid system id", "cycle"):
            self.assertIn(expected, text)
        self.assertEqual(of.check_systems({"k8s": {"kind": "enabling"}}, set()), [])
        edges = {"k8s": {}, "a": {"depends_on": [{"system": "k8s", "observed_by": "runtime", "evidence": ["x: y"]},
                                                 {"system": "k8s", "observed_by": "seen"},
                                                 {"system": "k8s", "evidence": {"no": 1}}]}}
        text = "\n".join(of.check_systems(edges, set()))
        self.assertIn("observed_by 'seen'", text)
        self.assertIn("evidence must be", text)
        self.assertEqual(text.count("edge to k8s"), 2)

    def test_mandate_fields(self):
        self.assertEqual(of.check_mandate_fields({"response_window": 45, "mandate_functions": ["hr", "goals"],
                                                  "mandate_exit": {"done_when": "x", "review": "2026-12-01"}}), [])
        text = "\n".join(of.check_mandate_fields({"response_window": True, "mandate_exit": {"review": "2026-12-01"},
                                                   "mandate_functions": ["sales"]}))
        for expected in ("response_window", "done_when", "'sales'"):
            self.assertIn(expected, text)

    def test_glob_intersection(self):
        yes = [("marketplace.*.toml", "*.discovery.toml"),          # acme_org's case: marketplace.discovery.toml
               ("storefront/**", "storefront/crates/x/*.rs"), ("a/*/c", "a/b/*"), ("x[0-9].toml", "x5.*"),
               ("**/Cargo.toml", "storefront/Cargo.toml")]
        no = [("storefront/crates/a/**", "storefront/crates/b/**"), ("a/*.rs", "a/b/c.rs"), ("*.toml", "*.yaml"),
              ("x[0-4].toml", "x[5-9].toml")]
        for a, b in yes:
            self.assertTrue(of.globs_intersect(a, b), (a, b))
        for a, b in no:
            self.assertFalse(of.globs_intersect(a, b), (a, b))

    def test_a_negated_class_matches_what_it_says(self):
        # a user organization, 2026-11-12: [!x] was read as a regex class, matching only "!" and "x"
        for pat in ("src/[!t]*.py", "src/[^t]*.py"):
            rx = of.glob_regex(pat)
            self.assertTrue(rx.match("src/main.py"), pat)
            self.assertFalse(rx.match("src/test.py"), pat)
            self.assertFalse(rx.match("src//a.py"), pat)
        self.assertTrue(of.glob_regex("x[0-9].toml").match("x5.toml"))
        self.assertFalse(of.glob_regex("x[0-9].toml").match("x!.toml"))

    def test_path_conflicts_use_real_files_when_the_repo_is_local(self):
        with tempfile.TemporaryDirectory() as root:
            repo = os.path.join(root, "cfg")
            os.makedirs(repo)
            subprocess.run(["git", "init", "-q", repo], check=True)
            for f in ("marketplace.discovery.toml", "shop.discovery.toml", "marketplace.bot.toml"):
                open(os.path.join(repo, f), "w").close()
            subprocess.run(["git", "-C", repo, "add", "."], check=True)
            claims = [("rh", "cfg/marketplace.*.toml"), ("disc", "cfg/*.discovery.toml"), ("eth", "cfg/eth.*.toml")]
            out = of.path_conflicts(claims, [root])
            self.assertEqual(len(out), 2)
            self.assertIn("eth (cfg/eth.*.toml) matches no tracked file in cfg", out[0])
            self.assertIn("both claim 1 file(s), e.g. cfg/marketplace.discovery.toml", out[1])
            self.assertEqual([x for x in of.path_conflicts([("eth", "cfg/eth.*.toml"), ("rh", "cfg/eth.x.toml.bak")], [root])
                              if "both claim" in x], [])
            self.assertIn("can claim the same path",                       # not local: by pattern
                          of.path_conflicts([("rh", "marketplace.*.toml"), ("d", "*.discovery.toml")], [root])[0])

    def test_missing_orgs_file(self):
        with self.assertRaisesRegex(of.OfError, "no organizations listed"):
            of.load_positions("/nonexistent/orgs.yaml")


class ApplyTest(unittest.TestCase):
    def test_from_scratch_reproduces_names_tokens_tabs_workspace(self):
        positions = [pos("of_a", "organic-framework", "architect", "s1", summary="design", _workspace="organic-framework"),
                     pos("of_b", "organic-framework", "biz", "s2", _workspace="organic-framework")]
        agents = [agent("w3:p2", "s1", tokens={}, ws="w3"), agent("w3:p4", "s2", name="old", tokens={"summary": "x"}, ws="w3")]
        tabs = [{"tab_id": "w3:t2", "pane_count": 1, "label": "1"},
                {"tab_id": "w3:t4", "pane_count": 1, "label": "biz"}]
        workspaces = [{"workspace_id": "w3", "label": "1"}]
        cmds, notes = of.plan_apply(positions, agents, tabs, workspaces)
        self.assertIn(["agent", "rename", "w3:p2", "of_a"], cmds)
        self.assertIn(["agent", "rename", "w3:p4", "of_b"], cmds)
        self.assertIn(["pane", "rename", "w3:p2", "■ of_a ■"], cmds)
        self.assertIn(["pane", "report-metadata", "w3:p2", "--source", "of:registry",
                       "--token", "system=organic-framework", "--token", "role=architect",
                       "--token", "summary=design"], cmds)
        self.assertIn(["pane", "report-metadata", "w3:p4", "--source", "of:registry",
                       "--token", "system=organic-framework", "--token", "role=biz",
                       "--clear-token", "summary"], cmds)
        self.assertIn(["tab", "rename", "w3:t2", "architect"], cmds)
        self.assertNotIn(["tab", "rename", "w3:t4", "biz"], cmds)
        self.assertIn(["workspace", "rename", "w3", "organic-framework"], cmds)
        self.assertEqual(notes, [])

    def test_idempotent(self):
        positions = [pos("of_a", "of", "a", "s1", summary="d", _workspace="of")]
        agents = [agent("w1:p1", "s1", name="of_a", tokens={"system": "of", "role": "a", "summary": "d"})]
        cmds, _ = of.plan_apply(positions, agents, [{"tab_id": "w1:t1", "pane_count": 1, "label": "a"}],
                                [{"workspace_id": "w1", "label": "of"}], {"w1:p1": "■ of_a ■"})
        self.assertEqual(cmds, [])

    def test_reports_unheld_unknown_and_name_clash(self):
        positions = [pos("of_a", session="s1"), pos("of_b", session="gone")]
        agents = [agent("w1:p1", "s1"), agent("w1:p2", "s9", name="of_b"), agent("w1:p3", "s8")]
        cmds, notes = of.plan_apply(positions, agents, [], [])
        text = "\n".join(notes)
        self.assertIn("no live holder: of_b (session gone); a live agent at w1:p2 has this name", text)
        self.assertIn("no position: pane w1:p2", text)
        self.assertIn("no position: pane w1:p3", text)

    def test_new_position_binds_by_canonical_name_and_asks_to_confirm(self):
        positions = [pos("of_lab", session=None, _file="build:registry/positions.yaml")]
        a = agent("w1:p9", "s77")
        a["terminal_title_stripped"] = "of_lab"
        cmds, notes = of.plan_apply(positions, [a], [], [])
        self.assertIn(["agent", "rename", "w1:p9", "of_lab"], cmds)
        self.assertTrue(any(n.startswith("bound by name: of_lab -> pane w1:p9, session s77") for n in notes))

    def test_does_not_steal_a_name_held_by_another_pane(self):
        positions = [pos("of_a", session="s1")]
        agents = [agent("w1:p1", "s1"), agent("w1:p2", "s2", name="of_a")]
        cmds, notes = of.plan_apply(positions, agents, [], [])
        self.assertNotIn(["agent", "rename", "w1:p1", "of_a"], cmds)
        self.assertIn("of_a: name is taken by pane w1:p2; not renamed", notes)

    def test_mixed_workspace_is_not_relabelled(self):
        positions = [pos("of_a", "of", session="s1")]
        agents = [agent("w1:p1", "s1"), agent("w1:p2", "s2")]
        cmds, _ = of.plan_apply(positions, agents, [], [{"workspace_id": "w1", "label": "1"}])
        self.assertFalse([c for c in cmds if c[0] == "workspace"])


    def test_workspace_is_the_organization_and_the_desk_is_left_alone(self):
        a1 = pos("acme_org", "mt", "org", "s1", _workspace="acme")
        a2 = pos("ci_keeper", "ci", "keeper", "s2", _workspace="acme")
        a3 = pos("of_a", "of", "a", "s3", _workspace="organic-framework")
        ags = [agent("w4:p1", "s1", ws="w4", tab="w4:t1"), agent("w4:p2", "s2", ws="w4", tab="w4:t1")]
        ren = lambda ps, ags, ws, tabs=(): [c for c in of.plan_apply(ps, ags, list(tabs), ws)[0] if c[0] in ("workspace", "tab")]
        self.assertEqual(ren([a1, a2], ags, [{"workspace_id": "w4", "label": "4"}]),
                         [["workspace", "rename", "w4", "acme"]])
        self.assertEqual(ren([a1, a2], ags, [{"workspace_id": "w4", "label": "acme"}]), [])
        desk = [agent("w6:p1", "s1", ws="w6", tab="w6:t1"), agent("w6:p3", "s3", ws="w6", tab="w6:t3")]
        self.assertEqual(ren([a1, a3], desk, [{"workspace_id": "w6", "label": "desk"}],
                             [{"tab_id": "w6:t1", "pane_count": 1, "label": "x"}]), [])

    def test_go_strips_label_markers(self):
        p = [pos("of_a", session="s1")]
        self.assertEqual(of.resolve("■ of_a ■", p, [agent("w1:p1", "s1")])["pane_id"], "w1:p1")


class WhoTest(unittest.TestCase):
    def test_owner_first_parked_last(self):
        positions = [pos("of_idle", session="s1", status="active"),
                     pos("of_parked", session="s2", status="parked"),
                     pos("of_blocked", session="s3", status="parked"),
                     pos("of_off", session="none")]
        agents = [agent("w1:p1", "s1"), agent("w1:p2", "s2"),
                  agent("w1:p3", "s3", status="blocked"), agent("w1:p4", "s4", name="stray")]
        names = [r["name"] for r in of.who_rows(positions, agents)]
        self.assertEqual(names[0], "of_blocked")
        self.assertEqual(names[-1], "of_parked")
        self.assertLess(names.index("of_idle"), names.index("stray"))
        self.assertIn("of_off", names)

    def test_waiting_on_owner_counts_as_needing_the_owner(self):
        positions = [pos("of_a", session="s1"), pos("of_w", session="s2", waiting_on="owner"),
                     pos("of_p", session="s3", waiting_on="of_a")]
        agents = [agent("w1:p1", "s1", status="working"), agent("w1:p2", "s2"), agent("w1:p3", "s3")]
        names = [r["name"] for r in of.who_rows(positions, agents)]
        self.assertEqual(names[0], "of_w")

    def test_waiting_order_owner_peer_external(self):
        positions = [pos("of_n", session="s1"), pos("of_x", session="s2", waiting_on="external:kernel-upstream"),
                     pos("of_p", session="s3", waiting_on="of_n"), pos("of_o", session="s4", waiting_on="owner")]
        agents = [agent(f"w1:p{i}", f"s{i}") for i in range(1, 5)]
        self.assertEqual([r["name"] for r in of.who_rows(positions, agents)], ["of_o", "of_p", "of_x", "of_n"])
        self.assertIsNone(of.waiting_rank("external:"))
        self.assertIsNone(of.waiting_rank("Bad Name"))


class GoTest(unittest.TestCase):
    def setUp(self):
        self.positions = [pos("of_architect", "of", "architect", "s1", aliases=["organic-framework-73"]),
                          pos("of_field-analyst", "of", "field-analyst", "s2"),
                          pos("of_biz-analyst", "of", "biz-analyst", "s3"),
                          pos("of_gone", "of", "gone", "s0")]
        self.agents = [agent("w3:p2", "s1", name="of_architect"), agent("w3:p3", "s2"),
                       agent("w3:p4", "s3"), agent("w4:p1", "s4", name="acme_org")]

    def go(self, q):
        return of.resolve(q, self.positions, self.agents)["pane_id"]

    def test_names_aliases_roles_panes_sessions(self):
        self.assertEqual(self.go("of_architect"), "w3:p2")
        self.assertEqual(self.go("organic-framework-73"), "w3:p2")
        self.assertEqual(self.go("architect"), "w3:p2")
        self.assertEqual(self.go("of_field-analyst"), "w3:p3")
        self.assertEqual(self.go("w3:p4"), "w3:p4")
        self.assertEqual(self.go("s3"), "w3:p4")
        self.assertEqual(self.go("acme_org"), "w4:p1")
        self.assertEqual(self.go("of_biz"), "w3:p4")

    def test_rejects_ambiguous_unknown_offline(self):
        with self.assertRaisesRegex(of.OfError, "ambiguous: of_architect, of_biz-analyst"):
            self.go("of_")  # prefix of several
        with self.assertRaisesRegex(of.OfError, "no position or live agent"):
            self.go("nobody")
        with self.assertRaisesRegex(of.OfError, "has no live holder"):
            self.go("of_gone")


if __name__ == "__main__":
    unittest.main()


import sys as _sys
_sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_desk  # noqa: E402

class DeskTest(unittest.TestCase):
    """The routing rules are in test_case.py (the case desk, 2026-11-09)."""

    def test_since_resets_on_status_change(self):
        s = of_desk.update_since({"p": ["idle", 5], "q": ["idle", 5]},
                                 [{"pane_id": "p", "agent_status": "idle"}, {"pane_id": "q", "agent_status": "done"}], 50)
        self.assertEqual(s, {"p": ["idle", 5], "q": ["done", 50]})


class UnitsTest(unittest.TestCase):
    def test_check_units(self):
        systems = {"storefront": {}, "storefront-hosts": {}, "k8s": {}}
        units = {"shop": {"name": "Shop", "owner": "acme_owner", "systems": ["storefront", "storefront-hosts"]},
                 "infra": {"owner": "ghost", "systems": ["k8s", "storefront", "nope"]}, "Bad.Id": {"owner": "acme_owner"}}
        text = "\n".join(of.check_units(units, {"acme_owner"}, systems))
        for expected in ("owner 'ghost'", "'storefront' is in two units", "'nope' is not in the systems map",
                         "invalid unit id", "systems must be a non-empty list"):
            self.assertIn(expected, text)
        self.assertEqual(of.check_units({"shop": units["shop"]}, {"acme_owner"}, systems), [])
        self.assertEqual(of.unit_of("storefront-hosts", units), "shop")
        self.assertIsNone(of.unit_of("other", units))

    def test_plan_layout(self):
        of.ORG_SHORT["mt"] = "ac"

        def up(name, system, sess, unit="shop", status="active"):
            return pos(name, system=system, session=sess, _org="mt", _workspace="acme", _unit=unit, status=status)
        names = [f"b{i}" for i in range(6)]
        positions = [up(n, "storefront", f"s{i}", status="parked" if i == 0 else "active") for i, n in enumerate(names)]
        positions += [up("h", "storefront-hosts", "sh"), up("d", "other", "sd", unit=None)]
        agents = [agent(f"wD:p{i}", f"s{i}", ws="wD", tab="wD:t1", status="working" if i == 5 else "idle")
                  for i in range(6)]
        agents += [agent("wD:p9", "sh", ws="wD", tab="wD:t2"), agent("w1:p4", "sd", ws="w1", tab="w1:t4")]
        workspaces = [{"workspace_id": "w1", "label": "acme"}, {"workspace_id": "wD", "label": "ac·shop"}]
        tabs = [{"tab_id": "wD:t1", "workspace_id": "wD", "pane_count": 6, "label": "storefront"},
                {"tab_id": "wD:t2", "workspace_id": "wD", "pane_count": 1, "label": "storefront-hosts"}]
        area = {"x": 0, "y": 0, "width": 340, "height": 98}
        layouts = {"wD:t2": {"area": area, "panes": [{"pane_id": "wD:p9", "rect": dict(area)}]}}
        plan = of.plan_layout(positions, agents, tabs, workspaces, layouts=layouts)
        got = [(w, label, [n for _, n in chunk], ok) for w, label, chunk, ok in plan]
        self.assertEqual(got, [("ac·shop", "storefront", ["b5", "b1", "b2", "b3"], False),  # working first
                               ("ac·shop", "storefront·2", ["b4", "b0"], False),            # parked last
                               ("ac·shop", "storefront-hosts", ["h"], True)])                   # already right
        of.HELD.add("mt")
        try:
            self.assertEqual(of.plan_layout(positions, agents, tabs, workspaces), [])
        finally:
            of.HELD.discard("mt")
        desk = [{"workspace_id": "wD", "label": of.DESK}]
        self.assertEqual(of.plan_layout(positions, agents, tabs, desk), [])  # the desk is never touched

    def test_balanced(self):
        area = {"x": 0, "y": 0, "width": 340, "height": 98}
        rects = of.expected_rects(4, area)
        lay = {"area": area, "panes": [{"pane_id": f"p{i}", "rect": {"x": x, "y": y, "width": w, "height": h}}
                                       for i, (x, y, w, h) in enumerate(rects)]}
        self.assertTrue(of.balanced(lay, ["p0", "p1", "p2", "p3"]))
        self.assertTrue(of.balanced(lay, ["p1", "p0", "p2", "p3"]))  # order does not matter: no churn
        self.assertFalse(of.balanced(lay, ["p0", "p1", "p2"]))
        lay["panes"][3]["rect"]["width"] = 40  # a halving sliver
        self.assertFalse(of.balanced(lay, ["p0", "p1", "p2", "p3"]))
        self.assertEqual(of.tab_label("storefront", 2), "storefront·3")

    def test_sticky_layout(self):
        of.ORG_SHORT["mt"] = "ac"
        ps = [pos(f"b{i}", system="storefront", session=f"s{i}", _org="mt", _workspace="acme", _unit="u")
              for i in range(5)]
        ags = [agent(f"wD:p{i}", f"s{i}", ws="wD", tab="wD:t2" if i == 0 else "wD:t1",
                     status="working" if i == 0 else "idle") for i in range(5)]
        tabs = [{"tab_id": "wD:t1", "workspace_id": "wD", "label": "storefront", "pane_count": 4},
                {"tab_id": "wD:t2", "workspace_id": "wD", "label": "storefront·2", "pane_count": 1}]
        plan = of.plan_layout(ps, ags, tabs, [{"workspace_id": "wD", "label": "ac·u"}])
        # b0 turned working but stays in storefront·2: states changing never reshuffles the tabs
        self.assertEqual([(lab, [n for _, n in ch]) for _, lab, ch, _ in plan],
                         [("storefront", ["b1", "b2", "b3", "b4"]), ("storefront·2", ["b0"])])


class ServerLineTest(unittest.TestCase):
    def test_health_lines(self):
        line = of.server_health_line
        self.assertIsNone(line({"queued": 0}))
        self.assertEqual(line({"queued": 177, "queue_bytes": 2.9e9, "drain_rate_bps": 4e5, "drain_eta_s": 6600}),
                         "of-server: draining 177 batch(es) (2.9 GB), ~1 h 50 min left")
        self.assertIn("dead batch", line({"dead": 2, "queued": 1}))
        self.assertIn("writer failing", line({"writer_error": "x", "queued": 3}))
        stuck = {"queued": 5, "drain_rate_bps": None, "drain_eta_s": None, "oldest_queued": "2026-11-06T07:00:00Z"}
        self.assertIn("not draining", line(stuck, now=of.calendar_ts("2026-11-06T07:20:00Z")))
        self.assertIsNone(line(stuck, now=of.calendar_ts("2026-11-06T07:05:00Z")))


class InitStructureTest(unittest.TestCase):
    """of init writes the owner's conductor first, then the onboarding unit and its run, from bench/onboarding."""

    def setUp(self):
        import importlib
        import sys as _sys
        _sys.path.insert(0, os.path.join(HERE, "..", "bin"))
        self.of_init = importlib.import_module("of_init")
        self.pkg = self.of_init.load_package()
        self.values = {"org": "acme", "Org": "Acme", "owner_id": "alex", "owner_name": "Alex"}

    def built(self, values=None):
        return self.of_init.build_structure(self.pkg, values or self.values)

    def test_conductor_first_and_the_registry_checks_pass(self):
        b = self.built()
        doc = {"organization": "acme"}
        self.of_init.merge_structure(doc, b)
        names = [p["name"] for p in doc["positions"]]
        self.assertEqual(names[0], "acme_conductor")
        self.assertEqual(set(names), {"acme_conductor", "acme_owner", "acme_onboarding", "acme_business-case",
                                      "acme_system-architect", "acme_org", "acme_flow"})
        conductor = doc["positions"][0]
        self.assertTrue(conductor["owner_chat"])
        self.assertEqual(conductor["package"], {"name": "onboarding", "version": 1, "role": "conductor"})
        self.assertEqual(conductor["owner"], "acme_owner")
        self.assertEqual(doc["positions"][1]["holder"], {"runtime": "human", "person": "alex"})
        self.assertFalse(any((p.get("holder") or {}).get("session") for p in doc["positions"]))  # no agent started
        problems = []
        for p in doc["positions"]:
            self.assertTrue(of.NAME_RE.match(p["name"]), p["name"])
            self.assertLessEqual(len(p["summary"]), of.SUMMARY_MAX, p["name"])
            problems += of.check_mandate_fields(p)
        problems += of.check_units(doc["units"], set(names), doc["systems"])
        problems += of.check_works(doc["works"], set(names), doc["systems"], doc["units"], {})
        problems += of.check_people(doc, doc["positions"])
        self.assertEqual(problems, [])
        self.assertEqual([s["id"] for s in doc["works"]["acme-onboarding"]["stages"]],
                         ["charter", "problem", "data", "baselines", "integrate", "decide", "increments", "close"])

    def test_every_role_and_step_names_its_source(self):
        for r in self.pkg["roles"]:
            self.assertTrue(r.get("sources"), r["id"])
            self.assertTrue(r.get("brief"), r["id"])
        for s in self.pkg["run"]["stages"]:
            self.assertTrue(s.get("sources") and s.get("done_when"), s["id"])

    def test_files_are_filled(self):
        b = self.built()
        for rel, text in b["files"].items():
            self.assertIsNone(self.of_init.TOKEN.search(text), rel)
        for rel in ("registry/briefs/acme_conductor.md", "onboarding/charter.md", "onboarding/role-design.md",
                    "onboarding/recheck.md", "onboarding/champion.md", "onboarding/README.md"):
            self.assertIn(rel, b["files"])
        self.assertIn("of spawn acme_conductor", b["files"]["onboarding/README.md"])

    def test_merge_keeps_what_exists(self):
        doc = {"organization": "acme", "positions": [{"name": "acme_org", "system": "x", "role": "mine"}],
               "units": {"core": {"owner": "acme_org", "systems": ["x"]}}}
        added = self.of_init.merge_structure(doc, self.built())
        self.assertNotIn("acme_org", added)
        self.assertEqual(next(p for p in doc["positions"] if p["name"] == "acme_org")["role"], "mine")
        self.assertIn("core", doc["units"])
        self.assertEqual(doc["positions"][0]["name"], "acme_conductor")
        self.assertEqual(self.of_init.merge_structure(doc, self.built()), [])  # a second run adds nothing

    def test_files_never_overwritten(self):
        with tempfile.TemporaryDirectory() as repo:
            self.of_init.write_files(repo, {"onboarding/charter.md": "template"}, out=lambda *_: None)
            path = os.path.join(repo, "onboarding", "charter.md")
            with open(path, "w") as fh:
                fh.write("filled in")
            self.of_init.write_files(repo, {"onboarding/charter.md": "template"}, out=lambda *_: None)
            with open(path) as fh:
                self.assertEqual(fh.read(), "filled in")

    def test_hand_start_without_herdr(self):
        doc = {"organization": "acme"}
        self.of_init.merge_structure(doc, self.built())
        lines = self.of_init.hand_start(doc, "acme_conductor", "/home/alex/of/acme")
        conductor = doc["positions"][0]
        sid = conductor["holder"]["session"]
        self.assertEqual(conductor["holder"]["workplace"], self.of_init.HAND_START)
        self.assertIn(f"--session {sid} --position acme_conductor", lines[0])
        self.assertTrue(lines[0].startswith("of event injected_prompt --injector of_init"))
        self.assertIn(f"claude --session-id {sid} --name acme_conductor", lines[1])
        self.assertIn("registry/briefs/acme_conductor.md", lines[1])
        again = self.of_init.hand_start(doc, "acme_conductor", "/home/alex/of/acme")
        self.assertEqual(doc["positions"][0]["holder"]["session"], sid)  # a re-run reuses the reserved id
        self.assertEqual(again, lines)
        conductor["holder"] = {"runtime": "claude-code", "session": "live-one", "workplace": "herdr pane w1:p1"}
        self.assertIsNone(self.of_init.hand_start(doc, "acme_conductor", "/home/alex/of/acme"))  # a real holder stays
        self.assertIsNone(self.of_init.hand_start(doc, "acme_nobody", "/home/alex/of/acme"))

    def test_the_agents_find_first_and_the_head_confirms(self):
        files = self.built()["files"]
        lead = " ".join(files["registry/briefs/acme_onboarding.md"].split())
        self.assertIn("Find out first, then ask to confirm", lead)
        self.assertIn("Never ask them to write a list", lead)
        self.assertLess(lead.index("spawn acme_org"), lead.index("spawn acme_system-architect"))  # keeper at step 1
        charter = files["onboarding/charter.md"]
        for term in ("Sponsor:", "champion: owns", "presenting problem"):
            self.assertNotIn(term, charter)  # the head reads the charter: no method terms
        keeper = next(r for r in self.pkg["roles"] if r["id"] == "structure-keeper")
        self.assertTrue(keeper["starts"].startswith("at step 1"))
        self.assertIn("Find it, then ask to confirm", self.of_init.AGENTS_MD)

    def test_the_head_chooses_the_format_and_new_work_starts_now(self):
        files = self.built()["files"]
        conductor = " ".join(files["registry/briefs/acme_conductor.md"].split())
        self.assertLess(conductor.index("Hands-off (offer it first)"), conductor.index("Controlled:"))
        self.assertIn("provisional position", conductor)
        keeper = " ".join(files["registry/briefs/acme_org.md"].split())
        self.assertIn("provisional: {asked:", keeper)
        self.assertIn("event: <the of.human_prompt event id", keeper)
        self.assertIn("no credentials, no tools beyond the defaults", keeper)
        self.assertIn("How the onboarding runs:", files["onboarding/charter.md"])
        self.assertIn("hands-off", files["onboarding/README.md"])

    def test_the_remote_path_today(self):
        files = self.built()["files"]
        conductor = " ".join(files["registry/briefs/acme_conductor.md"].split())
        for step in ("of asks --json", "of handover list", "of conductor panel --once", "waits for the machine",
                     "Never answer a card for them"):
            self.assertIn(step, conductor)
        for p in ("conductor", "onboarding", "business-case", "system-architect", "org", "flow"):
            self.assertIn("run `of inbox --mine`", " ".join(files[f"registry/briefs/acme_{p}.md"].split()), p)

    def test_the_remote_surface_starts_agents_in_tmux(self):
        i = self.of_init
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "owner.yaml")
            self.assertEqual(i.resolve_surface(None, path), ("herdr", False))  # the default
            self.assertEqual(i.resolve_surface("remote", path), ("remote", True))
            i.write_owner_setting("surface", "remote", path)
            self.assertEqual(i.resolve_surface(None, path), ("remote", False))
            with self.assertRaises(ValueError):
                i.resolve_surface("phone", path)
        doc = {"organization": "acme"}
        i.merge_structure(doc, self.built())
        c = i.hand_start(doc, "acme_conductor", "/home/alex/of/acme", detach=True, remote_control=True,
                         tmux="/opt/homebrew/bin/tmux")
        self.assertTrue(c[1].startswith("tmux new-session -d -s acme_conductor 'cd /home/alex/of/acme && claude"))
        self.assertIn("--remote-control acme_conductor", c[1])

    def test_a_long_organization_name_is_used_where_the_name_is_needed(self):
        i = self.of_init
        org = "acme-logistics-europe"  # longer than PREFIX_MAX: the prefix is cut, the name is not
        prefix = i.org_prefix(org)
        self.assertNotEqual(prefix, org)
        values = dict(self.values, org=prefix, org_name=org)
        cap = i.capacity_structure(values)
        self.assertEqual(cap["settings"]["position"], f"{org}:{prefix}_capacity")  # an address: the full name
        self.assertEqual([p["name"] for p in cap["positions"]], [f"{prefix}_capacity"])
        conductor = " ".join(i.build_structure(self.pkg, values)["files"][f"registry/briefs/{prefix}_conductor.md"]
                             .split())
        self.assertIn(f"of init {org} --capacity yes --yes", conductor)
        self.assertNotIn(f"of init {prefix} ", conductor)

    def test_capacity_keeper_on_the_heads_yes(self):
        i = self.of_init
        self.assertEqual(i.resolve_capacity("no", accounts=3), "no")
        with self.assertRaises(ValueError):
            i.resolve_capacity("maybe", accounts=3)
        self.assertEqual(i.resolve_capacity(None, accounts=1, ask=lambda q: "y"), "no")  # one account: not asked
        self.assertEqual(i.resolve_capacity(None, accounts=2, served="acme:acme_capacity", ask=lambda q: "y"), "no")
        asked = []
        import of_locale
        said = of_locale.variants("yes")[-1]  # "yes" in the person's language, from the catalogue
        self.assertEqual(i.resolve_capacity(None, accounts=2, ask=lambda q: asked.append(q) or said,
                                            position="acme_capacity"), "yes")
        self.assertEqual(i.resolve_capacity(None, accounts=2, ask=lambda q: "y"), "yes")
        self.assertIn("This machine has 2 Claude accounts", asked[0])
        self.assertIn("acme_capacity", asked[0])
        self.assertEqual(i.resolve_capacity(None, accounts=2, ask=lambda q: ""), "no")  # no by default
        self.assertIsNone(i.resolve_capacity(None, yes=True, accounts=2))  # nobody here: the card
        cap = i.capacity_structure(self.values)
        self.assertEqual([p["name"] for p in cap["positions"]], ["acme_capacity"])
        self.assertEqual(cap["positions"][0]["package"], {"name": "capacity", "version": 1, "role": "capacity"})
        self.assertEqual(cap["settings"]["position"], "acme:acme_capacity")
        self.assertTrue(cap["settings"]["auto"])
        self.assertNotIn("{", cap["files"]["registry/briefs/acme_capacity.md"].replace("{{", ""))
        b = self.built()
        b["positions"] += cap["positions"]
        doc = {"organization": "acme"}
        i.merge_structure(doc, b)
        names = {p["name"] for p in doc["positions"]}
        problems = []
        for p in doc["positions"]:
            problems += of.check_mandate_fields(p)
        problems += of.check_people(doc, doc["positions"])
        self.assertEqual(problems, [])
        self.assertIn(next(p for p in doc["positions"] if p["name"] == "acme_capacity")["owner"], names)
        calls = []
        run = lambda argv, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "asked a1b2c3d4\n", "")
        self.assertEqual(i.file_capacity_card(3, "acme_capacity", "en", of_bin="of", run=run),
                         (True, "asked a1b2c3d4"))
        self.assertEqual(calls[0][:2], ["of", "ask"])
        self.assertIn("This machine has 3 Claude accounts", calls[0][2])
        self.assertTrue(calls[0][4].startswith("yes, move them and add acme_capacity"))
        self.assertEqual(len(calls[0][4].split("|")), 2)  # both options reach the card
        i.file_capacity_card(3, "acme_capacity", "ru", of_bin="of", run=run)
        ru = of_locale.tr(i.CAPACITY_OPTIONS[1], "ru", position="acme_capacity")
        self.assertNotEqual(calls[1][2], calls[0][2])  # the head's language, from the catalogue
        self.assertEqual(calls[1][4].split("|")[1], ru)
        asked = []
        i.resolve_capacity(None, accounts=2, ask=lambda q: asked.append(q) or "", position="acme_capacity", lang="ru")
        self.assertTrue(asked[0].startswith(of_locale.tr(i.CAPACITY_QUESTION, "ru", n=2, position="acme_capacity")))
        self.assertNotIn("This machine has", asked[0])

    def test_herdr_plugin_linked_when_herdr_is_here(self):
        i, said = self.of_init, []
        self.assertFalse(i.link_herdr_plugin(True, said.append, install=lambda h: 1 / 0, which=lambda n: None))
        self.assertIn("not installed here", said[-1])
        homes = []
        cfg = lambda h: homes.append(h) or (True, ["prefix+a opens the questions screen", "sidebar: $asks"])
        self.assertTrue(i.link_herdr_plugin(True, said.append, which=lambda n: "/usr/bin/herdr", configure=cfg,
                                             install=lambda h: homes.append(h) or (True, "linked")))
        self.assertTrue(any("herdr plugin unlink organic-framework" in x for x in said))  # said before it is done
        self.assertEqual(homes, [os.path.dirname(i.HERE)] * 2)
        self.assertEqual(said[-1], "  herdr config: sidebar: $asks")
        self.assertFalse(i.link_herdr_plugin(True, said.append, which=lambda n: "/usr/bin/herdr",
                                              install=lambda h: (False, "herdr plugin link failed"),
                                              configure=lambda h: (False, "herdr config check failed")))
        self.assertIn("NOT linked: herdr plugin link failed", said[-2])
        self.assertIn("herdr config NOT changed: herdr config check failed", said[-1])

    def test_no_password_manager_is_assumed(self):
        files = self.built()["files"]
        for rel, text in files.items():
            self.assertNotIn("1Password", text.replace("Never assume 1Password", ""), rel)
        self.assertNotIn("1Password", self.of_init.AGENTS_MD)
        self.assertIn("where they keep secrets", " ".join(files["registry/briefs/acme_conductor.md"].split()))

    def test_plugins_proposed_and_chosen(self):
        i = self.of_init
        found = lambda: ["blobs: found s3; choose with `blobs: <name>`",
                         "secret_stores: found keychain; choose with `…`", "secrets: found keychain; choose with `…`"]
        self.assertEqual(i.plugin_proposals(found), ["blobs: found s3; choose with `blobs: <name>`"])  # secrets later
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "plugins.yaml")
            with open(path, "w") as fh:
                fh.write("storage: sqlite\n")
            doc = i.write_plugin_choices(["blobs=s3"], path, check=lambda f, n: None)
            self.assertEqual(doc, {"storage": "sqlite", "blobs": "s3"})
            with self.assertRaises(ValueError):
                i.write_plugin_choices(["blobs"], path, check=lambda f, n: None)

    def test_a_rerun_keeps_the_owner_and_refreshes_untouched_files(self):
        i = self.of_init
        doc = {"organization": "acme"}
        i.merge_structure(doc, self.built())
        self.assertEqual(i.existing_owner(doc, "acme"), ("alex", "Alex"))
        self.assertIsNone(i.existing_owner({"positions": []}, "acme"))
        quiet = lambda *a: None
        with tempfile.TemporaryDirectory() as repo:
            read = lambda rel: open(os.path.join(repo, rel)).read()
            v1 = {"AGENTS.md": "rules v1", "registry/briefs/acme_conductor.md": "brief v1",
                  "registry/briefs/acme_org.md": "keeper v1", "onboarding/charter.md": "template v1"}
            state = {}
            i.write_files(repo, v1, out=quiet, state=state)
            with open(os.path.join(repo, "registry/briefs/acme_org.md"), "w") as fh:
                fh.write("keeper v1, with our edits")  # edited before any commit
            with open(os.path.join(repo, "onboarding/charter.md"), "w") as fh:
                fh.write("their charter")
            v2 = {"AGENTS.md": "rules v2", "registry/briefs/acme_conductor.md": "brief v2",
                  "registry/briefs/acme_org.md": "keeper v2", "onboarding/charter.md": "template v2"}
            done, kept = i.refresh_files(repo, v2, out=quiet, stamp="T1", state=state)
            self.assertEqual(done, ["AGENTS.md", "registry/briefs/acme_conductor.md"])
            self.assertEqual(kept, ["registry/briefs/acme_org.md"])  # its hash is not the one init wrote
            self.assertEqual(read("registry/briefs/acme_org.md"), "keeper v1, with our edits")
            self.assertEqual(read("registry/briefs/acme_org.md.package-new"), "keeper v2")
            self.assertEqual(read("onboarding/.before-refresh-T1/registry/briefs/acme_conductor.md"), "brief v1")
            self.assertEqual(read("onboarding/charter.md"), "their charter")  # a working document: never touched
            v3 = dict(v2, **{"registry/briefs/acme_conductor.md": "brief v3"})
            done, _ = i.refresh_files(repo, v3, out=quiet, stamp="T2", state=state)
            self.assertEqual(done, ["registry/briefs/acme_conductor.md"])  # a second refresh still refreshes
            with open(os.path.join(repo, "registry/briefs/acme_conductor.md"), "w") as fh:
                fh.write("brief v0")  # no record of this one: unknown, kept...
            fresh = {}
            _, kept = i.refresh_files(repo, v3, out=quiet, stamp="T3", state=fresh)
            self.assertIn("registry/briefs/acme_conductor.md", kept)
            done, _ = i.refresh_files(repo, v3, out=quiet, stamp="T4", state={},  # ...unless an earlier package wrote it
                                      history=lambda rel, text: text == "brief v0")
            self.assertEqual(done, ["registry/briefs/acme_conductor.md"])
            i.ignore_refresh_copies(repo)
            i.ignore_refresh_copies(repo)
            self.assertEqual(read(".gitignore").count("onboarding/.before-refresh-*/"), 1)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError) as e:
                i.write_plugin_choices(["secrets=keychain"], os.path.join(d, "plugins.yaml"), check=lambda f, n: None)
            self.assertIn("organization's choice", str(e.exception))
            with self.assertRaises(ValueError):
                i.write_plugin_choices(["secret_stores=keychain"], os.path.join(d, "plugins.yaml"),
                                       check=lambda f, n: None)

    def test_relay_and_listener_run_as_services(self):
        i = self.of_init
        relay, listen = i.UNITS
        plist = i.unit_text(relay, "Darwin", py="/usr/bin/python3", of="/x/bin/of", log="/l/relay.log", path="/p:/usr/bin")
        self.assertIn("<string>dev.organic-framework.relay</string>", plist)  # the label of relay on|off uses
        self.assertIn("<string>relay</string><string>run</string>", plist)
        self.assertIn("<key>PATH</key><string>/p:/usr/bin</string>", plist)
        self.assertIn("<key>OF_AUTOMATION</key><string>1</string>", plist)
        unit = i.unit_text(listen, "Linux", py="/usr/bin/python3", of="/x/bin/of", log="-", path="/p")
        self.assertIn("ExecStart=/usr/bin/python3 /x/bin/of listen run", unit)
        self.assertIn("Restart=always", unit)
        calls, state = [], {"active": True}

        def run(cmd, **kw):
            calls.append(cmd)
            out = "active\n" if (cmd[:3] == ["systemctl", "--user", "is-active"] and state["active"]) else ""
            return subprocess.CompletedProcess(cmd, 0, out, "")
        lines = []
        self.assertEqual(i.install_units(out=lines.append, system="Linux", run=run, sleep=lambda s: None), [])
        self.assertIn(["systemctl", "--user", "enable", "--now", "of-relay.service"], calls)
        self.assertTrue(any("relay: running" in x for x in lines))
        state["active"] = False
        lines = []
        self.assertEqual(i.install_units(out=lines.append, system="Linux", run=run, wait_s=0.05,
                                         sleep=lambda s: None), ["relay", "listen"])
        self.assertTrue(any("relay: NOT running" in x for x in lines))
        state["active"], calls[:] = True, []
        i.install_units(out=lines.append, system="Linux", run=run, sleep=lambda s: None)
        self.assertFalse([c for c in calls if "enable" in c])  # an existing, running unit is left alone

    def test_a_source_a_person_hands_over_is_read(self):
        files = self.built()["files"]
        for brief in ("acme_onboarding", "acme_system-architect", "acme_business-case", "acme_conductor", "acme_owner"):
            text = " ".join(files[f"registry/briefs/{brief}.md"].split())
            self.assertRegex(text, r"(hands? (you|over)|hand an agent)", brief)
            self.assertIn("§8.4 (9)", text, brief)
        self.assertIn("| Source | Given by | Their words, verbatim | When (UTC) | Read by |", files["onboarding/charter.md"])

    def test_agents_start_on_opus_xhigh(self):
        i = self.of_init
        self.assertEqual(i.agent_settings(), {"model": "opus", "effort": "xhigh"})
        self.assertEqual(i.agent_settings(effort="max"), {"model": "opus", "effort": "max"})
        self.assertEqual(i.agent_settings(current={"model": "sonnet", "effort": "high"}), {"model": "sonnet", "effort": "high"})
        self.assertEqual(i.agent_settings(model="opus", current={"model": "sonnet", "effort": "high"}),
                         {"model": "opus", "effort": "high"})
        with self.assertRaises(ValueError):
            i.agent_settings(effort="xhight")
        doc = {"organization": "acme"}
        i.merge_structure(doc, self.built())
        lines = i.hand_start(doc, "acme_conductor", "/home/alex/of/acme", agents=i.agent_settings())
        self.assertIn("--name acme_conductor --dangerously-skip-permissions --model opus --effort xhigh 'You hold",
                      lines[1])
        doc = {"organization": "acme"}
        i.merge_structure(doc, self.built())
        lead = i.hand_start(doc, "acme_onboarding", "/home/alex/of/acme", agents=i.agent_settings(), detach=True,
                            tmux="")  # no tmux: screen
        self.assertTrue(lead[1].startswith("screen -U -dmS acme_onboarding sh -c 'cd /home/alex/of/acme && claude"))
        self.assertTrue(lead[0].startswith("of event injected_prompt"))  # an agent runs both; nothing asks the head
        readme = i.run_readme(self.pkg, i.fill(self.pkg["run"], self.values), dict(self.values, language="English"),
                              by_hand={"acme_onboarding": lead})
        self.assertIn("No agent here asks you to run a command", readme)
        files = self.built()["files"]
        for brief in ("registry/briefs/acme_conductor.md", "registry/briefs/acme_onboarding.md"):
            self.assertIn("do it yourself, and explain it simply", files[brief])
        self.assertIn("starts the onboarding lead itself", readme)

    def test_the_language_is_one_setting_asked_at_init(self):
        lang = self.of_init
        self.assertEqual([lang.language_code(x) for x in ("ru", "Russian", "русский", "EN", "Klingon")],
                         ["ru", "ru", "ru", "en", "Klingon"])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "owner.yaml")
            self.assertEqual(lang.resolve_language(None, yes=True, path=path), ("en", True))  # no terminal: English
            self.assertEqual(lang.resolve_language(None, path=path, ask=lambda q: "Russian"), ("ru", True))
            lang.write_language("ru", path)
            self.assertEqual(lang.resolve_language(None, path=path, ask=lambda q: self.fail("asked again")), ("ru", False))
            self.assertEqual(lang.resolve_language("de", yes=True, path=path), ("de", True))  # --language wins
            with open(path, "a") as fh:
                fh.write("time_zone: Asia/Dubai\n")
            lang.write_language("de", path)
            self.assertEqual(lang.owner_settings(path), {"language": "de", "time_zone": "Asia/Dubai"})
        b = self.built(dict(self.values, language="Russian"))
        conductor = b["files"]["registry/briefs/acme_conductor.md"]
        self.assertIn("Alex's language is Russian", conductor)
        self.assertIn("`title` (the title Alex sees, in Russian)", conductor)
        self.assertNotIn("_ru", conductor)
        self.assertIn("addressed in Russian", b["files"]["onboarding/charter.md"])
        self.assertIn("is English", self.built()["files"]["registry/briefs/acme_conductor.md"])  # the default

    def test_the_heads_holding_carries_its_evidence(self):
        ev = self.of_init.owner_evidence("acme", "Alex", "Alex", day="2026-11-11")
        self.assertEqual(ev, 'of init acme --owner "Alex", run on this machine on 2026-11-11')
        self.assertIn("without --owner", self.of_init.owner_evidence("acme", None, "alex", day="2026-11-11"))
        doc = {"organization": "acme"}
        self.of_init.merge_structure(doc, self.built(dict(self.values, owner_evidence=ev)))
        owner = next(p for p in doc["positions"] if p["name"] == "acme_owner")
        self.assertEqual(owner["holder"], {"runtime": "human", "person": "alex", "evidence": ev})
        self.assertEqual(of.check_evidence(doc, doc["positions"]), [])  # docs/registry.md, People
        data = next(s for s in doc["works"]["acme-onboarding"]["stages"] if s["id"] == "data")
        self.assertIn("people map", data["done_when"])

    def test_conductor_start_trusts_the_folder_once(self):
        start = self.of_init.conductor_start
        lines = start("acme", "/home/alex/of/acme", "/home/alex/.claude")
        words = " ".join(x for x in lines if x.startswith("#"))  # the person's step, in words, with its place
        self.assertIn("by the person on this machine", words)
        self.assertIn("never as a command to paste", words)
        self.assertIn("Yes, I trust this folder", words)
        self.assertIn("/exit", words)
        self.assertEqual(lines[-2], "cd /home/alex/of/acme && claude --dangerously-skip-permissions")
        self.assertTrue(lines[-1].startswith("of spawn acme_conductor --config-dir /home/alex/.claude"))
        other = start("acme", "/home/alex/of/acme", "/home/alex/.claude-b", (True, False))
        self.assertIn("CLAUDE_CONFIG_DIR=/home/alex/.claude-b claude --dangerously-skip-permissions", other[-2])
        self.assertEqual(len(start("acme", "/home/alex/of/acme", "/home/alex/.claude", (True, True))), 1)
        import json
        with tempfile.TemporaryDirectory() as acct:  # an empty account: neither trusted nor accepted
            self.assertEqual(self.of_init.start_ready(acct, os.path.join(acct, "of", "acme")), (False, False))
            with open(os.path.join(acct, "settings.json"), "w") as fh:
                json.dump({"skipDangerousModePermissionPrompt": True}, fh)
            self.assertEqual(self.of_init.start_ready(acct, os.path.join(acct, "of", "acme")), (False, True))

    def test_long_organization_names_fit(self):
        prefix = self.of_init.org_prefix("a-very-long-organization-name")
        b = self.built(dict(self.values, org=prefix))
        self.assertTrue(all(of.NAME_RE.match(p["name"]) for p in b["positions"]))
        self.assertEqual(self.of_init.display_name("acme"), "Acme")
        self.assertEqual(self.of_init.display_name("deXample"), "deXample")
        self.assertEqual(self.of_init.owner_identity("Alex Doe"), ("alex-doe", "Alex Doe"))


class InitNamesTest(unittest.TestCase):
    def test_propose_name(self):
        import importlib
        import sys as _sys
        _sys.path.insert(0, os.path.join(HERE, "..", "bin"))
        of_init = importlib.import_module("of_init")
        taken = set()
        a = {"cwd": "/home/alex/code/api", "name": "lima-of-lab-qs-code-a"}
        n1 = of_init.propose_name({"cwd": "/home/alex/code/api"}, taken)
        self.assertEqual(n1, ("api_dev", "api", "dev"))
        taken.add(n1[0])
        self.assertEqual(of_init.propose_name({"cwd": "/home/alex/code/api"}, taken)[0], "api_dev-2")
        self.assertEqual(of_init.propose_name({"cwd": "/x/billing", "session_name": "billing_operator"}, set())[0],
                         "billing_operator")
        self.assertEqual(of_init.propose_name({"cwd": "/x/web", "name": "claude"}, set())[0], "web_dev")
        self.assertTrue(of.NAME_RE.match(of_init.propose_name(a, set())[0]))

    def test_ask_without_tty_raises(self):
        import importlib
        of_init = importlib.import_module("of_init")
        import io
        import sys as _sys
        saved = _sys.stdin
        _sys.stdin = io.StringIO("y\n")
        try:
            with self.assertRaises(of_init.NoTTY):
                of_init._ask("Write?", False)
            self.assertTrue(of_init._ask("Write?", True))
        finally:
            _sys.stdin = saved


class KeeperPinTest(unittest.TestCase):
    def test_keeper_takes_the_first_slot(self):
        of.ORG_SHORT["mt"] = "ac"
        systems = {"storefront": {"keeper": "op"}}
        names = ["b1", "b2", "b3", "b4", "op"]
        ps = [pos(n, system="storefront", session=f"s{n}", _org="mt", _workspace="acme", _unit="u",
                  _systems=systems, status="parked" if n == "b2" else "active") for n in names]
        ags = [agent(f"wD:p{i}", f"s{n}", ws="wD", tab="wD:t2" if n == "op" else "wD:t1")
               for i, n in enumerate(names)]
        tabs = [{"tab_id": "wD:t1", "workspace_id": "wD", "label": "storefront", "pane_count": 4},
                {"tab_id": "wD:t2", "workspace_id": "wD", "label": "storefront·2", "pane_count": 1}]
        plan = of.plan_layout(ps, ags, tabs, [{"workspace_id": "wD", "label": "ac·u"}])
        got = [(lab, [n for _, n in ch]) for _, lab, ch, _ in plan]
        self.assertEqual(got[0][1][0], "op")                      # first slot of the first tab
        self.assertNotIn("b2", got[0][1])                          # the parked agent gave its place
        self.assertEqual(got[1], ("storefront·2", ["b2"]))


class DeskEventTypeTest(unittest.TestCase):
    def test_event_type_shapes(self):
        self.assertEqual(of_desk._event_type({"event": "pane.created", "pane_id": "x"}), "pane.created")
        self.assertEqual(of_desk._event_type({"event": {"type": "pane.closed"}}), "pane.closed")
        self.assertEqual(of_desk._event_type({"result": {"type": "pane.exited"}}), "pane.exited")
        self.assertEqual(of_desk._event_type({"id": "of-desk", "result": {"ok": True}}), "")
        self.assertEqual(of_desk._event_type("x"), "")


class ViewsTest(unittest.TestCase):
    def test_main_left_60_moves_and_match(self):
        import sys as _s
        _s.path.insert(0, os.path.join(HERE, "..", "bin"))
        import of_views
        v = {"name": "core", "workspace": "organic-framework", "tab": "core", "layout": "main-left-60",
             "positions": ["a", "b", "c", "d"]}
        cmds = of_views.plan_moves(v, ["pa", "pb", "pc", "pd"], workspace_id="w3")
        self.assertEqual(cmds[0][:4], ["pane", "move", "pa", "--new-tab"])
        ratios = [(c[2], c[c.index("--split") + 1], c[c.index("--target-pane") + 1], c[c.index("--ratio") + 1])
                  for c in cmds[1:]]
        self.assertEqual(ratios, [("pb", "right", "pa", "0.6"), ("pc", "down", "pb", "0.3333"),
                                  ("pd", "down", "pc", "0.5000")])
        area = {"x": 0, "y": 0, "width": 340, "height": 99}
        lay = {"area": area, "panes": [{"pane_id": p, "rect": {"x": x, "y": y, "width": w, "height": h}}
                                       for p, (x, y, w, h) in of_views.expected(v, area, ["pa", "pb", "pc", "pd"])]}
        self.assertTrue(of_views.matches(v, lay, ["pa", "pb", "pc", "pd"]))
        lay["panes"][0]["rect"]["width"] = 136  # 40/60 the wrong way round
        self.assertFalse(of_views.matches(v, lay, ["pa", "pb", "pc", "pd"]))
        self.assertEqual(of_views.check(dict(v, layout="x"), {"a", "b", "c"}),
                         ["layout 'x' is not one of main-left-60, grid", "position 'd' is not in any registry"])


class AskMarkLabelsTest(unittest.TestCase):
    def test_apply_reads_through_the_markers(self):
        self.assertEqual(of.base_label("■ k8s_keeper · ❓ asks you ■"), "■ k8s_keeper ■")
        self.assertEqual(of.base_label("storefront ❓2"), "storefront")
        self.assertEqual(of.strip_label("■ k8s_keeper · ❓ asks you ■"), "k8s_keeper")


class DuplicateKeyTest(unittest.TestCase):
    def test_duplicate_keys_found(self):
        text = "positions:\n  - name: a\n    unit: x\n    role: r\n    unit: x\n  - name: b\n    unit: y\n"
        self.assertEqual(of.duplicate_keys(text), [(5, "unit")])
        self.assertEqual(of.duplicate_keys("a: 1\nb: 2\n"), [])

    def test_a_value_cut_by_a_comment_is_found(self):
        # acme_org, 2026-11-08: ' #2498 leftovers' on a continuation line dropped the rest; the file parsed
        text = ("positions:\n  - name: a\n    note: the long note about\n      the cleanup #2498 leftovers\n"
                "    contribution: Refunds work, RFC #2338.\n    summary: #12 first\n")
        self.assertEqual(of.comment_cuts(text), [
            (4, "note", "the long note about the cleanup", "#2498 leftovers", True),
            (5, "contribution", "Refunds work, RFC", "#2338.", True),
            (6, "summary", "", "#12 first", True)])
        self.assertEqual([x[1:] for x in of.comment_cuts("l:\n  - one #12 two\n")], [("l", "one", "#12 two", True)])

    def test_comments_written_as_comments_and_quoted_hashes_pass(self):
        text = ("a: text  # a comment\nb: 'RFC #2338'\nc: \"PR #1 and #2\"\nd: C#sharp\ne: >\n  folded #3 text\n"
                "# a whole-line comment #4\nf:  #5 an empty value, commented\n")
        self.assertEqual(of.comment_cuts(text), [])
        self.assertEqual(of.comment_cuts("a: [unclosed\n"), [])  # a broken file is reported by the parse error

    def test_one_space_and_a_space_after_is_a_warning(self):
        self.assertEqual(of.comment_cuts("a: text # a comment or text?\n"), [(1, "a", "text", "# a comment or text?", False)])


class PresenceMergeTest(unittest.TestCase):
    def test_remote_holder_shows(self):
        import sys as _s
        _s.path.insert(0, os.path.join(HERE, "..", "bin"))
        import of_client
        import of_ship
        saved = (of_client.Client.presence, of_ship.config, of_ship.org_servers)
        of_ship.config = lambda: {"server_url": "http://x"}
        of_ship.org_servers = lambda cfg: {}
        of_client.Client.presence = lambda self, org: {"holders": [{"position": "lab_colleague", "machine_id": "m-other",
                                                                    "state": "human", "kind": "human"}]}
        try:
            rows = [{"position": "lab_colleague", "state": "offline", "pane": ""},
                    {"position": "of_a", "state": "idle", "pane": "w1:p1"}]
            of._merge_presence(rows, [{"name": "lab_colleague", "_org": "lab"}, {"name": "of_a", "_org": "lab"}])
            self.assertEqual((rows[0]["state"], rows[0]["pane"]), ("remote:human", "machine m-other"))
            self.assertEqual(rows[1]["state"], "idle")
        finally:
            of_client.Client.presence, of_ship.config, of_ship.org_servers = saved


class WorksTest(unittest.TestCase):
    """The works: schema (docs/works.md step 1), with acme_org's draft's fields as the fixture."""

    def test_valid_draft_shapes(self):
        works = {"mt-nonces-wallets": {
            "title": "Nonces and wallets for Shop sends", "kind": "rfc",
            "doc": "storefront:docs/rfc/shop-nonces-and-wallets.md (PR #2683)", "systems": ["storefront"],
            "owner": "acme_owner", "doers": ["storefront_checkout", "external:dana"], "draft_state": "live",
            "done_when": "part 1 merged and flipped", "review": "2026-11-13",
            "match": {"prs": [2218, 2683], "branches": ["claude/wallet-*"], "paths": ["storefront:docs/rfc/*.md"]},
            "stages": [{"id": "w2", "title": "allocator live", "done_when": "watch clean", "state": "done"},
                       {"id": "part-1", "title": "claim by block order", "done_when": "#2683 merged", "state": "open"}]},
            "mt-old": {"title": "parked one", "kind": "project", "state": "parked"}}
        self.assertEqual(of.check_works(works, {"acme_owner", "storefront_checkout"}, {"storefront": {}}), [])

    def test_match_globs_take_a_repo_prefix(self):
        w = {"wx": {"title": "t", "kind": "rfc", "match": {"branches": ["devops-k8s:claude/*core-v2*", "claude/a-*"],
                                                           "paths": ["storefront:contracts/**"]}}}
        self.assertEqual(of.check_works(w, set(), {}), [])
        w["wx"]["match"]["branches"] = [":claude/*", "devops-k8s:", 7]
        self.assertEqual(len([x for x in of.check_works(w, set(), {}) if "not a glob" in x]), 3)

    def test_multi_unit_owner_and_lost_doers(self):
        units = {"platform": {"owner": "platform_owner", "systems": ["k8s"]}, "bots": {"owner": "acme_owner", "systems": ["storefront"]}}
        w = {"x-1": {"title": "t", "kind": "project", "systems": ["k8s", "storefront"], "doers": ["k8s_keeper"]}}
        out = of.check_works(w, {"k8s_keeper"}, {"k8s": {}, "storefront": {}}, units, {"k8s_keeper": "parked"})
        self.assertTrue(any("span units" in x and "owner is required" in x for x in out))
        self.assertTrue(any(x.startswith(of.WARN) and "lost its doer" in x for x in out))
        for st in ("done", "parked"):  # the keeper set that state on purpose: no doer needed (acme_org, 2026-11-06)
            out = of.check_works({"x-1": dict(w["x-1"], state=st)}, {"k8s_keeper"}, {"k8s": {}, "storefront": {}}, units,
                                 {"k8s_keeper": "parked"})
            self.assertFalse(any("lost its doer" in x for x in out), st)
        w["x-1"]["owner"] = "acme_owner"
        out = of.check_works(w, {"k8s_keeper", "acme_owner"}, {"k8s": {}, "storefront": {}}, units, {"k8s_keeper": "active"})
        self.assertEqual(out, [])

    def test_problems(self):
        works = {"Bad Id": {"title": "", "kind": "epic", "systems": ["nope"], "owner": "ghost",
                            "doers": ["dana"], "state": "active", "due": "next week",
                            "stages": [{"id": "s1", "done_when": "x"}, {"id": "s1", "done_when": "y", "state": "half"},
                                       {"title": "no id"}],
                            "match": {"prs": ["#12"]}}}
        text = "\n".join(of.check_works(works, {"acme_owner"}, {"storefront": {}}))
        for want in ("the id must match", "title is required", "kind 'epic'", "system 'nope'", "owner 'ghost'",
                     "doer 'dana'", "only done, parked", "due 'next week'", "appears twice", "is not done or open",
                     "needs an id", "match.prs"):
            self.assertIn(want, text)


class PickGrantTest(unittest.TestCase):
    def test_pick(self):
        g = {"lab": [{"id": "g1", "subject": "of_platform", "scope": {"to_org": ["acme"]}, "effect": "allow"},
                     {"id": "g2", "subject": "of_platform", "scope": {"to_org": ["other"]}},
                     {"id": "g3", "subject": "of_lab", "scope": {"to_org": ["acme"]}},
                     {"id": "g4", "subject": "of_platform", "scope": {"to_org": ["acme"]}, "expires": "2000-01-01T00:00:00Z"}]}
        self.assertEqual(of.pick_grants(g, "of_platform", "acme"), ["g1"])
        self.assertEqual(of.pick_grants(g, "of_studio", "acme"), [])

    def test_to_positions(self):
        """The founder's 22ed05e1: Acme positions may write to organic-framework-build:of_platform only."""
        g = {"ofb": [{"id": "g-2026-11-06-002", "subject": "k8s_keeper", "effect": "allow", "expires": "2099-01-01T00:00:00Z",
                      "scope": {"to_org": ["organic-framework-build"],
                                "to_positions": ["organic-framework-build:of_platform"]}}]}
        self.assertEqual(of.pick_grants(g, "k8s_keeper", "organic-framework-build",
                                        to_position="organic-framework-build:of_platform"), ["g-2026-11-06-002"])
        self.assertEqual(of.pick_grants(g, "k8s_keeper", "organic-framework-build",
                                        to_position="organic-framework-build:of_studio"), [])
        self.assertEqual(of.pick_grants(g, "k8s_keeper", "organic-framework-build"), [])  # no target: not picked


class ConfirmTest(unittest.TestCase):
    def test_systems_confirm(self):
        ok = {"a": {"confirm": True}, "b": {"confirm": ["keeper", "live"]}}
        self.assertEqual(of.check_systems(ok, set()), [])
        bad = "\n".join(of.check_systems({"c": {"confirm": "yes"}, "d": {"confirm": ["owner"]}, "e": {"confirm": []}}, set()))
        self.assertEqual(bad.count("confirm must be"), 3)


class IngestReadsTest(unittest.TestCase):
    """2026-11-06: the read events found while ingesting were written through a second connection
    inside the ingest's write transaction; each waited out the busy timeout on its own process's
    lock and every other writer stalled behind it."""

    def test_read_events_are_written_on_the_ingest_connection_without_waiting(self):
        import sys
        import time
        sys.path.insert(0, os.path.join(HERE, "..", "bin"))
        import json
        import of_events
        import of_hook
        import of_reads
        import of_store
        with tempfile.TemporaryDirectory() as root:
            saved = (of_events.STATE_DIR, of_hook._registry_index, of_reads.detect, of_store.connect)
            of_events.STATE_DIR = root
            of_hook._registry_index = lambda: {"labels": {}, "sessions": {"S": ["reader"]}, "systems": []}
            of_reads.detect = lambda obj, reader, system, systems: [({"path": "/x", "owner": "other"}, obj["uuid"])]
            real_connect = of_store.connect
            of_store.connect = lambda path=None, timeout=10.0: real_connect(path, timeout=0.5)
            try:
                conn = of_store.connect()
                tr = os.path.join(root, "S.jsonl")
                with open(tr, "w") as fh:
                    for i in range(3):
                        fh.write(json.dumps({"uuid": f"u{i}", "sessionId": "S", "type": "assistant",
                                             "timestamp": "2026-11-06T12:00:00Z"}) + "\n")
                t0 = time.time()
                self.assertEqual(of._ingest_with_reads(conn, tr), 3)
                self.assertLess(time.time() - t0, 0.5)  # no wait on a lock its own process holds
                n = conn.execute("SELECT count(*) FROM event WHERE name = 'of.artifact_read'").fetchone()[0]
                self.assertEqual(n, 3)
                conn.close()
            finally:
                of_events.STATE_DIR, of_hook._registry_index, of_reads.detect, of_store.connect = saved


class SendAddressTest(unittest.TestCase):
    """A target that fails the name rule or is in no registry is refused with the nearest names, never queued."""
    POS = [{"name": "of_platform", "_org": "ofb", "aliases": ["platform"]}, {"name": "of_architect", "_org": "ofb"},
           {"name": "acme_org", "_org": "acme"}]

    def test_addresses(self):
        r = lambda a: of._resolve_address(a, self.POS)
        self.assertEqual(r("of_platform"), "ofb:of_platform")
        self.assertEqual(r("ofb:platform"), "ofb:of_platform")          # an alias within the organization
        self.assertEqual(r("other-org:someone"), "other-org:someone")   # an organization not listed here
        with self.assertRaises(of.OfError) as e:
            r("ofb:of_platform --reply-to 01a1b256")                  # one argument by a quoting slip
        self.assertIn("nearest: ofb:of_platform", str(e.exception))
        with self.assertRaises(of.OfError) as e:
            r("ofb:of_platfrom")
        self.assertIn("nearest: of_platform", str(e.exception))
        with self.assertRaises(of.OfError):
            r("acme:of_platform")                                 # a position of another organization


class InboxMineFromTest(unittest.TestCase):
    """of_lab, 2026-11-07: in a shared lab registry --mine could not tell the position, and refused --from."""
    POS = [{"name": "lab_owner", "_org": "lab", "holder": {"runtime": "claude-code", "session": "S-OWN"}},
           {"name": "lab_colleague", "_org": "lab", "holder": {"runtime": "human", "person": "colleague"}},
           {"name": "lab_agent", "_org": "lab", "holder": {"runtime": "claude-code", "session": "S-AG"}}]

    def run_mine(self, sender, env_sid):
        import types
        saved = (of._load_quiet, os.environ.get("CLAUDE_CODE_SESSION_ID"))
        of._load_quiet = lambda: self.POS
        if env_sid:
            os.environ["CLAUDE_CODE_SESSION_ID"] = env_sid
        else:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        try:
            return of._inbox_mine(types.SimpleNamespace(sender=sender, take=None, mine=True))
        finally:
            of._load_quiet = saved[0]
            if saved[1] is not None:
                os.environ["CLAUDE_CODE_SESSION_ID"] = saved[1]
            else:
                os.environ.pop("CLAUDE_CODE_SESSION_ID", None)

    def test_from_only_names_the_position_the_session_must_hold(self):
        with self.assertRaises(of.OfError) as e:
            self.run_mine("lab_agent", "S-OWN")          # a peer's queue: refused
        self.assertIn("does not hold lab_agent", str(e.exception))
        with self.assertRaises(of.OfError) as e:
            self.run_mine("lab_colleague", None)         # a person's messages live in `of inbox`
        self.assertIn("held by a person", str(e.exception))


class SystemLifecycleTest(unittest.TestCase):
    def test_lifecycle_enum(self):
        """docs/registry.md (5523cb6): live (default) | unsupported | retired."""
        for v in ("live", "unsupported", "retired", None):
            sysd = {"name": "x", "kind": "target"} if v is None else {"name": "x", "kind": "target", "lifecycle": v}
            self.assertFalse([p for p in of.check_systems({"bot": sysd}, set()) if "lifecycle" in p], v)
        out = of.check_systems({"bot": {"name": "x", "kind": "target", "lifecycle": "dead"}}, set())
        self.assertTrue(any("lifecycle 'dead' is not one of live, unsupported, retired" in p for p in out))


class LiveOnlyOwnerTest(unittest.TestCase):
    def test_a_retired_system_in_another_unit_asks_for_no_owner(self):
        units = {"platform": {"owner": "po", "systems": ["k8s"]}, "bots": {"owner": "mo", "systems": ["old-bot"]}}
        systems = {"k8s": {}, "old-bot": {"lifecycle": "retired"}}
        w = {"x-1": {"title": "t", "kind": "project", "systems": ["k8s", "old-bot"]}}
        self.assertFalse(any("owner is required" in p for p in of.check_works(w, set(), systems, units, {})))
        systems["old-bot"] = {}
        self.assertTrue(any("owner is required" in p for p in of.check_works(w, set(), systems, units, {})))
