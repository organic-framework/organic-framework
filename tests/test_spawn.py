"""of spawn: the brief is declared by construction; refusals before anything starts."""

import json
import os
import shlex
import tempfile
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_spawn  # noqa: E402

SYSTEMS = {"net": {"repos": ["ansible-creek"]}, "acme": {"repos": ["org"]}, "sec": {}}
P = {"name": "net_keeper", "system": "net", "_org": "acme", "_unit": "platform", "mandate": "registry/briefs/net_keeper.md",
     "holder": {"runtime": "claude-code"}}


def bind(positions, agents):
    by = {a["name"]: a for a in agents}
    return [(p, by[p["name"]]) for p in positions if p["name"] in by], [], []


def plan(p=P, agents=(), **kw):
    kw.setdefault("config_dir", "~/.claude-e")
    return of_spawn.plan(p, [p], list(agents), bind, SYSTEMS, ["~/acme"], "/h/acme/org",
                         lambda q: "ac·" + q["_unit"], trusted=lambda d, c: "k", sid="S-1", **kw)


class SpawnPlanTest(unittest.TestCase):
    def test_outside_a_unit_a_new_pane_gets_a_tab_of_its_role(self):
        """The founder, prompt 01a20537-b8c3: one system's agents all landed in one tab (hand-over 81931dd4)."""
        p = {"name": "of_docs", "system": "organic-framework", "role": "docs"}
        self.assertEqual(of_spawn.place(p, lambda q: "of"), ("of", "docs"))
        unit = dict(p, _unit="u", _systems={"organic-framework": {"label": "of"}})
        self.assertEqual(of_spawn.place(unit, lambda q: "of·u"), ("of·u", "of"))

    def test_folder_brief_place_and_command(self):
        exists = lambda path: path.endswith(("acme/ansible-creek", "acme"))
        self.assertTrue(of_spawn.work_dir(P, SYSTEMS, ["~/acme"], exists).endswith("acme/ansible-creek"))
        self.assertTrue(of_spawn.work_dir(dict(P, system="sec"), SYSTEMS, ["~/acme"], exists).endswith("/acme"))
        pl = plan(cwd="/tmp")
        self.assertEqual((pl["workspace"], pl["tab"], pl["session"]), ("ac·platform", "net", "S-1"))
        self.assertTrue(pl["brief"].startswith("You hold the position net_keeper. Read /h/acme/org/registry/briefs/"))
        self.assertNotIn("subagents", pl["brief"])   # no owner's yes in the registry: nothing said (PR 241 review)
        p = {"name": "net_keeper", "mandate": "registry/briefs/net_keeper.md"}
        self.assertIn(" Your owner asks you to run the independent parts of your work as subagents, in parallel "
                      "(docs/method.md, Subagents; your organization's registry, subagents:). At the end of every turn:",
                      of_spawn.brief_text(p, "/h/acme/org", subagents=True))   # hand-over ff0f6f09
        self.assertIn("--session-id S-1 -n net_keeper --settings ", pl["command"])  # every session: AGENT_SETTINGS
        self.assertTrue(pl["command"].endswith(".settings.json --dangerously-skip-permissions"), pl["command"])
        self.assertIn("CLAUDE_CONFIG_DIR=", pl["command"])
        self.assertIn("holder: {runtime: claude-code, session: S-1", of_spawn.registry_line(pl, "wF:p1"))

    def test_refusals(self):
        with self.assertRaises(of_spawn.SpawnError):   # already held live
            plan(agents=[{"name": "net_keeper", "pane_id": "wF:p1"}], cwd="/tmp")
        with self.assertRaises(of_spawn.SpawnError):   # held by a person
            plan(dict(P, holder={"runtime": "human", "person": "founder"}), cwd="/tmp")
        with self.assertRaises(of_spawn.SpawnError):   # parked
            plan(dict(P, status="parked"), cwd="/tmp")
        with self.assertRaises(of_spawn.SpawnError):   # no account with headroom
            plan(config_dir=None, cwd="/tmp", capacity_rows=[], best_target=lambda rows: None)

    def test_an_untrusted_folder_is_a_task_for_the_founder(self):
        pl = of_spawn.plan(P, [P], [], bind, SYSTEMS, ["~/acme"], "/h", lambda q: "acme", config_dir="/x/.claude-b",
                           cwd="/tmp/w", trusted=lambda d, c: None, sid="S")
        self.assertIsNone(pl["trusted"])
        title, steps = of_spawn.trust_task(pl)
        self.assertIn("/tmp/w", title)
        self.assertIn("CLAUDE_CONFIG_DIR=/x/.claude-b claude", steps)

    def test_the_default_accounts_trust_is_carried_else_the_task_stands(self):
        """of_review on PR 568: a start on an account that does not trust the folder carries the default account's
        trust, as a move does; with no account trusting it the owner's task is filed as before."""
        calls = []

        def carried(src, dst, cwd, dry_run=False):
            calls.append((src, dst, cwd, dry_run))
            return "carried", cwd

        def nobody(src, dst, cwd, dry_run=False):
            raise RuntimeError("folder is trusted in no config")
        pl = {"trusted": None, "config_dir": "/x/.claude-b", "cwd": "/tmp/w"}
        self.assertEqual(of_spawn.carry_trust(dict(pl), carried, dry_run=True, default_dir="/x/.claude"),
                         "would carry (/tmp/w)")
        got = dict(pl)
        self.assertEqual(of_spawn.carry_trust(got, carried, default_dir="/x/.claude"), "carried (/tmp/w)")
        self.assertEqual(got["trusted"], "/tmp/w")
        self.assertEqual(calls[-1], ("/x/.claude", "/x/.claude-b", "/tmp/w", False))
        left = dict(pl)
        self.assertIsNone(of_spawn.carry_trust(left, nobody))
        self.assertIsNone(left["trusted"])                              # the owner's task follows
        title, _ = of_spawn.trust_task(dict(left, position="of_x", session="S", workspace="w", tab="t"))
        self.assertIn("/tmp/w", title)
        self.assertIsNone(of_spawn.carry_trust(dict(pl, trusted="/tmp"), carried))   # trusted already: untouched

    def test_the_account_that_trusts_the_folder_is_chosen_first(self):
        rows = [{"config_dir": "/u/.claude-d", "free": 90}, {"config_dir": "/u/.claude-e", "free": 60},
                {"config_dir": "/u/.claude", "free": 70}]
        best = lambda rs: max(rs, key=lambda r: r["free"]) if rs else None
        pick = lambda trusted: of_spawn.plan(P, [P], [], bind, SYSTEMS, ["~/acme"], "/h", lambda q: "acme",
                                             cwd="/w/ansible-creek", capacity_rows=rows, best_target=best,
                                             trusted=trusted, sid="S")["config_dir"]
        self.assertEqual(pick(lambda d, c: "k" if not d.endswith("-d") else None), "/u/.claude")
        self.assertEqual(pick(lambda d, c: None), "/u/.claude-d")  # none trusts it: the best, and a task

    def test_a_trusted_parent_folder_counts(self):
        """acme_org, 2026-11-07: ~/acme was trusted in the account, ~/acme/org was not."""
        home = os.path.expanduser("~")
        trusted = {f"{home}/acme"}
        key = lambda d, c, cands=None: next((k for k in (cands or [c]) if k in trusted), None)
        no_git = lambda c: (None, False)
        self.assertEqual(of_spawn.trusted_with_parents("/acct", f"{home}/acme/notes", key, no_git),
                         f"{home}/acme")  # outside git a trusted parent holds
        self.assertIsNone(of_spawn.trusted_with_parents("/acct", f"{home}/elsewhere/x", key, no_git))
        self.assertIsNone(of_spawn.trusted_with_parents("/acct", "/opt/x", lambda d, c, cands=None: "/" if cands == ["/"] else None,
                                                        no_git))

    def test_a_repository_under_a_trusted_folder_is_not_trusted(self):
        """acme_org, 2026-11-09: ~/acme trusted, ~/acme/ansible-creek (its own repository) not; Claude Code
        2.1.319 showed the trust dialog to net_outage-fix, which of spawn had launched."""
        home = os.path.expanduser("~")
        repo = f"{home}/acme/ansible-creek"
        trusted = {f"{home}/acme"}
        key = lambda d, c, cands=None: next((k for k in (cands or [c]) if k in trusted), None)
        self.assertIsNone(of_spawn.trusted_with_parents("/acct", repo, key, lambda c: (repo, False)))
        self.assertIsNone(of_spawn.trusted_with_parents("/acct", f"{repo}/roles", key, lambda c: (repo, False)))
        trusted.add(repo)  # inside a trusted repository a subfolder is trusted
        self.assertEqual(of_spawn.trusted_with_parents("/acct", f"{repo}/roles", key, lambda c: (repo, False)), repo)
        trusted.discard(repo)
        trusted.add(f"{repo}/roles")  # a linked worktree: only its root counts
        self.assertIsNone(of_spawn.trusted_with_parents("/acct", f"{repo}/roles/x", key, lambda c: (repo, True)))

    def test_git_root_finds_the_repository_and_a_linked_worktree(self):
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "repo", ".git"))
            os.makedirs(os.path.join(t, "repo", "a", "b"))
            os.makedirs(os.path.join(t, "wt", "c"))
            with open(os.path.join(t, "wt", ".git"), "w") as fh:
                fh.write("gitdir: ../repo/.git/worktrees/wt\n")
            self.assertEqual(of_spawn.git_root(os.path.join(t, "repo", "a", "b")), (os.path.join(t, "repo"), False))
            self.assertEqual(of_spawn.git_root(os.path.join(t, "wt", "c")), (os.path.join(t, "wt"), True))

    def test_a_trust_dialog_after_launch_is_named_without_a_second_task(self):
        trust = ("\n Do you trust the files in this folder?\n\n /Users/alex/acme/ansible-creek\n\n"
                 " ❯ 1. Yes, proceed\n   2. No, exit\n")
        herdr = lambda *a: {"read": {"text": trust}} if a[:2] == ("pane", "read") else {}
        self.assertTrue(of_spawn.trust_dialog(herdr, "wF:p1Z"))
        self.assertFalse(of_spawn.trust_dialog(lambda *a: {"read": {"text": "│ > \n ? for shortcuts"}}, "wF:p1Z"))

    def test_run_types_the_brief_declared_into_the_new_pane(self):
        calls, typed = [], []

        def herdr(*a):
            calls.append(a)
            if a[:2] == ("workspace", "list"):
                return {"workspaces": [{"workspace_id": "wF", "label": "ac·platform"}]}
            if a[:2] == ("tab", "list"):
                return {"tabs": [{"tab_id": "t1", "workspace_id": "wF", "label": "net"}]}
            if a[:2] == ("pane", "list"):
                return {"panes": [{"pane_id": "wF:p1", "tab_id": "t1"}]}
            if a[:2] == ("pane", "split"):
                return {"pane": {"pane_id": "wF:p2"}}
            return {}
        pl = plan(cwd="/tmp")
        pane = of_spawn.run(pl, herdr, deliver=lambda pane, text, sid: (typed.append((pane, text, sid)), (True, None))[1],
                            rename=lambda pane, name, sid: "named", wait_session=lambda sid, t: {"pane_id": "wF:p2"},
                            log=lambda m: None)
        self.assertEqual(pane, "wF:p2")
        self.assertIn(("pane", "run", "wF:p2", pl["command"]), calls)
        self.assertEqual(typed, [("wF:p2", pl["brief"], "S-1")])   # into the new pane, for the new session
        self.assertFalse(any(a[:2] == ("pane", "run") and a[2] == "wF:p1" for a in calls))  # never the caller's pane
        # a user organization's keeper, message 01a1e0f1-c913: herdr did not see the agent for a moment after the session came up
        typed.clear()
        answers = [(False, "herdr agent wait failed (rc 1): agent_not_running: agent is no longer running")] * 2 + \
            [(True, None)]
        with mock.patch.object(of_spawn.time, "sleep", lambda s: None):
            of_spawn.run(pl, herdr, deliver=lambda pane, text, sid: (typed.append(pane), answers.pop(0))[1],
                         rename=lambda pane, name, sid: "named", wait_session=lambda sid, t: {"pane_id": "wF:p2"},
                         log=lambda m: None)
        self.assertEqual(typed, ["wF:p2"] * 3)
        with self.assertRaises(of_spawn.SpawnError):  # any other failure stops at once, as before
            of_spawn.run(pl, herdr, deliver=lambda pane, text, sid: (typed.append(pane), (False, "a dialog"))[1],
                         rename=lambda pane, name, sid: "named", wait_session=lambda sid, t: {"pane_id": "wF:p2"},
                         log=lambda m: None)
        self.assertEqual(len(typed), 4)


    def test_a_brief_left_in_the_input_box_gets_one_enter(self):
        """A user organization's structure keeper, message 01a1e8ce-44ee: a spawn's brief sat typed and unsent for 17
        minutes at a busy moment, until Enter was pressed by hand."""
        brief = "Message from of_spawn (via of spawn, authority: none) You hold the position x."
        clock, pressed = [0.0], []

        def sleep(s):
            clock[0] += s
        held = lambda boxes: of_spawn.submit_held("wF:p2", lambda p: boxes.pop(0) if len(boxes) > 1 else boxes[0],
                                                  lambda p: pressed.append(p) or True, wait_s=3, sleep=sleep,
                                                  clock=lambda: clock[0])
        self.assertEqual(held([brief, ""]), "sent")                       # it left the box: nothing pressed
        self.assertEqual(pressed, [])
        self.assertEqual(held([brief]), "pressed")                         # still there after the wait: Enter once
        self.assertEqual(pressed, ["wF:p2"])
        self.assertEqual(held(["[Pasted text #1 +3 lines]"]), "pressed")  # Claude Code's paste placeholder
        self.assertEqual(held(["half a sentence someone typed"]), "unknown")  # not the brief: never pressed
        self.assertEqual(held([None]), "unknown")                          # the pane cannot be read
        self.assertEqual(len(pressed), 2)


