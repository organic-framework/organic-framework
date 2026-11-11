#!/usr/bin/env python3
"""lab-drift-v0 synthetic fixtures: per rule, one positive (the drift) and one negative (a benign look-alike),
built to of_studio's input spec (DESIGN.md). Each scenario is {name, doc, events, labels, context?}.
Events are load_events shape (use ev from harness). Rules filled in incrementally; harness scores whatever is here.
"""
from harness import ev

SCENARIOS = []

FA = "fa-2026-11-05"   # of_measures baseline classifier version
HUMAN = "founder"      # of_model.HUMAN: the root owner, affected of owner-router


# ---- shared helpers (mirror tests/test_studio.py ev/prompt shapes) ----
def fa(cats=(), kind="founder"):
    return {"kind": kind, "cats": list(cats), "approval": None, "extracted": True}


def prompt(i, ts, sid, text, cats=(), origin="human", position=None):
    return ev(i, ts, "of.human_prompt", sid,
              {"origin": origin, "text": text, "classifications": {FA: fa(cats)}}, position=position)


def question(i, ts, sid):
    return ev(i, ts, "of.agent_question", sid,
              {"detected_by": "heuristic", "classifications": {Q0: True, "q-v1": True},
               "questions": [{"question": "merge?"}]})


Q0 = "q0-v1"


def mout(i, ts, sid, to, position):
    return ev(i, ts, "of.agent_message_out", sid, {"to_position": to}, position=position)


def relay_in(i, ts, recv, sender):
    return ev(i, ts, "of.agent_message_in", sid_of(recv),
              {"text": f'Message from acme:{sender} (via relay) hi', "from_position": sender}, position=recv)


def request_in(i, ts, recv, sender):
    return ev(i, ts, "of.agent_message_in", sid_of(recv),
              {"text": f'<cross-session-message from="uds:x" from-name="{sender}">need a review',
               "from_position": sender, "classifications": {"rq-v0": True}}, position=recv)


def nudge(i, ts, recv, waiter):
    return ev(i, ts, "of.human_prompt", sid_of(recv),
              {"origin": "injected", "text": f"Message from {waiter} (via of pulse) still waiting"}, position=recv)


def turns(names, days=("2026-11-02", "2026-11-04", "2026-11-06")):
    return [ev(f"t-{n}-{d}", f"{d}T09:00Z", "of.turn_end", sid_of(n), {}, position=n) for n in names for d in days]


def wrec(i, ts, systems, position):
    return ev(i, ts, "of.work_record", sid_of(position),
              {"source": "gh", "author": "founder-gh", "systems": systems, "repo": "o/r", "number": 7000 + i},
              position=position)


def sid_of(name):
    return f"s-{name}"


# a small org used by owner-router, over-coupled and chatty: no units (so the human owner is `founder`), one system
# with a keeper (keeperless never fires), no depends_on (coordination-gap blocked), no mandate_functions
# (function-gap blocked) and no mandate_exit (lingering blocked) -- so only the rule under test can fire.
def _org_doc():
    return {
        "organization": "acme",
        "systems": {"bot": {"kind": "target", "keeper": "bot_operator", "repos": ["arb"]}},
        "positions": [
            {"name": "bot_operator", "system": "bot", "role": "keeper", "kind": "keeper", "owner": "founder",
             "holder": {"session": "s-bot_operator"}},
            {"name": "bot_search", "system": "bot", "role": "search", "kind": "project", "owner": "bot_operator",
             "holder": {"session": "s-bot_search"}},
            {"name": "bot_nonce", "system": "bot", "role": "nonce", "kind": "project", "owner": "bot_operator",
             "holder": {"session": "s-bot_nonce"}},
        ],
    }


# ---- ap.orphan-work@1-2 part keeperless; since 2026-11-10 ap.unkept-system@0 part keeper (orphan-work@3 dropped it) ----
# >= 5 non-bot work records on a registry system with NO keeper and no home position (the writer homes another
# system). unkept-system fires on such a system with or without work: these four scenarios now label it.
def _owner_doc(orphan_system, repos="orf"):
    return {
        "organization": "acme",
        "systems": {
            "home_sys": {"kind": "target", "keeper": "home_keeper", "repos": ["home"]},
            orphan_system: {"kind": "target", "repos": [repos]},   # no keeper, no home
        },
        "positions": [
            {"name": "home_keeper", "system": "home_sys", "role": "keeper", "kind": "keeper", "owner": "founder",
             "holder": {"session": "s-home"}},
            {"name": "worker", "system": "home_sys", "role": "worker", "kind": "project", "owner": "home_keeper",
             "holder": {"session": "s-w"}},
        ],
    }


def _wr(i, day, system, sid="s-w", pos="worker"):
    return ev(i, f"2026-11-0{day}T10:00Z", "of.work_record", sid,
              {"repo": "org/x", "number": 100 + i, "systems": [system]}, position=pos)


SCENARIOS.append({
    "name": "unkept-system/positive-with-work",
    "doc": _owner_doc("orphan_sys"),
    "events": [_wr(i, 2 + i, "orphan_sys") for i in range(5)] +
              [ev(90, "2026-11-02T09:00Z", "of.turn_end", "s-w", {}, position="worker")],
    "labels": [{"rule": "ap.unkept-system", "affected": ["orphan_sys"], "label": "positive"}],
})

SCENARIOS.append({
    "name": "unkept-system/positive-little-work",
    "doc": _owner_doc("quiet_sys", repos="qs"),
    "events": [_wr(i, 2 + i, "quiet_sys") for i in range(4)] +
              [ev(90, "2026-11-02T09:00Z", "of.turn_end", "s-w", {}, position="worker")],
    "labels": [{"rule": "ap.unkept-system", "affected": ["quiet_sys"], "label": "positive"}],   # 4 PRs: under orphan-work@2's 5
})


# ---- keeperless negatives of orphan-work@2 (of_field-analyst, 2026-11-07; registry.md 5523cb6), now unkept-system's ----
# The real cases: Acme's `research`, kept by an external colleague (labelled true_but_not_now), and `partner-feed`,
# unsupported and not running (labelled wrong_remedy). Same 5 work records as the positive; only the registry differs.
def _owner_doc_with(orphan_system, repos, **fields):
    doc = _owner_doc(orphan_system, repos=repos)
    doc["systems"][orphan_system].update(fields)
    return doc


SCENARIOS.append({
    "name": "unkept-system/negative-external-keeper",
    "doc": _owner_doc_with("ext_sys", "ext", governed_by="external:colleague"),
    "events": [_wr(i, 2 + i, "ext_sys") for i in range(5)] +
              [ev(90, "2026-11-02T09:00Z", "of.turn_end", "s-w", {}, position="worker")],
    "labels": [{"rule": "ap.unkept-system", "affected": ["ext_sys"], "label": "negative"}],
})

