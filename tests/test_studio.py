"""Unit tests for bin/of_model.py, bin/of_measures.py and bin/of_studio.py. Pure logic: fixtures only.

Run: python3 -m unittest discover -s tests
"""

import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))

import of_diagnose  # noqa: E402
import of_measures as M  # noqa: E402
import of_model  # noqa: E402
import of_studio  # noqa: E402

FA, Q0 = M.FA, M.QV0
of_measures_FA = M.FA

DOC = {
    "organization": "acme",
    "systems": {
        "trading": {"kind": "target"},
        "bot": {"kind": "target", "parent": "trading", "keeper": "bot_operator", "repos": ["arb"],
                "paths": ["arb/crates/bot/**"], "depends_on": [{"system": "k8s", "kind": "enabled_by"}]},
        "k8s": {"kind": "enabling", "keeper": "k8s_keeper", "repos": ["infra"]},
        "data": {"kind": "enabling", "keeper": "data_keeper"},
    },
    "positions": [
        {"name": "bot_operator", "system": "bot", "role": "operator", "kind": "keeper", "owner": "founder",
         "throughput": "sustain", "holder": {"session": "s-op"}},
        {"name": "bot_search", "system": "bot", "role": "search", "kind": "project", "owner": "bot_operator",
         "throughput": "raise", "holder": {"session": "s-search"}, "waiting_on": "owner"},
        {"name": "k8s_keeper", "system": "k8s", "role": "keeper", "kind": "keeper", "owner": "colleague",
         "throughput": "expense", "holder": {"session": "s-k8s"}},
        {"name": "colleague", "system": "k8s", "role": "owner", "kind": "owner", "owner": "founder",
         "holder": {"runtime": "human"}},
        {"name": "data_keeper", "system": "data", "role": "keeper", "kind": "keeper", "owner": "founder",
         "status": "parked"},
    ],
}


def ev(i, ts, name, sid, payload=None, position=None, cwd="~/acme", source="hook"):
    return {"event_id": f"e{i}", "ts": ts, "name": name, "session_id": sid, "position": position,
            "payload": payload or {}, "cwd": cwd, "source": source}


def prompt(i, ts, sid, text, kind="founder", cats=(), approval=None, reply_to=None, **kw):
    pl = {"origin": "human", "text": text, "classifications": {FA: {"kind": kind, "cats": list(cats), "approval": approval}}}
    if reply_to:
        pl["reply_to"] = reply_to
    return ev(i, ts, "of.human_prompt", sid, pl, **kw)


def question(i, ts, sid, q0=True):
    return ev(i, ts, "of.agent_question", sid, {"detected_by": "heuristic", "classifications": {Q0: q0, "q-v1": True},
                                                "questions": [{"question": "merge?"}]})


def judged(rows):
    """The judge's verdict after every heuristic question (bin/of_judge.py, founder's ask ddd6e3a3): a guessed question
    is a candidate shown to nobody until the judge confirms that the agent waits on its owner."""
    out = []
    for r in rows:
        out.append(r)
        if r["name"] == "of.agent_question" and r["payload"].get("detected_by") == "heuristic":
            out.append({"event_id": "j" + r["event_id"], "ts": r["ts"], "name": "of.agent_question_judged",
                        "session_id": r["session_id"], "position": r["position"], "cwd": r["cwd"], "source": "of",
                        "payload": {"ask_id": r["event_id"], "verdict": "owner_question",
                                    "question": r["payload"]["questions"][0]["question"]}})
    return out


class ModelTest(unittest.TestCase):
    def test_owner_chain_and_units(self):
        m = of_model.build(DOC, [], at="2026-11-05T00:00Z")
        p = m["positions"]
        self.assertEqual(p["bot_search"]["accountable"], "founder")      # via bot_operator, an agent
        self.assertEqual(p["k8s_keeper"]["accountable"], "colleague")    # a human owner below the founder
        self.assertEqual(p["colleague"]["accountable"], "founder")
        self.assertEqual(m["units"]["colleague"], ["k8s_keeper"])

    def test_coverage_flags(self):
        m = of_model.build(DOC, [], at="2026-11-05T00:00Z")
        cov = m["coverage"]["systems"]
        self.assertEqual(cov["trading"]["flags"], [])   # a supersystem whose every part has a keeper
        self.assertNotIn("orphan", cov["trading"]["flags"])      # a supersystem of bot
        doc = dict(DOC, systems=dict(DOC["systems"], web={"kind": "target", "parent": "trading"}))
        self.assertIn("no_keeper", of_model.build(doc, [], at="2026-11-05T00:00Z")["coverage"]["systems"]["trading"]["flags"])
        doc = dict(DOC, systems=dict(DOC["systems"], old={"kind": "target", "lifecycle": "unsupported", "repos": ["old"]},
                                     dana={"kind": "target", "governed_by": "external:dana"}))
        wr = [ev(i, "2026-11-04T10:00Z", "of.work_record", None, {"repo": "old", "systems": ["old"], "position": p})
              for i, p in ((1, "bot_search"), (2, "k8s_keeper"))]
        m = of_model.build(doc, wr, at="2026-11-05T00:00Z")
        self.assertEqual(m["coverage"]["systems"]["old"]["writers"], {"bot_search": 1, "k8s_keeper": 1})   # shown
        self.assertEqual((m["systems"]["old"]["lifecycle"], m["systems"]["bot"]["lifecycle"]), ("unsupported", "live"))
        cov = m["coverage"]["systems"]
        self.assertEqual((cov["old"]["flags"], cov["old"]["notes"]), ([], []))   # shown, not flagged
        self.assertEqual((cov["dana"]["flags"], cov["dana"]["notes"]), (["orphan"], ["kept by dana (external)"]))
        self.assertIn("keeper_parked", cov["data"]["flags"])
        self.assertEqual(cov["bot"]["homes"], ["bot_search"])
        self.assertEqual(cov["bot"]["flags"], [])

    def test_declared_dsm_and_logical_coupling(self):
        wr = [ev(1, "2026-11-04T10:00Z", "of.work_record", "s-search",
                 {"repo": "org/arb", "paths": ["crates/bot/src/a.rs"], "touched": [{"repo": "infra", "path": "k/x.yaml"}]}),
              ev(2, "2026-11-04T11:00Z", "of.work_record", "s-op", {"repo": "arb", "paths": ["crates/bot/b.rs"]})]
        m = of_model.build(DOC, wr, at="2026-11-05T00:00Z")
        self.assertEqual(m["system_dsm"][("bot", "k8s")]["declared"], "enabled_by")
        self.assertEqual(m["system_dsm"][("bot", "k8s")]["coupling"], 1)
        cov = m["coverage"]["systems"]["bot"]
        self.assertEqual(cov["writers"], {"bot_search": 1, "bot_operator": 1})
        self.assertIn("fragmented", cov["flags"])
        self.assertIn("non_keeper_writes", cov["flags"])
        self.assertEqual(of_model.cell_code(m["dmm"][("bot_search", "bot")]), "W1h")

    def test_imported_systems_are_used_as_given(self):
        wr = [ev(1, "2026-11-04T10:00Z", "of.work_record", "s-op", {"repo": "org/x", "systems": ["bot", "k8s", "gone"]}),
              ev(2, "2026-11-04T10:00Z", "of.work_record", "s-op", {"repo": "org/x", "systems": []})]
        m = of_model.build(DOC, wr, at="2026-11-05T00:00Z")
        self.assertEqual(m["system_dsm"][("bot", "k8s")]["coupling"], 1)
        self.assertEqual(m["coverage"]["systems"]["k8s"]["writers"], {"bot_operator": 1})
        self.assertEqual(m["unmapped_work_records"], 1)

    def test_work_on_a_system_that_is_not_live_is_not_orphan_work(self):
        doc = dict(DOC, systems=dict(DOC["systems"], old={"kind": "target", "lifecycle": "unsupported"},
                                     lonely={"kind": "target"}))
        chk = lambda i, sysid, verdict, ver: ev(i, "2026-11-04T10:00Z", "of.mandate_check", "s-op",
                                                {"verdict": verdict, "system": sysid, "router_version": ver, "tool_name": "Edit"})
        rows = [chk(1, "old", "unowned", "rt-v4"), chk(2, "old", "non_live:unsupported", "rt-v5"), chk(3, "lonely", "unowned", "rt-v5")]
        m = of_model.build(doc, rows, at="2026-11-05T00:00Z")
        self.assertEqual(set(m["orphan_work"]), {"lonely"})
        self.assertEqual((m["mandate"]["bot_operator"]["non_live"], m["mandate"]["bot_operator"]["unowned"]), (2, 1))

    def test_a_commit_without_systems_maps_by_its_paths(self):
        """of import git leaves systems [] while the map names no paths; the model maps the paths with the map at t."""
        c = ev(1, "2026-11-04T10:00Z", "of.work_record", None,
               {"source": "git", "unit": "commit", "repo": "arb", "sha": "abc", "position": "bot_search",
                "paths": ["crates/bot/src/a.rs"], "systems": []})
        m = of_model.build(DOC, [c], at="2026-11-05T00:00Z")
        self.assertEqual(m["coverage"]["systems"]["bot"]["writers"], {"bot_search": 1})
        self.assertEqual(m["unmapped_work_records"], 0)

    def test_pr_links_attribute_merged_prs(self):
        gh = ev(1, "2026-11-04T10:00Z", "of.work_record", None,
                {"source": "gh", "repo": "Org/arb", "number": 12, "author": "founder-gh", "systems": ["bot"]})
        link = ev(2, "2026-11-03T10:00Z", "of.work_record", "old-session",
                  {"source": "pr-link", "repo": "Org/arb", "number": 12, "url": "https://github.com/Org/arb/pull/12"})
        seen = ev(3, "2026-11-03T09:00Z", "of.human_prompt", "old-session", {}, position="bot_search")
        orphan_link = ev(4, "2026-11-03T10:00Z", "of.work_record", "old-session", {"source": "pr-link", "repo": "Org/x", "number": 9})
        m = of_model.build(DOC, [gh, link, seen, orphan_link], at="2026-11-05T00:00Z")
        self.assertEqual(m["work_records"]["total"], 1)          # pr-links are not work records
        self.assertEqual(m["work_records"]["attributed"], 1)
        self.assertEqual(m["work_records"]["unmapped"], 0)
        self.assertEqual(m["coverage"]["systems"]["bot"]["writers"], {"bot_search": 1})

    def test_bot_authors_carry_no_coupling(self):
        wr = [ev(1, "2026-11-04T10:00Z", "of.work_record", "s-op", {"author": "app/mergify", "systems": ["bot", "k8s"]}),
              ev(2, "2026-11-04T10:00Z", "of.work_record", "s-op", {"author": "dependabot[bot]", "systems": ["bot", "k8s"]}),
              ev(3, "2026-11-04T10:00Z", "of.work_record", "s-op", {"author": "founder-gh", "systems": ["bot", "k8s"]})]
        m = of_model.build(DOC, wr, at="2026-11-05T00:00Z")
        self.assertEqual(m["system_dsm"][("bot", "k8s")]["coupling"], 1)
        self.assertEqual(m["bot_work_records"], 2)

    def test_mandate_checks(self):
        mc = lambda i, v, sysid, tool="Edit", path=None: ev(i, "2026-11-04T10:00Z", "of.mandate_check", "s-search",
                                                            {"verdict": v, "system": sysid, "tool_name": tool, "path": path,
                                                             "router_version": "rt-v4"})
        rows = [mc(1, "in_mandate", "bot"), mc(2, "owned_by:k8s_keeper", "k8s", "Read"), mc(3, "owned_by:k8s_keeper", "k8s"),
                mc(4, "unowned", None, "Read", "~/acme/scratch/x.py"), mc(5, "unowned", "trading")]
        m = of_model.build(DOC, rows, at="2026-11-05T00:00Z")
        c = m["mandate"]["bot_search"]
        self.assertEqual((c["in_mandate"], c["owned_by"], c["unowned"], c["reads"], c["edits"]), (1, {"k8s_keeper": 2}, 2, 2, 3))
        self.assertEqual(m["orphan_work"], {"~/acme/scratch": {"bot_search": 1}, "trading": {"bot_search": 1}})
        self.assertIn("orphan_work", m["coverage"]["systems"]["trading"]["flags"])
        self.assertEqual(of_model.cell_code(m["dmm"][("bot_search", "k8s")]), "mr")
        self.assertEqual(m["position_dsm"][("bot_search", "k8s_keeper")]["reads"], 1)
        self.assertNotIn("scratch", of_model.snapshot(m))

    def test_wide_prs_and_runtime_basis(self):
        doc = {"systems": {"a": {"depends_on": [{"system": "b", "kind": "uses", "observed_by": "runtime"}]},
                           "b": {}, "c": {}, "d": {}, "e": {}}, "positions": []}
        wr = [ev(1, "2026-11-04T10:00Z", "of.work_record", None, {"author": "founder-gh", "systems": ["c", "d"]}),
              ev(2, "2026-11-04T10:00Z", "of.work_record", None, {"author": "founder-gh", "systems": ["c", "d", "e", "a"]})]
        m = of_model.build(doc, wr, at="2026-11-05T00:00Z")
        rows = {(r["a"], r["b"]): r for r in of_model.overlay(m)}
        self.assertEqual(rows[("a", "b")]["status"], "runtime_only")
        self.assertEqual((rows[("c", "d")]["coupling"], rows[("c", "d")]["wide"], rows[("c", "d")]["weighted"]), (1, 1, 1.33))
        self.assertEqual(rows[("c", "e")]["status"], "wide_only")      # only a 4-system PR touched both
        self.assertEqual({(r["a"], r["b"]): r for r in of_model.overlay(m, 4)}[("c", "e")]["status"], "undeclared")

    def test_mandate_checks_before_the_router_fix_are_discounted(self):
        mc = lambda i, ts, v, extra=None: ev(i, ts, "of.mandate_check", "s-search",
                                             dict({"verdict": v, "system": None, "tool_name": "Bash", "path": "~/acme"}, **(extra or {})))
        v2 = {"router_version": "rt-v2"}
        rows = [mc(1, "2026-11-04T08:00Z", "unowned"),                              # rt-v1: ignored
                mc(2, "2026-11-04T09:00Z", "unscoped", v2), mc(3, "2026-11-04T09:10Z", "unowned", v2),
                mc(4, "2026-11-03T09:00Z", "unowned", {"router_version": "rt-v3"})]
        built = of_model.build(DOC, rows, at="2026-11-05T00:00Z")
        c = built["mandate"]["bot_search"]
        self.assertEqual((c["unowned"], c["unscoped"]), (2, 1))
        self.assertEqual(built["orphan_work"], {})   # an orphan area needs rt-v4: earlier routers misjudged linked worktrees

    def test_overlay_statuses(self):
        wr = [ev(1, "2026-11-04T10:00Z", "of.work_record", "s-op", {"author": "founder-gh", "systems": ["bot", "k8s"]}),
              ev(2, "2026-11-04T10:00Z", "of.work_record", "s-op", {"author": "founder-gh", "systems": ["bot", "data"]}),
              ev(3, "2026-11-04T10:00Z", "of.work_record", "s-op", {"author": "founder-gh", "systems": ["data", "bot"]})]
        rows = {(r["a"], r["b"]): r for r in of_model.overlay(of_model.build(DOC, wr, at="2026-11-05T00:00Z"))}
        self.assertEqual((rows[("bot", "k8s")]["status"], rows[("bot", "k8s")]["ab"]), ("both", "enabled_by"))
        self.assertEqual((rows[("bot", "data")]["status"], rows[("bot", "data")]["coupling"]), ("undeclared", 2))

    def test_events_after_t_do_not_count(self):
        wr = [ev(1, "2026-11-06T10:00Z", "of.work_record", "s-op", {"repo": "arb", "paths": ["crates/bot/b.rs"]})]
        m = of_model.build(DOC, wr, at="2026-11-05T00:00Z")
        self.assertEqual(m["coverage"]["systems"]["bot"]["writers"], {})

    def test_position_dsm_decays(self):
        rows = [question(1, "2026-11-02T00:00Z", "s-search"), question(2, "2026-11-05T00:00Z", "s-search")]
        m = of_model.build(DOC, rows, at="2026-11-05T00:00Z")
        e = m["position_dsm"][("bot_search", "founder")]
        self.assertEqual(e["asks"], 2)
        self.assertAlmostEqual(e["weight"], 1.5, places=3)   # 1 + 2^(-72/72)

    def test_path_map_prefers_globs(self):
        pm = of_model.PathMap(DOC["systems"])
        self.assertEqual(pm.system("arb", "crates/bot/x.rs"), "bot")
        self.assertIsNone(pm.system("arb", "README.md"))      # the repo is only claimed by paths
        self.assertEqual(pm.system("infra", "any/file"), "k8s")

    def test_snapshot_is_stable_and_textless(self):
        m = of_model.build(DOC, [question(1, "2026-11-04T00:00Z", "s-search")], at="2026-11-05T00:00Z", rev="abc")
        s1, s2 = of_model.snapshot(m), of_model.snapshot(m)
        self.assertEqual(s1, s2)
        self.assertNotIn("merge?", s1)
        self.assertNotIn("s-search", s1)   # no session ids in organization snapshots


