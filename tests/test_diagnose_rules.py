"""Tests of the detection rules (bin/of_diagnose_rules.py). Owner: of_field-analyst, since 2026-11-07.

Moved from tests/test_studio.py with the rules; the run (labels, parts, merges, ranking) stays tested there."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))

import of_diagnose  # noqa: E402  (the run)
import of_diagnose_rules as R  # noqa: E402
import of_measures as M  # noqa: E402
import of_model  # noqa: E402

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


def R_spec(name):
    """A rule's entry in the catalogue (docs/diagnose-v0.yaml)."""
    return of_diagnose.catalogue()["rules"][name]


class RulesTest(unittest.TestCase):
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

    def owner_doc(self):
        return {"systems": {"acme": {"kind": "creating", "keeper": "acme_org"},
                            "bot": {"keeper": "bot_operator"}, "k8s": {"keeper": "k8s_keeper"}},
                "positions": [{"name": n, "system": s, "holder": {"session": f"s-{n}"}}
                              for n, s in (("bot_operator", "bot"), ("k8s_keeper", "k8s"), ("acme_org", "acme"),
                                           ("bot_search", "bot"), ("bot_nonce", "bot"))]}

    def test_unkept_system_with_or_without_work_and_orphan_work_v3(self):
        rows = [self.wr(i, ["lonely"]) for i in range(5)] + [self.wr(9, [])] + [self.wr(10, ["lonely"], "app/mergify")]
        d = self.diag(rows)
        unkept = {tuple(i["affected"]): i for i in d["issues"] if i["rule"] == "ap.unkept-system"}
        self.assertEqual(list(unkept), [("lonely",)])
        i = unkept[("lonely",)]
        self.assertEqual((i["part"], i["decider"], i["decider_position"]), ("keeper", "structure_keeper", None))
        self.assertEqual(i["evidence"]["merged_prs"], 5)   # the bot's PR is not counted
        # orphan-work@3: keeperless moved to unkept-system; unmapped stays (1 of 6 = 17% >= 5%)
        self.assertEqual({tuple(i["affected"]) for i in d["issues"] if i["rule"] == "ap.orphan-work"}, {("(unmapped)",)})
        # no work at all: the system is still live and kept by nobody
        self.assertEqual([i["affected"] for i in self.diag([])["issues"] if i["rule"] == "ap.unkept-system"], [["lonely"]])

    def unkept(self, system, extra=None, units=None, positions=()):
        """unkept-system on DOC2 with `lonely` shaped as `system` (and `extra` systems, `units`, more positions)."""
        doc = {**self.DOC2, "systems": {**self.DOC2["systems"], "lonely": system, **(extra or {})},
               "positions": self.DOC2["positions"] + list(positions)}
        if units:
            doc["units"] = units
        m = of_model.build(doc, [], at=self.B)
        return [(i["part"], i["affected"]) for i in R.rule_unkept_system(m, [], self.A, self.B, of_diagnose.catalogue(),
                                                                         R_spec("unkept-system"))]

    def test_unkept_system_reads_studio_coverage(self):
        """Kept by nobody, as coverage computes it: governed_by external:<who>, not live, or every part kept, is kept."""
        self.assertEqual(self.unkept({}), [("keeper", ["lonely"])])
        self.assertEqual(self.unkept({"governed_by": "external:colleague"}), [])
        self.assertEqual(self.unkept({"lifecycle": "unsupported"}), [])
        self.assertEqual(self.unkept({"lifecycle": "retired"}), [])
        self.assertEqual(self.unkept({}, extra={"part": {"parent": "lonely", "keeper": "k8s_keeper"}}), [])   # a supersystem
        self.assertEqual(self.unkept({"keeper": "gone"}), [("keeper", ["lonely"])])                           # keeper_unknown
        parked = {"name": "old_keeper", "system": "lonely", "status": "parked"}
        self.assertEqual(self.unkept({"keeper": "old_keeper"}, positions=[parked]), [("keeper", ["lonely"])])   # keeper_parked

    def test_unkept_system_unit_part(self):
        units = {"core": {"owner": "bot_operator", "systems": ["bot", "k8s"]}}
        self.assertEqual(self.unkept({"keeper": "k8s_keeper"}, units=units), [("unit", ["lonely"])])
        self.assertEqual(self.unkept({"keeper": "k8s_keeper"}, units={"core": {"owner": "bot_operator",
                                                                             "systems": ["bot", "k8s", "lonely"]}}), [])
        # a supersystem whose parts are all in units, and a system kept outside, need no unit of their own
        self.assertEqual(self.unkept({"governed_by": "external:colleague"},
                                     extra={"top": {"keeper": "k8s_keeper"}, "bot": {"keeper": "bot_operator", "parent": "top"},
                                            "k8s": {"keeper": "k8s_keeper", "parent": "top"}}, units=units), [])
        self.assertEqual(self.unkept({"keeper": "k8s_keeper"}), [])   # no units in the organization: no unit part

    def mcheck(self, i, sid, verdict, tool="Edit", system=None, rv="rt-v5"):
        return ev(f"mc{i}", "2026-11-03T10:00Z", "of.mandate_check", sid,
                  {"verdict": verdict, "tool_name": tool, "system": system, "path": "/acme/infra/x.yaml", "router_version": rv})

    def off_map(self, rows):
        m = of_model.build(self.DOC2, rows, at=self.B)
        return R.rule_off_map_work(m, rows, of_model.epoch(self.A), of_model.epoch(self.B), of_diagnose.catalogue(),
                                   R_spec("off-map-work"))

    def test_off_map_work(self):
        away = [self.mcheck(i, "s-op", "owned_by:k8s_keeper", system="k8s") for i in range(12)]
        got = self.off_map(away)
        self.assertEqual([(i["affected"], i["part"]) for i in got], [(["bot_operator"], "no_own_paths")])
        self.assertEqual(got[0]["evidence"]["owned_by"], {"k8s_keeper": 12})
        own = [self.mcheck(100 + i, "s-op", "in_mandate", system="bot") for i in range(3)]
        again = self.off_map(away + own)   # 3 of 15 in its own system: same issue, the other part
        self.assertEqual([(i["issue_id"], i["part"]) for i in again], [(got[0]["issue_id"], "mostly_elsewhere")])
        self.assertEqual(self.off_map(away + [self.mcheck(200 + i, "s-op", "in_mandate", system="bot") for i in range(12)]), [])
        # reads of others' code, a shell at a repository root, verdicts before rt-v4 and a few edits are not findings
        self.assertEqual(self.off_map([self.mcheck(i, "s-op", "owned_by:k8s_keeper", tool="Read", system="k8s") for i in range(30)]), [])
        self.assertEqual(self.off_map([self.mcheck(i, "s-op", "unscoped", tool="Bash") for i in range(50)]), [])
        self.assertEqual(self.off_map([self.mcheck(i, "s-op", "unowned", rv="rt-v3") for i in range(12)]), [])
        self.assertEqual(self.off_map([self.mcheck(i, "s-op", "unowned") for i in range(12)]), [])   # the map's gap: orphan-work's
        self.assertEqual(self.off_map(away[:9]), [])

    CELLS = {"systems": {"eng": {"keeper": "lead", "needs": ["build", "verify", "operate"]},
                         "ops": {"keeper": "lead", "needs": ["operate"]},
                         "old": {"keeper": "lead", "lifecycle": "retired", "needs": ["build"]}},
             "positions": [{"name": "lead", "system": "eng", "owns": [{"system": "eng", "method": "build", "decides": True}]},
                           {"name": "a", "system": "eng", "owns": [{"system": "eng", "method": "verify"}]},
                           {"name": "b", "system": "eng", "owns": [{"system": "eng", "method": "build"}]},
                           {"name": "c", "system": "eng", "owns": [{"system": "eng", "method": "build"}]}]}

    def test_cell_gap_and_overlap(self):
        m = of_model.build(self.CELLS, [], at=self.B)
        cat = of_diagnose.catalogue()
        gaps = {tuple(i["affected"]): i["part"] for i in R.rule_cell_gap(m, [], 0, 1, cat, R_spec("cell-gap"))}
        self.assertEqual(gaps, {("eng", "method:verify"): "no_decider", ("eng", "method:operate"): "nobody",
                                ("ops",): "system"})   # ops: no position owns any of its cells; old: retired
        over = R.rule_cell_overlap(m, [], 0, 1, cat, R_spec("cell-overlap"))
        self.assertEqual([(i["affected"], i["evidence"]["deciders"]) for i in over], [(["eng", "method:build"], ["lead"])])
        # a no_decider gap on an overlapping cell is evidence on the overlap, one issue (merge_into)
        doc = {**self.CELLS, "positions": self.CELLS["positions"] + [
            {"name": n, "system": "eng", "owns": [{"system": "eng", "method": "verify"}]} for n in ("d", "e")]}
        d = of_diagnose.diagnose(of_model.build(doc, [], at=self.B), [], self.A, self.B)
        verify = [i for i in d["issues"] if i["affected"] == ["eng", "method:verify"]]
        self.assertEqual([i["rule"] for i in verify], ["ap.cell-overlap"])
        self.assertIn("ap.cell-gap@0", verify[0]["evidence"]["merged"])
        self.assertTrue(all(i["decider"] == "structure_keeper" for i in d["issues"] if i["rule"].startswith("ap.cell-")))

    HID = "01a1c759-4e99-7222-b4aa-f0f8aa11bb22"

    def ho(self, i, title, context=(), done_when="a test shows it", frm="compliance_watcher", to="bot_operator",
           hid=None, ts="2026-11-02T10:00Z", **extra):
        return ev(f"ho{i}", ts, "of.handover_proposed", None,
                  dict({"id": hid or self.HID, "from": frm, "to": to, "title": title, "done_when": done_when,
                        "context": list(context)}, **extra))

    def pr(self, n, title, position="bot_operator", ts="2026-11-03T10:00Z", author="founder-gh", **extra):
        return ev(f"pr{n}", ts, "of.work_record", None,
                  dict({"source": "gh", "repo": "acme/arb", "number": n, "url": f"https://github.com/acme/arb/pull/{n}",
                        "author": author, "merged_at": ts, "title": title, "position": position, "systems": ["bot"]}, **extra))

    def unsourced(self, rows):
        m = of_model.build(self.DOC2, rows, at=self.B)
        return R.rule_unsourced_requirement(m, rows, of_model.epoch(self.A), of_model.epoch(self.B), of_diagnose.catalogue(),
                                            R_spec("unsourced-requirement"))

    HEDGED = ["registers/reg.md#G-REG-02", "Decree 1/2020 Art. 5(1) [confirm with counsel, Q-REG-05]"]

    def test_unsourced_requirement_strengthened_and_carried(self):
        """A watcher's unconfirmed reading becomes a hand-over stated as work to do, and the receiver's PR carries it."""
        root = self.ho(1, "Before live sends: skip listed addresses", self.HEDGED)
        work = [self.pr(42, "feat(bot): screen each send against a list (G-REG-02)"),
                self.pr(43, "merge queue: merged #42: feat(bot): screen each send (G-REG-02)", author="app/mergify"),
                self.pr(44, "docs: list format (G-REG-02)", position="k8s_keeper")]   # not the receiver: not carried
        got = self.unsourced([root] + work)
        self.assertEqual([(i["part"], i["affected"], i["decider"]) for i in got],
                         [("strengthened", ["bot_operator", "compliance_watcher"], "structure_keeper")])
        ev_ = got[0]["evidence"]
        self.assertEqual([(x["record"], x["link"]) for x in ev_["carried_into"]], [("arb#42", "the receiver's work cites G-REG-02")])
        self.assertIn("[confirm with counsel, Q-REG-05]", got[0]["summary"])
        self.assertEqual(got[0]["issue_id"], self.unsourced([root])[0]["issue_id"])   # one issue per hand-over, with or without work
        # the PR body, when the record carries it, shows how far the claim grew; a delivered result links work too
        body = self.pr(42, "feat(bot): screen", body="Compliance requirement G-REG-02 (hand-over aa11bb22): skip listed ones.")
        done = ev("d1", "2026-11-03T12:00Z", "of.handover_delivered", None, {"id": self.HID, "result": "acme/arb#45 merged"})
        got = self.unsourced([root, body, self.pr(45, "feat(bot): the list job", position="k8s_keeper"), done])
        recs = {x["record"]: x for x in got[0]["evidence"]["carried_into"]}
        self.assertEqual(recs["arb#42"]["link"], "its text cites the hand-over")
        self.assertIn("Compliance requirement G-REG-02", recs["arb#42"]["states"])
        self.assertEqual(recs["arb#45"]["link"], "the hand-over's delivered result names it")
        self.assertEqual(got[0]["evidence"]["delivered"], "acme/arb#45 merged")

    def test_unsourced_requirement_negatives(self):
        """The basis carried, a quoted source, an owner's ask, a question, or a receiver who returned it: no issue."""
        self.assertEqual(self.unsourced([self.ho(1, "Unconfirmed, pending counsel: skip listed addresses", self.HEDGED)]), [])
        self.assertEqual(self.unsourced([self.ho(1, "Skip listed addresses", self.HEDGED,
                                                 done_when="inference, unconfirmed until counsel answers Q-REG-05")]), [])
        quoted = ["Decree 1/2020 Art. 5: \"any person freezes listed persons' funds and makes none available\""]
        self.assertEqual(self.unsourced([self.ho(1, "Skip listed addresses", quoted)]), [])
        self.assertEqual(self.unsourced([self.ho(1, "Skip listed addresses", basis="ask:a76abee5")]), [])
        self.assertEqual(self.unsourced([self.ho(1, "Do our licensed activities cover trading?", self.HEDGED)]), [])
        for kind in ("declined", "returned", "withdrawn", "expired"):
            gone = ev("x", "2026-11-02T11:00Z", f"of.handover_{kind}", None, {"id": self.HID})
            self.assertEqual(self.unsourced([self.ho(1, "Skip listed addresses", self.HEDGED), gone]), [], kind)
        # a spec's must or required is not an obligation claim (the first calibration week's look-alikes)
        self.assertEqual(self.unsourced([self.ho(1, "Bid gas count is a required key; the parser must refuse a missing one"),
                                         self.pr(50, "config(search): the prefetch distance is a required TOML key"),
                                         self.pr(51, "fix(policy): show the model the grammar it must follow")]), [])
        # the basis field (of handover propose --basis, 8056acf): the offer shows an inference as unconfirmed, so it grows
        # only when the title or done_when calls it owed
        self.assertEqual(self.unsourced([self.ho(1, "Skip listed addresses", basis="inference")]), [])
        got = self.unsourced([self.ho(1, "Compliance requirement G-REG-02: skip listed addresses", basis="inference")])
        self.assertEqual([i["part"] for i in got], ["strengthened"])

    def test_unsourced_requirement_without_any_basis(self):
        got = self.unsourced([self.ho(1, "Add the audit log: a compliance requirement"),
                              self.pr(60, "feat(bot): audit log", body="This is a legal requirement for the exchange."),
                              self.pr(61, "feat(bot): audit log", body="A legal requirement (founder, ask a76abee5)."),
                              self.pr(62, "feat(bot): audit log", body="Maybe a legal requirement: unconfirmed.")])
        self.assertEqual(sorted((i["part"], tuple(i["affected"])) for i in got),
                         [("unsourced", ("bot_operator",)), ("unsourced", ("bot_operator", "compliance_watcher"))])
        self.assertEqual(next(i for i in got if "record" in i["evidence"])["evidence"]["record"], "arb#60")
        # a hand-over that kept its hedge, and a PR that cites it and states it as owed: the PR is where it grew
        kept = self.ho(1, "Skip listed addresses (unconfirmed)", self.HEDGED)
        grew = self.pr(63, "feat(bot): screen", body="Compliance requirement G-REG-02 (hand-over aa11bb22).")
        got = self.unsourced([kept, grew])
        self.assertEqual([(i["part"], i["affected"], i["evidence"]["record"]) for i in got],
                         [("strengthened", ["bot_operator"], "arb#63")])
        # with no hand-over in the window the rule is blocked (it reads hand-overs first)
        self.assertEqual(self.status(self.diag([]), "unsourced-requirement")["status"], "blocked")

    def test_cell_rules_blocked_until_cells_are_declared(self):
        d = self.diag([])
        self.assertEqual(self.status(d, "cell-gap")["status"], "blocked")
        self.assertEqual(self.status(d, "cell-overlap")["status"], "blocked")

    def test_coordination_gap_needs_observed_keepers(self):
        msg = ev(50, "2026-11-02T10:00Z", "of.agent_message_out", "s-op", {"to_position": "nobody"})
        base = [self.wr(1, ["bot"]), msg]
        d = self.diag(base)
        self.assertEqual(self.status(d, "coordination-gap")["status"], "blocked")   # keepers not observed
        d = self.diag(base + self.active("s-op") + self.active("s-k8s"))
        gaps = [i for i in d["issues"] if i["rule"] == "ap.coordination-gap"]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["evidence"]["keepers"], ["bot_operator", "k8s_keeper"])
        talk = dict(ev(51, "2026-11-02T11:00Z", "of.agent_message_out", "s-op", {"to_position": "k8s_keeper"}), source="hook")
        d = self.diag(base + self.active("s-op") + self.active("s-k8s") + [talk])
        self.assertEqual(self.status(d, "coordination-gap")["status"], "quiet")

    def test_bookkeeping_is_not_activity(self):
        rows = [self.wr(1, ["bot"]), ev(50, "2026-11-02T10:00Z", "of.agent_message_out", "s-op", {})] + \
            [dict(r, source="of") for r in self.active("s-op") + self.active("s-k8s")]
        self.assertEqual(self.status(self.diag(rows), "coordination-gap")["status"], "blocked")

    def test_function_gap_and_lingering(self):
        doc = {"systems": {}, "positions": [
            {"name": "boss", "system": "x", "mandate_functions": ["strategy", "goals"], "holder": {"session": "s1"}},
            {"name": "temp", "system": "x", "mandate_exit": {"done_when": "the migration is done", "review": "2026-10-30"},
             "holder": {"session": "s2"}},
            {"name": "fresh", "system": "x", "mandate_exit": {"review": "2026-11-04"}},
            {"name": "gone", "system": "x", "status": "parked", "mandate_functions": ["hr"]}]}
        rows = [prompt(70, "2026-11-02T10:00Z", "s2", "how are things?")]
        m = of_model.build(doc, rows, at=self.B)
        d = of_diagnose.diagnose(m, rows, self.A, self.B)
        gaps = {i["evidence"]["function"] for i in d["issues"] if i["rule"] == "ap.function-gap"}
        self.assertNotIn("strategy", gaps)
        self.assertIn("hr", gaps)                 # covered only by a parked position
        self.assertEqual(len(gaps), len(of_model.MANAGEMENT_FUNCTIONS) - 2)
        ling = [i for i in d["issues"] if i["rule"] == "ap.lingering"]
        self.assertEqual([i["affected"] for i in ling], [["temp"]])   # "fresh" is within 48 h of its review
        self.assertEqual(ling[0]["score_terms"]["delta_m"], 1.5)
        extended = rows + [ev(71, "2026-11-02T00:00Z", "of.mandate_changed", None, {"position": "temp"})]
        d = of_diagnose.diagnose(of_model.build(doc, extended, at=self.B), extended, self.A, self.B)
        self.assertFalse([i for i in d["issues"] if i["rule"] == "ap.lingering"])

    def test_owner_router_and_relay(self):
        doc = self.owner_doc()
        fa = lambda cats: {"kind": "founder", "cats": cats, "approval": None}
        rows = []
        for i in range(10):   # 4 of 10 founder messages are plumbing: routing to k8s_keeper that names bot_operator
            cats = ["routing"] if i < 4 else []
            rows.append(ev(200 + i, f"2026-11-0{1 + i % 4}T1{i}:00Z", "of.human_prompt", "s-k8s_keeper",
                           {"origin": "human", "text": f"tell bot_operator about pool {i}", "classifications": {of_measures_FA: fa(cats)}}))
        rows += [ev(220, "2026-11-02T10:00Z", "of.human_prompt", "s-acme_org",
                    {"origin": "human", "text": "tell bot_operator", "classifications": {of_measures_FA: fa(["routing"])}})] * 1
        msg = lambda i, sender, sid, relayed: ev(i, "2026-11-03T10:00Z", "of.agent_message_in", sid,
                                                 {"text": f'<cross-session-message from="uds:x" from-name="{sender}">hi',
                                                  "classifications": {of_measures_FA: {"relayed": relayed}}})
        rows += [msg(230 + i, "bot_search", "s-bot_nonce", i < 5) for i in range(15)]            # 5 of 15: fires
        rows += [msg(260 + i, "bot_operator", "s-k8s_keeper", i < 5) for i in range(100)]        # 5 of 100: status traffic
        rows.append(question(240, "2026-11-03T11:00Z", "s-bot_search"))
        rows.append(ev(241, "2026-11-03T11:05Z", "of.agent_message_out", "s-bot_search", {"to_position": "bot_nonce"}))
        m = of_model.build(doc, rows, at=self.B)
        d = of_diagnose.diagnose(m, rows, self.A, self.B)
        router = [i for i in d["issues"] if i["rule"] == "ap.owner-router"]
        self.assertEqual(len(router), 1)
        self.assertIn("plumbing", router[0]["summary"])
        relay = {tuple(i["affected"]): i["evidence"] for i in d["issues"] if i["rule"] == "ap.owner-relay"}
        ids = [i["issue_id"] for i in d["issues"] if i["rule"] == "ap.owner-relay"]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(relay[("bot_operator", "k8s_keeper")]["owner_relays"], 4)
        self.assertNotIn(("bot_operator", "acme_org"), relay)                         # the structure keeper's job
        self.assertEqual(relay[("bot_nonce", "bot_search")]["messages_with_owner_words"], 5)   # affected is sorted
        self.assertEqual(relay[("bot_nonce", "bot_search")]["pair_messages"], 15)
        self.assertEqual(relay[("bot_operator", "k8s_keeper")]["messages_with_owner_words"], 5)   # fires on its relays half
        self.assertEqual(relay[("bot_operator", "k8s_keeper")]["owner_words_share"], 0.05)

    def test_over_coupled_and_fragmented(self):
        doc = self.owner_doc()
        days = ("2026-10-30", "2026-11-01", "2026-11-03")
        act = [dict(ev(f"{n}{d}", f"{d}T09:00Z", "of.turn_end", f"s-{n}", {}), source="hook")
               for n in ("bot_search", "bot_nonce") for d in days]
        msgs = [ev(300 + i, "2026-11-02T10:00Z", "of.agent_message_out", "s-bot_search" if i % 2 else "s-bot_nonce",
                   {"to_position": "bot_nonce" if i % 2 else "bot_search"}) for i in range(12)]
        prs = [ev(400 + i, f"{day}T12:00Z", "of.work_record", f"s-{w}",
                  {"source": "gh", "author": "founder-gh", "systems": ["bot"], "repo": "o/r", "number": 400 + i}, position=w)
               for i, (day, w) in enumerate([(d, w) for d in ("2026-10-24", "2026-11-02")
                                            for w in ("bot_search", "bot_nonce", "k8s_keeper", "bot_operator")])]
        rows = act + msgs + prs
        d = of_diagnose.diagnose(of_model.build(doc, rows, at=self.B), rows, self.A, self.B)
        oc = [i for i in d["issues"] if i["rule"] == "ap.over-coupled"]
        self.assertEqual([i["affected"] for i in oc], [["bot_nonce", "bot_search"]])   # 12 messages / 2 PRs, reciprocal
        frag = [i for i in d["issues"] if i["rule"] == "ap.fragmented-ownership"]
        self.assertEqual(frag[0]["affected"], ["bot", "bot_operator"])                 # keeper wrote 25%, two windows
        self.assertEqual((frag[0]["part"], frag[0]["severity"]), ("code", "medium"))
        late = {"keeper_since": lambda sysid, k: of_model.epoch("2026-11-01T00:00Z")}   # named inside the windows
        d = of_diagnose.diagnose(of_model.build(doc, rows, at=self.B), rows, self.A, self.B, context=late)
        self.assertFalse([i for i in d["issues"] if i["rule"] == "ap.fragmented-ownership"])
        st = next(r for r in d["rules"] if r["rule"] == "ap.fragmented-ownership")
        self.assertEqual(st["notes"], ["bot: quiet, keeper bot_operator since 2026-11-01"])
        # over-coupled@2: the same traffic with a unit owner (kind owner) or the structure keeper is their mandate
        for i, x in enumerate(doc["positions"]):
            if x["name"] == "bot_nonce":
                owner_doc = dict(doc, positions=doc["positions"][:i] + [dict(x, kind="owner")] + doc["positions"][i + 1:])
                keeper_doc = dict(doc, systems=dict(doc["systems"], acme={"kind": "creating", "keeper": "bot_nonce"}))
        for dd in (owner_doc, keeper_doc):
            d = of_diagnose.diagnose(of_model.build(dd, rows, at=self.B), rows, self.A, self.B)
            self.assertFalse([i for i in d["issues"] if i["rule"] == "ap.over-coupled"])

    def test_chatty_org_trend_compares_like_with_like(self):
        """From 2026-11-06 the relay logs every send (source `of`); a window before it saw only transcript messages."""
        doc = self.owner_doc()
        def out(i, day, source):
            return dict(ev(f"o{i}", f"{day}T10:{i % 60:02d}Z", "of.agent_message_out", "s-bot_search",
                           {"to_position": "bot_nonce"}), source=source)
        prs = [ev(f"w{i}", f"{day}T12:00Z", "of.work_record", "s-bot_search",
                  {"source": "gh", "author": "founder-gh", "systems": ["bot"], "repo": "o/r", "number": i}, position="bot_search")
               for i, day in enumerate(["2026-10-25"] * 5 + ["2026-11-01"] * 5 + ["2026-11-04"])]
        before = [out(i, "2026-10-25", "import") for i in range(5)]
        rise = lambda source: [out(100 + i, "2026-11-01", source) for i in range(9)]   # 1 -> 1.8 per PR: +80%
        for source, fired in (("import", True), ("of", False)):
            rows = before + rise(source) + prs
            d = of_diagnose.diagnose(of_model.build(doc, rows, at=self.B), rows, self.A, self.B)
            org = [i for i in d["issues"] if i["rule"] == "ap.chatty" and i["affected"] == ["(organization)"]]
            self.assertEqual(bool(org), fired, source)
            notes = next(r for r in d["rules"] if r["rule"] == "ap.chatty")["notes"]
            self.assertEqual(bool(notes), not fired)

    def test_betweenness_star(self):
        bc = R.betweenness({"hub": {"a", "b", "c"}, "a": {"hub"}, "b": {"hub"}, "c": {"hub"}})
        self.assertAlmostEqual(bc["hub"], 1.0)
        self.assertEqual(bc["a"], 0.0)

    def test_unclear_mandate_and_churn(self):
        doc = self.owner_doc()
        snap = lambda i, ts, cls, pos="bot_search": ev(i, ts, "of.mandate_changed", None,
                                                       {"position": pos, "summary": "same", "throughput": cls})
        rows = [snap(600, "2026-10-20T00:00Z", "raise"), snap(601, "2026-10-31T00:00Z", "sustain"),
                snap(602, "2026-11-02T00:00Z", "raise"),
                ev(604, "2026-10-21T00:00Z", "of.mandate_changed", None, {"position": "bot_nonce", "summary": "a", "throughput": "raise"}),
                ev(605, "2026-10-31T00:00Z", "of.mandate_changed", None, {"position": "bot_nonce", "summary": "b", "throughput": "raise",
                                                                         "mandate_exit": {"done_when": "x"}}),
                ev(606, "2026-11-02T00:00Z", "of.mandate_changed", None, {"position": "bot_nonce", "summary": "c", "throughput": "raise",
                                                                         "mandate_exit": {"done_when": "x"}})]
        prs = [ev(700 + i, f"2026-10-{24 + i // 10:02d}T10:00Z", "of.work_record", None,
                  {"source": "gh", "author": "founder-gh", "merged_at": f"2026-10-{24 + i // 10:02d}T10:00Z", "systems": ["bot"]})
               for i in range(70)]                                   # 10 a day on 10-24..10-30, none on 10-31
        prs.append(ev(799, "2026-11-04T10:00Z", "of.work_record", None,
                      {"source": "gh", "author": "founder-gh", "merged_at": "2026-11-04T10:00Z", "systems": ["bot"]}))
        rows += prs + [ev(603, "2026-10-31T01:00Z", "of.holder_changed", None, {"position": "bot_search"})]
        d = of_diagnose.diagnose(of_model.build(doc, rows, at=self.B), rows, self.A, self.B)
        um = [i for i in d["issues"] if i["rule"] == "ap.unclear-mandate"]
        self.assertEqual([i["affected"] for i in um], [["bot_search"]])   # two throughput changes; bot_nonce: summary
        #                                                                      edits and a newly added done_when don't count
        churn = [i for i in d["issues"] if i["rule"] == "ap.churn" and i["affected"] == ["bot_search"]]
        self.assertEqual(churn[0]["evidence"]["dip_days"], ["2026-10-31", "2026-11-02"])   # both days fell below half the median

    def test_chatty_pair(self):
        doc = self.owner_doc()
        days = ("2026-10-30", "2026-11-01", "2026-11-03")
        act = [dict(ev(f"{n}{d}", f"{d}T09:00Z", "of.turn_end", f"s-{n}", {}), source="hook") for n in ("bot_search", "bot_nonce") for d in days]
        msgs = [ev(800 + i, "2026-11-02T10:00Z", "of.agent_message_out", "s-bot_search", {"to_position": "bot_nonce"}) for i in range(12)]
        pr = ev(900, "2026-11-02T11:00Z", "of.work_record", None, {"source": "gh", "author": "founder-gh", "systems": ["bot"]})
        # the part divides by attributed merged PRs: an unattributed PR leaves it blocked (wr.position), not 12 per PR
        # (the first user organization, 2026-11-10: 90 of 820 attributed, 133 pair issues)
        d = of_diagnose.diagnose(of_model.build(doc, act + msgs + [pr], at=self.B), act + msgs + [pr], self.A, self.B)
        self.assertEqual([i for i in d["issues"] if i["rule"] == "ap.chatty"], [])
        row = next(r for r in d["rules"] if r["rule"] == "ap.chatty")
        self.assertEqual(row["parts_blocked"].get("default"), ["wr.position"])
        rows = act + msgs + [dict(pr, position="bot_search")]
        d = of_diagnose.diagnose(of_model.build(doc, rows, at=self.B), rows, self.A, self.B)
        self.assertIn(["bot_nonce", "bot_search"], [i["affected"] for i in d["issues"] if i["rule"] == "ap.chatty"])
        # needs keyed by part: the per-PR part runs without the receiver parts' inputs, which report as blocked parts
        import copy
        cat = copy.deepcopy(of_diagnose.catalogue())
        cat["rules"]["chatty"]["needs"] = {"default": ["of.agent_message_out", "of.work_record", "wr.position"],
                                           "receiver_flood": ["of.agent_message_in"], "repeat_nudges": ["of.human_prompt"]}
        cat["rules"]["chatty"]["remedies"] = {"default": ["status surface"], "repeat_nudges": ["answer or decline"]}
        d = of_diagnose.diagnose(of_model.build(doc, rows, at=self.B), rows, self.A, self.B, cat=cat)
        pair = next(i for i in d["issues"] if i["rule"] == "ap.chatty" and i["affected"] == ["bot_nonce", "bot_search"])
        self.assertEqual(pair["remedies"], ["status surface"])          # an issue without a part reads `default`
        row = next(r for r in d["rules"] if r["rule"] == "ap.chatty")
        self.assertEqual(sorted(row["parts_blocked"]), ["receiver_flood", "repeat_nudges"])

    def _queues(self, pos, days, depth=3, org="acme", i0=0):
        """One backlog episode a day for `days` days before B: depth `depth` from 08:00, oldest 07:00, empty at 10:00."""
        rows = []
        for k, d in enumerate(days):
            pl = lambda n, o: {"org": org, "position": pos, "depth": n, "oldest_created_at": o}
            rows += [ev(i0 + 2 * k, f"{d}T08:00Z", "of.inbound_queue", None, pl(depth, f"{d}T07:00Z"), position=pos),
                     ev(i0 + 2 * k + 1, f"{d}T10:00Z", "of.inbound_queue", None, pl(0, None), position=pos)]
        return rows

    def test_inbound_backlog(self):
        cat = of_diagnose.catalogue()
        spec = cat["rules"]["inbound-backlog"]
        a, b = of_model.epoch("2026-11-01T00:00Z"), of_model.epoch("2026-11-08T00:00Z")
        doc = dict(DOC, positions=DOC["positions"] + [{"name": "old_bot", "system": "bot", "status": "parked"}])
        m = of_model.build(doc, [], at=b, org="acme")
        three = ["2026-11-02", "2026-11-03", "2026-11-04"]
        # two days of data only: blocked, with a note
        res, notes = R.rule_inbound_backlog(m, self._queues("bot_operator", three[:2]), a, b, cat, spec)
        self.assertEqual(res, [])
        self.assertIn("2 of 3 days", notes[0])
        # overload needs an average depth >= 2: 3 messages for 2 h a day is far below it over the window
        rows = self._queues("bot_operator", three)
        self.assertEqual(R.rule_inbound_backlog(m, rows, a, b, cat, spec), [])
        rows = self._queues("bot_operator", three, depth=400)
        res = R.rule_inbound_backlog(m, rows, a, b, cat, spec)
        self.assertEqual([(i["affected"], i["part"], i["severity"]) for i in res], [(["bot_operator"], "overload", "medium")])
        self.assertEqual(res[0]["evidence"]["backlog_days"], three)
        self.assertEqual(res[0]["evidence"]["backlog_h"], 6.0)
        # unattended: a parked position, 2 days are enough, severity high
        rows = self._queues("bot_operator", three) + self._queues("old_bot", three[:2], depth=1, i0=50)
        res = R.rule_inbound_backlog(m, rows, a, b, cat, spec)
        self.assertEqual([(i["affected"], i["part"], i["severity"], i["evidence"]["holder"]) for i in res],
                         [(["old_bot"], "unattended", "high", "status parked")])
        # the owner kind is excluded (owner-router's case)
        rows = self._queues("colleague", three, depth=400)
        self.assertEqual(R.rule_inbound_backlog(m, rows, a, b, cat, spec), [])

    def test_scratch_areas(self):
        for a in ("private/tmp/claude-501", "/tmp/x", "~/work/scratchpad/notes", "var/folders/ab/T"):
            self.assertTrue(R.is_scratch(a), a)
        self.assertFalse(R.is_scratch("~/acme/storefront"))


