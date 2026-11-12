"""Structural diffs (of_diff): format checks and narrow text edits that keep comments."""

import os
import sys
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_diff  # noqa: E402

REG = """# Position registry (hand-written).
tenant: alex
organization: ofb
positions:
  - name: of_a
    system: organic-framework
    role: a
    kind: project            # a kept comment
    owner: founder
    summary: "first"
    status: active
    contribution: >
      Folded text
      on two lines.
    holder:
      runtime: claude-code
      session: s-old
      workplace: herdr local
    aliases: [old-a]

  - name: of_b
    system: organic-framework
    role: b
    kind: project
    owner: founder
    summary: "second"   # another comment
    status: parked

systems:
  organic-framework: {keeper: of_a}
"""

BASE = {"id": "sd-2026-11-06-test", "tier": "low", "author": "of_architect", "org": "ofb",
        "hypothesis": "h", "predicted": [{"metric": "owner_relays", "to": 0}], "review": {"after": "7 days"},
        "reversal": "the reverse"}


def diff(kind, *registry, **extra):
    return dict(BASE, kind=kind, changes={"registry": list(registry), **extra})


class DiffTest(unittest.TestCase):
    def test_format(self):
        self.assertEqual(of_diff.check_format(diff("spawn", {"op": "add", "value": {}})), [])
        bad = dict(BASE, kind="split", tier="medium", predicted=[], review=None, changes={})
        text = "\n".join(of_diff.check_format(bad))
        for expected in ("not applicable yet", "predicted is required",
                         "approvals: [owner]", "changes.registry is required"):
            self.assertIn(expected, text)
        self.assertNotIn("review is required", text)  # optional: required dates were filled with invented deadlines (ask 53628150)
        self.assertIn("review, when given", "\n".join(of_diff.check_format(dict(BASE, review={"compare": "predicted"}))))
        self.assertIn("not in of_studio's catalogue",
                      "\n".join(of_diff.check_format(diff("spawn", {}), catalogue={"interface_ratio"})))

    def test_spawn_appends_inside_positions(self):
        v = {"name": "of_c", "system": "organic-framework", "role": "c", "kind": "project", "owner": "founder",
             "summary": "third", "status": "active", "mandate": "registry/briefs/of_c.md"}
        new, problems = of_diff.preview(diff("spawn", {"op": "add", "value": v}), REG)
        self.assertEqual(problems, [])
        doc = yaml.safe_load(new)
        self.assertEqual([p["name"] for p in doc["positions"]], ["of_a", "of_b", "of_c"])
        self.assertEqual(doc["systems"], {"organic-framework": {"keeper": "of_a"}})
        self.assertIn("# a kept comment", new)
        self.assertIn("# another comment", new)
        _, problems = of_diff.preview(diff("spawn", {"op": "add", "value": dict(v, name="of_a")}), REG)
        self.assertIn("exists already", "\n".join(problems))

    def test_mandate_rewrite_replaces_folded_and_keeps_the_rest(self):
        d = diff("mandate_rewrite", {"op": "set", "name": "of_a", "fields": {
            "summary": "new summary", "contribution": "One line now.",
            "mandate_exit": {"done_when": "tests pass", "review": "2026-12-01"}}})
        new, problems = of_diff.preview(d, REG)
        self.assertEqual(problems, [])
        a = {p["name"]: p for p in yaml.safe_load(new)["positions"]}["of_a"]
        self.assertEqual((a["summary"], a["contribution"], a["mandate_exit"]["done_when"]),
                         ("new summary", "One line now.", "tests pass"))
        self.assertEqual(a["holder"]["session"], "s-old")
        self.assertIn("# a kept comment", new)
        self.assertNotIn("Folded text", new)
        _, problems = of_diff.preview(diff("mandate_rewrite", {"op": "set", "name": "of_a",
                                                              "fields": {"holder": {}}}), REG)
        self.assertIn("may set only", "\n".join(problems))

    def test_swap_holder(self):
        h = {"runtime": "claude-code", "session": "s-new", "workplace": "herdr local, tab a"}
        new, problems = of_diff.preview(diff("swap_holder", {"op": "set", "name": "of_a", "fields": {"holder": h}}), REG)
        self.assertEqual(problems, [])
        doc = yaml.safe_load(new)
        self.assertEqual(doc["positions"][0]["holder"], h)
        self.assertEqual(doc["positions"][0]["aliases"], ["old-a"])
        self.assertEqual(doc["positions"][1], yaml.safe_load(REG)["positions"][1])
        _, problems = of_diff.preview(diff("swap_holder", {"op": "set", "name": "of_x", "fields": {"holder": h}}), REG)
        self.assertIn("no position 'of_x'", "\n".join(problems))

    def test_review_due_and_digest(self):
        import datetime as dt
        now = dt.datetime(2026, 11, 6, tzinfo=dt.timezone.utc)
        self.assertEqual(of_diff.review_due({"review": {"after": "20 tasks or 7 days"}}, now), "2026-11-13")
        self.assertIsNone(of_diff.review_due({"review": {"after": "the first 20 tasks"}}, now))  # no invented date
        self.assertIsNone(of_diff.review_due({}, now))
        self.assertEqual(of_diff.digest(BASE), of_diff.digest(dict(BASE)))