class MeasuresTest(unittest.TestCase):
    A, B = "2026-11-04T00:00Z", "2026-11-05T00:00Z"

    def rows(self):
        return [
            question(1, "2026-11-04T10:00Z", "s-search"),
            prompt(2, "2026-11-04T10:30Z", "s-search", "да", approval="short", reply_to="e1"),
            question(3, "2026-11-04T12:00Z", "s-op"),
            prompt(4, "2026-11-04T14:00Z", "s-op", "как дела?", cats=["status_poll"], reply_to="e3"),
            question(5, "2026-11-04T20:00Z", "s-search"),                 # open at the window's end
            question(6, "2026-11-04T09:00Z", "s-k8s"),                    # the colleague's, not the founder's
            prompt(7, "2026-11-04T15:00Z", "s-op", "Ты — новая сессия", kind="brief"),
            prompt(8, "2026-11-04T14:00:30Z", "s-search", "как дела?", cats=["status_poll"]),  # copy: same minute + text
        ]

    def test_little(self):
        m = of_model.build(DOC, [], at=self.B)
        o = M.owner(m, self.rows(), self.A, self.B)
        self.assertEqual(o["arrivals"], 3)
        self.assertAlmostEqual(o["lambda_per_h"], 3 / 24, places=3)
        # open time: 0.5 h + 2 h + 4 h (censored) = 6.5 h over 24 h
        self.assertAlmostEqual(o["L"], 6.5 / 24, places=3)
        self.assertAlmostEqual(o["W_h"], 6.5 / 3, places=3)
        self.assertAlmostEqual(o["residual"], 0.0, places=3)
        self.assertEqual(o["censored"], 1)
        self.assertEqual(o["answer_delay_min"]["n"], 2)
        self.assertEqual(o["answer_delay_min"]["p50"], 120.0)

    def test_concentration_plumbing_and_throughput(self):
        m = of_model.build(DOC, [], at=self.B)
        o = M.owner(m, self.rows(), self.A, self.B)
        self.assertEqual(o["owner_share_of_human_asks"], round(3 / 4, 3))
        self.assertEqual(o["owner_messages"], 2)       # brief excluded, copy deduplicated
        self.assertEqual(o["plumbing_share"], 0.5)
        self.assertEqual(o["throughput_share"]["share"], 1.0)   # raise + sustain

    def test_moved_on_is_not_an_answer(self):
        rows = [question(100, "2026-11-04T10:00Z", "s-op"),
                ev(101, "2026-11-04T10:30Z", "of.agent_message_in", "s-op", {"from": "k8s_keeper"}),
                ev(1011, "2026-11-04T10:30:30Z", "of.agent_message_out", "s-op", {"to_position": "k8s_keeper"}),
                question(102, "2026-11-04T12:00Z", "s-search"),
                prompt(103, "2026-11-04T12:10Z", "s-search", "да")]
        m = of_model.build(DOC, [], at=self.B)
        o = M.owner(m, rows, self.A, self.B)
        self.assertEqual(o["moved_on"], 1)
        self.assertEqual(o["answer_delay_min"]["n"], 1)          # only the founder's answer
        self.assertEqual(o["answer_delay_min"]["p50"], 10.0)
        self.assertAlmostEqual(o["L"], (0.5 + 30 / 3600 + 1 / 6) / 24, places=3)   # moved-on time still counts in L

    def test_annotation_at_the_same_instant_does_not_close_an_ask(self):
        q = question(140, "2026-11-04T10:00Z", "s-op")
        note = dict(ev(141, "2026-11-04T10:30:00.000Z", "of.paste_sourced", "s-op", {}), source="of")
        ans = dict(prompt(142, "2026-11-04T10:30:00.000Z", "s-op", "да"), source="import")
        m = of_model.build(DOC, [], at=self.B)
        for order in ([q, note, ans], [q, ans, note]):
            k = [k for k in M.asks_of(m, order) if k["event_id"] == "e140"][0]
            self.assertEqual((k["state"], k["closed_by"]), ("answered", "e142"))

    def test_a_question_stays_after_its_own_turn_end(self):
        te = ev(150, "2026-11-04T10:00Z", "of.turn_end", "s-op", {})
        q = question(151, "2026-11-04T10:00Z", "s-op")              # the same instant, written after the turn end
        ans = prompt(152, "2026-11-04T10:20Z", "s-op", "да")
        k = [k for k in M.asks_of(of_model.build(DOC, [], at=self.B), [te, q, ans]) if k["event_id"] == "e151"][0]
        self.assertEqual(k["state"], "answered")

    def test_carry_in_and_in_degree(self):
        rows = [question(110, "2026-11-03T22:00Z", "s-op"), prompt(111, "2026-11-04T02:00Z", "s-op", "ok"),
                ev(112, "2026-11-04T05:00Z", "of.agent_message_in", "s-op", {"text": "Можешь глянуть PR?", "classifications": {"rq-v0": True}}),
                ev(113, "2026-11-04T05:01Z", "of.agent_message_in", "s-op", {"text": "FYI: deployed", "classifications": {"rq-v0": True}}),
                ev(115, "2026-11-04T05:02Z", "of.agent_message_in", "s-op", {"text": "heads-up, done"}),
                question(114, "2026-11-04T09:00Z", "s-search")]
        m = of_model.build(DOC, [], at=self.B)
        o = M.owner(m, rows, self.A, self.B)
        self.assertEqual((o["carry_in"], o["carry_in_L"]), (1, round(2 / 24, 3)))
        self.assertEqual(o["peer_requests"], 2)                          # the two labelled rq-v0
        self.assertEqual(o["inbound_concentration"], round(1 / 3, 3))   # 1 ask in window, 2 peer requests

    def test_polls_vs_studio(self):
        m = of_model.build(DOC, [], at=self.B)
        views = [ev(90, "2026-11-04T09:00Z", "of.studio_viewed", None, {"tenant": True, "view": "inbox"}),
                 ev(91, "2026-11-04T09:05Z", "of.studio_viewed", None, {"tenant": False, "scope": "unit", "owner": "colleague"})]
        o = M.owner(m, self.rows() + views, self.A, self.B)
        pv = o["polls_vs_studio"]
        self.assertEqual((pv["polls"], pv["views"], pv["recorded"]), (1, 1, True))
        self.assertEqual(pv["days"]["2026-11-04"], {"polls": 1, "views": 1})
        oc = M.owner(m, self.rows() + views, self.A, self.B, who="colleague")
        self.assertEqual(oc["polls_vs_studio"]["views"], 1)
        agent = ev(92, "2026-11-04T10:00Z", "of.studio_viewed", None, {"tenant": True, "view": "works", "requester": "agent:of_server"})
        self.assertEqual(M.owner(m, self.rows() + views + [agent], self.A, self.B)["polls_vs_studio"]["views"], 1)

    def test_colleague_sees_own_asks(self):
        m = of_model.build(DOC, [], at=self.B)
        o = M.owner(m, self.rows(), self.A, self.B, who="colleague")
        self.assertEqual(o["arrivals"], 1)

    def test_dead_end(self):
        m = of_model.build(DOC, [], at="2026-11-06T00:00Z")
        o = M.owner(m, self.rows(), "2026-11-05T00:00Z", "2026-11-06T00:00Z")
        self.assertIn("e5", o["dead_end_asks"])     # open 28 h at the end
        self.assertNotIn("e1", o["dead_end_asks"])

    def test_gate_pairs_only_pinned_questions(self):
        rows = self.rows() + [question(9, "2026-11-04T16:00Z", "s-op", q0=False),
                              prompt(10, "2026-11-04T16:05Z", "s-op", "ок", approval="short", reply_to="e9")]
        m = of_model.build(DOC, [], at=self.B)
        g = M.gate(m, rows, [], self.A, self.B)
        self.assertEqual(g["founder_messages_claude"], 3)
        self.assertEqual(g["answers_to_agent_asks"], 2)        # e9 is q-v1 only
        self.assertEqual(g["bare_yes_answers"], 1)
        self.assertEqual(g["short_approvals"], 2)
        self.assertEqual(g["answer_delay_min"]["p50"], 120.0)  # baseline quantile: element floor(0.5·2) = the larger
        self.assertEqual(g["agent_hours_waiting_on_asks"], 2.5)

    def test_gate_pairs_only_the_last_turn(self):
        # an ask, then a later turn (a reply to a peer), then the founder: not an answer to the ask (baseline §2.6)
        rows = [question(80, "2026-11-04T10:00Z", "s-op"), ev(81, "2026-11-04T10:00:01Z", "of.turn_end", "s-op", {}),
                ev(82, "2026-11-04T10:20Z", "of.turn_end", "s-op", {}),
                prompt(83, "2026-11-04T11:00Z", "s-op", "да", approval="short", reply_to="e80")]
        g = M.gate(of_model.build(DOC, [], at=self.B), rows, [], self.A, self.B)
        self.assertEqual(g["answers_to_agent_asks"], 0)
        g = M.gate(of_model.build(DOC, [], at=self.B), rows[:2] + rows[3:], [], self.A, self.B)
        self.assertEqual(g["answers_to_agent_asks"], 1)

    def test_relay_delivery_log_is_not_a_received_message(self):
        hook = dict(ev(130, "2026-11-04T10:00Z", "of.agent_message_in", "s-op", {"text": "hi", "classifications": {"rq-v0": True}}), source="hook")
        log = dict(hook, event_id="e131", source="of")
        o = M.owner(of_model.build(DOC, [], at=self.B), self.rows() + [hook, log], self.A, self.B)
        self.assertEqual(o["peer_requests"], 1)

    def test_gate_scope_excludes_sessions(self):
        rows = self.rows() + [prompt(11, "2026-11-04T17:00Z", "s-of", "x", cwd="~/organic-framework")]
        m = of_model.build(DOC, [], at=self.B)
        self.assertEqual(M.gate(m, rows, [], self.A, self.B, exclude_cwd=["~/organic-framework"])["founder_messages_claude"], 2)

    def test_concurrency(self):
        idx = [{"session_id": "a", "type": "assistant", "timestamp": "2026-11-04T00:01:00Z"},
               {"session_id": "b", "type": "assistant", "timestamp": "2026-11-04T00:03:00Z"},
               {"session_id": "a", "type": "assistant", "timestamp": "2026-11-04T00:07:00Z"},
               {"session_id": "a", "type": "user", "timestamp": "2026-11-04T00:12:00Z"}]
        c = M.concurrency(idx, of_model.epoch("2026-11-04T00:00Z"), of_model.epoch("2026-11-04T00:20Z"))
        self.assertEqual(c, {"mean": 0.8, "p90": 2, "peak": 2})   # buckets 2, 1, 0, 0

    def test_waits_by_cause(self):
        rows = self.rows() + [ev(20, "2026-11-04T01:00Z", "of.state_declared", "s-op", {"state": "suspended"}),
                              ev(21, "2026-11-04T01:30Z", "of.state_declared", "s-op", {"state": "resumed"})]
        m = of_model.build(DOC, [], at=self.B)
        w = M.waits(m, rows, self.A, self.B)
        self.assertEqual(w["grid"]["bot_operator"]["2026-11-04T01:00:00Z"]["suspended"], 30.0)
        self.assertEqual(w["grid"]["bot_search"]["2026-11-04T10:00:00Z"]["owner"], 30.0)
        self.assertEqual(w["grid"]["bot_operator"]["2026-11-04T12:00:00Z"]["owner"], 60.0)

    def test_inbound_queues(self):
        def q(i, ts, pos, depth, oldest=None, org="acme"):
            return ev(i, ts, "of.inbound_queue", None, {"org": org, "position": pos, "depth": depth,
                                                        "oldest_created_at": oldest}, position=pos)
        rows = [q(40, "2026-11-03T23:00Z", "k8s_keeper", 1, "2026-11-03T22:50Z"),   # carries into the window
                q(41, "2026-11-04T06:00Z", "k8s_keeper", 3, "2026-11-03T22:50Z"),
                q(42, "2026-11-04T12:00Z", "k8s_keeper", 0),
                q(43, "2026-11-04T23:00Z", "bot_operator", 2, "2026-11-04T22:00Z"),
                q(44, "2026-11-04T10:00Z", "bot_operator", 9, "2026-11-04T10:00Z", org="other")]   # not this org
        m = of_model.build(DOC, [], at=self.B, org="acme")
        out = M.inbound_queues(m, rows, self.A, self.B)
        k = out["k8s_keeper"]
        self.assertEqual((k["depth_now"], k["depth_max"], k["oldest_min_now"]), (0, 3, None))
        self.assertEqual(k["depth_avg"], round((1 * 6 + 3 * 6) / 24, 2))
        self.assertEqual(k["backlog_min"], 12 * 60.0)   # the oldest was over 30 min old from 00:00 to 12:00
        o = out["bot_operator"]
        self.assertEqual((o["depth_now"], o["depth_max"], o["oldest_min_now"], o["backlog_min"]), (2, 2, 120.0, 60.0))
        self.assertEqual(o["observed_from"], "2026-11-04T23:00:00Z")
        self.assertEqual(o["depth_avg"], 2.0)

    def test_codex_prompts_counted_apart(self):
        cx = dict(prompt(30, "2026-11-04T18:00Z", "c1", "сделай"), harness="codex")
        cx2 = dict(prompt(31, "2026-11-04T18:00:20Z", "c1", "сделай"), harness="codex")   # a copy: same minute and text
        m = of_model.build(DOC, [], at=self.B)
        g = M.gate(m, self.rows() + [cx, cx2], [], self.A, self.B)
        self.assertEqual(g["founder_messages_codex"], 1)
        self.assertEqual(g["founder_messages_claude"], 2)
        self.assertIn("codex", M.present(self.rows() + [cx], [], of_model.epoch(self.A), of_model.epoch(self.B)))

    def test_present_marks_missing_inputs(self):
        rows = [ev(1, "2026-11-04T10:00Z", "of.work_record", "s-op", {"author": "founder-gh"})]
        have = M.present(rows, [], of_model.epoch(self.A), of_model.epoch(self.B))
        self.assertEqual(have, {"of.work_record"})
        self.assertEqual([n for n in M.NEEDS["answer_delay_min"] if n not in have], ["of.human_prompt", "of.agent_question"])
        self.assertEqual([n for n in M.NEEDS["agent_prs_merged"] if n not in have], ["agent_authors"])   # the baseline names them

    def test_gate_counts_the_baseline_agent_authors(self):
        pr = lambda i, n, who: ev(i, "2026-11-04T10:00Z", "of.work_record", None,
                                  {"source": "gh", "repo": "o/shop", "number": n, "author": who, "merged_at": "2026-11-04T10:00Z"})
        rows = self.rows() + [pr(50, 1, "founder-gh"), pr(51, 1, "founder-gh"), pr(52, 2, "dana"), pr(53, 3, "founder-gh")]
        m = of_model.build(DOC, [], at=self.B)
        self.assertEqual(M.gate(m, rows, [], self.A, self.B)["agent_prs_merged"], 0)             # no authors named: none
        self.assertEqual(M.gate(m, rows, [], self.A, self.B, agent_authors=("founder-gh",))["agent_prs_merged"], 2)

    def test_overlapping_sources_count_once(self):
        # an ask 10:00-10:30 and a pulse that saw the same wait: 30 minutes, not 60
        pulse = ev(40, "2026-11-04T10:30Z", "of.pulse", None, {"positions": [
            {"position": "bot_search", "kind": "waiting_owner", "since": "2026-11-04T10:00Z"}]})
        w = M.waits(of_model.build(DOC, [], at=self.B), self.rows()[:2] + [pulse], self.A, self.B)
        self.assertEqual(w["grid"]["bot_search"]["2026-11-04T10:00:00Z"], {"owner": 30.0})
        self.assertEqual(w["totals"]["bot_search"]["owner"], 30.0)

    def test_owner_outranks_peer_and_stall_ends_at_life(self):
        rows = [ev(41, "2026-11-04T05:00Z", "of.state_declared", "s-op", {"position": "bot_operator", "state": "waiting",
                                                                         "waiting_on": "resource:runner"}),
                ev(42, "2026-11-04T05:10Z", "of.attention", None, {"notification_type": "herdr_blocked", "position": "bot_operator"}),
                ev(43, "2026-11-04T05:20Z", "of.turn_end", "s-op", {}),
                ev(44, "2026-11-04T05:40Z", "of.state_declared", "s-op", {"position": "bot_operator", "state": "working"}),
                ev(45, "2026-11-04T07:00Z", "of.stall_detected", None, {"position": "k8s_keeper", "idle_since": "2026-11-04T06:30Z"}),
                ev(46, "2026-11-04T07:15Z", "of.turn_end", "s-k8s", {})]
        w = M.waits(of_model.build(DOC, [], at=self.B), rows, self.A, self.B)
        self.assertEqual(w["grid"]["bot_operator"]["2026-11-04T05:00:00Z"], {"capacity:runner": 30.0, "owner": 10.0})
        self.assertEqual(w["totals"]["k8s_keeper"], {"stalled": 45.0})

    def test_wait_intervals_shape(self):
        ivs = M.wait_intervals(of_model.build(DOC, [], at=self.B), self.rows(), of_model.epoch(self.A), of_model.epoch(self.B))
        ask = next(x for x in ivs if x["source_event"] == "e1")
        self.assertEqual((ask["position"], ask["cause"], ask["session_id"]), ("bot_search", "owner", "s-search"))
        self.assertIsNotNone(ask["to"])
        self.assertIsNone(next(x for x in ivs if x["source_event"] == "e5")["to"])   # still open at the window's end

    def test_declared_peer_wait_in_heatmap(self):
        rows = [ev(30, "2026-11-04T03:00Z", "of.state_declared", "s-op", {"position": "bot_operator", "state": "waiting", "waiting_on": "k8s_keeper"}),
                ev(31, "2026-11-04T03:20Z", "of.state_declared", "s-op", {"position": "bot_operator", "state": "working"})]
        w = M.waits(of_model.build(DOC, [], at=self.B), rows, self.A, self.B)
        self.assertEqual(w["grid"]["bot_operator"]["2026-11-04T03:00:00Z"]["peer:k8s_keeper"], 20.0)

    def test_check(self):
        targets = {"observations": {
            "plumbing_share": {"W7": 0.248, "tol": {"abs": 0.01}},
            "answer_delay_min": {"W7": {"p50": 2.4, "p90": 43.8}, "tol": {"rel": 0.1, "abs": 0.5}},
            "concurrency_5min": {"W7": {"mean": 2.7, "peak": 27}, "tol": {"mean": {"abs": 0.3}, "peak": {"abs": 2}}}}}
        obs = {"plumbing_share": 0.257, "answer_delay_min": {"p50": 2.8, "p90": 49.0},
               "concurrency_5min": {"mean": 3.1, "peak": 26}}
        res = {(k, s): ok for k, s, _, _, _, ok in M.check(obs, targets, "W7")}
        self.assertTrue(res[("plumbing_share", None)])
        self.assertTrue(res[("answer_delay_min", "p50")])     # within 0.5 min
        self.assertFalse(res[("answer_delay_min", "p90")])    # 5.2 > 4.38
        self.assertFalse(res[("concurrency_5min", "mean")])
        self.assertTrue(res[("concurrency_5min", "peak")])


class AttentionTest(unittest.TestCase):
    NOW = "2026-11-05T12:00Z"

    def att(self, rows):
        m = of_model.build(DOC, [], at=self.NOW)
        return {x["position"]: x for x in M.attention(m, rows, self.NOW)}

    def test_one_row_per_position_across_sources(self):
        rows = judged([question(1, "2026-11-05T10:00Z", "s-search")]) + [
                ev(2, "2026-11-05T09:00Z", "of.agent_question", "s-search",
                   {"source": "declared", "topic_key": "merge-12", "from_position": "bot_search", "to": "founder",
                    "questions": [{"question": "merge #12?"}]})]
        rows.sort(key=lambda r: r["ts"])
        a = self.att(rows)
        x = a["bot_search"]   # also declared waiting_on: owner in the registry
        self.assertEqual(x["kind"], "waiting_owner")
        self.assertEqual(x["since"], "2026-11-05T09:00Z")
        self.assertEqual(sorted(x["sources"]), ["of ask", "question (heuristic)", "registry"])
        self.assertEqual(x["text"], "merge #12?")

    def test_closed_declared_ask_drops_out(self):
        rows = [ev(1, "2026-11-05T09:00Z", "of.agent_question", None,
                   {"source": "declared", "topic_key": "t1", "from_position": "bot_operator", "to": "founder"}),
                ev(2, "2026-11-05T09:30Z", "of.ask_withdrawn", None, {"topic_key": "t1"})]
        self.assertNotIn("bot_operator", self.att(rows))
        rows[1] = ev(2, "2026-11-05T09:30Z", "of.agent_question_answered", None, {"ask_id": "e1"})
        self.assertNotIn("bot_operator", self.att(rows))

    def test_declared_asks_close_by_ids_status_or_prompt(self):
        q = lambda i, topic, sid=None: ev(i, "2026-11-05T09:00Z", "of.agent_question", sid,
                                          {"detected_by": "declared", "source": "declared", "topic_key": topic,
                                           "from_position": "bot_operator", "to": "founder"})
        self.assertNotIn("bot_operator", self.att([q(1, "a"), q(2, "b"),
                                                   ev(3, "2026-11-05T09:10Z", "of.agent_question_answered", None, {"ask_ids": ["e1", "e2"]})]))
        self.assertNotIn("bot_operator", self.att([q(1, "a"), ev(3, "2026-11-05T09:10Z", "of.state_declared", None,
                                                                 {"position": "bot_operator", "state": "working"})]))
        self.assertNotIn("bot_operator", self.att([q(1, "a", sid="s-op"), prompt(3, "2026-11-05T09:10Z", "s-op", "да")]))

    def test_herdr_blocked_is_waiting_on_owner_until_life(self):
        b = ev(1, "2026-11-05T11:00Z", "of.attention", None, {"notification_type": "herdr_blocked", "position": "bot_operator"})
        self.assertEqual(self.att([b])["bot_operator"]["sources"], ["herdr blocked"])
        self.assertNotIn("bot_operator", self.att([b, ev(2, "2026-11-05T11:05Z", "of.turn_end", "s-op", {})]))

    def test_same_topic_asked_twice_is_one_row(self):
        q = lambda i, ts: ev(i, ts, "of.agent_question", None, {"source": "declared", "topic_key": "t1",
                                                                "from_position": "bot_operator", "to": "founder"})
        a = self.att([q(1, "2026-11-05T09:00Z"), q(2, "2026-11-05T10:00Z")])
        self.assertEqual(a["bot_operator"]["sources"], ["of ask"])

    def test_stall_from_pulse_unless_a_reason_is_declared(self):
        rows = [ev(1, "2026-11-05T11:50Z", "of.stall_detected", None, {"position": "bot_operator", "idle_since": "2026-11-05T10:00Z"}),
                ev(2, "2026-11-05T11:51Z", "of.nudge_sent", None, {"position": "bot_operator"}),
                ev(3, "2026-11-05T11:50Z", "of.stall_detected", None, {"position": "k8s_keeper", "idle_since": "2026-11-05T11:00Z"}),
                ev(4, "2026-11-05T11:55Z", "of.state_declared", "s-k8s",
                   {"position": "k8s_keeper", "state": "idle", "reason": "waiting for the maintenance window"}),
                ev(5, "2026-11-05T11:50Z", "of.stall_detected", None, {"position": "colleague"}),
                ev(6, "2026-11-05T11:58Z", "of.turn_end", None, {}, position="colleague")]   # shows life again
        a = self.att(rows)
        self.assertEqual(a["bot_operator"]["kind"], "stalled")
        self.assertEqual(a["bot_operator"]["text"], "nudged 1×")
        self.assertNotIn("k8s_keeper", a)
        self.assertNotIn("colleague", a)

    def test_who_waits_on_whom(self):
        st = ev(1, "2026-11-05T11:00Z", "of.state_declared", "s-op",
                {"position": "bot_operator", "state": "waiting", "waiting_on": "k8s_keeper", "reason": "node pool"})
        ext = ev(2, "2026-11-05T11:00Z", "of.state_declared", "s-k8s",
                 {"position": "k8s_keeper", "state": "waiting", "waiting_on": "external:aws-support"})
        a = self.att([st, ext])
        self.assertEqual((a["bot_operator"]["kind"], a["bot_operator"]["on"]), ("waiting_peer", "k8s_keeper"))
        self.assertEqual(a["k8s_keeper"]["kind"], "waiting_external")

    def test_waiting_on_the_owner_ranks_first(self):
        st = ev(1, "2026-11-05T08:00Z", "of.state_declared", "s-op",
                {"position": "bot_operator", "state": "waiting", "waiting_on": "k8s_keeper"})
        order = [x["position"] for x in M.attention(of_model.build(DOC, [], at=self.NOW), [st], self.NOW)]
        self.assertEqual(order, ["bot_search", "bot_operator"])


