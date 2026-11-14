"""Development loops (RFC 0005, steps 1 and 2): the loop schema check and the hypothesis register."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_loop  # noqa: E402

LOOP = {"target": {"metric": "day pass, STOREFRONT per day", "money": "STOREFRONT per day of bot revenue", "throughput_class": "raise",
                   "observed_at": "the day pass's own ledger",
                   "segment": {"start": "the block reaches our node", "end": "the order leaves our sender",
                               "context": ["network after the send", "the exchange's ordering rule"]}},
        "measures": [{"name": "latency_p50_ms", "to_money": "STOREFRONT per ms from the race arithmetic",
                      "observed_at": "our sender's send log"}],
        "ladder": [{"rung": "oracle", "cost": "0", "accept": "0 differing rows"},
                   {"rung": "offline", "cost": "local CPU", "accept": "no candidate lost"},
                   {"rung": "day_pass", "cost": "6 runner-h", "accept": "> baseline + 1%"}],
        "checkpoint_days": 3, "wip_arms": 2, "babysit_model": "sonnet"}


def ev(i, name, minute, **p):
    return {"name": name, "ts": f"2026-11-07T10:{minute:02d}:00Z", "payload": p}


class LoopCheckTest(unittest.TestCase):
    def test_the_rfc_example_passes(self):
        self.assertEqual(of_loop.check_loop(LOOP, "w"), [])
        extended = dict(LOOP, target=dict(LOOP["target"], better="higher"),
                        ladder=LOOP["ladder"] + [{"rung": "shadow", "accept": "no loss", "production": True, "since": "2026-11-08"}])
        self.assertEqual(of_loop.check_loop(extended, "w"), [])
        self.assertTrue(of_loop.check_loop(dict(LOOP, target=dict(LOOP["target"], better="up")), "w"))

    def test_problems_and_warnings(self):
        bad = {"target": {"metric": "x"}, "measures": [{"name": "latency"}],
               "ladder": [{"rung": "Day Pass"}, {"rung": "a", "accept": "ok"}, {"rung": "a", "accept": "ok"}],
               "checkpoint_days": 0, "wip_arms": "two", "babysit_model": "gpt-5", "extra": 1}
        out = "\n".join(of_loop.check_loop(bad, "w"))
        for want in ("target.money is required", "measure 'latency' needs to_money", "ladder[0].rung must match",
                     "rung 'a' appears twice", "checkpoint_days must be a positive", "wip_arms must be a positive",
                     "warning: w: loop: babysit_model", "warning: w: loop: unknown key 'extra'"):
            self.assertIn(want, out)
        self.assertEqual(of_loop.check_loop("x", "w"), ["w: loop must be a mapping"])
        self.assertIn("ladder is required", "\n".join(of_loop.check_loop({"target": LOOP["target"]}, "w")))

    def test_the_target_segment(self):
        """docs/registry.md, Works, "The target's controlled segment" (hand-over c35460ef, 2026-11-12)."""
        def check(segment=None, metric=None, measures=None, drop=False):
            t = {k: v for k, v in LOOP["target"].items() if not (drop and k == "segment")}
            if segment is not None:
                t["segment"] = segment
            if metric is not None:
                t["metric"] = metric
            return of_loop.check_loop(dict(LOOP, target=t, measures=measures or LOOP["measures"]), "w")
        self.assertEqual(check(drop=True), ["warning: w: loop: target has no segment (the part the organization "
                                            "controls, from its start event to its end event; docs/registry.md, Works)"])
        self.assertEqual(check({"start": "a", "end": "b"}), [])                     # context is optional
        cases = {"target.segment must be a mapping": "a to b",
                 "target.segment.start is required": {"end": "b"},
                 "target.segment.end is required": {"start": "a", "end": " "},
                 "start and end are the same event": {"start": "Order sent", "end": "order  sent"},
                 "context must be a list": {"start": "a", "end": "b", "context": "the network"},
                 "context[1] is neither a text nor a mapping": {"start": "a", "end": "b", "context": ["x", 3]},
                 "context[0] is neither": {"start": "a", "end": "b", "context": [{"name": "net"}]},
                 "context[0].name must match": {"start": "a", "end": "b", "context": [{"name": "Net", "what": "n"}]},
                 "the name 'net' appears twice": {"start": "a", "end": "b",
                                                  "context": [{"name": "net", "what": "x"}, {"name": "net", "what": "y"}]},
                 "warning: w: loop: target.segment.context[0]: unknown key 'gate'":
                     {"start": "a", "end": "b", "context": [{"what": "x", "gate": 4}]},
                 "warning: w: loop: target.segment: unknown key 'gates'": {"start": "a", "end": "b", "gates": 4}}
        for want, seg in cases.items():
            out = check(seg)
            self.assertTrue(any(want.strip() in x for x in out), (want, out))
            self.assertEqual(len(out), 1, out)
        # PR 58's matching: a name matches a part's name; a prose metric matches when it holds every content word
        seg = {"start": "a", "end": "b", "context": ["the network after the send",
                                                      {"name": "rival_ordering", "what": "a third party's ordering rule"}]}
        self.assertEqual(check(seg), [])
        self.assertEqual(check(seg, metric="p50 of the network after our send, in ms"),
                         ["w: loop: target.metric 'p50 of the network after our send, in ms' matches the context part "
                          "'the network after the send', outside the controlled segment"])
        self.assertEqual(check(seg, metric="network latency"), [])           # not every content word: no match
        self.assertEqual(check(seg, measures=[{"name": "rival_ordering", "to_money": "x", "observed_at": "x"}]),
                         ["w: loop: measure 'rival_ordering' is the context part 'rival_ordering', outside the "
                          "controlled segment"])
        self.assertEqual(check(seg, measures=[{"name": "network after the send", "to_money": "x", "observed_at": "x"}]), [])  # names only
        self.assertTrue(of_loop.context_matches("the change moves the third party's ordering rule", seg["context"][1],
                                                prose=True))
        # of_field-analyst, 2026-11-12: a possessive's "s" and a plural must not block a match
        self.assertEqual(of_loop.content_words("a third party's ordering rule"), {"third", "party", "ordering", "rule"})
        self.assertTrue(of_loop.context_matches("lobby the third party to change its ordering rules",
                                                "a third party's ordering rule", prose=True))
        self.assertEqual(of_loop.content_words("parties process the bus"), {"party", "process", "bus"})
        self.assertTrue(of_loop.context_matches("ordering_rule_third_party_rank", "a third party's ordering rule",
                                                prose=True))   # of_review's probe on PR 59

    def test_a_loop_yaml_beside_the_doc_is_checked_and_one_place_only(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
        spec = importlib.util.spec_from_loader("of_cli", loader)
        of = importlib.util.module_from_spec(spec)
        loader.exec_module(of)
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "storefront", "docs", "rfc"))
            with open(os.path.join(root, "storefront", "docs", "rfc", "search.md"), "w") as fh:
                fh.write("# x\n")
            with open(os.path.join(root, "storefront", "docs", "rfc", "loop.yaml"), "w") as fh:
                fh.write("loop:\n  target: {metric: x}\n  ladder: [{rung: offline, accept: ok}]\n")
            works = {"w": {"title": "t", "kind": "rfc", "doc": "storefront:docs/rfc/search.md (PR #1)"}}
            self.assertTrue(of.doc_path(works["w"]["doc"], [root]).endswith("search.md"))
            out = of.loop_files(works, [root])
            self.assertTrue(any("target.money is required" in x for x in out), out)
            works["w"]["loop"] = LOOP
            self.assertTrue(any("both in the registry and in" in x for x in of.loop_files(works, [root])))


class RegisterTest(unittest.TestCase):
    def test_status_is_derived_and_the_next_is_first(self):
        rows = [ev(1, "of.loop_hypothesis", 1, work="w", hypothesis="h1", change="cheap and valuable", cost="oracle, 0",
                   cost_h=0.0, voi="high"),
                ev(2, "of.loop_hypothesis", 2, work="w", hypothesis="h2", change="dear", cost="day_pass, 6 runner-h",
                   cost_h=6.0, voi="high"),
                ev(3, "of.loop_hypothesis", 3, work="w", hypothesis="h3", change="dropped", cost_h=1.0, voi="low"),
                ev(4, "of.loop_run", 4, work="w", rung="day_pass", hypothesis="h2", result="325.8 STOREFRONT"),
                ev(5, "of.loop_decision", 5, work="w", decision="drop", hypothesis="h3"),
                ev(6, "of.loop_hypothesis", 6, work="other", hypothesis="hx", change="x", voi="low")]
        reg = of_loop.register(rows, "w")
        self.assertEqual([(h["id"], h["status"]) for h in reg["hypotheses"]], [("h1", "open"), ("h2", "testing"),
                                                                               ("h3", "dropped")])
        self.assertEqual((len(reg["runs"]), len(reg["decisions"])), (1, 1))
        self.assertEqual([h["unmet_open"] for h in reg["hypotheses"]], [False, False, False])
        # of_conductor, 2026-11-12; of_review on PR 61: a run that missed its rung's accept criterion, no decision yet
        refuted = rows + [ev(7, "of.loop_run", 7, work="w", rung="day_pass", hypothesis="h2", value=1.0, accepted=False)]
        h2 = next(h for h in of_loop.register(refuted, "w")["hypotheses"] if h["id"] == "h2")
        self.assertEqual((h2["status"], h2["unmet_open"], h2["last_run"]["accepted"], h2["last_run"]["rung"]),
                         ("testing", True, False, "day_pass"))
        closed = refuted + [ev(8, "of.loop_decision", 8, work="w", decision="drop", hypothesis="h2")]
        h2 = next(h for h in of_loop.register(closed, "w")["hypotheses"] if h["id"] == "h2")
        self.assertEqual((h2["status"], h2["unmet_open"]), ("dropped", False))
        self.assertEqual(of_loop.cost_hours("day_pass, 6 runner-h"), 6.0)
        self.assertEqual(of_loop.cost_hours("gate, 40 min"), 40 / 60)
        self.assertEqual(of_loop.cost_hours("0"), 0.0)


class ReopenTest(unittest.TestCase):
    """RFC 0005 §4.1: reopen puts a parked, dropped or kept hypothesis back to open (hand-over 7b804b09 from
    of_architect; a revived hypothesis had been recorded as keep on 2026-11-14)."""

    def test_register_derives_open_after_a_reopen(self):
        rows = [ev(1, "of.loop_hypothesis", 1, work="w", hypothesis="h1", change="a", cost_h=1.0, voi="med"),
                ev(2, "of.loop_run", 2, work="w", rung="day_pass", hypothesis="h1", accepted=False),
                ev(3, "of.loop_decision", 3, work="w", decision="park", hypothesis="h1"),
                ev(4, "of.loop_hypothesis", 4, work="w", hypothesis="h2", change="b", cost_h=1.0, voi="med"),
                ev(5, "of.loop_decision", 5, work="w", decision="keep", hypothesis="h2")]
        reg = of_loop.register(rows, "w")
        self.assertEqual({h["id"]: h["status"] for h in reg["hypotheses"]}, {"h1": "parked", "h2": "kept"})
        rows += [ev(6, "of.loop_decision", 6, work="w", decision="reopen", hypothesis="h1", note="the owner revived it"),
                 ev(7, "of.loop_decision", 7, work="w", decision="reopen", hypothesis="h2", note="kept by mistake")]
        hs = {h["id"]: h for h in of_loop.register(rows, "w")["hypotheses"]}
        self.assertEqual({k: h["status"] for k, h in hs.items()}, {"h1": "open", "h2": "open"})  # no longer kept
        self.assertFalse(hs["h1"]["unmet_open"])                                # the reopen answers the missed run
        rows.append(ev(8, "of.loop_run", 8, work="w", rung="day_pass", hypothesis="h1", accepted=False))
        h1 = next(h for h in of_loop.register(rows, "w")["hypotheses"] if h["id"] == "h1")
        self.assertEqual((h1["status"], h1["unmet_open"]), ("testing", True))   # a new run after it is unanswered

    def test_of_loop_decision_reopen(self):
        import json
        import sqlite3
        import subprocess
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ, OF_ORGS=cli_org(root, LOOP), OF_STATE=os.path.join(root, "state"))
            env.pop("CLAUDE_CODE_SESSION_ID", None)
            of = os.path.join(HERE, "..", "bin", "of")
            run = lambda *a: subprocess.run([sys.executable, of, "loop", *a, "--from", "lab_doer"],  # noqa: E731
                                            capture_output=True, text=True, env=env)
            hid = run("hypothesis", "w-speed", "drop the floor", "--cost", "oracle, 0", "--voi", "high").stdout.split()[-1]
            r = run("decision", "w-speed", "reopen", "--hypothesis", hid, "--note", "x")
            self.assertNotEqual(r.returncode, 0)                                # open: nothing to reopen
            self.assertIn("is open: reopen is for a parked, dropped or kept hypothesis", r.stderr)
            run("run", "w-speed", "--rung", "oracle", "--hypothesis", hid, "--accepted", "no")
            self.assertIn("is testing", run("decision", "w-speed", "reopen", "--hypothesis", hid, "--note", "x").stderr)
            self.assertEqual(run("decision", "w-speed", "park", "--hypothesis", hid).returncode, 0)
            for bad in (("--hypothesis", hid), ("--note", "the owner revived it"), ("--hypothesis", hid, "--note", " ")):
                r = run("decision", "w-speed", "reopen", *bad)
                self.assertNotEqual(r.returncode, 0, bad)
                self.assertIn("reopen names the hypothesis and what changed", r.stderr)
            r = run("decision", "w-speed", "reopen", "--hypothesis", hid, "--note", "the owner revived it")
            self.assertEqual(r.returncode, 0, r.stderr)
            db = sqlite3.connect(os.path.join(root, "state", "of.db"))
            dec = json.loads(db.execute("SELECT payload FROM event WHERE name = 'of.loop_decision' ORDER BY rowid DESC "
                                        "LIMIT 1").fetchone()[0])
            self.assertEqual((dec["decision"], dec["hypothesis"], dec["note"]), ("reopen", hid, "the owner revived it"))
            st = [json.loads(p)["status"] for (p,) in db.execute("SELECT payload FROM event WHERE name = "
                                                                 "'of.loop_hypothesis' ORDER BY rowid")]
            self.assertEqual(st, ["open", "testing", "parked", "open"])        # its status update, back to open
            out = run("show", "w-speed").stdout
            self.assertIn(f"{hid} open", out)
            self.assertIn("decision", out)
            self.assertIn("reopen", out)


PROXIED = dict(LOOP, measures=LOOP["measures"] + [
    {"name": "race_margin_ms", "observed_at": "our nodes' receive time of the rival's transaction",
     "proxy_for": {"at": "the block's builder", "valid_when": "one of our own nodes built the block"},
     "to_money": "STOREFRONT per ms"}])


class ReadingPointTest(unittest.TestCase):
    """docs/registry.md, Works, "Where a reading is taken" (hand-over 52b9d314 from of_architect; the founder's prompt
    01a1da18-f250: a reading at our own nodes was taken as the builder's)."""

    def test_the_check_names_readings_without_a_point_and_proxies_without_their_condition(self):
        self.assertEqual(of_loop.check_loop(PROXIED, "w"), [])
        bare = dict(LOOP, target={k: v for k, v in LOOP["target"].items() if k != "observed_at"},
                    measures=[{"name": "m", "to_money": "x"}])
        out = of_loop.check_loop(bare, "w")
        self.assertIn("warning: w: loop: target has no observed_at (where its reading is taken)", out)
        self.assertIn("warning: w: loop: measure 'm' has no observed_at (where its reading is taken)", out)
        for pf in ({"at": "the builder"}, {"valid_when": "ours built it"}, "the builder", {"at": " ", "valid_when": "x"}):
            m = dict(PROXIED["measures"][1], proxy_for=pf)
            out = of_loop.check_loop(dict(LOOP, measures=[m]), "w")
            self.assertIn("w: loop: measure 'race_margin_ms': proxy_for needs at (where the event happens) and "
                          "valid_when (when the reading equals the one there)", out, pf)
        self.assertEqual(of_loop.reading_of(PROXIED, "race_margin_ms"),
                         ("our nodes' receive time of the rival's transaction",
                          {"at": "the block's builder", "valid_when": "one of our own nodes built the block"}))
        self.assertEqual(of_loop.reading_of(PROXIED, LOOP["target"]["metric"]), ("the day pass's own ledger", None))

    def test_of_loop_run_decision_and_show_on_a_proxy(self):
        import json
        import sqlite3
        import subprocess
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ, OF_ORGS=cli_org(root, PROXIED), OF_STATE=os.path.join(root, "state"))
            env.pop("CLAUDE_CODE_SESSION_ID", None)
            of = os.path.join(HERE, "..", "bin", "of")
            run = lambda *a: subprocess.run([sys.executable, of, "loop", *a, "--from", "lab_doer"],  # noqa: E731
                                            capture_output=True, text=True, env=env)
            hid = run("hypothesis", "w-speed", "faster sender", "--cost", "oracle, 0", "--voi", "high").stdout.split()[-1]
            r = run("run", "w-speed", "--rung", "oracle", "--hypothesis", hid, "--metric", "race_margin_ms",
                    "--value", "0.4", "--accepted", "yes")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("not recorded: --accepted", r.stdout)                 # no accept judged on such a reading
            db = sqlite3.connect(os.path.join(root, "state", "of.db"))
            p = json.loads(db.execute("SELECT payload FROM event WHERE name = 'of.loop_run'").fetchone()[0])
            self.assertEqual((p["observed_at"], p["proxy"]), ("our nodes' receive time of the rival's transaction",
                             {"for": "the block's builder", "valid_when": "one of our own nodes built the block",
                              "held": "unknown"}))
            self.assertNotIn("accepted", p)
            for dec in ("keep", "drop"):                                        # it decides nothing
                r = run("decision", "w-speed", dec, "--hypothesis", hid)
                self.assertNotEqual(r.returncode, 0, dec)
                self.assertIn("every run of it is a proxy that decides nothing. Its latest is not observed at the "
                              "block's builder; its reading at our nodes' receive time of the rival's transaction is a "
                              "proxy (race_margin_ms 0.4), valid only when one of our own nodes built the block, which "
                              "is not known to hold", r.stderr)
            out = run("show", "w-speed").stdout
            self.assertIn("not observed at the block's builder; its reading at our nodes' receive time", out)
            self.assertNotIn("accept criterion", out)
            run("run", "w-speed", "--rung", "oracle", "--hypothesis", hid, "--metric", "race_margin_ms",
                "--proxy-held", "not_held")
            self.assertNotEqual(run("decision", "w-speed", "keep", "--hypothesis", hid).returncode, 0)
            r = run("run", "w-speed", "--rung", "oracle", "--hypothesis", hid, "--metric", "race_margin_ms",
                    "--value", "1.2", "--accepted", "yes", "--proxy-held", "held")  # on the blocks our nodes built
            self.assertEqual(r.returncode, 0, r.stderr)
            p = json.loads(db.execute("SELECT payload FROM event WHERE name = 'of.loop_run' ORDER BY rowid DESC "
                                      "LIMIT 1").fetchone()[0])
            self.assertEqual((p["proxy"]["held"], p["accepted"]), ("held", True))
            self.assertEqual(run("decision", "w-speed", "keep", "--hypothesis", hid).returncode, 0)  # now it may
            h2 = run("hypothesis", "w-speed", "second", "--cost", "oracle, 0", "--voi", "low").stdout.split()[-1]
            r = run("run", "w-speed", "--rung", "oracle", "--hypothesis", h2, "--proxy-for", "the builder",
                    "--valid-when", "we built it")                              # a run that names its proxy itself
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(run("decision", "w-speed", "park", "--hypothesis", h2, "--note", "await builder data"
                                 ).returncode, 0)                               # park stays open
            for bad in (("--proxy-held", "held"),                              # the target is read where it happens
                        ("--proxy-for", "the builder"), ("--valid-when", "x"),
                        ("--metric", "race_margin_ms", "--proxy-held", "maybe")):
                self.assertNotEqual(run("run", "w-speed", "--rung", "oracle", *bad).returncode, 0, bad)

    def test_the_start_context_shows_such_runs_as_not_observed(self):
        import of_context
        import of_events
        import of_store
        with tempfile.TemporaryDirectory() as root:
            conn = of_store.connect(os.path.join(root, "of.db"))
            proxy = {"for": "the block's builder", "valid_when": "one of our own nodes built the block",
                     "held": "unknown"}
            for i, (hid, held) in enumerate((("h1", "unknown"), ("h2", "held"), ("h1", "unknown"))):
                rec = of_events.record("loop_run", {"work": "w-speed", "hypothesis": hid, "rung": "oracle",
                                                    "metric": "race_margin_ms", "value": 0.4 + i,
                                                    "observed_at": "our nodes", "proxy": dict(proxy, held=held)})
                of_store.add_event(conn, rec)
            conn.commit()
            runs = of_loop.blind_runs(conn, ["w-speed"])
            self.assertEqual([(w, r["hypothesis"], r["value"]) for w, r, _ in runs], [("w-speed", "h1", 2.4)])
            self.assertEqual(of_loop.blind_runs(conn, ["w-other"]), [])
            lines = "\n".join(of_context.not_observed_runs(runs))
            self.assertIn("## Loop runs not observed where their event happens", lines)
            self.assertIn("not observed at the block's builder; its reading at our nodes is a proxy (race_margin_ms "
                          "2.4), valid only when one of our own nodes built the block, which is not known to hold.", lines)
            self.assertEqual(of_context.not_observed_runs([]), [])
            conn.close()


