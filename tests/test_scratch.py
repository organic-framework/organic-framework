"""Scratch folders and free disk (bin/of_scratch.py; Acme, 2026-11-11): sized on a slow cadence, a position
told once over the cap, one founder card per low-disk episode that closes itself."""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_scratch  # noqa: E402
import of_store  # noqa: E402

GiB = of_scratch.GiB
POS = [{"name": n, "_org": "t"} for n in ("big", "small", "away")]


def agent(name, sid, status="idle"):
    return {"pane_id": f"p-{name}", "agent_status": status, "name": name, "agent_session": {"value": sid}}


def bind(positions, agents):
    by = {a["name"]: a for a in agents}
    return [(p, by[p["name"]]) for p in positions if p["name"] in by], [], []


class ScratchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = os.path.join(self.tmp.name, "state")
        os.makedirs(of_events.STATE_DIR)
        self.base = os.path.join(self.tmp.name, "claude-501")
        for proj, sid in (("-Users-x-repo", "S-big"), ("-Users-x-repo", "S-small"), ("-Users-x-other", "S-big")):
            os.makedirs(os.path.join(self.base, proj, sid))
        self.sizes = {"S-big": 2 * GiB, "S-small": GiB // 10}
        self.calls = []

    def tearDown(self):
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def size(self, path):
        self.calls.append(path)
        return self.sizes[os.path.basename(path)]

    def run_check(self, mem, now, free, agents=None, typed=None, positions=POS, keeper=None):
        agents = agents or [agent("big", "S-big"), agent("small", "S-small"), agent("away", "S-none")]
        return of_scratch.check(positions, agents, bind, mem, now=now, free=free, size=self.size, base=self.base,
                                prompt=lambda pane, text, who: (typed if typed is not None else []).append((pane, text)),
                                log=lambda m: None, swap=lambda: 14 * GiB, store=lambda: 11 * GiB,
                                keeper=keeper or (lambda ps, org: None))

    def events(self, name):
        c = of_store.connect()
        out = [json.loads(p) for (p,) in c.execute("SELECT payload FROM event WHERE name = ?", (name,))]
        c.close()
        return out

    def test_told_once_over_the_cap_sized_slowly(self):
        mem, typed, t = {}, [], 1_000_000
        self.assertEqual(self.run_check(mem, t, 60 * GiB, typed=typed), ["big"])  # 2 folders x 2 GiB = 4 GiB
        self.assertEqual([p for p, _ in typed], ["p-big"])
        self.assertIn("4.0 GiB, over the 3.0 GiB cap", typed[0][1])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.run_check(mem, t + 60, 60 * GiB, typed=typed), [])  # not re-sized, not told again
        self.assertEqual(len(self.calls), 3)
        self.run_check(mem, t + of_scratch.OVER_EVERY_S, 60 * GiB, typed=typed)  # over the cap: every pulse
        self.assertEqual(len(self.calls), 5)  # big's two folders; small (under the cap) waits its 30 minutes
        self.sizes["S-big"] = GiB // 2
        self.run_check(mem, t + of_scratch.SIZE_EVERY_S, 60 * GiB, typed=typed)     # pruned: under the cap
        self.sizes["S-big"] = 2 * GiB
        self.assertEqual(self.run_check(mem, t + 2 * of_scratch.SIZE_EVERY_S, 60 * GiB, typed=typed), ["big"])
        usage = self.events("of.disk_usage")
        self.assertEqual(len(usage), 4)  # one more sizing: the over-cap pulse
        self.assertEqual({r["position"]: r["bytes"] for r in usage[0]["scratch"]}, {"big": 4 * GiB, "small": GiB // 10})
        self.assertEqual(len([e for e in self.events("of.nudge_sent") if e["kind"] == "scratch_over_cap"]), 2)

    def test_a_busy_position_is_told_when_idle_and_an_ended_session_is_forgotten(self):
        mem, typed = {}, []
        self.assertEqual(self.run_check(mem, 1, 60 * GiB, [agent("big", "S-big", "working")], typed), [])
        self.assertEqual(self.run_check(mem, 2, 60 * GiB, [agent("big", "S-big")], typed), ["big"])
        self.run_check(mem, 3, 60 * GiB, [agent("small", "S-small")], typed)
        self.assertNotIn("S-big", mem["scratch"])

    def test_one_founder_card_per_episode_closed_by_the_pulse(self):
        mem = {}
        self.run_check(mem, 1, 19 * GiB)
        self.run_check(mem, 2, 18 * GiB)
        cards = [e for e in self.events("of.agent_question") if e.get("source") == "disk_check"]
        self.assertEqual(len(cards), 1)
        q = cards[0]["questions"][0]["question"]                           # English unless owner.yaml says otherwise
        self.assertIn("What takes the space: swap 14.0 GiB (it grows when memory runs short: fewer live sessions "
                      "shrink it); OF's event store 11.0 GiB; the agents' temporary folders 4.1 GiB, most in "
                      "Users-x-repo 2.1 GiB, Users-x-other 2.0 GiB.", q)                       # 2026-11-14: name what takes the space
        self.run_check(mem, 3, 22 * GiB)  # between the marks: same episode, still open
        self.assertEqual(self.events("of.agent_question_answered"), [])
        self.run_check(mem, 4, 26 * GiB)
        self.assertEqual(len(self.events("of.agent_question_answered")), 1)
        self.run_check(mem, 5, 19 * GiB)  # a new episode
        self.assertEqual(len([e for e in self.events("of.agent_question") if e.get("source") == "disk_check"]), 2)

    def test_over_the_quota_is_listed_for_the_guard_until_back_under_the_cap(self):
        mem, typed, t = {}, [], 1_000_000
        self.sizes["S-big"] = 4 * GiB  # two folders: 8 GiB, over the 6 GiB quota
        self.run_check(mem, t, 60 * GiB, typed=typed)
        q = of_scratch.read_quota()
        self.assertEqual(list(q), ["S-big"])
        self.assertEqual((q["S-big"]["position"], q["S-big"]["bytes"]), ("big", 8 * GiB))
        self.assertEqual(len(typed), 1)
        self.assertIn("over the 6.0 GiB quota", typed[0][1])
        n = len(self.calls)
        self.sizes["S-big"] = 2 * GiB  # 4 GiB: under the quota, over the cap: still listed, re-sized every pulse
        self.run_check(mem, t + of_scratch.OVER_EVERY_S, 60 * GiB, typed=typed)
        self.assertGreater(len(self.calls), n)
        self.assertIn("S-big", of_scratch.read_quota())
        self.sizes["S-big"] = GiB  # 2 GiB: under the cap, released
        self.run_check(mem, t + 2 * of_scratch.OVER_EVERY_S, 60 * GiB, typed=typed)
        self.assertEqual(of_scratch.read_quota(), {})
        self.assertEqual(len(typed), 1)  # told once

    def test_a_project_over_its_cap_tells_its_structure_keeper_once(self):
        """2026-11-14, a user organization's structure keeper: 24 GiB in one project folder, every session under its own quota."""
        positions = POS + [{"name": "keeper", "_org": "t"}]
        os.makedirs(os.path.join(self.base, "-Users-x-repo", "S-k"))
        self.sizes.update({"S-big": 5 * GiB, "S-small": 5 * GiB, "S-k": GiB // 10})
        agents = [agent("big", "S-big", "busy"), agent("small", "S-small", "busy"), agent("keeper", "S-k", "busy")]
        typed, mem = [], {}
        keeper = lambda ps, org: "keeper" if org == "t" else None  # noqa: E731
        self.run_check(mem, 1, 100 * GiB, agents, typed, positions, keeper)
        self.assertEqual([p for p, _ in typed if p == "p-keeper"], [])          # 10.1 GiB in x-repo: under the cap
        self.sizes["S-small"] = 7 * GiB                                         # a quota's folder, and 12.1 GiB together
        agents[2] = agent("keeper", "S-k", "busy")
        for now in (2000, 4000):                                                # busy: told once it is idle
            self.run_check(mem, now, 100 * GiB, agents, typed, positions, keeper)
        self.assertEqual([p for p, _ in typed if p == "p-keeper"], [])
        agents[2] = agent("keeper", "S-k")
        for now in (6000, 8000):
            self.run_check(mem, now, 100 * GiB, agents, typed, positions, keeper)
        notes = [t for p, t in typed if p == "p-keeper"]
        self.assertEqual(len(notes), 1)                                          # once per episode
        self.assertIn("The live sessions in the project folder Users-x-repo together hold 12.1 GiB", notes[0])
        self.assertIn("small 7.0 GiB; big 5.0 GiB; keeper 0.1 GiB", notes[0])
        u = self.events("of.disk_usage")[-1]
        self.assertEqual((u["swap_bytes"], u["store_bytes"], u["projects"][0]),
                         (14 * GiB, 11 * GiB, {"project": "Users-x-repo", "bytes": 12 * GiB + GiB // 10}))
        self.sizes["S-small"] = GiB                                              # back under: a new episode tells again
        for now in (10000, 12000):
            self.run_check(mem, now, 100 * GiB, agents, typed, positions, keeper)
        self.sizes["S-small"] = 7 * GiB
        for now in (14000, 16000):
            self.run_check(mem, now, 100 * GiB, agents, typed, positions, keeper)
        self.assertEqual(len([t for p, t in typed if p == "p-keeper"]), 2)

    def test_a_project_is_named_without_the_home_folder(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"HOME": "/Users/alex"}):
            self.assertEqual(of_scratch.project_of("/private/tmp/claude-501/-Users-alex-acme-repo/S"), "acme-repo")
            self.assertEqual(of_scratch.project_of("/private/tmp/claude-501/-srv-acme/S"), "srv-acme")

    def test_folders_never_leave_the_base(self):
        self.assertEqual(of_scratch.folders("../x", self.base), [])
        self.assertEqual(of_scratch.folders("", self.base), [])
        self.assertEqual(len(of_scratch.folders("S-big", self.base)), 2)


if __name__ == "__main__":
    unittest.main()
