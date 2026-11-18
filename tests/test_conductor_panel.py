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

import of_locale  # noqa: E402

def setUpModule():
    of_locale.set_language("ru")  # these tests read the founder's panel, in Russian; LanguageTest covers English

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
        cache = {"note:" + panel._key("ru", "Отчёт готов"): "Отчёт готов, его проверяет дирижёр."}
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

    def test_of_view_add_takes_a_panel_and_the_questions_one_needs_no_command(self):
        import shlex
        name, cmd = of_views.panel_spec("questions", "/py dir/python3", "/of/bin")
        self.assertEqual(name, "questions")
        self.assertEqual(shlex.split(cmd), ["env", "OF_AUTOMATION=1", "/py dir/python3", "/of/bin/of_herdr_plugin.py",
                                            "keep", "--name", "the questions panel", "--", "/py dir/python3",
                                            "/of/bin/of", "asks", "--panel"])
        # no command of one's own: the listener would run it every minute as the person (of_review on PR 345)
        for bad in ("requests", "requests = of conductor panel", "questions=touch x", "status=touch x", ""):
            with self.assertRaises(ValueError):
                of_views.panel_spec(bad, "/p", "/b")
        v = {"name": "c", "workspace": "w", "tab": "t", "positions": ["of_conductor", "panel:questions"],
             "panels": {"questions": cmd}}
        self.assertEqual(of_views.check(v, {"of_conductor"}), [])

    def test_a_view_labels_its_first_agents_own_tab_rather_than_move_it(self):
        """of init pins the conductor's view before of spawn opens the conductor in a tab named by its system: the
        tab is relabelled, and the conductor is not moved out of the tab the person watches (lab, 2026-11-18)."""
        tabs = [{"tab_id": "w1:t1", "workspace_id": "w1", "label": "acme"},
                {"tab_id": "w1:t2", "workspace_id": "w1", "label": "lead"},
                {"tab_id": "w2:t1", "workspace_id": "w2", "label": "acme"}]
        alone = {"w1:p1": "w1:t1", "w1:p2": "w1:t1", "w1:p3": "w1:t2"}
        self.assertEqual(of_views.adoptable_tab(tabs, alone, ["w1:p1", "w1:p2"], "w1")["tab_id"], "w1:t1")
        self.assertEqual(of_views.adoptable_tab(tabs, alone, ["w1:p1"], "w1"), None)  # w1:p2 is not the view's
        self.assertEqual(of_views.adoptable_tab(tabs, alone, ["w1:p1", "w1:p2"], "w2"), None)  # another workspace
        self.assertEqual(of_views.adoptable_tab(tabs, alone, [], "w1"), None)
        self.assertEqual(of_views.split_ratio({"layout": "grid"}), "0.5")
        self.assertEqual(of_views.split_ratio({}), "0.6")

    def test_view_apply_runs_only_built_in_panels_never_a_stored_command(self):
        """views.yaml is written by anyone and applied each minute as the person (of_review on PR 345; of_server,
        msg 01a1efce-da1d): a built-in panel runs the code's command, another name runs nothing and is named."""
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("of_cli_panels", os.path.join(HERE, "..", "bin", "of"))
        of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_panels", loader))
        loader.exec_module(of)
        ran, said, splits = [], [], iter(["w1:p2", "w1:p3"])
        of.herdr_json = lambda *a: {"pane": {"pane_id": next(splits)}}
        of.herdr_do = lambda args, dry: ran.append(args) or True
        v = {"layout": "main-left-60", "panels": {"questions": "touch /tmp/owned", "evil": "touch /tmp/owned"}}
        out = of._panel_panes(v, [], False, said.append, anchor="w1:p1")
        self.assertEqual(out, {"questions": "w1:p2"})
        cmd = next(a[3] for a in ran if a[:2] == ["pane", "run"])
        self.assertIn("asks --panel", cmd)
        self.assertNotIn("/tmp/owned", " ".join(" ".join(a) for a in ran))
        self.assertTrue(any("panel evil: not a built-in panel" in m for m in said))

    def test_a_view_of_panels_alone_starts_its_panel_in_its_own_tab(self):
        """A machine that holds no position (the owner's laptop under contours) has no agent to split (hand-over
        830275c0): the panel opens in the view's own tab, its workspace made when missing; a view that names an agent
        with none live waits for it, and a dry run starts nothing."""
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("of_cli_alone", os.path.join(HERE, "..", "bin", "of"))
        of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_alone", loader))
        loader.exec_module(of)
        state = {"workspaces": [{"workspace_id": "w1", "label": "acme"}], "tabs": [], "panes": []}
        calls, ran, said = [], [], []

        def herdr_json(*a):
            calls.append(list(a))
            if a[:2] == ("workspace", "create"):
                state["panes"].append({"pane_id": "w2:p1", "tab_id": "w2:t1"})
                return {"type": "workspace_created", "workspace": {"workspace_id": "w2", "label": a[3]},
                        "tab": {"tab_id": "w2:t1", "label": "1"}, "root_pane": {"pane_id": "w2:p1"}}
            if a[0] in ("workspace", "tab", "pane") and a[1] == "list":
                return {a[0] + "s": state[a[0] + "s"]}
            raise AssertionError(a)
        of.herdr_json = herdr_json
        of.herdr_do = lambda args, dry: ran.append(args) or True
        v = {"name": "questions", "workspace": "questions", "tab": "questions", "layout": "main-left-60",
             "positions": ["panel:questions"], "panels": {"questions": "ignored"}}
        self.assertEqual(of._panel_panes(dict(v), [], True, said.append), {})
        self.assertEqual(ran, [])
        self.assertTrue(any("would start it: herdr workspace create --label questions" in m for m in said))
        out = of._panel_panes(dict(v), [], False, said.append)
        self.assertEqual(out, {"questions": "w2:p1"})
        create = next(c for c in calls if c[:2] == ["workspace", "create"])
        self.assertIn("--no-focus", create)
        self.assertIn(["tab", "rename", "w2:t1", "questions"], ran)
        self.assertIn("asks --panel", next(a[3] for a in ran if a[:2] == ["pane", "run"]))
        self.assertIn(["pane", "rename", "w2:p1", "▤ questions"], ran)
        ran.clear()
        waits = dict(v, positions=["acme_conductor", "panel:questions"])
        self.assertEqual(of._panel_panes(waits, [], False, said.append), {})
        self.assertEqual(ran, [])

    def test_a_panel_is_held_by_one_view_per_machine(self):
        """Its pane is found by its label, one per machine: two views holding it would take it from each other
        (of_onboarding, msg 01a1efbe-b153), so of view add --panel refuses a second one; the same view may be added
        again."""
        views = [{"name": "conductor", "positions": ["of_conductor", "panel:questions"]},
                 {"name": "core", "positions": ["of_architect"]}]
        self.assertEqual(of_views.panel_held_elsewhere(views, "questions", "acme"), "conductor")
        self.assertEqual(of_views.panel_held_elsewhere(views, "questions", "conductor"), None)
        self.assertEqual(of_views.panel_held_elsewhere(views, "requests", "acme"), None)


class LanguageTest(unittest.TestCase):
    """of_conductor and of_onboarding, 2026-11-11: a new organization's panel follows owner.yaml (English by default)
    and reads the ledger's `title` (the conductor brief no longer writes title_ru)."""

    def tearDown(self):
        of_locale.set_language("ru")

    def test_english_by_default_and_the_title_field(self):
        of_locale.set_language("en")
        self.assertEqual(panel.ago(None, 0), "—")
        self.assertEqual(panel.ago(100, 100 + 600), "10 min ago")
        self.assertEqual(panel._title({"title": "Fix the build"}), "Fix the build")
        self.assertEqual(panel._title({"title_ru": "Старое"}), "Старое")
        self.assertEqual(panel._title({}), "Untitled")
        of_locale.set_language("ru")
        self.assertEqual(panel._title({}), "Без названия")
        self.assertEqual(panel.ago(100, 100 + 600), "10 мин назад")


if __name__ == "__main__":
    unittest.main()


def tearDownModule():
    of_locale.set_language(None)  # the next modules read the setting again
