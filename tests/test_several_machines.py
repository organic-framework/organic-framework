"""Two machines of one organization (docs/server-several-machines.md §4.3, §4.4): only the machine that holds a
position acts on it and records events about it. The coordination feed is simulated by copying each store's shared
events into the other one, as the relay's pull does (raw_ref coord:)."""

import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_asks  # noqa: E402
import of_coord  # noqa: E402
import of_events  # noqa: E402
import of_handover  # noqa: E402
import of_presence  # noqa: E402
import of_pulse  # noqa: E402
import of_store  # noqa: E402

DELEG = {"id": "del-u", "unit": "u", "to_owner": "o", "reporting": {"escalate_when": ["ask.blocking"]}}
POS = [{"name": n, "status": "active", "_org": "t", "_orgid": "t", **extra} for n, extra in (
    ("w", {}), ("p", {}), ("o", {}),
    ("s1", {"_unit": "u", "_unit_owner": "o", "_delegation": DELEG}),
    ("s2", {"_unit": "u", "_unit_owner": "o", "_delegation": DELEG}))]


def agent(name, status):
    return {"pane_id": f"p-{name}", "agent_status": status, "name": name, "agent_session": {"value": f"S-{name}"}}


def bind(positions, agents):
    by = {a["name"]: a for a in agents}
    return [(p, by[p["name"]]) for p in positions if p["name"] in by], [], []


class Machine:
    def __init__(self, root, name, agents):
        self.dir = os.path.join(root, name)
        os.makedirs(self.dir)
        self.agents, self.typed, self.seq = agents, [], 0

    def __enter__(self):
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.dir
        return self

    def __exit__(self, *a):
        of_events.STATE_DIR = self.saved

    def conn(self):
        with self:
            return of_store.connect()

    def held(self):
        return {a["name"] for a in self.agents}

    def pulse(self, other, now):
        with self:
            conn = of_store.connect()
            rows = of_asks.load(conn)
            asks = of_asks.project(rows)
            handovers = of_handover.project(of_handover.load(conn))
            rows = of_pulse.with_messages(conn, rows)
            away = {("t", n): {"machine_id": "x"} for n in other.held()}
            of_pulse.run(POS, self.agents, rows, asks, bind, now=now, handovers=handovers,
                         send=lambda *a, **k: None, log=lambda m: None,
                         prompt=lambda pane, text, who: self.typed.append((pane, text)), elsewhere=away)
            conn.close()


def feed(src, dst):
    """Copy src's shared events it wrote itself into dst, as the relay pulls the feed (skipping ids dst has)."""
    a, b = src.conn(), dst.conn()
    for rec, raw in a.execute("SELECT record, raw_ref FROM event ORDER BY ts, rowid"):
        r = json.loads(rec)
        if of_coord.shared(r["event.name"]) and not (raw or "").startswith(of_coord.RAW_REF):
            dst.seq += 1
            of_store.add_event(b, r, raw_ref=f"{of_coord.RAW_REF}t:{dst.seq}")
    a.close(), b.close()


def count(m, name, **where):
    c = m.conn()
    n = 0
    for (pl,) in c.execute("SELECT payload FROM event WHERE name = ?", (name,)):
        p = json.loads(pl or "{}")
        n += all(p.get(k) == v for k, v in where.items())
    c.close()
    return n


