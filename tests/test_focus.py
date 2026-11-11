"""OF never moves the founder's focus (hand-over 3c7e6f17): pane moves that would are held back."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_focus  # noqa: E402
import of_views  # noqa: E402

PANES = [{"pane_id": "w3:p1H", "tab_id": "w3:t33", "focused": True},   # of_conductor, he types here
         {"pane_id": "w3:p1J", "tab_id": "w3:t33"},                    # its panel, same tab
         {"pane_id": "w3:p1G", "tab_id": "w3:t30"},                    # of_onboarding-research, alone in its tab
         {"pane_id": "w3:pF", "tab_id": "w3:t2Z"}, {"pane_id": "w3:p8", "tab_id": "w3:t2Z"}]


class FocusTest(unittest.TestCase):
    def test_the_case_of_2026_11_10(self):
        # of view apply conductor moved of_conductor's pane into a new tab: held back now
        self.assertEqual(of_focus.block(["pane", "move", "w3:p1H", "--new-tab", "--workspace", "w3", "--label",
                                         "conductor", "--no-focus"], PANES), "it is the pane the founder works in")

    def test_what_is_held_back_and_what_runs(self):
        alone = [dict(p, focused=p["pane_id"] == "w3:p1G") for p in PANES]  # he looks at a one-pane tab
        self.assertEqual(of_focus.block(["pane", "move", "w3:p1G", "--tab", "w3:t2Z", "--split", "right"], alone),
                         "it is the pane the founder works in")
        looking = [dict(p, focused=p["pane_id"] == "w3:pF") for p in PANES]
        self.assertIsNone(of_focus.block(["pane", "move", "w3:p8", "--tab", "w3:t30"], looking))  # pF stays with him
        self.assertIsNone(of_focus.block(["pane", "move", "w3:p1J", "--tab", "w3:t33", "--split", "right",
                                          "--target-pane", "w3:p1H"], PANES))  # within his own tab
        self.assertIsNone(of_focus.block(["pane", "move", "w3:p1G", "--tab", "w3:t2Z"], PANES))  # elsewhere
        self.assertIsNone(of_focus.block(["pane", "rename", "w3:p1H", "x"], PANES))  # not a move

    def test_a_view_whose_tab_exists_is_laid_out_in_it(self):
        v = {"name": "conductor", "workspace": "w", "tab": "conductor", "positions": ["of_conductor", "panel:requests"]}
        cmds = of_views.plan_moves(v, ["w3:p1H", "w3:p1J"], workspace_id="w3", in_tab=True)
        self.assertFalse(any("--new-tab" in c or "--new-workspace" in c for c in cmds))
        self.assertEqual(cmds, [["pane", "move", "w3:p1J", "--tab", "{TAB}", "--split", "right", "--target-pane",
                                 "w3:p1H", "--ratio", "0.6", "--no-focus"]])
        self.assertIn("--new-tab", of_views.plan_moves(v, ["w3:p1H", "w3:p1J"], workspace_id="w3")[0])


if __name__ == "__main__":
    unittest.main()