OWNED = dict(LOOP, target={"metric": "reaction from the input to the send, p99, ms", "better": "lower",
                           "decided": "01a1d957-c8b6", "segment": LOOP["target"]["segment"],
                           "observed_at": "our sender's send log"},
             measures=[{"name": "triggers_covered", "to_target": "an uncovered trigger is a reaction never made",
                        "observed_at": "the trigger replay"}])


def owned(decided):
    return dict(OWNED, target=dict(OWNED["target"], decided=decided))


def cli_org(root, loop):
    """A lab organization with one work `w-speed` carrying LOOP, owned by lab_owner and done by lab_doer."""
    import yaml
    org = os.path.join(root, "org")
    os.makedirs(os.path.join(org, "registry"), exist_ok=True)
    with open(os.path.join(org, "registry", "positions.yaml"), "w") as fh:
        yaml.safe_dump({"organization": "lab", "systems": {"s": {"name": "S", "kind": "target"}},
                        "positions": [{"name": "lab_doer", "system": "s", "role": "doer", "enrolled": True},
                                      {"name": "lab_owner", "system": "s", "role": "owner", "enrolled": True},
                                      {"name": "lab_other", "system": "s", "role": "other", "enrolled": True},
                                      {"name": "lab_conductor", "system": "s", "role": "conductor"}],
                        "works": {"w-speed": {"title": "Speed", "kind": "rfc", "owner": "lab_owner",
                                              "doers": ["lab_doer"], "loop": loop}}}, fh)
    orgs = os.path.join(root, "orgs.yaml")
    with open(orgs, "w") as fh:
        yaml.safe_dump({"orgs": [{"name": "lab", "path": org, "paths": [root]}]}, fh)
    return orgs