if __name__ == "__main__":
    unittest.main()


UNITS_REG = REG.replace("systems:\n  organic-framework: {keeper: of_a}\n", """systems:
  organic-framework: {keeper: of_a}
  k8s: {keeper: of_b}

units:
  platform:
    name: Platform
    owner: of_a              # the founder's position today
    systems: [k8s]
""")


class DelegateTest(unittest.TestCase):
    def d(self, **c):
        rec = {"op": "delegate", "unit": "platform", "from_owner": "of_a", "to_owner": "of_b",
               "envelope": {"budget": {"usd_month": 200}, "risk_appetite": "low", "critical_denies": "inherited"},
               "reporting": {"rollup": ["throughput_share"], "escalate_when": ["ask.blocking", "ask_unanswered > 2h"]},
               "review": "2026-12-01", "way_back": "reverse-diff"}
        rec.update(c)
        return dict(BASE, kind="delegate", tier="high", approvals=["owner"], changes={"registry": [rec]})

    def test_delegate_edits_units_and_records(self):
        new, problems = of_diff.preview(self.d(), UNITS_REG)
        self.assertEqual(problems, [])
        doc = yaml.safe_load(new)
        self.assertEqual(doc["units"]["platform"]["owner"], "of_b")
        rec = doc["delegations"][0]
        self.assertEqual((rec["unit"], rec["from_owner"], rec["to_owner"], rec["systems"]),
                         ("platform", "of_a", "of_b", ["k8s"]))
        self.assertIn("# the founder's position today", new)
        self.assertEqual(doc["positions"], yaml.safe_load(UNITS_REG)["positions"])

    def test_delegate_checks(self):
        _, p = of_diff.preview(self.d(to_owner="ghost", envelope={"critical_denies": "dropped"},
                                      reporting={"escalate_when": ["whenever"]}, way_back=None), UNITS_REG)
        text = "\n".join(p)
        for expected in ("not a position", "critical denies stay", "not understood", "way_back is required"):
            self.assertIn(expected, text)
        _, p = of_diff.preview(dict(self.d(), tier="low"), UNITS_REG)
        self.assertIn("high tier", "\n".join(p))


class EndToEndTest(unittest.TestCase):
    def test_propose_popup_approve_apply(self):
        import json
        import subprocess
        import tempfile
        of = os.path.join(HERE, "..", "bin", "of")
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "org", "registry"))
            os.makedirs(os.path.join(t, "state"))
            with open(os.path.join(t, "org", "registry", "positions.yaml"), "w") as fh:
                fh.write(REG)
            with open(os.path.join(t, "orgs.yaml"), "w") as fh:
                fh.write(f"orgs:\n  - name: ofb\n    path: {t}/org\n    workspace: ofb\n")
            diff_file = os.path.join(t, "sd.yaml")
            with open(diff_file, "w") as fh:  # a YAML date in it, as real diffs have
                fh.write("diff:\n  id: sd-2026-11-06-e2e\n  kind: mandate_rewrite\n  tier: medium\n"
                         "  author: of_a\n  org: ofb\n  hypothesis: h\n  approvals: [owner]\n"
                         "  predicted: [{metric: m, to: 0}]\n  review: {after: 7 days}\n  reversal: back\n"
                         "  changes:\n    registry:\n      - op: set\n        name: of_b\n        fields:\n"
                         "          mandate_exit: {done_when: done, review: 2026-12-01}\n")
            env = dict(os.environ, OF_STATE=os.path.join(t, "state"), OF_ORGS=os.path.join(t, "orgs.yaml"),
                       OF_CONFIG_DIR=t, PATH="/usr/bin:/bin")
            env.pop("CLAUDE_CODE_SESSION_ID", None)
            run = lambda *a: subprocess.run([sys.executable, of, *a], env=env, capture_output=True, text=True, timeout=60)
            r = run("diff", "propose", diff_file, "--from", "of_a")  # tier medium: no generated ask
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("hand-written ask", r.stdout + r.stderr)
            r = run("diff", "propose", diff_file, "--from", "of_a", "--ask", "Утвердить новый срок пересмотра of_b?")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = run("diff", "list")
            self.assertIn("sd-2026-11-06-e2e", r.stdout)
            self.assertIn("proposed", r.stdout)
            r = run("diff", "apply", "sd-2026-11-06-e2e")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("waits for the owner's approval", r.stdout + r.stderr)
            # the owner answers the diff's ask in the popup
            import sqlite3
            c = sqlite3.connect(os.path.join(t, "state", "of.db"))
            ask, pl, sid = c.execute("SELECT event_id, payload, session_id FROM event "
                                     "WHERE name = 'of.agent_question'").fetchone()
            c.close()
            # the proposer's session, as of ask records it: the hook and the judge count the card as its open ask
            # (acme_org, 2026-11-10: the judge told acme_org to file its open diff card again)
            self.assertEqual(sid, "s-old")
            pl = json.loads(pl)  # acme_org: the owner's question in Russian; the English hypothesis stays in the event
            self.assertTrue(pl["questions"][0]["question"].startswith("Утвердить новый срок пересмотра of_b?"))
            self.assertIn("of diff approve sd-2026-11-06-e2e", pl["questions"][0]["question"])
            self.assertEqual((pl["hypothesis"], pl["questions"][0]["options"]), ("h", ["approve", "reject"]))
            code = ("import sys; sys.path.insert(0, %r); import of_asks_answer; "
                    "of_asks_answer.record([%r], 'да')" % (os.path.join(HERE, "..", "bin"), ask))
            subprocess.run([sys.executable, "-c", code], env=env, check=True)
            r = run("diff", "apply", "sd-2026-11-06-e2e")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            doc = yaml.safe_load(open(os.path.join(t, "org", "registry", "positions.yaml")))
            self.assertEqual(doc["positions"][1]["mandate_exit"], {"done_when": "done", "review": "2026-12-01"})
            self.assertIn("applied", run("diff", "list").stdout)


