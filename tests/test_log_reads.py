"""No new read of the whole event log (the founder's goal of 2026-11-14, prompt 01a1d68e-1d3e; organic-framework-build
reports/of-log-readers-2026-11-14.md, 3.3, accepted by of_architect).

A static scan of bin/ finds every read of the `event` table and every call of a loader over it, and calls one unbounded
when it has no cursor (`rowid >`), no time window (`ts >=`), no LIMIT and no single id, session or position. The ones
left today are listed below by file, function and the start of their query, each with its reason. A new unbounded
read fails this test: give it a bound, or a view (3.1, 3.2), or add it here with its reason and a reviewer's yes. A
listed read that is gone fails it too, so the list only shrinks.
"""

import ast
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, "..", "bin")
BOUND = (r"rowid\s*>", r"\bts\s*>=?\s*\?", r"\bts\s*>", r"\bLIMIT\b", r"event_id\s*(=|IN)", r"session_id\s*=",
         r"position\s*=\s*\?")
LOADERS = {"of_asks.load": "since_iso", "of_model.load_events": "since", "of_handover.load": None, "load_events": "since"}

# The reasons, by kind (section 2 of the report):
ASKS = "the asks projection where no listener view exists yet (current_asks returns no view): the one case left"
HANDOVER = "hand-overs (769 events) by the name index since f91fae4: milliseconds; a view when they grow"
RARE = "one rare name by the name index (kind B): a small view in step 4 of the order"
CONST = "a constant read: the last rowid"
FALLBACK = ("a trust decision (a diff's approval) reads the log, never a view anyone of this user may write: one "
            "name by the event_name_ts index (of_server on PR 154)")
BYID = "one event by its rowid"
OFFLINE = "an offline command (of db stats)"
STUDIO = "of_studio's readers: its fold views (its steps 2 and 3, on the shared contract)"
CAPACITY = "of_capacity's readers: cursor views like a021f2f, or small names by the index"
GITHUB = "the GitHub import, a timer every 30 minutes: by organization and name (kind D)"

