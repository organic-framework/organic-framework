"""The owner's machine joined to two organizations' servers (docs/contours.md §3, the questions panel row): one panel
shows both organizations' cards, read from each organization's coordination feed; what the owner does on a card in
the panel leaves his machine only for that card's organization's server, and reaches the asker on the machine that
holds it through that organization's feed. The feed is simulated as in tests/test_several_machines.py: what one
machine ships, the other stores with raw_ref coord:."""

import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_asks  # noqa: E402
import of_asks_answer  # noqa: E402
import of_asks_tui  # noqa: E402
import of_coord  # noqa: E402
import of_events  # noqa: E402
import of_ship  # noqa: E402
import of_store  # noqa: E402

OF_URL, ACME_URL = "https://127.0.0.1:18200", "https://127.0.0.1:18300"
ASK_OF, ASK_ACME, ASK_ACME2 = ("01a1f100-0000-7000-8000-00000000000a", "01a1f100-0000-7000-8000-00000000000b",
                           "01a1f100-0000-7000-8000-00000000000c")


def ask_record(eid, org, position, text, ref):
    """A declared ask as `of ask` records it (bin/of, cmd_ask), filed on the agent host."""
    return {"of.schema": 1, "of.event.id": eid, "timestamp": "2026-11-19T10:00:00.000Z",
            "event.name": "of.agent_question", "host.id": "the-host", "of.org": org, "gen_ai.agent.name": position,
            "gen_ai.conversation.id": f"S-{position}", "of.source": "of",
            "of.payload": {"ref": ref, "detected_by": "declared", "source": "declared", "from_position": position,
                           "to": "founder", "questions": [{"question": text, "options": ["yes", "no"]}]}}


class Machine:
    """A machine's store and its orgs.yaml. `enrolled`: as `of enroll` leaves the owner's machine for two
    organizations, each named with its server and no database, ship.yaml's server the one enrolled last."""

    def __init__(self, root, name):
        self.root = os.path.join(root, name)
        os.makedirs(self.root)
        self.orgs = os.path.join(self.root, "orgs.yaml")
        with open(self.orgs, "w") as fh:
            fh.write(f"orgs:\n  - {{name: ofb, server: '{OF_URL}', raw: false}}\n"
                     f"  - {{name: acme, server: '{ACME_URL}', raw: false}}\n")
        self.cfg = dict(of_ship.DEFAULTS, server_url=OF_URL)

    def __enter__(self):
        self.saved = of_events.STATE_DIR, os.environ.get("OF_ORGS")
        of_events.STATE_DIR = os.path.join(self.root, "state")
        os.environ["OF_ORGS"] = self.orgs
        of_events._CONN.clear()
        self.conn = of_store.connect()
        return self

    def __exit__(self, *a):
        self.conn.close()
        of_events._CONN.clear()
        of_events.STATE_DIR = self.saved[0]
        if self.saved[1] is None:
            os.environ.pop("OF_ORGS", None)
        else:
            os.environ["OF_ORGS"] = self.saved[1]

    def from_feed(self, rec, org, seq):
        """What the relay's pull_coord stores from an organization's feed."""
        of_store.add_event(self.conn, rec, raw_ref=f"{of_coord.RAW_REF}{org}:{seq}")

    def wire(self):
        """{server: [record]}: where `of ship` sends this machine's own new events (of_ship.plan_wire)."""
        router = of_ship.Router(self.cfg, self.orgs)
        router.org_by_session, router.own_org_by_session = {}, {}
        servers = of_ship.org_servers(self.cfg, self.orgs)
        conn = of_store.connect()
        conn.row_factory = sqlite3.Row  # as of_ship.ship reads the store
        batches = list(of_ship._batches(conn, self.cfg, "the-laptop"))
        conn.close()
        out = {}
        for table, _, batch in batches:
            if table == "event":
                for server, records in of_ship.plan_wire(table, batch, router, servers, self.cfg["server_url"]).items():
                    out.setdefault(server, []).extend(x["data"] for x in records)
        return out


def names(records):
    return sorted((d["name"], json.loads(d["payload"]).get("ask_ids", [None])[0], d["org_id"]) for d in records)


