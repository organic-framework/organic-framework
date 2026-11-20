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
        self.assertEqual(box["filesystem"]["denyWrite"], box["filesystem"]["denyRead"])   # never written either
        self.assertIn(os.path.expanduser("~/.ssh"), box["filesystem"]["denyRead"])
        self.assertEqual(box["filesystem"]["allowWrite"], [os.path.expanduser("~/.local/state/of")])
        self.assertEqual((box["network"]["allowAllUnixSockets"], box["network"]["strictAllowlist"]), (True, True))
        self.assertIn("github.com", box["network"]["allowedDomains"])                # bypass mode: the list binds
        self.assertIn("/home/alex/.claude/projects", box["filesystem"]["denyRead"])   # transcripts
        self.assertIn("/home/alex/.claude/.credentials.json", box["filesystem"]["denyRead"])   # the account's sign-in
        self.assertEqual(s["permissions"]["deny"], deny)                            # the file tools' rules unchanged
        self.assertNotIn("sandbox", json.loads(of_spawn.session_settings(None, deny, platform="darwin")))

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
