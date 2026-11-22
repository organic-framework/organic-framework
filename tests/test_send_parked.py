"""`of send` to a position parked on purpose and still held (docs/guard.md R11; a user organization's structure keeper, msg
01a1f0a9-ad31: messages piled up unread on parked positions that kept their sessions): the message goes, and the
sender is told who covers the work now, with the command that sends there instead. When its successor is active the
message is refused, except from that successor, its unit's owner and its structure keeper, or with --to-parked (the
same keeper, msg 01a1f3a8-b16b: the note on stderr moved no sender). `of inbox move|drop` are for the structure keeper
and the unit owner only."""

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


class ParkedRefusalTest(unittest.TestCase):
    BASE = [pos("acme_org", role="org"), pos("acme_ops"), pos("acme_lead"), pos("acme_dev")]
    WATCH = pos("acme_watch", status="parked", successor="acme_ops", system="watch", _unit="ops",
                _unit_owner="acme_lead", _units={"ops": {"owner": "acme_lead", "systems": ["watch"]}})

    def test_a_parked_position_whose_successor_is_active_is_refused_with_the_successor_named(self):
        got = of_cli._parked_refusal("acme:acme_watch", self.BASE + [self.WATCH], "acme:acme_dev")
        self.assertIn("not sent", got)
        self.assertIn('`of send acme:acme_ops "…"`', got)
        self.assertIn("--to-parked", got)

    def test_its_unit_owner_and_structure_keeper_still_reach_it(self):
        for sender in ("acme:acme_lead", "acme:acme_org"):
            with self.subTest(sender=sender):
                self.assertIsNone(of_cli._parked_refusal("acme:acme_watch", self.BASE + [self.WATCH], sender))

    def test_its_successor_is_refused_since_it_does_that_work(self):
        """A successor's own messages sat in the parked queue until they raised a backlog card (a user organization
        through its structure keeper, msg 01a2034b-a102)."""
        got = of_cli._parked_refusal("acme:acme_watch", self.BASE + [self.WATCH], "acme:acme_ops")
        self.assertIn("you, its successor, do its work now", got)
        self.assertIn("--to-parked", got)
        self.assertNotIn("of send acme:acme_ops", got)                  # never "send it to yourself"
        lead = dict(self.WATCH, successor="acme_lead")                   # a successor that is also its unit's owner
        self.assertIsNone(of_cli._parked_refusal("acme:acme_watch", self.BASE + [lead], "acme:acme_lead"))
        note = of_cli._parked_note("acme:acme_watch", self.BASE + [self.WATCH], "acme:acme_ops")
        self.assertIn("parked on purpose", note)
        self.assertNotIn("send it there instead", note)    # never "send it to yourself"

    def test_no_refusal_without_an_active_successor_or_for_an_active_receiver(self):
        gone = dict(self.WATCH, successor="acme_gone")
        self.assertIsNone(of_cli._parked_refusal("acme:acme_watch", self.BASE + [gone], "acme:acme_dev"))
        self.assertIsNotNone(of_cli._parked_note("acme:acme_watch", self.BASE + [gone], "acme:acme_dev"))
        self.assertIsNone(of_cli._parked_refusal("acme:acme_ops", self.BASE + [self.WATCH], "acme:acme_dev"))

    def test_send_refuses_before_any_server_unless_to_parked(self):
        import argparse
        args = argparse.Namespace(to="acme:acme_watch", text="x", sender="acme:acme_dev", reply_to=None, grant=None,
                                  fyi=False, urgent=False, to_parked=False)
        saved = of_cli._load_quiet
        of_cli._load_quiet = lambda: self.BASE + [self.WATCH]
        try:
            with self.assertRaises(of_cli.OfError) as cm:
                of_cli.cmd_send(args)
            self.assertIn("its successor", str(cm.exception))
        finally:
            of_cli._load_quiet = saved

    def test_to_parked_passes_the_refusal(self):
        """of_review on PR 375: only the refusal's text was tested. With --to-parked the send goes on to its server
        (stopped here at the first step after the refusal)."""
        import argparse
        import of_ship

        class Reached(Exception):
            pass

        def stop():
            raise Reached()
        args = argparse.Namespace(to="acme:acme_watch", text="x", sender="acme:acme_dev", reply_to=None, grant=None,
                                  fyi=False, urgent=False, to_parked=True)
        saved, saved_cfg = of_cli._load_quiet, of_ship.config
        of_cli._load_quiet, of_ship.config = (lambda: self.BASE + [self.WATCH]), stop
        try:
            with self.assertRaises(Reached):
                of_cli.cmd_send(args)
        finally:
            of_cli._load_quiet, of_ship.config = saved, saved_cfg

    def test_only_the_structure_keeper_or_the_unit_owner_manages_a_queue(self):
        ps = self.BASE + [self.WATCH, dict(pos("zeta_org", role="org"), _org="zeta")]
        self.assertIsNone(of_cli._queue_manager(ps[0], self.WATCH, ps))           # the structure keeper
        self.assertIsNone(of_cli._queue_manager(ps[2], self.WATCH, ps))           # its unit's owner
        self.assertIn("may move or drop", of_cli._queue_manager(ps[3], self.WATCH, ps))
        self.assertIn("not a position of acme", of_cli._queue_manager(ps[-1], self.WATCH, ps))

    def _inbox(self, action, sid, **kw):
        import argparse
        args = argparse.Namespace(action=action, id="acme:acme_watch", to=None, msg=None, dry_run=False, reason=None,
                                  sender=None, mine=False, take=None, taken=False, all=False)
        for k, v in kw.items():
            setattr(args, k, v)
        saved, env = of_cli._load_quiet, os.environ.get("CLAUDE_CODE_SESSION_ID")
        of_cli._load_quiet = lambda: self.BASE + [self.WATCH]
        os.environ["CLAUDE_CODE_SESSION_ID"] = sid
        try:
            with self.assertRaises(of_cli.OfError) as cm:
                of_cli.cmd_inbox(args)
            return str(cm.exception)
        finally:
            of_cli._load_quiet = saved
            if env is None:
                os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            else:
                os.environ["CLAUDE_CODE_SESSION_ID"] = env

    def test_drop_checks_the_role_and_never_takes_a_dry_run(self):
        """of_secrets-review on PR 375: the client's drop checked no role, and drop --dry-run dropped."""
        self.assertIn("may move or drop", self._inbox("drop", "S-acme_dev", msg=["m1"], reason="stale"))
        self.assertIn("--dry-run is for move", self._inbox("drop", "S-acme_org", msg=["m1"], reason="stale",
                                                           dry_run=True))
        self.assertIn("of inbox drop <position> --msg ID", self._inbox("drop", "S-acme_org", reason="stale"))
        self.assertIn("may move or drop", self._inbox("move", "S-acme_dev", to="acme:acme_ops"))

    def test_a_persons_queue_is_refused_before_any_server(self):
        desk = pos("acme_desk", holder={"person": "alex"}, _units={})
        saved = self.BASE
        self.BASE = saved + [desk]
        try:
            got = self._inbox("drop", "S-acme_org", id="acme:acme_desk", msg=["m1"], reason="stale")
        finally:
            self.BASE = saved
        self.assertIn("a person holds acme:acme_desk", got)

if __name__ == "__main__":
    unittest.main()
