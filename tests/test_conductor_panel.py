"""The conductor's panel (hand-over a8ee7e96): the ledger's open requests with each thread's live state from records."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_conductor_panel as panel  # noqa: E402
import of_events  # noqa: E402
import of_store  # noqa: E402
import of_views  # noqa: E402

T0 = panel._ts("2026-11-10T20:00:00Z")
LEDGER = {"requests": [
    {"id": "R1", "title_ru": "Онбординг Acme", "asked_at": "2026-11-10T19:20Z", "state": "open",
     "threads": [{"owner": "organic-framework-build:of_onboarding-research", "via": "handover", "handover": "cfd1de58",
                  "asks": ["4abb30ac"], "state": "routed"}]},
    {"id": "R2", "title_ru": "Статус CI", "asked_at": "2026-11-10T19:28Z", "state": "closed",
     "closed_at": "2026-11-10T19:40Z", "threads": [{"owner": "acme:ci_keeper", "via": "records", "state": "done"}]},
    {"id": "R3", "title_ru": "Кэш раннеров", "asked_at": "2026-11-10T17:00Z", "state": "open",
     "threads": [{"owner": "acme:platform_owner", "via": "message", "message": "01a1-msg-x", "state": "routed"}]},
    {"id": "R0", "title_ru": "Старый", "asked_at": "2026-11-09T10:00Z", "state": "closed",
     "closed_at": "2026-11-09T12:00Z", "threads": []}]}


class PanelTest(unittest.TestCase):
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

    def ev(self, name, payload, ts, position=None, eid=None):
        rec = of_events.record(name, payload, position=position, session="S", org="ofb")
        rec["timestamp"] = ts
        if eid:
            rec["of.event.id"] = eid
        of_store.add_event(self.conn, rec)

    def test_live_states_last_movement_and_the_stuck_flag(self):
        hid = "01a1c6e9-e99b-718f-8995-8605cfd1de58"
        self.ev("handover_proposed", {"from": "of_conductor"}, "2026-11-10T19:23:00Z", eid=hid)
        self.ev("handover_accepted", {"id": hid}, "2026-11-10T19:23:30Z")
        self.ev("handover_delivered", {"id": hid, "note": "Отчёт готов"}, "2026-11-10T19:31:54Z")
        self.ev("state_declared", {"state": "waiting", "waiting_on": "founder"}, "2026-11-10T19:32:00Z",
                position="of_onboarding-research")
        cache = {"note:" + panel._key("Отчёт готов"): "Отчёт готов, его проверяет дирижёр."}
        text = panel.render(LEDGER, self.conn, now=T0, width=120, color=False, cache=cache)
        self.assertIn("Твои просьбы", text)
        self.assertIn("● Онбординг Acme", text)
        self.assertIn("● of_onboarding-research — сделал, ждёт проверки", text)  # no title cached: the position
        self.assertIn("      Отчёт готов, его проверяет дирижёр.", text)  # the plain sentence, never the raw note
        self.assertIn("последнее движение 28 мин назад", text)
        self.assertIn("СДЕЛАНО ЗА ПОСЛЕДНИЕ ЧАСЫ\n  ● Статус CI", text)  # closed within 6 hours: one line
        self.assertNotIn("Старый", text)  # closed long ago: gone
        # R3: a message nobody answered for 3 hours: stuck, first in the list, and counted in the summary
        self.assertIn("▲ platform_owner — написали, ещё не ответил", text)
        self.assertIn("ЗАСТРЯЛО", text)
        self.assertIn("● 1 застряли", text)
        self.assertLess(text.index("Кэш раннеров"), text.index("Онбординг Acme"))
        self.ev("agent_message_in", {"text": "Message from acme:platform_owner (via relay; msg y, reply to 01a1-msg-x) done"},
                "2026-11-10T19:59:00Z", position="of_conductor")
        text = panel.render(LEDGER, self.conn, now=T0, width=120, color=False)
        self.assertIn("platform_owner — ответил", text)
        self.assertNotIn("ЗАСТРЯЛО", text)

    def test_a_card_of_his_and_plain_titles(self):
        ledger = {"requests": [{"id": "R9", "title_ru": "Кэш", "asked_at": "2026-11-10T19:50Z", "state": "open",
                                "threads": [{"owner": "acme:platform_owner", "via": "message", "message": "m1",
                                             "asks": ["7df5d350"], "state": "routed"}]}]}
        self.ev("agent_question", {"detected_by": "declared", "to": "founder", "questions": [{"question": "Кэш?"}]},
                "2026-11-10T19:55:00Z", position="platform_owner", eid="01a1c700-0000-7000-8000-00007df5d350")
        positions = [{"name": "platform_owner", "summary": "platform unit owner", "contribution": "Owns the platform"}]
        calls = []

        def runner(argv, **kw):
            calls.append(argv[argv.index("-p") + 1])
            items = [{"id": i["id"], "ru": "Руководитель платформы"} for i in
                     __import__("json").loads(argv[argv.index("-p") + 1].split("\n", 1)[1])]
            return __import__("subprocess").CompletedProcess([], 0, stdout=__import__("json").dumps(
                {"structured_output": {"items": items}}))
        cache = {}
        views = [t for _, _, tv in panel.views_of(ledger, self.conn, T0) for t in tv]
        self.assertTrue(panel.fill_plain(views, positions, cache, runner=runner))
        self.assertFalse(panel.fill_plain(views, positions, cache, runner=runner))  # cached: no second call
        self.assertEqual(len(calls), 1)
        text = panel.render(ledger, self.conn, now=T0, color=False, positions=positions, cache=cache)
        self.assertIn("◆ Руководитель платформы — работает, ещё не ответил", text.replace("написали, ещё не ответил",
                                                                                        "работает, ещё не ответил"))
        self.assertIn("Ждёт твоего ответа в окне вопросов: «Кэш?»", text)  # its words, never an id he cannot find
        self.assertIn("● 1 ждут тебя", text)

    def test_the_picture_page_and_its_kitty_sequence(self):
        """The founder: "в более Apple формате". The page carries the same facts; the image goes by kitty graphics."""
        html = panel.render_html(LEDGER, self.conn, now=T0, width=900, height=1200)
        self.assertIn("Твои просьбы", html)
        self.assertIn("Онбординг Acme", html)
        self.assertIn("застряло", html)  # R3's message went unanswered for 3 hours
        self.assertIn("Сделано за последние часы", html)
        self.assertNotIn("Старый", html)
        png = os.path.join(self.tmp.name, "p.png")
        with open(png, "wb") as fh:
            fh.write(b"\x89PNG" + b"x" * 5000)
        seq = panel.kitty_image(png, 80, 40)
        self.assertTrue(seq.startswith("\033_Ga=d,d=A,q=2\033\\"))  # the previous image goes first
        self.assertIn("\033_Ga=T,f=100,t=d,c=80,r=40,q=2,m=1;", seq)  # PNG over 80x40 cells, more chunks follow
        self.assertTrue(seq.endswith("\033\\") and "m=0" in seq)

    def test_the_picture_stays_small_enough_for_herdr_to_show(self):
        """Lab, 2026-11-11: herdr re-sends a pane image as raw RGBA; a 2392x1764 render never got placed, 960 did."""
        w, h = panel.picture_size(136, 88, 1224, 1672)
        self.assertEqual(w, panel.PICTURE_MAX_W)
        self.assertLess(w * h * 4, 6_000_000)
        self.assertEqual(panel.picture_size(80, 24, 640, 432), (640, 432))  # a small pane: its own size

    def test_the_card_view_is_aligned_and_says_the_same(self):
        """The founder ~20:45Z: the picture was blurry and did not scroll; text cards are sharp and scroll."""
        lines = panel.card_lines(LEDGER, self.conn, now=T0, width=90, color=True)
        boxed = [x for x in lines if "│" in x or "╭" in x or "╰" in x]
        self.assertTrue(boxed)
        self.assertEqual({panel._vlen(x) for x in boxed}, {panel._vlen(boxed[0])})  # every card line one width
        plain = "\n".join(panel.card_lines(LEDGER, self.conn, now=T0, width=90, color=False))
        self.assertIn("Твои просьбы", plain)
        self.assertIn("Онбординг Acme", plain)
        self.assertIn("застряло, дирижёр разберётся", plain)
        self.assertIn("СДЕЛАНО ЗА ПОСЛЕДНИЕ ЧАСЫ", plain)

    def test_an_unreadable_ledger_says_so(self):
        text = panel.render(panel.load_ledger(os.path.join(self.tmp.name, "none.yaml")), self.conn, now=T0, color=False)
        self.assertIn("Не получается прочитать список просьб дирижёра", text)

    def test_a_view_places_its_panels_and_they_are_no_positions(self):
        v = {"name": "conductor", "workspace": "w", "tab": "t", "positions": ["of_conductor", "panel:requests"],
             "panels": {"requests": "of conductor panel"}}
        self.assertEqual(of_views.check(v, {"of_conductor"}), [])
        self.assertIn("panel 'extra' is not placed", "\n".join(of_views.check(
            dict(v, panels={"requests": "x", "extra": "y"}), {"of_conductor"})))
        self.assertEqual(of_views.pinned_positions([v]), {"of_conductor"})


if __name__ == "__main__":
    unittest.main()