SCENARIOS.append({
    "name": "unkept-system/negative-unsupported",
    "doc": _owner_doc_with("old_sys", "old", lifecycle="unsupported"),
    "events": [_wr(i, 2 + i, "old_sys") for i in range(5)] +
              [ev(90, "2026-11-02T09:00Z", "of.turn_end", "s-w", {}, position="worker")],
    "labels": [{"rule": "ap.unkept-system", "affected": ["old_sys"], "label": "negative"}],
})


# ---- ap.owner-router@0, affected [founder] ----
# Positive: plumbing share > 0.05 (routing founder prompts, naming NO position so owner-relay stays quiet) AND
# in-degree share > 0.25 (an ask to the owner with no peer requests -> share 1.0). Needs human_prompt +
# agent_question + agent_message_out present or the rule is blocked, so each scenario carries all three.
SCENARIOS.append({
    "name": "owner-router/positive",
    "doc": _org_doc(),
    "events": [prompt(200 + i, f"2026-11-0{1 + i % 4}T1{i}:00Z", "s-bot_operator",
                      "разрули очередь по пулам" if i < 4 else f"как дела {i}", cats=["routing"] if i < 4 else [])
               for i in range(10)] +
              [question(240, "2026-11-01T11:00Z", "s-bot_search"),
               mout(241, "2026-11-03T11:05Z", "s-bot_search", "bot_nonce", position="bot_search")],
    "labels": [{"rule": "ap.owner-router", "affected": [HUMAN], "label": "positive"}],
})

# Negative: no routing prompts (plumbing 0) and peer requests (rq-v0) dilute the in-degree share below 0.25
# (1 ask : 4 peer requests = 0.2). All three input kinds present, so the rule runs and stays quiet.
SCENARIOS.append({
    "name": "owner-router/negative",
    "doc": _org_doc(),
    "events": [prompt(250 + i, f"2026-11-0{2 + i}T10:00Z", "s-bot_operator", f"как дела {i}") for i in range(3)] +
              [question(260, "2026-11-01T09:00Z", "s-bot_search"),
               mout(261, "2026-11-03T11:05Z", "s-bot_search", "bot_nonce", position="bot_search")] +
              [request_in(262 + i, f"2026-11-0{2 + i}T12:00Z", "bot_operator", "bot_search") for i in range(4)],
    "labels": [{"rule": "ap.owner-router", "affected": [HUMAN], "label": "negative"}],
})


# ---- ap.over-coupled@1, affected [two positions sorted] ----
# Positive: 12 reciprocal messages for 2 merged PRs (ratio 6 >= 3, reciprocity 1.0 >= 0.5); both observed >= 3 days;
# no declared waits -> reciprocity_without_waits (1.0 >= 0.8) is the third signal. wr attributed 100% (not blocked).
SCENARIOS.append({
    "name": "over-coupled/positive",
    "doc": _org_doc(),
    "events": turns(["bot_search", "bot_nonce"]) +
              [mout(300 + i, "2026-11-03T10:00Z", "s-bot_search" if i % 2 else "s-bot_nonce",
                    "bot_nonce" if i % 2 else "bot_search",
                    position="bot_search" if i % 2 else "bot_nonce") for i in range(12)] +
              [wrec(320, "2026-11-03T12:00Z", ["bot"], "bot_search"),
               wrec(321, "2026-11-03T12:00Z", ["bot"], "bot_nonce")],
    "labels": [{"rule": "ap.over-coupled", "affected": ["bot_nonce", "bot_search"], "label": "positive"}],
})

# Negative: one-directional traffic (12 bot_search -> bot_nonce, 0 back) -> reciprocity 0 < 0.5. Observed, attributed.
SCENARIOS.append({
    "name": "over-coupled/negative-one-directional",
    "doc": _org_doc(),
    "events": turns(["bot_search", "bot_nonce"]) +
              [mout(330 + i, "2026-11-03T10:00Z", "s-bot_search", "bot_nonce", position="bot_search") for i in range(12)] +
              [wrec(350, "2026-11-03T12:00Z", ["bot"], "bot_search"),
               wrec(351, "2026-11-03T12:00Z", ["bot"], "bot_nonce")],
    "labels": [{"rule": "ap.over-coupled", "affected": ["bot_nonce", "bot_search"], "label": "negative"}],
})


# ---- ap.lingering@0, affected [position] ----
# Both labels in one doc: `temp` is active, its review 2026-10-30 is > 48 h past (due = review + 24 h), not extended
# -> fires; `fresh` review 2026-11-06 is within 48 h of B -> quiet. No mandate_functions -> function-gap blocked.
_LINGER_DOC = {
    "organization": "acme",
    "systems": {"x": {"kind": "target"}},
    "positions": [
        {"name": "temp", "system": "x", "role": "worker", "kind": "project", "owner": "founder", "status": "active",
         "mandate_exit": {"done_when": "the migration is done", "review": "2026-10-30"}, "holder": {"session": "s-temp"}},
        {"name": "fresh", "system": "x", "role": "worker", "kind": "project", "owner": "founder", "status": "active",
         "mandate_exit": {"done_when": "the backfill is done", "review": "2026-11-06"}, "holder": {"session": "s-fresh"}},
    ],
}
SCENARIOS.append({
    "name": "lingering/positive-and-negative",
    "doc": _LINGER_DOC,
    "events": [],
    "labels": [{"rule": "ap.lingering", "affected": ["temp"], "label": "positive"},
               {"rule": "ap.lingering", "affected": ["fresh"], "label": "negative"}],
})


# ---- ap.chatty@0 part receiver_flood, affected [receiver] ----
# >= 20 injected turns/hour (relayed messages) on >= 3 hours. No work_record -> default part blocked; no human_prompt
# -> repeat_nudges blocked; so only receiver_flood runs and fires.
SCENARIOS.append({
    "name": "chatty-receiver-flood/positive",
    "doc": _org_doc(),
    "events": [relay_in(400 + h * 20 + i, f"2026-11-03T1{h}:{i:02d}Z", "bot_operator", "bot_search")
               for h in range(3) for i in range(20)],
    "labels": [{"rule": "ap.chatty", "affected": ["bot_operator"], "label": "positive"}],
})

# Negative: 10 relayed turns in a single hour -> no hour clears 20, no flood.
SCENARIOS.append({
    "name": "chatty-receiver-flood/negative",
    "doc": _org_doc(),
    "events": [relay_in(460 + i, f"2026-11-03T10:{i:02d}Z", "bot_operator", "bot_search") for i in range(10)],
    "labels": [{"rule": "ap.chatty", "affected": ["bot_operator"], "label": "negative"}],
})


# ---- ap.chatty@0 part repeat_nudges, affected [waiter, receiver] sorted ----
# One waiter nudging the same peer >= 10 times (nudge = a human_prompt in of pulse's frame). No work_record / no
# agent_message_in -> the other two parts blocked.
SCENARIOS.append({
    "name": "chatty-repeat-nudges/positive",
    "doc": _org_doc(),
    "events": [nudge(500 + i, f"2026-11-0{2 + i % 5}T10:0{i % 6}Z", "bot_search", "bot_operator") for i in range(10)],
    "labels": [{"rule": "ap.chatty", "affected": ["bot_operator", "bot_search"], "label": "positive"}],
})