class Yeses:
    """A store under ROOT/state with the yeses and non-yeses resolve_decided tells apart."""

    def __init__(self, root):
        import of_events
        import of_store
        os.makedirs(os.path.join(root, "state"), exist_ok=True)
        self.path = os.path.join(root, "state", "of.db")
        self.conn = of_store.connect(self.path)

        def add(name, payload, position="lab_doer"):
            rec = of_events.record(name, payload, position=position)
            of_store.add_event(self.conn, rec)
            return rec["of.event.id"]
        import time
        self.prompt = add("human_prompt", {"origin": "human", "text": "reaction time is the target, I accept the risk"},
                          position="lab_conductor")  # typed in the session holding the person's conductor
        time.sleep(0.005)  # alone in its millisecond: its first two groups name it only
        self.injected = add("human_prompt", {"origin": "injected", "text": "a relayed message"}, position="lab_conductor")
        self.corrected = add("human_prompt", {"origin": "human", "text": "typed by a tool"}, position="lab_conductor")
        self.elsewhere = add("human_prompt", {"origin": "human", "text": "typed in a doer's pane"})
        add("prompt_origin", {"event_id": self.corrected, "origin": "injected", "evidence": "turnOrigin"})
        def answer(ask, words, by="owner", via="asks-ui", **more):
            add("agent_question_answered", {"ask_id": ask, "ask_ids": [ask], "by": by, "answer": words,
                                            **({"via": via} if via else {}), **more})
        opts = ["yes, p50 is the target", "no, keep p99"]

        def ask(q, work="w-speed", yes=1):
            return add("agent_question", {"questions": [{"question": q, "options": opts}], "work": work,
                                          **({"yes_option": yes} if yes else {})})
        self.ask = ask("p50 instead of p99?")
        answer(self.ask, "yes, p50 is the target")                           # option 1 chosen in the popup
        self.chat = ask("p50 as the target, from the chat?")
        answer(self.chat, "yes, p50 is the target", via="conductor", option=1,  # option 1 through the conductor's chat
               quote="ok, p50 it is")
        self.bare = ask("p50, closed by of asks answered?")
        answer(self.bare, "yes, p50 is the target", via=None)                 # by: owner with no person's channel
        answer(self.bare, "yes, p50 is the target", via="status")
        self.refused = ask("p50 as the target?")
        answer(self.refused, "no, keep p99")                                  # option 2: the refusal
        self.free = ask("p50, really?")
        answer(self.free, "sure, why not")                                    # free text: no option chosen
        self.other = ask("Rotate the relay token?", work="w-ops")
        answer(self.other, "yes, p50 is the target")
        self.unmarked = ask("p50, with no yes marked?", yes=None)
        answer(self.unmarked, "yes, p50 is the target")                       # filed without --yes: no option is a yes
        self.flipped = ask("p90?")
        answer(self.flipped, "yes, p50 is the target")
        answer(self.flipped, "no, keep p99")                                  # the latest answer counts
        self.closed = add("agent_question", {"questions": [{"question": "p95?"}]})
        add("agent_question_answered", {"ask_id": self.closed, "ask_ids": [self.closed], "by": "lab_doer",
                                        "via": "status"})
        self.open = add("agent_question", {"questions": [{"question": "p90?"}]})
        self.conn.commit()