class OnePanelTwoOrganizationsTest(unittest.TestCase):
    def test_the_panel_shows_both_organizations_cards_each_by_its_own_card_id(self):
        with tempfile.TemporaryDirectory() as root, Machine(root, "laptop") as m:
            m.from_feed(ask_record(ASK_OF, "ofb", "of_server", "Ship the relay fix?", "OF-7"), "ofb", 1)
            m.from_feed(ask_record(ASK_ACME, "acme", "acme_ops", "Roll back the release?", "ACM-3"), "acme", 1)
            groups = dict(of_asks_tui.load([], m.conn, from_log=True))
        shown = [(of_asks_tui.card_ref(x), x["text"]) for x in groups["Waiting on you"]]
        self.assertEqual(sorted(shown), [("ACM-3", "Roll back the release?"), ("OF-7", "Ship the relay fix?")])

    def test_what_the_owner_does_on_a_card_ships_only_to_its_organizations_server(self):
        with tempfile.TemporaryDirectory() as root, Machine(root, "laptop") as m:
            m.from_feed(ask_record(ASK_OF, "ofb", "of_server", "Ship the relay fix?", "OF-7"), "ofb", 1)
            m.from_feed(ask_record(ASK_ACME, "acme", "acme_ops", "Roll back the release?", "ACM-3"), "acme", 1)
            m.from_feed(ask_record(ASK_ACME2, "acme", "acme_ops", "Shall I go on?", "ACM-4"), "acme", 2)
            self.assertEqual(m.wire(), {}, "the cards from the feeds never ship back")
            # the panel's own records (of_asks_answer, of_asks_inbox): an answer, a label, a card handed over
            of_events.emit("agent_question_answered", {"ask_id": ASK_ACME, "ask_ids": [ASK_ACME], "answer": "yes",
                                                       "via": "asks-ui"})
            of_events.emit("question_labelled", {"ask_id": ASK_ACME2, "ask_ids": [ASK_ACME2],
                                                 "label": "not_a_question", "via": "asks-ui"})
            of_events.emit("ask_handed", {"ask_id": ASK_ACME2, "ask_ids": [ASK_ACME2], "to": "acme_org", "by": "owner"})
            of_events.emit("agent_question_answered", {"ask_id": ASK_OF, "ask_ids": [ASK_OF], "answer": "no",
                                                       "via": "asks-ui"})
            wire = m.wire()
        self.assertEqual(sorted(wire), [OF_URL, ACME_URL])
        self.assertEqual(names(wire[ACME_URL]), [("of.agent_question_answered", ASK_ACME, "acme"),
                                               ("of.ask_handed", ASK_ACME2, "acme"),
                                               ("of.question_labelled", ASK_ACME2, "acme")])
        self.assertEqual(names(wire[OF_URL]), [("of.agent_question_answered", ASK_OF, "ofb")])
        self.assertTrue(all(of_coord.shared(d["name"]) for d in wire[ACME_URL] + wire[OF_URL]),
                        "each reaches the asker's machine only through its organization's feed")

    def test_the_answer_and_the_hand_over_reach_the_panes_on_the_machine_that_holds_them(self):
        with tempfile.TemporaryDirectory() as root:
            with Machine(root, "laptop") as m:
                m.from_feed(ask_record(ASK_ACME, "acme", "acme_ops", "Roll back the release?", "ACM-3"), "acme", 1)
                m.from_feed(ask_record(ASK_ACME2, "acme", "acme_ops", "Shall I go on?", "ACM-4"), "acme", 2)
                of_events.emit("agent_question_answered", {"ask_id": ASK_ACME, "ask_ids": [ASK_ACME], "answer": "yes",
                                                           "via": "asks-ui"})
                of_events.emit("ask_handed", {"ask_id": ASK_ACME2, "ask_ids": [ASK_ACME2], "to": "acme_org", "by": "owner"})
                shipped = m.wire()[ACME_URL]
            with Machine(root, "host") as h:  # the other organization's contour: its own asks, the laptop's records from its feed
                for rec in (ask_record(ASK_ACME, "acme", "acme_ops", "Roll back the release?", "ACM-3"),
                            ask_record(ASK_ACME2, "acme", "acme_ops", "Shall I go on?", "ACM-4")):
                    of_store.add_event(h.conn, rec, raw_ref="r")
                feed = [d for d in shipped if of_coord.shared(d["name"])]  # what the server copies (of_server_coord)
                for seq, d in enumerate(feed, 3):
                    h.from_feed(json.loads(d["record"]), "acme", seq)
                asks = of_asks.project(of_asks.load(h.conn))
                positions = [{"name": n, "status": "active", "_org": "acme", "_orgid": "acme"} for n in ("acme_ops", "acme_org")]
                todo = of_asks_answer.pending(h.conn, asks, positions, min_age=0)
        self.assertEqual(sorted((t["position"], t["key"].split(":")[0]) for t in todo),
                         [("acme_ops", "answer"), ("acme_org", "handed")])