# Negative: only 5 nudges -> below nudges_per_pair_gte (10).
SCENARIOS.append({
    "name": "chatty-repeat-nudges/negative",
    "doc": _org_doc(),
    "events": [nudge(520 + i, f"2026-11-0{2 + i}T10:00Z", "bot_search", "bot_operator") for i in range(5)],
    "labels": [{"rule": "ap.chatty", "affected": ["bot_operator", "bot_search"], "label": "negative"}],
})


# ---- development-loop rules, diagnose.md 2.16-2.22 (of_field-analyst, 2026-11-07) ----
# One work with a loop; ladder offline < gate < day_pass < shadow (production). Each scenario trips only its rule:
# the loop starts within checkpoint_days of the window end unless the checkpoint is the rule under test, at most
# wip_arms hypotheses are in test, runs climb the ladder, no run gives started_at, every run measures the target,
# and no tool call is classed runner or monitor, except where the scenario tests that.
_LW = "w-search"


def _loop_doc(**over):
    spec = {"target": {"metric": "day_pass", "money": "USD per day", "stake": "65 USD a day"},
            "measures": [{"name": "latency_ms"}],
            "ladder": [{"rung": "offline", "latency": "5 min"}, {"rung": "gate", "latency": "40 min"},
                       {"rung": "day_pass", "latency": "4 h"}, {"rung": "shadow", "production": True}],
            "checkpoint_days": 3, "wip_arms": 2, "babysit_model": "sonnet"}
    spec.update(over)
    return {"organization": "acme",
            "systems": {"bot": {"kind": "target", "keeper": "bot_operator", "repos": ["arb"]}},
            "positions": [{"name": "bot_operator", "system": "bot", "role": "keeper", "kind": "keeper", "owner": "founder",
                           "holder": {"session": "s-bot_operator"}},
                          {"name": "bot_search", "system": "bot", "role": "search", "kind": "project",
                           "owner": "bot_operator", "holder": {"session": "s-bot_search"}}],
            "works": {_LW: {"owner": "bot_operator", "doers": ["bot_search"], "loop": spec}}}


def _lrun(i, ts, rung, hyp, value=None, metric=None, queued=None, started=None):
    pl = {"work": _LW, "run": f"r{i}", "rung": rung, "hypothesis": hyp, "value": value, "accepted": True, "ended_at": ts}
    pl.update({k: v for k, v in (("metric", metric), ("queued_at", queued), ("started_at", started)) if v})
    return ev(f"lr{i}", ts, "of.loop_run", "s-bot_search", pl, position="bot_search")


def _climb(i0, day, hyp, value=300, hour=12):
    """offline, gate and day_pass for one hypothesis on one day: a run that skips nothing."""
    return [_lrun(i0 + k, f"2026-11-0{day}T{hour + k}:00Z", r, hyp, value if r == "day_pass" else None)
            for k, r in enumerate(("offline", "gate", "day_pass"))]


def _calls(model, n):
    return [ev(f"mc{model[:9]}{i}", f"2026-11-06T{10 + i % 10}:{i % 60:02d}Z", "of.mandate_check", "s-bot_search",
               {"command_class": "runner" if i % 3 else "monitor", "model": model, "tool_name": "Bash", "verdict": "in_mandate"},
               position="bot_search") for i in range(n)]


def _loop_case(name, events, label, doc=None, rule=None):
    return {"name": name, "doc": doc or _loop_doc(), "events": events,
            "labels": [{"rule": rule, "affected": [_LW], "label": label}]}


SCENARIOS += [
    _loop_case("loop-cheapest-rung/positive",
               [_lrun(1, "2026-11-06T14:00Z", "day_pass", "h1", 300), _lrun(2, "2026-11-07T14:00Z", "day_pass", "h2", 310)],
               "positive", rule="ap.loop-cheapest-rung"),
    _loop_case("loop-cheapest-rung/negative-climbs", _climb(1, 6, "h1") + _climb(5, 7, "h2", 310),
               "negative", rule="ap.loop-cheapest-rung"),
    _loop_case("loop-checkpoint/positive",
               [_lrun(i, f"2026-11-0{d}T12:00Z", "offline", "h1") for i, d in enumerate((2, 4, 7), 1)],
               "positive", rule="ap.loop-checkpoint"),
    _loop_case("loop-checkpoint/negative-shadow",
               [_lrun(i, f"2026-11-0{d}T12:00Z", "offline", "h1") for i, d in enumerate((2, 4, 7), 1)] +
               [_lrun(9, "2026-11-06T18:00Z", "shadow", "h1")],
               "negative", rule="ap.loop-checkpoint"),
    _loop_case("loop-plateau/positive",
               _climb(1, 5, "h1", 300) + [_lrun(10 + k, f"2026-11-0{6 + k // 2}T{10 + 4 * (k % 2)}:00Z", "day_pass", "h1", v)
                                          for k, v in enumerate((300.5, 301.0, 300.9))],
               "positive", rule="ap.loop-plateau"),
    _loop_case("loop-plateau/negative-rising",
               _climb(1, 5, "h1", 300) + [_lrun(10 + k, f"2026-11-0{6 + k // 2}T{10 + 4 * (k % 2)}:00Z", "day_pass", "h1", v)
                                          for k, v in enumerate((310, 320, 330))],
               "negative", rule="ap.loop-plateau"),
    _loop_case("loop-wip/positive", [_lrun(i, "2026-11-06T10:00Z", "offline", f"h{i}") for i in (1, 2, 3)],
               "positive", rule="ap.loop-wip"),
    _loop_case("loop-wip/negative-at-limit", [_lrun(i, "2026-11-06T10:00Z", "offline", f"h{i}") for i in (1, 2)],
               "negative", rule="ap.loop-wip"),
    _loop_case("loop-babysit-model/positive", [_lrun(1, "2026-11-06T12:00Z", "offline", "h1")] + _calls("claude-fable-5-1", 120),
               "positive", rule="ap.loop-babysit-model"),
    _loop_case("loop-babysit-model/negative-sonnet",
               [_lrun(1, "2026-11-06T12:00Z", "offline", "h1")] + _calls("claude-sonnet-5-5", 120),
               "negative", rule="ap.loop-babysit-model"),
    _loop_case("loop-objective-drift/positive",
               [_lrun(1, "2026-11-06T09:00Z", "offline", "h1")] +
               [_lrun(i, f"2026-11-06T1{i}:00Z", "offline", "h1", 18 - i, metric="latency_ms") for i in range(2, 6)],
               "positive", rule="ap.loop-objective-drift"),
    _loop_case("loop-objective-drift/negative-priced",
               [_lrun(1, "2026-11-06T09:00Z", "offline", "h1")] +
               [_lrun(i, f"2026-11-06T1{i}:00Z", "offline", "h1", 18 - i, metric="latency_ms") for i in range(2, 6)],
               "negative", rule="ap.loop-objective-drift",
               doc=_loop_doc(measures=[{"name": "latency_ms", "to_money": "USD per ms from the race arithmetic"}])),
    _loop_case("loop-eval-bottleneck/positive",
               [_lrun(1, "2026-11-05T13:00Z", "offline", "h1")] +
               [_lrun(1 + d, f"2026-11-0{5 + d}T13:00Z", "gate", "h1", queued=f"2026-11-0{5 + d}T10:00Z",
                      started=f"2026-11-0{5 + d}T12:00Z") for d in (0, 1, 2)],
               "positive", rule="ap.loop-eval-bottleneck"),
    _loop_case("loop-eval-bottleneck/negative-short-wait",
               [_lrun(1, "2026-11-05T13:00Z", "offline", "h1")] +
               [_lrun(1 + d, f"2026-11-0{5 + d}T13:00Z", "gate", "h1", queued=f"2026-11-0{5 + d}T10:00Z",
                      started=f"2026-11-0{5 + d}T10:05Z") for d in (0, 1, 2)],
               "negative", rule="ap.loop-eval-bottleneck"),
]