class OwnersMeasureTest(unittest.TestCase):
    """docs/registry.md, Works, "The owner's measure" (hand-over 42e9c5db from of_architect, 2026-11-14), with the
    owner's yes found in the log (of_review on organic-framework PR 168)."""

    def test_1_the_check_takes_a_decided_target_without_money_and_to_target_measures(self):
        yes = lambda d: d == "01a1d957-c8b6"  # noqa: E731
        self.assertEqual(of_loop.check_loop(OWNED, "w", resolve=yes), [])
        self.assertEqual(of_loop.check_loop(OWNED, "w"), [])                    # a call that reads no log: as written
        reading = dict(OWNED, target=dict(OWNED["target"], money="win rate, as a reading"),
                       measures=OWNED["measures"] + [{"name": "p50_ms", "to_money": "STOREFRONT per ms", "observed_at": "our sender"}])
        self.assertEqual(of_loop.check_loop(reading, "w", resolve=yes), [])     # money beside it, and to_money, still fine
        undecided = dict(OWNED, target={k: v for k, v in OWNED["target"].items() if k != "decided"})
        out = "\n".join(of_loop.check_loop(undecided, "w", resolve=yes))
        self.assertIn("target.money is required", out)                          # without decided: both stay problems
        self.assertIn("measure 'triggers_covered' needs to_money", out)
        self.assertIn("to_target only on a target the owner decided", out)
        out = "\n".join(of_loop.check_loop(owned("01a1d999-0000"), "w", resolve=yes))
        self.assertIn("target.decided '01a1d999-0000' is not the owner's yes in this machine's log", out)
        self.assertIn("target.money is required", out)                          # an unresolved yes lifts nothing
        self.assertIn("measure 'triggers_covered' needs to_money", out)
        for bad in ("", "  ", 7, ["01a1d957"]):                                 # decided is a non-empty text
            out = "\n".join(of_loop.check_loop(owned(bad), "w", resolve=yes))
            self.assertIn("target.decided is the owner's yes", out)
            self.assertIn("target.money is required", out)
        bare = dict(OWNED, measures=[{"name": "triggers_covered"}])
        self.assertIn("measure 'triggers_covered' needs to_target (how it moves the target the owner decided) or "
                      "to_money", "\n".join(of_loop.check_loop(bare, "w", resolve=yes)))
        self.assertEqual(of_loop.decided(OWNED), "01a1d957-c8b6")
        self.assertIsNone(of_loop.decided(LOOP))

    def test_1_the_owners_yes_is_found_in_the_log(self):
        with tempfile.TemporaryDirectory() as root:
            y = Yeses(root)
            seen = []
            y.conn.set_trace_callback(seen.append)
            person = lambda pos, host: pos == "lab_conductor"  # noqa: E731
            r = lambda d: of_loop.resolve_decided(y.conn, d, person, "w-speed")  # noqa: E731
            self.assertEqual(r(y.prompt), ("prompt", y.prompt))                 # a prompt a person typed
            self.assertEqual(r(y.prompt[:13]), ("prompt", y.prompt))            # by its first two groups
            self.assertEqual(r(y.ask + "#1"), ("ask", y.ask))                   # the ask with the option that is the yes
            self.assertEqual(r(y.ask[-8:] + "#1"), ("ask", y.ask))              # by its short id, as of asks prints it
            self.assertEqual(r(y.chat + "#1"), ("ask", y.chat))                 # the option's number, from the chat
            self.assertEqual(r(y.chat + "#1").words, 'asked "p50 as the target, from the chat?", chose option 1 "yes, '
                             'p50 is the target", in their words "ok, p50 it is"')  # the person's own quote beside it
            self.assertEqual(of_loop.resolve_decided(y.conn, y.elsewhere), ("prompt", y.elsewhere))  # no place rule
            self.assertEqual(r(y.ask + "#1").words,                             # the cited words
                             'asked "p50 instead of p99?", chose option 1 "yes, p50 is the target"')
            self.assertEqual(r(y.prompt).words, "reaction time is the target, I accept the risk")
            self.assertIsNone(of_loop.resolve_decided(y.conn, y.other + "#1", work="w-speed", goal_yes=y.other[-8:]))
            # a goal's own yes is not cited for a loop's target (docs/registry.md, Works)
            self.assertIsNone(of_loop.resolve_decided(y.conn, y.prompt, person, kinds=("ask",)))  # change_objective
            for no in (y.ask, y.bare + "#1", y.unmarked + "#1",                 # no option named; no person's channel;
                       y.refused + "#2",                                        # no yes marked; a refusal by its number
                       y.ask + "#2", y.refused + "#1", y.free + "#1",           # another option, a refusal, free text
                       y.other + "#1", y.flipped + "#1", y.ask + "#3",          # another work, taken back, no such option
                       y.injected, y.corrected, y.elsewhere, y.closed + "#1", y.open + "#1", y.closed[-8:],
                       "01a1d999-0000", y.prompt[:8], y.prompt + "#1", "", None, "x' OR 1=1 --", "owner"):
                self.assertIsNone(r(no), no)                                    # an agent's word or nothing never counts
            reads = [q for q in seen if "FROM event" in q]
            self.assertTrue(reads)
            for q in reads:                                                     # by the key or one indexed name
                self.assertTrue("event_id >=" in q or "name = '" in q, q)

    def test_1_of_ask_yes_marks_the_option_that_is_the_yes(self):
        """of_review on organic-framework PR 168: the ask names its yes when filed (`of ask --work W --yes N`)."""
        import json
        import sqlite3
        import subprocess
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ, OF_ORGS=cli_org(root, OWNED), OF_STATE=os.path.join(root, "state"))
            env.pop("CLAUDE_CODE_SESSION_ID", None)
            of = os.path.join(HERE, "..", "bin", "of")
            q = ("Change the speed loop's target from p99 to p50 latency? The p50 tracks the triggers we lose; the "
                 "p99 stays reported.")
            ask = lambda *a: subprocess.run([sys.executable, of, "ask", q, "--options",  # noqa: E731
                                             "yes, p50 is the target|no, keep p99", *a, "--from", "lab_doer"],
                                            capture_output=True, text=True, env=env)
            for bad in (("--yes", "1"), ("--work", "w-speed", "--yes", "3"), ("--work", "w-speed", "--yes", "0")):
                r = ask(*bad)
                self.assertNotEqual(r.returncode, 0, bad)
                self.assertIn("--yes <n> marks which of --options is the yes", r.stderr)
            r = ask("--work", "w-speed", "--yes", "1")
            self.assertEqual(r.returncode, 0, r.stderr)
            db = sqlite3.connect(os.path.join(root, "state", "of.db"))
            p = json.loads(db.execute("SELECT payload FROM event WHERE name = 'of.agent_question'").fetchone()[0])
            self.assertEqual((p["work"], p["yes_option"]), ("w-speed", 1))

    def test_1_decided_is_read_from_the_registry_only_and_an_unread_log_warns(self):
        """docs/registry.md, Works, "The owner's measure", "Where it is read" (of_architect, after PR 168's review)."""
        import importlib.machinery
        import importlib.util
        import yaml
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
        spec = importlib.util.spec_from_loader("of_cli", loader)
        of = importlib.util.module_from_spec(spec)
        loader.exec_module(of)
        yes = lambda d: ("prompt", d)  # noqa: E731 - every id resolves: only where it is written decides here
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "storefront", "docs"))
            with open(os.path.join(root, "storefront", "docs", "speed.md"), "w") as fh:
                fh.write("# x\n")
            with open(os.path.join(root, "storefront", "docs", "loop.yaml"), "w") as fh:
                yaml.safe_dump({"loop": OWNED}, fh)
            works = {"w": {"title": "t", "kind": "rfc", "doc": "storefront:docs/speed.md"}}
            out = "\n".join(of.loop_files(works, [root], yes))
            self.assertIn("w: loop: target.decided is read from the registry only", out)
            self.assertIn("target.money is required", out)                      # and the loop has no exception
            orgs = os.path.join(root, "orgs.yaml")
            with open(orgs, "w") as fh:
                yaml.safe_dump({"orgs": [{"name": "lab", "path": root, "paths": [root]}]}, fh)
            self.assertNotIn("decided", of._loop_of(works["w"], "lab", orgs)["target"])  # of loop, the start context
        unread = lambda d: of_loop.UNREAD  # noqa: E731 - the store does not open
        out = of_loop.check_loop(OWNED, "w", resolve=unread)
        self.assertIn("warning: w: loop: target.decided '01a1d957-c8b6' could not be looked up", "\n".join(out))
        self.assertIn("w: loop: target.money is required", "\n".join(out))
        self.assertNotIn("target.decided is the owner's yes", "\n".join(out))
        self.assertIsNone(of_loop.owners_yes(OWNED, unread))
        self.assertIn("unresolved", of_loop.target_line(OWNED, unread))

    def test_2_an_absent_voi_ranks_as_med_by_voi_per_runner_hour(self):
        rows = [ev(1, "of.loop_hypothesis", 1, work="w", hypothesis="low", change="l", cost_h=1.0, voi="low"),
                ev(2, "of.loop_hypothesis", 2, work="w", hypothesis="none", change="n", cost_h=1.0),
                ev(3, "of.loop_hypothesis", 3, work="w", hypothesis="high", change="h", cost_h=1.0, voi="high"),
                ev(4, "of.loop_hypothesis", 4, work="w", hypothesis="none-dear", change="d", cost_h=1.5),
                ev(5, "of.loop_hypothesis", 5, work="w", hypothesis="med", change="m", cost_h=1.0, voi="med")]
        self.assertEqual([h["id"] for h in of_loop.register(rows, "w")["hypotheses"]],
                         ["high", "none", "med", "none-dear", "low"])           # 3, 2, 2 (older first), 2/1.5, 1

    def run_cli(self, root, loop):
        import subprocess
        env = dict(os.environ, OF_ORGS=cli_org(root, loop), OF_STATE=os.path.join(root, "state"))
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        of = os.path.join(HERE, "..", "bin", "of")
        return lambda *a: subprocess.run([sys.executable, of, "loop", *a, "--from", "lab_doer"], capture_output=True,
                                         text=True, env=env)

    def test_2_3_4_of_loop_on_a_decided_target(self):
        import json
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            y = Yeses(root)
            run = self.run_cli(root, owned(y.prompt[:13]))
            r = run("hypothesis", "w-speed", "skip the decode", "--predict", "p99 -0.2 ms", "--cost", "oracle, 0")
            self.assertEqual(r.returncode, 0, r.stderr)                         # (2) no --voi on such a loop
            self.assertNotEqual(run("hypothesis", "w-speed", "x", "--voi", "huge").returncode, 0)
            for more in (("--to-money", "STOREFRONT per ms"), ("--decided", y.ask[-8:] + "#1"), ()):
                r = run("decision", "w-speed", "change_objective", "--metric", "p50 ms", *more)
                self.assertNotEqual(r.returncode, 0, more)                      # (3) the measure changes in the registry
                self.assertTrue("it changes only in the registry" in r.stderr or "unrecognized" in r.stderr, r.stderr)
            db = sqlite3.connect(os.path.join(root, "state", "of.db"))
            hyp = json.loads(db.execute("SELECT payload FROM event WHERE name = 'of.loop_hypothesis'").fetchone()[0])
            self.assertNotIn("voi", hyp)
            self.assertIsNone(db.execute("SELECT 1 FROM event WHERE name = 'of.loop_decision'").fetchone())
            out = run("show", "w-speed").stdout                                 # (4) the target, its yes, money reported
            self.assertIn("target reaction from the input to the send, p99, ms (lower is better); the owner decided "
                          f"this measure ({y.prompt[:13]}: reaction time is the target, I accept the risk): its money is reported "
                          "beside it, never asked of the owner as a proof", out)
            self.assertIn("voi (med)", out)
        with tempfile.TemporaryDirectory() as root:                            # a decided id the log does not hold
            Yeses(root)
            run = self.run_cli(root, owned("01a1d999-0000"))
            r = run("hypothesis", "w-speed", "x", "--cost", "oracle, 0")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("names 01a1d999-0000 as the owner's yes, but this machine's log holds no", r.stderr)
            r = run("decision", "w-speed", "change_objective", "--metric", "p50 ms")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("chain to money", r.stderr)                          # unresolved: the money loop's rule
            out = run("show", "w-speed").stdout
            self.assertIn("it names 01a1d999-0000 as the owner's yes, but this machine's log holds no such yes", out)
            self.assertIn("unresolved, so it is not the owner's decision", out)
            self.assertNotIn("the owner decided this measure", out)
        with tempfile.TemporaryDirectory() as root:                            # elsewhere: as before
            run = self.run_cli(root, LOOP)
            r = run("hypothesis", "w-speed", "x", "--cost", "oracle, 0")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("optional only on a loop whose target the owner decided", r.stderr)
            self.assertEqual(run("decision", "w-speed", "change_objective", "--metric", "p50 ms",
                                 "--to-money", "STOREFRONT per ms").returncode, 0)
            self.assertNotIn("owner decided", run("show", "w-speed").stdout)

    def test_1_of_registry_check_reads_the_log_for_decided(self):
        import subprocess
        of = os.path.join(HERE, "..", "bin", "of")
        for decided, bad in ((None, False), ("01a1d999-0000", True)):
            with tempfile.TemporaryDirectory() as root:
                y = Yeses(root)
                env = dict(os.environ, OF_ORGS=cli_org(root, owned(decided or y.prompt)), OF_STATE=os.path.join(root, "state"))
                r = subprocess.run([sys.executable, of, "registry", "check"], capture_output=True, text=True, env=env)
                said = r.stdout + r.stderr
                self.assertEqual("is not the owner's yes in this machine's log" in said, bad, said)
                self.assertEqual("target.money is required" in said, bad, said)

    def test_4_the_start_context_names_the_decided_target(self):
        import importlib.machinery
        import importlib.util
        import of_context
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
        spec = importlib.util.spec_from_loader("of_cli", loader)
        of = importlib.util.module_from_spec(spec)
        loader.exec_module(of)
        with tempfile.TemporaryDirectory() as root:
            y = Yeses(root)
            orgs = cli_org(root, owned(y.prompt))
            positions, _ = of.load_positions(orgs)
            resolve = of._decided_resolver(y.conn, positions)
            by = {p["name"]: p for p in positions}
            self.assertEqual([(w, r) for w, r, _ in of.position_loops(by["lab_owner"], orgs)], [("w-speed", "owner")])
            for name, role in (("lab_owner", "owner"), ("lab_doer", "doer")):
                ctx = of_context.build(name, positions, of.position_loops(by[name], orgs), resolve)
                self.assertIn("## Loops whose target names the owner's yes", ctx)
                self.assertIn(f"- `w-speed` (you are its {role}): target reaction from the input to the send, p99, ms "
                              f"(lower is better); the owner decided this measure ({y.prompt}: reaction time is the target, I accept "
                              "the risk): its money is "
                              "reported beside it, never asked of the owner as a proof.", ctx)
                self.assertIn("never ask the owner, or relay an ask, to price, justify or prove it in money", ctx)
            self.assertEqual(of.position_loops(by["lab_other"], orgs), [])
            self.assertNotIn("owner's yes", of_context.build("lab_other", positions, [], resolve))
            orgs = cli_org(root, owned("01a1d999-0000"))                       # unresolved: named so, no guidance
            positions, _ = of.load_positions(orgs)
            me = next(p for p in positions if p["name"] == "lab_doer")
            ctx = of_context.build("lab_doer", positions, of.position_loops(me, orgs), resolve)
            self.assertIn("unresolved, so it is not the owner's decision", ctx)
            self.assertNotIn("the owner decided this measure", ctx)
            self.assertNotIn("never ask the owner", ctx)
        with tempfile.TemporaryDirectory() as root:                            # a loop priced in money: nothing added
            orgs = cli_org(root, LOOP)
            positions, _ = of.load_positions(orgs)
            me = next(p for p in positions if p["name"] == "lab_owner")
            self.assertNotIn("owner's yes", of_context.build("lab_owner", positions, of.position_loops(me, orgs)))

