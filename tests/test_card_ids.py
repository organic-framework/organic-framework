"""Card ids (the founder, prompt 01a1ea59-aeb7, via of_conductor, 2026-11-17: "add an ID to the cards, like in
Linear, show them in the sidebar and the popup, and refer to them in chat by it, not by the hash"): stamped once when
an ask is filed, raised from other machines' asks, resolved by every `of asks` command."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_asks  # noqa: E402
import of_events  # noqa: E402
import of_store  # noqa: E402


class CardIdTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name
        of_events._CONN.clear()
        self.conn = of_store.connect(os.path.join(self.tmp.name, "of.db"))

    def tearDown(self):
        self.conn.close()
        of_events._CONN.clear()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def ask(self, org, text="Ship it?", **kw):
        rec = of_events.record("agent_question", {"detected_by": "declared", "questions": [{"question": text}]},
                               position="p", session="S", org=org, **kw)
        of_store.add_event(self.conn, rec)
        return rec

    def test_stamped_once_per_organization_in_order(self):
        a, b, c = self.ask("acme-shop"), self.ask("acme-shop"), self.ask("organic-framework-build")
        self.assertEqual([r["of.payload"]["ref"] for r in (a, b, c)], ["AS-1", "AS-2", "OFB-1"])
        imported = of_events.record("agent_question", {"questions": []}, org="acme-shop", source="import")
        self.assertNotIn("ref", imported["of.payload"])          # history imported from transcripts: no card
        candidate = of_events.record("agent_question", {"detected_by": "heuristic", "questions": []}, org="acme-shop")
        side = of_events.record("agent_question", {"subagent": True, "questions": []}, org="acme-shop")
        self.assertNotIn("ref", side["of.payload"])              # a subagent's question is no card either
        self.assertNotIn("ref", candidate["of.payload"])         # no card until the judge files it (of_asks, PR 300)
        given = of_events.record("agent_question", {"ref": "AS-77"}, org="acme-shop")
        self.assertEqual(given["of.payload"]["ref"], "AS-77")    # a writer's own id is kept
        self.assertEqual(self.ask("acme-shop")["of.payload"]["ref"], "AS-3")

    def test_a_default_prefix_keeps_to_two_to_five_letters(self):
        self.assertEqual([of_events.default_prefix(o) for o in ("acme-shop", "acme", "a-b-c-d-e-f", "organic-framework-build")],
                         ["AS", "ACM", "ABCDE", "OFB"])

    def test_another_machines_number_raises_the_counter(self):
        self.ask("acme-shop")
        far = of_events.record("agent_question", {"ref": "AS-40", "questions": []}, org="acme-shop")
        far["host.id"] = "another-machine"
        of_store.add_event(self.conn, far)
        self.assertEqual(self.ask("acme-shop")["of.payload"]["ref"], "AS-41")

    def test_resolved_any_case_and_carried_by_the_projection(self):
        a = self.ask("acme-shop")
        self.assertEqual(of_asks.ref_id(self.conn, "as-1"), a["of.event.id"])
        self.assertIsNone(of_asks.ref_id(self.conn, "AS-9"))
        self.assertIsNone(of_asks.ref_id(self.conn, "1a2b3c4d"))
        # an ask filed before ids existed gets one by an of.ask_ref event
        old = of_events.record("agent_question", {"ref": None, "detected_by": "declared",
                                                  "questions": [{"question": "Old?"}]}, org="acme-shop")
        old["of.payload"].pop("ref", None)
        of_store.add_event(self.conn, old)
        of_events.append(of_events.record("ask_ref", {"ask_id": old["of.event.id"], "ref": "AS-5"}, org="acme-shop"))
        self.assertEqual(of_asks.ref_id(self.conn, "AS-5"), old["of.event.id"])
        rows = of_asks.load(self.conn)
        refs = {x["event_id"]: x.get("ref") for x in of_asks.project(rows)}
        self.assertEqual((refs[a["of.event.id"]], refs[old["of.event.id"]]), ("AS-1", "AS-5"))

    def test_two_machines_alike_the_later_is_suffixed(self):
        a = self.ask("acme-shop")
        twin = of_events.record("agent_question", {"ref": "AS-1", "questions": []}, org="acme-shop")
        twin["of.event.id"] = "ffffffff-" + a["of.event.id"][9:]
        of_store.add_event(self.conn, twin)
        first, later = sorted([a["of.event.id"], twin["of.event.id"]])
        self.assertEqual(of_asks.ref_id(self.conn, "AS-1"), first)
        self.assertEqual(of_asks.ref_id(self.conn, "AS-1b"), later)
        shown = {x["event_id"]: x["ref"] for x in of_asks.project(of_asks.load(self.conn))}
        self.assertEqual((shown[first], shown[later]), ("AS-1", "AS-1b"))   # shown as resolved (of_review, PR 300)
        self.assertEqual((of_asks.card_name(self.conn, first), of_asks.card_name(self.conn, later)), ("AS-1", "AS-1b"))

    def test_a_chat_answer_names_the_card_by_its_id(self):
        """of_review on PR 300: the chat shows only the card id, and an answer citing it was refused."""
        import of_asks_chat
        self.assertEqual(of_asks_chat.names_card("AS-12: yes, ship it", ["01a1aaaa-bbbb-cccc"], ["p"], refs=["AS-12"]),
                         "its card id AS-12")
        self.assertIsNone(of_asks_chat.names_card("AS-120: yes", ["01a1aaaa-bbbb-cccc"], ["q"], refs=["AS-12"]))
        self.assertEqual(of_asks_chat.names_card("as-12b: yes", ["01a1aaaa"], ["q"], refs=["AS-12b"]),
                         "its card id AS-12b")


if __name__ == "__main__":
    unittest.main()