class ChattyInjectedTest(unittest.TestCase):
    """ap.chatty's receiver side (the founder, 2026-11-07): turns started by typed-in messages and pulse nudges."""

    def test_flood_and_repeat_nudges(self):
        m = of_model.build(DOC, [], at="2026-11-06T12:00Z")
        rows = []
        for h in (8, 9, 10):
            for k in range(4):
                rows.append(ev(f"r{h}{k}", f"2026-11-05T{h:02d}:{10 + k:02d}:00Z", "of.agent_message_in", "s-op",
                               {"text": "Message from acme:k8s_keeper (via relay, authority: none)\nping"}))
        for k in range(3):
            rows.append(prompt(f"n{k}", f"2026-11-05T11:{10 + k:02d}:00Z", "s-op",
                               "Message from bot_search (via of pulse, authority: none; automatic nudge)\nbot_search is waiting on you (bot_operator)."))
        rows.append(prompt("h1", "2026-11-05T12:00:00Z", "s-op", "how is it going?"))
        a, b = of_model.epoch("2026-11-05T00:00Z"), of_model.epoch("2026-11-06T00:00Z")
        turns = R.injected_turns(m, rows, a, b)
        self.assertEqual([k for _, _, k, _ in turns].count("relay"), 12)
        self.assertEqual([k for _, _, k, _ in turns].count("nudge"), 3)
        cat = of_diagnose.catalogue()
        got = R._chatty_injected(m, rows, a, b, cat, {"injected_turns_per_hour_gte": 4, "flood_hours_gte": 3,
                                                                 "nudges_per_pair_gte": 3})
        parts = {(i["part"], tuple(i["affected"])) for i in got}
        self.assertEqual(parts, {("receiver_flood", ("bot_operator",)), ("repeat_nudges", ("bot_operator", "bot_search"))})
        flood = next(i for i in got if i["part"] == "receiver_flood")
        self.assertEqual((flood["evidence"]["flood_hours"], flood["evidence"]["top_senders"]), (3, {"k8s_keeper": 12, "bot_search": 3}))
        self.assertNotEqual(flood["issue_id"], of_diagnose.issue_id("chatty", ["bot_operator"]))   # its own key, never a pair's id