if __name__ == "__main__":
    unittest.main()


class LoopCliTest(unittest.TestCase):
    def test_hypothesis_run_decision_show(self):
        import subprocess
        import yaml
        with tempfile.TemporaryDirectory() as root:
            org = os.path.join(root, "org")
            os.makedirs(os.path.join(org, "registry"))
            with open(os.path.join(org, "registry", "positions.yaml"), "w") as fh:
                yaml.safe_dump({"organization": "lab", "positions": [{"name": "lab_doer", "system": "s", "role": "doer"}],
                                "systems": {"s": {"name": "S", "kind": "target"}},
                                "works": {"w-search": {"title": "Search", "kind": "rfc", "loop": LOOP}}}, fh)
            orgs = os.path.join(root, "orgs.yaml")
            with open(orgs, "w") as fh:
                yaml.safe_dump({"orgs": [{"name": "lab", "path": org, "paths": [root]}]}, fh)
            env = dict(os.environ, OF_ORGS=orgs, OF_STATE=os.path.join(root, "state"))
            env.pop("CLAUDE_CODE_SESSION_ID", None)
            of = os.path.join(HERE, "..", "bin", "of")
            run = lambda *a: subprocess.run([sys.executable, of, "loop", *a], capture_output=True, text=True, env=env)
            r = run("hypothesis", "w-search", "drop the floor", "--predict", "+1%", "--cost", "day_pass, 6 runner-h",
                    "--voi", "high", "--from", "lab_doer")
            self.assertEqual(r.returncode, 0, r.stderr)
            hid = r.stdout.split()[-1]
            self.assertNotEqual(run("run", "w-search", "--rung", "moon", "--from", "lab_doer").returncode, 0)
            self.assertEqual(run("run", "w-search", "--rung", "day_pass", "--hypothesis", hid, "--result", "325.8 STOREFRONT",
                                 "--value", "325.8", "--accepted", "yes", "--started-at", "2026-11-07T08:00:00Z",
                                 "--from", "lab_doer").returncode, 0)
            self.assertNotEqual(run("run", "w-search", "--rung", "day_pass", "--value", "lots", "--from", "lab_doer").returncode, 0)
            for _ in range(2):  # a runner's job, synced twice, is recorded once
                r = run("run", "w-search", "--rung", "offline", "--run", "cbx-job-1", "--source", "cbx", "--from", "lab_doer")
                self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("already recorded", r.stdout)
            self.assertNotEqual(run("decision", "w-search", "change_objective", "--metric", "latency", "--from", "lab_doer").returncode, 0)
            self.assertEqual(run("decision", "w-search", "change_objective", "--metric", "p50 ms", "--to-money", "STOREFRONT per ms",
                                 "--from", "lab_doer").returncode, 0)
            import json
            import sqlite3
            db = sqlite3.connect(os.path.join(root, "state", "of.db"))
            runp = json.loads(db.execute("SELECT payload FROM event WHERE name = 'of.loop_run'").fetchone()[0])
            self.assertEqual((runp["value"], runp["accepted"], runp["metric"], runp["started_at"]),
                             (325.8, True, "day pass, STOREFRONT per day", "2026-11-07T08:00:00Z"))
            self.assertTrue(runp["ended_at"].endswith("Z") and runp["run"])
            cbx = [json.loads(r[0]) for r in db.execute("SELECT payload FROM event WHERE name = 'of.loop_run' "
                                                         "AND payload LIKE '%cbx-job-1%'")]
            self.assertEqual([(c["run"], c["source"]) for c in cbx], [("cbx-job-1", "cbx")])
            decp = json.loads(db.execute("SELECT payload FROM event WHERE name = 'of.loop_decision' "
                                         "AND payload LIKE '%change_objective%'").fetchone()[0])
            self.assertEqual((decp["metric"], decp["to_money"], decp["money"]), ("p50 ms", "STOREFRONT per ms", "STOREFRONT per ms"))
            hyp = [json.loads(r[0]) for r in db.execute("SELECT payload FROM event WHERE name = 'of.loop_hypothesis' ORDER BY rowid")]
            self.assertEqual(hyp[0]["cost"], {"rung": "day_pass", "runner_h": 6.0, "text": "day_pass, 6 runner-h"})
            self.assertEqual([h.get("status") for h in hyp], ["open", "testing"])  # the first run: testing
            self.assertEqual(run("decision", "w-search", "keep", "--hypothesis", hid, "--from", "lab_doer").returncode, 0)
            out = run("show", "w-search").stdout
            self.assertIn(f"{hid} kept", out)
            self.assertIn("prediction (not a result): ", out)
            self.assertIn("accept criterion met", out)
            r = run("hypothesis", "w-search", "second", "--predict", "p50 under 3 ms", "--cost", "oracle, 0", "--voi", "low",
                    "--from", "lab_doer")
            h2 = r.stdout.split()[-1]
            r = run("run", "w-search", "--rung", "oracle", "--hypothesis", h2, "--accepted", "no", "--from", "lab_doer")
            self.assertIn(f"the run did not meet the oracle rung's accept criterion; {h2} stays undecided", r.stdout)
            self.assertNotIn("refute", r.stdout)
            out = run("show", "w-search").stdout
            self.assertIn("accept criterion not met", out)
            self.assertIn("note: its latest run did not meet the oracle rung's accept criterion and no decision follows "
                          "it; it is still testing", out)
            self.assertNotIn("refute", out)
            self.assertIn("oracle > offline > day_pass", out)
            self.assertIn("segment (controlled): the block reaches our node -> the order leaves our sender", out)
            self.assertIn("context (outside the segment, never a target): network after the send;", out)


class LoopWorkTest(unittest.TestCase):
    def test_a_work_with_a_loop_needs_owner_and_doers(self):
        """of_architect, 4b5a274: owner and doers are required on a work with a loop."""
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
        spec = importlib.util.spec_from_loader("of_cli", loader)
        of = importlib.util.module_from_spec(spec)
        loader.exec_module(of)
        w = {"w-s": {"title": "t", "kind": "rfc", "loop": LOOP}}
        out = "\n".join(of.check_works(w, {"o", "d"}, {}))
        self.assertIn("needs an owner", out)
        self.assertIn("needs doers", out)
        w["w-s"].update(owner="o", doers=["d"])
        self.assertEqual([x for x in of.check_works(w, {"o", "d"}, {}) if "loop" in x or "owner" in x], [])
