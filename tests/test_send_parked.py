"""`of send` to a position parked on purpose and still held (docs/guard.md R11; a user organization's structure keeper, msg
01a1f0a9-ad31: messages piled up unread on parked positions that kept their sessions): the message goes, and the
sender is told who covers the work now, with the command that sends there instead."""

import importlib.machinery
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
_loader = importlib.machinery.SourceFileLoader("of_cli_parked", os.path.join(HERE, "..", "bin", "of"))
_spec = importlib.util.spec_from_loader("of_cli_parked", _loader)
of_cli = importlib.util.module_from_spec(_spec)
_loader.exec_module(of_cli)


def pos(name, **kw):
    return dict({"name": name, "_org": "acme", "holder": {"session": "S-" + name}}, **kw)


class ParkedNoteTest(unittest.TestCase):

    def test_a_parked_held_position_names_who_covers_it(self):
        base = [pos("acme_org", role="org"), pos("acme_ops"), pos("acme_lead")]
        got = of_cli._parked_note("acme:acme_watch", base + [pos("acme_watch", status="parked", successor="acme_ops")])
        self.assertIn("parked on purpose", got)
        self.assertIn('acme:acme_ops, its successor, covers its work now', got)
        self.assertIn('`of send acme:acme_ops "…"`', got)
        got = of_cli._parked_note("acme:acme_watch", base + [pos("acme_watch", status="parked", _unit="ops",
                                                                 _unit_owner="acme_lead")])
        self.assertIn("acme:acme_lead, the owner of its unit ops", got)
        got = of_cli._parked_note("acme:acme_watch", base + [pos("acme_watch", status="parked",
                                                                 successor="acme_gone")])  # not a live position
        self.assertIn("acme:acme_org, its organization's structure keeper", got)

    def test_an_active_receiver_gets_no_note(self):
        self.assertIsNone(of_cli._parked_note("acme:acme_ops", [pos("acme_org", role="org"), pos("acme_ops")]))
        self.assertIsNone(of_cli._parked_note("acme:nobody", [pos("acme_org", role="org")]))


if __name__ == "__main__":
    unittest.main()
