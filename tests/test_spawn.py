"""of spawn: the brief is declared by construction; refusals before anything starts."""

import json
import os
import shlex
import tempfile
import sys
import unittest

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
    def test_folder_brief_place_and_command(self):
        exists = lambda path: path.endswith(("acme/ansible-creek", "acme"))
        self.assertTrue(of_spawn.work_dir(P, SYSTEMS, ["~/acme"], exists).endswith("acme/ansible-creek"))
        self.assertTrue(of_spawn.work_dir(dict(P, system="sec"), SYSTEMS, ["~/acme"], exists).endswith("/acme"))
        pl = plan(cwd="/tmp")
        self.assertEqual((pl["workspace"], pl["tab"], pl["session"]), ("ac·platform", "net", "S-1"))
        self.assertTrue(pl["brief"].startswith("You hold the position net_keeper. Read /h/acme/org/registry/briefs/"))
        self.assertIn("--session-id S-1 -n net_keeper --dangerously-skip-permissions", pl["command"])
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


CAT = {  # of_credentials.load()'s shape: two organizations, absolute paths
    "acme": {"roots": ["/h/.config/acme"],
           "credentials": {"graf": {"paths": ["/h/.config/acme/grafana/viewer.token"], "keeper": "obs_keeper"},
                           "deploy": {"paths": ["/h/.ssh/mt-deploy"], "keeper": "net_keeper"},
                           "pg": {"paths": ["/h/.config/acme/pg/*.pgpass"], "keeper": "db_keeper"}},
           "positions": {"app_dev": ["pg"]}},
    "globex": {"roots": ["/h/.config/globex"],
               "credentials": {"x": {"paths": ["/h/.config/globex/x.cfg"], "keeper": "k"}}, "positions": {}}}


class CredentialDenyTest(unittest.TestCase):
    def test_rules_deny_what_the_position_may_not_use(self):
        self.assertEqual(of_spawn.deny_rules("app_dev", "acme", CAT, named=False, roots=False), [])  # both layers off: nothing
        self.assertFalse(any(x.endswith("/**)") for x in of_spawn.deny_rules("app_dev", "acme", CAT, named=True, roots=False)))
        self.assertEqual([x for x in of_spawn.deny_rules("app_dev", "acme", CAT, named=False, roots=True) if "mt-deploy" in x], [])
        r = of_spawn.deny_rules("app_dev", "acme", CAT, named=True, roots=True)
        self.assertIn("Read(//h/.config/acme/grafana/viewer.token)", r)   # absolute: two slashes
        self.assertIn("Edit(//h/.ssh/mt-deploy)", r)                            # outside the roots, named
        self.assertIn("Read(//h/.config/globex/**)", r)                         # another organization's root
        self.assertNotIn("Read(//h/.config/acme/**)", r)                   # it uses a file there: the guard's
        self.assertFalse(any("pgpass" in x for x in r))                         # what it lists is never denied
        keeper = of_spawn.deny_rules("net_keeper", "acme", CAT, named=True, roots=True)
        self.assertNotIn("Read(//h/.ssh/mt-deploy)", keeper)                    # its keeper uses it
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
        self.assertNotIn("--settings", plan(cwd="/w")["command"])
        self.assertEqual(json.loads(of_spawn.session_settings(None, ["Read(//a)"])), {"permissions": {"deny": ["Read(//a)"]}})


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


if __name__ == "__main__":
    unittest.main()