class LoopRulesTest(unittest.TestCase):
    """The development-loop rules (diagnose.md 2.16-2.22) through the projection of_model.build makes (of_loops)."""
    A, B = "2026-11-01T00:00Z", "2026-11-08T00:00Z"
    W = "w-search"

    def doc(self, **loop):
        spec = {"target": {"metric": "day_pass", "money": "USD per day", "stake": "650 USD a day"},
                "measures": [{"name": "latency_ms"}],
                "ladder": [{"rung": "offline", "latency": "5 min"}, {"rung": "gate", "latency": "40 min"},
                           {"rung": "day_pass", "latency": "4 h"}, {"rung": "shadow", "production": True}],
                "checkpoint_days": 3, "wip_arms": 2, "babysit_model": "sonnet"}
        spec.update(loop)
        return {"organization": "acme", "systems": {"bot": {"keeper": "bot_operator"}},
                "positions": [{"name": "bot_operator", "system": "bot", "holder": {"session": "s-op"}},
                              {"name": "bot_search", "system": "bot", "holder": {"session": "s-search"}}],
                "works": {self.W: {"owner": "bot_operator", "doers": ["bot_search"], "loop": spec}}}

    def run_(self, i, ts, rung, hyp, value=None, metric=None, accepted=True, queued=None, started=None):
        pl = {"work": self.W, "run": f"r{i}", "rung": rung, "hypothesis": hyp, "value": value, "accepted": accepted,
              "ended_at": ts}
        if metric:
            pl["metric"] = metric
        if queued:
            pl["queued_at"] = queued
        if started:
            pl["started_at"] = started
        return ev(f"run{i}", ts, "of.loop_run", "s-search", pl, position="bot_search")

    def hyp(self, i, ts, hid):
        return ev(f"h{i}", ts, "of.loop_hypothesis", "s-search", {"work": self.W, "id": hid, "change": hid, "status": "open"},
                  position="bot_search")

    def decide(self, i, ts, hid, kind="drop"):
        return ev(f"d{i}", ts, "of.loop_decision", "s-search", {"work": self.W, "hypothesis": hid, "decision": kind},
                  position="bot_search")

    def found(self, rule, rows, doc=None):
        m = of_model.build(doc or self.doc(), rows, at=self.B)
        d = of_diagnose.diagnose(m, rows, self.A, self.B)
        return [i for i in d["issues"] if i["rule"] == "ap." + rule], next(r for r in d["rules"] if r["rule"] == "ap." + rule)

    def test_loop_rules_are_blocked_without_a_loop(self):
        m = of_model.build({k: v for k, v in self.doc().items() if k != "works"}, [], at=self.B)
        d = of_diagnose.diagnose(m, [], self.A, self.B)
        for r in d["rules"]:
            if r["rule"].startswith("ap.loop-"):
                self.assertEqual(r["status"], "blocked", r["rule"])

    def test_cheapest_rung(self):
        skip = [self.hyp(1, "2026-11-05T09:00Z", "h1"), self.hyp(2, "2026-11-05T09:00Z", "h2"),
                self.run_(1, "2026-11-05T14:00Z", "day_pass", "h1", 300), self.run_(2, "2026-11-06T14:00Z", "day_pass", "h2", 301)]
        got, st = self.found("loop-cheapest-rung", skip)
        self.assertEqual([i["affected"] for i in got], [[self.W]])
        self.assertEqual(got[0]["decider"], "doer")
        self.assertIn("offline first", got[0]["summary"])
        stepped = [self.hyp(1, "2026-11-05T09:00Z", "h1")] + [self.run_(i, f"2026-11-05T1{i}:00Z", r, "h1", 300)
                                                              for i, r in enumerate(("offline", "gate", "day_pass"), 1)]
        self.assertEqual(self.found("loop-cheapest-rung", stepped)[0], [])
        # a cheap rung that did not exist yet was not skipped (the first user organization's offline harness came on day four)
        late = self.doc(ladder=[{"rung": "offline", "since": "2026-11-06T00:00Z"}, {"rung": "day_pass"}])
        early = [self.run_(1, "2026-11-03T14:00Z", "day_pass", "h1", 300), self.run_(2, "2026-11-04T14:00Z", "day_pass", "h2", 301)]
        self.assertEqual(self.found("loop-cheapest-rung", early, late)[0], [])

    def test_production_checkpoint(self):
        runs = [self.run_(i, f"2026-11-0{d}T12:00Z", "day_pass", "h1", 300 + i) for i, d in enumerate((2, 4, 6, 7), 1)]
        got, _ = self.found("loop-checkpoint", runs)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["decider"], "work_owner")
        self.assertGreater(got[0]["evidence"]["days_without_production"], 3)
        shadow = runs + [self.run_(9, "2026-11-06T18:00Z", "shadow", "h1", 310)]
        self.assertEqual(self.found("loop-checkpoint", shadow)[0], [])

    def test_plateau(self):
        flat = [self.run_(i, f"2026-11-0{d}T12:00Z", "day_pass", "h1", v)
                for i, (d, v) in enumerate(((3, 211.7), (4, 325.8), (5, 325.8), (6, 326.0), (7, 325.9)), 1)]
        got, _ = self.found("loop-plateau", flat)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["evidence"]["values"], [325.8, 325.8, 326.0, 325.9])
        rising = [self.run_(i, f"2026-11-0{d}T12:00Z", "day_pass", "h1", v)
                  for i, (d, v) in enumerate(((3, 300), (4, 310), (5, 320), (6, 330)), 1)]
        self.assertEqual(self.found("loop-plateau", rising)[0], [])
        # lower is better: falling values are gains
        falling = [self.run_(i, f"2026-11-0{d}T12:00Z", "day_pass", "h1", v)
                   for i, (d, v) in enumerate(((3, 40), (4, 30), (5, 20), (6, 10)), 1)]
        low = self.doc(target={"metric": "day_pass", "better": "lower"})
        self.assertEqual(self.found("loop-plateau", falling, low)[0], [])

    def test_wip_limit(self):
        three = [self.run_(i, "2026-11-05T10:00Z", "offline", f"h{i}", 1) for i in (1, 2, 3)]
        got, _ = self.found("loop-wip", three)
        self.assertEqual(len(got), 1)
        self.assertGreaterEqual(got[0]["evidence"]["hours_over"], 12)
        settled = three + [self.decide(3, "2026-11-05T14:00Z", "h3")]
        self.assertEqual(self.found("loop-wip", settled)[0], [])   # 4 h over the limit

    def test_babysit_model(self):
        def calls(model, n):
            return [ev(f"mc{model}{i}", f"2026-11-06T{10 + i % 10}:{i % 60:02d}Z", "of.mandate_check", "s-search",
                       {"command_class": "runner" if i % 3 else "monitor", "model": model, "tool_name": "Bash"},
                       position="bot_search") for i in range(n)]
        run = [self.run_(1, "2026-11-06T12:00Z", "offline", "h1", 1)]
        got, _ = self.found("loop-babysit-model", run + calls("claude-fable-5-1", 120))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["evidence"]["calls"], 120)
        self.assertEqual(self.found("loop-babysit-model", run + calls("claude-sonnet-5-5", 120))[0], [])

    def test_objective_drift(self):
        runs = [self.run_(1, "2026-11-04T12:00Z", "day_pass", "h1", 325.8)] + \
               [self.run_(i, f"2026-11-0{4 + i // 2}T1{i}:00Z", "offline", "h2", 18 - i, metric="latency_ms") for i in range(2, 6)]
        got, _ = self.found("loop-objective-drift", runs)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["evidence"]["unpriced"], {"latency_ms": 4})
        priced = self.doc(measures=[{"name": "latency_ms", "to_money": "USD per ms from the checkout arithmetic"}])
        self.assertEqual(self.found("loop-objective-drift", runs, priced)[0], [])

    def test_evaluation_bottleneck(self):
        def gate(i, started):
            d = f"2026-11-0{3 + i}"
            return self.run_(i, f"{d}T13:00Z", "gate", f"h{i}", 300, queued=f"{d}T10:00Z", started=f"{d}T{started}Z")
        got, _ = self.found("loop-eval-bottleneck", [gate(i, "12:00") for i in (1, 2, 3)])
        self.assertEqual(len(got), 1)
        self.assertGreater(got[0]["evidence"]["rungs"]["gate"]["wait_share"], 0.4)
        self.assertEqual(self.found("loop-eval-bottleneck", [gate(i, "10:05") for i in (1, 2, 3)])[0], [])


if __name__ == "__main__":
    unittest.main()