CAT = {  # of_credentials.load()'s shape: two organizations, absolute paths
    "acme": {"roots": ["/h/.config/acme"],
           "credentials": {"graf": {"paths": ["/h/.config/acme/grafana/viewer.token"], "keeper": "obs_keeper"},
                           "deploy": {"paths": ["/h/.ssh/acme-deploy"], "keeper": "net_keeper"},
                           "pg": {"paths": ["/h/.config/acme/pg/*.pgpass"], "keeper": "db_keeper"}},
           "positions": {"app_dev": ["pg"]}},
    "globex": {"roots": ["/h/.config/globex"],
               "credentials": {"x": {"paths": ["/h/.config/globex/x.cfg"], "keeper": "k"}}, "positions": {}}}


# CI runs on Linux, where the settings carry the sandbox block (SandboxBlock tests it)
@__import__("unittest.mock", fromlist=["patch"]).patch.object(__import__("of_spawn"), "PLATFORM", "darwin")
class CredentialDenyTest(unittest.TestCase):
    def test_rules_deny_what_the_position_may_not_use(self):
        self.assertEqual(of_spawn.deny_rules("app_dev", "acme", CAT, named=False, roots=False), [])  # both layers off: nothing
        self.assertFalse(any(x.endswith("/**)") for x in of_spawn.deny_rules("app_dev", "acme", CAT, named=True, roots=False)))
        self.assertEqual([x for x in of_spawn.deny_rules("app_dev", "acme", CAT, named=False, roots=True) if "acme-deploy" in x], [])
        r = of_spawn.deny_rules("app_dev", "acme", CAT, named=True, roots=True)
        self.assertIn("Read(//h/.config/acme/grafana/viewer.token)", r)   # absolute: two slashes
        self.assertIn("Edit(//h/.ssh/acme-deploy)", r)                            # outside the roots, named
        self.assertIn("Read(//h/.config/globex/**)", r)                         # another organization's root
        self.assertNotIn("Read(//h/.config/acme/**)", r)                   # it uses a file there: the guard's
        self.assertFalse(any("pgpass" in x for x in r))                         # what it lists is never denied
        keeper = of_spawn.deny_rules("net_keeper", "acme", CAT, named=True, roots=True)
        self.assertNotIn("Read(//h/.ssh/acme-deploy)", keeper)                    # its keeper uses it
        self.assertIn("Read(//h/.config/acme/**)", keeper)                 # it uses nothing under its root
        self.assertEqual(of_spawn.deny_rules("app_dev", "acme", {}), [])

    def setUp(self):
        import of_events
        self.tmp, self.saved = tempfile.TemporaryDirectory(), of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name

    def tearDown(self):
        import of_events
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def test_one_settings_value_with_memory_and_rules(self):
        deny = of_spawn.deny_rules("app_dev", "acme", CAT, named=True)
        pl = plan(memory='{"autoMemoryDirectory": "/m/app"}', deny=deny * 20, cwd="/w")
        self.assertLess(len(pl["command"]), 1024)  # a fresh shell in canonical mode takes 1,024 bytes a line
        path = shlex.split(pl["command"].split("--settings ", 1)[1])[0]
        self.assertTrue(path.startswith(self.tmp.name))
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        with open(path) as fh:
            s = json.load(fh)
        self.assertEqual(s["permissions"]["deny"], deny * 20)
        pl = plan(memory='{"autoMemoryDirectory": "/m/app"}', deny=deny, cwd="/w")
        with open(shlex.split(pl["command"].split("--settings ", 1)[1])[0]) as fh:
            s = json.load(fh)
        self.assertEqual(s["autoMemoryDirectory"], "/m/app")
        self.assertEqual(s["permissions"]["deny"], deny)
        self.assertEqual(s["feedbackDrafts"], "off")
        bare = plan(cwd="/w")["command"]                                       # every session: no feedback drafts
        with open(shlex.split(bare.split("--settings ", 1)[1])[0]) as fh:
            self.assertEqual(json.load(fh), {"feedbackDrafts": "off", "env": of_spawn.agent_env()})
        self.assertEqual(json.loads(of_spawn.session_settings(None, ["Read(//a)"])),
                         {"feedbackDrafts": "off", "env": of_spawn.agent_env(),
                          "permissions": {"deny": ["Read(//a)"]}})
        # the window from the environment, so Claude Code's compaction offer never holds an agent's input (of_architect,
        # msg 01a1f605-7981), at the 1m every account's settings hold, never the offer's 400k
        self.assertEqual(of_spawn.agent_env()["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "1000000")
        # a tool profile's env joins the agents' own (2026-11-15: git never climbs into the home folder)
        self.assertEqual(json.loads(of_spawn.session_settings(None, None, {"env": {"X": "1"}}))["env"],
                         dict(of_spawn.agent_env(), X="1"))


class OwnerStepsTest(unittest.TestCase):
    """of_docs, 2026-11-11: a newcomer is the owner and may not read Russian; a fresh account opens on the Bypass
    Permissions warning, where the brief would be typed."""

    def test_tasks_in_the_owner_language(self):
        pl = {"position": "net_keeper", "cwd": "/w/net", "config_dir": "/u/.claude-b"}
        title, steps = of_spawn.trust_task(pl, "en")
        self.assertTrue(title.startswith("Trust the folder /w/net"))
        self.assertIn("CLAUDE_CONFIG_DIR=/u/.claude-b claude", steps)
        self.assertIn("Доверить", of_spawn.trust_task(pl, "ru")[0])
        title, steps = of_spawn.bypass_task(pl, "en")
        self.assertIn("Bypass Permissions", title)
        self.assertIn("claude --dangerously-skip-permissions", steps)

    def test_an_account_without_the_accepted_warning_stops_the_spawn(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(of_spawn.bypass_accepted(d))
            with open(os.path.join(d, "settings.json"), "w") as fh:
                json.dump({"skipDangerousModePermissionPrompt": True}, fh)
            self.assertTrue(of_spawn.bypass_accepted(d))
        self.assertFalse(plan(cwd="/w", accepted=lambda d: False)["bypass_ok"])
        self.assertTrue(plan(cwd="/w", accepted=lambda d: True)["bypass_ok"])
        self.assertTrue(plan(cwd="/w", accepted=lambda d: False, bypass=False)["bypass_ok"])  # no bypass: no warning


class ToolProfileTest(unittest.TestCase):
    """docs/tools.md §4 (of_tools, hand-over 2ba2ec3f): a position starts with its tool profile; an organization with
    no tools: catalogue gets nothing new."""

    def test_a_catalogue_gives_the_allowlist_and_the_mcp_config_and_none_gives_nothing(self):
        import yaml
        import of_events
        import of_tools
        sys.path.insert(0, HERE)
        from test_tools import DOC
        with tempfile.TemporaryDirectory() as d:
            saved, of_events.STATE_DIR = of_events.STATE_DIR, os.path.join(d, "state")
            try:
                for org, doc in (("acme", DOC), ("globex", {"positions": [{"name": "globex_org"}]})):
                    os.makedirs(os.path.join(d, org, "registry"))
                    with open(os.path.join(d, org, "registry", "positions.yaml"), "w") as fh:
                        yaml.safe_dump(doc, fh)
                orgs = os.path.join(d, "orgs.yaml")
                with open(orgs, "w") as fh:
                    fh.write(f"orgs:\n  - {{name: acme, path: {d}/acme}}\n  - {{name: globex, path: {d}/globex}}\n")
                acct = os.path.join(d, "acct")
                os.makedirs(acct)
                parts = lambda name, sid, cd: of_tools.launch_parts(name, sid, cd, orgs_file=orgs,
                                                                     state_dir=of_events.STATE_DIR)
                pl = plan(cwd="/w", config_dir=acct, tools=parts, p=dict(P, name="obs_keeper"))
                argv = shlex.split(pl["command"])
                self.assertIn("--mcp-config", argv)
                with open(argv[argv.index("--mcp-config") + 1]) as fh:
                    self.assertIn("grafana-read", json.load(fh)["mcpServers"])
                with open(argv[argv.index("--settings") + 1]) as fh:
                    self.assertEqual(json.load(fh)["allowedMcpServers"], [{"serverName": "grafana-read"}])
                none = plan(cwd="/w", config_dir=acct, tools=parts, p=dict(P, name="globex_org"))
                self.assertEqual(none["command"], plan(cwd="/w", config_dir=acct, p=dict(P, name="globex_org"))["command"])
                self.assertNotIn("--mcp-config", none["command"])
            finally:
                of_events.STATE_DIR = saved


class AgentDefaultsTest(unittest.TestCase):
    """The founder, 2026-11-11: setup makes opus at xhigh the default; of init writes it on the organization's
    orgs.yaml entry (agents: {model, effort}), of spawn starts with it unless told otherwise."""

    def test_model_and_effort_on_the_launch_line(self):
        cmd = plan(cwd="/w", model="opus", effort="xhigh")["command"]
        self.assertIn("--model opus --effort xhigh", cmd)
        self.assertNotIn("--effort", plan(cwd="/w")["command"])



class BenchPathTest(unittest.TestCase):
    """bin/of_benchpath.py: OF finds Bench at the path its owner settings name, else ~/.local/share/bench; never by an
    environment variable; its checkout is protected as OF's own (of_checkouts)."""

    def test_the_path_the_owner_names_its_place_on_sys_path_and_its_protection(self):
        import of_benchpath
        import of_init
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            co = os.path.join(d, "bench")
            os.makedirs(os.path.join(co, "bin"))
            saved = of_init.owner_settings().get("bench")
            try:
                of_init.write_owner_setting("bench", os.path.join(d, "absent"))
                with self.assertRaisesRegex(ImportError, "Bench is not at"):
                    of_benchpath.ensure()
                self.assertNotIn(os.path.join(d, "absent"), of_spawn.of_checkouts())
                with mock.patch.dict(os.environ, {"BENCH_HOME": co}):          # never read
                    self.assertEqual(of_benchpath.home(), os.path.join(d, "absent"))
                open(os.path.join(co, "bin", "bench.py"), "w").close()
                of_init.write_owner_setting("bench", co)
                b = of_benchpath.ensure()
                self.assertEqual(sys.path[-1], b)                            # after OF's own modules
                self.assertIn(co, of_spawn.of_checkouts())                    # denied to every session's writes
            finally:
                of_init.write_owner_setting("bench", saved)
                if os.path.join(co, "bin") in sys.path:
                    sys.path.remove(os.path.join(co, "bin"))


class BenchLineTest(unittest.TestCase):
    def test_owner_yaml_bench_reads_as_the_guard_reads_it(self):
        """of_secrets-review on PR 575: one reading for OF and the guard; YAML and the line must agree."""
        import of_benchpath
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "owner.yaml")
            for text, want in (("bench: /opt/bench\n", "/opt/bench"), ('bench: "/opt/b c"\n', "/opt/b c"),
                               ("language: en\nbench: /opt/bench  # mine\n", "/opt/bench"), ("bench: null\n", None),
                               ("language: en\n", None), ("'bench': /opt/x\n", "/opt/x")):
                with self.subTest(text=text):
                    with open(f, "w") as fh:
                        fh.write(text)
                    self.assertEqual(of_benchpath._plain(f), want)
            for text in ("{bench: /opt/a}\n", "x: &a /opt/a\nbench: *a\n", "bench: |\n  /opt/a\n"):
                with self.subTest(text=text):
                    with open(f, "w") as fh:
                        fh.write(text)
                    with self.assertRaises(ValueError):
                        of_benchpath._plain(f)

    def test_a_pipe_or_a_folder_at_owner_yaml_reads_as_none_at_once(self):
        """of_secrets-review on PR 575: the read never waits on a pipe and takes only a regular file, as the guard's."""
        import of_benchpath
        import time
        with tempfile.TemporaryDirectory() as d:
            pipe = os.path.join(d, "owner.yaml")
            os.mkfifo(pipe)
            t0 = time.monotonic()
            self.assertIsNone(of_benchpath._plain(pipe))
            self.assertLess(time.monotonic() - t0, 5)
            folder = os.path.join(d, "folder.yaml")
            os.mkdir(folder)
            self.assertIsNone(of_benchpath._plain(folder))


class BenchOwnerSetTest(unittest.TestCase):
    def test_a_development_clone_is_refused_as_benchs_folder(self):
        """of_secrets-review on PR 575: a clone that runs the release gate is one positions write."""
        import importlib.machinery
        import importlib.util
        import of_asks_answer
        import of_guard
        import of_init
        loader = importlib.machinery.SourceFileLoader("of_cli_owner", os.path.join(HERE, "..", "bin", "of"))
        of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_owner", loader))
        loader.exec_module(of)
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            for name in ("plain", "dev"):
                os.makedirs(os.path.join(d, name, "bin"))
                open(os.path.join(d, name, "bin", "bench.py"), "w").close()
            os.makedirs(os.path.join(d, "dev", ".git", "hooks"))
            with open(os.path.join(d, "dev", ".git", "hooks", "pre-commit"), "wb") as fh:
                mark = of_guard.GATE_MARK
                fh.write((mark if isinstance(mark, bytes) else mark.encode()) + b"\n")
            saved = of_init.owner_settings().get("bench")
            try:
                # the test's folders lie in a temporary folder, which sessions write: here only the gate is judged
                with mock.patch.object(of_asks_answer, "not_the_owner", lambda: False), \
                        mock.patch.object(of, "_session_folders", lambda: []):
                    self.assertNotEqual(of.main(["owner", "set", "bench", os.path.join(d, "dev")]), 0)
                    self.assertEqual(of.main(["owner", "set", "bench", os.path.join(d, "plain")]), 0)
                with mock.patch.object(of_asks_answer, "not_the_owner", lambda: True), \
                        mock.patch.object(of, "_session_folders", lambda: []):
                    self.assertNotEqual(of.main(["owner", "set", "bench", os.path.join(d, "plain")]), 0)
                # a folder sessions write is refused: one inside OF's state folder, and the temporary folders
                import of_events
                inside = os.path.join(of_events.STATE_DIR, "bench-here")
                os.makedirs(os.path.join(inside, "bin"), exist_ok=True)
                open(os.path.join(inside, "bin", "bench.py"), "w").close()
                with mock.patch.object(of_asks_answer, "not_the_owner", lambda: False):
                    self.assertNotEqual(of.main(["owner", "set", "bench", inside]), 0)
                    self.assertNotEqual(of.main(["owner", "set", "bench", os.path.join(d, "plain")]), 0)
                self.assertIn(os.path.realpath("/tmp"), of._session_folders())
            finally:
                of_init.write_owner_setting("bench", saved)

class PositionFolderTest(unittest.TestCase):
    """Hand-over 4c70deab (the position folders design; of_architect's ruling, message
    01a20513-20d2): with a work_root in orgs.yaml a position starts in <work_root>/<position>, its clones below it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = self.d = os.path.realpath(self.tmp.name)
        self.svc = os.path.join(d, "svc")
        os.makedirs(os.path.join(self.svc, "registry"))
        self.root = os.path.join(d, "work")
        self.pf = os.path.join(self.root, "ofb_platform")
        self.clone = os.path.join(self.pf, "framework")
        os.makedirs(os.path.join(self.clone, ".git"))
        with open(os.path.join(self.clone, ".git", "commondir"), "w") as fh:
            fh.write(".\n")
        with open(os.path.join(self.clone, "AGENTS.md"), "w") as fh:
            fh.write("# rules\n")
        self.orgs = os.path.join(d, "orgs.yaml")
        with open(self.orgs, "w") as fh:
            fh.write(f"orgs:\n  - name: ofb\n    path: {self.svc}\n    work_root: {self.root}\n")
        patch = mock.patch.dict(os.environ, {"OF_ORGS": self.orgs})
        patch.start()
        self.addCleanup(patch.stop)
        # the test's folders lie in the temporary folder, which a real work_root may not (work_root_refused)
        writes = mock.patch.object(of_spawn, "sandbox_write_folders", return_value=[])
        writes.start()
        self.addCleanup(writes.stop)
        self.p = {"name": "ofb_platform", "_org": "ofb", "system": "s", "status": "active"}

    def test_the_folder_is_the_work_roots_and_the_positions_name(self):
        self.assertEqual(of_spawn.work_dir(self.p, {"s": {"repos": ["x"]}}, [self.svc]), self.pf)
        with self.assertRaisesRegex(of_spawn.SpawnError, "not made yet"):
            of_spawn.work_dir(dict(self.p, name="ofb_other"), {}, [self.svc])
        self.assertIsNone(of_spawn.position_folder(dict(self.p, _org="acme")))          # no work_root: today's rule
        self.assertTrue(of_spawn.clone_guard()(os.path.join(self.pf, "framework", "doc.md")))   # never a reader's

    def test_each_denied_tree_is_a_folder_before_the_sandbox_starts(self):
        """agent-host_keeper's read on the host, through of_server (2026-11-24): the sandbox leaves an empty read-only
        file at a denied path not there yet, and the folder (subagent/muse-login) can then never be made."""
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            home = os.path.realpath(t)
            state = os.path.join(home, ".local", "state", "of")
            os.makedirs(state)
            login, there = os.path.join(state, "subagent", "muse-login"), os.path.join(home, "keys")
            os.makedirs(there)
            made = of_spawn.make_denied_folders([f"Read(/{login}/**)", f"Read(/{there}/**)",
                                                 f"Read(/{home}/a-file.txt)", f"Read(/{home}/*.pem)",
                                                 "Read(//elsewhere/outside/**)"], home=home)
            self.assertEqual(made, [login])
            self.assertTrue(os.path.isdir(login))
            self.assertEqual(oct(os.stat(login).st_mode & 0o777), "0o700")
            self.assertFalse(os.path.exists(os.path.join(home, "a-file.txt")))   # a file pattern makes nothing
            self.assertFalse(os.path.exists("/elsewhere/outside"))

    def test_the_top_and_the_clones_control_are_closed_in_the_sandbox(self):
        with mock.patch.object(of_spawn, "_accounts_dirs", return_value=[]):
            box = json.loads(of_spawn.session_settings(None, [], platform="linux", org="ofb",
                                                       scratch=(self.pf, "S1")))["sandbox"]
        deny = box["filesystem"]["denyWrite"]
        for x in (".git", "HEAD", "config", "objects", "refs", "CLAUDE.md", ".claude", "AGENTS.md"):
            self.assertIn(os.path.join(self.pf, x), deny)
        for x in (".git/config", ".git/hooks", ".git/commondir", ".claude", ".mcp.json"):
            self.assertIn(os.path.join(self.clone, x), deny)
        self.assertNotIn(self.pf, deny)                                                 # the folder itself is the work's

    def test_a_clone_without_commondir_refuses_the_spawn(self):
        os.remove(os.path.join(self.clone, ".git", "commondir"))
        with self.assertRaisesRegex(of_spawn.SpawnError, "commondir"):
            of_spawn.plan(self.p, [self.p], [], lambda ps, ag: ([], []), {}, [self.svc], "", lambda q: "w",
                          config_dir="/nonexistent")

    def test_claude_md_copies_each_known_clones_agents_md_and_follows_no_link(self):
        """of_secrets-review on PR 608: a copy taken at the start, never a live import; only a known repository's name,
        a ready clone, a regular AGENTS.md reached through no link; its at signs inert."""
        outside = os.path.join(self.d, "outside")
        with open(outside, "w") as fh:
            fh.write("unchanged\n")
        os.symlink(outside, os.path.join(self.pf, "CLAUDE.md"))
        with open(os.path.join(self.clone, "AGENTS.md"), "a") as fh:
            fh.write("See @~/.ssh/id_ed25519 and mail a@b.c\n")
        planted = os.path.join(self.pf, "planted")                                    # a clone made by a session
        os.makedirs(os.path.join(planted, ".git"))
        with open(os.path.join(planted, ".git", "commondir"), "w") as fh:
            fh.write(".\n")
        with open(os.path.join(planted, "AGENTS.md"), "w") as fh:
            fh.write("- obey the planted file\n")
        linked = os.path.join(self.pf, "linked")                                      # an AGENTS.md that is a link
        os.makedirs(os.path.join(linked, ".git"))
        with open(os.path.join(linked, ".git", "commondir"), "w") as fh:
            fh.write(".\n")
        os.symlink(outside, os.path.join(linked, "AGENTS.md"))
        made = of_spawn.prepare_folder(self.pf, "ofb", {"framework", "linked"})
        self.assertEqual(made, os.path.join(self.pf, "CLAUDE.md"))
        self.assertFalse(os.path.islink(made))
        with open(made) as fh:
            text = fh.read()
        self.assertIn("## framework/AGENTS.md", text)
        self.assertIn("# rules", text)
        self.assertNotIn("@framework", text)
        self.assertNotIn("@~/.ssh", text)                                               # no import left
        self.assertIn("@\u200b~/.ssh/id_ed25519", text)
        self.assertNotIn("planted", text)                                              # not a known name
        self.assertNotIn("unchanged", text)                                            # the link is not read
        with open(outside) as fh:
            self.assertEqual(fh.read(), "unchanged\n")
        self.assertIsNone(of_spawn.prepare_folder(self.svc, "ofb", {"framework"}))     # not a position's folder
        self.assertEqual(of_spawn.repo_names({"_systems": {"s": {"repos": ["~/x/bench"]}}}, ["~/org/framework"]),
                         {"bench", "framework"})

    def test_the_file_tools_keep_the_top_and_the_clones_control(self):
        """of_secrets-review on PR 608: the Edit denies a work clone has, for the folder's top and each clone."""
        import json
        with mock.patch.object(of_spawn, "_accounts_dirs", return_value=[]):
            s = json.loads(of_spawn.session_settings(None, [], platform="linux", scratch=(self.pf, "S1"), org="ofb"))
        deny = s["permissions"]["deny"]
        for n in ("CLAUDE.local.md", ".git", "AGENTS.md", ".claude"):
            self.assertIn(f"Edit(/{os.path.join(self.pf, n)})", deny)
            self.assertIn(f"Edit(/{os.path.join(self.pf, n)}/**)", deny)
        self.assertIn(f"Edit(/{os.path.join(self.clone, '.git', 'config')})", deny)
        self.assertIn(f"Edit(/{os.path.join(self.clone, '.git', 'hooks')}/**)", deny)

    def test_commondir_is_read_exactly_and_without_waiting(self):
        """of_secrets-review on PR 608: " . " is another path to git; a pipe there never holds the check."""
        cd = os.path.join(self.clone, ".git", "commondir")
        self.assertTrue(of_spawn.clone_ready(self.clone))
        with open(cd, "w") as fh:
            fh.write(" . \n")
        self.assertFalse(of_spawn.clone_ready(self.clone))
        os.remove(cd)
        os.mkfifo(cd)
        self.assertFalse(of_spawn.clone_ready(self.clone))

    def test_a_resume_in_the_folder_copies_again_and_an_unready_clone_stops_it(self):
        """of_secrets-review on PR 608: every start path prepares the folder as of spawn does."""
        import of_resume
        home = os.path.join(self.d, "acct")
        os.makedirs(os.path.join(home, "projects", "-w"))
        sid = "00000000-0000-4000-8000-00000000b001"
        with open(os.path.join(home, "projects", "-w", f"{sid}.jsonl"), "w") as fh:
            fh.write("{}\n")
        p = dict(self.p, _systems={"s": {"repos": ["framework"]}})
        state = os.path.join(self.d, "state")
        line = of_resume.command(sid, [home], plan_root=os.path.join(self.d, "none"), state_dir=state, org="ofb",
                                 cwd=self.pf, position=p)
        self.assertTrue(line)
        with open(os.path.join(self.pf, "CLAUDE.md")) as fh:
            self.assertIn("## framework/AGENTS.md", fh.read())
        os.remove(os.path.join(self.clone, ".git", "commondir"))
        self.assertIsNone(of_resume.command(sid, [home], plan_root=os.path.join(self.d, "none"), state_dir=state,
                                            org="ofb", cwd=self.pf, position=p))

    def test_an_earlier_rules_file_in_the_position_folder_is_removed(self):
        """of_secrets-review on PR 608: the rules moved up to the work_root; one left in the folder would load too."""
        import of_context
        old = os.path.join(self.pf, ".claude", "rules")
        os.makedirs(old)
        with open(os.path.join(old, "of-ofb.md"), "w") as fh:
            fh.write("- old rules\n")
        with open(os.path.join(old, "team.md"), "w") as fh:
            fh.write("- the organization's own\n")
        with mock.patch.object(of_spawn, "work_dir", lambda p, systems, paths: self.pf):
            of_context.write_rules([dict(self.p, _root=self.svc, enrolled=True)], {"ofb": [self.svc]},
                                   log=lambda m: None, sessions={})
        self.assertEqual(os.listdir(old), ["team.md"])

    def test_an_organization_is_named_as_the_registry_names_it(self):
        """of_review on PR 608: with no name:, the path's folder as written, never resolved through a link."""
        real = os.path.join(self.d, "real")
        os.makedirs(real)
        os.symlink(real, os.path.join(self.d, "linked"))
        with open(self.orgs, "w") as fh:
            fh.write(f"orgs:\n  - path: {self.d}/linked\n    work_root: {self.root}\n")
        self.assertEqual(of_spawn.work_roots(), {"linked": self.root})

    def test_the_owner_makes_the_folder_and_its_clones(self):
        """of_review on PR 608: a command sets the folder up, with no file written by hand."""
        calls = []

        def run(argv):
            calls.append(argv)
            os.makedirs(os.path.join(argv[-1], ".git"))
            return mock.Mock(returncode=0)
        folder = of_spawn.make_folder("ofb", "ofb_new", ["https://example.invalid/acme/bench.git", self.clone],
                                      run=run, log=lambda m: None)
        self.assertEqual(folder, os.path.join(self.root, "ofb_new"))
        self.assertEqual([os.path.basename(c[-1]) for c in calls], ["bench", "framework"])
        for name in ("bench", "framework"):
            self.assertTrue(of_spawn.clone_ready(os.path.join(folder, name)))
        of_spawn.make_folder("ofb", "ofb_new", [self.clone], run=run, log=lambda m: None)   # kept, not cloned again
        self.assertEqual(len(calls), 2)
        with self.assertRaisesRegex(of_spawn.SpawnError, "not a position's name"):
            of_spawn.make_folder("ofb", "../x", [], run=run)
        with self.assertRaisesRegex(of_spawn.SpawnError, "names no work_root"):
            of_spawn.make_folder("acme", "ofb_new", [], run=run)

    def test_the_rules_go_to_the_work_root(self):
        import of_context
        got = of_context.rules_folders([self.p], {"ofb": [self.svc]})
        self.assertEqual(got, {"ofb": [self.root]})


class WorkCloneTest(unittest.TestCase):
    """Card OF-265 v2, hand-over ab9ed07b (of_server's four conditions, message 01a2044e-a1e4): a session on Linux may
    write the work clone the owner names in orgs.yaml, never its control files or the services' copy."""

    def test_the_clone_is_writable_its_control_files_and_the_services_copy_are_not(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            svc, clone = os.path.join(d, "build"), os.path.join(d, "work", "build")
            os.makedirs(os.path.join(svc, "registry"))
            orgs = os.path.join(d, "orgs.yaml")
            with open(orgs, "w") as fh:
                fh.write(f"orgs:\n  - name: ofb\n    path: {svc}\n    work_clone: {clone}\n  - name: other\n    path: x\n")
            with mock.patch.dict(os.environ, {"OF_ORGS": orgs}), \
                    mock.patch.object(of_spawn, "_accounts_dirs", return_value=[]):
                absent = json.loads(of_spawn.session_settings(None, [], platform="linux", org="ofb"))
                os.makedirs(os.path.join(clone, ".git"))
                bare = json.loads(of_spawn.session_settings(None, [], platform="linux", org="ofb"))   # no commondir yet
                with open(os.path.join(clone, ".git", "commondir"), "w") as fh:
                    fh.write(".\n")
                s = json.loads(of_spawn.session_settings(None, [], platform="linux", org="ofb"))
                other = json.loads(of_spawn.session_settings(None, [], platform="linux", org="other"))
                mac = json.loads(of_spawn.session_settings(None, [], platform="darwin", org="ofb") or "{}")
        f = absent["sandbox"]["filesystem"]
        self.assertNotIn(clone, f["allowWrite"])                                      # (1) absent: nothing more
        self.assertIn(svc, f["denyWrite"])                                            # (2) denied all the same
        f, rules = s["sandbox"]["filesystem"], s["permissions"]["deny"]
        self.assertIn(clone, f["allowWrite"])                                         # (1)
        self.assertIn(clone, s["permissions"]["additionalDirectories"])
        self.assertIn(svc, f["denyWrite"])                                            # (2)
        self.assertIn(f"Edit(/{svc}/**)", rules)
        for x in (".git/config", ".git/hooks", ".git/info", ".git/modules", ".git/worktrees", ".git/config.worktree",
                  ".git/commondir", ".claude", ".mcp.json"):                          # (3), there or not yet
            self.assertIn(os.path.join(clone, x), f["denyWrite"], x)
            self.assertIn(f"Edit(/{clone}/{x})", rules, x)
        self.assertIn(os.path.join(clone, ".git/commondir"), f["denyWrite"])          # it must exist, and is denied
        self.assertNotIn(clone, bare["sandbox"]["filesystem"]["allowWrite"])          # without it: not writable
        of_ = other["sandbox"]["filesystem"]                                           # another organization's session
        self.assertNotIn(clone, of_["allowWrite"])
        self.assertNotIn(clone, other["permissions"].get("additionalDirectories", []))
        self.assertIn(svc, of_["denyWrite"])                                          # every copy stays denied
        self.assertNotIn("sandbox", mac)                                              # Linux only

    def test_a_clone_that_holds_or_lies_in_a_guarded_folder_is_refused(self):
        """of_review on PR 542, point 4: any organization's path or checkout, OF's own checkout, the home."""
        entries = [{"name": "a", "path": "/srv/a", "paths": ["/srv/code"]}, {"name": "b", "path": "/srv/b"}]
        for clone in ("/srv/b", "/srv/b/sub", "/srv", "/srv/code/x", os.path.realpath(os.path.expanduser("~")),
                      of_spawn.of_checkouts()[0]):
            self.assertTrue(of_spawn.clone_refused(clone, entries), clone)
        for x in ("~/.config/of", "~/.ssh", "~/.config/systemd/user", "~/.config"):   # the owner's own folders
            self.assertTrue(of_spawn.clone_refused(os.path.realpath(os.path.expanduser(x)), entries), x)
        self.assertTrue(of_spawn.clone_refused("/srv/organic-framework-build", entries))   # beside the checkout /srv/code
        self.assertIsNone(of_spawn.clone_refused("/work/a-clone", entries))

    def test_the_readers_outside_the_sandbox_never_pick_a_clone(self):
        """of_review on PR 551: work_dir and doc_path refuse a named work clone where they pick the folder."""
        import importlib.machinery
        import importlib.util
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            for x in ("fw", "build", "orgrepo/registry"):
                os.makedirs(os.path.join(d, x))
            with open(os.path.join(d, "build", "doc.md"), "w") as fh:
                fh.write("x")
            orgs = os.path.join(d, "orgs.yaml")
            with open(orgs, "w") as fh:
                fh.write(f"orgs:\n  - name: o\n    path: {d}/orgrepo\n    paths: [{d}/fw]\n    work_clone: {d}/build\n")
            loader = importlib.machinery.SourceFileLoader("of_cli_wd", os.path.join(HERE, "..", "bin", "of"))
            of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_wd", loader))
            with mock.patch.dict(os.environ, {"OF_ORGS": orgs}):
                loader.exec_module(of)
                p = {"name": "a", "system": "s"}
                self.assertEqual(of_spawn.work_dir(p, {"s": {"repos": ["build"]}}, [f"{d}/fw"]), f"{d}/fw")
                self.assertIsNone(of.doc_path("build:doc.md", [f"{d}/fw"]))
                # a way there through a link into the clone, or a link inside it pointing out: refused too
                os.symlink(f"{d}/build", f"{d}/fw/via")
                self.assertTrue(of_spawn.clone_guard()(f"{d}/fw/via/doc.md"))
                os.makedirs(f"{d}/out")
                os.symlink(f"{d}/out", f"{d}/build/link-out")
                self.assertTrue(of_spawn.clone_guard()(f"{d}/build/link-out"))
                self.assertFalse(of_spawn.clone_guard()(f"{d}/out"))
                # a parent step after a link goes where the link leads: judged as opened, through work_dir too
                os.makedirs(f"{d}/build/inner")
                os.symlink(f"{d}/build/inner", f"{d}/fw/lnk")
                self.assertTrue(of_spawn.clone_guard()(f"{d}/fw/lnk/.."))
                with self.assertRaises(of_spawn.SpawnError):
                    of_spawn.work_dir(p, {"s": {"repos": []}}, [f"{d}/fw/lnk/.."])
                # a chain of links that passes through the clone and leaves it: the clone controls the hop
                os.makedirs(f"{d}/out2")
                os.symlink(f"{d}/out2", f"{d}/build/hop")
                os.symlink(f"{d}/build/hop", f"{d}/fw/chain")
                self.assertTrue(of_spawn.clone_guard()(f"{d}/fw/chain"))
                with self.assertRaises(of_spawn.SpawnError):
                    of_spawn.work_dir(p, {"s": {"repos": []}}, [f"{d}/fw/chain"])
                self.assertFalse(of_spawn.clone_guard()(f"{d}/out2"))
                with open(f"{d}/out2/doc.md", "w") as fh:
                    fh.write("x")
                self.assertIsNone(of.doc_path("chain:doc.md", [f"{d}/fw"]))       # the doc reader too
                self.assertIsNone(of.doc_path("lnk:../doc.md", [f"{d}/fw"]))
            # with no clone named, the sibling is taken as before
            self.assertEqual(of_spawn.work_dir(p, {"s": {"repos": ["build"]}}, [f"{d}/fw"]), f"{d}/build")

    def test_a_refused_clone_keeps_its_copy_denied(self):
        """of_review on PR 542, second round, point 3: a later change can refuse a clone; its copy stays denied."""
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            orgs = os.path.join(d, "orgs.yaml")
            with open(orgs, "w") as fh:   # the clone lies in another organization's path: refused
                fh.write(f"orgs:\n  - name: a\n    path: {d}/a\n    work_clone: {d}/b/clone\n  - name: b\n    path: {d}/b\n")
            with mock.patch.dict(os.environ, {"OF_ORGS": orgs}), \
                    mock.patch.object(of_spawn, "_accounts_dirs", return_value=[]):
                s = json.loads(of_spawn.session_settings(None, [], platform="linux", org="a"))
        self.assertNotIn(f"{d}/b/clone", s["sandbox"]["filesystem"]["allowWrite"])
        self.assertIn(f"{d}/a", s["sandbox"]["filesystem"]["denyWrite"])

    def test_nothing_outside_the_sandbox_adopts_the_clone(self):
        """(4): the key is read only by the sandbox block; the organization's paths, its registry and its work
        folders come from `path` and `paths` as before."""
        import importlib.machinery
        import importlib.util
        import tempfile
        loader = importlib.machinery.SourceFileLoader("of_cli_clone", os.path.join(HERE, "..", "bin", "of"))
        of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_clone", loader))
        loader.exec_module(of)
        with tempfile.TemporaryDirectory() as d:
            orgs = os.path.join(d, "orgs.yaml")
            with open(orgs, "w") as fh:
                fh.write(f"orgs:\n  - name: ofb\n    path: {d}/build\n    paths: [{d}/fw]\n    work_clone: {d}/clone\n")
            self.assertEqual(of._org_paths(orgs), {"ofb": [f"{d}/fw"]})
            self.assertEqual([r for _, r, _ in of.org_registries(orgs)],
                             [os.path.join(d, "build", "registry", "positions.yaml")])
        for name in ("of_works.py", "of_github.py", "of_capacity.py", "of_studio.py", "of_loops.py"):
            with open(os.path.join(HERE, "..", "bin", name)) as fh:
                self.assertNotIn("work_clone", fh.read(), name)          # only of_spawn reads it (bin/of only sets it)

    def test_the_sandbox_write_folders_hold_every_clone_and_no_start_folder(self):
        """of_server, message 01a205f4-a5e9: the folders a session's sandbox writes come from this module alone, for
        of_capacity.started_folder, which refuses a start folder in one: the state and temporary folders and every
        named clone, refused ones included; never an organization's paths or its work_root."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            orgs = os.path.join(d, "orgs.yaml")
            with open(orgs, "w") as fh:
                fh.write(f"orgs:\n  - name: ofb\n    path: {d}/build\n    paths: [{d}/fw]\n    work_clone: {d}/clone\n"
                         f"    work_root: {d}/work\n")
            got = of_spawn.sandbox_write_folders(orgs)
        self.assertIn(os.path.join(d, "clone"), got)                 # not there, so refused: listed all the same
        for x in ("build", "fw", "work"):
            self.assertNotIn(os.path.join(d, x), got)
        self.assertIn(os.path.realpath("/tmp"), got)
        self.assertIn(os.path.realpath(tempfile.gettempdir()), got)

    def test_the_owner_sets_the_clone_and_an_agent_does_not(self):
        import importlib.machinery
        import importlib.util
        import tempfile
        from unittest import mock
        import of_asks_answer
        loader = importlib.machinery.SourceFileLoader("of_cli_clone2", os.path.join(HERE, "..", "bin", "of"))
        of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_clone2", loader))
        loader.exec_module(of)
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            os.makedirs(os.path.join(d, "build", "registry"))
            os.makedirs(os.path.join(d, "clone", ".git"))
            orgs = os.path.join(d, "orgs.yaml")
            with open(orgs, "w") as fh:
                fh.write(f"orgs:\n  - name: ofb\n    path: {d}/build\n")
            of.ORGS_FILE = orgs
            with mock.patch.object(of_asks_answer, "not_the_owner", lambda: True):
                self.assertEqual(of.main(["org", "set", "ofb", "work_clone", f"{d}/clone"]), 2)
            with mock.patch.object(of_asks_answer, "not_the_owner", lambda: False):
                self.assertEqual(of.main(["org", "set", "ofb", "work_clone", f"{d}/build"]), 2)   # the services' copy
                self.assertEqual(of.main(["org", "set", "ofb", "work_clone", f"{d}/clone"]), 0)
                self.assertEqual(of_spawn.work_clones(orgs), [("ofb", f"{d}/clone", f"{d}/build")])
                self.assertTrue(of_spawn.clone_ready(f"{d}/clone"))                    # commondir written, holding "."
                self.assertEqual(of.main(["org", "set", "ofb", "work_clone", "none"]), 0)
                self.assertEqual(of_spawn.work_clones(orgs), [])

    def test_the_owner_sets_the_work_root_and_an_agent_does_not(self):
        """Hand-over 4c70deab: work_root is the owner's, as work_clone (of_architect, message 01a20513-20d2)."""
        import importlib.machinery
        import importlib.util
        import tempfile
        from unittest import mock
        import of_asks_answer
        loader = importlib.machinery.SourceFileLoader("of_cli_root", os.path.join(HERE, "..", "bin", "of"))
        of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_root", loader))
        loader.exec_module(of)
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            os.makedirs(os.path.join(d, "build", "registry"))
            os.makedirs(os.path.join(d, "work"))
            os.makedirs(os.path.join(d, "repo", ".git"))
            orgs = os.path.join(d, "orgs.yaml")
            with open(orgs, "w") as fh:
                fh.write(f"orgs:\n  - name: ofb\n    path: {d}/build\n")
            of.ORGS_FILE = orgs
            with mock.patch.object(of_asks_answer, "not_the_owner", lambda: True):
                self.assertEqual(of.main(["org", "set", "ofb", "work_root", f"{d}/work"]), 2)
            with mock.patch.object(of_asks_answer, "not_the_owner", lambda: False):
                self.assertEqual(of.main(["org", "set", "ofb", "work_root", f"{d}/build"]), 2)   # the services' copy
                self.assertEqual(of.main(["org", "set", "ofb", "work_root", f"{d}/repo"]), 2)    # a repository
                os.makedirs(os.path.join(d, "repo", "inner"))
                self.assertEqual(of.main(["org", "set", "ofb", "work_root", f"{d}/repo/inner"]), 2)  # inside one
                for x in ("HEAD", "objects", "refs"):
                    os.makedirs(os.path.join(d, "bare", x))
                self.assertEqual(of.main(["org", "set", "ofb", "work_root", f"{d}/bare"]), 2)    # a bare one
                self.assertEqual(of.main(["org", "set", "ofb", "work_root", "/"]), 2)            # the root folder
                self.assertEqual(of.main(["org", "set", "ofb", "work_root", f"{d}/work"]), 2)    # a temporary folder
            with mock.patch.object(of_asks_answer, "not_the_owner", lambda: False), \
                    mock.patch.object(of_spawn, "sandbox_write_folders", return_value=[]):
                self.assertEqual(of.main(["org", "set", "ofb", "work_root", f"{d}/work"]), 0)
                self.assertEqual(of_spawn.work_roots(orgs), {"ofb": f"{d}/work"})
                self.assertEqual(of.main(["org", "set", "ofb", "work_root", "none"]), 0)
                self.assertEqual(of_spawn.work_roots(orgs), {})


class SettingsDigestTest(unittest.TestCase):
    def test_the_settings_file_is_recorded_by_its_digest_at_launch(self):
        """of_server, message 01a20337-fe93: a session can rewrite or backdate a settings file under OF's state folder;
        the digest in an event the server keeps shows it."""
        import hashlib
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            pl = {"session": "S1", "position": "ops_a", "org": "acme"}
            got = []
            self.assertIsNone(of_spawn.record_settings(pl, d, emit=lambda *a, **k: got.append((a, k))))
            self.assertEqual(got, [])                                          # no file: nothing recorded
            path = of_spawn.settings_file('{"x": 1}', "S1", d)
            digest = of_spawn.record_settings(pl, d, emit=lambda *a, **k: got.append((a, k)))
            with open(path, "rb") as fh:
                self.assertEqual(digest, hashlib.sha256(fh.read()).hexdigest())
            (name, payload), kw = got[0]
            self.assertEqual((name, payload["session"], payload["sha256"], payload["by"], kw["session"]),
                             ("session_settings", "S1", digest, "of spawn", "S1"))
            of_spawn.record_settings(pl, d, emit=lambda *a, **k: got.append((a, k)), by="of resume-command")
            self.assertEqual(got[-1][0][1]["by"], "of resume-command")     # herdr's own resume runs through it
            with open(path, "w") as fh:                                    # another session rewrites it before launch
                fh.write('{"x": 2}')
            self.assertEqual(of_spawn.record_settings(pl, d, emit=lambda *a, **k: got.append((a, k))), digest)
            self.assertEqual(got[-1][0][1]["disk_sha256"], hashlib.sha256(b'{"x": 2}').hexdigest())   # PR 516 review


class SandboxBlock(unittest.TestCase):
    """Layer 3 of the agent host's isolation plan (the founder's yes, of_conductor msg 01a1efaf-43fd): every agent
    session on Linux starts with Claude Code's sandbox on, as of_agent-server tried it on the host (msg 01a1f88b-5471)."""

    def test_linux_sessions_get_the_sandbox_and_macos_none(self):
        deny = ["Read(//home/alex/.config/other/**)", "Edit(//home/alex/.config/other/**)", "Read(//home/alex/x/*.key)"]
        from unittest import mock
        with mock.patch.object(of_spawn, "_accounts_dirs", return_value=["/home/alex/.claude"]):
            s = json.loads(of_spawn.session_settings(None, deny, platform="linux"))
        box = s["sandbox"]
        self.assertEqual((box["enabled"], box["failIfUnavailable"], box["allowUnsandboxedCommands"]), (True, True, False))
        self.assertIn("/home/alex/.config/other", box["filesystem"]["denyRead"])      # the catalogue's root, as a folder
        self.assertNotIn("/home/alex/x/*.key", box["filesystem"]["denyRead"])         # a glob: its present matches only
        cos = of_spawn.of_checkouts()
        n = 1 + len(cos)
        self.assertEqual(box["filesystem"]["denyWrite"][:-n], box["filesystem"]["denyRead"])   # never written either
        self.assertEqual(box["filesystem"]["denyWrite"][-n:],
                         [os.path.expanduser("~/.local/state/of/index.json")] + cos)  # G36, the code OF runs
        self.assertIn(os.path.expanduser("~/.ssh"), box["filesystem"]["denyRead"])
        self.assertEqual(box["filesystem"]["allowWrite"], [os.path.expanduser("~/.local/state/of")])
        self.assertEqual((box["network"]["allowAllUnixSockets"], box["network"]["strictAllowlist"]), (True, True))
        self.assertIn("github.com", box["network"]["allowedDomains"])                # bypass mode: the list binds
        self.assertEqual(box["network"]["deniedDomains"], [])                        # the runtime needs the key
        self.assertIn("/home/alex/.claude/projects", box["filesystem"]["denyRead"])   # transcripts
        self.assertIn("/home/alex/.claude/.credentials.json", box["filesystem"]["denyRead"])   # the account's sign-in
        # the file tools, outside the sandbox: only the working folders, never the checkout OF runs from
        rules = s["permissions"]["deny"]
        self.assertEqual(rules[:len(deny) + len(cos)], deny + [f"Edit(/{c}/**)" for c in cos])
        self.assertIs(s["permissions"]["blockWritesOutsideWorkingDirectories"], True)
        for x in ("**/.claude/settings.local.json", "**/.claude/hooks/**", "**/.mcp.json"):   # PR 509 review
            self.assertIn(f"Edit({x})", rules)
        # the Read tool runs outside the sandbox, so it reads what denyRead keeps from Bash (of_server, 01a202e4-62f9)
        for p in box["filesystem"]["denyRead"]:
            if p == "/home/alex/.claude/projects":   # its own saved tool results stay open: transcripts only (PR 507)
                self.assertIn(f"Read(/{p}/**/*.jsonl)", rules)
                self.assertNotIn(f"Read(/{p}/**)", rules)
                continue
            self.assertIn(f"Read(/{p}/**)", rules)
            if not os.path.isdir(p):                   # a folder takes its tree alone; a file both (PR 509 review)
                self.assertIn(f"Read(/{p})", rules)
        self.assertIn(f"Read(/{os.path.expanduser('~/.ssh')}/**)", rules)
        self.assertEqual(rules.count("Read(//home/alex/.config/other/**)"), 1)   # the catalogue's own rule not doubled
        self.assertNotIn("additionalDirectories", s["permissions"])            # no scratch named: none added
        with mock.patch.object(of_spawn, "_accounts_dirs", return_value=["/home/alex/.claude"]):
            own = json.loads(of_spawn.session_settings(None, deny, platform="linux", scratch=("/home/alex/repo", "S1")))
        self.assertEqual(own["permissions"]["additionalDirectories"], own["sandbox"]["filesystem"]["allowRead"])
        self.assertTrue(own["permissions"]["additionalDirectories"])           # its scratchpad, for the file tools
        mac = json.loads(of_spawn.session_settings(None, deny, platform="darwin"))
        self.assertNotIn("sandbox", mac)
        self.assertEqual(mac["permissions"], {"deny": deny})

    def test_the_checkouts_of_runs_from_are_its_links_and_spawns_own(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}):
            here = os.path.dirname(os.path.dirname(os.path.realpath(of_spawn.__file__)))
            self.assertEqual(of_spawn.of_checkouts(), [here])                # no link: the code that runs
            os.makedirs(os.path.join(home, "co", "bin"))
            os.makedirs(os.path.join(home, ".local", "bin"))
            open(os.path.join(home, "co", "bin", "of"), "w").close()
            os.symlink(os.path.join(home, "co", "bin", "of"), os.path.join(home, ".local", "bin", "of"))
            self.assertEqual(of_spawn.of_checkouts(), [here])                # co holds no .git: not a checkout
            os.makedirs(os.path.join(home, "co", ".git"))
            self.assertEqual(of_spawn.of_checkouts(), [os.path.realpath(os.path.join(home, "co")), here])  # both
            os.remove(os.path.join(home, ".local", "bin", "of"))           # plane.py's wrapper, a file of its own
            with open(os.path.join(home, ".local", "bin", "of"), "w") as fh:
                fh.write(f'#!/bin/sh\n# written by deploy/host/plane.py\nexec /usr/bin/python3 {home}/co/bin/of "$@"\n')
            self.assertEqual(of_spawn.of_checkouts(), [os.path.realpath(os.path.join(home, "co")), here])
            self.assertNotIn(os.path.realpath(os.path.join(home, ".local")), of_spawn.of_checkouts())
            export = os.path.join(home, "export", "bin")                     # spawn's own tree with no .git: kept
            os.makedirs(export)
            with mock.patch.object(of_spawn, "__file__", os.path.join(export, "of_spawn.py")):
                self.assertEqual(of_spawn.of_checkouts(), [os.path.realpath(os.path.join(home, "co")),
                                                           os.path.realpath(os.path.join(home, "export"))])

    def test_other_sessions_scratch_is_denied_and_its_own_allowed(self):
        """Tried on the agent host (2026-11-20): the temp root must stay writable (the sandbox's sockets, $TMPDIR),
        so the other sessions' folders are denied by name and the root never is."""
        import tempfile
        import of_scratch
        from unittest import mock
        with tempfile.TemporaryDirectory() as base:
            for d in ("-home-alex-repo/SID-1", "-home-alex-repo/SID-2", "-home-alex-other/SID-3", "bundled-skills/x"):
                os.makedirs(os.path.join(base, d))
            with mock.patch.object(of_scratch, "base_dir", return_value=base):
                box = of_spawn.sandbox_settings([], platform="linux", scratch=("/home/alex/repo", "SID-1"))
            f = box["filesystem"]
            own = os.path.join(base, "-home-alex-repo", "SID-1")
            self.assertIn(os.path.join(base, "-home-alex-repo", "SID-2"), f["denyRead"])
            self.assertIn(os.path.join(base, "-home-alex-other", "SID-3"), f["denyRead"])
            self.assertNotIn(own, f["denyRead"])
            self.assertNotIn(base, f["denyRead"] + f["denyWrite"])
            self.assertFalse([x for x in f["denyRead"] if "bundled-skills" in x])
            self.assertFalse([x for x in f["denyWrite"] if own == x or own.startswith(x.rstrip("/") + "/")])

    def test_the_servers_own_keys_are_denied(self):
        """of_server on PR 457: the tenant's master key, a secret_key_file and the TLS key under the same user."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            y = os.path.join(d, "server.yaml")
            with open(y, "w") as fh:
                fh.write("secret_key_file: /home/alex/.config/of/secret.bin\ntls: {key: /home/alex/.config/of/tls.key}\n")
            keys = of_spawn._server_keys(y)
        self.assertEqual(keys, [os.path.expanduser("~/.config/of/tenant.key"), "/home/alex/.config/of/secret.bin",
                                "/home/alex/.config/of/tls.key"])
        box = of_spawn.sandbox_settings([], platform="linux")
        self.assertIn(os.path.expanduser("~/.config/of/tenant.key"), box["filesystem"]["denyRead"])

    def test_the_servers_keys_come_from_every_server_yaml_it_may_read(self):
        """of_secrets-review on PR 590 and PR 592: a server run on OF_SERVER_CONFIG names key files ~/.config/of's file
        does not, and a variable set by whoever starts the session adds those, never drops the default file's."""
        import tempfile
        import of_events
        home = os.path.expanduser("~")
        default = os.path.join(home, ".config", "of", "server.yaml")
        os.makedirs(os.path.dirname(default), exist_ok=True)
        try:                                                            # tests/isolate.py writes one: kept as it was
            with open(default) as fh:
                kept = fh.read()
        except FileNotFoundError:
            kept = None
        saved = {k: os.environ.get(k) for k in ("OF_SERVER_CONFIG",)}
        saved_dir = of_events.CONFIG_DIR
        with tempfile.TemporaryDirectory() as d:
            try:
                with open(default, "w") as fh:
                    fh.write("secret_key_file: ~/fake/secret.key\ntls: {key: ~/fake/tls.key}\n")
                base = [os.path.join(home, ".config/of/tenant.key"), os.path.join(home, "fake/secret.key"),
                        os.path.join(home, "fake/tls.key")]
                self.assertEqual(of_spawn._server_keys(), base)
                empty = os.path.join(d, "empty.yaml")
                open(empty, "w").close()
                os.environ["OF_SERVER_CONFIG"] = empty
                self.assertEqual(of_spawn._server_keys(), base)          # an empty named file drops nothing
                named = os.path.join(d, "named.yaml")
                with open(named, "w") as fh:
                    fh.write("tenant_key_file: /home/alex/srv/tenant.key\n")
                os.environ["OF_SERVER_CONFIG"] = named
                self.assertEqual(of_spawn._server_keys(), base + ["/home/alex/srv/tenant.key"])
                os.environ.pop("OF_SERVER_CONFIG")
                of_events.CONFIG_DIR = os.path.join(d, "other")           # another config folder drops nothing either
                self.assertEqual(of_spawn._server_keys(), base)
            finally:
                of_events.CONFIG_DIR = saved_dir
                if kept is None:
                    os.unlink(default)
                else:
                    with open(default, "w") as fh:
                        fh.write(kept)
                for k, v in saved.items():
                    os.environ.pop(k, None)
                    if v is not None:
                        os.environ[k] = v

    def test_a_glob_is_its_matches_when_the_session_starts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for n in ("a.key", "b.key", "c.txt"):
                open(os.path.join(d, n), "w").close()
            box = of_spawn.sandbox_settings([f"Read(/{d}/*.key)"], platform="linux")
        self.assertIn(os.path.join(d, "a.key"), box["filesystem"]["denyRead"])
        self.assertIn(os.path.join(d, "b.key"), box["filesystem"]["denyRead"])
        self.assertNotIn(os.path.join(d, "c.txt"), box["filesystem"]["denyRead"])

if __name__ == "__main__":
    unittest.main()