ALLOWED = {
    ('of', '_diff_answer', "SELECT payload FROM event WHERE name = 'of.agent_question_an"): FALLBACK,
    ('of', 'cmd_handover', 'of_handover.load(conn)'): HANDOVER,
    ('of', 'cmd_pulse', 'of_handover.load(conn)'): HANDOVER,
    ('of', '_asks_now', 'of_asks.load(conn or of_store.connect())'): ASKS,
    ('of', 'cmd_loop', "SELECT name, ts, payload FROM event WHERE name IN ('of.loop_"): RARE,
    ('of', '_diff_events', 'SELECT ts, name, payload, event_id FROM event WHERE name IN '): RARE,
    ('of', 'cmd_registry', "SELECT name, org, payload FROM event WHERE name IN ('of.gran"): RARE,
    ('of', 'cmd_listen', 'SELECT max(rowid) FROM event'): CONST,
    ('of_ask_text.py', 'open_asks', 'of_asks.load(conn)'): ASKS,
    ('of_asks_chat.py', 'recorded', "SELECT payload FROM event WHERE name = 'of.agent_question_an"): RARE,
    ('of_asks_live.py', 'run', 'of_asks.load(conn)'): ASKS,
    ('of_asks_live.py', 'run', "SELECT event_id FROM event WHERE name = 'of.agent_question' "): RARE,
    ('of_asks_tui.py', 'load', 'of_asks.load(conn)'): ASKS,
    ('of_capacity.py', '_session_rows', "SELECT ts, payload FROM event WHERE name = 'of.holder_change"): CAPACITY,
    ('of_capacity.py', 'creds', "SELECT name, payload FROM event WHERE name IN ('of.credentia"): CAPACITY,
    ('of_conductor_panel.py', 'open_cards', 'of_asks.load(conn)'): ASKS,
    ('of_conductor_panel.py', 'card_titles', 'of_asks.load(conn)'): ASKS,
    ('of_corrections.py', 'due_digests', 'SELECT COALESCE(MAX(rowid), 0) FROM event'): CONST,
    ('of_github.py', 'pr_links', 'SELECT position, org, session_id, payload FROM event WHERE n'): GITHUB,
    ('of_github.py', 'rematch', 'SELECT event_id, payload FROM event WHERE org = ? AND name I'): GITHUB,
    ('of_github.py', 'last_merged', "SELECT max(ts) FROM event WHERE name = 'of.work_record' AND "): GITHUB,
    ('of_github.py', 'last_local', "SELECT max(ts) FROM event WHERE name = 'of.work_record' AND "): GITHUB,
    ('of_handover.py', 'load', 'SELECT event_id, ts, name, position, payload FROM event WHER'): HANDOVER,
    ('of_hook.py', '_mandate_check', 'of_handover.load(of_events._conn())'): HANDOVER,
    ('of_listen.py', 'flush', 'of_handover.load(conn)'): HANDOVER,  # on a hand-over event only: the hook's copy
    ('of_judge.py', 'judged', "SELECT payload FROM event WHERE name = 'of.agent_question_ju"): RARE,
    ('of_judge.py', 'noted', "SELECT count(*) FROM event WHERE name = 'of.nudge_sent' AND "): RARE,
    ('of_listen.py', 'open_asks', "SELECT payload FROM event WHERE name = 'of.agent_question_an"): RARE,
    ('of_listen.py', 'open_asks', "SELECT event_id, payload FROM event WHERE name = 'of.agent_q"): RARE,
    ('of_listen.py', 'load_state', 'of_asks.load(conn)'): ASKS,
    ('of_listen.py', 'step', 'SELECT COALESCE(MAX(rowid), 0) FROM event'): CONST,
    ('of_listen.py', 'prepare', 'SELECT COALESCE(MAX(rowid), 0) FROM event'): CONST,
    ('of_paste.py', 'pending', "SELECT json_extract(payload, '$.prompt_id') FROM event WHERE"): RARE,
    ('of_pulse.py', 'load_rows', 'SELECT event_id, ts, name, session_id, position, payload, or'): CONST,
    ('of_pulse.py', 'current_asks', 'of_asks.load(conn or of_store.connect())'): ASKS,
    ('of_pulse.py', 'run_escalations', "SELECT payload FROM event WHERE name = 'of.ask_escalated'"): RARE,
    ('of_pulse.py', 'studio_escalations', "SELECT payload FROM event WHERE name = 'of.agent_question' A"): RARE,
    ('of_pulse.py', 'studio_escalations', "SELECT event_id, position, payload FROM event WHERE name = '"): RARE,
    ('of_pulse.py', 'close_guard_asks', "SELECT event_id FROM event WHERE name = 'of.agent_question' "): RARE,
    ('of_redact.py', 'redact_events', 'SELECT payload, record FROM event WHERE rowid = ?'): BYID,
    ('of_redact.py', 'redact_events', 'SELECT record FROM event WHERE rowid = ?'): BYID,
    ('of_scratch.py', '_open_cards', "SELECT event_id FROM event WHERE name = 'of.agent_question' "): RARE,
    ('of_secrets.py', 'reviewed', "SELECT payload FROM event WHERE name = 'of.secret_reviewed' "): RARE,
    ('of_spend.py', '_store_inputs', "SELECT ts, payload FROM event WHERE name = 'of.holder_change"): CAPACITY,
    ('of_spend.py', 'owner_groups', 'SELECT session_id, org, position, count(*), min(ts), max(ts)'): CAPACITY,
    ('of_store.py', 'stats', 'SELECT count(*) FROM event'): CONST,
    ('of_studio.py', 'pr_state_rows', "of_model.load_events(of_store.connect(), names=('of.pr_state"): STUDIO,
    ('of_studio.py', 'diff_rows', "of_model.load_events(of_store.connect(), names=('of.diff_app"): STUDIO,
    ('of_studio.py', 'loop_rows', 'of_model.load_events(conn, names=of_loops.KINDS)'): STUDIO,
    ('of_studio_cli.py', 'cmd_corrections', "of_model.load_events(conn, names=('of.founder_correction',))"): STUDIO,
    ('of_tools.py', 'usage', 'SELECT session_id, position FROM event WHERE position IS NOT'): RARE,
}


def sites():
    """{(file, function, the query's start): line} of the unbounded reads in bin/."""
    out = {}
    for f in sorted(os.listdir(BIN)):
        p = os.path.join(BIN, f)
        if not (f.endswith(".py") or f == "of") or f.startswith("of_server"):
            continue
        with open(p) as fh:
            src = fh.read()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        funcs = [(n.lineno, n.end_lineno, n.name) for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]

        def where(line):
            best = max(((a, name) for a, b, name in funcs if a <= line <= b), default=(0, "<module>"))
            return best[1]
        lines = src.split("\n")
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and re.search(r"FROM event\b(?!_)", n.value):
                seg = "\n".join(lines[n.lineno - 1:n.lineno + 3])
                if not any(re.search(rx, seg, re.I) for rx in BOUND):
                    out[(f, where(n.lineno), re.sub(r"\s+", " ", n.value)[:60])] = n.lineno
            if isinstance(n, ast.Call) and ast.unparse(n.func) in LOADERS:
                kw = {k.arg for k in n.keywords}
                arg = LOADERS[ast.unparse(n.func)]
                if not (len(n.args) > 1 or (arg in kw if arg else False)):
                    out[(f, where(n.lineno), ast.unparse(n)[:60])] = n.lineno
    return out


class NoNewLogReadTest(unittest.TestCase):
    def test_no_unbounded_read_beyond_the_list_and_the_list_only_shrinks(self):
        found = sites()
        new = sorted(f"bin/{k[0]}:{line} {k[1]}: {k[2]}" for k, line in found.items() if k not in ALLOWED)
        self.assertEqual(new, [], "a new read of the whole event log: give it a bound or a view, or list it here "
                                  "with its reason (tests/test_log_reads.py)")
        gone = sorted(f"{k[0]} {k[1]}: {k[2]}" for k in ALLOWED if k not in found)
        self.assertEqual(gone, [], "these reads are bounded or gone now: take them off ALLOWED")


if __name__ == "__main__":
    unittest.main()