class DiagnoseTest(unittest.TestCase):
    A, B = "2026-10-29T00:00Z", "2026-11-05T00:00Z"
    DOC2 = {"systems": {"bot": {"keeper": "bot_operator", "depends_on": [{"system": "k8s", "kind": "enabled_by"}]},
                        "k8s": {"keeper": "k8s_keeper"}, "lonely": {}},
            "positions": [{"name": "bot_operator", "system": "bot", "holder": {"session": "s-op"}},
                          {"name": "k8s_keeper", "system": "k8s", "holder": {"session": "s-k8s"}}]}

    def wr(self, i, systems, author="founder-gh"):
        return ev(i, "2026-11-03T10:00Z", "of.work_record", None, {"author": author, "systems": systems})

    def active(self, sid, days=("2026-10-30", "2026-11-01", "2026-11-03"), name="of.turn_end"):
        return [dict(ev(f"{sid}{d}", f"{d}T10:00Z", name, sid, {}), source="hook") for d in days]

    def diag(self, rows):
        m = of_model.build(self.DOC2, rows, at=self.B)
        return of_diagnose.diagnose(m, rows, self.A, self.B)

    def status(self, d, rule):
        return next(r for r in d["rules"] if r["rule"] == "ap." + rule)

    def test_missing_inputs_block_and_unwritten_rules_say_so(self):
        d = self.diag([self.wr(1, ["bot"])])
        self.assertEqual(self.status(d, "over-coupled")["status"], "blocked")
        self.assertIn("wr.position", self.status(d, "over-coupled")["missing"])
        self.assertEqual(self.status(d, "lingering")["missing"], ["registry.mandate_exit"])
        self.assertIn(self.status(d, "chatty")["status"], ("blocked", "not_built"))

    def test_labels(self):
        rows = [self.wr(i, []) for i in range(5)]   # orphan-work's unmapped part (keeperless moved to unkept-system, 2026-11-10)
        iid = of_diagnose.issue_id("orphan-work", ["(unmapped)"])
        ver = next(r["version"] for r in self.diag(rows)["rules"] if r["rule"] == "ap.orphan-work")
        lab = lambda label, v=ver: ev(60, "2026-11-04T10:00Z", "of.issue_labelled", None,
                                      {"issue_id": iid, "rule_version": v, "label": label})
        state = lambda rows: next(i for i in self.diag(rows)["issues"] if i["issue_id"] == iid)["state"]
        self.assertEqual(state(rows), "open")
        self.assertEqual(state(rows + [lab("false_positive")]), "dismissed")
        self.assertEqual(state(rows + [lab("false_positive", "ap.orphan-work@-1")]), "open")   # a new version is judged anew
        self.assertEqual(state(rows + [lab("false_positive", "ap.orphan-work@0")]), "dismissed")  # labels_carry_from: [0]
        self.assertEqual(state(rows + [lab("true_but_not_now")]), "snoozed")

    def test_parts_run_when_their_inputs_exist(self):
        d = self.diag([self.wr(i, ["lonely"]) for i in range(5)] + [self.wr(9, [])])
        st = self.status(d, "orphan-work")
        self.assertEqual(st["status"], "fired")
        self.assertEqual(sorted(st["parts_blocked"]), ["cluster"])          # no mandate_check yet
        parts = {i["part"]: i for i in d["issues"] if i["rule"] == "ap.orphan-work"}
        self.assertEqual(parts["unmapped"]["decider"], "structure_keeper")
        self.assertEqual(parts["unmapped"]["score_terms"]["m_decide"], 3)
        self.assertNotIn("keeperless", parts)   # orphan-work@3: moved to unkept-system, the structure keeper's
        self.assertEqual(next(i for i in d["issues"] if i["rule"] == "ap.unkept-system")["decider"], "structure_keeper")

    def test_functions_match_the_cli(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("of_cli_fn", os.path.join(os.path.dirname(__file__), "..", "bin", "of"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_fn", loader))
        loader.exec_module(mod)
        self.assertEqual(tuple(mod.MANAGEMENT_FUNCTIONS), of_model.MANAGEMENT_FUNCTIONS)

    def owner_doc(self):
        return {"systems": {"mt": {"kind": "creating", "keeper": "acme_org"},
                            "bot": {"keeper": "bot_operator"}, "k8s": {"keeper": "k8s_keeper"}},
                "positions": [{"name": n, "system": s, "holder": {"session": f"s-{n}"}}
                              for n, s in (("bot_operator", "bot"), ("k8s_keeper", "k8s"), ("acme_org", "mt"),
                                           ("bot_search", "bot"), ("bot_nonce", "bot"))]}

    def test_sender_from_the_matching_out_message(self):
        doc = self.owner_doc()
        out = ev(500, "2026-11-03T10:00:00Z", "of.agent_message_out", "s-bot_search", {"text": "Owner's yes to deploy  the pool", "to": "x"})
        inn = ev(501, "2026-11-03T10:00:03Z", "of.agent_message_in", "s-bot_nonce",
                 {"text": '<cross-session-message from="uds:/tmp/9.sock" from-name="pool-rfc">Owner\'s yes to deploy the pool'})
        late = ev(502, "2026-11-03T11:00:00Z", "of.agent_message_in", "s-bot_nonce",
                  {"text": '<cross-session-message from="uds:/tmp/9.sock" from-name="pool-rfc">Owner\'s yes to deploy the pool'})
        m = of_model.build(doc, [out, inn, late], at=self.B)
        got = [(r["event_id"], p, how) for r, p, _, how in of_model.message_senders(m, [out, inn, late], of_model.epoch(self.A), of_model.epoch(self.B))]
        self.assertEqual(got, [("e501", "bot_search", "out"), ("e502", None, None)])   # outside ±5 s, no alias
        sub = ev(503, "2026-11-03T12:00:00Z", "of.agent_message_in", "s-bot_nonce",
                 {"text": '<agent-message from="a3b3994c9dab0352b">done'})
        rows = [out, inn, late, sub]
        self.assertEqual(of_model.unresolved_senders(m, rows, of_model.epoch(self.A), of_model.epoch(self.B)),
                         {"pool-rfc": 1})                                           # the subagent is left out

    def test_registry_unconfirmed(self):
        doc = {"systems": {"lonely": {"confirm": True, "doubt": "one system or two?"},
                           "c": {"keeper": "bot_operator", "confirm": ["keeper"]}, "g": {"keeper": "bot_operator", "confirm": ["grain"]}},
               "positions": [{"name": "bot_operator", "system": "c", "holder": {"session": "s-op"}}]}
        m = of_model.build(doc, [], at=self.B)
        cat = of_diagnose.catalogue()
        mk = lambda aff: of_diagnose.mark_unconfirmed(m, of_diagnose.issue(cat, "orphan-work", aff, {}, "x", part="keeperless"))["evidence"]
        self.assertEqual(mk(["lonely"]), {"registry_unconfirmed": True, "unconfirmed": {"lonely": "one system or two?"}})
        self.assertTrue(mk(["c"])["keeper_unconfirmed"])          # confirm lists keeper
        self.assertNotIn("keeper_unconfirmed", mk(["g"]))          # grain only
        self.assertEqual(mk(["c", "g"])["unconfirmed_what"], ["grain", "keeper"])

    def test_inbound_backlog_merges_into_the_broker(self):
        cat = of_diagnose.catalogue()
        broker = of_diagnose.issue(cat, "bottleneck-broker", ["k8s_keeper"], {"inbound_share": 0.4}, "x")
        ib = of_diagnose.issue(cat, "inbound-backlog", ["k8s_keeper"], {"backlog_h": 6.0}, "y", part="overload")
        other = of_diagnose.issue(cat, "inbound-backlog", ["bot_operator"], {"backlog_h": 3.0}, "z", part="overload")
        status = [{"rule": "ap.inbound-backlog", "issues": 2, "status": "fired"}]
        out = of_diagnose.merge_into(cat, [broker, ib, other], status)
        self.assertEqual([i["rule"] for i in out], ["ap.bottleneck-broker", "ap.inbound-backlog"])
        self.assertEqual(out[0]["evidence"]["merged"]["ap.inbound-backlog@0"], {"backlog_h": 6.0, "part": "overload"})
        self.assertEqual((status[0]["issues"], status[0]["status"]), (1, "fired"))

    def test_kinds_needed_cover_the_catalogue(self):
        k = of_diagnose.kinds_needed()
        for n in ("of.work_record", "of.agent_message_out", "of.issue_labelled", "of.compaction"):
            self.assertIn(n, k)
        k = of_diagnose.kinds_needed({"rules": {"r": {"needs": ["of.a"], "reads": ["of.b", "not-a-kind"]},
                                                "q": {"needs": {"x": ["of.c"]}, "reads": ["of.d"]}}})
        self.assertTrue({"of.a", "of.b", "of.c", "of.d"} <= set(k))                     # `reads` load too
        self.assertNotIn("not-a-kind", k)


class DelegationTest(unittest.TestCase):
    NOW = "2026-11-05T00:00Z"
    DOC = {"systems": {"mt": {"kind": "creating", "keeper": "acme_org"}, "k8s": {"keeper": "k8s_keeper"},
                       "bot": {"keeper": "bot_operator"}},
           "units": {"platform": {"owner": "plat_owner", "systems": ["k8s"]}, "trading": {"owner": "founder_pos", "systems": ["bot"]}},
           "delegations": [{"id": "del-1", "unit": "platform", "systems": ["k8s"], "positions": ["k8s_keeper"],
                            "from_owner": "founder_pos", "to_owner": "plat_owner", "review": "2026-12-01",
                            "reporting": {"escalate_when": ["ask.blocking", "issue.severity >= high", "budget_used > 80%"]}}],
           "positions": [{"name": "k8s_keeper", "system": "k8s", "holder": {"session": "s-k8s"}},
                         {"name": "plat_owner", "system": "k8s", "holder": {"runtime": "human"}},
                         {"name": "bot_operator", "system": "bot", "holder": {"session": "s-op"}},
                         {"name": "acme_org", "system": "mt"}]}

    def test_asks_route_to_the_unit_owner(self):
        m = of_model.build(self.DOC, [], at=self.NOW)
        self.assertEqual(m["positions"]["k8s_keeper"]["accountable"], "plat_owner")
        self.assertEqual(m["positions"]["k8s_keeper"]["delegation"], "del-1")
        self.assertEqual(m["positions"]["bot_operator"]["accountable"], "founder")   # trading is not delegated
        rows = [question(1, "2026-11-04T10:00Z", "s-k8s"), question(2, "2026-11-04T10:00Z", "s-op")]
        self.assertEqual([k["position"] for k in M.asks_of(m, rows)], ["bot_operator"])
        self.assertEqual([k["position"] for k in M.asks_of(m, rows, who="plat_owner")], ["k8s_keeper"])

    def test_delegation_takes_effect_when_applied(self):
        doc = dict(self.DOC, delegations=[dict(self.DOC["delegations"][0], diff="sd-1")])
        m = of_model.build(doc, [], at=self.NOW)
        applied = ev(5, "2026-11-04T12:00Z", "of.diff_applied", None, {"id": "sd-1", "kind": "delegate"})
        rows = [question(1, "2026-11-04T10:00Z", "s-k8s"), applied, question(2, "2026-11-04T14:00Z", "s-k8s")]
        self.assertEqual([k["event_id"] for k in M.asks_of(m, rows)], ["e1"])                    # before: the founder's
        self.assertEqual([k["event_id"] for k in M.asks_of(m, rows, who="plat_owner")], ["e2"])   # after: the unit owner's

    def test_delegation_review_measures(self):
        doc = dict(self.DOC, delegations=[dict(self.DOC["delegations"][0], diff="sd-1")])
        m = of_model.build(doc, [], at=self.NOW)
        rows = [question(1, "2026-11-04T10:00Z", "s-k8s"), prompt(2, "2026-11-04T10:05Z", "s-k8s", "да"),
                ev(3, "2026-11-04T12:00Z", "of.diff_applied", None, {"id": "sd-1", "kind": "delegate"}),
                question(4, "2026-11-04T14:00Z", "s-k8s"), question(5, "2026-11-04T15:00Z", "s-op")]
        rec = doc["delegations"][0]
        before = M.delegation_review(m, rows, "2026-11-04T00:00Z", "2026-11-04T12:00Z", rec)
        after = M.delegation_review(m, rows, "2026-11-04T12:00Z", "2026-11-05T00:00Z", rec)
        self.assertEqual((before["founder"]["asks"], before["founder"]["founder_messages_in_unit"]), (1, 1))
        self.assertEqual(before["founder"]["answer_delay_min"]["p50"], 5.0)
        self.assertEqual((after["founder"]["asks"], after["unit_owner_side"]["asks"]), (0, 1))
        self.assertEqual(after["founder"]["unit_share_of_founder_asks"], 0.0)   # the founder's one ask is bot_operator's
        self.assertEqual(M.dotted(after, "unit_owner_side.asks"), 1)
        self.assertEqual(after["founder"]["founder_asks_total"], 1)

    def test_delegation_series_matches_the_review_at_each_point(self):
        doc = dict(self.DOC, delegations=[dict(self.DOC["delegations"][0], diff="sd-1")])   # not applied: asks go to the founder
        rec = doc["delegations"][0]
        m = of_model.build(doc, [], at=self.NOW)
        rows = [question(1, "2026-11-04T10:00Z", "s-k8s"), prompt(2, "2026-11-04T10:05Z", "s-k8s", "да"),
                question(3, "2026-11-04T14:00Z", "s-k8s"), prompt(4, "2026-11-04T16:00Z", "s-k8s", "ок"),
                question(5, "2026-11-04T20:00Z", "s-k8s"),
                ev(6, "2026-11-06T00:00Z", "of.diff_applied", None, {"id": "sd-1", "kind": "delegate"})]   # after the window
        a = of_model.epoch("2026-11-04T00:00Z")
        offs = [h * 3600 for h in (11, 15, 17, 21, 24)]
        series = M.delegation_series(m, rows, a, a + 86400, rec, offs)
        for off, x in zip(offs, series):
            one = M.delegation_review(m, rows, a, a + off, rec)
            for k in ("asks", "answered", "open_at_end", "agent_hours_waited", "founder_messages_in_unit"):
                self.assertEqual(x["founder"][k], one["founder"][k], (off, k))
        self.assertEqual([x["founder"]["asks"] for x in series], [1, 2, 2, 3, 3])
        self.assertEqual(series[-1]["founder"]["by_position"], {"k8s_keeper": 3})

    def _review(self, predictions, extra_rows=(), agent_owner=False):
        import tempfile
        from unittest import mock
        doc = dict(self.DOC, delegations=[dict(self.DOC["delegations"][0], id="del-t", diff="sd-1")])
        if agent_owner:
            doc["positions"] = [dict(p, holder={"session": "s-plat"}) if p["name"] == "plat_owner" else p for p in doc["positions"]]
        rec = doc["delegations"][0]
        m = of_model.build(doc, [], at="2026-11-12T00:00Z")
        with tempfile.TemporaryDirectory() as repo:
            os.makedirs(os.path.join(repo, "reports", "delegations"))
            with open(os.path.join(repo, "reports", "delegations", "del-t.yaml"), "w") as fh:
                fh.write('baseline: {window: ["2026-11-01T00:00Z", "2026-11-08T00:00Z"], founder: {asks: 4}}\n'
                         'review_window: ["2026-11-08T00:00Z", "2026-11-15T00:00Z"]\n'
                         f'predictions: {predictions}\n')
            # baseline: 2 asks on day 1, 2 more on day 6; review: 2 asks on day 1; applied after both windows
            rows = [question(i, ts, "s-k8s") for i, ts in enumerate(
                ["2026-11-01T10:00Z", "2026-11-01T11:00Z", "2026-11-06T10:00Z", "2026-11-06T11:00Z",
                 "2026-11-08T10:00Z", "2026-11-08T11:00Z"], 1)] + list(extra_rows)
            rows.append(ev(9, "2026-11-20T00:00Z", "of.diff_applied", None, {"id": "sd-1", "kind": "delegate"}))

            class D:
                pass
            d = D()
            d.repo, d.rows, d.now = repo, rows, of_model.epoch("2026-11-09T00:00Z")
            d.rows_between = lambda a, b: [r for r in rows if of_model.epoch(r["ts"]) < b]   # drops the diff_applied
            d.delegations_effective = lambda: of_model.delegation_effective(rows)
            with mock.patch.object(of_studio, "_review_cache_path", lambda rid: os.path.join(repo, f"cache-{rid}.json")):
                return of_studio.delegation_review(d, m, rec)

    def test_review_pace_against_the_baseline_point(self):
        rv = self._review('[{metric: founder.asks, op: "<=", value: 2}]')
        r = rv["rows"][0]
        self.assertEqual((r["baseline_ours"], r["baseline_same_point"], r["observed"]), (4, 2, 2))
        self.assertEqual(r["expected_now"], 1.0)        # 2 × (2 of 4 reached by day 1)
        self.assertIs(r["on_track"], False)
        self.assertFalse(r["decided"])
        self.assertFalse(rv["early"])
        self.assertIn("off pace", of_studio._pace_word(r, rv)[0])

    def test_review_settles_a_growing_measure_past_its_value(self):
        rv = self._review('[{metric: founder.asks, op: "<=", value: 1}, {metric: founder.asks, op: ">=", value: 2}]')
        low, high = rv["rows"]
        self.assertEqual((low["on_track"], low["decided"]), (False, True))
        self.assertEqual(of_studio._pace_word(low, rv)[0], "missed for the week")
        self.assertEqual((high["on_track"], high["decided"]), (True, True))
        self.assertIn("1 met for the week, 1 missed for the week", of_studio._review_html(rv))

    def test_review_shape_and_unmeasurable_for_the_unit_owner(self):
        preds = ('[{metric: unit_owner_side.asks, op: ">=", value: 10}, '
                 '{metric: unit_owner_side.answer_delay_min.p50, op: "<=", value: 5}]')
        rv = self._review(preds, agent_owner=True)
        asks, delay = rv["rows"]
        self.assertEqual(asks["shape"], "founder.asks")   # no baseline curve: the founder asks' shape, 2 of 4 by day 1
        self.assertEqual(asks["expected_now"], 5.0)
        self.assertNotEqual(delay["on_track"], "unmeasurable")   # of_asks v6: the asked position's reply closes the ask
        rv = self._review('[{metric: founder.asks, op: "<=", value: 9, unmeasurable: "re-derive pending"}]')
        self.assertEqual((rv["rows"][0]["on_track"], rv["rows"][0]["unmeasurable"]), ("unmeasurable", "re-derive pending"))
        self.assertIn("unmeasurable", of_studio._pace_word(rv["rows"][0], rv)[0])

    def test_unit_owner_answers_and_escalated_copies(self):
        rec = self.DOC["delegations"][0]   # no diff: in effect from the start
        m = of_model.build(self.DOC, [], at=self.NOW)
        decl = lambda i, ts, to, **kw: ev(i, ts, "of.agent_question", "s-k8s", dict(
            {"detected_by": "declared", "source": "declared", "from_position": "k8s_keeper", "to": to,
             "questions": [{"question": "go?"}]}, **kw))
        rows = [decl(1, "2026-11-04T10:00Z", "plat_owner"),
                ev(2, "2026-11-04T10:10Z", "of.agent_message_in", "s-k8s", {"from": "acme:plat_owner", "via": "relay"}),
                ev(3, "2026-11-04T11:00Z", "of.permission_request", "s-k8s", {"tool_name": "Bash"}),
                ev(4, "2026-11-04T11:02Z", "of.turn_end", "s-k8s", {}),          # the tool ran: approved in the keeper's pane
                ev(5, "2026-11-04T12:00Z", "of.agent_question", None,
                   {"detected_by": "declared", "source": "escalated", "from_position": "k8s_keeper", "to": "founder",
                    "escalated_from": "e1", "questions": [{"question": "go?"}]}, position="plat_owner")]
        r = M.delegation_review(m, rows, "2026-11-04T00:00Z", "2026-11-05T00:00Z", rec)
        o, f = r["unit_owner_side"], r["founder"]
        self.assertEqual((o["asks"], o["answered"], o["closed_otherwise"]), (2, 1, 1))   # the reply counts, the pane's approval not
        self.assertEqual(o["answer_delay_min"]["p50"], 10.0)
        self.assertEqual((f["asks"], f["escalated_copies"], f["by_position"]), (1, 1, {"k8s_keeper": 1}))
        self.assertEqual(f["escalated_by_position"], {"k8s_keeper": 1})

    def test_unit_owner_yes_rule(self):
        rec = self.DOC["delegations"][0]
        m = of_model.build(self.DOC, [], at=self.NOW)
        hdr = "Message from acme:plat_owner (via relay, authority: none: no grant; msg 1)\n"
        msg = lambda i, sid, text, frm="acme:plat_owner", **cl: ev(i, "2026-11-04T10:00Z", "of.agent_message_in", sid,
                                                                    {"from": frm, "via": "relay", "text": hdr + text,
                                                                     "classifications": cl})
        pr = lambda i, n, title: ev(i, "2026-11-04T12:00Z", "of.work_record", None, {
            "source": "gh", "repo": "o/infra", "number": n, "author": "vp", "merged_at": "2026-11-04T12:00Z",
            "title": title, "systems": ["k8s"]})
        rows = [msg(1, "s-k8s", "yes: merge #80 on green CI, my routine yes"),
                msg(2, "s-k8s", "platform_owner: the founder said YES to the package"),     # relaying the founder's yes
                msg(3, "s-k8s", "platform_owner: approved, apply from main at 16:00"),
                msg(4, "s-plat", "can I merge #81?", frm="acme:k8s_keeper", **{"rq-v0": True}),
                pr(5, 80, "node pools: taints"), pr(6, 81, 'Revert "node pools: taints"'), pr(7, 82, "other")]
        doc = dict(self.DOC, positions=[dict(p, holder={"session": "s-plat"}) if p["name"] == "plat_owner" else p
                                        for p in self.DOC["positions"]])
        m = of_model.build(doc, [], at=self.NOW)
        y = M.unit_owner_yes(m, rows, "2026-11-04T00:00Z", "2026-11-05T00:00Z", rec)
        self.assertEqual((y["messages_to_keepers"], y["owner_yes"], y["requests_from_keepers"]), (3, 2, 1))
        self.assertEqual((y["unit_prs_merged"], y["yes_prs"]), (2, ["o/infra#80"]))
        self.assertEqual((y["reverts_of_yes_merges"], y["reverts_in_unit"]), (1, 1))
        self.assertEqual(y["reverted"]["of_yes_merges"], [{"pr": "o/infra#80", "revert": "o/infra#81"}])
        # of_field-analyst's hand check of uy-v0: the four it missed, the two relays it took, the standing yes
        yes = lambda t: M.yes_sentences(t)
        self.assertTrue(yes("A) yes: merge #761 on green CI")[0])
        self.assertTrue(yes("Thanks. Merging #771 afterwards is a routine yes from me.")[0])
        self.assertTrue(yes("#772 as updated is fine. Merge after the window.")[0])
        self.assertTrue(yes("Checked the plan; go ahead and apply it from main at 16:00.")[0])
        self.assertFalse(yes("platform_owner: the founder said YES to the offboarding package, merge it today")[0])
        self.assertFalse(yes("founder yes 9e2bd3fe covers the merge of #80")[0])
        self.assertEqual(yes("Standing routine yes inside the unit: you may force-push your own PR branches."), ([], True))
        self.assertFalse(M.is_unit_owner_yes("yes, I read it"))   # no merge, apply, push or PR
        self.assertEqual(yes("platform_owner: standing founder yes (ask 47f5527d): you may push PR branches."), ([], False))
        self.assertEqual(yes("Note: the org-wide rule (founder) applies. You may push your OWN branches without a yes."), ([], False))
        self.assertFalse(yes("Yours: GitHub deploy keys, fine-grained PATs approved for the org, Mergify, branch protections.")[0])
        self.assertFalse(yes("aws_keeper: three founder answers. (1) H9 #77 YES (77bf16a2): apply tags only. (2) H2 #78 yes: merge it.")[0])
        self.assertFalse(yes("yes: merge #90, the founder's go (9e2bd3fe).")[0])          # an ask id: the founder's answer
        self.assertTrue(yes("The founder said yes to the package. Merge #770 on green CI as my routine yes.")[0])

    def test_org_less_closures_follow_their_ask(self):
        org = {"name": "acme", "paths": ["~/acme"]}
        q = ev(1, "2026-11-04T10:00Z", "of.agent_question", "s-k8s")
        rec = ev(2, "2026-11-04T11:00Z", "of.agent_question_answered", None,
                 {"ask_id": "e1", "ask_ids": ["e1"], "via": "reconcile", "reason": "dialog_closed"}, cwd=None)
        other = ev(3, "2026-11-04T11:00Z", "of.ask_withdrawn", None, {"ask_id": "e99"}, cwd=None)
        kept = [r["event_id"] for r in of_studio.org_rows([q, rec, other], org, [])]
        self.assertEqual(kept, ["e1", "e2"])

    def test_escalation_on_severity_and_unmeasured_budget(self):
        m = of_model.build(self.DOC, [], at=self.NOW)
        crit = dict(of_diagnose.issue(of_diagnose.catalogue(), "fragmented-ownership", ["k8s", "k8s_keeper"], {}, "x",
                                      critical=True, part="runtime"))
        esc = {x["condition"]: x for x in of_diagnose.escalations(m, {"issues": [crit]})}
        self.assertNotIn("ask.blocking", esc)                          # of pulse's condition
        self.assertEqual(esc["issue.severity >= high"]["status"], "fired")
        self.assertEqual(esc["budget_used > 80%"]["status"], "not_measured")
        self.assertEqual(of_diagnose.escalations(m, {"issues": []})[0]["status"], "quiet")
        esc = {x["condition"]: x for x in of_diagnose.escalations(m, {"issues": []},
                                                                  {"platform": {"used": 900, "budget": 1000, "share": 0.9}})}
        self.assertEqual(esc["budget_used > 80%"]["status"], "fired")
        esc = {x["condition"]: x for x in of_diagnose.escalations(m, {"issues": []},
                                                                  {"platform": {"usd_used": 900, "usd_budget": 1000, "usd_share": 0.9}})}
        self.assertEqual(esc["budget_used > 80%"]["status"], "fired")             # dollars alone can fire it

    def test_issues_inside_a_delegated_unit_go_to_its_owner(self):
        m = of_model.build(self.DOC, [], at=self.NOW)
        cat = of_diagnose.catalogue()
        inside = of_diagnose.route_to_unit(m, of_diagnose.issue(cat, "orphan-work", ["k8s"], {}, "x", part="keeperless"))
        across = of_diagnose.route_to_unit(m, of_diagnose.issue(cat, "coordination-gap", ["k8s", "bot"], {}, "y"))
        self.assertEqual((inside["decider"], inside["decider_position"], inside["unit"]), ("unit_owner", "plat_owner", "platform"))
        self.assertEqual(across["decider"], "owner")

    def test_founder_sees_the_delegated_unit_as_one_row(self):
        dd = of_studio.Data.__new__(of_studio.Data)
        dd.orgs, dd.org, dd.now = ["acme"], "acme", of_model.epoch(self.NOW)
        dd.a, dd.b, dd.unit = dd.now - 7 * 86400, dd.now, None
        dd.rows = [question(1, "2026-11-04T10:00Z", "s-k8s")]
        dd.compute(of_model.build(self.DOC, dd.rows, at=dd.now, org="acme", rev="x", window_h=168))
        row = next(u for u in dd.rollup if u["owner"] == "plat_owner")
        self.assertEqual((row["unit"], row["delegation"], row["open_asks"]), ("platform", "del-1", 1))
        self.assertNotIn("k8s_keeper", [x["position"] for x in dd.attention])   # not in the founder's inbox
        self.assertIn("del-1", of_studio.render("owner", dd))


class StudioTest(unittest.TestCase):
    def data(self, rows=(), unit=None):
        d = of_studio.Data.__new__(of_studio.Data)
        d.orgs, d.org, d.now = ["acme"], "acme", of_model.epoch("2026-11-05T00:00Z")
        d.a, d.b = d.now - 7 * 86400, d.now
        d.rows = list(rows)
        d.unit = unit
        d.compute(of_model.build(DOC, d.rows, at=d.now, org="acme", rev="undated:x", window_h=168), unit)
        return d

    def test_colleague_sees_only_their_unit(self):
        rows = judged(MeasuresTest().rows())      # e6 is an ask from k8s_keeper, owned by the colleague
        d = self.data(rows, unit="colleague")
        self.assertEqual(set(d.model["positions"]), {"colleague", "k8s_keeper"})
        self.assertEqual(set(d.model["systems"]), {"k8s"})
        self.assertEqual([x["position"] for x in d.attention], ["k8s_keeper"])
        html = of_studio.render("inbox", d) + of_studio.render("coverage", d)
        for other in ("bot_search", "bot_operator", "data_keeper"):   # the founder's own positions stay out
            self.assertNotIn(other, html)

    def test_founder_sees_delegated_unit_as_one_row(self):
        d = self.data(judged(MeasuresTest().rows()))
        self.assertNotIn("k8s_keeper", [x["position"] for x in d.attention])   # the colleague answers it
        self.assertEqual([u["owner"] for u in d.rollup], ["colleague"])
        self.assertEqual(d.rollup[0]["waiting_on_owner"], 1)
        self.assertIn("delegated unit", of_studio.render("owner", d))

    def test_loop_issues_go_to_the_doers(self):
        d = self.data()
        cat = of_diagnose.catalogue()
        i = of_diagnose.issue(cat, "chatty", ["w1"], {"work": "w1"}, "run it on offline first")
        i.update(decider="doer", decider_position="bot_search")
        d.diagnosis = {"issues": [i], "rules": [], "catalogue": "diagnose-v0"}
        html = of_studio.render("inbox", d)
        self.assertIn("1 loop issue for the works' doers (bot_search; the pulse nudges them): run it on offline first", html)
        self.assertNotIn("None with a known gain", html.split("loop issue")[1])

    def test_coverage_shows_lifecycle_and_external_keepers(self):
        doc = dict(DOC, systems=dict(DOC["systems"], old={"kind": "target", "lifecycle": "retired"},
                                     dana={"kind": "target", "governed_by": "external:dana"}))
        d = of_studio.Data.__new__(of_studio.Data)
        d.orgs, d.org, d.now, d.rows, d.unit = ["acme"], "acme", of_model.epoch("2026-11-05T00:00Z"), [], None
        d.a, d.b = d.now - 7 * 86400, d.now
        d.compute(of_model.build(doc, [], at=d.now, org="acme", rev="x", window_h=168), None)
        html = of_studio.render("coverage", d)
        self.assertIn('old<span class="sub">retired</span></th>', html)       # next to the name
        self.assertIn("kept by dana (external)", html)

    def test_unit_viewer_must_name_an_owner(self):
        self.assertEqual(of_studio.handle("GET", "/studio/", None, {"scope": "unit"})[0], 403)

    def test_views_render_without_inline_styles_or_scripts(self):
        d = self.data(MeasuresTest().rows())
        for view in ("inbox", "owner", "coverage", "structure", "waits"):
            st, ct, body = of_studio.handle("GET", f"/studio/{view}", "org=acme", {"tenant": True}, page=d)
            self.assertEqual(st, 200)
            html = body.decode()
            self.assertNotIn("<script", html)
            self.assertIsNone(re.search(r'\sstyle="', html), view)

    def test_inbox_lists_open_ask_and_declared_wait(self):
        d = self.data(judged(MeasuresTest().rows()))
        html = of_studio.render("inbox", d)
        self.assertIn("merge?", html)       # the open ask e5
        self.assertIn("bot_search", html)   # declared waiting_on: owner
        self.assertLess(html.index("Waiting on you"), html.index("Stalled"))
        self.assertLess(html.index("Stalled"), html.index("Waiting on someone else"))

    def test_all_organizations_inbox(self):
        d1 = self.data(MeasuresTest().rows())
        d2 = self.data()
        d2.org = "other"
        for x in d2.attention:
            x["org"] = "other"
        all_ = of_studio.AllData([d1, d2])
        html = of_studio.render("inbox", all_)
        self.assertIn("<th>Org</th>", html)
        self.assertIn("all organizations", html)
        self.assertEqual(len(all_.attention), len(d1.attention) + len(d2.attention))

    def test_issues_in_inbox_and_critical_on_top(self):
        d = self.data()
        crit = of_diagnose.issue(of_diagnose.catalogue(), "fragmented-ownership", ["k8s"], {}, "runtime write to k8s by bot_operator",
                                 critical=True, part="runtime")
        d.diagnosis = {"issues": [crit] + d.diagnosis["issues"], "rules": d.diagnosis["rules"], "catalogue": "diagnose-v0"}
        html = of_studio.render("inbox", d)
        self.assertLess(html.index("Critical"), html.index("Waiting on you"))
        self.assertIn("runtime write to k8s", html)
        self.assertIn("blocked", html)          # the rules' status line

    def test_structure_keeper_issues_leave_the_founders_block(self):
        d = self.data()
        cat = of_diagnose.catalogue()
        unm = dict(of_diagnose.issue(cat, "orphan-work", ["(unmapped)"], {}, "30% of PRs unmapped", part="unmapped"),
                   decider_position="acme_org")
        own = of_diagnose.issue(cat, "orphan-work", ["lonely"], {}, "9 PRs on lonely without keeper", part="keeperless")
        d.diagnosis = {"issues": [own, unm], "rules": [], "catalogue": "diagnose-v0"}
        html = of_studio.render("inbox", d)
        self.assertIn("routed to acme_org", html)
        self.assertLess(html.index("9 PRs on lonely"), html.index("routed to acme_org"))
        self.assertNotIn("<td class=\"text\">30% of PRs unmapped", html)

    def test_inbox_groups_ranked_and_risks(self):
        d = self.data()
        cat = of_diagnose.catalogue()
        ranked = of_diagnose.issue(cat, "owner-relay", ["a", "b"], {}, "relay a to b", delta_m=3.0)
        risk_hi = of_diagnose.issue(cat, "orphan-work", ["lonely"], {}, "lonely has no keeper", part="keeperless")
        risk_lo = of_diagnose.issue(cat, "orphan-work", ["(unmapped)"], {}, "unmapped prs", part="unmapped")
        risk_lo["decider"] = "owner"
        items = sorted([risk_lo, ranked, risk_hi], key=of_diagnose.rank_key)
        self.assertEqual([i["summary"] for i in items], ["relay a to b", "lonely has no keeper", "unmapped prs"])
        d.diagnosis = {"issues": items, "rules": [], "catalogue": "diagnose-v0"}
        html = of_studio.render("inbox", d)
        self.assertLess(html.index("relay a to b"), html.index("Risks without a ΔT yet"))
        self.assertLess(html.index("Risks without a ΔT yet"), html.index("lonely has no keeper"))

    def test_function_gaps_collapse_into_one_row(self):
        d = self.data()
        cat = of_diagnose.catalogue()
        gaps = [of_diagnose.issue(cat, "function-gap", [f"function:{f}"], {"function": f}, f"No mandate covers {f}")
                for f in ("marketing", "hr", "community")]
        d.diagnosis = {"issues": gaps, "rules": [], "catalogue": "diagnose-v0"}
        html = of_studio.render("inbox", d)
        self.assertEqual(html.count("<details>"), 1)
        self.assertIn("function gaps: 3 (marketing, hr, community)", html)
        for g in gaps:
            self.assertIn(f"of issues label {g['issue_id']} false_positive", html)

    def test_unconfirmed_keeper_on_the_card(self):
        d = self.data()
        i = of_diagnose.issue(of_diagnose.catalogue(), "owner-relay", ["a", "b"],
                              {"registry_unconfirmed": True, "unconfirmed": {"a": "merge them?"}}, "relay", delta_m=1.0)
        d.diagnosis = {"issues": [i], "rules": [], "catalogue": "diagnose-v0"}
        html = of_studio.render("inbox", d)
        self.assertIn("registry entry not yet confirmed", html)
        self.assertIn("merge them?", html)
        i["evidence"]["unconfirmed_what"] = ["boundary", "keeper"]
        self.assertIn("boundary, keeper not yet confirmed", of_studio.render("inbox", d))

    def test_escapes_text(self):
        rows = [ev(1, "2026-11-04T10:00Z", "of.agent_question", "s-op", {"questions": [{"question": "<b>x</b>"}]})]
        html = of_studio.render("inbox", self.data(rows))
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)

    def test_server_source_is_used(self):
        class Source:
            def __init__(self):
                self.calls = []

            def events(self, org, since_iso=None, names=None):
                self.calls.append((org, names))
                yield {"event_id": "x1", "ts": "2026-11-04 10:00:00.000", "name": "of.agent_question", "session_id": "s-op",
                       "org_id": org, "payload": '{"questions": [{"question": "deploy?"}]}', "harness_name": "claude-code"}
        src = Source()
        d = of_studio.Data.__new__(of_studio.Data)
        rows = [of_studio._normalize(r) for r in src.events("acme")]
        self.assertEqual(rows[0]["payload"]["questions"][0]["question"], "deploy?")
        self.assertEqual(rows[0]["harness"], "claude-code")
        self.assertEqual(rows[0]["org"], "acme")
        self.assertEqual(rows[0]["ts"], "2026-11-04T10:00:00.000Z")
        self.assertEqual(of_model.epoch(rows[0]["ts"]), of_model.epoch("2026-11-04T10:00Z"))

    def test_no_registry_is_503_not_500(self):
        old = os.environ.get("OF_ORGS")
        os.environ["OF_ORGS"] = "/nonexistent/orgs.yaml"
        try:
            st, ct, body = of_studio.handle("GET", "/studio/", None, {"tenant": True})
        finally:
            if old is None:
                os.environ.pop("OF_ORGS")
            else:
                os.environ["OF_ORGS"] = old
        self.assertEqual(st, 503)
        self.assertIn(b"Registry not available", body)
        self.assertEqual(of_studio.handle("GET", "/studio/static/studio.css", None, {"tenant": True})[0], 200)

    def test_access_and_routes(self):
        d = self.data()
        self.assertEqual(of_studio.handle("POST", "/studio/", None, {"tenant": True}, page=d)[0], 405)
        self.assertEqual(of_studio.handle("GET", "/studio/", None, {}, page=d)[0], 403)
        self.assertEqual(of_studio.handle("GET", "/studio/nope", None, {"tenant": True}, page=d)[0], 404)
        st, ct, _ = of_studio.handle("GET", "/studio/static/studio.css", None, {"tenant": True}, page=d)
        self.assertEqual((st, ct), (200, "text/css; charset=utf-8"))


class WorksTest(unittest.TestCase):
    NOW = "2026-11-06T12:00Z"

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        rfc = os.path.join(root, "arb", "docs", "rfc")
        os.makedirs(rfc)
        os.makedirs(os.path.join(root, "arb", ".git"))                        # a main checkout
        os.makedirs(os.path.join(root, "arb-wt", "docs", "rfc"))
        open(os.path.join(root, "arb-wt", ".git"), "w").write("gitdir: x")    # a worktree: skipped
        for slug, head in (("spam-triggers", "# Spam triggers\nStatus: measurement, 2026-10-29\n"),
                           ("old-idea", "# An old idea\nStatus: proposal, 2026-10-01\n"),
                           ("fast-path", "# Hot path\nStatus: built\n")):
            open(os.path.join(rfc, slug + ".md"), "w").write(head)
        open(os.path.join(rfc, "README.md"), "w").write("# index")
        repo = os.path.join(root, "org")
        os.makedirs(os.path.join(repo, "registry", "briefs"))
        brief = lambda n, t: open(os.path.join(repo, "registry", "briefs", n + ".md"), "w").write(t)
        brief("bot_search", "# Brief\n## Current mandate\nOwn docs/rfc/spam-triggers.md: H1-H10.\n")
        brief("bot_operator", "# Brief\n## Read first\n- docs/rfc/fast-path.md\n## Mandate\nRun the bots.\n")
        self.org = {"name": "acme", "repo": repo, "paths": [root]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_derive_and_state(self):
        import of_works
        m = of_model.build(DOC, [], at=self.NOW)
        now = of_model.epoch(self.NOW)
        ws = {w["id"]: w for w in of_works.derive(m, self.org, now)}
        self.assertEqual(sorted(ws), ["arb:fast-path", "arb:old-idea", "arb:spam-triggers"])   # bot_search became the RFC's work
        self.assertEqual((ws["arb:spam-triggers"]["doers"], ws["arb:spam-triggers"]["project"]), (["bot_search"], "bot_search"))
        self.assertEqual(ws["arb:fast-path"]["doers"], [])             # a "Read first" link is reading, not work
        self.assertEqual(ws["arb:spam-triggers"]["title"], "Spam triggers")
        pr = ev(1, "2026-11-05T10:00Z", "of.work_record", None, {"source": "gh", "repo": "o/arb", "number": 7, "author": "vp",
                                                                 "merged_at": "2026-11-05T10:00Z", "head_ref": "claude/x",
                                                                 "title": "unrelated"}, position="bot_search")
        wait = ev(2, "2026-11-06T11:00Z", "of.state_declared", "s-op", {"position": "bot_operator", "state": "waiting",
                                                                         "waiting_on": "k8s_keeper"})
        rows = of_works.rows_for(m, [pr, wait], self.org, now)
        got = {w["id"]: w for w in rows}
        self.assertEqual(got["arb:spam-triggers"]["state"], "waiting")   # bot_search declares waiting_on: owner
        self.assertTrue(of_works.waits_on_owner(got["arb:spam-triggers"]))
        self.assertEqual(rows[0]["id"], "arb:spam-triggers")              # works waiting on the owner come first
        self.assertEqual(got["arb:spam-triggers"]["progress_7d"], 1)      # the doer's PR
        self.assertEqual(got["arb:old-idea"]["state"], "proposed")
        self.assertEqual(got["arb:fast-path"]["state"], "quiet")

    def test_registered_works_and_matching(self):
        import of_works
        with open(os.path.join(self.org["repo"], "registry", "works.draft.yaml"), "w") as fh:
            fh.write("draft: {status: 'confirmed by the founder'}\nworks:\n"
                     "  w-spam: {title: Spam, kind: rfc, doc: 'arb:docs/rfc/spam-triggers.md (PR #9)', systems: [bot],\n"
                     "           doers: [bot_operator], draft_state: live, match: {prs: [9], branches: ['claude/spam-*']}}\n"
                     "  w-other: {title: Other, kind: project, doc: 'arb:docs/x.md', systems: [bot], doers: [bot_operator], draft_state: live}\n"
                     "  w-solo: {title: Solo, kind: project, doc: 'arb:docs/y.md', systems: [k8s], doers: [k8s_keeper], draft_state: live}\n"
                     "  w-old: {title: Old, kind: rfc, doc: 'arb:docs/rfc/old-idea.md', systems: [bot], draft_state: parked}\n")
        m = of_model.build(DOC, [], at=self.NOW)
        now = of_model.epoch(self.NOW)
        pr = lambda i, n, branch, pos: ev(i, "2026-11-05T10:00Z", "of.work_record", None, {
            "source": "gh", "repo": "o/arb", "number": n, "author": "vp", "merged_at": "2026-11-05T10:00Z", "head_ref": branch,
            "title": "t"}, position=pos)
        rows = [pr(1, 9, "claude/a", "bot_operator"), pr(2, 10, "claude/spam-h2", "bot_operator"),
                pr(3, 11, "claude/b", "bot_operator"), pr(4, 12, "claude/c", "k8s_keeper")]
        got = {w["id"]: w for w in of_works.rows_for(m, rows, self.org, now)}
        self.assertEqual(sorted(got), ["w-old", "w-other", "w-solo", "w-spam"])     # the list replaces the derived works
        self.assertEqual([p["number"] for p in got["w-spam"]["prs"]], [9, 10])     # by number and by branch
        self.assertEqual(got["w-other"]["prs"], [])                                # #11: bot_operator has two open works
        self.assertEqual(got["w-other"]["ambiguous_doers"], ["bot_operator"])

        self.assertEqual([p["number"] for p in got["w-solo"]["prs"]], [12])        # a doer with one open work
        self.assertEqual(got["w-old"]["state"], "parked")
        self.assertEqual(got["w-spam"]["doc"], {"repo": "arb", "path": "docs/rfc/spam-triggers.md"})
        # a merged PR's changed paths against match.paths
        with open(os.path.join(self.org["repo"], "registry", "works.draft.yaml"), "a") as fh:
            fh.write("  w-paths: {title: P, kind: migration, doc: 'arb:docs/z.md', doers: [], draft_state: live,\n"
                     "            match: {paths: ['arb:k8s/talos/**']}}\n")
        prp = ev(8, "2026-11-05T10:00Z", "of.work_record", None, {"source": "gh", "repo": "o/arb", "number": 13, "author": "vp",
                                                                  "merged_at": "2026-11-05T10:00Z", "head_ref": "claude/q", "title": "t",
                                                                  "paths": ["k8s/talos/nodes/w1.yaml"]}, position="k8s_keeper")
        got = {w["id"]: w for w in of_works.rows_for(m, rows + [prp], self.org, now)}
        self.assertEqual([p["number"] for p in got["w-paths"]["prs"]], [13])
        self.assertNotIn(13, [p["number"] for p in got["w-solo"]["prs"]])    # claimed by the path match, not the doer

    def test_registry_state_done_wins_over_draft_state(self):
        import of_works
        with open(os.path.join(self.org["repo"], "registry", "works.draft.yaml"), "w") as fh:
            fh.write("draft: {status: 'confirmed by the founder'}\nworks:\n"
                     "  w-closed: {title: C, kind: rfc, doc: 'arb:docs/rfc/c.md', systems: [bot], doers: [], state: done, draft_state: live}\n"
                     "  w-held: {title: H, kind: rfc, doc: 'arb:docs/rfc/h.md', systems: [bot], doers: [], state: parked, draft_state: live}\n"
                     "  w-live: {title: L, kind: rfc, doc: 'arb:docs/rfc/l.md', systems: [bot], doers: [], draft_state: live}\n"
                     "  w-drop: {title: D, kind: rfc, doc: 'arb:docs/rfc/d.md', systems: [bot], doers: [], draft_state: parked}\n")
        m = of_model.build(DOC, [], at=self.NOW)
        got = {w["id"]: w for w in of_works.rows_for(m, [], self.org, of_model.epoch(self.NOW))}
        self.assertEqual(got["w-closed"]["state"], "done")       # the keeper's end, whatever the document says
        self.assertEqual(got["w-held"]["state"], "parked")
        self.assertNotIn(got["w-live"]["state"], ("done", "parked"))
        self.assertEqual(got["w-drop"]["state"], "parked")       # a parked document still parks the work

    def test_record_works_and_open_prs(self):
        import of_works
        with open(os.path.join(self.org["repo"], "registry", "works.draft.yaml"), "w") as fh:
            fh.write("draft: {status: 'confirmed by the founder'}\nworks:\n"
                     "  w-spam: {title: Spam, kind: rfc, doc: 'arb:docs/rfc/spam-triggers.md', systems: [bot],\n"
                     "           doers: [bot_operator], draft_state: live, match: {branches: ['claude/spam-*']},\n"
                     "           stages: [{id: h2, title: second}]}\n"
                     "  w-other: {title: Other, kind: project, doc: 'arb:docs/x.md', systems: [bot], doers: [bot_search], draft_state: live}\n")
        m = of_model.build(DOC, [], at=self.NOW)
        now = of_model.epoch(self.NOW)
        merged = ev(1, "2026-11-01T10:00Z", "of.work_record", None, {
            "source": "gh", "repo": "o/arb", "number": 20, "author": "vp", "merged_at": "2026-11-01T10:00Z",
            "head_ref": "claude/spam-x", "title": "t", "works": ["w-other"]})     # the record's works win over the branch glob
        st = lambda i, n, upd, state, push, review=None, works=("w-spam",), branch="claude/spam-h2": ev(
            i, upd, "of.pr_state", None, {"source": "gh", "repo": "o/arb", "number": n, "author": "vp", "head_ref": branch,
                                          "title": "t", "state": state, "draft": True, "updated_at": upd,
                                          "last_push_at": push, "last_review_at": review, "works": list(works)})
        rows = [merged,
                st(2, 30, "2026-11-04T09:00Z", "open", "2026-11-04T08:00Z"),
                st(3, 30, "2026-11-05T09:00Z", "open", "2026-11-04T08:00Z", review="2026-11-05T09:00Z"),   # newest state of #30
                st(4, 31, "2026-11-03T09:00Z", "open", "2026-11-03T08:00Z"),
                st(5, 31, "2026-11-04T09:00Z", "closed", "2026-11-03T08:00Z")]                           # closed unmerged
        got = {w["id"]: w for w in of_works.rows_for(m, rows, self.org, now)}
        self.assertEqual([p["number"] for p in got["w-other"]["prs"]], [20])
        self.assertEqual(got["w-spam"]["prs"], [])
        self.assertEqual([p["number"] for p in got["w-spam"]["open_prs"]], [30])
        self.assertEqual(got["w-spam"]["last_progress"], of_model.epoch("2026-11-05T09:00Z"))   # the review on the open PR
        self.assertEqual(got["w-spam"]["progress_7d"], 1)
        self.assertEqual(got["w-spam"]["stages"][0]["state"], "moving")                         # its branch names h2

    def test_stages_from_an_rfc(self):
        import of_works
        text = ("# RFC\nStatus: phases 0 and 1 are built; phase 2 in review\n## 1. Summary\n## Phase 0: bench\n## Phase 1: shadow\n"
                "## Phase 2: live\n### 5.1 P1 (Robinhood): children\n| ID | Hypothesis |\n|---|---|\n"
                "| H1 (SP1) | siblings rarely land | x |\n| H3 (SP3) | our own storefront. **Closed 2026-10-30** | x |\n"
                "| P1 | a problem row is not a stage |\n")
        st = of_works.stages_from_doc(text, of_works._status_of(text))
        self.assertEqual([x["id"] for x in st], ["phase-0", "phase-1", "phase-2", "p1", "h1", "h3"])
        self.assertEqual([x["done"] for x in st], [True, True, False, False, False, True])
        self.assertEqual(st[5]["title"], "our own storefront. Closed 2026-10-30")
        prs = [{"key": of_works._norm("claude/x-h1-replay docs(rfc): spam triggers, H1 on the burst"), "number": 5}]
        of_works.stage_evidence(st, prs, [{"repo": "arb", "branch": "claude/phase-2-live", "last": 0}])
        self.assertEqual([x["state"] for x in st], ["done", "done", "moving", "not started", "moving", "done"])
        self.assertEqual(of_works.summarize_stages(st), "3 of 6 done · moving: Phase 2, H1")
        self.assertEqual(of_works.stage_label("h4a"), "H4a")
        self.assertEqual((of_works.stage_label("t2-t5"), of_works.stage_label("phase-2")), ("T2-t5", "Phase 2"))
        self.assertEqual(of_works.stage_tokens("t2-t5"), ["t2", "t3", "t4", "t5"])
        self.assertEqual(of_works.stage_tokens("V2-V3"), ["v2", "v3"])
        self.assertEqual((of_works.stage_tokens("4-6"), of_works.stage_tokens("1"), of_works.stage_tokens("s1-bt")), ([], [], ["s1-bt"]))
        reg = [{"id": "t3", "title": "x", "done": False, "source": "registry"}, {"id": "kp", "title": "y", "done": False, "source": "registry"}]
        of_works.stage_evidence(reg, [], [{"repo": "infra", "branch": "claude/t3-node-w4", "last": 0}])
        self.assertEqual([x["state"] for x in reg], ["moving", "open"])

    def test_branch_commits_move_a_work(self):
        import shutil
        import subprocess
        import of_works
        if not shutil.which("git"):
            self.skipTest("no git")
        arb = os.path.join(self.tmp.name, "arb")
        shutil.rmtree(os.path.join(arb, ".git"))
        # no user or system git config reaches the test: the global one signs commits through the founder's
        # 1Password (commit.gpgsign, op-ssh-sign), and a test must never call it (of_platform, 2026-11-07)
        env = {k: v for k, v in os.environ.items() if k not in ("SSH_AUTH_SOCK", "GIT_DIR", "GIT_WORK_TREE")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
                   GIT_COMMITTER_DATE="2026-11-06T11:00:00+00:00", GIT_AUTHOR_DATE="2026-11-06T11:00:00+00:00")
        git = lambda *a: subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", "-C", arb, *a],
                                        env=env, capture_output=True, check=True)
        git("init", "-q")
        git("add", "-A")
        git("commit", "-qm", "x")
        git("update-ref", "refs/remotes/origin/claude/ext-work", "HEAD")
        with open(os.path.join(self.org["repo"], "registry", "works.draft.yaml"), "w") as fh:
            fh.write("draft: {status: confirmed}\nworks:\n  w-ext: {title: Ext, kind: rfc, doc: 'arb:docs/rfc/old-idea.md', "
                     "doers: ['external:dana'], draft_state: live, match: {branches: ['claude/ext-*']}}\n")
        m = of_model.build(DOC, [], at=self.NOW)
        w = of_works.rows_for(m, [], self.org, of_model.epoch(self.NOW))[0]
        self.assertEqual([b["branch"] for b in w["branches"]], ["claude/ext-work"])
        self.assertEqual(w["state"], "active")                  # an external doer's branch, no merged PR


    def test_works_has_its_own_help(self):
        """`of works --help` names `of works`, not `of model` (of_docs, 2026-11-11), and runs the same cmd_works."""
        import contextlib
        import io
        from unittest import mock
        import of_studio_cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            of_studio_cli.main(["works", "--help"])
        self.assertTrue(out.getvalue().startswith("usage: of works"))
        self.assertNotIn("summary,positions", out.getvalue())
        seen = []
        with mock.patch.object(of_studio_cli, "cmd_works", lambda a: seen.append((a.org, a.all, a.json)) or 0):
            of_studio_cli.main(["works", "--org", "acme", "--all"])
            of_studio_cli.main(["model", "works", "--org", "acme", "--json"])
        self.assertEqual(seen, [("acme", True, False), ("acme", False, True)])


class PromptOriginTest(unittest.TestCase):
    """of.prompt_origin corrections (of db prompt-origins): a cron fire recorded as human is never the owner's word."""

    def rows(self):
        return [prompt(1, "2026-11-06T10:00:00Z", "s-op", "please merge it"),
                prompt(2, "2026-11-06T10:05:00Z", "s-op", "hourly check-in: report status"),
                ev(3, "2026-11-06T19:00:00Z", "of.prompt_origin", None, {"event_id": "e2", "origin": "scheduled"}, source="of")]

    def test_apply_from_rows(self):
        import of_asks
        rows = of_asks.apply_prompt_origins(self.rows())
        self.assertEqual([r["payload"]["origin"] for r in rows], ["human", "scheduled"])   # the correction row dropped

    def test_founder_messages_drop_corrected(self):
        self.assertEqual(len(M.founder_messages(self.rows())), 2)        # uncorrected: the label says founder
        import of_asks
        msgs = M.founder_messages(of_asks.apply_prompt_origins(self.rows()))
        self.assertEqual([m["event_id"] for m in msgs], ["e1"])

    def test_old_prompt_without_origin_still_counts(self):
        r = ev(9, "2026-11-06T10:00:00Z", "of.human_prompt", "s-op", {"text": "x"})
        self.assertTrue(of_model.owner_prompt(r))
        self.assertFalse(of_model.owner_prompt(ev(8, "2026-11-06T10:00:00Z", "of.human_prompt", "s-op", {"origin": "scheduled"})))

    def test_load_events_reads_corrections_outside_the_window(self):
        import json, tempfile, of_store
        with tempfile.TemporaryDirectory() as d:
            conn = of_store.connect(os.path.join(d, "of.db"))
            for r in self.rows():
                conn.execute("INSERT INTO event (event_id, ts, name, session_id, source, payload, record) VALUES (?,?,?,?,?,?,?)",
                             (r["event_id"], r["ts"], r["name"], r["session_id"], r["source"], json.dumps(r["payload"]), "{}"))
            rows = of_model.load_events(conn, since="2026-11-06T09:00:00Z", until="2026-11-06T11:00:00Z",
                                        names=("of.human_prompt",))
            self.assertEqual([r["payload"]["origin"] for r in rows], ["human", "scheduled"])
            conn.close()


class MessageSplitTest(unittest.TestCase):
    def test_incident_topics(self):
        asks = [{"event_id": "q-0000aaaa11112222", "text": "Перевыпустить ли сегодня приватный ключ GitHub-приложения?"},
                {"event_id": "q-0000bbbb33334444", "text": "Перезапустить ли раннер бэктестов на новую конфигурацию?" + " " * 200 + "(ключи доступа не меняются)"},
                {"event_id": "q-0000cccc55556666", "text": "Окно на завтра?", "tags": [{"tag": "security_incident"}]}]
        msgs = [ev(1, "2026-11-06T10:00Z", "of.human_prompt", "s", {"text": "Answer from the founder to your question 11112222: да"}),
                ev(2, "2026-11-06T10:01Z", "of.human_prompt", "s", {"text": "Answer from the founder to your question 33334444: да"}),
                ev(3, "2026-11-06T10:02Z", "of.human_prompt", "s", {"text": "Answer from the founder to your question 55556666: да"}),
                ev(4, "2026-11-06T10:03Z", "of.human_prompt", "s", {"text": "Мы же уволили Алексея, проверь доступы"}),
                ev(5, "2026-11-06T10:04Z", "of.human_prompt", "s", {"text": "как у нас дела?"})]
        got = M.incident_topics(msgs, asks)
        self.assertEqual([got[m["event_id"]] for m in msgs], [True, False, True, True, False])

    def test_tool_frame_is_not_the_founder(self):
        rows = [prompt(1, "2026-11-06T10:00Z", "s", "Question from ci_keeper (unit platform, delegated to you; ask 1): ok?"),
                prompt(2, "2026-11-06T10:01Z", "s", "merge it")]
        self.assertEqual([m["event_id"] for m in M.founder_messages(rows)], ["e2"])
        tools = [prompt(3, "2026-11-06T10:02Z", "s", "You hold the position net_keeper. Read the brief and start."),
                 prompt(4, "2026-11-06T10:03Z", "s", "of_capacity moved this session to another account. Read the note."),
                 prompt(5, "2026-11-06T10:04Z", "s", "of_capacity finished moving this session (typed by hand)")]
        self.assertEqual([m["event_id"] for m in M.founder_messages(tools)], ["e5"])

    def test_split_rates(self):
        rv = {"elapsed_h": 12.0, "ended": False, "unit_owner": "boss",
              "rows": [{"metric": "founder.founder_messages_in_unit", "op": "<=", "value": 113}],
              "now": {"founder": {"founder_messages_split": {"to_unit_owner": 8, "to_keepers": 2, "incident": 6, "other": 4,
                                                             "popup_answers": 7, "rule": "inc-v0"}}}}
        x = of_studio.message_split(rv)
        self.assertEqual((x["parts"]["other"]["week"], x["other_above"]), (56, False))
        self.assertIn("within the prediction &lt;= 113", of_studio._split_html(rv, rv["rows"][0]))


class StatusPollTest(unittest.TestCase):
    """sp-v1 on the examples of docs/diagnose.md §7.1."""
    POLLS = ["как тут дела?", "Какой прогресс по миграции?", "Что у нас осталось?", "Как прошла ночь?",
             "Что случилось, пока меня не было?", "Ты уже смержил PR?", "Когда мы будем мигрировать мон?",
             "Сколько ждать очередь?", "На что ты ждёшь моё да?", "Какие ко мне вопросы?", "Чего так долго?",
             "Расскажи, что случилось", "Проанализируй сессии и скажи, какие ко мне вопросы",
             "Узнай у сессии X, всё ли готово", "Привет, я вернулся со спортзала. Как тут дела?", "ну что?"]
    NOT = {"Надо теперь донастроить алерты, что у нас тут осталось": "instruction_with_poll",
           "Делай следующий шаг, ты зачем меня ждешь?": "instruction_with_poll",
           "Какой сейчас статус по лимитам?": "system_status",
           "Мы продолжаем терять логи или нет?": "system_status",
           "Что делает DEFENDER?": None, "Смержи #12 после 14:00": None,
           "Прикол, а что с этим делать?": None}

    def test_polls(self):
        self.assertEqual([t for t in self.POLLS if M.status_poll_label(t) != "status_poll"], [])

    def test_not_polls(self):
        self.assertEqual({t: M.status_poll_label(t) for t in self.NOT}, self.NOT)

    def test_measure_and_labels(self):
        m = of_model.build(DOC, [], at="2026-11-06T12:00Z")
        rows = [prompt(1, "2026-11-05T10:00Z", "s-op", "как тут дела?"),
                prompt(2, "2026-11-05T11:00Z", "s-op", "смержи #12"),
                prompt(3, "2026-11-05T12:00Z", "s-k8s", "Ты уже смержил PR?"),
                prompt(4, "2026-11-05T13:00Z", "s-k8s", "Какой сейчас статус по лимитам?")]
        a, b = of_model.epoch("2026-11-05T00:00Z"), of_model.epoch("2026-11-06T00:00Z")
        r = M.status_polls(m, rows, a, b)
        self.assertEqual((r["polls"], r["owner_messages"], r["share"], r["system_status"]), (2, 4, 0.5, 1))
        self.assertEqual(r["by_position"]["bot_operator"], {"owner_messages": 2, "polls": 1, "share": 0.5})
        self.assertEqual(M.status_polls(m, rows, a, b, exclude={"k8s_keeper"})["polls"], 1)
        c = M.check_labels(r, {"e1": "status_poll", "e3": "question", "e2": "status_poll"})
        self.assertEqual((c["agree"], c["missed"], c["not_labelled_poll"], c["diff"]), (1, ["e2"], ["e3"], 0))


class EscalationTagTest(unittest.TestCase):
    def test_declared_tag(self):
        self.assertEqual(M.declared_tag({"tags": [{"tag": "window_go:obs", "source": "declared"}]}), "window_go")
        self.assertEqual(M.declared_tag({"tags": [{"tag": "money", "source": "tg-v0"}]}), "untagged")
        self.assertEqual(M.declared_tag({"tags": ["security_incident"]}), "security_incident")
        self.assertEqual(M.declared_tag({}), "untagged")
        fixes = {"33c005a6": "security_incident"}
        self.assertEqual(M.declared_tag({"event_id": "01a1-0000-33c005a6"}, fixes), "security_incident")
        self.assertEqual(M.declared_tag({"event_id": "01a1-0000-33c005a6", "tags": ["money"]}, fixes), "security_incident")
        self.assertEqual(M.declared_tag({"event_id": "01a1-0000-99999999"}, fixes), "untagged")
        self.assertEqual(M.declared_tag({"kind": "blocked:limit"}), "detected")
        self.assertEqual(M.declared_tag({"kind": "question (heuristic)"}), "detected")
        esc = {"kind": "question (declared)", "source": "escalated",
               "text": "[escalated from platform_owner: prod_change{affects:[net]}] 890025ce: file the report"}
        self.assertEqual(M.declared_tag(esc), "prod_change")
        self.assertEqual(M.declared_tag(dict(esc, text="[escalated: issue.severity >= high in unit platform] x")), "issue.severity")


class ChangePredictionTest(unittest.TestCase):
    def test_works_view_days(self):
        rows = [ev(1, "2026-11-13T14:00Z", "of.studio_viewed", None, {"view": "works"}),
                ev(2, "2026-11-13T18:00Z", "of.studio_viewed", None, {"view": "works"}),
                ev(3, "2026-11-14T09:00Z", "of.studio_viewed", None, {"view": "inbox"}),
                ev(4, "2026-11-15T09:00Z", "of.studio_viewed", None, {"view": "works"})]
        a, b = of_model.epoch("2026-11-13T13:40Z"), of_model.epoch("2026-11-20T13:40Z")
        self.assertEqual(of_studio.works_view_days(rows, a, b), 2)
        self.assertEqual(of_studio.works_view_days(rows, a, b, excluded={"e4"}), 1)            # the record excludes it
        agent = ev(5, "2026-11-16T09:00Z", "of.studio_viewed", None, {"view": "works", "requester": "agent:of_platform"})
        self.assertEqual(of_studio.works_view_days(rows + [agent], a, b), 2)                   # an agent's link

    def test_card(self):
        cr = {"file": "/r/org/reports/predictions/works-screen.yaml", "doc": {"review": "2026-11-20", "change": "Works v0"},
              "review_window": ["2026-11-13T13:40Z", "2026-11-20T13:40Z"], "opened": False, "ended": False,
              "rows": [{"metric": "judged_scope.share", "op": "<=", "value": 0.034, "baseline": 0.068, "baseline_ours": 0.068,
                        "observed": 0.061, "verdict": None, "why": "w"}],
              "precondition": {"metric": "works_view_days", "op": ">=", "value": 4}, "view_days": 0, "precondition_met": False,
              "cur": {"judged_scope": {"polls": 34, "owner_messages": 554, "share": 0.061, "instruction_with_poll": 5,
                                       "system_status": 2, "by_position": {"a": 6}}},
              "base": {"judged_scope": {"polls": 34, "owner_messages": 503, "share": 0.068}},
              "measure": {"name": "status-poll", "version": "sp-v1"}}
        h = of_studio._change_html(cr)
        self.assertIn("0.034", h)
        self.assertIn("not met so far", h)
        self.assertIn("34 of 503 (0.068)", h)


class RuleSurfaceTest(unittest.TestCase):
    """The detection rules are of_field-analyst's (bin/of_diagnose_rules.py); they read the model only through
    of_model.RULE_SURFACE. Changing a pinned parameter list here needs a note to of_field-analyst first."""

    PINNED = {"epoch": "(ts)", "iso": "(sec)", "in_window": "(row, a, b)", "is_received": "(r)",
              "received_labels": "(r)", "owner_prompt": "(row)", "session_index": "(positions)",
              "position_of": "(row, positions, sessions)", "session_positions": "(model, rows)",
              "message_senders": "(model, rows, a, b, slack_s=5.0)", "structure_keeper": "(model)",
              "overlay": "(model, max_systems=None)", "pr_key": "(pl)", "pr_attribution": "(events, positions, sessions)",
              "founder_messages": "(rows)", "message_categories": "(row)", "asks_of": "(model, rows, who='founder')",
              "little": "(asks, a, b)", "owner_measures": "(model, rows, a, b, who='founder')",
              "inbound_queues": "(model, rows, a, b, old_after_s=None)",
              "issue": "(cat, name, affected, evidence, summary, delta_m=0.0, critical=False, part=None, key=None)",
              "issue_id": "(rule, affected)", "spec_part": "(spec, key, part, default=None)", "loops": "(model)"}
    CONSTANTS = ("HUMAN", "BOT_AUTHOR", "MANAGEMENT_FUNCTIONS")

    def test_surface_is_pinned(self):
        import inspect
        self.assertEqual(set(of_model.RULE_SURFACE), set(self.PINNED) | set(self.CONSTANTS))
        for n, sig in self.PINNED.items():
            self.assertEqual(str(inspect.signature(getattr(of_model, n))), sig, n)
        for n in self.CONSTANTS:
            self.assertTrue(hasattr(of_model, n), n)

    def test_rules_read_only_the_surface(self):
        import ast
        path = os.path.join(os.path.dirname(__file__), "..", "bin", "of_diagnose_rules.py")
        tree = ast.parse(open(path).read())
        stdlib = getattr(sys, "stdlib_module_names", {"collections", "os", "re", "math", "statistics", "itertools"})
        surface = set(of_model.RULE_SURFACE)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.assertTrue(a.name == "of_model" or a.name.split(".")[0] in stdlib, a.name)
            elif isinstance(node, ast.ImportFrom):
                self.assertTrue(node.module == "of_model" or (node.module or "").split(".")[0] in stdlib, node.module)
                if node.module == "of_model":
                    self.assertLessEqual({a.name for a in node.names}, surface)
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "of_model":
                self.assertIn(node.attr, surface)

    def test_the_run_reads_every_rule(self):
        import of_diagnose_rules
        self.assertIs(of_diagnose.RULES, of_diagnose_rules.RULES)
        self.assertLessEqual(set(of_diagnose.RULES), set(of_diagnose.catalogue()["rules"]))


class PageCacheTest(unittest.TestCase):
    """of-server serves pages computed beforehand (docs/studio.md 5.1a): the first page in well under 2 s."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.orig = (of_studio._pages_dir, of_studio._refresh_in_background, of_studio.Data)
        of_studio._pages_dir = lambda: self.dir
        self.refreshes = []
        of_studio._refresh_in_background = lambda: self.refreshes.append(1) or True

    def tearDown(self):
        of_studio._pages_dir, of_studio._refresh_in_background, of_studio.Data = self.orig

    def put(self, org, view, age_s, show_all=False):
        path = of_studio._page_path(org, view, show_all)
        of_studio._write_page(path, f"<footer>Model at x<!--of:age--> · {org} {view}</footer>")
        t = time.time() - age_s
        os.utime(path, (t, t))
        return path

    def test_a_cached_page_is_served_without_computing(self):
        self.put("all", "inbox", 30)
        self.put("acme", "coverage", 30, show_all=True)

        def no_compute(*a, **k):
            raise AssertionError("computed although the page was cached")
        of_studio.Data = no_compute
        t = time.time()
        st, ct, body = of_studio.handle("GET", "/studio/", None, {"tenant": True})
        self.assertLess(time.time() - t, 0.5)
        self.assertEqual((st, body), (200, b"<footer>Model at x \xc2\xb7 all inbox</footer>"))
        st, _, body = of_studio.handle("GET", "/studio/coverage", "org=acme&all=1", {"tenant": True})
        self.assertIn(b"acme coverage", body)
        self.assertEqual(self.refreshes, [])                     # fresh: no refresh
        self.assertEqual(oct(os.stat(of_studio._page_path("all", "inbox")).st_mode & 0o777), "0o600")

    def test_a_stale_page_is_served_with_its_age_and_starts_one_refresh(self):
        self.put("acme", "owner", 3 * 3600)
        body, age = of_studio.cached_page("owner", {"org": "acme"})
        self.assertGreater(age, of_studio.PAGE_STALE_S)
        self.assertIn(b"computed 3 h ago, a newer one is being computed", body)
        self.assertEqual(self.refreshes, [1])

    def test_one_render_at_a_time(self):
        held = of_studio._render_lock()
        self.assertIsNotNone(held)
        try:
            self.assertEqual(of_studio.render_cache(orgs_file="/nonexistent"), "another render is running: nothing done")
            of_studio._refresh_in_background = self.orig[1]
            self.assertFalse(of_studio._refresh_in_background())        # nothing spawned beside a running render
        finally:
            held.close()
        again = of_studio._render_lock()                                 # released with the file
        self.assertIsNotNone(again)
        again.close()

    def test_what_the_cache_does_not_hold(self):
        self.put("acme", "works", 10)
        self.assertIsNone(of_studio.cached_page("works", {"org": "acme", "w": "rfc-1"}))   # a work's card
        self.assertIsNone(of_studio.cached_page("inbox", {"org": "acme", "all": "1"}))
        self.assertIsNone(of_studio.cached_page("waits", {"org": "acme"}))                 # not written yet


class LoopTest(unittest.TestCase):
    """RFC 0005: the loop metrics, derived from of.loop_* events, and the loop card."""
    NOW = "2026-11-08T00:00Z"
    SPEC = {"target": {"metric": "day pass USD", "money": "USD per day", "stake": "about 65 USD a day"},
            "measures": [{"name": "rows_differing", "to_money": "0 rows: no lost candidate"}],
            "ladder": [{"rung": "oracle", "cost": "0", "latency": "1 min", "accept": "0 differing rows"},
                       {"rung": "offline", "latency": "5 min"}, {"rung": "day_pass", "cost": "6 runner-h", "latency": "4 h"},
                       {"rung": "shadow", "latency": "1 day"}, {"rung": "all_sites"}],
            "checkpoint_days": 3, "wip_arms": 1}

    def ev(self, i, ts, name, pl):
        return ev(f"l{i}", ts, name, None, dict(pl, work="w1"), source="of")

    def rows(self):
        h = lambda i, ts, hid, voi, rh, **kw: self.ev(i, ts, "of.loop_hypothesis",
                                                    dict({"id": hid, "change": hid, "voi": voi, "cost": {"rung": "day_pass", "runner_h": rh}}, **kw))
        run = lambda i, ts, hid, rung, value=None, **kw: self.ev(i, ts, "of.loop_run", dict({"hypothesis": hid, "rung": rung, "value": value}, **kw))
        dec = lambda i, ts, hid, d: self.ev(i, ts, "of.loop_decision", {"hypothesis": hid, "decision": d})
        return [h(1, "2026-11-02T00:00Z", "h1", "high", 6), h(2, "2026-11-02T01:00Z", "h2", "low", 6),
                h(3, "2026-11-02T02:00Z", "h3", "high", 1), h(4, "2026-11-02T03:00Z", "h4", "med", 6),
                run(10, "2026-11-02T06:00Z", "h1", "offline", queued_at="2026-11-02T04:00Z", started_at="2026-11-02T05:00Z",
                    metric="rows_differing", value=0, runner_h=0),
                run(11, "2026-11-03T00:00Z", "h1", "day_pass", 211.7, started_at="2026-11-02T20:00Z", runner_h=6),
                dec(12, "2026-11-03T01:00Z", "h1", "keep"),
                run(13, "2026-11-04T00:00Z", "h2", "day_pass", 325.8, runner_h=6), dec(14, "2026-11-04T01:00Z", "h2", "keep"),
                run(15, "2026-11-05T00:00Z", "h4", "day_pass", 326.0, runner_h=6), dec(16, "2026-11-05T01:00Z", "h4", "drop"),
                run(17, "2026-11-06T00:00Z", "h4", "day_pass", 326.2, runner_h=6),
                run(18, "2026-11-07T00:00Z", "h4", "day_pass", 326.4, runner_h=6),
                self.ev(19, "2026-11-07T01:00Z", "of.loop_hypothesis", {"id": "h5", "change": "faster", "voi": "low",
                                                                        "cost": {"rung": "gate", "runner_h": 1}}),
                run(20, "2026-11-07T02:00Z", "h5", "gate", 41, metric="latency_p50_ms"),
                run(21, "2026-11-07T03:00Z", "h5", "gate", 38, metric="latency_p50_ms"),
                run(22, "2026-11-07T04:00Z", "h5", "gate", 35, metric="latency_p50_ms"),
                ev("l30", "2026-11-07T00:00Z", "of.loop_run", None, {"work": "other", "rung": "day_pass", "value": 1}, source="of")]

    def card(self, rows=None, spec=None):
        import of_loops
        return of_loops.project("w1", spec or self.SPEC, self.rows() if rows is None else rows, of_model.epoch(self.NOW))

    def test_metrics_are_derived_from_the_events(self):
        c = self.card()
        rung = {x["rung"]: x for x in c["ladder"]}
        self.assertEqual([x["rung"] for x in c["ladder"]], ["oracle", "offline", "day_pass", "shadow", "all_sites", "gate"])
        self.assertFalse(rung["gate"]["declared"])                         # a rung the ladder does not name still shows
        self.assertEqual((rung["offline"]["cycle_time_h"], rung["offline"]["wait_share"]), (2.0, 0.5))
        self.assertEqual(rung["day_pass"]["runs"], 5)
        self.assertEqual(c["highest"]["rung"], "day_pass")
        self.assertTrue(c["checkpoint"]["overdue"])                        # no shadow in 6 days, due every 3
        self.assertIsNone(c["checkpoint"]["last"])
        self.assertIsNone(c["time_to_first_money_days"])
        self.assertEqual((c["kept"], c["decided"], c["hit_rate"]), (2, 3, 0.667))
        self.assertEqual([p[1] for p in c["curve"]], [211.7, 325.8, 326.0, 326.2, 326.4])   # other works and metrics left out
        self.assertTrue(c["plateau"]["flat"])                              # 3 cycles under 1% each
        self.assertEqual(c["drift"], ["latency_p50_ms"])                   # the latest runs: no price in money
        self.assertEqual(c["cost_week"]["runner_h"], 30.0)
        self.assertEqual((c["cost_week"]["usd"], c["cost_week"]["runs_without_usd"]), (None, 9))   # nothing priced yet
        cost = lambda i, run, usd: self.ev(i, "2026-11-07T06:00Z", "of.loop_cost", {"run": run, "usd": usd, "fresh_tokens": 1000})
        c2 = self.card(self.rows() + [cost(50, "el17", 4.5), cost(51, "el18", 3.0), cost(52, "el20", None)])
        self.assertEqual((c2["cost_week"]["usd"], c2["cost_week"]["runs_without_usd"], c2["cost_week"]["tokens"]), (7.5, 7, 3000.0))
        self.assertEqual(c2["cost_week"]["usd_per_kept_cycle"], 3.75)   # h1 and h2 kept this week
        self.assertEqual([h["id"] for h in c["register"]["open"]], ["h3"])  # h5 is in test
        self.assertEqual(c["wip"], {"testing": 1, "limit": 1, "over": False})

    def test_register_order_and_production_checkpoint(self):
        import of_loops
        rows = self.rows()[:4] + [self.ev(40, "2026-11-06T00:00Z", "of.loop_run", {"hypothesis": "h1", "rung": "shadow", "result": "no loss path"})]
        c = self.card(rows)
        self.assertEqual([h["id"] for h in c["register"]["open"]], ["h3", "h4", "h2"])   # voi per runner-hour
        self.assertEqual(c["checkpoint"]["days_since"], 2.0)
        self.assertFalse(c["checkpoint"]["overdue"])
        self.assertEqual(c["time_to_first_money_days"], 4.0)
        self.assertIn("last production checkpoint 2 d ago", of_loops.summary(c, of_model.epoch(self.NOW)))
        self.assertIsNone(of_loops.project("nothing", None, rows, of_model.epoch(self.NOW)))   # no loop, no events

    def test_the_model_projects_loops_for_the_rules(self):
        doc = dict(DOC, works={"w1": {"title": "Search", "owner": "colleague", "doers": ["bot_search"], "loop": self.SPEC},
                               "w2": {"title": "No loop"}})
        chk = ev("m1", "2026-11-07T05:00Z", "of.mandate_check", "s-search",
                 {"verdict": "in_mandate", "command_class": "monitor", "model": "claude-opus-5-5"}, position="bot_search")
        rows = self.rows() + [chk]
        m = of_model.build(doc, rows, at=self.NOW)
        lp = of_model.loops(m)
        self.assertEqual(set(lp), {"w1", "other"})                        # a loop's events alone make it one
        w = lp["w1"]
        self.assertEqual((w["owner"], w["doers"], w["target"]["better"], w["babysit_model"]), ("colleague", ["bot_search"], "higher", "sonnet"))
        self.assertEqual([x["production"] for x in w["ladder_spec"]], [False, False, False, True, True])
        self.assertEqual(w["metrics"]["rungs"]["offline"], {"runs": 1, "cycle_time": 2.0, "wait_share": 0.5})
        self.assertEqual(w["metrics"]["wip"][:2], [(of_model.epoch("2026-11-02T06:00Z"), 1), (of_model.epoch("2026-11-03T01:00Z"), 0)])
        self.assertEqual(w["metrics"]["production"], {"last_at": None, "days_since": None})
        self.assertEqual(w["metrics"]["babysit"], {"bot_search": {"runner": {}, "monitor": {"claude-opus-5-5": 1}}})
        self.assertEqual(next(h for h in w["hypotheses"] if h["id"] == "h1")["status"], "kept")
        self.assertEqual({r["rung"] for r in w["runs"]}, {"offline", "day_pass", "gate"})
        # the run: inputs present, and the two deciders
        have = of_diagnose.present(m, rows, of_model.epoch("2026-11-01T00:00Z"), of_model.epoch(self.NOW), of_diagnose.catalogue())
        self.assertIn("registry.works.loop", have)
        self.assertIn("of.loop_run", have)
        self.assertNotIn("of.loop_run", of_diagnose.present(m, rows, of_model.epoch("2026-11-07T12:00Z"),
                                                            of_model.epoch(self.NOW), of_diagnose.catalogue()))
        mk = lambda dec: {"decider": dec, "evidence": {"work": "w1"}}
        self.assertEqual(of_diagnose.decider_position(m, mk("doer"), "keeper"), "bot_search")
        self.assertEqual(of_diagnose.decider_position(m, mk("work_owner"), "keeper"), "colleague")
        self.assertEqual(set(of_model.restrict(m, {"colleague", "k8s_keeper"})["loops"]), {"w1"})   # the owner's unit sees it

    def test_of_platform_names(self):
        """docs/events.md: a status update by hypothesis with status_at, a run id in `run`, a park decision."""
        rows = [self.ev(1, "2026-11-02T00:00Z", "of.loop_hypothesis", {"id": "h1", "change": "x", "voi": "high"}),
                self.ev(2, "2026-11-02T00:00Z", "of.loop_hypothesis", {"hypothesis": "h2", "change": "y"}),
                self.ev(3, "2026-11-03T00:05Z", "of.loop_hypothesis", {"id": "h1", "status": "testing", "status_at": "2026-11-03T00:00Z"}),
                self.ev(4, "2026-11-03T02:00Z", "of.loop_run", {"run": "r-1", "hypothesis": "h1", "rung": "offline"}),
                self.ev(5, "2026-11-04T00:00Z", "of.loop_decision", {"hypothesis": "h2", "decision": "park"})]
        c = self.card(rows)
        h = {x["id"]: x for x in c["hypotheses"]}
        self.assertEqual((h["h1"]["status"], h["h1"]["status_at"]), ("testing", of_model.epoch("2026-11-03T00:00Z")))
        self.assertEqual(h["h2"]["status"], "parked")
        self.assertEqual(c["runs"][0]["id"], "r-1")
        self.assertEqual([x["id"] for x in c["register"]["parked"]], ["h2"])

    def test_the_card_renders(self):
        c = self.card()

        class D:
            org, now, unit = "acme", of_model.epoch(self.NOW), None

            def loop_of(self, w):
                return c
        w = {"id": "w1", "title": "Search", "owner": "acme_throughput-owner"}
        html = of_studio._loop_card(D(), w) + of_studio._loops_table(D(), [(w, c)])
        for want in ("Development loop", "The latest runs measure latency_p50_ms", "plateau window: flat",
                     "No production checkpoint in the 6 d", "Hit rate 67% (2 of 3 decided)", "30 runner-h", "no cost recorded yet",
                     "production checkpoint overdue", "highest rung day_pass", "acme_throughput-owner"):
            self.assertIn(want, html)


class StandingWorkTest(unittest.TestCase):
    """A standing work (docs/registry.md, of_architect 2026-11-10): never derived as done; progress is the loop
    target's trend by checkpoint, not stages; past review only when its last loop decision is older than
    checkpoint_days."""

    def setUp(self):
        import tempfile
        import yaml
        self.tmp = tempfile.TemporaryDirectory()
        repo = os.path.join(self.tmp.name, "org")
        os.makedirs(os.path.join(repo, "registry"))
        base = {"title": "Search", "kind": "project", "doc": "arb:docs/x.md", "systems": ["bot"], "owner": "bot_operator",
                "doers": ["bot_search"], "review": "2026-11-03",
                "stages": [{"id": "s1", "done_when": "x", "state": "done"}], "loop": LoopTest.SPEC}
        with open(os.path.join(repo, "registry", "works.yaml"), "w") as fh:
            yaml.safe_dump({"works": {"w1": dict(base, standing=True), "w2": base}}, fh)
        self.org = {"name": "acme", "repo": repo, "paths": [self.tmp.name]}

    def tearDown(self):
        self.tmp.cleanup()

    def works(self, now):
        import of_works
        rows = LoopTest().rows()
        m = of_model.build(DOC, rows, at=now)
        return {w["id"]: w for w in of_works.rows_for(m, rows, self.org, of_model.epoch(now))}

    def test_trend_instead_of_stages_and_review_by_decision(self):
        ws = self.works("2026-11-08T00:00Z")
        w, plain = ws["w1"], ws["w2"]
        self.assertTrue(w["standing"])
        self.assertNotEqual(w["state"], "done")
        self.assertEqual((w["stages"], w["stage_summary"]), ([], None))
        self.assertEqual(plain["stage_summary"], "1 of 1 done")
        self.assertEqual(w["trend_summary"], "day pass USD: 325.8 → 326.4 by 3-day checkpoint (+0.2%)")
        self.assertEqual([c["best"] for c in w["trend"]["checkpoints"]], [325.8, 326.4])
        self.assertFalse(w["past_review"])        # the last decision (11-05 01:00) is under 3 days old
        self.assertTrue(plain["past_review"])     # its review date passed with no progress
        self.assertTrue(self.works("2026-11-08T02:00Z")["w1"]["past_review"])

    def test_row_and_card(self):
        now = of_model.epoch("2026-11-08T00:00Z")
        rows = LoopTest().rows()
        d = of_studio.Data.__new__(of_studio.Data)
        d.orgs, d.org, d.now, d.rows, d.unit = ["acme"], "acme", now, rows, None
        d.a, d.b = now - 7 * 86400, now
        d.compute(of_model.build(DOC, rows, at=now, org="acme", rev="x", window_h=168), None)
        w = self.works("2026-11-08T00:00Z")["w1"]
        row, card = of_studio._work_row(w, d), of_studio._work_card(d, w)
        self.assertIn(" · standing</td>", row)
        self.assertIn("day pass USD: 325.8 → 326.4 by 3-day checkpoint (+0.2%)", row)
        for want in ("standing: no final end", "a checkpoint every 3 d; last loop decision 2 d ago",
                     "The target by checkpoint", "+0.2% towards better"):
            self.assertIn(want, card)
        self.assertNotIn("<h2>Stages</h2>", card)

    def test_lower_is_better(self):
        import of_loops
        t0 = of_model.epoch("2026-11-01T00:00Z")
        tr = of_loops.checkpoint_trend([[t0 + 3600, 41, "gate", "h"], [t0 + 4 * 86400, 35, "gate", "h"]], t0,
                                       t0 + 5 * 86400, 3, "lower")
        self.assertEqual((tr["change"], tr["pct"]), (6, round(6 / 41, 4)))


def correction(i, ts, sid=None, position=None, repeat_of=None, rule="say the money metric before a speed change", **pl):
    payload = dict({"position": position, "rule": rule, "kind": "process", "repeat_of": repeat_of}, **pl)
    return dict(ev(i, ts, "of.founder_correction", sid, {k: v for k, v in payload.items() if v is not None}), source="of")


class CorrectionTest(unittest.TestCase):
    """The repeat-correction rate per position and ISO week (RFC 0006 §2.4, step 5)."""
    B = "2026-11-05T00:00Z"   # a Thursday: its ISO week, 2026-W45, began on Monday 2026-11-02

    def rows(self):
        return [
            correction(1, "2026-09-01T10:00Z", position="bot_search"),                    # before the weeks shown
            correction(2, "2026-10-28T10:00Z", position="bot_search", prompt_id="p2"),    # W44
            correction(3, "2026-11-03T10:00Z", position="bot_search", repeat_of="e2", prompt_id="p3"),
            correction(4, "2026-11-03T10:05Z", position="bot_search", repeat_of="e2", prompt_id="p3"),  # the judge retried
            correction(5, "2026-11-04T09:00Z", position="bot_search", rule="other"),
            correction(6, "2026-11-04T11:00Z", sid="s-k8s"),                                # the session's position
            correction(7, "2026-11-05T00:00Z", position="bot_search", repeat_of="e2"),     # at b: not yet
        ]

    def test_rate_per_position_and_week(self):
        m = of_model.build(DOC, [], at=self.B)
        rc = M.repeat_corrections(m, self.rows(), self.B, weeks=2)
        self.assertEqual(rc["weeks"], ["2026-W44", "2026-W45"])
        self.assertEqual(rc["from"], "2026-10-26T00:00:00Z")
        self.assertTrue(rc["recorded"])
        bs = rc["positions"]["bot_search"]
        self.assertEqual(bs["weeks"]["2026-W44"], {"n": 1, "repeats": 0, "rate": 0.0})
        self.assertEqual(bs["weeks"]["2026-W45"], {"n": 2, "repeats": 1, "rate": 0.5})   # e4 is e3 again
        self.assertEqual((bs["n"], bs["repeats"], bs["rate"]), (3, 1, 0.333))
        self.assertEqual(bs["repeated_rules"], [("say the money metric before a speed change", 1)])
        self.assertEqual(rc["positions"]["k8s_keeper"]["weeks"]["2026-W45"]["n"], 1)
        self.assertEqual(rc["total"]["2026-W45"], {"n": 3, "repeats": 1, "rate": 0.333})

    def test_alias_counts_as_its_position(self):
        doc = dict(DOC, positions=[dict(p, aliases=["searcher"]) if p["name"] == "bot_search" else p
                                   for p in DOC["positions"]])
        m = of_model.build(doc, [], at=self.B)
        rc = M.repeat_corrections(m, [correction(1, "2026-11-03T10:00Z", position="searcher")], self.B)
        self.assertEqual(list(rc["positions"]), ["bot_search"])

    def test_owner_page_table_and_unit_view(self):
        d = StudioTest().data(self.rows())
        html = of_studio.render("owner", d)
        self.assertIn("Repeat corrections", html)
        self.assertIn('<td title="1 of 2">50.0% <span class="src">1/2</span></td>', html)
        self.assertIn("say the money metric before a speed change ×1", html)
        unit = of_studio.render("owner", StudioTest().data(self.rows(), unit="colleague"))
        self.assertIn("k8s_keeper", unit.split("Repeat corrections")[1])
        self.assertNotIn("bot_search", unit.split("Repeat corrections")[1])

    def test_cli_table(self):
        import contextlib
        import io
        from unittest import mock
        import of_studio_cli
        m = of_model.build(DOC, [], at=self.B, org="acme")
        out = io.StringIO()
        with mock.patch.object(of_studio_cli, "_model", lambda args, rows: ({"name": "acme"}, m, self.rows())), \
                contextlib.redirect_stdout(out):
            of_studio_cli.main(["model", "corrections", "--weeks", "2"])
        text = out.getvalue()
        self.assertIn("acme: repeat corrections (rc-v0), 2026-10-26", text)
        self.assertRegex(text, r"bot_search\s+0% 0/1\s+50% 1/2\s+33% 1/3")
        self.assertRegex(text, r"all\s+0% 0/1\s+33% 1/3\s+25% 1/4")

    def test_owner_page_before_the_judge_writes(self):
        html = of_studio.render("owner", StudioTest().data())
        self.assertIn("Not recorded yet", html)


class JudgedAskTest(unittest.TestCase):
    def test_the_judges_no_is_not_an_ask(self):
        rows = [question(1, "2026-11-04T10:00Z", "s-search"),
                {"event_id": "j1", "ts": "2026-11-04T10:00:20Z", "name": "of.agent_question_judged", "session_id": "s-search",
                 "position": None, "cwd": "~/acme", "source": "of", "payload": {"ask_id": "e1", "verdict": "not_a_question"}},
                question(2, "2026-11-04T12:00Z", "s-op")]   # not judged yet: counted, as before the judge
        m = of_model.build(DOC, [], at=MeasuresTest.B)
        self.assertEqual([k["event_id"] for k in M.asks_of(m, rows)], ["e2"])
        self.assertEqual(M.owner(m, rows, MeasuresTest.A, MeasuresTest.B)["arrivals"], 1)


if __name__ == "__main__":
    unittest.main()


class SystemsEvidenceTest(unittest.TestCase):
    """RFC 0007 step 4: each system's evidence, the cells view, and the code activity as model output."""
    B = "2026-11-05T00:00Z"

    def doc(self):
        own = {"bot_operator": [{"system": "bot", "method": "build", "decides": True},
                                {"system": "bot", "method": "operate", "decides": True}],
               "bot_search": [{"system": "bot", "method": "build"}, {"system": "bot", "method": "verify"},
                              {"system": "bot", "method": "pricing"}, {"system": "nosuch", "method": "build"}],
               "k8s_keeper": [{"system": "bot", "method": "build"}, {"system": "k8s", "method": "operate", "decides": True},
                              {"system": "k8s", "method": "cook"}],
               "colleague": [{"system": "k8s", "method": "operate", "decides": True}],
               "data_keeper": [{"system": "data", "method": "data", "decides": True}]}
        systems = dict(DOC["systems"])
        systems["bot"] = dict(systems["bot"], needs=["build", "verify", "operate", "researching"],
                              runs={"where": "k8s shop/bot", "evidence": "infra:values.yaml"},
                              money="P&L line: storefront sales revenue", confirmed={"keeper": "abcd1234"})
        systems["k8s"] = dict(systems["k8s"], needs=["operate"], confirmed={"keeper": "zzzz9999"}, runs="nowhere")
        systems["data"] = dict(systems["data"], needs=["data"], governed_by="external:dana")
        systems["old"] = {"kind": "target", "lifecycle": "retired", "needs": ["build"]}
        return dict(DOC, methods={"pricing": "prices and bids"}, systems=systems,
                    positions=[dict(p, owns=own.get(p["name"], [])) for p in DOC["positions"]])

    def rows(self):
        def wr(i, ts, num, repo, paths=None, sid=None, author="vp", **kw):
            pl = {"source": "gh", "repo": f"o/{repo}", "number": num, "author": author, "merged_at": ts, "title": "t"}
            if paths is not None:
                pl["paths"] = paths
            pl.update(kw)
            return ev(i, ts, "of.work_record", sid, pl)

        def prs(i, ts, num, state, **kw):
            return ev(i, ts, "of.pr_state", None, dict({"repo": "o/arb", "number": num, "state": state, "author": "vp"}, **kw))
        return [
            wr(1, "2026-11-01T10:00Z", 1, "arb", ["crates/bot/src/a.rs", "crates/bot/src/b.rs"], sid="s-op"),
            wr(2, "2026-11-02T10:00Z", 2, "infra", ["k8s/x.yaml"], author="dana",
               touched=[{"repo": "arb", "path": "crates/bot/c.rs"}]),
            wr(3, "2026-11-02T11:00Z", 3, "arb", ["crates/bot/d.rs"], author="dependabot[bot]"),
            ev(4, "2026-11-03T09:00Z", "of.work_record", "s-search", {"source": "pr-link", "url": "https://github.com/o/arb/pull/4"}),
            wr(5, "2026-11-03T10:00Z", 4, "arb", ["crates/bot/z.rs"]),
            wr(6, "2026-09-01T10:00Z", 6, "arb", ["crates/bot/old.rs"], sid="s-op"),    # before the 30 days
            prs(7, "2026-11-03T10:00Z", 7, "open", systems=["bot"]),
            prs(8, "2026-11-04T10:00Z", 7, "closed", systems=["bot"]),                    # closed since: not open
            prs(9, "2026-11-04T10:00Z", 9, "open", paths=["crates/bot/q.rs"]),
            wr(10, "2026-11-04T12:00Z", 10, "arb", ["docs/readme.md"]),                     # no system claims it
        ]

    def model(self):
        return of_model.build(self.doc(), [], at=self.B, org="acme")

    def test_registry_fields(self):
        m = self.model()
        self.assertEqual(m["systems"]["bot"]["runs"], {"where": "k8s shop/bot", "evidence": "infra:values.yaml"})
        self.assertEqual(m["systems"]["k8s"]["runs"], {"where": "nowhere", "evidence": None})
        self.assertEqual(m["systems"]["bot"]["confirmed"], {"keeper": "abcd1234"})
        self.assertEqual(m["positions"]["bot_operator"]["owns"][0], {"system": "bot", "method": "build", "decides": True})
        import json
        self.assertNotIn("paths", json.loads(of_model.snapshot(m))["systems"]["bot"])   # no paths in snapshots

    def test_cells_gaps_overlaps_and_unknown(self):
        c = self.model()["cells"]
        self.assertIn("pricing", c["methods"])
        gaps = {(r["system"], r["method"]): r["gap"] for r in c["gaps"]}
        self.assertEqual(gaps, {("bot", "verify"): "no_decider", ("data", "data"): "nobody"})   # not old/build: retired
        data = next(r for r in c["cells"] if (r["system"], r["method"]) == ("data", "data"))
        self.assertEqual(data["inactive"], ["data_keeper"])                     # a parked decider owns nothing
        self.assertEqual([(r["system"], r["method"], r["doers"]) for r in c["overlaps"]],
                         [("bot", "build", ["bot_operator", "bot_search", "k8s_keeper"])])
        self.assertEqual([r["deciders"] for r in c["two_deciders"]], [["colleague", "k8s_keeper"]])
        pricing = next(r for r in c["cells"] if r["method"] == "pricing")
        self.assertEqual((pricing["needed"], pricing["gap"]), (False, None))    # held, not needed: never a gap
        self.assertEqual(sorted((u["position"] or "", u["why"]) for u in c["unknown"]),
                         [("", "unknown kind of work in needs"), ("bot_search", "unknown system"),
                          ("k8s_keeper", "unknown kind of work")])

    def test_governance_facts_and_ask_ids(self):
        ev_ = of_model.evidence(self.model(), None, {"abcd1234-0000-7000-8000-000000000001"})
        state = lambda sid: {f["fact"]: f["state"] for f in ev_[sid]["governance"]}
        self.assertEqual(state("bot"), {"keeper": "confirmed"})
        self.assertEqual(state("k8s"), {"keeper": "ask_not_found"})
        self.assertEqual(state("data"), {"keeper": "unconfirmed", "governed_by": "unconfirmed"})
        self.assertEqual(state("old"), {"lifecycle": "unconfirmed"})
        self.assertEqual(state("trading"), {})
        self.assertEqual((ev_["k8s"]["unconfirmed"], ev_["bot"]["unconfirmed"]), (1, 0))
        self.assertEqual(of_model.evidence(self.model())["k8s"]["governance"][0]["state"], "confirmed")   # not checked

    def test_code_activity(self):
        b = of_model.epoch(self.B)
        act = of_model.code_activity(self.model(), self.rows(), b - 30 * 86400, b, areas_depth=2)
        bot, k8s = act["systems"]["bot"], act["systems"]["k8s"]
        self.assertEqual((act["records"], act["unmapped"], act["bots"]), (4, 1, 1))
        self.assertEqual((bot["merged"], bot["open"], bot["unattributed"]), (3, 1, 1))
        self.assertEqual(bot["writers"], {"bot_operator": 1, "bot_search": 1})    # bot_search by its pr-link
        self.assertEqual((k8s["merged"], k8s["authors"]), (1, {"dana": 1}))
        self.assertEqual(bot["last"], "2026-11-03T10:00:00Z")
        self.assertEqual(act["co_change"], [{"a": "bot", "b": "k8s", "prs": 1, "weighted": 1.0, "wide": 0}])
        self.assertEqual(act["areas"]["arb/crates/bot"]["records"], 3)
        self.assertEqual((act["areas"]["arb/crates/bot"]["system"], act["areas"]["infra/k8s"]["system"],
                          act["areas"]["arb/docs"]["system"]), ("bot", "k8s", None))
        self.assertEqual([(p["a"], p["b"], p["prs"]) for p in act["area_co_change"]], [("arb/crates/bot", "infra/k8s", 1)])

    def data(self, unit=None):
        d = of_studio.Data.__new__(of_studio.Data)
        d.orgs, d.org, d.now = ["acme"], "acme", of_model.epoch(self.B)
        d.a, d.b = d.now - 7 * 86400, d.now
        d.rows, d.unit = self.rows(), unit
        d.compute(of_model.build(self.doc(), d.rows, at=d.now, org="acme", rev="undated:x", window_h=168), unit)
        return d

    def test_systems_page(self):
        d = self.data()
        html = of_studio.render("systems", d)
        self.assertIn('<a class="tab on" href="/studio/systems?org=acme">Systems</a>', html)
        self.assertIn("k8s shop/bot", html)
        self.assertIn("P&amp;L line: storefront sales revenue", html)
        self.assertIn("3 merged · 1 open", html)
        self.assertIn("bot_operator 1, bot_search 1", html)
        self.assertIn('title="dana 1">1 by no position', html)
        self.assertIn("<b>keeper</b> bot_operator <span class=\"src\">✓ ask abcd1234</span>", html)  # not checked here
        self.assertIn("<b>governed_by</b> external:dana <span class=\"flag\"", html)
        self.assertIn("ask ids not checked against the store here", html)
        self.assertIs(d.code_activity(), d.code_activity())   # computed once per page
        d.full["systems"]["k8s"]["paths"] = ["arb/crates/bot/**"]   # k8s_keeper's unit, written by the founder's positions
        d.full["systems"]["bot"]["paths"] = ["arb/none/**"]
        unit = self.data(unit="colleague")
        unit.full = d.full
        unit.__dict__.pop("_code_activity", None)
        html = of_studio.render("systems", unit)
        self.assertIn("outside the unit 2", html)
        self.assertNotIn("bot_operator", html)

    def test_cells_page_and_unit_view(self):
        html = of_studio.render("cells", self.data())
        self.assertIn('title="bot · verify: needed · no decider · doers: bot_search">1</td>', html)
        self.assertIn('<td class="cell w" title="bot · build', html)
        self.assertIn("doers, no decider", html)
        self.assertIn("Two deciders on one cell", html)
        unit = of_studio.render("cells", self.data(unit="colleague"))
        self.assertIn("k8s_keeper, +2 outside the unit", unit)      # bot · build: its own doer, the others counted
        self.assertNotIn("bot_search", unit)
        self.assertNotIn("bot_operator", unit)
        empty = StudioTest().data()
        self.assertIn("No system lists <code>needs:</code>", of_studio.render("cells", empty))

    def test_cli_cells_and_evidence(self):
        import contextlib
        import io
        from unittest import mock
        import of_studio_cli
        m = self.model()
        with mock.patch.object(of_studio_cli, "_model", return_value=({"name": "acme"}, m, self.rows())), \
                mock.patch.object(of_studio_cli, "_code_rows", return_value=[]), \
                mock.patch.object(of_studio_cli, "_ask_ids", return_value=None):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                of_studio_cli.main(["model", "cells", "--org", "acme"])
                of_studio_cli.main(["model", "evidence", "--org", "acme", "--json"])
                of_studio_cli.main(["model", "activity", "--org", "acme", "--json", "--areas", "2"])
        text = out.getvalue()
        self.assertIn("needed, no owner: 2", text)
        self.assertIn("bot     verify  no_decider", text)
        self.assertIn("unknown system", text)
        self.assertIn('"where": "k8s shop/bot"', text)
        self.assertIn('"area_co_change"', text)


class KeeperHistoryTest(unittest.TestCase):
    """keeper_since reads the registry's history once: each revision's keepers persist on disk by commit id."""

    def test_keeper_since_and_disk_cache(self):
        import subprocess
        import tempfile
        from unittest import mock
        repo = tempfile.mkdtemp()
        git = lambda *a, **kw: subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", "-C", repo, *a],
                                              check=True, capture_output=True, env=dict(os.environ, **kw))
        git("init", "-q")
        os.makedirs(os.path.join(repo, "registry"))
        for i, (keeper, broken) in enumerate([("a", False), ("b", False), ("b", True), ("b", False)]):
            with open(os.path.join(repo, "registry", "positions.yaml"), "w") as fh:
                fh.write(f"organization: x\nsystems:\n  s1:\n    keeper: {keeper}\n\n  s2: {{keeper: z}}\n"
                         f"positions:\n  - name: {keeper}\n" + ("    bad: [unclosed\n" if broken else ""))
            when = f"2026-11-0{i + 1}T00:00:00Z"
            git("add", "-A")
            git("commit", "-q", "-m", f"r{i}", GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when,
                GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")
        of_model._registry_history.cache_clear()
        self.assertEqual(of_model.keeper_since(repo, "s1", "b"), of_model.epoch("2026-11-02T00:00Z"))
        self.assertEqual(of_model.keeper_since(repo, "s2", "z"), of_model.epoch("2026-11-01T00:00Z"))
        self.assertIsNone(of_model.keeper_since(repo, "s1", "a"))         # not its keeper now
        self.assertTrue(os.path.exists(of_model._keepers_cache_path(repo, "registry/positions.yaml")))
        of_model._registry_history.cache_clear()                          # a new process: nothing parsed again
        with mock.patch.object(of_model, "_keepers_of", side_effect=AssertionError("parsed again")):
            self.assertEqual(of_model.keeper_since(repo, "s1", "b"), of_model.epoch("2026-11-02T00:00Z"))


class UnitUsageTest(unittest.TestCase):
    """The delegated units' usage refresh reads transcripts forward from saved offsets, inside a time budget."""

    def setUp(self):
        import tempfile
        self.dirs = [tempfile.mkdtemp(), tempfile.mkdtemp()]
        for p in (of_studio._usage_index_path(), of_studio._usage_cache_path()):
            if os.path.exists(p):
                os.remove(p)

    def index(self):
        import json
        with open(of_studio._usage_index_path()) as fh:
            return json.load(fh)

    def line(self, mid, ts, inp=10, out=5, w1=20, model=None):
        import json
        return json.dumps({"timestamp": ts, "message": {"id": mid, **({"model": model} if model else {}), "usage": {
            "input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": 100,
            "cache_creation": {"ephemeral_1h_input_tokens": w1, "ephemeral_5m_input_tokens": 0}}}}) + "\n"

    def write(self, d, rel, text, mode="a"):
        path = os.path.join(d, "projects", "p", rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode) as fh:
            fh.write(text)
        return path

    def test_incremental_equals_full_read(self):
        from unittest import mock
        import of_capacity
        a, b = self.dirs
        main = self.write(a, "s1.jsonl", self.line("m1", "2026-11-03T10:00:00Z") * 2 + self.line("m0", "2026-10-30T10:00:00Z")
                          + '{"type": "user"}\n' + self.line("m2", "2026-11-03T11:00:00Z", inp=7))
        self.write(a, "s1/subagents/agent-x.jsonl", self.line("m3", "2026-11-03T12:00:00Z", out=50))
        self.write(b, "s1.jsonl", self.line("m1", "2026-11-03T10:00:00Z") + self.line("m2", "2026-11-03T11:00:00Z", inp=7))  # a move's copy
        self.write(a, "s9.jsonl", self.line("m9", "2026-11-03T10:00:00Z"))             # not a unit's session
        start = of_model.epoch("2026-11-01T00:00Z")
        with mock.patch.object(of_capacity, "config_dirs", return_value=[a, b]):
            self.assertTrue(of_studio._usage_advance("2026-11", start, {"s1"}, time.time() + 30))
            full = of_capacity.usage(dirs=[a, b], since=start, sessions={"s1"})["s1"]
            got = self.index()
            self.assertEqual(got["sessions"]["s1"]["fresh"], full["fresh"])           # m1 once, m2, m3; not m0, not m9
            self.assertAlmostEqual(got["sessions"]["s1"]["cost_usd"], full["cost_usd"])
            self.assertEqual(sorted(got["ids"]), ["m1", "m2", "m3"])
            self.write(a, "s1.jsonl", self.line("m4", "2026-11-04T10:00:00Z", inp=1000) + '{"partial": ')   # one more, one half-written
            with mock.patch.object(of_capacity, "usage_cost", wraps=of_capacity.usage_cost) as cost:
                self.assertTrue(of_studio._usage_advance("2026-11", start, {"s1"}, time.time() + 30))
                self.assertEqual(cost.call_count, 1)                                   # only m4 is read
            got2 = self.index()
            self.assertEqual(got2["sessions"]["s1"]["fresh"], full["fresh"] + 1000 + 5 + 20)
            self.assertEqual(got2["files"][main]["offset"], os.path.getsize(main) - len('{"partial": '))

    def test_deadline_keeps_progress_and_old_numbers(self):
        from unittest import mock
        import of_capacity
        a = self.dirs[0]
        self.write(a, "s1.jsonl", self.line("m1", "2026-11-03T10:00:00Z"))
        self.write(a, "s2.jsonl", self.line("m2", "2026-11-03T10:00:00Z"))
        model = {"org_units": {"u1": {"delegation": {"id": "d1"}}}}
        now = of_model.epoch("2026-11-05T00:00Z")
        with mock.patch.object(of_capacity, "config_dirs", return_value=[a]), \
                mock.patch.object(of_model, "session_positions", return_value={"s1": "p1", "s2": "p1"}), \
                mock.patch.object(of_diagnose, "unit_members", return_value=(set(), {"p1"})):
            self.assertEqual(of_studio.unit_usage(model, [], now, refresh=True, budget_s=-1), {})   # out of time: no numbers yet
            self.assertEqual(len(self.index()["files"]), 1)   # one file read before the deadline
            got = of_studio.unit_usage(model, [], now, refresh=True, budget_s=30)
        self.assertEqual((got["u1"]["sessions"], got["u1"]["fresh"]), (2, 70))


    def test_usage_per_position_day_and_model(self):
        """`of model usage` (a user organization's structure keeper, 2026-11-11): a unit's month per position, per UTC day and per model, the same
        count as the unit's budget row; an index of the old shape is read again; a partial index prints a gap."""
        import contextlib
        import io
        import json
        from unittest import mock
        import of_capacity
        import of_studio_cli
        a = self.dirs[0]
        self.write(a, "s1.jsonl", self.line("m1", "2026-11-03T10:00:00Z", model="claude-opus-5-5")
                   + self.line("m2", "2026-11-04T23:59:00Z", inp=100, model="claude-haiku-5-5"))
        self.write(a, "s2.jsonl", self.line("m3", "2026-11-04T01:00:00Z", out=500, model="claude-opus-5-5"))
        self.write(a, "s3.jsonl", self.line("m4", "2026-11-04T01:00:00Z"))                 # p3: another unit
        os.makedirs(os.path.dirname(of_studio._usage_index_path()), exist_ok=True)
        with open(of_studio._usage_index_path(), "w") as fh:                                 # the old shape, read to the end
            json.dump({"month": "2026-11", "files": {os.path.join(a, "projects", "p", f"s{i}.jsonl"): {"offset": 10 ** 6}
                                                     for i in (1, 2, 3)}, "ids": [], "sessions": {"s1": {"fresh": 1}}}, fh)
        model = {"org_units": {"u1": {"delegation": {"id": "d1", "to_owner": "boss",
                                                     "envelope": {"budget": {"tokens_month": 1000, "usd_month": 2}}}},
                               "u2": {"delegation": {"id": "d2"}}}}
        now = of_model.epoch("2026-11-05T00:00Z")
        members = {"u1": (set(), {"p1", "p2"}), "u2": (set(), {"p3", "p2"})}                   # p2 in both units
        with mock.patch.object(of_capacity, "config_dirs", return_value=[a]), \
                mock.patch.object(of_model, "session_positions", return_value={"s1": "p1", "s2": "p2", "s3": "p3"}), \
                mock.patch.object(of_diagnose, "unit_members", side_effect=lambda m, uid: members[uid]):
            self.assertFalse(of_studio.usage_by_position(model, [], now)["complete"])          # old shape: not read yet
            u = of_studio.usage_by_position(model, [], now, units=["u1"], refresh=True, budget_s=30)
            self.assertTrue(u["complete"])
            self.assertEqual(list(u["units"]), ["u1"])
            r = u["units"]["u1"]
            self.assertEqual((r["owner"], r["sessions"], r["budget_tokens"], r["budget_usd"]), ("boss", 2, 1000, 2))
            p1, p2 = r["positions"]["p1"], r["positions"]["p2"]
            self.assertEqual(p1["fresh"], 35 + 125)                                            # m1 and m2, re-read from 0
            self.assertEqual({d: v["fresh"] for d, v in p1["days"].items()}, {"2026-11-03": 35, "2026-11-04": 125})
            self.assertEqual(p1["models"], {"claude-opus-5-5": 35, "claude-haiku-5-5": 125})
            self.assertEqual((p2["fresh"], p2["also_in"], p1["also_in"]), (530, ["u2"], []))
            self.assertEqual(r["fresh"], 35 + 125 + 530)
            budget = of_studio.unit_usage(model, [], now, refresh=True, budget_s=30)            # the budget row's count
            self.assertEqual(budget["u1"]["fresh"], r["fresh"])
            self.assertAlmostEqual(budget["u1"]["cost_usd"], r["cost_usd"], places=2)
            out = io.StringIO()
            with mock.patch.object(of_studio_cli, "_model", return_value=({"name": "acme"}, model, [])), \
                    mock.patch.object(of_studio_cli, "_rows", return_value=[]), \
                    mock.patch.object(of_studio.time, "time", return_value=now), contextlib.redirect_stdout(out):
                of_studio_cli.main(["model", "usage", "--unit", "u1"])
            text = out.getvalue()
            self.assertIn("u1 (owner boss): 690 tokens of 1,000 (69.0%)", text)
            self.assertRegex(text, r"p2\* +1 +530 +76.8%")
            self.assertIn("* also in p2: u2 (counted in each unit)", text)                    # --unit u1 still says it
            self.assertIn("haiku-5-5 78.1%, opus-5-5 21.9%", text)                           # p1: 125 and 35 of 160
            self.assertRegex(text, r"11-04 +655 +[0-9.]+ +p2")                                # the unit's day, its top position
            out = io.StringIO()
            with mock.patch.object(of_studio_cli, "_model", return_value=({"name": "acme"}, model, [])), \
                    mock.patch.object(of_studio_cli, "_rows", return_value=[]), \
                    mock.patch.object(of_studio.time, "time", return_value=now), contextlib.redirect_stdout(out):
                of_studio_cli.main(["model", "usage", "--per-day"])
            self.assertRegex(out.getvalue(), r"11-04 +p1 +125 ")
            self.assertRegex(out.getvalue(), r"11-04 +p3 +35 ")                               # u2's position, its own table
            idx = self.index()
            idx["complete"] = False
            with open(of_studio._usage_index_path(), "w") as fh:
                json.dump(idx, fh)
            out = io.StringIO()
            with mock.patch.object(of_studio_cli, "_model", return_value=({"name": "acme"}, model, [])), \
                    mock.patch.object(of_studio_cli, "_rows", return_value=[]), \
                    mock.patch.object(of_studio.time, "time", return_value=now), contextlib.redirect_stdout(out):
                of_studio_cli.main(["model", "usage"])
            self.assertTrue(out.getvalue().startswith("gap: the usage index for 2026-11 has not read every transcript"))
            self.assertNotIn("tokens of", out.getvalue())


class IssuesAcrossOrgsTest(unittest.TestCase):
    """`of issues` lists and labels every organization's issues (of_architect, 2026-11-11: an OF team issue from the
    keeper digest answered "no single open issue matches")."""

    def issue(self, iid, org=None):
        return dict({"issue_id": iid, "state": "open", "decider": "owner", "decider_position": None, "rule": "ap.r",
                     "rule_version": "ap.r@0", "summary": "s"}, **({"org": org} if org else {}))

    def test_model_issues_org_all(self):
        import io
        import json
        from contextlib import redirect_stdout
        from unittest import mock
        import of_studio_cli
        per = {"acme": [self.issue("aaa111"), self.issue("same00")], "team": [self.issue("bbb222"), self.issue("same00")]}
        orgs = [{"name": "acme", "repo": "/nonexistent", "paths": []}, {"name": "team", "repo": "/nonexistent", "paths": []}]
        diag = lambda m, rows, a, b, context=None: {"issues": per[m["org"]], "rules": [], "structure_keeper": m["org"] + "_org"}
        out = io.StringIO()
        with mock.patch.object(of_studio, "orgs", return_value=orgs), \
                mock.patch.object(of_studio_cli, "_rows", return_value=[]), \
                mock.patch.object(of_studio_cli, "_model", lambda args, rows: ({"name": args.org, "repo": "/nonexistent"}, {"org": args.org}, [])), \
                mock.patch.object(of_diagnose, "diagnose", diag), redirect_stdout(out):
            of_studio_cli.main(["model", "issues", "--org", "all", "--json"])
            got = json.loads(out.getvalue())
            out.seek(0), out.truncate()
            of_studio_cli.main(["model", "issues", "--org", "team", "--json"])
            one = json.loads(out.getvalue())
            out.seek(0), out.truncate()
            of_studio_cli.main(["model", "issues", "--org", "all"])
            table = out.getvalue()
        self.assertEqual([(i["org"], i["issue_id"]) for i in got["issues"]],
                         [("acme", "aaa111"), ("acme", "same00"), ("team", "bbb222"), ("team", "same00")])
        self.assertEqual(got["orgs"]["team"]["structure_keeper"], "team_org")
        self.assertEqual(set(one), {"issues", "rules", "structure_keeper"})     # one organization: the shape the digest reads
        self.assertTrue(table.startswith("ORG"))
        self.assertIn("rules for team:", table)

    def test_label_finds_the_issue_in_any_org(self):
        import importlib.machinery
        import importlib.util
        import json
        import subprocess
        from unittest import mock
        import of_events
        loader = importlib.machinery.SourceFileLoader("of_cli_issues", os.path.join(os.path.dirname(__file__), "..", "bin", "of"))
        of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_issues", loader))
        loader.exec_module(of)
        listed = {"issues": [self.issue("43b590d385f6", "team"), self.issue("6081a150e82b", "acme"),
                             self.issue("6081a150e82b", "team")]}
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(listed), stderr="")

        def args(iid, org=None):
            import argparse
            return argparse.Namespace(action="label", id=iid, label="true_positive", note=None, rule_version=None,
                                      json=False, sender=None, org=org)
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
        with mock.patch.object(of.subprocess, "run", run), mock.patch.object(of_events, "emit") as emit, \
                mock.patch.dict(os.environ, env, clear=True), mock.patch("builtins.print"):
            of.cmd_issues(args("43b590"))
            self.assertEqual(calls[-1][-2:], ["--org", "all"])
            self.assertEqual(emit.call_args.kwargs["org"], "team")                 # the owner's label lands in the issue's org
            self.assertEqual(emit.call_args.args[1]["by"], "owner")
            with self.assertRaises(of.OfError) as e:
                of.cmd_issues(args("6081a150e82b"))
            self.assertIn("add --org", str(e.exception))
            listed["issues"] = [i for i in listed["issues"] if i["org"] == "acme"]   # what `--org acme` returns
            of.cmd_issues(args("6081a150e82b", "acme"))
            self.assertEqual(calls[-1][-2:], ["--org", "acme"])
            self.assertEqual(emit.call_args.kwargs["org"], "acme")


class FlowMeasureTest(unittest.TestCase):
    """The flow analyst's series (docs/onboarding-practice.md §5, fl-v0): a value or a gap naming the missing input
    (of_onboarding, 2026-11-11: the oracle of lab-onboarding-v0's check C5)."""
    A, B = "2026-11-04T00:00Z", "2026-11-06T00:00Z"
    DOC = DelegationTest.DOC

    def series(self, fl):
        return {s["key"]: s for s in fl["series"]}

    def commit(self, i, sha, at):
        return ev(i, at, "of.work_record", None, {"source": "git", "unit": "commit", "repo": "shop", "sha": sha, "merged_at": at})

    def pr(self, i, n, opened, merged, author="dev"):
        return ev(i, merged, "of.work_record", None, {"source": "gh", "repo": "o/shop", "number": n, "author": author,
                                                      "created_at": opened, "merged_at": merged})

    def test_before_onboarding_commits_only(self):
        m = of_model.build({"positions": [], "systems": {}}, [], at=self.B)
        rows = [self.commit(1, "a1", "2026-11-04T10:00Z"), self.commit(2, "a2", "2026-11-04T11:00Z"),
                self.commit(3, "a2", "2026-11-04T11:00Z")]                     # imported twice: one commit
        fl = M.flow(m, rows, self.A, self.B)
        s = self.series(fl)
        self.assertEqual(fl["commits_from_git"], 2)
        for k in ("merged_prs_per_day", "pr_lead_time_h", "merged_prs_per_prompt"):
            self.assertIsNone(s[k]["value"])
            self.assertIn("no of.work_record from GitHub", s[k]["gap"])
        self.assertEqual(s["agents_at_once"]["gap"], "no of.turn_end")
        self.assertEqual(s["head_prompts_per_day"]["gap"], "no of.human_prompt")
        self.assertIn("no unit owner below the head", s["settled_below_share"]["gap"])
        self.assertEqual(s["wait_h"]["gap"], "no of.state_declared")
        self.assertEqual([x["measure"] for x in fl["never_collected"]],
                         ["process time, %C&A, activity ratio, rolled %C&A", "work outside GitHub and git",
                          "the share of people who record their sessions"])
        self.assertEqual(fl["silent_days"], ["2026-11-05"])
        self.assertEqual(fl["partial_days"], [])

    def test_series_values(self):
        m = of_model.build(self.DOC, [], at=self.B)
        decl = lambda i, ts, sid, to, **kw: ev(i, ts, "of.agent_question", sid, dict(
            {"detected_by": "declared", "source": "declared", "to": to, "questions": [{"question": "go?"}]}, **kw))
        rows = [
            self.pr(1, 7, "2026-11-04T08:00Z", "2026-11-04T10:00Z"), self.pr(2, 7, "2026-11-04T08:00Z", "2026-11-04T10:00Z"),
            self.pr(3, 8, "2026-11-04T09:00Z", "2026-11-04T13:00Z"), self.pr(4, 9, "2026-11-04T12:00Z", "2026-11-04T12:01Z", "app/mergify"),
            prompt(10, "2026-11-04T10:00Z", "s-op", "сделай"),                  # opens s-op's turn
            ev(11, "2026-11-04T10:12Z", "of.turn_end", "s-op"),                 # buckets 10:00, 10:05, 10:10
            ev(12, "2026-11-04T10:07Z", "of.turn_end", "s-k8s"),                # no prompt recorded: its own bucket
            decl(20, "2026-11-04T11:00Z", "s-k8s", "plat_owner", from_position="k8s_keeper"),
            decl(21, "2026-11-04T11:30Z", "s-k8s", "plat_owner", from_position="k8s_keeper"),
            ev(22, "2026-11-04T12:00Z", "of.agent_question", None,
               {"detected_by": "declared", "source": "escalated", "from_position": "k8s_keeper", "to": "founder",
                "escalated_from": "e20", "questions": [{"question": "go?"}]}, position="plat_owner"),
            ev(30, "2026-11-04T09:00Z", "of.state_declared", "s-op", {"position": "bot_operator", "state": "waiting",
                                                                       "waiting_on": "k8s_keeper"}),
            ev(31, "2026-11-04T10:00Z", "of.state_declared", "s-op", {"position": "bot_operator", "state": "working"}),
        ]
        fl = M.flow(m, rows, self.A, self.B)
        s = self.series(fl)
        self.assertEqual(s["merged_prs_per_day"]["value"], 1.0)               # 2 pull requests in 2 days; the bot's apart
        self.assertEqual(s["merged_prs_per_day"]["detail"], {"merged": 2, "bots_left_out": 1})
        self.assertEqual(s["pr_lead_time_h"]["value"], 4.0)                   # 2 h and 4 h: the baseline's floor(p·n) element
        self.assertEqual(s["agents_at_once"]["value"]["mean_working"], round(4 / 3, 2))
        self.assertEqual(s["agents_at_once"]["value"]["peak"], 2)
        self.assertEqual(s["head_prompts_per_day"]["value"], 0.5)
        self.assertEqual(s["merged_prs_per_prompt"]["value"], 2.0)
        self.assertEqual(s["settled_below_share"]["value"], 0.5)              # 2 asks reached plat_owner, 1 went up
        self.assertEqual(s["settled_below_share"]["detail"]["unit_owners"], ["plat_owner"])
        self.assertEqual(s["wait_h"]["value"]["peer"], 1.0)
        self.assertTrue(all(x["gap"] is None for k, x in s.items() if k not in ("answer_median_min",)), s)
        self.assertEqual(s["answer_median_min"]["gap"], "no answered ask")

    def test_cli_prints_values_and_gaps(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        import of_studio_cli
        m = of_model.build({"positions": [], "systems": {}}, [], at=self.B)
        rows = [self.commit(1, "a1", "2026-11-04T10:00Z")]
        out = io.StringIO()
        with mock.patch.object(of_studio_cli, "_model", return_value=({"name": "acme"}, m, rows)), \
                mock.patch.object(of_studio_cli, "_rows", return_value=rows), redirect_stdout(out):
            of_studio_cli.main(["model", "measures", "--org", "acme", "--from", self.A, "--to", self.B])
        text = out.getvalue()
        self.assertIn("gap: no of.work_record from GitHub", text)
        self.assertIn("(1 commits from git in the window", text)
        self.assertIn("gap: process time, %C&A", text)
