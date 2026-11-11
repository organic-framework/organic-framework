"""of_net.online (a drop of the mobile link must not spend the judge's tries; founder, 2026-11-11)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import of_net  # noqa: E402


class OnlineTest(unittest.TestCase):
    def setUp(self):
        of_net._last.clear()

    def test_a_refused_or_timed_out_connect_is_offline_and_cached(self):
        calls = []

        class Sock:
            def close(self):
                pass

        def up(addr, timeout):
            calls.append(addr)
            return Sock()

        def down(addr, timeout):
            calls.append(addr)
            raise OSError("no route")
        self.assertFalse(of_net.online(connect=down, now=100))
        self.assertFalse(of_net.online(connect=up, now=130))  # cached within a minute
        self.assertTrue(of_net.online(connect=up, now=200))   # asked again after it
        self.assertEqual(len(calls), 2)


class JudgeOfflineTest(unittest.TestCase):
    def test_no_try_is_spent_while_the_api_is_unreachable(self):
        import of_judge
        started = []
        saved = of_judge.pending_count
        of_judge.pending_count = lambda: 1  # a candidate waits for the judge
        try:
            n, _ = of_judge.pulse_pass(None, [], [], None, None, log=lambda m: None,
                                       start=lambda a: started.append(a) or True, online=lambda: False)
            self.assertEqual((n, started), (0, []))
            n, _ = of_judge.pulse_pass(None, [], [], None, None, log=lambda m: None,
                                       start=lambda a: started.append(a) or True, online=lambda: True)
            self.assertEqual(n, 1)
        finally:
            of_judge.pending_count = saved


if __name__ == "__main__":
    unittest.main()