# ---- the older catalogue rules, diagnose.md 2.2-2.15 (of_field-analyst, 2026-11-10) ----
# A positive and a benign look-alike per rule, and per part where the parts read different inputs. Each scenario trips
# only its rule: the inputs another rule needs are left out, so that rule reports blocked. Where the calibration found
# a false positive, the negative is that case: a busy pair's status traffic and a unit owner's own unit (owner-relay@1),
# an unobserved keeper (coordination-gap@1), a keeper named inside the windows (fragmented-ownership@1), a coordinator
# by mandate (bottleneck-broker@1), schema adoption and onboarding (unclear-mandate@1), scratch paths and verdicts from
# before rt-v4 (orphan-work cluster). context-overload and missing-verifier read of.correction, which is not recorded:
# their check is that they report blocked.
import datetime as _dt   # noqa: E402

import of_model   # noqa: E402  (harness puts bin/ on the path)
from harness import epoch   # noqa: E402


def _at(day, hour=10, minute=0):
    """'11-03' -> 2026-11-03T10:00Z; '10-28' for the window before A."""
    return f"2026-{day}T{hour:02d}:{minute:02d}Z"


def _day(d0, k):
    return (_dt.date(2026, int(d0[:2]), int(d0[3:])) + _dt.timedelta(days=k)).strftime("%m-%d")


def _pr(i, ts, systems, position, number):
    return ev(i, ts, "of.work_record", sid_of(position),
              {"source": "gh", "author": "founder-gh", "systems": systems, "repo": "o/r", "number": number}, position=position)


def _case(name, doc, events, *labels, context=None):
    sc = {"name": name, "doc": doc, "events": events,
          "labels": [{"rule": r, "affected": list(aff), "label": lab} for r, aff, lab in labels]}
    if context:
        sc["context"] = context
    return sc


# ---- ap.owner-relay@1, affected [p, q] ----
# Relay half: the owner's routing prompts to q's session naming p, >= 3. Words half: messages q received from p with the
# `relayed` label, >= 5 and >= 10% of the pair's. No of.agent_question: owner-router is blocked.
def _route(i, ts, recv, named):
    return prompt(i, ts, sid_of(recv), f"спроси у {named}, готов ли пул", cats=["routing"], position=recv)


def _words_in(i, ts, recv, sender, relayed):
    body = "the founder approved the pool swap, go ahead" if relayed else f"status {i}: the pool is synced"
    return ev(i, ts, "of.agent_message_in", sid_of(recv),
              {"text": f'<cross-session-message from="uds:x" from-name="{sender}">{body}', "from_position": sender,
               "classifications": {FA: {"relayed": relayed}}}, position=recv)