class ShippedAtOnceTest(unittest.TestCase):
    """On the owner's machine no agent's hook starts the ingest worker that ships, so the panel ships what he did."""

    def test_an_answer_and_a_label_start_one_shipper_each_and_a_repeat_none(self):
        with tempfile.TemporaryDirectory() as root, Machine(root, "laptop") as m:
            of_store.add_event(m.conn, ask_record(ASK_OF, "ofb", "of_server", "Ship the relay fix?", "OF-7"), "r")
            item = {"ids": [ASK_OF], "first": {"event_id": ASK_OF}, "who": ["of_server"], "kind": "question"}
            started, saved = [], of_asks_answer.ship_soon
            of_asks_answer.ship_soon = lambda: started.append(1)
            try:
                ok, _ = of_asks_answer.answer(m.conn, item, "yes", [], lambda name: None)
                self.assertTrue(ok)
                self.assertEqual(len(started), 1)
                of_asks_answer.answer(m.conn, item, "yes", [], lambda name: None)  # recorded already
                self.assertEqual(len(started), 1)
                of_asks_answer.not_a_question({"ids": [ASK_OF], "text": "Ship the relay fix?"})
                self.assertEqual(len(started), 2)
            finally:
                of_asks_answer.ship_soon = saved

    def test_the_shipper_is_a_detached_child_and_none_under_no_detach(self):
        started, saved, saved_env = [], subprocess.Popen, dict(os.environ)

        class Child:
            def __init__(self, argv, **kw):
                started.append((argv, kw))
        subprocess.Popen = Child
        try:
            of_asks_answer.ship_soon()  # tests/isolate.py: OF_NO_DETACH=1
            self.assertEqual(started, [])
            os.environ["OF_NO_DETACH"] = ""
            of_asks_answer.ship_soon()
        finally:
            subprocess.Popen = saved
            os.environ.clear()
            os.environ.update(saved_env)
        argv, kw = started[0]
        self.assertEqual(argv[1:], [os.path.abspath(of_asks_answer.__file__), "--ship"])
        self.assertTrue(kw["start_new_session"])
        self.assertEqual((kw["env"]["OF_AUTOMATION"], kw["env"]["SSH_AUTH_SOCK"]), ("1", ""))

    def test_the_child_waits_for_a_running_shipper_then_ships(self):
        with tempfile.TemporaryDirectory() as root, Machine(root, "laptop"):
            lock = open(os.open(os.path.join(of_events.STATE_DIR, "ship.lock"), os.O_WRONLY | os.O_CREAT, 0o600))
            fcntl.flock(lock, fcntl.LOCK_EX)  # another shipper, which may have read its batches already
            shipped, saved = [], of_ship.ship
            of_ship.ship = lambda log=print, cfg=None: shipped.append(time.time()) or 0
            try:
                child = threading.Thread(target=of_asks_answer._ship_now)
                child.start()
                time.sleep(0.3)
                self.assertEqual(shipped, [])
                released = time.time()
                lock.close()
                child.join(5)
            finally:
                of_ship.ship = saved
        self.assertEqual(len(shipped), 1)
        self.assertGreaterEqual(shipped[0], released)


if __name__ == "__main__":
    unittest.main()