class GoalChangeTest(unittest.TestCase):
    """docs/handover.md §3, goal_change: goals and conditions added, rewritten or closed in one diff."""
    D = {"id": "sd-2026-11-12-goals", "kind": "goal_change", "tier": "medium", "author": "of_a", "org": "ofb",
         "hypothesis": "h", "approvals": ["owner"], "predicted": [{"metric": "m", "to": 0}], "reversal": "back",
         "changes": {"goals": [
             {"op": "add", "id": "g-revenue", "value": {"title": "Revenue", "rank": 1, "measure": "m", "state": "active",
                                                       "owner": "founder"}},
             {"op": "add", "id": "g-orders", "value": {"parent": "g-revenue", "title": "Orders", "rank": 1,
                                                      "measure": "orders a day", "target": "+10%", "state": "active"}}],
             "conditions": [{"op": "add", "id": "c-uptime", "value": {"title": "Up", "measure": "m", "level": "99.9%",
                                                                       "state": "active"}}]}}

    def test_add_then_rewrite_and_close(self):
        self.assertEqual(of_diff.check_format(self.D), [])
        # an approval binds the full SHA-256: a 64-bit prefix could be collided by the diff's author (of_server)
        self.assertRegex(of_diff.digest(self.D), r"^[0-9a-f]{64}$")
        a, b = "0123456789abcdef" + "0" * 48, "0123456789abcdef" + "1" * 48   # two digests, one 16-character prefix
        self.assertFalse(of_diff.same_digest(a, b))
        self.assertFalse(of_diff.same_digest(a[:16], a))                      # a proposal with the old short digest
        self.assertFalse(of_diff.same_digest(a[:16], a[:16]))
        self.assertTrue(of_diff.same_digest(a, a))
        new, problems = of_diff.preview(self.D, REG)
        self.assertEqual(problems, [])
        doc = yaml.safe_load(new)
        self.assertIn("# a kept comment", new)
        self.assertEqual(doc["goals"]["g-orders"]["target"], "+10%")
        self.assertEqual(doc["goals"]["g-revenue"]["decided"], "pending approval of sd-2026-11-12-goals")
        self.assertEqual(doc["conditions"]["c-uptime"]["level"], "99.9%")
        self.assertEqual(doc["positions"], yaml.safe_load(REG)["positions"])
        stamped = yaml.safe_load(of_diff.stamp_decided(new, self.D, "ask-1234"))
        self.assertEqual({g["decided"] for g in stamped["goals"].values()} | {stamped["conditions"]["c-uptime"]["decided"]},
                         {"ask-1234"})
        later = dict(self.D, id="sd-2026-11-12-goals-2", changes={"goals": [
            {"op": "rewrite", "id": "g-orders", "value": {"target": "+15%"}},
            {"op": "close", "id": "g-revenue", "value": {"state": "achieved"}}]})
        text = of_diff.stamp_decided(new, self.D, "ask-1234").replace("goals:", "goals:  # the owner's tree", 1)
        newer, problems = of_diff.preview(later, text)
        self.assertEqual(problems, [])
        doc = yaml.safe_load(newer)
        self.assertEqual((doc["goals"]["g-orders"]["target"], doc["goals"]["g-orders"]["measure"]), ("+15%", "orders a day"))
        self.assertEqual(doc["goals"]["g-revenue"]["state"], "achieved")
        self.assertEqual(doc["goals"]["g-revenue"]["decided"], "pending approval of sd-2026-11-12-goals-2")
        self.assertEqual(doc["conditions"]["c-uptime"]["decided"], "ask-1234")   # untouched nodes keep theirs
        self.assertIn("goals:  # the owner's tree", newer)

    def test_propose_approve_apply_stamps_the_ask(self):
        import json
        import subprocess
        import tempfile
        of = os.path.join(HERE, "..", "bin", "of")
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "org", "registry"))
            os.makedirs(os.path.join(t, "state"))
            with open(os.path.join(t, "org", "registry", "positions.yaml"), "w") as fh:
                fh.write(REG)
            with open(os.path.join(t, "orgs.yaml"), "w") as fh:
                fh.write(f"orgs:\n  - name: ofb\n    path: {t}/org\n    workspace: ofb\n")
            env = dict(os.environ, OF_STATE=os.path.join(t, "state"), OF_ORGS=os.path.join(t, "orgs.yaml"),
                       OF_CONFIG_DIR=t, PATH="/usr/bin:/bin")
            env.pop("CLAUDE_CODE_SESSION_ID", None)
            run = lambda *a: subprocess.run([sys.executable, of, *a], env=env, capture_output=True, text=True, timeout=60)
            bad = os.path.join(t, "bad.yaml")  # a sub-goal without target: the goal checks run on the registry after
            with open(bad, "w") as fh:
                d = json.loads(json.dumps(self.D))
                del d["changes"]["goals"][1]["value"]["target"]
                yaml.safe_dump({"diff": dict(d, id="sd-2026-11-12-bad")}, fh)
            r = run("diff", "check", bad)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("target is required on a sub-goal", r.stdout)
            good = os.path.join(t, "sd.yaml")
            with open(good, "w") as fh:
                yaml.safe_dump({"diff": self.D}, fh)
            r = run("diff", "propose", good, "--from", "of_a", "--ask", "Approve the goal tree: revenue, then orders?")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            import sqlite3
            c = sqlite3.connect(os.path.join(t, "state", "of.db"))
            ask = c.execute("SELECT event_id FROM event WHERE name = 'of.agent_question'").fetchone()[0]
            c.close()
            code = ("import sys; sys.path.insert(0, %r); import of_asks_answer; "
                    "of_asks_answer.record([%r], 'approve')" % (os.path.join(HERE, "..", "bin"), ask))
            subprocess.run([sys.executable, "-c", code], env=env, check=True)
            r = run("diff", "apply", self.D["id"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(os.path.join(t, "org", "registry", "positions.yaml")) as fh:
                doc = yaml.safe_load(fh)
            cite = ask  # the answered ask, its full id
            self.assertEqual({g["decided"] for g in doc["goals"].values()}, {cite})
            self.assertEqual(doc["conditions"]["c-uptime"]["decided"], cite)
            # approved at the terminal (an of.diff_approved event, no answered ask): its event id is cited
            later = dict(self.D, id="sd-2026-11-12-goals-2",
                         changes={"goals": [{"op": "rewrite", "id": "g-orders", "value": {"target": "+15%"}}]})
            with open(good, "w") as fh:
                yaml.safe_dump({"diff": later}, fh)
            r = run("diff", "propose", good, "--from", "of_a", "--ask", "Raise the orders target to +15%?")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            c = sqlite3.connect(os.path.join(t, "state", "of.db"))
            dg = json.loads(c.execute("SELECT payload FROM event WHERE name = 'of.diff_proposed' "
                                      "AND payload LIKE '%goals-2%'").fetchone()[0])["digest"]
            c.close()
            code = ("import sys; sys.path.insert(0, %r); import of_events; "
                    "print(of_events.emit('diff_approved', {'id': %r, 'digest': %r, 'by': 'owner'}))"
                    % (os.path.join(HERE, "..", "bin"), later["id"], dg))
            eid = subprocess.run([sys.executable, "-c", code], env=env, check=True, capture_output=True,
                                 text=True).stdout.strip()
            r = run("diff", "apply", later["id"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(os.path.join(t, "org", "registry", "positions.yaml")) as fh:
                doc = yaml.safe_load(fh)
            self.assertEqual(doc["goals"]["g-orders"]["decided"], eid)
            self.assertEqual(doc["goals"]["g-revenue"]["decided"], cite)

    def test_a_short_digest_proposal_is_proposed_again(self):
        """hand-over ab02a7db: a diff proposed before the full digest is refused at approve and apply."""
        import subprocess
        import tempfile
        of = os.path.join(HERE, "..", "bin", "of")
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "org", "registry"))
            os.makedirs(os.path.join(t, "state"))
            with open(os.path.join(t, "org", "registry", "positions.yaml"), "w") as fh:
                fh.write(REG)
            with open(os.path.join(t, "orgs.yaml"), "w") as fh:
                fh.write(f"orgs:\n  - name: ofb\n    path: {t}/org\n    workspace: ofb\n")
            env = dict(os.environ, OF_STATE=os.path.join(t, "state"), OF_ORGS=os.path.join(t, "orgs.yaml"),
                       OF_CONFIG_DIR=t, PATH="/usr/bin:/bin")
            short = of_diff.digest(self.D)[:16]
            code = ("import sys; sys.path.insert(0, %r); import of_events; of_events.emit('diff_proposed', "
                    "{'id': %r, 'kind': 'goal_change', 'tier': 'medium', 'org': 'ofb', 'author': 'of_a', 'digest': %r, "
                    "'diff': %r})" % (os.path.join(HERE, "..", "bin"), self.D["id"], short, self.D))
            subprocess.run([sys.executable, "-c", code], env=env, check=True)
            run = lambda *a: subprocess.run([sys.executable, of, *a], env=env, capture_output=True, text=True, timeout=60)
            r = run("diff", "approve", self.D["id"], "--prompt", "p-1")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("proposed with the old short digest", r.stdout + r.stderr)
            code = ("import sys; sys.path.insert(0, %r); import of_events; "
                    "of_events.emit('diff_approved', {'id': %r, 'digest': %r, 'by': 'owner'})"
                    % (os.path.join(HERE, "..", "bin"), self.D["id"], short))
            subprocess.run([sys.executable, "-c", code], env=env, check=True)
            r = run("diff", "apply", self.D["id"])   # approved with the short digest: still refused
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("propose it again", r.stdout + r.stderr)

    def test_refusals(self):
        self.assertIn("medium tier at least", "\n".join(of_diff.check_format(dict(self.D, tier="low"))))
        self.assertIn("changes.goals or changes.conditions is required",
                      "\n".join(of_diff.check_format(dict(self.D, changes={"registry": [{"op": "set"}]}))))
        cases = {"decided is written by `of diff apply`": {"op": "add", "id": "g-x", "value": {"decided": "a"}},
                 "no goal 'g-none' to rewrite": {"op": "rewrite", "id": "g-none", "value": {"rank": 2}},
                 "takes {op: add | rewrite | close": {"op": "delete", "id": "g-x"}}
        for want, c in cases.items():
            self.assertIn(want, "\n".join(of_diff.preview(dict(self.D, changes={"goals": [c]}), REG)[1]), want)
        added = of_diff.preview(self.D, REG)[0]
        for want, c in {"g-revenue exists already": {"op": "add", "id": "g-revenue", "value": {"rank": 3}},
                        "close sets state to dropped": {"op": "close", "id": "c-uptime", "value": {"state": "achieved"}}
                        }.items():
            block = "conditions" if c["id"].startswith("c-") else "goals"
            self.assertIn(want, "\n".join(of_diff.preview(dict(self.D, changes={block: [c]}), added)[1]), want)
        other = dict(self.D, kind="mandate_rewrite", changes={"registry": [{"op": "set", "name": "of_b",
                                                                             "fields": {"summary": "x"}}],
                                                               "goals": [{"op": "add", "id": "g-x", "value": {}}]})
        self.assertIn("belong to a goal_change diff", "\n".join(of_diff.preview(other, REG)[1]))


DEL = REG + """
units:
  platform:
    owner: of_b
    systems: [organic-framework]
delegations:
  - id: del-x
    unit: platform
    from_owner: founder
    to_owner: of_b
    envelope:
      grants: attenuated  # kept
      budget:
        tokens_month: 1000
      critical_denies: inherited
    reporting:
      escalate_when:
      - ask.blocking
  - id: del-y
    unit: platform
    to_owner: of_c
"""

AGENTS = """# Rules

## Working together

- **Nobody waits unseen.** Ask with of ask.
- **In a delegated unit, ask the unit owner first**; the founder gets
  only escalations.
- Never stop another agent.
"""


class DiffV2Test(unittest.TestCase):
    """amend_delegation and agents_md insert_line (of_architect, 2026-11-06)."""

    def test_amend_delegation_in_place(self):
        approvals = "inside the unit the owner's yes stands for the founder's yes on routine merges and applies"
        d = diff("mandate_rewrite", {"op": "amend_delegation", "id": "del-x", "under": "envelope",
                                     "set": {"approvals": approvals, "budget": {"tokens_month": 2000}}})
        new, problems = of_diff.preview(d, DEL)
        self.assertEqual(problems, [])
        doc = yaml.safe_load(new)
        env = doc["delegations"][0]["envelope"]
        self.assertEqual((env["approvals"], env["budget"], env["critical_denies"]), (approvals, {"tokens_month": 2000}, "inherited"))
        self.assertIn("grants: attenuated  # kept", new)  # comments stay
        self.assertEqual(of_diff.amend_delegation(new, "del-x", "envelope", {"approvals": approvals}), new)  # idempotent
        self.assertEqual(doc["delegations"][1], {"id": "del-y", "unit": "platform", "to_owner": "of_c"})
        self.assertEqual(doc["delegations"][0]["reporting"], {"escalate_when": ["ask.blocking"]})

    def test_amend_a_list_written_at_the_keys_indent(self):
        """acme_org: yaml.safe_dump writes a list at its key's indent; the old items must go too."""
        new_list = ["ask.blocking", "money", "irreversible"]
        d = diff("mandate_rewrite", {"op": "amend_delegation", "id": "del-x", "under": "reporting",
                                     "set": {"escalate_when": new_list}})
        new, problems = of_diff.preview(d, DEL)
        self.assertEqual(problems, [])
        self.assertEqual(yaml.safe_load(new)["delegations"][0]["reporting"]["escalate_when"], new_list)
        self.assertEqual(new.count("- ask.blocking"), 1)

    def test_amend_may_widen_systems_and_positions_never_narrow(self):
        rec = DEL.replace("    from_owner: founder\n", "    systems:\n    - organic-framework\n    positions:\n    - of_a\n"
                          "    from_owner: founder\n", 1)
        d = diff("mandate_rewrite", {"op": "amend_delegation", "id": "del-x",
                                     "set": {"systems": ["organic-framework", "net"], "positions": ["of_a", "of_b"]}})
        new, problems = of_diff.preview(d, rec)
        self.assertEqual(problems, [])
        got = yaml.safe_load(new)["delegations"][0]
        self.assertEqual((got["systems"], got["positions"]), (["organic-framework", "net"], ["of_a", "of_b"]))
        self.assertEqual(new.count("- of_a"), 1)  # the old list is replaced whole
        _, problems = of_diff.preview(diff("mandate_rewrite", {"op": "amend_delegation", "id": "del-x",
                                                               "set": {"positions": ["of_b"]}}), rec)
        self.assertTrue(any("may only grow" in p and "of_a" in p for p in problems), problems)

    def test_amend_delegation_limits(self):
        for change, want in (({"id": "del-z", "set": {"review": "x"}}, "no delegation"),
                             ({"id": "del-x", "set": {"to_owner": "of_c"}}, "op delegate"),
                             ({"id": "del-x", "under": "envelope", "set": {"critical_denies": "none"}}, "inherited"),
                             ({"id": "del-x", "under": "grants", "set": {"a": 1}}, "under is envelope")):
            _, problems = of_diff.preview(diff("mandate_rewrite", dict(op="amend_delegation", **change)), DEL)
            self.assertTrue(any(want in p for p in problems), (change, problems))
        twice = DEL + "  - id: del-x\n    unit: platform\n"
        with self.assertRaises(ValueError):
            of_diff.amend_delegation(twice, "del-x", None, {"review": "x"})

    def test_insert_line_after_a_whole_bullet(self):
        line = "- **In the platform unit, platform_owner's yes stands for the founder's yes** on routine merges."
        out = of_diff.insert_line(AGENTS, "- **In a delegated unit, ask the unit owner first**", line)
        lines = out.split("\n")
        self.assertEqual(lines[lines.index(line) - 1], "  only escalations.")  # after the continuation line
        self.assertEqual(of_diff.insert_line(out, "- **In a delegated unit", line), out)  # idempotent
        with self.assertRaises(ValueError):
            of_diff.insert_line(AGENTS, "- **", line)  # an anchor must match exactly one line
        _, briefs, problems = of_diff.plan(dict(BASE, kind="mandate_rewrite", changes={
            "registry": [{"op": "amend_delegation", "id": "del-x", "set": {"review": "2026-11-13"}}],
            "agents_md": [{"op": "insert_line", "after": "- **In a delegated", "text": line}]}),
            yaml.safe_load(DEL))
        self.assertEqual((problems, briefs[0]["path"]), ([], "AGENTS.md"))


class RenderTest(unittest.TestCase):
    def test_unit_and_multiline_read_back(self):
        for text in ("One.\nTwo.\n", "One.\nTwo.", "single line"):
            v = {"name": "of_c", "system": "organic-framework", "unit": "platform", "role": "c", "kind": "project",
                 "owner": "founder", "summary": "s", "status": "active", "contribution": text}
            new, problems = of_diff.preview(diff("spawn", {"op": "add", "value": v}), REG)
            self.assertEqual(problems, [], text)
            entry = new[new.index("- name: of_c"):]
            self.assertLess(entry.index("unit:"), entry.index("role:"))  # unit right after system
        _, problems = of_diff.preview(diff("mandate_rewrite", {"op": "set", "name": "of_a", "fields": {"unit": "x"}}), REG)
        self.assertNotIn("may set only", "\n".join(problems))


MOVE_REG = """organization: mt
methods: {}
positions:
  - name: wf_keeper
    system: payouts
    unit: shop
    role: keeper
    kind: keeper
    owner: founder
    summary: "moves funds"
    status: active
  - name: treasury_keeper
    system: treasury
    unit: company
    role: keeper
    kind: keeper
    owner: founder
    summary: "treasury"
    status: active
    owns:
      - {system: payouts, method: account, decides: true}

units:
  shop:
    owner: tp_owner
    # payouts stays here until the move
    systems: [storefront, payouts]   # a kept comment
  company:
    owner: acme_owner
    systems: [treasury]

systems:
  storefront: {keeper: wf_keeper}
  payouts: {keeper: wf_keeper}
  treasury: {keeper: treasury_keeper}

delegations:
  - id: del-shop
    unit: shop
    systems:
    - storefront
    - payouts
    positions:
    - wf_keeper
    to_owner: tp_owner
"""


class MoveAndOwnsTest(unittest.TestCase):
    """acme_org, 2026-11-11 (sd-2026-11-11-payouts-move): a system and its keeper change unit, and the keeper's
    cells are set, without hand edits after the founder's yes."""

    MOVE = {"op": "move_system", "system": "payouts", "from_unit": "shop", "to_unit": "company"}

    def test_move_system_between_units_and_delegations(self):
        d = dict(diff("mandate_rewrite", self.MOVE, {"op": "set", "name": "wf_keeper", "fields": {"unit": "company"}}),
                 tier="high")
        new, problems = of_diff.preview(d, MOVE_REG)
        self.assertEqual(problems, [])
        doc = yaml.safe_load(new)
        self.assertEqual(doc["units"]["shop"]["systems"], ["storefront"])
        self.assertEqual(doc["units"]["company"]["systems"], ["treasury", "payouts"])
        self.assertEqual(doc["delegations"][0]["systems"], ["storefront"])
        self.assertEqual(doc["delegations"][0]["positions"], ["wf_keeper"])  # positions are not the op's
        self.assertIn("systems: [storefront]   # a kept comment", new)
        self.assertIn("# payouts stays here until the move", new)
        _, p = of_diff.preview(dict(d, tier="medium"), MOVE_REG)
        self.assertIn("high tier", "\n".join(p))  # it changes a delegation
        bad = dict(self.MOVE, from_unit="company", to_unit="nowhere")
        _, p = of_diff.preview(dict(diff("mandate_rewrite", bad), tier="high"), MOVE_REG)
        self.assertIn("no unit 'nowhere'", "\n".join(p))
        self.assertIn("unit company does not list payouts", "\n".join(p))

    def test_owns_set_by_a_mandate_rewrite_one_decider_per_cell(self):
        owns = [{"system": "payouts", "method": "build", "decides": True},
                {"system": "payouts", "method": "operate", "decides": True}]
        d = diff("mandate_rewrite", {"op": "set", "name": "wf_keeper", "fields": {"owns": owns}})
        new, problems = of_diff.preview(d, MOVE_REG)
        self.assertEqual(problems, [])
        self.assertEqual(yaml.safe_load(new)["positions"][0]["owns"], owns)
        two = owns + [{"system": "payouts", "method": "account", "decides": True}]
        _, p = of_diff.preview(diff("mandate_rewrite", {"op": "set", "name": "wf_keeper", "fields": {"owns": two}}),
                               MOVE_REG)
        self.assertIn("payouts x account has 2 deciders", "\n".join(p))
        doer = owns + [{"system": "payouts", "method": "account", "decides": False}]
        _, p = of_diff.preview(diff("mandate_rewrite", {"op": "set", "name": "wf_keeper", "fields": {"owns": doer}}),
                               MOVE_REG)
        self.assertEqual(p, [])
        _, p = of_diff.preview(diff("mandate_rewrite", {"op": "set", "name": "wf_keeper", "fields": {"owns": [
            {"system": "ghost", "method": "vibes", "decides": "yes"}]}}), MOVE_REG)
        self.assertIn("decides: true|false", "\n".join(p))
        _, p = of_diff.preview(diff("mandate_rewrite", {"op": "set", "name": "wf_keeper", "fields": {"owns": [
            {"system": "ghost", "method": "vibes"}]}}), MOVE_REG)
        self.assertIn("'ghost' is not a system", "\n".join(p))
        self.assertIn("'vibes' is not a kind of work", "\n".join(p))


POS_REG = """organization: mt
positions:
  - name: tp_owner
    system: ops
    role: owner
    kind: manager
    owner: founder
    summary: "units owner"
    status: active
  - name: feed_keeper
    system: feed
    role: keeper
    kind: keeper
    owner: founder
    summary: "token prices"
    status: active
  - name: cat_keeper
    system: catalogue
    role: keeper
    kind: keeper
    owner: founder
    summary: "catalogue"
    status: active

units:
  shop:
    owner: tp_owner
    systems: [feed]
  catalogue:
    owner: tp_owner
    systems: [catalogue]
  company:
    owner: acme_owner
    systems: [ops]

systems:
  feed: {keeper: feed_keeper}
  catalogue: {keeper: cat_keeper}
  ops: {keeper: tp_owner}

delegations:
  - id: del-shop
    unit: shop
    positions: [feed_keeper]   # counted in shop's budget
    from_owner: acme_owner
    to_owner: tp_owner
  - id: del-catalogue
    unit: catalogue
    positions:
    - cat_keeper
    from_owner: acme_owner
    to_owner: tp_owner
"""


class MovePositionTest(unittest.TestCase):
    """docs/handover.md, move_position (a user organization, 2026-11-11: a position moved between two units delegated
    to the same owner was still counted in the old unit's budget)."""

    MOVE = {"op": "move_position", "position": "feed_keeper", "from_unit": "shop", "to_unit": "catalogue"}

    def test_the_unit_and_both_delegation_lists_in_their_style(self):
        new, problems = of_diff.preview(dict(diff("mandate_rewrite", self.MOVE), tier="medium"), POS_REG)
        self.assertEqual(problems, [])
        doc = yaml.safe_load(new)
        self.assertEqual(next(p for p in doc["positions"] if p["name"] == "feed_keeper")["unit"], "catalogue")
        self.assertEqual(doc["delegations"][0]["positions"], [])
        self.assertEqual(doc["delegations"][1]["positions"], ["cat_keeper", "feed_keeper"])
        self.assertIn("positions: []   # counted in shop's budget", new)  # the flow list keeps its comment
        self.assertIn("    - feed_keeper", new)                             # the block list stays a block list

    def test_a_position_already_in_the_new_unit_only_leaves_the_old_list(self):
        """acme_org, 2026-11-11: the position's system moved, so its unit followed; only the old list still named it."""
        reg = POS_REG.replace("  shop:\n    owner: tp_owner\n    systems: [feed]", "  shop:\n    owner: tp_owner\n    systems: []") \
                     .replace("    systems: [catalogue]", "    systems: [catalogue, feed]")
        new, problems = of_diff.preview(dict(diff("mandate_rewrite", self.MOVE), tier="medium"), reg)
        self.assertEqual(problems, [])
        doc = yaml.safe_load(new)
        self.assertNotIn("unit", next(p for p in doc["positions"] if p["name"] == "feed_keeper"))  # follows its system
        self.assertEqual((doc["delegations"][0]["positions"], doc["delegations"][1]["positions"]),
                         ([], ["cat_keeper", "feed_keeper"]))
        listed = POS_REG.replace("positions: [feed_keeper]", "positions: []").replace(
            "    systems: [feed]", "    systems: []").replace("    systems: [catalogue]", "    systems: [catalogue, feed]")
        _, p = of_diff.preview(dict(diff("mandate_rewrite", self.MOVE), tier="medium"), listed)
        self.assertIn("shop's delegation does not list it", "\n".join(p))  # nothing to clean up

    def test_refusals(self):
        _, p = of_diff.preview(dict(diff("mandate_rewrite", self.MOVE), tier="low"), POS_REG)
        self.assertIn("medium tier at least", "\n".join(p))
        other = dict(self.MOVE, to_unit="company")  # company is not delegated: authority changes hands
        _, p = of_diff.preview(dict(diff("mandate_rewrite", other), tier="medium"), POS_REG)
        self.assertIn("reverse diff of shop's delegation", "\n".join(p))
        wrong = dict(self.MOVE, position="cat_keeper")
        _, p = of_diff.preview(dict(diff("mandate_rewrite", wrong), tier="medium"), POS_REG)
        self.assertIn("cat_keeper is in unit catalogue, not shop", "\n".join(p))


class AddUnitTest(unittest.TestCase):
    """acme_org, 2026-11-11: a spawn diff could not add the units company and compliance; they were added by hand."""

    def spawn(self, *more):
        v = {"name": "comp_owner", "system": "treasury", "unit": "compliance", "role": "owner", "kind": "manager",
             "owner": "founder", "summary": "second line", "status": "active"}
        return diff("spawn", {"op": "add", "value": v}, *more)

    def test_a_spawn_adds_its_unit(self):
        add = {"op": "add_unit", "id": "compliance", "name": "Compliance: the second line", "owner": "comp_owner",
               "systems": ["storefront"]}
        _, p = of_diff.preview(self.spawn(add), MOVE_REG)
        self.assertIn("storefront is in unit shop; a system sits in one unit", "\n".join(p))
        move = {"op": "move_system", "system": "storefront", "from_unit": "shop", "to_unit": "compliance"}
        new, p = of_diff.preview(dict(self.spawn(add, move), tier="high"), MOVE_REG)
        self.assertEqual(p, [])
        doc = yaml.safe_load(new)
        self.assertEqual(doc["units"]["compliance"], {"name": "Compliance: the second line", "owner": "comp_owner",
                                                      "systems": ["storefront"]})
        self.assertEqual(doc["units"]["shop"]["systems"], ["payouts"])
        self.assertIn("# a kept comment", new)
        self.assertEqual(list(doc["units"]), ["shop", "company", "compliance"])  # appended to units:

    def test_add_unit_checks(self):
        bad = {"op": "add_unit", "id": "company", "name": "x", "owner": "ghost", "systems": ["nope"]}
        _, p = of_diff.preview(diff("mandate_rewrite", bad), MOVE_REG)
        text = "\n".join(p)
        for expected in ("unit company exists already", "owner 'ghost' is not a position", "'nope' is not a system"):
            self.assertIn(expected, text)
        _, p = of_diff.preview(diff("mandate_rewrite", {"op": "add_unit", "id": "x"}), MOVE_REG)
        self.assertIn("add_unit takes", "\n".join(p))
        new = of_diff.add_unit("positions: []\n", "u1", "A unit", "a", ["s"])
        self.assertEqual(yaml.safe_load(new)["units"], {"u1": {"name": "A unit", "owner": "a", "systems": ["s"]}})