def _pair_words(i0, relayed, total, days=5):
    """`total` messages bot_search -> bot_nonce over `days` days, one an hour, the first `relayed` carrying owner words."""
    per = -(-total // days)
    return [_words_in(f"{i0}-{k}", _at(_day("11-02", k // per), 6 + k % per), "bot_nonce", "bot_search", k < relayed)
            for k in range(total)]


_RELAY_OUT = [mout("ro-out", _at("11-02", 9), "s-bot_search", "bot_nonce", position="bot_search")]


def _unit_owner_doc():
    doc = _org_doc()
    doc["positions"][0]["kind"] = "owner"   # bot_operator owns the unit bot_search works in
    return doc


SCENARIOS += [
    _case("owner-relay/positive-relays", _org_doc(),
          [_route(f"rr{k}", _at(d, 11), "bot_nonce", "bot_search") for k, d in enumerate(("11-03", "11-04", "11-05"))]
          + _RELAY_OUT,
          ("ap.owner-relay", ["bot_nonce", "bot_search"], "positive")),
    _case("owner-relay/positive-owner-words", _org_doc(),
          _pair_words("rw", 6, 20) + _RELAY_OUT + [prompt("rw-p", _at("11-03", 8), "s-bot_operator", "как дела")],
          ("ap.owner-relay", ["bot_nonce", "bot_search"], "positive")),
    # a busy pair: 6 messages with owner words among 80 (7.5% < 10%) is status reporting, not the owner's channel
    _case("owner-relay/negative-busy-pair", _org_doc(),
          _pair_words("rb", 6, 80) + _RELAY_OUT + [prompt("rb-p", _at("11-03", 8), "s-bot_operator", "как дела")],
          ("ap.owner-relay", ["bot_nonce", "bot_search"], "negative")),
    # talking to a unit owner about its unit's position is delegation, not a relay (Acme, 2026-11-06)
    _case("owner-relay/negative-unit-owner", _unit_owner_doc(),
          [_route(f"ru{k}", _at(d, 11), "bot_operator", "bot_search") for k, d in enumerate(("11-03", "11-04", "11-05"))]
          + _RELAY_OUT,
          ("ap.owner-relay", ["bot_operator", "bot_search"], "negative")),
]


# ---- ap.coordination-gap@1, affected [both systems, both keepers] ----
# api declares depends_on db; both keepers active and observed on 3 days; they exchange nothing. One work record and one
# message elsewhere make the rule's inputs present.
def _gap_doc():
    return {"organization": "acme",
            "systems": {"api": {"kind": "target", "keeper": "api_keeper", "repos": ["api"],
                                "depends_on": [{"system": "db", "kind": "calls"}]},
                        "db": {"kind": "target", "keeper": "db_keeper", "repos": ["db"]}},
            "positions": [
                {"name": "api_keeper", "system": "api", "role": "keeper", "kind": "keeper", "owner": "founder",
                 "holder": {"session": "s-api_keeper"}},
                {"name": "db_keeper", "system": "db", "role": "keeper", "kind": "keeper", "owner": "founder",
                 "holder": {"session": "s-db_keeper"}},
                {"name": "api_dev", "system": "api", "role": "dev", "kind": "project", "owner": "api_keeper",
                 "holder": {"session": "s-api_dev"}}]}


_GAP_BASE = [_pr("cg-wr", _at("11-03"), ["api"], "api_dev", 8001),
             mout("cg-out", _at("11-03", 11), "s-api_keeper", "api_dev", position="api_keeper")]
_GAP = ["api", "api_keeper", "db", "db_keeper"]

SCENARIOS += [
    _case("coordination-gap/positive", _gap_doc(), turns(["api_keeper", "db_keeper"]) + _GAP_BASE,
          ("ap.coordination-gap", _GAP, "positive")),
    _case("coordination-gap/negative-met", _gap_doc(),
          turns(["api_keeper", "db_keeper"]) + _GAP_BASE +
          [mout("cg-met", _at("11-04", 15), "s-api_keeper", "db_keeper", position="api_keeper")],
          ("ap.coordination-gap", _GAP, "negative")),
    # db_keeper shows activity on 2 days only: its silence is not evidence, and the rule says so (blocked)
    _case("coordination-gap/blocked-unobserved-keeper", _gap_doc(),
          turns(["api_keeper"]) + turns(["db_keeper"], days=("2026-11-02", "2026-11-04")) + _GAP_BASE,
          ("ap.coordination-gap", _GAP, "blocked")),
]


# ---- ap.fragmented-ownership@1 part code, affected [system, keeper] ----
# core has 4 writers in both windows and its keeper wrote 1 of 7 (0.14 < 0.3). No of.deployment: the runtime part is
# blocked; no messages: over-coupled and chatty are blocked.
def _frag_doc():
    ps = [{"name": "core_keeper", "system": "core", "role": "keeper", "kind": "keeper", "owner": "founder",
           "holder": {"session": "s-core_keeper"}}]
    ps += [{"name": f"core_w{k}", "system": "core", "role": f"w{k}", "kind": "project", "owner": "core_keeper",
            "holder": {"session": f"s-core_w{k}"}} for k in (1, 2, 3)]
    return {"organization": "acme", "systems": {"core": {"kind": "target", "keeper": "core_keeper", "repos": ["core"]}},
            "positions": ps}


def _frag_window(i0, first_day, keeper_prs):
    out, n = [], 0
    for who, prs in (("core_w1", 2), ("core_w2", 2), ("core_w3", 2), ("core_keeper", keeper_prs)):
        for _ in range(prs):
            out.append(_pr(f"{i0}-{n}", _at(_day(first_day, n % 5), 12), ["core"], who, i0 * 100 + n))
            n += 1
    return out


_FRAG = ["core", "core_keeper"]
SCENARIOS += [
    _case("fragmented-ownership/positive", _frag_doc(), _frag_window(81, "10-26", 1) + _frag_window(82, "11-02", 1),
          ("ap.fragmented-ownership", _FRAG, "positive")),
    # the keeper was named on 2026-11-03: the windows measure writes made before it held the system (obs, wholesale on Acme)
    _case("fragmented-ownership/negative-keeper-named-in-window", _frag_doc(),
          _frag_window(83, "10-26", 1) + _frag_window(84, "11-02", 1),
          ("ap.fragmented-ownership", _FRAG, "negative"),
          context={"keeper_since": lambda system, keeper: epoch("2026-11-03T00:00Z")}),
    # one window only: the window before, the keeper wrote 6 of 12 (0.5)
    _case("fragmented-ownership/negative-one-window", _frag_doc(),
          _frag_window(85, "10-26", 6) + _frag_window(86, "11-02", 1),
          ("ap.fragmented-ownership", _FRAG, "negative")),
]


# ---- ap.bottleneck-broker@1, affected [position] ----
# A star: four positions each send the hub 5 messages (strong ties), none to each other. The hub's inbound share is
# 1.0 and it sits on every shortest path (betweenness 1.0). No work records: over-coupled and chatty are blocked.
def _star_doc(hub_kind="keeper", spokes=("sp1", "sp2", "sp3", "sp4")):
    ps = [{"name": "hub", "system": "bot", "role": "hub", "kind": hub_kind, "owner": "founder",
           "holder": {"session": "s-hub"}}]
    ps += [{"name": s, "system": "bot", "role": s, "kind": "project", "owner": "hub", "holder": {"session": f"s-{s}"}}
           for s in spokes]
    return {"organization": "acme", "systems": {"bot": {"kind": "target", "keeper": "hub", "repos": ["arb"]}},
            "positions": ps}


_SPOKES = ("sp1", "sp2", "sp3", "sp4")
_STAR = (turns(("hub",) + _SPOKES) +
         [mout(f"bb-{s}-{k}", _at(_day("11-02", k), 9 + j), f"s-{s}", "hub", position=s)
          for j, s in enumerate(_SPOKES) for k in range(5)] +
         [relay_in("bb-in", _at("11-03", 15), "hub", "sp1")])


def _mesh():
    """Four positions, each pair 3 messages each way: a complete graph, betweenness 0, inbound share 0.25 each."""
    names = ("sp1", "sp2", "sp3", "sp4")
    out = [mout(f"bm-{p}-{q}-{k}", _at(_day("11-02", k), 9 + names.index(q)), f"s-{p}", q, position=p)
           for p in names for q in names if p != q for k in range(3)]
    return turns(names) + out + [relay_in("bm-in", _at("11-03", 15), "sp1", "sp2")]


SCENARIOS += [
    _case("bottleneck-broker/positive-star", _star_doc(), _STAR, ("ap.bottleneck-broker", ["hub"], "positive")),
    # the same star around a coordinator: brokering is its mandate, and chatty judges its traffic (diagnose.md 2.8)
    _case("bottleneck-broker/negative-coordinator", _star_doc("coordinator"), _STAR,
          ("ap.bottleneck-broker", ["hub"], "negative")),
    _case("bottleneck-broker/negative-mesh", _star_doc(), _mesh(),
          *[("ap.bottleneck-broker", [s], "negative") for s in _SPOKES]),
]


# ---- ap.unclear-mandate@1, affected [position] ----
# of.mandate_changed snapshots; the first one is the baseline. No work records: churn is blocked.
def _mc(i, ts, pos, **fields):
    pl = {"position": pos, "mandate": {"path": f"briefs/{pos}.md", "revision": "r1"}, "summary": "search the pools",
          "status": "active", "throughput": "raise"}
    pl.update(fields)
    return ev(i, ts, "of.mandate_changed", None, pl, position=pos, source="of")


SCENARIOS += [
    # throughput flips raise -> sustain -> raise inside the window
    _case("unclear-mandate/positive-flip-flop", _org_doc(),
          [_mc("um1", _at("10-20"), "bot_search"), _mc("um2", _at("11-03"), "bot_search", throughput="sustain"),
           _mc("um3", _at("11-05"), "bot_search", throughput="raise")],
          ("ap.unclear-mandate", ["bot_search"], "positive")),
    # bot_nonce: the schema gains mandate_exit and the summary is reworded twice (not substantive);
    # bot_operator: two throughput changes within settle_days of its first snapshot (onboarding)
    _case("unclear-mandate/negative-schema-and-onboarding", _org_doc(),
          [_mc("un1", _at("10-20"), "bot_nonce"),
           _mc("un2", _at("11-03"), "bot_nonce", summary="search the pools (prod)",
               mandate_exit={"done_when": "the nonce fix ships"}),
           _mc("un3", _at("11-05"), "bot_nonce", summary="search the pools in production",
               mandate_exit={"done_when": "the nonce fix ships"}),
           _mc("un4", _at("11-02"), "bot_operator"), _mc("un5", _at("11-03"), "bot_operator", throughput="sustain"),
           _mc("un6", _at("11-04"), "bot_operator", throughput="raise")],
          ("ap.unclear-mandate", ["bot_nonce"], "negative"), ("ap.unclear-mandate", ["bot_operator"], "negative")),
]


# ---- ap.function-gap@0, affected [function:<f>] ----
# Only the registry: `marketing` is covered by a parked position (positive) or an active one (negative).
def _functions_doc(growth_status):
    rest = [f for f in of_model.MANAGEMENT_FUNCTIONS if f != "marketing"]
    return {"organization": "acme", "systems": {"bot": {"kind": "target", "keeper": "lead", "repos": ["arb"]}},
            "positions": [
                {"name": "lead", "system": "bot", "role": "lead", "kind": "keeper", "owner": "founder",
                 "mandate_functions": rest, "holder": {"session": "s-lead"}},
                {"name": "growth", "system": "bot", "role": "growth", "kind": "project", "owner": "lead",
                 "status": growth_status, "mandate_functions": ["marketing"], "holder": {"session": "s-growth"}}]}


SCENARIOS += [
    _case("function-gap/positive-parked-cover", _functions_doc("parked"), [],
          ("ap.function-gap", ["function:marketing"], "positive")),
    _case("function-gap/negative-covered", _functions_doc("active"), [],
          ("ap.function-gap", ["function:marketing"], "negative")),
]


# ---- ap.churn@0, affected [position] or [day:<date>] ----
# bot_operator merges 4 PRs a day from 10-21 to 11-07; holder changes, and the PRs of the days they happen on.
# One in-window baseline snapshot of bot_nonce makes of.mandate_changed present (unclear-mandate stays quiet).
def _churn_events(i0, dip_days, holder_changes):
    out = [_mc(f"{i0}-mc", _at("11-02", 8), "bot_nonce")]
    n = 0
    for k in range(18):
        d = _day("10-21", k)
        for j in range(1 if d in dip_days else 4):
            out.append(_pr(f"{i0}-wr{n}", _at(d, 9 + j), ["bot"], "bot_operator", i0 * 1000 + n))
            n += 1
    for m, (pos, d) in enumerate(holder_changes):
        out.append(ev(f"{i0}-hc{m}", _at(d, 8), "of.holder_changed", None,
                      {"position": pos, "from_session": f"s-old-{m}", "to_session": sid_of(pos), "reason": "account"},
                      position=pos, source="of"))
    return out


SCENARIOS += [
    # two holder changes of bot_search, and merged PRs fall from 4 a day to 1 on both days
    _case("churn/positive-position", _org_doc(),
          _churn_events(91, ("11-04", "11-05"), [("bot_search", "11-04"), ("bot_search", "11-05")]),
          ("ap.churn", ["bot_search"], "positive")),
    # an account move that carried its state: two holder changes, merged PRs steady at 4 a day
    _case("churn/negative-no-dip", _org_doc(),
          _churn_events(92, (), [("bot_search", "11-04"), ("bot_search", "11-05")]),
          ("ap.churn", ["bot_search"], "negative")),
    # one day moves 2 of 3 active positions (an account move of many sessions), and merged PRs fall that day
    _case("churn/positive-one-day", _org_doc(),
          _churn_events(93, ("11-04",), [("bot_search", "11-04"), ("bot_nonce", "11-04")]),
          ("ap.churn", ["day:2026-11-04"], "positive")),
]


# ---- ap.inbound-backlog@0 parts overload and unattended, affected [position] ----
# of.inbound_queue steps (the relay writes one on each change of depth). Only queue events: every other rule is blocked.
def _q(i, ts, pos, depth, oldest=None):
    return ev(i, ts, "of.inbound_queue", None, {"position": pos, "depth": depth, "oldest_created_at": oldest},
              source="of")


_QDAYS = ("11-03", "11-04", "11-05", "11-06", "11-07")


def _unheld_doc():
    doc = _org_doc()
    del doc["positions"][2]["holder"]   # bot_nonce: nobody holds it
    return doc


def _drained(i0, pos, days):
    """A queue taken within 20 minutes on each day: observed, no backlog."""
    return [x for k, d in enumerate(days) for x in (_q(f"{i0}-{k}a", _at(d, 14), pos, 2, _at(d, 14)),
                                                     _q(f"{i0}-{k}b", _at(d, 14, 20), pos, 0))]


SCENARIOS += [
    # bot_operator holds 3-4 messages for days, the oldest always past 30 min
    _case("inbound-backlog/positive-overload", _org_doc(),
          [_q(f"ib{k}", _at(d), "bot_operator", 3 + k % 2, _at(d, 9)) for k, d in enumerate(_QDAYS)],
          ("ap.inbound-backlog", ["bot_operator"], "positive")),
    _case("inbound-backlog/negative-overload-drained", _org_doc(), _drained("ibd", "bot_operator", _QDAYS),
          ("ap.inbound-backlog", ["bot_operator"], "negative")),
    # messages for bot_nonce, which nobody holds, wait 2 hours on two days
    _case("inbound-backlog/positive-unattended", _unheld_doc(),
          [x for k, d in enumerate(("11-03", "11-04")) for x in (_q(f"iu{k}a", _at(d), "bot_nonce", 1, _at(d, 9)),
                                                                 _q(f"iu{k}b", _at(d, 12), "bot_nonce", 0))]
          + _drained("iuo", "bot_operator", ("11-05",)),
          ("ap.inbound-backlog", ["bot_nonce"], "positive")),
    # one such day only (unattended needs 2); the other days are observed and drained
    _case("inbound-backlog/negative-unattended-one-day", _unheld_doc(),
          [_q("iv0a", _at("11-03"), "bot_nonce", 1, _at("11-03", 9)), _q("iv0b", _at("11-03", 12), "bot_nonce", 0)]
          + _drained("ivo", "bot_nonce", ("11-04", "11-05")),
          ("ap.inbound-backlog", ["bot_nonce"], "negative")),
]


# ---- ap.chatty@1 default part: pair and organization ----
# Pair: 12 messages one way (reciprocity 0, so over-coupled stays quiet) for 1 merged PR; both sides observed.
# No of.agent_message_in: bottleneck-broker and receiver_flood are blocked.
def _one_way(i0, n, p, q, first="11-02", hour0=8):
    return [mout(f"{i0}-{k}", _at(_day(first, k % 5), hour0 + k // 5), sid_of(p), q, position=p) for k in range(n)]


SCENARIOS += [
    _case("chatty-pair/positive", _org_doc(),
          turns(["bot_search", "bot_operator"]) + _one_way("cp", 12, "bot_search", "bot_operator")
          + [_pr("cp-wr", _at("11-03", 16), ["bot"], "bot_search", 8101)],
          ("ap.chatty", ["bot_operator", "bot_search"], "positive")),
    # 12 messages for 2 merged PRs: 6 per PR, below 10
    _case("chatty-pair/negative", _org_doc(),
          turns(["bot_search", "bot_operator"]) + _one_way("cq", 12, "bot_search", "bot_operator")
          + [_pr("cq-wr1", _at("11-03", 16), ["bot"], "bot_search", 8111),
             _pr("cq-wr2", _at("11-04", 16), ["bot"], "bot_operator", 8112)],
          ("ap.chatty", ["bot_operator", "bot_search"], "negative")),
]


# Organization: the window before (10-26 to 11-01, as wide as the window's ended days) had 10 messages for 10 PRs;
# the window has `now` messages for 10 PRs by 11-07, when the last imported day ends. No pair reaches 10 per PR.
def _org_chatter(i0, now):
    before = _one_way(f"{i0}b", 10, "bot_search", "bot_nonce", first="10-27") + \
        [_pr(f"{i0}bw{k}", _at("10-28", 9 + k), ["bot"], ("bot_search", "bot_nonce")[k % 2], i0 * 100 + k) for k in range(10)]
    msgs = _one_way(f"{i0}m", now // 2, "bot_search", "bot_nonce") + \
        _one_way(f"{i0}n", now - now // 2, "bot_nonce", "bot_operator", hour0=14)
    prs = [_pr(f"{i0}w{k}", _at(_day("11-03", k % 4), 18), ["bot"], ("bot_search", "bot_nonce")[k % 2], i0 * 100 + 50 + k)
           for k in range(10)] + [_pr(f"{i0}wl", _at("11-07", 12), ["bot"], "bot_nonce", i0 * 100 + 99)]
    return before + msgs + prs


SCENARIOS += [
    _case("chatty-org/positive", _org_doc(), _org_chatter(71, 20), ("ap.chatty", ["(organization)"], "positive")),
    _case("chatty-org/negative", _org_doc(), _org_chatter(72, 12), ("ap.chatty", ["(organization)"], "negative")),
]


# ---- ap.orphan-work@2 parts cluster and unmapped ----
def _home_doc():
    return {"organization": "acme",
            "systems": {"home_sys": {"kind": "target", "keeper": "home_keeper", "repos": ["home"]}},
            "positions": [
                {"name": "home_keeper", "system": "home_sys", "role": "keeper", "kind": "keeper", "owner": "founder",
                 "holder": {"session": "s-home"}},
                {"name": "worker", "system": "home_sys", "role": "worker", "kind": "project", "owner": "home_keeper",
                 "holder": {"session": "s-w"}}]}


def _unowned(i0, path, n=3, router="rt-v4"):
    return [ev(f"{i0}-{k}", _at(_day("11-03", k), 11), "of.mandate_check", "s-w",
               {"verdict": "unowned", "path": path, "tool_name": "Edit", "router_version": router}, position="worker")
            for k in range(n)]


def _mapped(i0, n_mapped, n_unmapped):
    return [_pr(f"{i0}-{k}", _at(_day("11-02", k % 5), 8 + k // 5), ["home_sys"] if k < n_mapped else [], "worker",
                i0 * 100 + k) for k in range(n_mapped + n_unmapped)]


SCENARIOS += [
    _case("orphan-work-cluster/positive", _home_doc(), _unowned("oc", "~/acme/newtool/src/a.py"),
          ("ap.orphan-work", ["~/acme/newtool"], "positive")),
    # a scratchpad is not a work area (diagnose.md 2.7)
    _case("orphan-work-cluster/negative-scratch", _home_doc(),
          _unowned("os", "/private/tmp/claude-501/proj/scratchpad/a.py"),
          ("ap.orphan-work", ["private/tmp/claude-501"], "negative")),
    # rt-v3 judged a linked worktree outside the repository unowned; such verdicts count from rt-v4 on
    _case("orphan-work-cluster/negative-before-rt-v4", _home_doc(), _unowned("ow", "~/home-wt/feature/a.py", router="rt-v3"),
          ("ap.orphan-work", ["~/home-wt/feature"], "negative")),
    _case("orphan-work-unmapped/positive", _home_doc(), _mapped(61, 20, 2),
          ("ap.orphan-work", ["(unmapped)"], "positive")),
    _case("orphan-work-unmapped/negative", _home_doc(), _mapped(62, 30, 1),
          ("ap.orphan-work", ["(unmapped)"], "negative")),
]


# ---- context-overload@0 and missing-verifier@0: not built, of.correction not recorded ----
# Their other inputs are present (compactions; attributed work records); the check is that both report blocked.
SCENARIOS.append(_case(
    "blocked/no-corrections", _org_doc(),
    [ev(f"cx{k}", _at(_day("11-03", k % 2), 9 + k // 2), "of.compaction", "s-bot_search", {}, position="bot_search")
     for k in range(12)] +
    [_pr(f"mv{k}", _at(_day("11-02", k), 15), ["bot"], "bot_search", 8200 + k) for k in range(5)],
    ("ap.context-overload", ["bot_search"], "blocked"), ("ap.missing-verifier", ["bot_search"], "blocked")))


# chatty's default part divides by attributed merged PRs: with none attributed it reports blocked, not 12 per PR
# (Acme, 2026-11-10: 90 of 820 attributed, 133 pair issues). No relayed messages or prompts: the other parts block.
SCENARIOS.append(_case(
    "chatty-pair/blocked-unattributed", _org_doc(),
    turns(["bot_search", "bot_operator"]) + _one_way("cu", 12, "bot_search", "bot_operator")
    + [ev("cu-wr", _at("11-03", 16), "of.work_record", "s-ci",
          {"source": "gh", "author": "founder-gh", "systems": ["bot"], "repo": "o/r", "number": 8121})],
    ("ap.chatty", ["bot_operator", "bot_search"], "blocked")))



# ---- structure coverage, diagnose.md 2.23-2.26 (of_field-analyst, 2026-11-10; of_architect's yes the same day) ----
# The registry's coverage as Studio shows it, routed to the structure keeper. Events only where the rule reads them.
def _with(doc, systems=None, positions=(), units=None):
    doc = dict(doc, systems={**doc["systems"], **(systems or {})}, positions=doc["positions"] + list(positions))
    if units:
        doc["units"] = units
    return doc


SCENARIOS += [
    # delivery on Acme: live, kept by nobody, 4 merged PRs (under orphan-work@2's 5); here no work at all
    _case("unkept-system/positive-no-work", _with(_org_doc(), {"relay": {"kind": "enabling"}}), [],
          ("ap.unkept-system", ["relay"], "positive")),
    _case("unkept-system/positive-keeper-parked",
          _with(_org_doc(), {"relay": {"kind": "enabling", "keeper": "relay_keeper"}},
                [{"name": "relay_keeper", "system": "relay", "role": "keeper", "kind": "keeper", "owner": "founder",
                  "status": "parked"}]), [],
          ("ap.unkept-system", ["relay"], "positive")),
    # trading on Acme: a supersystem with no keeper whose every part is kept
    _case("unkept-system/negative-supersystem",
          _with(_org_doc(), {"trading": {"kind": "target"}, "bot": dict(_org_doc()["systems"]["bot"], parent="trading")}), [],
          ("ap.unkept-system", ["trading"], "negative")),
    _case("unkept-system/positive-no-unit",
          _with(_org_doc(), {"relay": {"kind": "enabling", "keeper": "bot_operator"}},
                units={"core": {"owner": "bot_operator", "systems": ["bot"]}}), [],
          ("ap.unkept-system", ["relay"], "positive")),
    # the same supersystem in an organization with units: its parts are in units, so it needs none
    _case("unkept-system/negative-supersystem-no-unit",
          _with(_org_doc(), {"trading": {"kind": "target"}, "bot": dict(_org_doc()["systems"]["bot"], parent="trading")},
                units={"core": {"owner": "bot_operator", "systems": ["bot"]}}), [],
          ("ap.unkept-system", ["trading"], "negative")),
]


# ---- ap.off-map-work@0, affected [position] ----
# bot_nonce (system bot) edits files of `core`, kept by bot_operator, as storefront's positions edit bot-core on Acme.
def _core_doc():
    return _with(_org_doc(), {"core": {"kind": "target", "keeper": "bot_operator", "repos": ["core"]}})


def _verdicts(i0, n, verdict, tool="Edit", system="core", rv="rt-v5", pos="bot_nonce"):
    return [ev(f"{i0}-{k}", _at(_day("11-02", k % 5), 9 + k // 5), "of.mandate_check", sid_of(pos),
               {"verdict": verdict, "tool_name": tool, "system": system, "path": f"~/acme/{system}/src/f{k}.rs",
                "router_version": rv}, position=pos) for k in range(n)]


SCENARIOS += [
    _case("off-map-work/positive", _core_doc(),
          _verdicts("om", 12, "owned_by:bot_operator") + _verdicts("on", 2, "in_mandate", system="bot"),
          ("ap.off-map-work", ["bot_nonce"], "positive")),
    # reading the keeper's code and running shells at the repository root are not edits outside the mandate
    _case("off-map-work/negative-reads-and-shells", _core_doc(),
          _verdicts("or", 40, "owned_by:bot_operator", tool="Read") + _verdicts("os", 40, "unscoped", tool="Bash", system=None)
          + _verdicts("ow", 12, "in_mandate", system="bot"),
          ("ap.off-map-work", ["bot_nonce"], "negative")),
]


# ---- ap.cell-gap@0 and ap.cell-overlap@0, affected [system, method:<kind>] or [system] ----
# RFC 0007 cells: `needs` on systems, `owns` on positions; the model computes gaps and overlaps as Studio shows them.
def _cell_doc(owns, needs=("build", "verify")):
    doc = _org_doc()
    doc["systems"]["bot"] = dict(doc["systems"]["bot"], needs=list(needs))
    for p in doc["positions"]:
        p["owns"] = [{"system": "bot", "method": m, "decides": d} for m, d in owns.get(p["name"], ())]
    return doc


SCENARIOS += [
    # verify: bot_search works it and nobody decides; build is decided by bot_operator
    _case("cell-gap/positive-no-decider",
          _cell_doc({"bot_operator": [("build", True)], "bot_search": [("verify", False)]}), [],
          ("ap.cell-gap", ["bot", "method:verify"], "positive")),
    _case("cell-gap/negative-decided",
          _cell_doc({"bot_operator": [("build", True)], "bot_search": [("verify", True)]}), [],
          ("ap.cell-gap", ["bot", "method:verify"], "negative")),
    # needs declared, and no position owns any cell yet: one issue for the system, not one per cell
    _case("cell-gap/positive-system-unmapped", _cell_doc({}), [], ("ap.cell-gap", ["bot"], "positive")),
    # three doers on build, one decides (storefront on Acme: 7-8 owners per kind of work)
    _case("cell-overlap/positive",
          _cell_doc({"bot_operator": [("build", True), ("verify", True)], "bot_search": [("build", False)],
                     "bot_nonce": [("build", False)]}), [],
          ("ap.cell-overlap", ["bot", "method:build"], "positive")),
    _case("cell-overlap/negative-two-doers",
          _cell_doc({"bot_operator": [("build", True), ("verify", True)], "bot_search": [("build", False)]}), [],
          ("ap.cell-overlap", ["bot", "method:build"], "negative")),
    # no system declares needs and no position owns: both rules say so rather than report no gaps
    _case("cell-rules/blocked-no-cells", _org_doc(), [],
          ("ap.cell-gap", ["bot"], "blocked"), ("ap.cell-overlap", ["bot"], "blocked")),
]



# ---- calibration on attributed PRs (2026-11-10): of import github reads the Position line, 74% of Acme's PRs attributed ----
# over-coupled@2: the positive's traffic between a unit owner and a position of its unit is their mandate
def _unit_owner_pair_doc():
    doc = _org_doc()
    doc["positions"][2] = dict(doc["positions"][2], kind="owner")   # bot_nonce owns the unit bot_search works in
    return doc


SCENARIOS.append({**SCENARIOS[[s["name"] for s in SCENARIOS].index("over-coupled/positive")],
                  "name": "over-coupled/negative-unit-owner", "doc": _unit_owner_pair_doc(),
                  "labels": [{"rule": "ap.over-coupled", "affected": ["bot_nonce", "bot_search"], "label": "negative"}]})


# chatty's organization part: the window before saw transcript messages only (import), this one the relay's log (of)
def _relogged(events):
    return [dict(e, source="of") if e["name"] == "of.agent_message_out" and e["ts"] >= "2026-11-01" else
            dict(e, source="import") if e["name"] == "of.agent_message_out" else e for e in events]


SCENARIOS.append(_case("chatty-org/negative-recording-changed", _org_doc(), _relogged(_org_chatter(73, 20)),
                       ("ap.chatty", ["(organization)"], "negative")))
