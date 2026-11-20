"""Card ids from the organization's server (of_architect, msg 01a1faa1-1fa0, choice (a); docs/registry.md, Card ids):
one counter on the server gives each number out once, a filing that cannot reach it has no id until the pulse gets one,
and an organization with no server numbers on its one machine."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_events  # noqa: E402


class Server:
    """A server's counter as of_server keeps it: once per event, above every floor it is told."""

    def __init__(self, fail=False):
        self.n, self.by_event, self.fail, self.calls = 0, {}, fail, []

    def card_ref(self, org, prefix, event_id, floor, timeout=None):
        self.calls.append((org, prefix, event_id, floor))
        if self.fail:
            raise OSError("refused")
        if event_id not in self.by_event:
            self.n = max(self.n, floor) + 1
            self.by_event[event_id] = f"{prefix}-{self.n}"
        return self.by_event[event_id]


class CardRefTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name
        p = mock.patch.object(of_events, "card_prefix", lambda org: "OF")
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def test_two_machines_never_share_a_number(self):
        srv = Server()
        for _ in range(3):
            of_events.next_ref("ofb")                        # this machine numbered 3 before: the server starts above
        a = of_events.card_ref("ofb", "e1", client=srv)
        b = of_events.card_ref("ofb", "e2", client=srv)
        self.assertEqual((a, b), ("OF-4", "OF-5"))
        self.assertEqual(of_events.card_ref("ofb", "e1", client=srv), "OF-4")   # a retry gets the same number
        self.assertEqual(srv.calls[0][3], 3)                  # the floor it was told

    def test_no_answer_files_without_an_id_and_the_pulse_gets_it(self):
        self.assertIsNone(of_events.card_ref("ofb", "e1", client=Server(fail=True)))
        srv, emitted = Server(), []
        asks = [{"event_id": "e1", "state": "open", "ts": "1", "org": "ofb", "host_id": "h"},
                {"event_id": "e2", "state": "open", "ts": "2", "org": "ofb", "host_id": "other-machine"},
                {"event_id": "e3", "state": "answered", "ts": "3", "org": "ofb", "host_id": "h"},
                {"event_id": "e4", "state": "open", "ts": "4", "org": "ofb", "host_id": "h", "ref": "OF-9"}]
        with mock.patch.object(of_events, "emit", lambda kind, payload, **kw: emitted.append((kind, payload, kw))):
            n = of_events.backfill_refs(asks, here="h", log=lambda m: None,
                                        ask=lambda org, eid: of_events.card_ref(org, eid, client=srv))
        self.assertEqual(n, 1)
        kind, payload, kw = emitted[0]
        self.assertEqual((kind, payload["ask_id"], payload["ref"]), ("ask_ref", "e1", "OF-1"))
        self.assertEqual(kw["event_id"], of_events.stable_id("ask_ref", "e1"))   # recorded once, whoever records it

    def test_a_server_without_the_counter_yet_leaves_this_machine_numbering(self):
        class Old:
            def card_ref(self, *a, **k):
                e = OSError("not found")
                e.status = 404
                raise e
        self.assertEqual(of_events.card_ref("ofb", "e1", client=Old()), "OF-1")

    def test_an_organization_with_no_server_numbers_on_its_machine(self):
        with mock.patch.object(of_events, "_org_server_url", lambda org: None):
            self.assertEqual(of_events.card_ref("acme", "e1"), "OF-1")
            self.assertEqual(of_events.card_ref("acme", "e2"), "OF-2")

    def test_a_hook_never_waits_on_the_network(self):
        with mock.patch.object(of_events, "HOOK_TIMEOUT", 5), \
                mock.patch.object(of_events, "card_ref", side_effect=AssertionError("called")):
            rec = of_events.record("agent_question", {"detected_by": "declared", "questions": [{"question": "q"}]},
                                   org="ofb")
        self.assertNotIn("ref", rec["of.payload"])

    def test_the_listener_never_waits_on_the_network(self):
        """of_release on PR 496: the listener files asks inside a write of the store."""
        calls = []
        with mock.patch.object(of_events, "CARD_REFS_LATER", True), \
                mock.patch.object(of_events, "card_ref", side_effect=lambda *a, **k: calls.append(a)):
            rec = of_events.record("agent_question", {"detected_by": "declared", "questions": [{"question": "q"}]},
                                   org="ofb")
        self.assertEqual((calls, rec["of.payload"].get("ref")), ([], None))


if __name__ == "__main__":
    unittest.main()