class TwoMachinesTest(unittest.TestCase):
    def test_one_wait_across_machines_one_nudge_no_duplicates(self):
        with tempfile.TemporaryDirectory() as root:
            # A holds the waiter w and the asker s1; B holds the peer p and the unit owner o; s2 is held nowhere
            A = Machine(root, "a", [agent("w", "working"), agent("s1", "working")])
            B = Machine(root, "b", [agent("p", "idle"), agent("o", "working")])
            self.assertNotEqual(_mid(A), _mid(B))  # each store has its own machine id
            now = time.time()
            with A:
                of_events.emit("state_declared", {"state": "waiting", "waiting_on": "p", "note": "need the API"},
                               position="w", org="t", session="S-w")
                for who in ("s1", "s2"):
                    of_events.emit("agent_question", {"detected_by": "declared", "to": "o", "blocking": True,
                                                      "questions": [{"question": f"{who}: roll back prod?"}]},
                                   position=who, org="t", session=f"S-{who}")
                mem = of_pulse.load_memory()
            with B:
                mem = of_pulse.load_memory()
                mem["panes"] = {"p-p": ["idle", now - 40 * 60]}  # p idle long enough to count as stalled
                of_pulse.save_memory(mem)
            feed(A, B), feed(B, A)
            for k in range(2):  # two rounds: the second sees the other machine's events through the feed
                t = now + 60 * k
                A.pulse(B, t), B.pulse(A, t)
                feed(A, B), feed(B, A)

            self.assertEqual(A.typed, [])  # nothing typed for a pane A does not hold
            self.assertEqual([p for p, _ in B.typed], ["p-p"])  # exactly one nudge, on the peer's machine
            self.assertTrue(B.typed[0][1].startswith("Message from w (via of pulse"))
            for m in (A, B):
                self.assertEqual(count(m, "of.nudge_sent", to_position="p"), 1)
                self.assertEqual(count(m, "of.stall_detected", position="p"), 1)
                self.assertEqual(count(m, "of.ask_escalated"), 2)  # s1 by A only; s2 by both, one id
                self.assertEqual(count(m, "of.agent_question", source="escalated"), 2)
                self.assertEqual(count(m, "of.holder_unbound"), 0)  # no machine unbinds another's holder
                self.assertEqual(count(m, "of.holder_bound"), 4)  # w, s1 on A; p, o on B
                for name in ("w", "p", "o", "s1", "s2"):
                    self.assertEqual(count(m, "of.mandate_changed", position=name), 1, name)

    def test_a_machine_alone_unbinds_its_own_holder(self):
        with tempfile.TemporaryDirectory() as root:
            A = Machine(root, "a", [agent("w", "working")])
            nobody = Machine(root, "z", [])
            now = time.time()
            A.pulse(nobody, now)
            A.agents = []
            A.pulse(nobody, now + 60)
            self.assertEqual(count(A, "of.holder_unbound", position="w"), 1)


def _mid(m):
    with m:
        return of_events.machine_id()


class PresenceTest(unittest.TestCase):
    def test_remote_holders_only_from_other_machines(self):
        class C:
            def presence(self, org):
                return {"holders": [
                    {"machine_id": "me", "position": "w", "state": "idle", "age_s": 3},
                    {"machine_id": "m2", "position": "p", "state": "working", "age_s": 12, "person": "ana"},
                    {"machine_id": "m2", "position": "zz", "state": "idle", "age_s": 5}]}
        with tempfile.TemporaryDirectory() as d:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = d
            try:
                kw = dict(me="me", servers={}, default="http://x", client_for=lambda url: C(), cache_s=0)
                got = of_presence.remote({"t"}, **kw)
                self.assertEqual([h["position"] for h in got["t"]], ["p", "zz"])
                away = of_presence.elsewhere(POS, {"o"}, **kw)
                self.assertEqual(list(away), [("t", "p")])  # zz is not a registry position
                self.assertEqual(of_presence.elsewhere(POS, {"p"}, **kw), {})  # held here too: here wins

                class Down:
                    def presence(self, org):
                        raise OSError("refused")
                self.assertEqual(of_presence.remote({"t"}, **dict(kw, client_for=lambda url: Down())), {})
            finally:
                of_events.STATE_DIR = saved

    def test_who_lists_machine_person_and_age(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
        spec = importlib.util.spec_from_loader("of_cli", loader)
        of = importlib.util.module_from_spec(spec)
        loader.exec_module(of)
        rows = [{"position": "p", "name": "p", "state": "offline", "pane": ""},
                {"position": "w", "name": "w", "state": "idle", "pane": "w1:p1"}]
        remote = {"t": [{"machine_id": "6df79335-aaaa", "position": "p", "state": "working", "age_s": 300,
                         "person": "ana"}]}
        of._merge_presence(rows, POS, remote=remote)
        self.assertEqual(rows[0]["state"], "remote:working")
        self.assertEqual(rows[0]["pane"], "machine 6df79335 · ana · 5m ago")
        self.assertEqual((rows[0]["machine"], rows[0]["person"], rows[0]["age_s"]), ("6df79335-aaaa", "ana", 300))
        self.assertEqual(rows[1]["state"], "idle")  # held here: untouched


if __name__ == "__main__":
    unittest.main()
