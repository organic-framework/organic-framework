"""Capacity (of_capacity): account per config dir, the probe's reading, positions per account,
the 30-minute probe cache, headroom and the target of a move."""

import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_capacity  # noqa: E402
import of_locale  # noqa: E402
import of_spawn  # noqa: E402

# Production positions are configuration (capacity.yaml `hold`); the tests name a set.
POLICY = dict(of_capacity.POLICY, hold=["k8s_keeper", "storefront-hosts_keeper", "forecasting_keeper", "storefront_operator"])

RATE_LIMIT = {"type": "rate_limit_event", "rate_limit_info": {
    "status": "allowed", "resetsAt": 1793963400, "rateLimitType": "five_hour", "overageStatus": "rejected",
    "overageDisabledReason": "org_level_disabled", "isUsingOverage": False,
    "unifiedWindows": {"five_hour": {"utilization": 0.01, "resetsAt": 1793963400, "status": "allowed"},
                       "seven_day": {"utilization": 0.99, "resetsAt": 1793980800, "status": "allowed_warning"}}}}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.00398774,
          "duration_ms": 1850, "num_turns": 1, "result": "ok",
          "usage": {"input_tokens": 2, "cache_creation_input_tokens": 19024, "cache_read_input_tokens": 9074,
                    "output_tokens": 184},
          "modelUsage": {"claude-haiku-5-5": {"costUSD": 0.00398774, "canonicalModel": "claude-haiku-5-5"}}}
# A test's git never reads the user's config: the global one signs commits through 1Password's SSH signer,
# and each test commit called 1Password (of_platform, 2026-11-07). No signing, no agent, no user identity.
GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", SSH_AUTH_SOCK="",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def git(*args):
    return subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                          check=True, env=GIT_ENV, capture_output=True, text=True)


LINES = [json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}),
         json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}),
         json.dumps(RATE_LIMIT), json.dumps(RESULT)]


class ProbeParsing(unittest.TestCase):
    def test_reading_from_the_stream(self):
        r = of_capacity.parse_probe(LINES, "", 0)
        self.assertTrue(r["logged_in"])
        self.assertEqual(r["windows"]["five_hour"]["utilization"], 1)   # the CLI reports a fraction
        self.assertEqual(r["windows"]["seven_day"]["utilization"], 99)
        self.assertEqual(r["windows"]["seven_day"]["status"], "allowed_warning")
        self.assertEqual(r["windows"]["five_hour"]["resets_at"], "2026-11-06T11:10:00Z")
        self.assertEqual(r["windows"]["seven_day"]["resets_at"], "2026-11-06T16:00:00Z")
        self.assertEqual(r["overage"], "org_level_disabled")
        self.assertEqual(r["probe"]["model"], "claude-haiku-5-5")
        self.assertAlmostEqual(r["probe"]["cost_usd"], 0.00398774)
        self.assertEqual(r["probe"]["tokens"], {"input": 2, "output": 184, "cache_read": 9074, "cache_creation": 19024})
        self.assertIsNone(r["error"])

    def test_not_logged_in(self):
        r = of_capacity.parse_probe([], "Not logged in · Please run /login\n", 1)
        self.assertFalse(r["logged_in"])
        self.assertEqual(r["windows"], {})
        self.assertIn("Not logged in", r["error"])
        self.assertIsNone(r["probe"]["cost_usd"])

    def test_no_reading_is_not_logged_out(self):
        for err, rc in (("claude not found", 127), ("probe timed out after 120s", 124), ("", 1)):
            r = of_capacity.parse_probe([], err, rc)
            self.assertIsNone(r["logged_in"], err)
            self.assertTrue(r["error"])
        self.assertTrue(of_capacity._stale({"observed_at": of_capacity._iso(1_800_000_000), "logged_in": None},
                                           1_800_000_060))
        self.assertFalse(of_capacity._stale({"observed_at": of_capacity._iso(1_800_000_000), "logged_in": False},
                                            1_800_000_060))

    def test_claude_found_without_path(self):
        saved = os.environ.get("PATH")
        os.environ["PATH"] = "/usr/bin:/bin"
        try:
            b = of_capacity.claude_bin()
        finally:
            os.environ["PATH"] = saved
        self.assertTrue(b == "claude" or os.path.exists(b))

    def test_limit_reached_still_a_reading(self):
        rl = json.loads(json.dumps(RATE_LIMIT))
        rl["rate_limit_info"]["status"] = "rejected"
        rl["rate_limit_info"]["unifiedWindows"]["seven_day"]["status"] = "rejected"
        rl["rate_limit_info"]["unifiedWindows"]["seven_day"]["utilization"] = 100   # older builds: whole percents
        del rl["rate_limit_info"]["unifiedWindows"]["five_hour"]   # a window in warning: only seven_day comes
        res = dict(RESULT, is_error=True, subtype="error_during_execution", total_cost_usd=0)
        r = of_capacity.parse_probe([json.dumps(rl), json.dumps(res)], "", 1)
        self.assertTrue(r["logged_in"])
        self.assertEqual(r["windows"]["seven_day"]["utilization"], 100)
        self.assertEqual(r["status"], "rejected")
        self.assertNotIn("five_hour", r["windows"])
        self.assertEqual(of_capacity.pct(0), 0)
        self.assertEqual(of_capacity.pct(0.5), 50)
        self.assertEqual(of_capacity.pct(42), 42)


class ConfigDirs(unittest.TestCase):
    def test_discovery_and_account(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".claude"))
            os.makedirs(os.path.join(home, ".claude-b", "sessions"))
            os.makedirs(os.path.join(home, ".claude-a"))
            with open(os.path.join(home, ".claude.json"), "w") as fh:  # the default dir's file is ~/.claude.json
                json.dump({"oauthAccount": {"emailAddress": "default@x"}}, fh)
            with open(os.path.join(home, ".claude-a", ".claude.json"), "w") as fh:
                json.dump({"oauthAccount": {"emailAddress": "a@x"}}, fh)
            with open(os.path.join(home, ".claude-backup.tgz"), "w") as fh:  # a file, not a dir
                fh.write("x")
            dirs = of_capacity.config_dirs(home)
            self.assertEqual([os.path.basename(d) for d in dirs], [".claude", ".claude-a", ".claude-b"])
            self.assertEqual(of_capacity.account_of(dirs[0], home), "default@x")
            self.assertEqual(of_capacity.account_of(dirs[1], home), "a@x")
            self.assertIsNone(of_capacity.account_of(dirs[2], home))

    def test_live_sessions_and_positions(self):
        with tempfile.TemporaryDirectory() as home:
            d = os.path.join(home, ".claude-a")
            os.makedirs(os.path.join(d, "sessions"))
            for pid, sid, st in ((1, "sid-1", "idle"), (2, "sid-2", "busy"), (3, "sid-3", "idle")):
                with open(os.path.join(d, "sessions", f"{pid}.json"), "w") as fh:
                    json.dump({"pid": pid, "sessionId": sid, "status": st, "name": f"n{pid}", "cwd": "/w"}, fh)
            alive = lambda pid: pid != 3  # noqa: E731
            ss = of_capacity.live_sessions(d, alive=alive)
            self.assertEqual([s["session"] for s in ss], ["sid-1", "sid-2"])
            positions = [{"name": "p1", "holder": {"session": "sid-1"}, "_org": "o1"},
                         {"name": "p9", "holder": {"session": "zzz"}, "_org": "o1"},
                         {"name": "p2", "holder": {"session": "sid-2"}, "_org": "o2"}]
            by = of_capacity.positions_by_dir(positions, {d: ss})
            self.assertEqual(by[d]["positions"], ["p1", "p2"])
            self.assertEqual(by[d]["by_org"], {"o1": ["p1"], "o2": ["p2"]})
            self.assertEqual(by[d]["sessions"], 2)
            self.assertEqual(by[d]["busy"], 1)
            self.assertEqual(by[d]["others"], [])
            self.assertEqual(of_capacity.dir_of_position({"name": "p2", "holder": {"session": "sid-2"}}, {d: ss}), d)
            self.assertIsNone(of_capacity.dir_of_position({"name": "p9", "holder": {"session": "zzz"}}, {d: ss}))


def _row(d, five, seven, logged_in=True):
    return {"config_dir": d, "logged_in": logged_in,
            "windows": {"five_hour": {"utilization": five}, "seven_day": {"utilization": seven}} if logged_in else {}}


class StatusAndTarget(unittest.TestCase):
    def test_status_tier_from_profile_and_policy(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch.object(of_capacity, "STATE_DIR", home), mock.patch.object(of_capacity.of_events, "CONFIG_DIR", home):
            d = os.path.join(home, ".claude-a")
            os.mkdir(d)
            with open(os.path.join(d, ".claude.json"), "w") as fh:
                json.dump({"oauthAccount": {"organizationRateLimitTier": "default_claude_max_5x"}}, fh)
            self.assertEqual(of_capacity.status([], [d], probe=False)[0]["tier"], "default_claude_max_5x")
            with open(os.path.join(home, "capacity.yaml"), "w") as fh:
                fh.write("tiers:\n  ~/.claude-a: default_claude_ai\n")
            self.assertEqual(of_capacity.status([], [d], probe=False)[0]["tier"], "default_claude_ai")

    def test_probe_cache_30_minutes(self):
        calls = []

        def probe_fn(d, now):
            calls.append(d)
            return dict(_row(d, 1, 2), observed_at=of_capacity._iso(now), account="x", probe={}, error=None)

        with tempfile.TemporaryDirectory() as state:
            of_capacity.STATE_DIR = state
            t0 = 1_800_000_000
            rows = of_capacity.status([], ["/d1"], now=t0, probe_fn=probe_fn, sessions_fn=lambda d: [], emit=lambda *a: None)
            self.assertEqual(calls, ["/d1"])
            self.assertEqual(rows[0]["windows"]["seven_day"]["utilization"], 2)
            of_capacity.status([], ["/d1"], now=t0 + 29 * 60, probe_fn=probe_fn, sessions_fn=lambda d: [], emit=lambda *a: None)
            self.assertEqual(calls, ["/d1"])  # cached
            of_capacity.status([], ["/d1"], now=t0 + 31 * 60, probe_fn=probe_fn, sessions_fn=lambda d: [], emit=lambda *a: None)
            self.assertEqual(calls, ["/d1", "/d1"])
            of_capacity.status([], ["/d1"], now=t0 + 32 * 60, fresh=True, probe_fn=probe_fn, sessions_fn=lambda d: [],
                               emit=lambda *a: None)
            self.assertEqual(len(calls), 3)
            rows = of_capacity.status([], ["/d1"], now=t0 + 33 * 60, probe=False, probe_fn=probe_fn,
                                      sessions_fn=lambda d: [], emit=lambda *a: None)
            self.assertEqual(len(calls), 3)
            self.assertEqual(rows[0]["observed_at"], of_capacity._iso(t0 + 32 * 60))

    def test_due_accounts_are_probed_at_once(self):
        # of_platform, 2026-11-17: four probes in a row took 37 s of a pulse. Each probe here returns only once all
        # three run together, so probes one after another break the barrier
        import threading
        gate = threading.Barrier(3, timeout=10)

        def probe_fn(d, now):
            gate.wait()
            return dict(_row(d, 1, int(d[-1])), observed_at=of_capacity._iso(now), account=d, probe={}, error=None)

        saved = of_capacity.STATE_DIR
        self.addCleanup(setattr, of_capacity, "STATE_DIR", saved)
        with tempfile.TemporaryDirectory() as state:
            of_capacity.STATE_DIR = state
            rows = of_capacity.status([], ["/d1", "/d2", "/d3"], now=1_800_000_000, probe_fn=probe_fn,
                                      sessions_fn=lambda d: [], emit=lambda *a: None)
            self.assertEqual([(r["config_dir"], r["windows"]["seven_day"]["utilization"]) for r in rows],
                             [("/d1", 1), ("/d2", 2), ("/d3", 3)])  # each reading on its own account, in order
            self.assertEqual(sorted(k for k in of_capacity.load_cache() if k.startswith("/")), ["/d1", "/d2", "/d3"])

    def test_readings_two_periods_old_are_behind(self):
        # of_review on PR 297: with the detached balancer stalled, the pulse probes for itself
        t0 = 1_800_000_000
        cache = {"/a": {"observed_at": of_capacity._iso(t0 - 59 * 60)}, "/b": {"observed_at": of_capacity._iso(t0 - 61 * 60)},
                 "/c": {"error": "not probed yet"}}
        from unittest import mock
        with mock.patch.object(of_capacity, "load_cache", return_value=cache):
            self.assertEqual(of_capacity.readings_behind(["/a", "/b", "/c"], now=t0), ["/b", "/c"])

    def test_snapshot_event_per_fresh_probe(self):
        events = []
        with tempfile.TemporaryDirectory() as state:
            of_capacity.STATE_DIR = state
            probe_fn = lambda d, now: dict(_row(d, 5, 50), observed_at=of_capacity._iso(now), account="a@x", probe={  # noqa: E731
                "cost_usd": 0.004}, error=None)
            positions = [{"name": "p1", "holder": {"session": "s1"}}]
            sessions = {"/d1": [{"pid": 1, "session": "s1", "status": "idle", "name": "p1", "cwd": "/w"}]}
            of_capacity.status(positions, ["/d1"], now=1_800_000_000, probe_fn=probe_fn,
                               sessions_fn=lambda d: sessions.get(d, []), emit=lambda k, p: events.append((k, p)))
            self.assertEqual([k for k, _ in events], ["capacity_snapshot"])
            self.assertEqual(events[0][1]["positions"], ["p1"])
            self.assertEqual(events[0][1]["windows"]["seven_day"]["utilization"], 50)
            self.assertEqual(events[0][1]["probe"]["cost_usd"], 0.004)
            of_capacity.status(positions, ["/d1"], now=1_800_000_000 + 60, probe_fn=probe_fn,
                               sessions_fn=lambda d: sessions.get(d, []), emit=lambda k, p: events.append((k, p)))
            self.assertEqual(len(events), 1)  # a cached reading writes no event

    def test_headroom_and_target(self):
        rows = [_row("/a", 0, 99), _row("/b", 0, 0), _row("/c", 40, 10), _row("/d", 0, 0, logged_in=False)]
        self.assertEqual(of_capacity.headroom(rows[0]), 1)
        self.assertEqual(of_capacity.headroom(rows[2]), 60)
        self.assertIsNone(of_capacity.headroom(rows[3]))
        self.assertEqual(of_capacity.best_target(rows, exclude="/b")["config_dir"], "/c")
        self.assertEqual(of_capacity.best_target(rows)["config_dir"], "/b")
        self.assertIsNone(of_capacity.best_target([rows[3]]))

    def test_best_target_uses_tier_when_known(self):
        pro, max20 = _row("/pro", 0, 0), _row("/max", 0, 31)
        self.assertEqual(of_capacity.best_target([max20, pro]), pro)
        pro["tier"] = "default_claude_ai"
        max20["tier"] = "default_claude_max_20x"
        self.assertEqual(of_capacity.best_target([pro, max20]), max20)

    def test_best_target_keeps_an_account_over_threshold_as_a_fallback(self):
        max20 = dict(_row("/max", 0, 96), tier="default_claude_max_20x")
        pro = dict(_row("/pro", 0, 40), tier="default_claude_ai")
        self.assertEqual(of_capacity.best_target([max20, pro]), pro)
        self.assertEqual(of_capacity.best_target([max20]), max20)

    def test_best_target_prefers_an_accepting_account_over_threshold_to_a_rejected_one(self):
        rejected = dict(_row("/a", 94, 10), status="rejected")
        accepting = dict(_row("/b", 0, 90), status="allowed")
        self.assertEqual(of_capacity.best_target([rejected, accepting]), accepting)
        self.assertEqual(of_capacity.best_target([rejected]), rejected)

    def test_free_capacity_uses_the_measured_week(self):
        row = dict(_row("/max", 0, 40), tier="default_claude_max_5x")
        self.assertEqual(of_capacity.free_capacity(row), 60 * 19)
        self.assertEqual(of_capacity.free_capacity(row, usd=12), 60 * 12)

    def test_over_threshold(self):
        self.assertEqual(of_capacity.over_threshold(_row("/a", 0, 99)), "seven_day 99% ≥ 90%")
        self.assertEqual(of_capacity.over_threshold(_row("/a", 96, 10)), "five_hour 96% ≥ 95%")
        self.assertIsNone(of_capacity.over_threshold(_row("/a", 50, 89)))
        self.assertIsNone(of_capacity.over_threshold(_row("/a", 0, 0, logged_in=False)))

    def test_table(self):
        rows = [dict(_row("/Users/alex/.claude-a", 3, 99), account="a@x", observed_at="2026-11-06T08:00:00Z",
                     positions=["of_platform", "of_lab"], others=["storefront-3f"], sessions=3, busy=1,
                     probe={"cost_usd": 0.004}, error=None),
                dict(_row("/Users/alex/.claude", 0, 0, logged_in=False), account="d@x", observed_at="2026-11-06T08:00:00Z",
                     positions=[], others=[], sessions=0, busy=0, probe={"cost_usd": None}, error="Not logged in")]
        text = of_capacity.format_table(rows, home="/Users/alex")
        self.assertIn("~/.claude-a", text)
        self.assertIn("99%", text)
        self.assertIn("of_platform, of_lab", text)
        self.assertIn("not logged in", text)
        rows[0]["positions"] = [f"p{i}" for i in range(6)]
        rows[0]["by_org"] = {"o1": ["p0", "p1"], "o2": ["p2", "p3", "p4", "p5"]}
        text = of_capacity.format_table(rows, home="/Users/alex")
        self.assertIn("o1 2, o2 4", text)
        self.assertIn("~/.claude-a o2: p2, p3, p4, p5", text)


class MovePieces(unittest.TestCase):
    def test_session_paths_and_copy(self):
        with tempfile.TemporaryDirectory() as t:
            src, dst, bak = (os.path.join(t, x) for x in ("src", "dst", "bak"))
            sid = "abc"
            for rel in (f"projects/-w/{sid}.jsonl", f"projects/-w/{sid}/subagents/a.jsonl", f"file-history/{sid}/f1",
                        f"session-env/{sid}/.keep", "projects/-w/other.jsonl"):
                os.makedirs(os.path.dirname(os.path.join(src, rel)), exist_ok=True)
                with open(os.path.join(src, rel), "w") as fh:
                    fh.write("new")
            os.makedirs(os.path.join(dst, "projects", "-w"))
            with open(os.path.join(dst, "projects", "-w", f"{sid}.jsonl"), "w") as fh:
                fh.write("old")
            self.assertEqual(of_capacity.session_paths(src, sid),
                             [f"projects/-w/{sid}.jsonl", f"projects/-w/{sid}", f"file-history/{sid}", f"session-env/{sid}"])
            self.assertEqual(of_capacity.project_key(src, sid), "-w")
            acts = of_capacity.copy_session(src, dst, sid, bak, dry_run=True)
            self.assertEqual(acts[0], ("backup", f"projects/-w/{sid}.jsonl"))
            self.assertFalse(os.path.exists(os.path.join(dst, "file-history")))
            of_capacity.copy_session(src, dst, sid, bak)
            self.assertEqual(of_capacity._read(os.path.join(dst, "projects", "-w", f"{sid}.jsonl")), b"new")
            self.assertEqual(of_capacity._read(os.path.join(bak, "projects", "-w", f"{sid}.jsonl")), b"old")
            self.assertTrue(os.path.exists(os.path.join(dst, "projects", "-w", sid, "subagents", "a.jsonl")))
            self.assertFalse(os.path.exists(os.path.join(dst, "projects", "-w", "other.jsonl")))

    def test_copy_over_read_only_file_history(self):
        # a session moved back to an account it ran on finds its 0444 file-history copies there (2026-11-18)
        with tempfile.TemporaryDirectory() as t:
            src, dst, bak = (os.path.join(t, x) for x in ("src", "dst", "bak"))
            sid = "abc"
            for d, text in ((src, "new"), (dst, "old")):
                for rel in (f"projects/-w/{sid}.jsonl", f"file-history/{sid}/f1@v1"):
                    p = os.path.join(d, rel)
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    with open(p, "w") as fh:
                        fh.write(text)
                    os.chmod(p, 0o444)
            of_capacity.copy_session(src, dst, sid, bak)
            for rel in (f"projects/-w/{sid}.jsonl", f"file-history/{sid}/f1@v1"):
                self.assertEqual(of_capacity._read(os.path.join(dst, rel)), b"new")
                self.assertEqual(of_capacity._read(os.path.join(bak, rel)), b"old")

    def test_memory_merge_never_loses_an_edit(self):
        with tempfile.TemporaryDirectory() as t:
            src, dst, base, conf = (os.path.join(t, x) for x in ("src", "dst", "base", "conf"))
            os.makedirs(src)

            def put(d, name, text):
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, name), "w") as fh:
                    fh.write(text)

            def get(d, name):
                return of_capacity._read(os.path.join(d, name)).decode()

            put(src, "a.md", "a1")
            put(src, "MEMORY.md", "- [A](a.md)\n")
            self.assertEqual(of_capacity.merge_memory(src, dst, base, conf), [("MEMORY.md", "copy"), ("a.md", "copy")])
            # the source edits a.md (target untouched): carried
            put(src, "a.md", "a2")
            self.assertEqual(dict(of_capacity.merge_memory(src, dst, base, conf))["a.md"], "update")
            self.assertEqual(get(dst, "a.md"), "a2")
            # the target edits a.md (source untouched): kept
            put(dst, "a.md", "a3")
            self.assertEqual(dict(of_capacity.merge_memory(src, dst, base, conf))["a.md"], "keep")
            self.assertEqual(get(dst, "a.md"), "a3")
            # both edit: a three-way line merge keeps both sides; the source is also kept in conflicts
            put(src, "a.md", "a4")
            self.assertEqual(dict(of_capacity.merge_memory(src, dst, base, conf))["a.md"], "merge3")
            self.assertEqual(get(dst, "a.md").splitlines(), ["a3", "a4"])
            self.assertEqual(get(conf, "a.md"), "a4")
            # both edit the index: union of lines, the target's order first
            put(src, "MEMORY.md", "- [A](a.md)\n- [B](b.md)\n")
            put(dst, "MEMORY.md", "- [A](a.md)\n- [C](c.md)\n")
            self.assertEqual(dict(of_capacity.merge_memory(src, dst, base, conf))["MEMORY.md"], "union")
            self.assertEqual(get(dst, "MEMORY.md"), "- [A](a.md)\n- [C](c.md)\n- [B](b.md)\n")
            # without a base, a newer target is a conflict, never an overwrite
            put(src, "z.md", "z-src")
            put(dst, "z.md", "z-dst")
            os.utime(os.path.join(src, "z.md"), (1000, 1000))
            os.utime(os.path.join(dst, "z.md"), (2000, 2000))
            self.assertEqual(dict(of_capacity.merge_memory(src, dst, base, conf))["z.md"], "conflict")
            self.assertEqual(get(dst, "z.md"), "z-dst")
            # without a base, an older target (a copy left on an account weeks ago) is replaced, and kept aside
            put(src, "y.md", "y-new")
            put(dst, "y.md", "y-old")
            os.utime(os.path.join(dst, "y.md"), (1000, 1000))
            self.assertEqual(dict(of_capacity.merge_memory(src, dst, base, conf))["y.md"], "replace_older")
            self.assertEqual(get(dst, "y.md"), "y-new")
            self.assertEqual(get(conf, "y.md.older-target"), "y-old")

    def test_input_draft(self):
        box = ["\x1b[38;2;136;136;136m──────── of_platform ─\x1b[0m", "❯\xa0",
               "\x1b[38;2;136;136;136m────────\x1b[0m", "  ⏵⏵ bypass permissions on"]
        self.assertEqual(of_capacity.input_draft(box), "")
        typed = list(box)
        typed[1] = "❯\xa0please also check x"
        self.assertEqual(of_capacity.input_draft(typed), "please also check x")
        hint = list(box)
        hint[1] = "❯\xa0\x1b[2m\x1b[38;2;153;153;153mcommit this\x1b[0m"
        self.assertEqual(of_capacity.input_draft(hint), "")
        self.assertIsNone(of_capacity.input_draft(["alex@mac ~ %"]))
        review = ["   → Include client.keys (Recommended)", "Ready to submit your answers?", "❯ 1. Submit answers",
                  "  2. Cancel", "──── host-1 ─"]
        self.assertIsNone(of_capacity.input_draft(review))  # a menu, never an input box: no Enter may go there
        # Claude Code's compaction offer (2026-11-10, a 432k session restarted on Fable): Enter compacts at 400k
        compact = ["─" * 40, " We recommend compacting earlier", "", "   ❯ Autocompact at ‹ 400k › tokens",
                   "     Keep 1m tokens", "", "   ↑/↓ choose · ←/→ change size · enter confirm · esc not now"]
        self.assertIsNone(of_capacity.input_draft(compact))
        self.assertIsNone(of_capacity.input_draft(["❯ 1. Stop and wait for limit to reset", "  2. Wait here"]))
        agents_panel = box[:3] + ["  ⏵⏵ bypass permissions on · 11 shells", "", "  ⏺ main",
                                  "❯ ◯ general-purpose  Waiting for CI on PR 696, W7-02   1d 13h"]
        self.assertEqual(of_capacity.input_draft(agents_panel), "")  # the agents list under the box is not a draft
        history = ["\x1b[48;2;55;55;55m❯ \x1b[0m\x1b[38;2;255;255;255man earlier prompt\x1b[0m", "⏺ ok"] + box
        self.assertEqual(of_capacity.input_draft(history), "")

    def test_launch_command_fits_a_line(self):
        cmd = of_capacity.launch_command("/Users/alex/organic-framework", "/Users/alex/.claude-e",
                                         "0b1e2c3d-4a5b-4c6d-8e7f-90a1b2c3d4e5", "of_field-analyst",
                                         "/Users/alex/.local/state/of/capacity/moves/of_field-analyst-20261106T103000Z.md",
                                         model="claude-opus-5-5", effort="high")
        self.assertIn("CLAUDE_CONFIG_DIR=/Users/alex/.claude-e claude --resume 0b1e2c3d-4a5b-4c6d-8e7f-90a1b2c3d4e5", cmd)
        self.assertIn("-n of_field-analyst --settings ", cmd)
        self.assertIn("--model claude-opus-5-5 --effort high --dangerously-skip-permissions", cmd)
        self.assertLess(len(cmd.encode()), 1024)  # macOS canonical line limit for `herdr pane run`
        home = os.path.expanduser("~")
        cmd = of_capacity.launch_command("/w", os.path.join(home, ".claude"), "sid", "n", "/note")
        self.assertIn("&& env -u CLAUDE_CONFIG_DIR claude --resume sid", cmd)

    def test_default_dir_is_probed_without_the_variable(self):
        self.assertNotIn("CLAUDE_CONFIG_DIR", of_capacity.probe_env("/h/.claude", home="/h"))
        self.assertEqual(of_capacity.probe_env("/h/.claude-a", home="/h")["CLAUDE_CONFIG_DIR"], "/h/.claude-a")
        self.assertNotIn("HERDR_PANE_ID", of_capacity.probe_env("/h/.claude-a", home="/h"))
        saved = os.environ.pop("USER", None)
        try:
            self.assertTrue(of_capacity.probe_env("/h/.claude-a", home="/h").get("USER"))  # keychain lookup needs it
        finally:
            if saved is not None:
                os.environ["USER"] = saved

    def test_note_and_registry_edit(self):
        note = of_capacity.handover_note("of_lab", "sid", "/Users/alex/.claude-a", "/Users/alex/.claude-b", "b@x",
                                         {"session_crons": [], "background_tasks": [
                                             {"type": "shell", "status": "running", "description": "lab run",
                                              "command": "run.sh 0"}]}, "2026-11-06T10:00:00Z")
        self.assertIn("background shell: lab run", note)
        self.assertIn("Re-create", note)
        for advice in ("run CronList first", "Re-create the tasks listed below as stopped by the exit",
                       "only on Claude Code versions before 2.1.329", "Re-create only what CronList lacks",
                       "run twice (2026-11-14)"):
            self.assertIn(advice, note)
        self.assertTrue(note.startswith("# Account move: of_lab, now on b@x\n"))  # the account in the first line
        self.assertIn("near its model limits", note)  # no reason given: the default
        # a move with its own reason says that reason, never the default one (2026-11-17)
        own = of_capacity.handover_note("p", "sid", "/Users/alex/.claude-a", "/Users/alex/.claude-b", "b@x", {}, "t",
                                        why="it drops a tool the old account carries.")
        self.assertIn("(b@x): it drops a tool the old account carries. Same session", own)
        self.assertNotIn("near its model limits", own)
        self.assertNotIn("Nothing else changed", own)
        bare = of_capacity.handover_note("p", "sid", "/a", "/b", "b@x", {}, "t", why="it drops a tool")
        self.assertIn("it drops a tool. Same session", bare)  # a reason gets its full stop (PR 269 review)
        self.assertIn("expire unused", of_capacity.balance_why("fill: its week resets first"))
        self.assertIn("stopped accepting work", of_capacity.balance_why("rescue: 429; ~/.claude-b accepts work"))
        self.assertIn("about to reach", of_capacity.balance_why("relief: the account is about to stall"))
        # a model change on the same account: says the model, the reason, and no account move
        mc = of_capacity.handover_note("p", "sid", "/Users/alex/.claude-a", "/Users/alex/.claude-a", "a@x", {},
                                       "2026-11-10T16:30:00Z", model=("claude-opus-5-5", "claude-fable-5-2"),
                                       why="The founder approved a 3-day trial (ask 8ee7f1bc).")
        self.assertIn("# Model change: p", mc)
        self.assertIn("on `claude-fable-5-2` (was `claude-opus-5-5`)", mc)
        self.assertIn("The founder approved a 3-day trial (ask 8ee7f1bc).", mc)
        self.assertNotIn("near its model limits", mc)
        self.assertNotIn("moved this session", mc)
        # a model change with an account move names both
        both = of_capacity.handover_note("p", "sid", "/Users/alex/.claude-a", "/Users/alex/.claude-b", "b@x", {},
                                         "t", model=("claude-opus-5-5", "claude-fable-5-2"))
        self.assertIn("from `~/.claude-a` to `~/.claude-b` (b@x)", both.replace("/Users/alex", "~"))
        self.assertIn("`claude-fable-5-2`", both)
        self.assertEqual(of_capacity.launch_prompt("/n.md", model="claude-fable-5-2"),
                         "of_capacity restarted this session on claude-fable-5-2. Read /n.md and follow it.")
        self.assertIn("moved this session to another account", of_capacity.launch_prompt("/n.md"))
        # a restart on the same account and model (move --restart, 2026-11-12: tools back after a partial profile)
        rs = of_capacity.handover_note("p", "sid", "/Users/alex/.claude-a", "/Users/alex/.claude-a", "a@x", {}, "t",
                                       why="Your tools are back.", restarted=True)
        self.assertIn("# Restart: p", rs)
        self.assertIn("on the same account", rs)
        self.assertIn("Your tools are back.", rs)
        self.assertNotIn("near its model limits", rs)
        self.assertEqual(of_capacity.launch_prompt("/n.md", restarted=True),
                         "of_capacity restarted this session on the same account. Read /n.md and follow it.")
        reg = ("positions:\n  - name: of_lab\n    holder:\n      runtime: claude-code\n"
               "      workplace: herdr local, workspace organic-framework, tab lab\n"
               "  - name: of_capacity\n    holder:\n"
               "      workplace: herdr local, workspace organic-framework, tab capacity, account ~/.claude-e\n")
        new, ch = of_capacity.workplace_edit(reg, "of_lab", "~/.claude-e")
        self.assertTrue(ch)
        self.assertIn("tab lab, account ~/.claude-e\n", new)
        new2, ch2 = of_capacity.workplace_edit(new, "of_lab", "~/.claude-e")
        self.assertFalse(ch2)
        quoted = ('positions:\n  - name: p\n    holder:\n'
                  '      workplace: "zellij quadratic-magpie, pane 193 (Tab #1), account ~/.claude-b"\n')
        q2, chq = of_capacity.workplace_edit(quoted, "p", "~/.claude")
        self.assertTrue(chq)
        self.assertIn('pane 193 (Tab #1), account ~/.claude"\n', q2)
        import yaml
        self.assertEqual(yaml.safe_load(q2)["positions"][0]["holder"]["workplace"],
                         "zellij quadratic-magpie, pane 193 (Tab #1), account ~/.claude")
        broken = 'positions:\n  - name: p\n    holder:\n      workplace: a: b: c\n'
        self.assertEqual(of_capacity.workplace_edit(broken, "p", "~/.claude"), (broken, False))
        new3, _ = of_capacity.workplace_edit(new, "of_capacity", "~/.claude-b")
        self.assertIn("tab capacity, account ~/.claude-b\n", new3)
        self.assertIn("tab lab, account ~/.claude-e\n", new3)


class PlanAndWatch(unittest.TestCase):
    ROWS = [dict(_row("/g", 0, 100), account="g@x", status="rejected", positions=["acme_done", "acme_a", "acme_prod", "acme_parked"],
                 by_org={"acme": ["acme_a", "acme_prod", "acme_parked"]}),
            dict(_row("/e", 5, 1), account="e@x", positions=["of_x"]),
            dict(_row("/d", 0, 0), account="d@x", positions=[]),
            dict(_row("/p", 0, 99), account="p@x", positions=[])]
    POS = [{"name": "acme_a", "throughput": "measure", "holder": {"session": "s1"}},
           {"name": "acme_prod", "throughput": "raise", "holder": {"session": "s2"}},
           {"name": "acme_parked", "throughput": "raise", "status": "parked", "holder": {"session": "s3"}},
           {"name": "of_x", "holder": {"session": "s4"}},
           {"name": "acme_done", "throughput": "raise", "status": "finished", "holder": {"session": "s5"}}]

    def test_plan_order_and_target(self):
        rows = [dict(r, windows={k: dict(v) for k, v in r["windows"].items()}) for r in self.ROWS]
        out = of_capacity.plan(rows, self.POS, sessions={"/g": [{"session": "s2", "status": "busy"}]})
        self.assertEqual([x["position"] for x in out], ["acme_a", "acme_parked", "acme_done", "acme_prod"])  # busy last
        self.assertEqual({x["to"] for x in out}, {"/d"})
        out = of_capacity.plan(rows, self.POS)
        self.assertEqual([x["position"] for x in out], ["acme_prod", "acme_a", "acme_parked", "acme_done"])
        self.assertEqual(out[0]["command"], "of capacity move acme_prod --to /d")

    def test_watch_asks_once_per_window_and_reset(self):
        import of_events
        asked = []

        def ask(text, event_id):
            asked.append((text, event_id))
            of_events.emit("agent_question", {"questions": [{"question": text}]}, event_id=event_id, org="acme")

        with tempfile.TemporaryDirectory() as state, mock.patch.object(of_capacity, "STATE_DIR", state), \
                mock.patch.object(of_events, "STATE_DIR", state):
            rows = [dict(r, observed_at="2026-11-06T06:50:00Z", probe={}, error=None,
                         windows={k: dict(v) for k, v in r["windows"].items()}) for r in self.ROWS]
            rows[0]["windows"]["seven_day"]["resets_at"] = "2026-11-08T09:00:00Z"
            probe_fn = lambda d, now: next(r for r in rows if r["config_dir"] == d)  # noqa: E731
            kw = dict(dirs=["/g", "/e", "/d", "/p"], now=1_800_000_000, probe_fn=probe_fn, sessions_fn=lambda d: [],
                      emit=lambda *a: None)
            pos = [dict(p) for p in self.POS]
            sessions = {"/g": [{"session": "s1"}, {"session": "s2"}, {"session": "s3"}], "/e": [{"session": "s4"}]}
            kw["sessions_fn"] = lambda d: sessions.get(d, [])
            of_locale.set_language("en")  # the English text, whatever owner.yaml says
            try:
                of_capacity.watch(pos, ask, log=lambda m: None, **kw)
            finally:
                of_locale.set_language(None)
            self.assertEqual(len(asked), 1)
            text, event_id = asked[0]
            self.assertIn("off the account /g (g@x)", text)
            self.assertIn("spent 100% of its week (threshold 90%)", text)
            self.assertIn("Most headroom: /d", text)
            self.assertEqual(event_id, of_events.stable_id("capacity_threshold", "g@x", "seven_day",
                                                          "2026-11-08T09:00:00Z"))
            of_capacity.watch(pos, ask, log=lambda m: None, fresh=True, **kw)
            self.assertEqual(len(asked), 1)  # same window, same reset: asked already
            of_capacity.save_cache({})  # the other machine has only the event and its own config path
            rows[0]["config_dir"] = kw["dirs"][0] = "/other"
            sessions["/other"] = sessions.pop("/g")
            self.assertEqual(of_capacity.watch(pos, ask, log=lambda m: None, **kw), [])
            self.assertEqual(len(asked), 1)
            rows[0]["windows"]["seven_day"]["resets_at"] = "2026-11-15T09:00:00Z"
            of_capacity.watch(pos, ask, log=lambda m: None, fresh=True, **kw)
            self.assertEqual(len(asked), 2)  # a new reset period: ask again
            self.assertNotEqual(asked[1][1], event_id)
            # a contour that files no account cards (docs/contours.md: the other contour's balancer does)
            of_capacity.save_cache({})
            rows[0]["windows"]["seven_day"]["resets_at"] = "2026-11-22T09:00:00Z"
            self.assertEqual(of_capacity.watch(pos, ask, log=lambda m: None, fresh=True,
                                               policy={"account_cards": False}, **kw), [])
            self.assertEqual(len(asked), 2)

    def test_threshold_id_uses_the_window_and_falls_back_to_the_dir(self):
        import of_events
        reset = of_capacity._iso(Balance.NOW + 86400)
        for account in ("shared@acme.example", None):
            for window in ("five_hour", "seven_day"):
                row = dict(self.ROWS[0], account=account, windows={window: {"utilization": 99, "resets_at": reset}})
                key, _, _, _ = of_capacity.proposals([row], set(), now=Balance.NOW, policy=POLICY)[0]
                self.assertEqual(key, of_events.stable_id("capacity_threshold", account or "/g", window, reset))

    def test_with_the_balancer_on_asks_only_when_no_account_has_room(self):
        asked = []

        def ask(text, event_id):
            asked.append(event_id)

        with tempfile.TemporaryDirectory() as state:
            of_capacity.STATE_DIR = state
            rows = [dict(r, observed_at="2026-11-06T06:50:00Z", probe={}, error=None,
                         windows={k: dict(v) for k, v in r["windows"].items()}) for r in self.ROWS]
            probe_fn = lambda d, now: next(r for r in rows if r["config_dir"] == d)  # noqa: E731
            sessions = {"/g": [{"session": "s1"}, {"session": "s2"}, {"session": "s3"}], "/e": [{"session": "s4"}]}
            kw = dict(dirs=["/g", "/e", "/d", "/p"], now=1_800_000_000, probe_fn=probe_fn,
                      sessions_fn=lambda d: sessions.get(d, []), emit=lambda *a: None)
            pos = [dict(p) for p in self.POS]
            on = {"auto": True}
            of_capacity.watch(pos, ask, log=lambda m: None, policy=on, **kw)
            self.assertEqual(asked, [])  # /d has room: relief moves them, nothing to decide
            for r in rows[1:]:
                r["windows"]["seven_day"]["utilization"] = 95
            of_capacity.watch(pos, ask, log=lambda m: None, policy=on, fresh=True, **kw)
            self.assertEqual(len(asked), 2)  # no room anywhere: the owner decides, for /g and for /e
            of_capacity.watch(pos, ask, log=lambda m: None, policy={"auto": False}, fresh=True, **kw)
            self.assertEqual(len(asked), 2)  # asked once per window and reset, as before

    def test_probe_runs_without_hooks_or_mcp(self):
        cmd = of_capacity.probe_command()
        self.assertIn('{"disableAllHooks": true}', cmd)
        self.assertIn("--strict-mcp-config", cmd)


class ZellijToHerdr(unittest.TestCase):
    WS = [{"workspace_id": "w3", "label": "organic-framework"}, {"workspace_id": "w7", "label": "acme·shop"}]
    TABS = [{"tab_id": "w7:t1", "workspace_id": "w7", "label": "storefront"}]
    PANES = [{"pane_id": "w7:p1", "tab_id": "w7:t1"}, {"pane_id": "w7:p2", "tab_id": "w7:t1"}]

    def test_place_new_workspace_tab_or_split(self):
        c, f = of_capacity.place_command("ac·platform", "k8s", "/w", self.WS, self.TABS, self.PANES)
        self.assertEqual(c[:2], ["workspace", "create"])
        self.assertIn("ac·platform", c)
        self.assertEqual(f, ("tab", "k8s"))
        c, f = of_capacity.place_command("acme·shop", "forecasting", "/w", self.WS, self.TABS, self.PANES)
        self.assertEqual(c[:4], ["tab", "create", "--workspace", "w7"])
        self.assertIsNone(f)
        c, f = of_capacity.place_command("acme·shop", "storefront", "/w", self.WS, self.TABS, self.PANES)
        self.assertEqual(c[:3], ["pane", "split", "w7:p2"])
        self.assertIn("down", c)

    def test_first_pane_id(self):
        self.assertEqual(of_capacity.first_pane_id({"tab": {"tab_id": "t"}, "root_pane": {"pane_id": "w9:p1"}}), "w9:p1")
        self.assertEqual(of_capacity.first_pane_id({"panes": [{"x": 1}, {"pane_id": "w9:p2"}]}), "w9:p2")
        self.assertIsNone(of_capacity.first_pane_id({"ok": True}))

    def test_flags_follow_the_old_process(self):
        cmd = of_capacity.launch_command("/w", "/Users/alex/.claude-a", "sid", "n", "/note", bypass=False)
        self.assertNotIn("--dangerously-skip-permissions", cmd)
        note = of_capacity.handover_note("n", "sid", "/a", "/b", "b@x", {}, "t", extra="You moved from Zellij to herdr.")
        self.assertIn("## From your organization", note)
        self.assertTrue(note.rstrip().endswith("You moved from Zellij to herdr."))


class Closure(unittest.TestCase):
    def test_daemon_up_from_its_log(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(of_capacity.daemon_up(d))  # no log: no daemon
            with open(os.path.join(d, "daemon.log"), "w") as fh:
                fh.write("[t] [bg] bg settled x (done)\n[t] [supervisor] shutting down (cause=signal)\n")
            self.assertFalse(of_capacity.daemon_up(d))
            with open(os.path.join(d, "daemon.log"), "a") as fh:
                fh.write("[t] [supervisor] auth: token still valid\n")
            self.assertTrue(of_capacity.daemon_up(d))


class HerdrName(unittest.TestCase):
    """A resumed process shows up in herdr as a new agent without the position's name."""
    def run_with(self, lists):
        calls, seq = [], iter(lists)
        def fake(*args, timeout=30):
            calls.append(args)
            if args[:2] == ("agent", "list"):
                return {"agents": next(seq, lists[-1])}
            return {}
        orig = of_capacity._herdr
        of_capacity._herdr = fake
        try:
            return of_capacity.rename_agent("w6:p18", "runners_keeper", "s-new", timeout=0.05, step=0.01), calls
        finally:
            of_capacity._herdr = orig

    def test_names_the_new_agent_once_herdr_shows_it(self):
        old = [{"pane_id": "w6:p18", "agent_session": {"value": "s-old"}, "name": "runners_keeper"}]
        new = [{"pane_id": "w6:p18", "agent_session": {"value": "s-new"}}]
        r, calls = self.run_with([old, new])
        self.assertEqual(r, "named")
        self.assertIn(("agent", "rename", "w6:p18", "runners_keeper"), calls)

    def test_keeps_a_name_already_there(self):
        r, calls = self.run_with([[{"pane_id": "w6:p18", "agent_session": {"value": "s-new"}, "name": "runners_keeper"}]])
        self.assertEqual(r, "kept")
        self.assertFalse([c for c in calls if c[:2] == ("agent", "rename")])

    def test_never_renames_another_pane_or_session(self):
        r, calls = self.run_with([[{"pane_id": "w6:p19", "agent_session": {"value": "s-new"}},
                                   {"pane_id": "w6:p18", "agent_session": {"value": "s-other"}}]])
        self.assertEqual(r, "unseen")
        self.assertFalse([c for c in calls if c[:2] == ("agent", "rename")])


class RegistryIsNotOurs(unittest.TestCase):
    REG = ('positions:\n  - name: acme_a\n    holder:\n      session: s1\n'
           '      workplace: zellij quadratic-magpie, pane 55 (tab "cloudflared (background)")\n'
           '  - name: acme_b\n    holder:\n      workplace: "zellij x, pane 193 (Tab #1), account ~/.claude-g"\n')

    def test_a_move_proposes_a_diff_and_never_writes_the_registry(self):
        import yaml
        with tempfile.TemporaryDirectory() as t:
            reg = os.path.join(t, "positions.yaml")
            with open(reg, "w") as fh:
                fh.write(self.REG)
            diff, path = of_capacity.propose_registry(reg, "acme_a", "~/.claude",
                                                      "herdr local, workspace acme·shop, tab storefront, pane wD:p1",
                                                      os.path.join(t, "work"))
            self.assertEqual(open(reg).read(), self.REG)  # untouched
            self.assertTrue(diff.startswith("--- a/positions.yaml\n+++ b/positions.yaml"))  # git apply in the repo
            self.assertTrue(os.path.exists(path))
            self.assertIn('+      workplace: "herdr local, workspace acme·shop, tab storefront, pane wD:p1"', diff)
            self.assertNotIn("account", diff.split("+      workplace:")[1].splitlines()[0])  # runtime, not structure
            new = self.REG.replace('zellij quadratic-magpie, pane 55 (tab "cloudflared (background)")',
                                   '"herdr local, workspace acme·shop, tab storefront, pane wD:p1"')
            self.assertEqual(yaml.safe_load(new)["positions"][0]["holder"]["workplace"],
                             "herdr local, workspace acme·shop, tab storefront, pane wD:p1")
            diff, path = of_capacity.propose_registry(reg, "acme_b", "~/.claude", out_dir=os.path.join(t, "w2"))
            self.assertEqual((diff, path), ("", None))  # an account-only move changes no structure
            self.assertEqual(open(reg).read(), self.REG)


class OpenQuestion(unittest.TestCase):
    def test_pending_question_survives_the_move(self):
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, "s.jsonl")
            ask = {"questions": [{"header": "fstrim w7", "question": "Run fstrim?", "options": [
                {"label": "Yes", "description": "do it"}, {"label": "No", "description": ""}]}]}
            rows = [{"type": "assistant", "timestamp": "T1", "message": {"content": [
                        {"type": "tool_use", "id": "a1", "name": "AskUserQuestion", "input": {"questions": []}}]}},
                    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a1"}]}},
                    {"type": "assistant", "timestamp": "T2", "message": {"content": [
                        {"type": "tool_use", "id": "a2", "name": "AskUserQuestion", "input": ask}]}}]
            with open(t, "w") as fh:
                fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
            q = of_capacity.pending_question(t)
            self.assertEqual(q, (ask, "T2"))
            note = of_capacity.question_note(*q)
            self.assertIn("of ask --blocking", note)
            self.assertIn("Run fstrim?", note)
            self.assertIn("option «Yes»: do it", note)
            with open(t, "a") as fh:
                fh.write(json.dumps({"type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "a2"}]}}) + "\n")
            self.assertIsNone(of_capacity.pending_question(t))


class StoppedTasks(unittest.TestCase):
    def test_matches_each_exit_notice_to_the_latest_preceding_creation_or_the_carry(self):
        since = "2026-11-20T10:13:14.123Z"
        hourly = "Review the hourly report and send a summary to the team. " + "Include every open item."
        wake = "Check the overnight run\nand report its outcome."

        def create(prompt, cron, **fields):
            return {"type": "tool_use", "name": "CronCreate",
                    "input": dict(prompt=prompt, cron=cron, **fields)}

        def stop(prefix, timestamp=since):
            return {"type": "system", "subtype": "informational", "timestamp": timestamp,
                    "content": "Stopped scheduled task: " + prefix}

        rows = [
            {"type": "assistant", "message": {"content": [
                create(hourly, "0 * * * *"), create(hourly, "23 * * * *"),
                create(wake, "52 11 21 11 *", recurring=False)]}},
            stop("earlier exit", "2026-11-20T10:13:14.122Z"),
            {"type": "attachment", "attachment": {"type": "session_cron_carry", "tasks": [
                {"id": "965eed44", "cron": "23 * * * *", "prompt": hourly},
                {"id": "aabb0011", "cron": "25 * * * *", "prompt": hourly[:60] + "another report"},
                {"id": "aabbccdd", "cron": "0 0 * * *", "prompt": "Only in the carry"}]}},
            stop(hourly[:60] + " different suffix… "), stop(hourly[:60] + " different suffix… "),
            stop("Check the overnight run …"), stop("Only in the carry"), stop("Missing prompt …"),
            {"type": "assistant", "message": {"content": [create("Missing prompt appears later", "0 8 * * *")]}},
        ]
        with tempfile.TemporaryDirectory() as d:
            transcript = os.path.join(d, "s.jsonl")
            with open(transcript, "w") as fh:
                fh.write("partial JSON\n")
                fh.writelines(json.dumps(row) + "\n" for row in rows)
            tasks = of_capacity.stopped_tasks(transcript, since)
            self.assertEqual(tasks, [
                {"cron": "23 * * * *", "recurring": True, "prompt": hourly, "id": "965eed44"},
                {"cron": "52 11 21 11 *", "recurring": False, "prompt": wake, "id": None},
                {"cron": "0 0 * * *", "recurring": True, "prompt": "Only in the carry", "id": "aabbccdd"},
                {"prompt": "Missing prompt", "unmatched": True}])
            note = os.path.join(d, "note.md")
            of_capacity._write_handover_note(note, "Earlier note\n", tasks, log=lambda _: None)
            with open(note) as fh:
                text = fh.read()
            self.assertIn("Earlier note\n\nThe exit stopped these scheduled tasks", text)
            self.assertIn("`23 * * * *` (recurring, old ID `965eed44`)", text)
            self.assertIn("The full prompt was not found in the transcript. Recorded prefix:\n\n  ```\n  Missing prompt", text)


class Balance(unittest.TestCase):
    NOW = 1_800_000_000

    def rows(self, e_week_reset_h=7, claude_week_reset_h=110):
        iso = lambda h: of_capacity._iso(self.NOW + h * 3600)  # noqa: E731
        return [
            {"config_dir": "/ez", "logged_in": True, "status": "allowed", "windows": {
                "five_hour": {"utilization": 22, "resets_at": iso(2)},
                "seven_day": {"utilization": 6, "resets_at": iso(e_week_reset_h)}}},
            {"config_dir": "/cl", "logged_in": True, "status": "allowed", "windows": {
                "seven_day": {"utilization": 10, "resets_at": iso(claude_week_reset_h)}}},
            {"config_dir": "/gg", "logged_in": True, "status": "rejected", "windows": {
                "seven_day": {"utilization": 100, "resets_at": iso(48)}}}]

    def setUp(self):
        orig = self.rows

        def rows(*a, **k):  # every account a Max 20x plan, as ours are
            return [dict(r, tier="default_claude_max_20x") for r in orig(*a, **k)]
        self.rows = rows

    POS = [{"name": n, "holder": {"session": n}} for n in ("a", "b", "c", "k8s_keeper", "of_x", "nonce")]
    SESS = {"/ez": [{"session": "of_x", "status": "idle"}],
            "/cl": [{"session": "a", "status": "idle"}, {"session": "b", "status": "busy"},
                    {"session": "c", "status": "idle"}, {"session": "k8s_keeper", "status": "idle"}],
            "/gg": [{"session": "nonce", "status": "busy"}]}  # mid-turn: never moved, even off a stalled account
    RATES = {"of_x": 78, "a": 50, "b": 30, "c": 3, "k8s_keeper": 40}

    def test_policy_supplies_a_tier_when_status_has_none(self):
        row = dict(_row("/a", 0, 0), tier=None)
        of_capacity._apply_policy_tiers([row], {"tiers": {"/a": "default_claude_max_5x"}})
        self.assertEqual(row["tier"], "default_claude_max_5x")

    def test_model_fits(self):
        fable, pro = "claude-fable-5-2", "default_claude_ai"
        self.assertFalse(of_capacity.model_fits(fable, pro))
        self.assertTrue(of_capacity.model_fits(fable, "default_claude_max_20x"))
        self.assertTrue(of_capacity.model_fits("claude-opus-5-5", pro))
        self.assertTrue(of_capacity.model_fits(None, pro))
        self.assertTrue(of_capacity.model_fits(fable, None))
        self.assertTrue(of_capacity.model_fits(fable, "unknown"))

    def test_fill_the_account_that_expires_first(self):
        pol = dict(POLICY)
        m = of_capacity.balance_plan(self.rows(), self.SESS, self.POS, self.RATES, {"/ez": 12.0}, pol, self.NOW)
        self.assertEqual([(x["position"], x["from"], x["to"]) for x in m], [("a", "/cl", "/ez")])
        # busy b, cheap c (under min_rate), held k8s_keeper and the mid-turn nonce stay
        m = of_capacity.balance_plan(self.rows(), self.SESS, self.POS, self.RATES, {"/ez": 12.0}, pol, self.NOW,
                                     skip={"a"})
        self.assertEqual(m, [])  # moved recently: no ping-pong

    def test_never_past_the_window(self):
        pol = dict(POLICY)
        rows = self.rows()
        rows[0]["windows"]["five_hour"]["utilization"] = 75  # projected 75 + 78*2/12 = 88 > 85: no room
        m = of_capacity.balance_plan(rows, self.SESS, self.POS, self.RATES, {"/ez": 12.0}, pol, self.NOW)
        self.assertEqual(m, [])

    def test_relief_when_about_to_stall(self):
        pol = dict(POLICY)
        rows = self.rows()
        rows[0]["windows"]["five_hour"]["utilization"] = 96
        m = of_capacity.balance_plan(rows, self.SESS, self.POS, self.RATES, {"/ez": 12.0}, pol, self.NOW)
        self.assertEqual([(x["position"], x["to"]) for x in m], [("of_x", "/cl")])
        self.assertIn("relief", m[0]["reason"])

    def test_relief_prefers_a_max_20x_with_room_to_a_fresh_pro(self):
        rows = [_row("/gg", 0, 100), dict(_row("/pro", 0, 0), tier="default_claude_ai"),
                dict(_row("/max", 0, 31), tier="default_claude_max_20x")]
        sessions = {"/gg": [{"session": "c", "status": "idle"}]}
        m = of_capacity.balance_plan(rows, sessions, self.POS, self.RATES, {"/pro": 0.6, "/max": 12.0}, POLICY, self.NOW)
        self.assertEqual([(x["position"], x["to"]) for x in m], [("c", "/max")])
        self.assertIn("relief", m[0]["reason"])

    def test_relief_skips_pro_for_fable(self):
        source = _row("/gg", 0, 98)
        pro = dict(_row("/pro", 0, 0), tier="default_claude_ai")
        max20 = dict(_row("/max", 0, 96), tier="default_claude_max_20x")  # the fresh Pro ranks first
        sessions = {"/gg": [{"session": "c", "status": "waiting"}]}
        for model, targets, expected in (("claude-fable-5-2", [pro, max20], ["/max"]),
                                         ("claude-fable-5-2", [pro], []),
                                         ("claude-opus-5-5", [pro], ["/pro"])):
            with self.subTest(model=model, targets=targets):
                moves = of_capacity.decide([source] + targets, sessions, self.POS, self.RATES,
                                           {"/pro": 0.6, "/max": 12.0}, POLICY, self.NOW, models={"c": model})
                self.assertEqual([m["to"] for m in moves], expected)
                self.assertTrue(all(m["reason"].startswith("relief:") for m in moves))

    def test_after_a_reset_the_load_follows_the_next_expiry(self):
        pol = dict(POLICY)
        rows = self.rows(e_week_reset_h=170, claude_week_reset_h=100)  # e just reset: ~/.claude expires first
        m = of_capacity.balance_plan(rows, self.SESS, self.POS, self.RATES, {"/ez": 12.0}, pol, self.NOW)
        self.assertEqual([(x["position"], x["from"], x["to"]) for x in m], [("of_x", "/ez", "/cl")])

    def test_remote_arrivals_take_the_same_room_as_local_moves_in_relief_and_fill(self):
        rows = self.rows()[:2]
        sink, source = rows
        sink["account"] = "shared@acme.example"
        sink["windows"]["five_hour"]["utilization"] = 70  # 15 points at $2 per point over two hours
        sessions = {"/cl": [{"session": s, "status": "idle"} for s in ("a", "c")]}
        rates = {"a": 7, "c": 6}
        for kind, utilization in (("fill", 10), ("relief", 98)):
            source["windows"]["seven_day"]["utilization"] = utilization
            args = rows, sessions, self.POS, rates, {"/ez": 2}, POLICY, self.NOW
            local = of_capacity.balance_plan(*args)
            self.assertEqual([(m["position"], m["reason"].split(":")[0]) for m in local], [("a", kind), ("c", kind)])
            peer = of_capacity.decide(*args, remote_moves={sink["account"]: 1})
            self.assertEqual([m["position"] for m in peer], ["a"])
            self.assertEqual(of_capacity.decide(*args, remote_moves={sink["account"]: 2}), [])
        rows.append(dict(source, config_dir="/second"))
        sessions["/second"] = [sessions["/cl"].pop()]
        peer = of_capacity.decide(*args, remote_moves={sink["account"]: 1})
        self.assertEqual([m["position"] for m in peer], ["a"])


class FiveHourRelief(unittest.TestCase):
    """2026-11-10: two idle sessions stood on a Max 5x account's 5-hour limit (week 55%) while two Max 20x accounts
    had 35 and 19 points of their week left. Relief found no room: the 20x accounts report no 5-hour window, the
    tier prior from the 5x one said $376 a window against their $100-115/h, and the target had to last a day."""
    NOW = Balance.NOW

    def rows(self, five=100, week=55):
        iso = lambda h: of_capacity._iso(self.NOW + h * 3600)  # noqa: E731
        return [
            {"config_dir": "/mm", "logged_in": True, "status": "rejected", "tier": "default_claude_max_5x", "windows": {
                "five_hour": {"utilization": five, "resets_at": iso(0.33)},
                "seven_day": {"utilization": week, "resets_at": iso(124)}}},
            {"config_dir": "/gg", "logged_in": True, "status": "allowed", "tier": "default_claude_max_20x", "windows": {
                "seven_day": {"utilization": 59, "resets_at": iso(113)}}}]

    POS = [{"name": n, "holder": {"session": n}} for n in ("st", "w1", "w2")]
    SESS = {"/mm": [{"session": "st", "status": "idle"}],
            "/gg": [{"session": "w1", "status": "busy"}, {"session": "w2", "status": "busy"}]}
    RATES = {"st": 8, "w1": 60, "w2": 55}

    def test_a_session_on_a_spent_five_hour_window_moves_to_an_account_with_week_left(self):
        pol = dict(POLICY)
        m = of_capacity.balance_plan(self.rows(), self.SESS, self.POS, self.RATES, {"/mm": 0.94}, pol, self.NOW,
                                     wcalib={"/gg": 28.8, "/mm": 7.2})
        self.assertEqual([(x["position"], x["to"]) for x in m], [("st", "/gg")])
        self.assertIn("relief", m[0]["reason"])

    def test_a_spent_week_still_needs_a_target_that_lasts_a_day(self):
        pol = dict(POLICY)
        rows = self.rows(five=40, week=100)  # $115/h for 24 h is more than the 35 points left on /gg
        m = of_capacity.balance_plan(rows, self.SESS, self.POS, self.RATES, {"/mm": 0.94}, pol, self.NOW,
                                     wcalib={"/gg": 28.8, "/mm": 7.2})
        self.assertEqual(m, [])

    def test_an_account_without_a_measured_week_is_sized_from_the_others_week(self):
        # 2026-11-12 (AfterAWeekReset): a week just reset has no measure of its own; the others' week, scaled by tier,
        # sizes it (here /mm's $7.2 per 1% times 4 is /gg's own $28.8). With no week measured anywhere, no room.
        pol = dict(POLICY)
        m = of_capacity.balance_plan(self.rows(), self.SESS, self.POS, self.RATES, {"/mm": 0.94}, pol, self.NOW,
                                     wcalib={"/mm": 7.2})
        self.assertEqual([(x["position"], x["to"]) for x in m], [("st", "/gg")])
        m = of_capacity.balance_plan(self.rows(), self.SESS, self.POS, self.RATES, {"/mm": 0.94}, pol, self.NOW,
                                     wcalib={})
        self.assertEqual(m, [])


class Rescue(unittest.TestCase):
    """The invariant (founder, 2026-11-10): no session stands on a usage limit while another account accepts work.
    Stops are observed in the transcript; no prediction vetoes the move."""
    NOW = Balance.NOW
    iso = staticmethod(lambda h: of_capacity._iso(Balance.NOW + h * 3600))

    def rows(self, src_seen_h=-0.1):
        return [
            {"config_dir": "/mm", "logged_in": True, "status": "rejected", "tier": "default_claude_max_5x",
             "observed_at": self.iso(src_seen_h), "windows": {
                 "five_hour": {"utilization": 100, "status": "rejected", "resets_at": self.iso(4.5)},
                 "seven_day": {"utilization": 55, "resets_at": self.iso(124)}}},
            {"config_dir": "/gg", "logged_in": True, "status": "allowed", "tier": "default_claude_max_20x",
             "observed_at": self.iso(-0.1), "windows": {"seven_day": {"utilization": 80, "resets_at": self.iso(113)}}},
            {"config_dir": "/ap", "logged_in": True, "status": "allowed", "tier": "default_claude_max_20x",
             "observed_at": self.iso(-0.1), "windows": {"seven_day": {"utilization": 60, "resets_at": self.iso(90)}}},
            {"config_dir": "/cl", "logged_in": True, "status": "rejected", "tier": "default_claude_max_20x",
             "observed_at": self.iso(-0.1), "windows": {
                 "seven_day": {"utilization": 100, "status": "rejected", "resets_at": self.iso(25)}}}]

    POS = [{"name": n, "holder": {"session": n}} for n in ("st1", "st2", "idle", "busy", "k8s_keeper")]
    STOPS_ONLY = dict(POLICY, rescue_ahead_min=10 ** 6)  # tests about stops: no move ahead of a stop
    SESS = {"/mm": [{"session": "st1", "status": "idle"}, {"session": "st2", "status": "idle"},
                    {"session": "idle", "status": "idle"}, {"session": "busy", "status": "busy"}],
            "/gg": [], "/ap": [], "/cl": [{"session": "k8s_keeper", "status": "idle"}]}

    def test_a_probe_with_no_reading_moves_nothing_ahead(self):
        # 2026-11-11, the founder's Mac on a mobile hotspot: a dropped link makes a probe fail (logged_in None). That is
        # no evidence the account refuses work, so its idle holders are not moved ahead; a stopped one still goes.
        rows = self.rows()
        rows[0] = {"config_dir": "/mm", "logged_in": None, "status": None, "windows": {}, "observed_at": None,
                   "error": "probe timed out after 60s"}
        pol = dict(POLICY, rescue_ahead_min=60)
        m = of_capacity.rescue_plan(rows, self.SESS, self.POS, {}, pol, self.NOW)
        self.assertEqual([x["from"] for x in m], ["/cl"])  # /cl's refusal is a reading: its holder goes ahead
        m = of_capacity.rescue_plan(rows, self.SESS, self.POS, {"st1": self.NOW - 600}, pol, self.NOW)
        self.assertEqual([x["position"] for x in m if x["from"] == "/mm"], ["st1"])
        self.assertNotIn("/mm", of_capacity.accepting_accounts(rows, self.SESS, {}, self.NOW))  # nor a target
        self.assertEqual(of_capacity.proposals(rows, set(), now=self.NOW, policy=pol), [])  # nor a card

    def test_a_run_reads_the_sessions_of_an_account_whose_probe_has_no_reading(self):
        # 2026-11-15: an account's week ran out and its probes timed out for half an hour; the run read sessions only
        # on accounts with a reading, so the dozen stopped there were not seen and nothing was rescued
        rows = self.rows()
        rows[0] = {"config_dir": "/mm", "logged_in": None, "status": None, "windows": {},
                   "observed_at": self.iso(0), "error": "probe timed out after 120s"}  # probed after the stop
        rows.append({"config_dir": "/out", "logged_in": False, "windows": {}, "observed_at": self.iso(0)})
        self.assertEqual(of_capacity.run_dirs(rows), ["/mm", "/gg", "/ap", "/cl"])  # a logged-out one only is left out
        m = of_capacity.rescue_plan(rows, self.SESS, self.POS, {"st1": self.NOW - 600}, dict(self.STOPS_ONLY),
                                    self.NOW)
        self.assertEqual([(x["position"], x["from"]) for x in m], [("st1", "/mm")])

    def test_stopped_sessions_go_to_accounts_that_accept_work_most_headroom_first(self):
        pol = dict(self.STOPS_ONLY, rescue_step=30)
        stopped = {"st1": self.NOW - 600, "st2": self.NOW - 300, "busy": self.NOW - 60}
        m = of_capacity.rescue_plan(self.rows(), self.SESS, self.POS, stopped, pol, self.NOW)
        # oldest stop first; /ap has the most headroom, then the step spreads the second to /gg; never /cl
        self.assertEqual([(x["position"], x["to"]) for x in m], [("st1", "/ap"), ("st2", "/gg")])
        self.assertTrue(all(x["reason"].startswith("rescue:") for x in m))
        # not stopped, or mid-turn: not a rescue (relief and fill decide about them)
        self.assertNotIn("idle", [x["position"] for x in m])
        self.assertNotIn("busy", [x["position"] for x in m])

    def test_rescue_skips_fable_when_only_pro_accepts(self):
        rows = self.rows()[:2]
        rows[1]["tier"] = "default_claude_ai"
        stopped = {"st1": self.NOW - 600, "st2": self.NOW - 300}
        moves = of_capacity.rescue_plan(rows, self.SESS, self.POS, stopped, dict(self.STOPS_ONLY, rescue_moves=1),
                                        self.NOW, models={"st1": "claude-fable-5-2", "st2": "claude-opus-5-5"})
        self.assertEqual([(m["session"], m["to"]) for m in moves], [("st2", "/gg")])

    def test_rescue_counts_recent_remote_moves_onto_the_same_account(self):
        import of_events
        import of_store
        rows = self.rows()
        account = rows[2]["account"] = "shared@acme.example"
        pol = dict(self.STOPS_ONLY, rescue_step=30)
        with tempfile.TemporaryDirectory() as state, mock.patch.object(of_events, "STATE_DIR", state):
            conn = of_events._conn()
            me = of_events.machine_id()

            def move(host, age, target, previous=None):
                payload = {"workplace": {"account": target, "config_dir": "/remote"},
                           "previous_workplace": {"account": previous, "config_dir": "/source"}}
                rec = of_events.record("holder_changed", payload, org="acme",
                                       extra={"host.id": host, "timestamp": of_events.now_iso(self.NOW - age)})
                of_store.add_event(conn, rec)

            def target():
                moves = of_capacity.rescue_plan(rows, self.SESS, self.POS, {"st1": self.NOW - 600}, pol, self.NOW,
                                                remote_moves=of_capacity.recent_remote_moves(self.NOW, conn))
                return moves[0]["to"]

            move(me, 60, account)
            move("peer", 901, account)
            move("peer", 60, "other@acme.example")
            move("peer", 60, account, previous=account)
            self.assertEqual(target(), "/ap")
            move("peer", 900, account)
            self.assertEqual(target(), "/gg")

    def test_a_fresh_small_account_does_not_outrank_a_large_one_with_week_left(self):
        rows = self.rows()[:2] + [{"config_dir": "/pro", "logged_in": True, "status": "allowed",
                                   "tier": "default_claude_ai", "observed_at": self.iso(-0.1),
                                   "windows": {"seven_day": {"utilization": 0, "resets_at": self.iso(150)}}}]
        sess = dict(self.SESS, **{"/pro": []})
        m = of_capacity.rescue_plan(rows, sess, self.POS, {"st1": self.NOW - 600}, dict(self.STOPS_ONLY),
                                    self.NOW, wcalib={"/gg": 29.0})
        self.assertEqual([x["to"] for x in m], ["/gg"])  # 20 points of an Ultra beat a whole Pro week

    def test_production_first_and_a_cap_per_run(self):
        pol = dict(POLICY, rescue_moves=1)
        stopped = {"st1": self.NOW - 600, "k8s_keeper": self.NOW - 60}
        m = of_capacity.rescue_plan(self.rows(), self.SESS, self.POS, stopped, pol, self.NOW)
        self.assertEqual([x["position"] for x in m], ["k8s_keeper"])

    def test_a_stop_newer_than_the_probe_blocks_an_account_the_probe_calls_allowed(self):
        rows = self.rows()
        rows[0].update(status="allowed", windows={"five_hour": {"utilization": 92, "resets_at": self.iso(4.5)},
                                                  "seven_day": {"utilization": 55, "resets_at": self.iso(124)}})
        rows[0]["observed_at"] = self.iso(-0.4)  # probed 24 minutes ago; the session stopped 5 minutes ago
        stopped = {"st1": self.NOW - 300}
        m = of_capacity.rescue_plan(rows, self.SESS, self.POS, stopped, dict(self.STOPS_ONLY), self.NOW)
        self.assertEqual([x["position"] for x in m], ["st1"])
        # a probe after the stop that says allowed: the window reset, the session can go on where it is
        rows[0]["observed_at"] = self.iso(0)
        self.assertEqual(of_capacity.rescue_plan(rows, self.SESS, self.POS, stopped, dict(self.STOPS_ONLY),
                                                 self.NOW), [])

    def test_a_holder_between_turns_on_an_account_that_refuses_work_goes_before_it_stops(self):
        # 2026-11-11: d's week ran out at ~06:20Z; storefront-hosts_keeper (production) sat idle there, a check due 06:48Z
        stopped = {"st1": self.NOW - 600}
        m = of_capacity.rescue_plan(self.rows(), self.SESS, self.POS, stopped, dict(POLICY), self.NOW)
        # the stopped one first, then production, then the rest; mid-turn never
        self.assertEqual([x["position"] for x in m], ["st1", "k8s_keeper", "idle", "st2"])
        self.assertIn("stopped on its account's usage limit", m[0]["reason"])
        self.assertIn("does not accept work until " + self.iso(25), m[1]["reason"])
        self.assertTrue(all(x["to"] in ("/ap", "/gg") for x in m))
        # a window that reopens within rescue_ahead_min: only the stopped one moves
        rows = self.rows()
        rows[0]["windows"]["five_hour"]["resets_at"] = self.iso(0.5)
        rows[3]["windows"]["seven_day"]["resets_at"] = self.iso(0.9)
        m = of_capacity.rescue_plan(rows, self.SESS, self.POS, stopped, dict(POLICY), self.NOW)
        self.assertEqual([x["position"] for x in m], ["st1"])
        # the cap counts them too, the stopped ones taking the places first
        m = of_capacity.rescue_plan(self.rows(), self.SESS, self.POS, stopped,
                                    dict(POLICY, rescue_moves=2), self.NOW)
        self.assertEqual([x["position"] for x in m], ["st1", "k8s_keeper"])

    def test_no_account_accepts_work_nothing_to_do_and_no_stall_reported(self):
        rows = [r for r in self.rows() if r["config_dir"] in ("/mm", "/cl")]
        stopped = {"st1": self.NOW - 600}
        self.assertEqual(of_capacity.rescue_plan(rows, self.SESS, self.POS, stopped, dict(POLICY),
                                                 self.NOW), [])
        self.assertEqual(of_capacity.stall_report(rows, self.SESS, self.POS, stopped, [], self.NOW), [])

    def test_rescue_and_invariant_share_the_same_stop_after_a_wake(self):
        rows = self.rows()[:3]
        rows[0].update(status="allowed", windows={}, observed_at=self.iso(0))
        stopped = {"st1": self.NOW - 3600, "st2": self.NOW - 3600}
        pol = dict(self.STOPS_ONLY, hold=["st2"], rescue_moves=1)
        wake_at = self.NOW - 300
        result = "deferred: a dialog is open in the pane"
        woken = {sid: {"at": wake_at, "dir": "/mm", "result": result} for sid in stopped}
        moves = of_capacity.decide(rows, self.SESS, self.POS, {}, {}, pol, self.NOW, stopped=stopped, woken=woken)
        self.assertEqual([(m["session"], m["to"]) for m in moves], [("st2", "/ap")])
        reason = f"woken on its account at {of_capacity._iso(wake_at)}, still stopped ({result})"
        self.assertIn("rescue: " + reason, moves[0]["reason"])
        self.assertIn(reason, of_capacity.balance_why(moves[0]["reason"]))
        report = of_capacity.stall_report(rows, self.SESS, self.POS, stopped, moves, self.NOW, woken=woken)
        self.assertEqual([(s["session"], s["why"]) for s in report], [("st1", reason), ("st2", reason)])
        moves[0]["done"] = True
        report = of_capacity.stall_report(rows, self.SESS, self.POS, stopped, moves, self.NOW, woken=woken)
        self.assertEqual([s["session"] for s in report], ["st1"])

        for outcome, age, stop, other, expected in (
                ("delivered", 300, self.NOW - 3600, True, False),
                ("delivered", of_capacity.WAKE_AGAIN_S + 1, self.NOW - 3600, True, True),
                (result, 300, wake_at, True, True),
                (result, 300, wake_at + 1, True, False),
                (result, 300, None, True, False),
                (result, 300, wake_at, False, False)):
            with self.subTest(outcome=outcome, age=age, stop=stop, other=other):
                pool = rows if other else rows[:1]
                stops = {"st1": stop} if stop else {}
                history = {"st1": {"dir": "/mm", "at": self.NOW - age, "result": outcome}}
                moves = of_capacity.rescue_plan(pool, self.SESS, self.POS, stops, pol, self.NOW, woken=history)
                stalls = of_capacity.stall_report(pool, self.SESS, self.POS, stops, [], self.NOW, woken=history)
                self.assertEqual(([m["to"] for m in moves], [s["session"] for s in stalls]),
                                 (["/ap"], ["st1"]) if expected else ([], []))

    def test_a_window_whose_reset_passed_stops_nothing(self):
        r = self.rows()[0]
        self.assertGreater(of_capacity.blocked_until(r, self.NOW), self.NOW)
        self.assertEqual(of_capacity.blocked_until(r, self.NOW + 5 * 3600), 0.0)
        self.assertEqual(of_capacity.blocked_until(dict(r, status="rejected", windows={}), self.NOW), float("inf"))

    def test_stall_report_names_what_is_still_stopped_and_why(self):
        stopped = {"st1": self.NOW - 600, "busy": self.NOW - 60}
        moves = [{"session": "st1", "position": "st1", "done": False, "error": "a dialog"}]
        rep = of_capacity.stall_report(self.rows(), self.SESS, self.POS, stopped, moves, self.NOW)
        self.assertEqual(sorted((x["position"], x["why"]) for x in rep),
                         [("busy", "session status busy"), ("st1", "move failed: a dialog")])
        moves[0]["done"] = True
        rep = of_capacity.stall_report(self.rows(), self.SESS, self.POS, stopped, moves, self.NOW)
        self.assertEqual([x["position"] for x in rep], ["busy"])

    def test_decide_rescues_what_the_room_estimate_would_leave(self):
        # the targets' predicted week room is short for the 4.5 h until the source reopens: relief alone moves
        # nothing, rescue moves the stopped holder anyway
        rows = self.rows()
        rows[2]["windows"]["seven_day"]["utilization"] = 82
        sess = dict(self.SESS, **{"/gg": [{"session": "w", "status": "busy"}], "/ap": [{"session": "v", "status": "busy"}]})
        rates, wcal = {"w": 150, "v": 150, "st1": 8}, {"/gg": 30.0, "/ap": 30.0, "/mm": 7.2}
        pol = dict(self.STOPS_ONLY)
        self.assertEqual(of_capacity.balance_plan(rows, sess, self.POS, rates, {"/mm": 0.94}, pol, self.NOW,
                                                  wcalib=wcal), [])
        m = of_capacity.decide(rows, sess, self.POS, rates, {"/mm": 0.94}, pol, self.NOW,
                               stopped={"st1": self.NOW - 600}, wcalib=wcal)
        self.assertEqual([x["position"] for x in m], ["st1"])

    def test_invariant_step_events_escalation_and_the_pool(self):
        now, st = self.NOW, {}
        stall = [{"session": "s1", "position": "p1", "account": "/mm", "since": "t", "why": "session status busy"}]
        # 1st run with a stall: one event, with the accounts that accepted work
        st, acts = of_capacity.invariant_step(st, stall, ["/gg"], [], [], now)
        self.assertEqual([k for k, _ in acts], ["event"])
        self.assertEqual(acts[0][1]["accepting"], ["/gg"])
        # 2nd run, the same stall: no new event, work for of_capacity once
        st, acts = of_capacity.invariant_step(st, stall, ["/gg"], [], [], now + 300)
        self.assertEqual([k for k, _ in acts], ["self"])
        self.assertIn("p1", acts[0][1])
        st, acts = of_capacity.invariant_step(st, stall, ["/gg"], [], [], now + 600)
        self.assertEqual(acts, [])  # not again every run
        # it clears: the empty set is recorded too, so the stall's duration is measurable
        st, acts = of_capacity.invariant_step(st, [], ["/gg"], [], [], now + 900)
        self.assertEqual(acts, [("event", {"stalled": [], "accepting": ["/gg"]})])
        # a session rescued twice within 5 hours: the pool is short, work for of_capacity once per window
        st, acts = of_capacity.invariant_step(st, [], ["/gg"], [("s2", "p2")], [], now + 1000)
        self.assertEqual(acts, [])
        st, acts = of_capacity.invariant_step(st, [], ["/gg"], [("s2", "p2")], [], now + 4000, numbers="- /gg: week 90%")
        self.assertEqual([k for k, _ in acts], ["pool"])
        self.assertIn("short", acts[0][1])
        self.assertIn("- /gg: week 90%", acts[0][1])
        st, acts = of_capacity.invariant_step(st, [], [], [], ["s3"], now + 5000)
        self.assertEqual(acts, [])  # spent, but already told within this window
        st, acts = of_capacity.invariant_step(st, [], [], [], ["s3"], now + 4000 + 5 * 3600)
        self.assertEqual([k for k, _ in acts], ["pool"])
        self.assertIn("spent", acts[0][1])
        # a rescue more than 5 hours after the last one is not "short"
        st, acts = of_capacity.invariant_step({}, [], ["/gg"], [("s4", "p4")], [], now)
        st, acts = of_capacity.invariant_step(st, [], ["/gg"], [("s4", "p4")], [], now + 5 * 3600 + 1)
        self.assertEqual(acts, [])

    def test_limit_stop_reads_the_synthetic_limit_message(self):
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, "s.jsonl")
            ok = {"type": "assistant", "timestamp": "2026-11-10T20:00:00Z", "message": {"model": "claude-opus-5-5",
                  "content": [{"type": "text", "text": "done"}]}}
            stop = {"type": "assistant", "timestamp": "2026-11-10T20:08:15Z", "isApiErrorMessage": True,
                    "error": "rate_limit", "apiError": "usage_limit_reached", "message": {"model": "<synthetic>",
                    "content": [{"type": "text", "text": "You've hit your 5-hour limit · resets 12:30am"}]}}
            note = {"type": "system", "subtype": "informational", "timestamp": "2026-11-10T20:08:16Z",
                    "content": "Usage limit reached · continuing automatically at 12:30am · esc to cancel"}
            side = dict(ok, isSidechain=True)
            with open(t, "w") as fh:
                fh.write("\n".join(json.dumps(x) for x in (ok, stop, note, side)) + "\n")
            self.assertEqual(of_capacity.limit_stop(t), of_capacity._ts("2026-11-10T20:08:15Z"))
            with open(t, "a") as fh:  # a reply after it: no longer stopped
                fh.write(json.dumps(dict(ok, timestamp="2026-11-10T20:31:00Z")) + "\n")
            self.assertIsNone(of_capacity.limit_stop(t))
        self.assertIsNone(of_capacity.limit_stop("/nonexistent"))


class Spend(unittest.TestCase):
    def _write(self, path, rows):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def rec(self, mid, ts, inp=10, out=100, cr=1000, cw=2000):
        u = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr,
             "cache_creation_input_tokens": cw, "cache_creation": {"ephemeral_1h_input_tokens": cw,
                                                                   "ephemeral_5m_input_tokens": 0}}
        return {"type": "assistant", "timestamp": ts, "message": {"id": mid, "usage": u, "content": []}}

    def test_one_response_counts_once_and_the_index_is_incremental(self):
        with tempfile.TemporaryDirectory() as home:
            of_capacity.STATE_DIR = os.path.join(home, "state")
            d = os.path.join(home, ".claude-a")
            f = os.path.join(d, "projects", "-w", "s1.jsonl")
            now = of_capacity._ts("2026-11-06T10:00:00Z")
            # three content blocks of one response, same id and usage; plus a subagent's response
            self._write(f, [self.rec("m1", "2026-11-06T09:50:00Z")] * 3)
            self._write(os.path.join(d, "projects", "-w", "s1", "subagents", "a.jsonl"),
                        [self.rec("m2", "2026-11-06T09:55:00Z")])
            u = of_capacity.usage([d])
            self.assertEqual(u["s1"]["responses"], 2)
            self.assertEqual(u["s1"]["fresh"], 2 * (10 + 100 + 2000))
            self.assertEqual(u["s1"]["cache_read"], 2000)
            one = of_capacity.usage_cost(self.rec("x", "")["message"]["usage"])
            self.assertAlmostEqual(of_capacity.spend_since(d, now - 3600, of_capacity.spend_index([d], now))["s1"],
                                   2 * one)
            # appended later: only the new bytes are read, the old response is not counted twice
            self._write(f, [self.rec("m3", "2026-11-06T09:58:00Z")])
            idx = of_capacity.spend_index([d], now)
            self.assertAlmostEqual(of_capacity.spend_since(d, now - 3600, idx)["s1"], 3 * one)
            self.assertEqual(len(idx), 3)
            # outside the window
            self.assertEqual(of_capacity.spend_since(d, now - 60, idx), {})
            # a copy made by a move counts only from its copy time on (the rest was spent on the old account)
            of_capacity.mark_copied([f], of_capacity._ts("2026-11-06T09:57:00Z"))
            idx = of_capacity.spend_index([d], now)
            self.assertAlmostEqual(of_capacity.spend_since(d, now - 3600, idx)["s1"], 2 * one)  # m3 and m2 (subagent)

    def test_a_fork_does_not_take_its_sessions_responses(self):
        # a side question forks the session: the fork's transcript repeats the session's records under its id
        with tempfile.TemporaryDirectory() as home:
            of_capacity.STATE_DIR = os.path.join(home, "state")
            d = os.path.join(home, ".claude-a")
            now = of_capacity._ts("2026-11-06T13:00:00Z")
            orig, fork = (os.path.join(d, "projects", "-w", f"{x}.jsonl") for x in ("s-orig", "s-fork"))
            self._write(orig, [self.rec("m1", "2026-11-06T12:40:00Z"), self.rec("m2", "2026-11-06T12:41:00Z")])
            of_capacity.spend_index([d], now)
            time_ = __import__("time")
            time_.sleep(1.1)  # the fork's file is born later
            self._write(fork, [self.rec("m1", "2026-11-06T12:40:00Z"), self.rec("m2", "2026-11-06T12:41:00Z"),
                               self.rec("m9", "2026-11-06T12:46:00Z")])  # m9: the fork's own answer
            idx = of_capacity.spend_index([d], now)
            self.assertEqual({k: v[1] for k, v in idx.items()}, {"m1": "s-orig", "m2": "s-orig", "m9": "s-fork"})
            # the session writes on after the fork: still its own, still counted once
            self._write(orig, [self.rec("m3", "2026-11-06T12:47:00Z")])
            idx = of_capacity.spend_index([d], now)
            self.assertEqual(sorted(k for k, v in idx.items() if v[1] == "s-orig"), ["m1", "m2", "m3"])

    def test_a_moved_session_burns_on_both_accounts(self):
        # found by the Muse cross-review trial (2026-11-06): the last account's rate overwrote the first's
        now = of_capacity._ts("2026-11-06T13:00:00Z")
        idx = {"m1": [now - 5400, "s1", 20.0, "/a", False], "m2": [now - 600, "s1", 6.0, "/b", False]}
        rates = of_capacity.burn_rates(["/a", "/b"], now, idx)
        self.assertAlmostEqual(rates["s1"], max(6.0 * 2, 26.0 / 2))


class FolderTrust(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        h = self.home.name
        self.src, self.dst = os.path.join(h, ".claude-src"), os.path.join(h, ".claude-dst")
        for d in (self.src, self.dst):
            os.makedirs(d)
        self.cwd = os.path.join(h, "work")
        os.makedirs(self.cwd)

    def tearDown(self):
        self.home.cleanup()

    def conf(self, d, projects):
        with open(os.path.join(d, ".claude.json"), "w") as fh:
            json.dump({"oauthAccount": {"emailAddress": "x@y"}, "projects": projects, "other": 1}, fh)

    def test_trusted_target_carried_trust_and_refusal(self):
        self.conf(self.src, {self.cwd: {"hasTrustDialogAccepted": True}})
        self.conf(self.dst, {self.cwd: {"hasTrustDialogAccepted": True}})
        self.assertEqual(of_capacity.ensure_trust(self.src, self.dst, self.cwd), ("trusted", self.cwd))
        self.conf(self.dst, {})
        self.assertEqual(of_capacity.ensure_trust(self.src, self.dst, self.cwd, dry_run=True), ("carried", self.cwd))
        self.assertIsNone(of_capacity.trusted_key(self.dst, self.cwd))  # a dry run writes nothing
        self.assertEqual(of_capacity.ensure_trust(self.src, self.dst, self.cwd), ("carried", self.cwd))
        with open(os.path.join(self.dst, ".claude.json")) as fh:
            conf = json.load(fh)
        self.assertTrue(conf["projects"][self.cwd]["hasTrustDialogAccepted"])
        self.assertEqual(conf["other"], 1)  # the rest of the config is kept
        self.conf(self.src, {})
        self.conf(self.dst, {})
        with self.assertRaises(of_capacity.MoveError) as e:
            of_capacity.ensure_trust(self.src, self.dst, self.cwd)
        self.assertEqual(e.exception.untrusted, self.cwd)

    def test_a_worktree_takes_its_main_repository_trust(self):
        repo = os.path.join(self.home.name, "repo")
        git("init", "-q", repo)
        git("-C", repo, "commit", "-q", "--allow-empty", "-m", "x")
        wt = os.path.join(self.home.name, "wt")
        git("-C", repo, "worktree", "add", "-q", wt)
        self.assertIn(os.path.realpath(repo), [os.path.realpath(c) for c in of_capacity.trust_candidates(wt)])


class ListenerProcessor(unittest.TestCase):
    def test_cursor_dedup_and_the_switch_after_backfill(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.executescript("""CREATE TABLE transcript_record (id INTEGER PRIMARY KEY, source_path TEXT, session_id TEXT,
          message_id TEXT, timestamp TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
          cache_write_tokens INTEGER, cache_write_5m_tokens INTEGER, cache_write_1h_tokens INTEGER);
          CREATE TABLE listener_meta (key TEXT PRIMARY KEY, value TEXT);""")
        now = of_capacity._ts("2026-11-06T10:00:00Z")
        ez = "/Users/alex/.claude-e/projects/-w/"

        def add(i, mid, ts, sid="s1", path=None):
            conn.execute("INSERT INTO transcript_record VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (i, path or ez + f"{sid}.jsonl", sid, mid, ts, 10, 100, 1000, 2000, 0, 2000))
        add(1, None, "2026-11-06T09:00:00Z")        # before the backfill: no message id
        add(2, "m2", "2026-11-06T09:30:00Z")
        add(3, "m2", "2026-11-06T09:30:00Z")        # the same response, another content block
        add(4, "m4", "2026-11-06T09:40:00Z", path=ez + "s1/subagents/agent-x.jsonl", sid="agent-x")
        add(5, "m5", "2026-11-06T01:00:00Z")        # older than 6 hours
        p = of_capacity.ListenerProcessor()
        p.reset(conn)
        of_capacity.ingest_spend(conn, now)
        self.assertIsNone(of_capacity.store_index(conn))  # incomplete: the balancer keeps the transcript cursor
        self.assertEqual(sorted(r[0] for r in conn.execute("SELECT message_id FROM view_capacity_spend")), ["m2", "m4"])
        self.assertEqual(conn.execute("SELECT session_id FROM view_capacity_spend WHERE message_id='m4'").fetchone()[0], "s1")
        conn.execute("UPDATE transcript_record SET message_id='m1' WHERE id=1")  # the backfill
        add(6, "m6", "2026-11-06T09:55:00Z")
        of_capacity.ingest_spend(conn, now)
        idx = of_capacity.store_index(conn)
        self.assertEqual(sorted(idx), ["m1", "m2", "m4", "m6"])  # rebuilt once, deduplicated
        self.assertEqual(idx["m1"][3], "/Users/alex/.claude-e")
        one = of_capacity.row_cost(10, 100, 1000, 2000, 0, 2000)
        self.assertAlmostEqual(of_capacity.spend_since("/Users/alex/.claude-e", now - 3600 * 2, idx)["s1"], 4 * one)
        # a fork of s1 (a side question) stops first while s1 is in a long turn: s1's responses stay s1's
        add(7, "m7", "2026-11-06T09:58:00Z", sid="s-fork")  # copied from s1, ingested at the fork's Stop
        add(8, "m8", "2026-11-06T09:59:00Z", sid="s-fork")  # the fork's own answer
        add(9, "m7", "2026-11-06T09:58:00Z")                # s1's own record, at s1's next Stop
        of_capacity.ingest_spend(conn, now)
        owner = dict(conn.execute("SELECT message_id, session_id FROM view_capacity_spend"))
        self.assertEqual((owner["m7"], owner["m8"]), ("s1", "s-fork"))
        add(10, "m7", "2026-11-06T09:58:00Z", sid="s-fork")  # the fork re-read later never takes it back
        of_capacity.ingest_spend(conn, now)
        self.assertEqual(conn.execute("SELECT session_id FROM view_capacity_spend WHERE message_id='m7'").fetchone()[0], "s1")

    def test_the_listener_only_starts_the_tick_and_one_tick_runs_at_a_time(self):
        """of_asks, 2026-11-13: the tick held the listener's loop for 442 s, and no heartbeat was written meanwhile."""
        from unittest import mock
        h = tempfile.mkdtemp()
        try:
            started, worked = [], []
            with mock.patch.object(of_capacity, "STATE_DIR", h), \
                    mock.patch.object(of_capacity.subprocess, "Popen", lambda argv, **k: started.append(argv)), \
                    mock.patch.object(of_capacity, "ingest_spend", lambda *a, **k: worked.append("ingest") or 0):
                os.makedirs(os.path.join(h, "capacity"))
                with open(os.path.join(h, "capacity", "tick.log"), "w") as fh:
                    fh.write("x" * (of_capacity.TICK_LOG_MAX + 1))
                self.assertFalse(of_capacity.ListenerProcessor().tick(None, 0))
                self.assertEqual([a[-2:] for a in started], [["capacity", "tick"]])
                self.assertTrue(os.path.exists(os.path.join(h, "capacity", "tick.log.1")))  # rotated, not grown
                self.assertEqual(worked, [])  # nothing of the work in the listener's own loop
                held = of_capacity._tick_lock()
                self.assertFalse(of_capacity.spawn_tick())  # a tick runs: no second one
                self.assertFalse(of_capacity.run_tick(None, 0, log=lambda *_: None))
                held.close()
                with mock.patch.object(of_capacity, "loop_costs", lambda *a: None), \
                        mock.patch.object(of_capacity, "load_policy", lambda: {"auto": True}):
                    balanced = []
                    self.assertTrue(of_capacity.run_tick(None, 0, log=lambda *_: None,
                                                         spawn=lambda: balanced.append(1)))
                self.assertEqual((worked, balanced), (["ingest"], [1]))
        finally:
            shutil.rmtree(h, ignore_errors=True)

    def test_reads_transcript_own_where_the_store_has_it(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.executescript("""CREATE TABLE transcript_record (id INTEGER PRIMARY KEY, source_path TEXT, session_id TEXT,
          message_id TEXT, timestamp TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
          cache_write_tokens INTEGER, cache_write_5m_tokens INTEGER, cache_write_1h_tokens INTEGER, record_uuid TEXT);
          CREATE TABLE listener_meta (key TEXT PRIMARY KEY, value TEXT);
          CREATE TABLE session_fork (session_id TEXT PRIMARY KEY, parent_session_id TEXT NOT NULL, shared INTEGER,
            method TEXT, detected_at TEXT);
          CREATE VIEW transcript_own AS SELECT t.* FROM transcript_record t WHERE NOT EXISTS (SELECT 1 FROM session_fork f
            JOIN transcript_record p ON p.session_id = f.parent_session_id AND p.record_uuid = t.record_uuid
            WHERE f.session_id = t.session_id);""")
        now = of_capacity._ts("2026-11-06T10:00:00Z")
        ez = "/Users/alex/.claude-e/projects/-w/"

        def add(i, mid, ts, sid, uuid):
            conn.execute("INSERT INTO transcript_record VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (i, ez + f"{sid}.jsonl", sid, mid, ts, 10, 100, 1000, 2000, 0, 2000, uuid))
        conn.execute("INSERT INTO session_fork VALUES ('s-fork', 's1', 1, 'fv-0:birthtime', '')")
        add(1, "m1", "2026-11-06T09:50:00Z", "s1", "u1")
        add(2, "m1", "2026-11-06T09:50:00Z", "s-fork", "u1")   # the fork's copy, ingested later
        add(3, "m2", "2026-11-06T09:55:00Z", "s-fork", "u2")   # the fork's own answer
        of_capacity.ListenerProcessor().reset(conn)
        of_capacity.ingest_spend(conn, now)
        self.assertEqual(dict(conn.execute("SELECT message_id, session_id FROM view_capacity_spend")),
                         {"m1": "s1", "m2": "s-fork"})


if __name__ == "__main__":
    unittest.main()


class DeclaredStart(unittest.TestCase):
    """Every text the tool types into a pane is declared right before (of_platform, 2026-11-07): a note that
    reaches a session undeclared is recorded as the owner's words."""
    def setUp(self):
        from unittest import mock
        self.calls, self.up = [], False
        self.patches = [mock.patch.object(of_capacity.time, "sleep", lambda *_: None),
                        mock.patch.object(of_capacity, "_inject",
                                          lambda sid, text, reason: self.calls.append(("inject", sid, text, reason))),
                        mock.patch.object(of_capacity, "live_sessions",
                                          lambda d, *a: [{"session": "s1", "pid": 7, "name": "runners_keeper",
                                                          "cwd": "/w", "file": __file__}] if self.up else [])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def start(self, screen=()):
        def run():
            self.calls.append(("run",))
        return of_capacity.start_session("s1", "runners_keeper", "/w", "/cfg", "of_capacity moved this session …",
                                         run, lambda: list(screen), lambda k: self.calls.append(("key", k)),
                                         "account move: hand-over note", log=lambda *_: None, wait=0.05, step=0.01)

    def test_the_note_is_declared_before_the_start_types_it(self):
        def run():
            self.calls.append(("run",))
            self.up = True
        new = of_capacity.start_session("s1", "runners_keeper", "/w", "/cfg", "note", run, lambda: [],
                                        lambda k: None, "r", log=lambda *_: None, wait=0.05, step=0.01)
        self.assertEqual(new["session"], "s1")
        self.assertEqual([c[0] for c in self.calls], ["inject", "run"])
        self.assertEqual(self.calls[0][1:3], ("s1", "note"))

    def test_a_start_held_by_the_trust_dialog_is_closed_with_no_exit(self):
        """platform_owner, 2026-11-06: the resume sat on the dialog from 09:27Z until someone passed it at
        12:30Z, and the note arrived three hours after its declaration."""
        with self.assertRaises(of_capacity.MoveError) as e:
            self.start(["\x1b[1mDo you trust the files in this folder?\x1b[0m", " ❯ 1. Yes, I trust this folder",
                        "   2. No, exit"])
        self.assertEqual(e.exception.untrusted, "/w")
        self.assertIn(("key", "Esc"), self.calls)  # "No, exit" grants nothing; Enter would trust the folder
        self.assertNotIn(("key", "Enter"), self.calls)
        self.assertEqual(len([c for c in self.calls if c[0] == "inject"]), 1)

    def test_a_slow_start_is_declared_again(self):
        with self.assertRaises(of_capacity.MoveError):
            self.start(["Resuming conversation…"])
        injects = [c for c in self.calls if c[0] == "inject"]
        self.assertEqual(len(injects), 2)
        self.assertEqual(injects[0][2], injects[1][2])  # the same text, so the hook matches either mark
        self.assertFalse([c for c in self.calls if c[0] == "key"])

    def test_launch_command_carries_the_declared_text(self):
        for resumed in (False, True):
            cmd = of_capacity.launch_command("/w", "/Users/alex/.claude-e", "s1", "n", "/n.md", resumed=resumed)
            self.assertTrue(cmd.endswith(of_capacity._q(of_capacity.launch_prompt("/n.md", resumed))))


class Resume(unittest.TestCase):
    """`of capacity resume` finishes a move whose resume never started, instead of a relaunch typed by hand
    (runners_keeper's note at 12:41Z on 2026-11-06 was recorded as the owner's words)."""
    def setUp(self):
        from unittest import mock
        self.home = tempfile.TemporaryDirectory()
        h = self.home.name
        self.cfg, self.cwd = os.path.join(h, ".claude-e"), os.path.join(h, "acme")
        os.makedirs(os.path.join(self.cfg, "projects", "-acme"))
        os.makedirs(self.cwd)
        with open(os.path.join(self.cfg, "projects", "-acme", "s1.jsonl"), "w") as fh:
            for r in ({"type": "user", "cwd": self.cwd, "permissionMode": "bypassPermissions",
                       "timestamp": "2026-11-06T09:23:00Z"},
                      {"type": "assistant", "cwd": self.cwd, "timestamp": "2026-11-06T09:24:00Z",
                       "message": {"model": "claude-opus-5-5"}}):
                fh.write(json.dumps(r) + "\n")
        self.trust(True)
        self.calls, self.fg, self.up, self.screen = [], [], False, []

        def herdr(*a, timeout=30):
            self.calls.append(a)
            if a[:2] == ("pane", "process-info"):
                return {"process_info": {"foreground_processes": [{"argv": ["claude"]}] if self.fg else []}}
            if a[:2] == ("pane", "send-keys") and a[3] == "esc":
                self.fg = []
            if a[:2] == ("pane", "run"):
                self.up = True
            if a[:2] == ("agent", "list"):
                return {"agents": [{"pane_id": "w6:p18", "agent_session": {"value": "s1"}, "name": "runners_keeper"}]}
            return {}
        self.patches = [mock.patch.object(of_capacity.time, "sleep", lambda *_: None),
                        mock.patch.object(of_capacity, "STATE_DIR", h),
                        mock.patch.object(of_capacity, "config_dirs", lambda *a: [self.cfg]),
                        mock.patch.object(of_capacity, "_herdr", herdr),
                        mock.patch.object(of_capacity, "_pane_lines", lambda *a, **k: list(self.screen)),
                        mock.patch.object(of_capacity, "check_bypass_after_start", lambda *a, **k: "bypass"),
                        # never the real herdr binary: a CI runner has none (PR 204, of_review)
                        mock.patch.object(of_capacity, "ensure_herdr_integration", lambda *a, **k: False),
                        mock.patch.object(of_capacity, "_inject",
                                          lambda sid, text, reason: self.calls.append(("inject", sid, text))),
                        mock.patch.object(of_capacity.of_events, "emit",
                                          lambda kind, payload, **k: self.calls.append(("emit", kind, payload))),
                        mock.patch.object(of_capacity, "live_sessions",
                                          lambda d, *a: [{"session": "s1", "pid": 9, "name": "runners_keeper",
                                                          "cwd": self.cwd, "file": __file__}] if self.up else [])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.home.cleanup()

    def trust(self, yes):
        with open(os.path.join(self.cfg, ".claude.json"), "w") as fh:
            json.dump({"oauthAccount": {"emailAddress": "vp@x"},
                       "projects": {self.cwd: {"hasTrustDialogAccepted": yes}}}, fh)

    def resume(self):
        return of_capacity.resume("runners_keeper", "s1", self.cfg, "w6:p18", log=lambda *_: None)

    def test_closes_a_stale_trust_dialog_then_starts_with_a_declared_note(self):
        self.fg, self.screen = [1], ["   1. Yes, I trust this folder", "   2. No, exit"]
        r = self.resume()
        self.assertEqual(r["session"], "s1")
        kinds = [c[0] if c[0] in ("inject", "emit") else " ".join(c[:2]) for c in self.calls]
        self.assertLess(kinds.index("pane send-keys"), kinds.index("inject"))
        self.assertLess(kinds.index("inject"), kinds.index("pane run"))
        run = next(c for c in self.calls if c[:2] == ("pane", "run"))
        inject = next(c for c in self.calls if c[0] == "inject")
        self.assertIn(of_capacity._q(inject[2]), run[3])  # the text typed is the text declared
        self.assertIn("--dangerously-skip-permissions", run[3])
        self.assertIn("--model claude-opus-5-5", run[3])
        self.assertTrue(os.path.exists(os.path.join(r["work"], "note.md")))
        held = next(c for c in self.calls if c[0] == "emit")
        self.assertEqual((held[1], held[2]["evidence"]), ("holder_changed", "of capacity resume"))

    def test_move_updates_the_private_note_after_exit_and_copy_before_resume(self):
        target = os.path.join(self.home.name, ".claude-b")
        os.makedirs(target)
        shutil.copy2(os.path.join(self.cfg, ".claude.json"), target)
        source = of_capacity.find_transcript(self.cfg, "s1")
        task = {"prompt": "Check the report\nincluding the ``` example", "cron": "52 11 21 11 *", "recurring": False}
        with open(source, "a") as fh:
            fh.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "call-1", "name": "CronCreate", "input": task}]}}) + "\n")
            fh.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "call-1"}]},
                                 "toolUseResult": {"id": "deadbeef"}}) + "\n")
        notes = os.path.join(self.home.name, "capacity", "moves", "*", "note.md")

        def exit_session(*args, **kwargs):
            path, = glob.glob(notes)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            with open(path) as fh:
                self.assertNotIn("The exit stopped these scheduled tasks", fh.read())
            with open(source, "a") as fh:
                fh.write(json.dumps({"type": "system", "subtype": "informational", "timestamp":
                                     of_capacity.datetime.now(of_capacity.timezone.utc).isoformat(timespec="milliseconds"),
                                     "content": "Stopped scheduled task: Check the report…"}) + "\n")

        def start_session(*args, **kwargs):
            path, = glob.glob(notes)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            with open(path) as fh:
                text = fh.read()
            self.assertIn("# Account move:", text)
            self.assertIn("Run CronList, then re-create with CronCreate", text)
            self.assertIn("`52 11 21 11 *` (one-shot, old ID `deadbeef`)", text)
            self.assertIn("  ````\n  Check the report\n  including the ``` example\n  ````", text)
            return {"session": "s1", "pid": 10}

        live = {"session": "s1", "cwd": self.cwd, "pid": 9, "status": "idle"}
        logs = []
        with mock.patch.multiple(of_capacity, live_sessions=lambda d: [live] if d == self.cfg else [],
                                 _agent_by_session=lambda _: {"pane_id": "w6:p18", "agent_status": "idle"},
                                 _continuation=lambda _: {}, load_policy=lambda: {}, proc_env=lambda _: {},
                                 proc_args=lambda _: "claude", background_processes=lambda _: [], missing_signins=lambda *_: [],
                                 connectors_seen=lambda _: set(), plugins_ready=lambda _: set(), granted_labels=lambda _: {},
                                 with_labels=lambda have, _: have, tools_for=lambda *_: None, deny_for=lambda *_: None,
                                 exit_session=exit_session, start_session=start_session), \
                mock.patch.object(of_capacity, "stopped_tasks", wraps=of_capacity.stopped_tasks) as scan:
            of_capacity.move({"name": "runners_keeper", "holder": {"session": "s1"}}, target,
                             assume_empty_input=True, log=logs.append)
        self.assertEqual(scan.call_args.args[0], of_capacity.find_transcript(target, "s1"))
        self.assertTrue(any("1 stopped scheduled task" in msg for msg in logs))

    def test_carry_note_recovers_a_one_shot_even_when_install_rewrites_its_stop_notice(self):
        import of_carry
        sid = "5e551011-0000-4000-8000-000000000001"
        entry = {"name": "keeper", "position": "keeper", "org": "team", "session": sid,
                 "config_dir": "/source/.claude-e", "cwd": "/source/acme", "model": "m"}
        bundle = os.path.join(self.home.name, "bundle")
        project = os.path.join(bundle, "files/source/.claude-e/projects/-acme")
        os.makedirs(project)
        with open(os.path.join(bundle, "plan.json"), "w") as fh:
            json.dump({"kind": "carry", "org": "team", "home": "/source", "uid": os.getuid(),
                       "prepared_at": "2026-11-20T10:13:14Z", "entries": [entry]}, fh)
        prompt = "Read /source/acme/report and send the full summary."
        with open(os.path.join(project, sid + ".jsonl"), "w") as fh:
            for row in ({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "CronCreate",
                         "input": {"prompt": prompt, "cron": "52 11 21 11 *", "recurring": False}}]}},
                        {"type": "system", "subtype": "informational", "timestamp": "2026-11-20T10:13:13.999Z",
                         "content": "Stopped scheduled task: Earlier exit"},
                        {"type": "system", "subtype": "informational", "timestamp": "2026-11-20T10:13:14.001Z",
                         "content": "Stopped scheduled task: Read /source/acme/report…"}):
                fh.write(json.dumps(row) + "\n")
        with mock.patch.multiple(of_capacity, _herdr=mock.Mock(return_value={"panes": [], "workspaces": [], "tabs": [],
                                                                             "pane_id": "w6:p18"}),
                                 load_policy=lambda: {}, deny_for=lambda *_: None, tools_for=lambda *_: None,
                                 rename_agent=mock.Mock(), start_session=mock.Mock(return_value={"pid": 9, "session": sid})):
            result, = of_carry.restore(bundle, [{"name": "keeper", "_org": "team"}], "team",
                                       home=self.home.name, log=lambda _: None)
        self.assertEqual(result[:2], ("keeper", "resumed"))
        with open(os.path.join(result[2]["work"], "note.md")) as fh:
            note = fh.read()
        self.assertIn(prompt, note)
        self.assertIn("`52 11 21 11 *` (one-shot, old ID unavailable)", note)
        self.assertNotIn("Earlier exit", note)

    def test_the_shell_input_line_is_emptied_right_before_the_command(self):
        """2026-11-12: a recalled history line left at the prompt took the resume as its tail, and another position's
        session started a second copy in the pane. Ctrl-C, which drops continuation lines too, goes as a raw byte:
        send-keys "ctrl+u" typed "7;5u"."""
        self.resume()
        texts = [i for i, c in enumerate(self.calls) if c[:2] == ("pane", "send-text")]
        run = next(i for i, c in enumerate(self.calls) if c[:2] == ("pane", "run"))
        self.assertTrue(texts)
        self.assertEqual(self.calls[texts[-1]][2:], ("w6:p18", "\x03"))
        self.assertEqual(texts[-1], run - 1)

    def test_of_apply_runs_on_ofs_own_python_not_the_shebang(self):
        """of_review on PR 105: a Mac's system python3 may have no PyYAML, so bin/of never runs by its shebang."""
        import sys as _sys
        from unittest import mock
        ran = []
        with mock.patch.object(of_capacity.subprocess, "run", lambda argv, **k: ran.append(argv)):
            of_capacity.resume("runners_keeper", "s1", self.cfg, "w6:p18", log=lambda *_: None, of_bin="/x/bin/of")
        self.assertIn([_sys.executable, "/x/bin/of", "apply"], ran)
        self.assertFalse([a for a in ran if a and a[0] == "/x/bin/of"])

    def test_refuses_a_busy_pane_a_live_session_and_an_untrusted_folder(self):
        self.fg, self.screen = [1], ["> working"]
        with self.assertRaises(of_capacity.MoveError):
            self.resume()
        self.fg, self.up = [], True
        with self.assertRaises(of_capacity.MoveError):
            self.resume()
        self.up = False
        self.trust(False)
        with self.assertRaises(of_capacity.MoveError) as e:
            self.resume()
        self.assertEqual(e.exception.untrusted, self.cwd)
        self.assertFalse([c for c in self.calls if c[0] == "inject" or c[:2] == ("pane", "run")])


def exit_dialog(cursor=1):
    opts = ["Exit and stop tasks", "Move to background and exit", "Stay"]
    return (["✻ Brewed for 7m 24s · done 12:28 PM", "▔▔▔▔▔▔▔▔", "   Background work is running",
             "   The following will stop when you exit:", "",
             "   scheduled task · Runs once in 5h · aws_keeper reminder (H12 #79 watch, 24 h, final):…", ""]
            + [f"   {'❯' if i == cursor else ' '} {i}. {o}" for i, o in enumerate(opts, 1)]
            + ["", "   Enter to confirm · Esc to cancel"])


def narrow_exit_dialog(cursor=1):
    """The exit dialog as a narrow pane wraps it (2026-11-20)."""
    opts = [["Exit and stop", "tasks"], ["Move to", "background and", "exit"], ["Stay"]]
    out = ["▔▔▔▔▔▔▔▔", "  Background work is running", "  The following will stop when", "  you exit:", "",
           "  scheduled task · Runs once in", "  5h · aws_keeper reminder…", ""]
    for i, words in enumerate(opts, 1):
        out.append(f"  {'❯' if i == cursor else ' '} {i}. {words[0]}")
        out += [f"       {w}" for w in words[1:]]
    return out + ["", "  Enter to confirm · Esc to", "  cancel"]


def worktree_dialog(cursor=1):
    opts = ["Keep worktree", "Remove worktree"]
    return (["✻ Brewed for 2m 3s", "▔▔▔▔▔▔▔▔", "   Exiting worktree session",
             "   You are working in a worktree. Keep it to continue working there, or remove it to clean up.", ""]
            + [f"   {'❯' if i == cursor else ' '} {i}. {o}" for i, o in enumerate(opts, 1)]
            + ["", "   Enter to confirm · Esc to cancel"])


INPUT_BOX = ["⏺ earlier answer", "❯ an earlier prompt in the history", "──────── aws_keeper ─", "❯\xa0",
             "────────", "  ⏵⏵ bypass permissions on"]


class NarrowExitDialog(unittest.TestCase):
    def test_a_wrapped_option_is_read_whole(self):
        self.assertEqual(of_capacity._exit_dialog(narrow_exit_dialog(1)), "Exit and stop tasks")
        self.assertEqual(of_capacity._exit_dialog(narrow_exit_dialog(2)), "Move to background and exit")
        self.assertTrue(of_capacity._exit_dialog(narrow_exit_dialog(1)).startswith(of_capacity.EXIT_STOP))
        self.assertFalse(of_capacity._exit_dialog(narrow_exit_dialog(2)).startswith(of_capacity.EXIT_STOP))
        self.assertEqual(of_capacity._exit_dialog(exit_dialog(1)), "Exit and stop tasks")  # a wide one as before


class _ExitScreen(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        self.alive, self.keys, self.screens = True, [], []
        self.patches = [mock.patch.object(of_capacity.time, "sleep", lambda *_: None),
                        mock.patch.object(of_capacity, "_alive", lambda pid: self.alive)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def run_exit(self, screen, on_key=None, allow_shells=True):
        self.screen = screen

        def key(k):
            self.keys.append(k)
            if on_key:
                on_key(k)
        return of_capacity.exit_session(7, lambda: self.keys.append("/exit"), lambda: list(self.screen), key,
                                        allow_shells=allow_shells, wait=0.2, settle=0.02, step=0.001,
                                        log=lambda *_: None)


class ExitByTheScreen(_ExitScreen):
    """The exit step watches the screen (aws_keeper, 2026-11-07): a blind Enter 20 s after /exit missed the
    "Background work is running" dialog, the move failed and left the dialog open for 2.5 h, and two messages
    typed into it were lost. It answers the dialog only with "Exit and stop tasks", never "Move to background",
    and on any failure it closes the dialog (Esc) so the session stays as it was."""
    def test_a_late_dialog_is_answered_with_exit_and_stop_tasks(self):
        reads = {"n": 0}

        def screen_then_dialog():
            reads["n"] += 1
            return INPUT_BOX if reads["n"] < 4 else exit_dialog()
        self.screen = None

        def key(k):
            self.keys.append(k)
            if k == "Enter" and reads["n"] >= 4:
                self.alive = False
        of_capacity.exit_session(7, lambda: self.keys.append("/exit"), screen_then_dialog, key,
                                 allow_shells=True, wait=0.5, settle=0.02, step=0.001)
        self.assertEqual(self.keys, ["/exit", "Enter"])  # no Enter before the dialog was on screen

    def test_never_moves_to_background(self):
        def on_key(k):
            if k == "Up":
                cur = int(next(x for x in self.screen if "❯" in x).split("❯")[1].split(".")[0])
                self.screen = exit_dialog(max(1, cur - 1))
            if k == "Enter":
                self.assertIn("❯ 1. Exit and stop tasks", "\n".join(self.screen))
                self.alive = False
        self.run_exit(exit_dialog(cursor=2), on_key)
        self.assertEqual(self.keys, ["/exit", "Up", "Enter"])

    def test_a_dialog_that_ignores_enter_is_closed_and_the_session_stays(self):
        def on_key(k):
            if k == "Esc":
                self.screen = INPUT_BOX
        with self.assertRaises(of_capacity.MoveError) as e:
            self.run_exit(exit_dialog(), on_key)
        self.assertEqual(self.keys[-1], "Esc")
        self.assertLessEqual(self.keys.count("Enter"), 2)
        self.assertFalse(e.exception.stuck)

    def test_without_allow_shells_the_dialog_is_cancelled(self):
        def on_key(k):
            if k == "Esc":
                self.screen = INPUT_BOX
        with self.assertRaises(of_capacity.MoveError) as e:
            self.run_exit(exit_dialog(), on_key, allow_shells=False)
        self.assertEqual(self.keys, ["/exit", "Esc"])
        self.assertIn("--allow-shells", str(e.exception))

    def test_an_unsubmitted_exit_is_submitted_once(self):
        typed = list(INPUT_BOX)
        typed[3] = "❯\xa0/exit"

        def on_key(k):
            if k == "Enter":
                self.alive = False
        self.run_exit(typed, on_key)
        self.assertEqual(self.keys, ["/exit", "Enter"])

    def test_a_pane_that_cannot_be_restored_is_reported_stuck(self):
        with self.assertRaises(of_capacity.MoveError) as e:
            self.run_exit(exit_dialog())  # Esc changes nothing
        self.assertTrue(e.exception.stuck)
        self.assertIn("Esc", self.keys)


class ExitAWorktreeSession(_ExitScreen):
    """2026-11-12, a user organization's session: inside a git worktree, /exit opens "Exiting worktree session", which the move did
    not read. It gave up, reported the dialog closed while it stayed open, two relay messages went into it, and the
    second one's Enter exited the session 37 minutes later. Now the dialog is answered with "Keep worktree"."""
    def test_the_worktree_is_kept_and_the_session_exits_without_allow_shells(self):
        reads = {"n": 0}

        def screen_then_dialog():
            reads["n"] += 1
            return INPUT_BOX if reads["n"] < 3 else worktree_dialog()

        def key(k):
            self.keys.append(k)
            if k == "Enter":
                self.assertGreaterEqual(reads["n"], 3)
                self.alive = False
        of_capacity.exit_session(7, lambda: self.keys.append("/exit"), screen_then_dialog, key,
                                 allow_shells=False, wait=0.5, settle=0.02, step=0.001, log=lambda *_: None)
        self.assertEqual(self.keys, ["/exit", "Enter"])

    def test_never_removes_the_worktree(self):
        def on_key(k):
            if k == "Up":
                self.screen = worktree_dialog(1)
            if k == "Enter":
                self.assertIn("❯ 1. Keep worktree", "\n".join(self.screen))
                self.alive = False
        self.run_exit(worktree_dialog(cursor=2), on_key)
        self.assertEqual(self.keys, ["/exit", "Up", "Enter"])

    def test_a_dialog_left_on_screen_is_never_reported_closed(self):
        with self.assertRaises(of_capacity.MoveError) as e:
            self.run_exit(worktree_dialog())  # neither Enter nor Esc changes anything
        self.assertTrue(e.exception.stuck)
        self.assertIn("still open", str(e.exception))
        self.assertNotIn("Remove", " ".join(self.keys))

    def test_an_unknown_dialog_is_closed_with_esc_before_the_session_is_said_to_stay(self):
        other = ["⏺ earlier answer", "   Something new to decide", "   ❯ 1. Yes", "     2. No", "",
                 "   Enter to confirm · Esc to cancel"]

        def on_key(k):
            if k == "Esc":
                self.screen = INPUT_BOX
        with self.assertRaises(of_capacity.MoveError) as e:
            self.run_exit(other, on_key)
        self.assertEqual(self.keys, ["/exit", "Esc"])
        self.assertFalse(e.exception.stuck)


class LeftExitDialog(unittest.TestCase):
    """2026-11-13: a relief move's /exit ran after the move gave up; two panes stood on the exit dialog for an hour."""
    def setUp(self):
        import sqlite3
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE event (ts TEXT, name TEXT, session_id TEXT, payload TEXT)")
        self.now = of_capacity._ts("2026-11-13T21:00:00Z")

    def ev(self, ts, name, sid=None, **payload):
        self.conn.execute("INSERT INTO event VALUES (?,?,?,?)", (ts, name, sid, json.dumps(payload)))

    def run_sweep(self, screens):
        keys = []
        closed = of_capacity.close_left_exit_dialogs(
            now=self.now, log=lambda *_: None, conn=self.conn,
            agent_of=lambda sid: {"pane_id": "p-" + sid}, read=lambda pane: screens.get(pane, INPUT_BOX),
            key=lambda pane, k: keys.append((pane, k)))
        return closed, keys

    def test_only_a_dialog_its_own_exit_opened_and_nobody_answered(self):
        self.ev("2026-11-13T19:52:00Z", "of.injected_prompt", injector="of_capacity", target_session="s1",
                reason="account move: exit")
        self.ev("2026-11-13T20:21:00Z", "of.injected_prompt", injector="of_capacity", target_session="s2",
                reason="account move: exit")
        self.ev("2026-11-13T20:30:00Z", "of.human_prompt", "s2", origin="human")  # a person typed since: theirs
        self.ev("2026-11-13T16:00:00Z", "of.injected_prompt", injector="of_capacity", target_session="s3",
                reason="account move: exit")  # too long ago
        self.ev("2026-11-13T20:40:00Z", "of.injected_prompt", injector="of-relay:x", target_session="s4",
                reason="relayed message")      # not an exit of of_capacity's
        closed, keys = self.run_sweep({p: exit_dialog() for p in ("p-s1", "p-s2", "p-s3", "p-s4")})
        self.assertEqual((closed, keys), (["p-s1"], [("p-s1", "esc")]))
        self.assertEqual(self.run_sweep({}), ([], []))  # no dialog on screen: nothing pressed


class StuckPane(unittest.TestCase):
    def test_the_owner_gets_a_task_naming_the_pane_and_the_safe_key(self):
        e = of_capacity.MoveError("aws_keeper: pid 7: the exit dialog ignored Enter twice; the exit dialog is still "
                                  "open in its pane: look at it now (wF:p12); the hand-over note was not used", stuck=True)
        of_locale.set_language("en")
        try:
            t = of_capacity.stuck_task("aws_keeper", e)
        finally:
            of_locale.set_language(None)
        title = t.splitlines()[0]
        self.assertIn("wF:p12", title)
        self.assertIn("of go aws_keeper", t)
        self.assertIn("Esc", t)
        self.assertIn('Do not choose "Move to background and exit"', t)


class CapacityPosition(unittest.TestCase):
    """capacity.yaml `position` names who gets the invariant's work (docs/accounts.md B4); with none, the owner."""

    def tearDown(self):
        of_locale.set_language(None)

    def test_stopped_capacity_holder_routes_to_its_registered_conductor(self):
        capacity = "acme:capacity"
        positions = [{"_org": "acme", "name": "capacity", "holder": {"session": "s1"}},
                     {"_org": "acme-lab", "name": "other_conductor"},
                     {"_org": "acme", "name": "guide", "role": "conductor", "holder": {"session": "s2"}}]
        self.assertEqual(of_capacity.invariant_route(capacity, positions, {"s2"}), (capacity, ""))
        to, note = of_capacity.invariant_route(capacity, positions, {"s1"})
        self.assertEqual(to, "acme:guide")
        self.assertIn("acme:capacity is stopped on a usage limit", note)
        self.assertIn("conductor", note)
        for registry in (positions[:2], positions):
            to, note = of_capacity.invariant_route(capacity, registry, {"s1", "s2"})
            self.assertIsNone(to)
            self.assertIn("owner", note)

    def test_the_position_and_the_name_records_carry(self):
        self.assertIsNone(of_capacity.capacity_position({}))
        self.assertIsNone(of_capacity.capacity_position({"position": "  "}))
        self.assertEqual(of_capacity.actor({}), "of_capacity")
        self.assertEqual(of_capacity.capacity_position({"position": "acme:acme_capacity"}), "acme:acme_capacity")
        self.assertEqual(of_capacity.actor({"position": "acme:acme_capacity"}), "acme_capacity")

    def test_write_defaults_never_overwrites_what_the_person_set(self):
        import yaml
        pkg = {"position": "acme:acme_capacity", "auto": True, "rescue_moves": 6, "max_moves": 0, "relief_moves": 0}
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "of", "capacity.yaml")
            self.assertEqual(of_capacity.write_defaults(pkg, path), (list(pkg), []))  # no file: all written
            self.assertEqual(yaml.safe_load(open(path).read()), pkg)
            with open(path, "w") as fh:
                fh.write("# mine\nauto: false  # not yet\nmax_moves: 2")  # no newline at the end
            written, kept = of_capacity.write_defaults(pkg, path)
            self.assertEqual((written, kept), (["position", "rescue_moves", "relief_moves"], ["auto", "max_moves"]))
            text = open(path).read()
            self.assertTrue(text.startswith("# mine\nauto: false  # not yet\nmax_moves: 2\n# bench/capacity"))
            got = yaml.safe_load(text)
            self.assertEqual((got["auto"], got["max_moves"], got["position"]), (False, 2, "acme:acme_capacity"))
            self.assertEqual(of_capacity.write_defaults(pkg, path), ([], list(pkg)))  # a re-run writes nothing
            with open(path, "w") as fh:
                fh.write("- a list\n")
            with self.assertRaises(ValueError):
                of_capacity.write_defaults(pkg, path)

    def test_the_owner_cards_without_a_position(self):
        stalls = [{"session": "s1", "position": "acme_a"}, {"session": "s2", "position": None}]
        cyr = re.compile("[\\u0400-\\u04ff]")
        for lang in ("en", "ru"):
            of_locale.set_language(lang)
            text, options = of_capacity.owner_card("self", stalls, ["/gg"], [], "")
            self.assertIn("acme_a, s2", text)
            self.assertIsNone(options)
            pool, opts = of_capacity.owner_card("pool", [], [], ["s3"], "- /gg: week 90%")
            self.assertIn("- /gg: week 90%", pool)
            self.assertEqual(len(opts.split("|")), 2)
            for t in (text, pool, opts):
                self.assertNotIn("{", t)
                self.assertEqual(cyr.search(t) is not None, lang == "ru", t)


class OwnerTextsInTheOwnersLanguage(unittest.TestCase):
    """The owner's cards are English in the code and come in the owner's language from the catalogue
    (bin/of_locale.py; organic-framework 15168b1)."""

    def tearDown(self):
        of_locale.set_language(None)

    def texts(self, lang):
        of_locale.set_language(lang)
        e = of_capacity.MoveError("x: the exit dialog is still open in its pane (wF:p12)", stuck=True)
        row = {"config_dir": "/g", "account": "g@x", "positions": ["a", "b"], "logged_in": True,
               "windows": {"seven_day": {"utilization": 100, "resets_at": "2026-11-15T09:00:00Z"}}}
        target = {"config_dir": "/d", "account": "d@x", "logged_in": True, "windows": {"seven_day": {"utilization": 40}}}
        return [of_capacity.stuck_task("aws", e), of_capacity.stuck_task("aws", "no pane named"),
                *of_capacity.signin_task("/x", [("acme/slack", {})], "aws"),
                of_capacity.signin_task("/x", [("acme/slack", {})], "aws", moved=True)[1],
                of_capacity.ask_text(row, "seven_day 100% 90%", target),
                of_capacity.ask_text(row, "five_hour 96% 95%", None)]

    def test_english_by_default_the_catalogue_for_another_language(self):
        en, ru = self.texts("en"), self.texts("ru")
        cyr = re.compile("[\u0400-\u04ff]")
        for a, b in zip(en, ru):
            self.assertIsNone(cyr.search(a), a)
            self.assertIsNotNone(cyr.search(b), b)
            self.assertNotIn("{", a + b)  # every placeholder filled
        self.assertIn("wF:p12", ru[0].splitlines()[0])
        self.assertIn("of go aws", ru[0])
        self.assertIn("/d (d@x)", ru[5])
        self.assertIn("60%", ru[5])
        self.assertNotIn("d@x, 60%)", ru[5])  # a bare share by the account read as spent (2026-11-14)
        self.assertIn("slack", ru[2])
        self.assertNotIn("seven_day", ru[5] + en[5])  # the window's raw name never shows
        self.assertNotIn("five_hour", ru[6] + en[6])


class MoneyFirst(unittest.TestCase):
    """founder, 2026-11-07 (ask 4576f634): model capacity goes first to the Acme positions whose
    throughput is raise or measure, then the rest; the platform unit (keep mode) comes last."""
    NOW = Balance.NOW
    PRIO = {"first": [{"org": "acme", "throughput": ["raise", "measure"]}],
            "last": [{"org": "acme", "unit": "platform"}]}
    POS = [{"name": "plat", "_org": "acme", "_unit": "platform", "throughput": "sustain", "holder": {"session": "plat"}},
           {"name": "bot", "_org": "acme", "_unit": "shop", "throughput": "raise", "holder": {"session": "bot"}},
           {"name": "acct", "_org": "acme", "_unit": "data-products", "throughput": "measure", "holder": {"session": "acct"}},
           {"name": "of_x", "_org": "organic-framework-build", "holder": {"session": "of_x"}}]

    def test_tiers(self):
        tier = {p["name"]: of_capacity.priority_tier(p, {"priority": self.PRIO}) for p in self.POS}
        self.assertEqual(tier, {"plat": 2, "bot": 0, "acct": 0, "of_x": 1})
        self.assertEqual(of_capacity.priority_tier(self.POS[0], {}), 1)  # no rule: everyone equal

    def test_relief_moves_the_money_side_first(self):
        rows = Balance().rows()
        rows[1]["windows"]["seven_day"]["utilization"] = 99  # /cl about to stall
        sess = {"/cl": [{"session": s, "status": "idle"} for s in ("plat", "of_x", "bot", "acct")], "/ez": [], "/gg": []}
        rates = {"plat": 90, "of_x": 60, "bot": 10, "acct": 8}
        pol = dict(POLICY, priority=self.PRIO, max_moves=2)
        m = of_capacity.balance_plan(rows, sess, self.POS, rates, {"/ez": 12.0}, pol, self.NOW)
        self.assertEqual([x["position"] for x in m], ["bot", "acct"])  # not the faster platform or OF holders
        pol["max_moves"] = 4
        m = of_capacity.balance_plan(rows, sess, self.POS, rates, {"/ez": 12.0}, pol, self.NOW)
        self.assertEqual([x["position"] for x in m], ["bot", "acct", "of_x", "plat"])


class LimitDialog(unittest.TestCase):
    """A session stopped on its account's limit (herdr: blocked) is what a move is for (2026-11-07: ten
    Acme sessions stood on ~/.claude at 100% and the move refused them as blocked)."""
    SCREEN = ["  ⎿  You've hit your weekly limit · resets Nov 12 at 1am (Asia/Dubai)", "▔▔▔▔", "   What do you want to do?",
              "   ❯ 1. Stop and wait for limit to reset", "     2. Wait here, then continue automatically at Nov 12 at 1am",
              "     3. Switch to usage credits", "   Enter to confirm · Esc to cancel"]
    WRAPPED = ["  ⎿  You've hit your weekly limit ·", "     resets Nov 26 at 1am", "     (Asia/Dubai)",
               "▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔",
               "  What do you want to do?", "  ❯ 1. Stop and wait for limit to", "       reset",
               "    2. Wait here, then continue", "       automatically at Nov 26 at", "       1am",
               "    3. Switch to usage credits", "  Enter to confirm · Esc to", "  cancel"]

    def test_cursor_on_stop_and_wait_only(self):
        self.assertTrue(of_capacity.limit_cursor(self.SCREEN))
        moved = [x.replace("❯", " ") for x in self.SCREEN]
        moved[5] = "   ❯ 3. Switch to usage credits"
        self.assertFalse(of_capacity.limit_cursor(moved))  # never Enter on the paid option
        self.assertFalse(of_capacity.limit_cursor(INPUT_BOX))

    def test_only_the_live_dialog_counts(self):
        # of_server on PR 318: a copy printed higher up, cursor on "Stop and wait", above the live dialog whose
        # cursor is on the paid option, passed
        live = [x.replace("❯", " ") for x in self.SCREEN]
        live[5] = "   ❯ 3. Switch to usage credits"
        self.assertFalse(of_capacity.limit_cursor(self.SCREEN + ["", "  ⏺ later output"] + live))
        self.assertFalse(of_capacity.limit_cursor(self.SCREEN + ["  ⏺ the dialog is gone; output below it"]))
        self.assertTrue(of_capacity.limit_cursor(["old output"] * 30 + self.SCREEN + ["", "  "]))  # trailing blanks

    def test_wrapped_dialog(self):
        self.assertTrue(of_capacity.limit_cursor(self.WRAPPED))
        self.assertTrue(of_capacity.has_limit_dialog(self.WRAPPED))
        self.assertEqual(of_capacity.live_dialog(self.WRAPPED), [
            "  What do you want to do?", "  ❯ 1. Stop and wait for limit to reset",
            "    2. Wait here, then continue automatically at Nov 26 at 1am",
            "    3. Switch to usage credits", "Enter to confirm · Esc to cancel"])
        self.assertEqual(of_capacity.live_dialog(self.SCREEN), self.SCREEN[2:])

    def test_wrapped_cursor_on_another_option(self):
        for option in ("2.", "3."):
            with self.subTest(option=option):
                moved = [x.replace("❯", " ").replace("    " + option, "  ❯ " + option) for x in self.WRAPPED]
                self.assertFalse(of_capacity.limit_cursor(moved))

    def test_wrapped_dialog_above_later_output(self):
        self.assertFalse(of_capacity.limit_cursor(self.WRAPPED + ["  ⏺ later output"]))
        self.assertFalse(of_capacity.limit_cursor(self.WRAPPED + ["  ⏺ later output mentions Enter to confirm"]))

    def test_footer_wraps_over_at_most_three_nonblank_lines(self):
        options = self.WRAPPED[:-2]
        self.assertTrue(of_capacity.limit_cursor(options + ["  Enter to", "  confirm · Esc", "", "  to cancel", ""]))
        self.assertFalse(of_capacity.limit_cursor(options + ["  Enter", "  to confirm", "  · Esc to", "  cancel"]))


class ReliefTakesStoppedSessions(unittest.TestCase):
    def test_relief_takes_shell_and_limit_stopped_sessions_fill_does_not(self):
        rows = Balance().rows()
        rows[1]["windows"]["seven_day"]["utilization"] = 100  # /cl stalled
        pos = [{"name": n, "holder": {"session": n}} for n in ("w", "sh", "busy", "k8s_keeper")]
        sess = {"/cl": [{"session": "w", "status": "waiting"}, {"session": "sh", "status": "shell"},
                        {"session": "busy", "status": "busy"}, {"session": "k8s_keeper", "status": "waiting"}],
                "/ez": [], "/gg": []}
        pol = dict(POLICY, max_moves=5)
        m = of_capacity.balance_plan(rows, sess, pos, {"w": 9, "sh": 8, "busy": 50, "k8s_keeper": 40}, {"/ez": 12.0}, pol,
                                     Balance.NOW)
        # never busy; a held (production) position goes first, but only because its account stalled (ask 44a3b1f9)
        self.assertEqual([x["position"] for x in m], ["k8s_keeper", "w", "sh"])
        rows[1]["windows"]["seven_day"]["utilization"] = 10  # not stalled: fill takes idle sessions only
        m = of_capacity.balance_plan(rows, sess, pos, {"w": 9, "sh": 8}, {"/ez": 12.0}, pol, Balance.NOW)
        self.assertEqual(m, [])

    def test_fill_never_takes_a_held_position(self):
        rows = Balance().rows()
        pos = [{"name": n, "holder": {"session": n}} for n in ("k8s_keeper", "w")]
        sess = {"/cl": [{"session": "k8s_keeper", "status": "idle"}, {"session": "w", "status": "idle"}],
                "/ez": [], "/gg": []}
        pol = dict(POLICY, max_moves=5)
        m = of_capacity.balance_plan(rows, sess, pos, {"k8s_keeper": 40, "w": 9}, {"/ez": 12.0, "/cl": 12.0}, pol,
                                     Balance.NOW)
        self.assertNotIn("k8s_keeper", [x["position"] for x in m])


class PermissionMode(unittest.TestCase):
    """Agent sessions stay in bypass (founder, 2026-11-08): warehouse_keeper ran a day in auto mode with
    --dangerously-skip-permissions on its command line."""
    BOX = ["────────", "❯ ", "────────"]

    def screen(self, mode):
        foot = {"bypass": "  ⏵⏵ bypass permissions on (shift+tab to cycle)", "auto": "  ⏵⏵ auto mode on (shift+tab to cycle)",
                "accept": "  ⏵⏵ accept edits on (shift+tab to cycle)", "plan": "  ⏸ plan mode on (shift+tab to cycle)",
                "default": "  ? for shortcuts"}[mode]
        return self.BOX + [foot]

    def test_footer_names_the_mode(self):
        for m in ("bypass", "auto", "accept", "plan", "default"):
            self.assertEqual(of_capacity.perm_mode(self.screen(m)), m)
        self.assertIsNone(of_capacity.perm_mode(["$ ls", "file"]))  # a shell, no input box

    def test_shift_tab_until_bypass_one_press_at_a_time(self):
        cycle = ["auto", "bypass", "default", "accept", "plan"]
        state, pressed = {"i": 0}, []

        def key(k):
            pressed.append(k)
            state["i"] = (state["i"] + 1) % len(cycle)
        read = lambda: self.screen(cycle[state["i"]])  # noqa: E731
        self.assertEqual(of_capacity.ensure_bypass(read, key, log=lambda m: None, sleep=lambda s: None), "bypass")
        self.assertEqual(pressed, ["shift+tab"])

    def test_never_presses_over_a_draft_or_a_dialog_or_blind(self):
        pressed = []
        draft = ["────────", "❯ half a sentence", "────────", "  ⏵⏵ auto mode on (shift+tab to cycle)"]
        self.assertEqual(of_capacity.ensure_bypass(lambda: draft, pressed.append, log=lambda m: None,
                                                   sleep=lambda s: None), "auto")
        dialog = ["   What do you want to do?", "   ❯ 1. Yes", "   Enter to confirm · Esc to cancel",
                  "  ⏵⏵ auto mode on (shift+tab to cycle)"]
        self.assertEqual(of_capacity.ensure_bypass(lambda: dialog, pressed.append, log=lambda m: None,
                                                   sleep=lambda s: None), "auto")
        self.assertEqual(pressed, [])
        stuck = self.screen("auto")  # a press that changes nothing stops at once
        self.assertEqual(of_capacity.ensure_bypass(lambda: stuck, pressed.append, log=lambda m: None,
                                                   sleep=lambda s: None), "auto")
        self.assertEqual(pressed, ["shift+tab"])

    def test_after_start_an_undrawn_footer_is_waited_for_never_cycled_blind(self):
        # 2026-11-14 20:34Z: a session resumed with --dangerously-skip-permissions recorded its first prompt in
        # bypass and was in auto a second later. Its input box was drawn before the footer's mode line, that read as
        # "default", and shift+tab cycled it out of bypass; a permission dialog then held it 13 hours
        def run(screens):
            pressed, seq = [], list(screens)

            def herdr(*a):
                if a[:2] == ("pane", "send-keys"):
                    pressed.append(a[3])
                    return None
                return "\n".join(seq.pop(0) if len(seq) > 1 else seq[0])
            now = [0.0]  # a clock that moves only when the wait sleeps: never the machine's load

            def sleep(s):
                now[0] += s
            return of_capacity.check_bypass_after_start("w1:p1", log=lambda m: None, wait=5, step=1, herdr=herdr,
                                                        sleep=sleep, clock=lambda: now[0]), pressed
        self.assertEqual(run([self.BOX, self.BOX, self.screen("bypass")]), ("bypass", []))
        self.assertEqual(run([self.BOX]), ("default", []))  # never drawn: the next balance run looks again
        mode, pressed = run([self.BOX, self.screen("auto"), self.screen("auto"), self.screen("bypass")])
        self.assertEqual((mode, pressed), ("bypass", ["shift+tab"]))  # a named mode is still turned back

    def test_resume_keeps_bypass_for_a_session_turned_to_auto(self):
        import tempfile
        f = os.path.join(tempfile.mkdtemp(), "t.jsonl")
        with open(f, "w") as fh:
            fh.write('{"type":"permission-mode","permissionMode":"bypassPermissions"}\n'
                     '{"type":"user","permissionMode":"auto"}\n')
        self.assertTrue(of_capacity.ran_in_bypass(f))


class WeekCalibration(unittest.TestCase):
    """The spend index holds 6 hours: the week is calibrated by its growth over those hours, not since it opened
    (2026-11-09: an Ultra came out at $5 per 1% instead of about $26)."""
    def test_growth_over_the_index_not_the_whole_week(self):
        now = 1_800_000_000
        reset = "2027-01-20T00:00:00Z"
        row = {"config_dir": "/a", "windows": {"seven_day": {"utilization": 19, "resets_at": reset}}}
        index = {f"m{i}": [now - 5 * 3600 + i * 60, "s", 10.0, "/a"] for i in range(10)}  # $100 in the last 5 h
        hist = [[now - 5.5 * 3600, 15, reset], [now - 3600, 18, reset]]
        self.assertAlmostEqual(of_capacity.calibrate_week(row, now, index, hist), 25.0)  # $100 / (19 - 15)
        self.assertIsNone(of_capacity.calibrate_week(row, now, index, []))  # no history: unknown, not small
        hist = [[now - 5.5 * 3600, 15, "2027-01-13T00:00:00Z"]]  # a previous week's reading does not count
        self.assertIsNone(of_capacity.calibrate_week(row, now, index, hist))


class LockDirs(unittest.TestCase):
    def test_a_lock_dir_is_not_an_account(self):
        home = tempfile.mkdtemp()
        for d in (".claude", ".claude-d", ".claude-d.lock", ".claude-c"):
            os.makedirs(os.path.join(home, d))
        self.assertEqual([os.path.basename(x) for x in of_capacity.config_dirs(home)],
                         [".claude", ".claude-c", ".claude-d"])


class FeedbackDrafts(unittest.TestCase):
    def test_the_sessions_drafts_go_aside_before_exit_others_stay(self):
        cfg, work = tempfile.mkdtemp(), tempfile.mkdtemp()
        d = os.path.join(cfg, "feedback", "drafts")
        os.makedirs(d)
        for name, sid in (("a.json", "s1"), ("b.json", "s2"), ("c.json", "s1")):
            with open(os.path.join(d, name), "w") as fh:
                json.dump({"draft_id": name, "source_session_id": sid, "status": "queued"}, fh)
        with open(os.path.join(d, "broken.json"), "w") as fh:
            fh.write("{")
        self.assertEqual(of_capacity.set_drafts_aside(cfg, "s1", work), ["a.json", "c.json"])
        self.assertEqual(sorted(os.listdir(d)), ["b.json", "broken.json"])  # another session's, and unreadable
        self.assertEqual(sorted(os.listdir(os.path.join(work, "feedback-drafts"))), ["a.json", "c.json"])
        self.assertEqual(of_capacity.set_drafts_aside(cfg, "s9", work), [])
        self.assertEqual(of_capacity.set_drafts_aside(tempfile.mkdtemp(), "s1", work), [])  # no drafts folder


class MoveBackups(unittest.TestCase):
    def test_backup_goes_after_six_hours_when_the_position_runs_notes_stay(self):
        root = tempfile.mkdtemp()
        now = 1_800_000_000
        old = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now - 8 * 3600))
        new = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now - 2 * 3600))
        for d in (f"op-{old}", f"op-{new}", f"gone-{old}", f"op-{old}-resume", "memory-merge-x"):
            os.makedirs(os.path.join(root, d, "backup", "projects"))
            with open(os.path.join(root, d, "backup", "projects", "t.jsonl"), "w") as fh:
                fh.write("x" * 100)
            with open(os.path.join(root, d, "note.md"), "w") as fh:
                fh.write("note")
        out = of_capacity.prune_move_backups({"op"}, now=now, root=root, log=lambda m: None)
        self.assertEqual(sorted(x[0] for x in out), [f"op-{old}", f"op-{old}-resume"])
        self.assertEqual(sum(x[1] for x in out), 200)
        self.assertFalse(os.path.exists(os.path.join(root, f"op-{old}", "backup")))
        self.assertTrue(os.path.exists(os.path.join(root, f"op-{old}", "note.md")))
        for keep in (f"op-{new}", f"gone-{old}", "memory-merge-x"):  # too young, not running, not a move
            self.assertTrue(os.path.isdir(os.path.join(root, keep, "backup")))


class Merge3(unittest.TestCase):
    def test_both_sides_append_and_the_frontmatter_is_not_doubled(self):
        front = "---\nname: runner\nmodified: 2026-11-06T20:00Z\n---\n"
        base = front + "- line 1\n- line 2\n"
        ours = front.replace("20:00Z", "21:40Z") + "- line 1\n- line 2\n- e note\n"
        theirs = front.replace("20:00Z", "23:10Z") + "- line 1\n- line 2\n- claude note A\n- claude note B\n"
        m = of_capacity.merge3(ours, base, theirs)
        self.assertEqual(m.count("modified:"), 1)
        for line in ("- line 1", "- e note", "- claude note A", "- claude note B"):
            self.assertIn(line, m)
        self.assertNotIn("<<<<<<<", m)
        self.assertEqual(of_capacity.merge3(ours, base, base), ours)  # one side unchanged: the other wins whole


class CredentialInventory(unittest.TestCase):
    """Broker step 2: names, accounts, dates and fingerprints; never a value."""
    def test_fingerprint_and_last_word(self):
        fp = of_capacity.fingerprint("sk-ant-oat01-example\n")
        self.assertEqual(fp, of_capacity.fingerprint("sk-ant-oat01-example"))
        self.assertRegex(fp, r"^[0-9a-f]{12}$")
        ev = [("of.credential_adopted", {"name": "tax", "kind": "setup_token", "account": "a@x", "fingerprint": fp}),
              ("of.credential_adopted", {"name": "lab", "kind": "setup_token", "account": "b@x", "fingerprint": "0" * 12}),
              ("of.credential_retired", {"fingerprint": "0" * 12})]
        rows = of_capacity.token_rows(ev)
        self.assertEqual([(r["name"], r["fingerprint"]) for r in rows], [("tax", fp)])
        self.assertNotIn("example", json.dumps(rows))

    def test_adopt_keeps_only_the_fingerprint(self):
        from unittest import mock
        seen = []
        with mock.patch.object(of_capacity.of_events, "emit", lambda kind, payload, **k: seen.append((kind, payload))):
            r = of_capacity.adopt("tax", "setup_token", "a@x", value="sk-ant-oat01-secret")
            with self.assertRaises(of_capacity.MoveError):
                of_capacity.adopt("tax", "setup_token", "a@x", value="v", fp="abc")  # one or the other
            with self.assertRaises(of_capacity.MoveError):
                of_capacity.adopt("tax", "password", "a@x", fp="0" * 12)
        self.assertEqual(seen[0][0], "credential_adopted")
        self.assertNotIn("secret", json.dumps(seen))
        self.assertEqual(r["fingerprint"], of_capacity.fingerprint("sk-ant-oat01-secret"))

    def test_login_rows_read_no_secrets(self):
        with tempfile.TemporaryDirectory() as h:
            d = os.path.join(h, ".claude-x")
            os.makedirs(d)
            with open(os.path.join(d, ".claude.json"), "w") as fh:
                json.dump({"oauthAccount": {"emailAddress": "x@y", "planDisplayName": "Ultra",
                                            "organizationRateLimitTier": "default_claude_max_20x"},
                           "primaryApiKey": "sk-should-not-show"}, fh)
            rows = of_capacity.login_rows([d])
        self.assertEqual((rows[0]["account"], rows[0]["plan"]), ("x@y", "Ultra"))
        self.assertNotIn("sk-should-not-show", json.dumps(rows))


class Connectors(unittest.TestCase):
    """claude.ai connectors belong to the account, not the config dir (2026-11-07: acme_dispatcher and obs_keeper
    lost Slack when they moved from alex@acme.example to alex@example.com)."""
    def test_used_connectors_from_the_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, "s.jsonl")
            with open(t, "w") as fh:
                for name in ("mcp__claude_ai_Slack__slack_search_public_and_private", "Bash", "mcp__grafana__query_loki_logs"):
                    fh.write(json.dumps({"type": "assistant", "message": {"content": [
                        {"type": "tool_use", "name": name, "input": {}}]}}) + "\n")
            self.assertEqual(of_capacity.connectors_used(t), {"Slack"})

    def test_fill_skips_a_target_without_the_connector_relief_does_not(self):
        rows = Balance().rows()
        pos = [{"name": "disp", "holder": {"session": "disp"}}]
        sess = {"/cl": [{"session": "disp", "status": "idle"}], "/ez": [], "/gg": []}
        pol = dict(POLICY)
        needs = {"disp": {"Slack"}}
        m = of_capacity.balance_plan(rows, sess, pos, {"disp": 50}, {"/ez": 12.0}, pol, Balance.NOW,
                                     connectors=(needs, {"/ez": set()}))
        self.assertEqual(m, [])  # fill: not onto an account that lacks Slack
        m = of_capacity.balance_plan(rows, sess, pos, {"disp": 50}, {"/ez": 12.0}, pol, Balance.NOW,
                                     connectors=(needs, {"/ez": {"Slack"}}))
        self.assertEqual([x["position"] for x in m], ["disp"])
        rows[1]["windows"]["seven_day"]["utilization"] = 100  # /cl stalled: relief moves it anyway, with a note
        m = of_capacity.balance_plan(rows, sess, pos, {"disp": 50}, {"/ez": 12.0}, pol, Balance.NOW,
                                     connectors=(needs, {"/ez": set()}))
        self.assertEqual([x["position"] for x in m], ["disp"])


class Plugins(unittest.TestCase):
    """Plugins are installed per config dir (2026-11-11: c had none; aws_keeper lost Cloudflare in a
    move there, with OFF-013's R2 token rotation waiting on it)."""
    def transcript(self, d, names):
        t = os.path.join(d, "s.jsonl")
        with open(t, "w") as fh:
            for name in names:
                fh.write(json.dumps({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": name, "input": {}}]}}) + "\n")
        return t

    def test_tools_used_names_plugins_and_connectors(self):
        with tempfile.TemporaryDirectory() as d:
            t = self.transcript(d, ["mcp__plugin_cloudflare_cloudflare__execute", "mcp__claude_ai_Slack__x",
                                    "mcp__plugin_slack_slack__slack_send_message", "mcp__grafana__query_loki_logs"])
            self.assertEqual(of_capacity.tools_used(t), {"plugin:cloudflare", "plugin:slack", "Slack"})
            self.assertEqual(of_capacity.connectors_used(t), {"Slack"})

    def test_plugins_seen_installed_and_not_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(of_capacity.plugins_seen(d), set())  # none installed: a known empty set
            os.makedirs(os.path.join(d, "plugins"))
            with open(os.path.join(d, "plugins", "installed_plugins.json"), "w") as fh:
                json.dump({"version": 2, "plugins": {"cloudflare@claude-plugins-official": [{}],
                                                     "rust-analyzer-lsp@claude-plugins-official": [{}]}}, fh)
            with open(os.path.join(d, "settings.json"), "w") as fh:
                json.dump({"enabledPlugins": {"rust-analyzer-lsp@claude-plugins-official": False}}, fh)
            self.assertEqual(of_capacity.plugins_seen(d), {"plugin:cloudflare"})
            with open(os.path.join(d, "settings.json"), "w") as fh:
                fh.write("{broken")
            self.assertIsNone(of_capacity.plugins_seen(d))

    def test_lacking_counts_only_what_was_read(self):
        used = {"plugin:cloudflare", "Slack"}
        self.assertEqual(of_capacity.lacking(used, of_capacity.account_tools(set(), set())), used)
        self.assertEqual(of_capacity.lacking(used, of_capacity.account_tools(None, set())), {"plugin:cloudflare"})
        self.assertEqual(of_capacity.lacking(used, of_capacity.account_tools({"Slack"}, None)), set())
        self.assertEqual(of_capacity.lacking(used, None), set())
        self.assertEqual(of_capacity.lacking(used, {"Slack"}), set())  # a plain connector set: plugins unknown

    def test_relief_and_fill_avoid_an_account_without_the_plugin(self):
        rows = Balance().rows()
        pos = [{"name": "aws", "holder": {"session": "aws"}}]
        sess = {"/cl": [{"session": "aws", "status": "idle"}], "/ez": [], "/gg": []}
        pol = dict(POLICY)
        needs = {"aws": {"plugin:cloudflare"}}
        without = {"/ez": of_capacity.account_tools(set(), set())}
        with_ = {"/ez": of_capacity.account_tools(set(), {"plugin:cloudflare"})}
        self.assertEqual(of_capacity.balance_plan(rows, sess, pos, {"aws": 50}, {"/ez": 12.0}, pol, Balance.NOW,
                                                  connectors=(needs, without)), [])
        self.assertEqual([x["position"] for x in of_capacity.balance_plan(
            rows, sess, pos, {"aws": 50}, {"/ez": 12.0}, pol, Balance.NOW, connectors=(needs, with_))], ["aws"])
        rows[1]["windows"]["seven_day"]["utilization"] = 100  # /cl stalled: relief, but not onto /ez without it
        self.assertEqual(of_capacity.balance_plan(rows, sess, pos, {"aws": 50}, {"/ez": 12.0}, pol, Balance.NOW,
                                                  connectors=(needs, without)), [])

    def test_rescue_prefers_a_target_with_the_plugin_else_moves_anyway(self):
        r = Rescue()
        stopped = {"st1": r.NOW - 600}
        needs = {"st1": {"plugin:cloudflare"}}
        have = {"/ap": of_capacity.account_tools(None, set()), "/gg": of_capacity.account_tools(None, {"plugin:cloudflare"})}
        m = of_capacity.rescue_plan(r.rows(), r.SESS, r.POS, stopped, r.STOPS_ONLY, r.NOW, tools=(needs, have))
        self.assertEqual([(x["position"], x["to"]) for x in m], [("st1", "/gg")])  # /ap has more room, no plugin
        have["/gg"] = of_capacity.account_tools(None, set())
        m = of_capacity.rescue_plan(r.rows(), r.SESS, r.POS, stopped, r.STOPS_ONLY, r.NOW, tools=(needs, have))
        self.assertEqual([(x["position"], x["to"]) for x in m], [("st1", "/ap")])  # none has it: still rescued

    def test_rescue_does_not_put_a_session_on_an_account_about_to_stop_for_its_plugin(self):
        # 2026-11-15: two sessions using Cloudflare were rescued onto an account at 99% of its week that had it; both
        # stopped there within two minutes and were rescued again, onto the account without it
        r = Rescue()
        rows = r.rows()
        rows[1]["windows"]["seven_day"]["utilization"] = 99  # /gg has the plugin and is about to stop
        needs = {"st1": {"plugin:cloudflare"}}
        have = {"/ap": of_capacity.account_tools(None, set()), "/gg": of_capacity.account_tools(None, {"plugin:cloudflare"})}
        m = of_capacity.rescue_plan(rows, r.SESS, r.POS, {"st1": r.NOW - 600}, r.STOPS_ONLY, r.NOW, tools=(needs, have))
        self.assertEqual([(x["position"], x["to"]) for x in m], [("st1", "/ap")])


class ToolProfiles(unittest.TestCase):
    """Hand-over 9843194a (docs/tools.md §4-§5): moves keep the position's tool profile, and an account where one of
    its account-bound tools is not signed in is not a target; the founder gets one task per account."""
    SLACK = "https://mcp.slack.com/mcp"
    DOCS = {"acme": {
        "tools_mode": "enforce",  # of_tools, PR 38: only an enforced catalogue strips the account's own tools
        "tools": {
            "grafana-read": {"kind": "mcp-stdio", "command": ["/opt/mcp-grafana", "--disable-write"],
                             "acts_as": "viewer", "scope": "read", "keeper": "obs_keeper"},
            "slack": {"kind": "plugin", "plugin": "slack@anthropic-plugin-directory", "url": SLACK,
                      "account_bound": True, "acts_as": "the owner", "scope": "read", "keeper": "acme_org"}},
        "positions": [{"name": "aws", "tools": ["grafana-read", "slack"]}, {"name": "plain"}]}}

    def servers(self, d, now=None, run=None):
        return {"/ez": [("plugin:slack:slack", self.SLACK, False)],
                "/cl": [("plugin:slack:slack", self.SLACK, True)], "/gg": []}.get(d)

    def test_a_move_carries_the_profile_in_its_one_settings_file(self):
        import of_tools
        from unittest import mock
        with tempfile.TemporaryDirectory() as t, mock.patch.object(of_tools, "registries", lambda *a: self.DOCS), \
                mock.patch.object(of_tools, "STATE_DIR", t):
            tools = of_capacity.tools_for({"name": "aws"}, "s1", os.path.join(t, "acct"))
            self.assertEqual(of_capacity.tools_for({"name": "nobody"}, "s1", t), ([], {}))
            line = of_capacity.launch_command("/w", "/h/.claude-b", "s1", "aws", "/n.md", deny=["Read(/k/**)"],
                                              memory_dir="/m/aws", state_dir=t, tools=tools)
            self.assertIn("--mcp-config " + os.path.join(t, "tools", "s1.mcp.json"), line)
            with open(os.path.join(t, "tools", "s1.mcp.json")) as fh:
                self.assertEqual(list(json.load(fh)["mcpServers"]), ["grafana-read"])
            settings = json.loads(of_capacity.settings_text(line))
        self.assertEqual(settings["allowedMcpServers"], [{"serverName": "grafana-read"}, {"serverUrl": self.SLACK}])
        self.assertEqual(settings["enabledPlugins"], {"slack@anthropic-plugin-directory": True})
        self.assertEqual((settings["permissions"]["deny"], settings["autoMemoryDirectory"]), (["Read(/k/**)"], "/m/aws"))

    def test_a_move_stops_where_a_bound_tool_is_signed_out_and_files_one_task(self):
        from unittest import mock
        cache, asks = {}, []
        with tempfile.TemporaryDirectory() as ez, tempfile.TemporaryDirectory() as cl, mock.patch.multiple(
                of_capacity, tool_docs=lambda: self.DOCS, load_cache=lambda: cache, save_cache=lambda c: None,
                config_dirs=lambda: [ez, cl], live_sessions=lambda d: [], dir_of_position=lambda p, s: cl,
                account_of=lambda d: "x@example.com",
                mcp_servers=lambda d, now=None, run=None: self.servers("/ez" if d == ez else "/cl")):
            for d in (ez, cl):  # installed on both; signed in on /cl only
                os.makedirs(os.path.join(d, "plugins"))
                with open(os.path.join(d, "plugins", "installed_plugins.json"), "w") as fh:
                    json.dump({"version": 2, "plugins": {"slack@anthropic-plugin-directory": [{}]}}, fh)
            self.assertEqual([i for i, _ in of_capacity.missing_signins("aws", ez)], ["acme/slack"])
            self.assertEqual(of_capacity.missing_signins("aws", cl), [])
            self.assertEqual(of_capacity.missing_signins("plain", ez), [])  # no bound tool: nothing changes
            for _ in range(2):
                with self.assertRaisesRegex(of_capacity.MoveError, "not signed in on .*acme/slack"):
                    of_capacity.move({"name": "aws", "holder": {"session": "s1"}}, ez,
                                     ask=lambda t, task=None: asks.append((t, task)), log=lambda x: None)
        self.assertEqual(len(asks), 1)  # one task per account and tool, not one per attempt
        self.assertIn("slack", asks[0][0])
        self.assertIn(f"CLAUDE_CONFIG_DIR=", asks[0][1])

    def test_plan_names_only_an_account_where_the_tools_are_signed_in(self):
        from unittest import mock
        rows = [{"config_dir": "/gg", "logged_in": True, "positions": ["aws", "plain"],
                 "windows": {"seven_day": {"utilization": 99}}},
                {"config_dir": "/ez", "logged_in": True, "windows": {"seven_day": {"utilization": 5}}},
                {"config_dir": "/cl", "logged_in": True, "windows": {"seven_day": {"utilization": 40}}}]
        pos = [{"name": "aws", "holder": {"session": "s1"}}, {"name": "plain", "holder": {"session": "s2"}}]
        with mock.patch.multiple(of_capacity, tool_docs=lambda: self.DOCS, mcp_servers=self.servers,
                                 load_policy=lambda: dict(POLICY)):
            steps = {x["position"]: x for x in of_capacity.plan(rows, pos)}
        self.assertEqual((steps["aws"]["to"], steps["aws"]["not_signed_in"]), ("/cl", {"/ez": ["acme/slack"]}))
        self.assertEqual(steps["plain"]["to"], "/ez")

    def test_fill_and_relief_avoid_the_account_rescue_prefers_another(self):
        from unittest import mock
        b = Balance()
        b.setUp()
        rows, pos = b.rows(), [{"name": "aws", "holder": {"session": "aws"}}]
        sess = {"/cl": [], "/ez": [], "/gg": [{"session": "aws", "status": "idle"}]}
        needs, seen = {"aws": set()}, {}
        with mock.patch.object(of_capacity, "mcp_servers", self.servers):
            of_capacity.add_bound(needs, seen, pos, ["/ez", "/cl", "/gg"], docs=self.DOCS)
        self.assertEqual(needs["aws"], {"bound:acme/slack"})
        self.assertEqual((seen["/ez"], seen["/cl"]), ({"bound:known"}, {"bound:acme/slack", "bound:known"}))
        self.assertTrue(of_capacity.lacking(needs["aws"], seen["/ez"]))
        self.assertFalse(of_capacity.lacking(needs["aws"], seen["/cl"]))
        plan_ = of_capacity.balance_plan(rows, sess, pos, {"aws": 50}, {"/ez": 12.0, "/cl": 12.0}, dict(POLICY),
                                         Balance.NOW, connectors=(needs, seen))
        self.assertEqual([(m["to"], m["reason"][:6]) for m in plan_], [("/cl", "relief")])  # /ez resets first
        seen["/ez"] = seen["/cl"]  # signed in there too: still one move for the session, not relief and fill
        plan_ = of_capacity.balance_plan(rows, sess, pos, {"aws": 50}, {"/ez": 12.0, "/cl": 12.0}, dict(POLICY),
                                         Balance.NOW, connectors=(needs, seen))
        self.assertEqual(len(plan_), 1)
        r = Rescue()  # a session already stopped: the account with the sign-in first, else any (it cannot work at all)
        needs, have = {"st1": {"bound:acme/slack"}}, {"/ap": {"bound:known"}, "/gg": {"bound:acme/slack", "bound:known"}}
        m = of_capacity.rescue_plan(r.rows(), r.SESS, r.POS, {"st1": r.NOW - 600}, r.STOPS_ONLY, r.NOW, tools=(needs, have))
        self.assertEqual([x["to"] for x in m], ["/gg"])
        have["/gg"] = {"bound:known"}
        m = of_capacity.rescue_plan(r.rows(), r.SESS, r.POS, {"st1": r.NOW - 600}, r.STOPS_ONLY, r.NOW, tools=(needs, have))
        self.assertEqual([x["to"] for x in m], ["/ap"])


class Stay(unittest.TestCase):
    """2026-11-16: the founder reaches of_conductor from his phone, signed in to the Claude app on that session's
    account; a move breaks the link. capacity.yaml `stay` positions move only by rescue."""

    def test_fill_and_relief_leave_a_stay_position_rescue_still_moves_it(self):
        from unittest import mock
        b = Balance()
        b.setUp()
        rows, pos = b.rows(), [{"name": "aws", "holder": {"session": "aws"}}]
        sess = {"/cl": [], "/ez": [], "/gg": [{"session": "aws", "status": "idle"}]}
        args = (rows, sess, pos, {"aws": 50}, {"/ez": 12.0, "/cl": 12.0})
        self.assertEqual(len(of_capacity.balance_plan(*args, dict(POLICY), Balance.NOW)), 1)  # relief, as a control
        self.assertEqual(of_capacity.balance_plan(*args, dict(POLICY, stay=["aws"]), Balance.NOW), [])
        r = Rescue()
        m = of_capacity.rescue_plan(r.rows(), r.SESS, r.POS, {"st1": r.NOW - 600}, dict(r.STOPS_ONLY, stay=["st1"]),
                                    r.NOW)
        self.assertEqual([x["position"] for x in m], ["st1"])  # stopped on its limit: it moves anyway



class Restore(unittest.TestCase):
    """2026-11-18: a rescue of 11-17 19:50Z put a user organization's cloud keeper on an account without its Cloudflare;
    it ran there 14 hours and its daily token watch failed, while another account had the tool and room from 22:05Z.
    Nothing moved it back: fill and relief only avoid an account without a session's tools. Restore holds its target
    to fill's tests and to tools read present there (of_review, PR 325)."""
    NOW = Balance.NOW
    POS = [{"name": n, "holder": {"session": n}} for n in ("cf", "k8s_keeper", "of_conductor", "busy1")]

    def setUp(self):
        self.b = Balance()
        self.b.setUp()

    def seen(self, ez=("plugin:cloudflare",), cl=(), ez_conn=(), cl_conn=()):
        """Accounts read in full: their plugins and `claude mcp list` (connectors and the plugins' sign-ins)."""
        return {"/ez": of_capacity.account_tools(set(ez_conn), set(ez)) if ez is not None else None,
                "/cl": of_capacity.account_tools(set(cl_conn), set(cl)),
                "/gg": of_capacity.account_tools(set(), set(ez or ()))}

    def plan(self, who="cf", status="idle", rows=None, seen=None, needs=None, rates=None, pol=None, skip=(),
             stopped=(), contexts=None, wcalib=None, ez_sessions=()):
        sess = {"/ez": list(ez_sessions), "/cl": [{"session": who, "status": status}], "/gg": []}
        return of_capacity.balance_plan(
            rows or self.b.rows(), sess, self.POS, dict({who: 3}, **(rates or {})), {"/ez": 12.0, "/cl": 12.0},
            pol or dict(POLICY), self.NOW, skip=skip, contexts=contexts, wcalib=wcalib, stopped=stopped,
            connectors=(needs or {who: {"plugin:cloudflare"}}, seen or self.seen()))

    def restores(self, m):
        return [(x["position"], x["from"], x["to"]) for x in m if x["reason"].startswith("restore")]

    def test_a_holder_without_its_tool_goes_back_to_one_that_holds_it(self):
        m = self.plan()
        self.assertEqual(self.restores(m), [("cf", "/cl", "/ez")])  # its $3/h is under min_rate: fill leaves it
        self.assertEqual(m[0]["reason"], "restore: cloudflare missing on /cl; /ez holds all it uses and has room")
        self.assertEqual(of_capacity.balance_why(m[0]["reason"]),
                         "cloudflare (a tool your position uses) was missing on the old account; this one has it "
                         "signed in.")

    def test_only_a_target_read_to_hold_every_tool(self):
        self.assertEqual(self.restores(self.plan(seen=self.seen(ez=None))), [])  # no reading at all
        unread = dict(self.seen(), **{"/ez": of_capacity.account_tools({"Slack"}, None)})  # plugins unreadable
        self.assertEqual(self.restores(self.plan(seen=unread)), [])
        self.assertEqual(self.restores(self.plan(seen=self.seen(ez=()))), [])  # read, and without it
        needs = {"cf": {"bound:acme/cf"}}
        seen = {"/cl": {"bound:known"}, "/ez": {"bound:acme/cf"}, "/gg": None}  # claude mcp list did not answer on /ez
        self.assertEqual(self.restores(self.plan(needs=needs, seen=seen)), [])
        seen["/ez"] = {"bound:acme/cf", "bound:known"}
        self.assertEqual(self.restores(self.plan(needs=needs, seen=seen)), [("cf", "/cl", "/ez")])
        unknown = dict(self.seen(), **{"/cl": None})  # what it has where it runs is unknown: no move on a guess
        self.assertEqual(self.restores(self.plan(seen=unknown)), [])
        self.assertEqual(self.restores(self.plan(seen=self.seen(cl=("plugin:cloudflare",)))), [])  # nothing missing
        # Codex review, PR 328: a plugin counts only where claude mcp list answered (its sign-in read) ...
        no_list = dict(self.seen(), **{"/ez": of_capacity.account_tools(None, {"plugin:cloudflare"})})
        self.assertEqual(self.restores(self.plan(seen=no_list)), [])
        # ... and a restore never trades a connector it has for the plugin it lacks
        needs = {"cf": {"plugin:cloudflare", "Slack"}}
        self.assertEqual(self.restores(self.plan(needs=needs, seen=self.seen(cl_conn=("Slack",)))), [])
        self.assertEqual(self.restores(self.plan(needs=needs, seen=self.seen(cl_conn=("Slack",), ez_conn=("Slack",)))),
                         [("cf", "/cl", "/ez")])
        self.assertEqual(self.restores(self.plan(needs=needs, seen=self.seen())), [("cf", "/cl", "/ez")])  # no Slack to lose

    def test_the_target_passes_fill_tests(self):
        # its week reaches week_max within relief_ahead_h at its load: relief's own stall, so no target
        rows = self.b.rows(e_week_reset_h=90)
        rows[0]["windows"]["seven_day"]["utilization"] = 90
        m = self.plan(rows=rows, rates={"busy1": 40}, wcalib={"/ez": 26.0},
                      ez_sessions=[{"session": "busy1", "status": "busy"}])
        self.assertEqual(self.restores(m), [])
        self.assertEqual(self.restores(self.plan(contexts={"cf": 20_000_000})), [])  # a $160 resume over the $120 cap
        self.assertEqual(self.restores(self.plan(rates={"cf": 500})), [])  # its rate over the target's room
        rows = self.b.rows()
        rows[0]["windows"]["seven_day"]["utilization"] = 97
        self.assertEqual(self.restores(self.plan(rows=rows)), [])
        # Codex review, PR 328: a target with no week reading; one where a session stopped after its probe; one that
        # rescue fills in the same plan
        rows = self.b.rows()
        rows[0]["windows"] = {}
        self.assertEqual(self.restores(self.plan(rows=rows)), [])
        rows = self.b.rows()
        rows[0]["observed_at"] = of_capacity._iso(self.NOW - 600)
        m = self.plan(rows=rows, ez_sessions=[{"session": "busy1", "status": "busy"}], stopped={"busy1": self.NOW - 60})
        self.assertEqual(self.restores(m), [])
        sess = {"/ez": [], "/cl": [{"session": "cf", "status": "idle"}], "/gg": []}
        m = of_capacity.balance_plan(self.b.rows(), sess, self.POS, {"cf": 3}, {"/ez": 12.0, "/cl": 12.0}, dict(POLICY),
                                     self.NOW, connectors=({"cf": {"plugin:cloudflare"}}, self.seen()),
                                     incoming={"/ez": 10_000.0})
        self.assertEqual(self.restores(m), [])

    def test_a_source_whose_probe_failed_still_has_holders_to_restore(self):
        rows = self.b.rows()
        rows[1] = {"config_dir": "/cl", "logged_in": None, "windows": {}, "error": "probe timed out"}
        self.assertEqual(self.restores(self.plan(rows=rows)), [("cf", "/cl", "/ez")])

    def test_never_busy_production_stay_resting_or_stopped(self):
        self.assertEqual(self.restores(self.plan(status="busy")), [])
        self.assertEqual(self.restores(self.plan(who="k8s_keeper")), [])
        self.assertEqual(self.restores(self.plan(who="of_conductor", pol=dict(POLICY, stay=["of_conductor"]))), [])
        self.assertEqual(self.restores(self.plan(skip={"cf"})), [])  # moved lately, or its move failed
        self.assertEqual(self.restores(self.plan(stopped={"cf"})), [])  # rescue and the wake take a stopped one

    def test_decide_leaves_a_stopped_session_to_rescue(self):
        sess = {"/ez": [], "/cl": [{"session": "cf", "status": "idle"}], "/gg": []}
        args = (self.b.rows(), sess, self.POS, {"cf": 3}, {"/ez": 12.0, "/cl": 12.0}, dict(POLICY), self.NOW)
        conn = ({"cf": {"plugin:cloudflare"}}, self.seen())
        m = of_capacity.decide(*args, connectors=conn)
        self.assertEqual([(x["position"], x["reason"][:8]) for x in m], [("cf", "restore:")])
        m = of_capacity.decide(*args, stopped={"cf": self.NOW - 600}, connectors=conn)
        self.assertEqual([(x["position"], x["to"], x["reason"][:7]) for x in m], [("cf", "/ez", "rescue:")])


class PluginOfAnotherMarketplace(unittest.TestCase):
    """2026-11-18: an account's only Cloudflare plugin was cloudflare@anthropic-plugin-directory, the founder's
    key-creating sign-in (labelled), on the same server url as the catalogue's plain cloudflare@claude-plugins-official.
    The check by url called the plain tool signed in there, so a rescue onto it never said Cloudflare was missing."""
    CF = "https://mcp.cloudflare.com/mcp"
    DOCS = {"acme": {"tools_mode": "enforce", "tools": {
        "cf": {"kind": "plugin", "plugin": "cloudflare@claude-plugins-official", "url": CF, "account_bound": True,
               "acts_as": "the owner", "scope": "write", "keeper": "acme_org"},
        "cf-keys": {"kind": "plugin", "plugin": "cloudflare@anthropic-plugin-directory", "url": CF,
                    "account_bound": True, "signin": "keys", "acts_as": "the owner", "scope": "write",
                    "keeper": "acme_org"}},
        "positions": [{"name": "aws", "tools": ["cf"]}, {"name": "net", "tools": ["cf-keys"]}]}}

    def test_the_plain_tool_is_missing_where_only_the_other_plugin_is_installed_and_no_task_is_filed(self):
        from unittest import mock
        asks = []
        with tempfile.TemporaryDirectory() as ml, tempfile.TemporaryDirectory() as cl:
            for d, pid in ((ml, "cloudflare@anthropic-plugin-directory"), (cl, "cloudflare@claude-plugins-official")):
                os.makedirs(os.path.join(d, "plugins"))
                with open(os.path.join(d, "plugins", "installed_plugins.json"), "w") as fh:
                    json.dump({"version": 2, "plugins": {pid: [{}]}}, fh)
            labels = {os.path.realpath(ml): {"cloudflare@anthropic-plugin-directory": "keys"}}
            servers = lambda d, now=None, run=None: [("plugin:cloudflare:cloudflare", self.CF, True)]  # noqa: E731
            with mock.patch.multiple(of_capacity, tool_docs=lambda: self.DOCS, signin_labels=lambda: labels,
                                     mcp_servers=servers, load_cache=lambda: {}, save_cache=lambda c: None,
                                     config_dirs=lambda: [ml, cl], live_sessions=lambda d: [],
                                     dir_of_position=lambda p, s: cl, account_of=lambda d: "x@example.com"):
                self.assertEqual([i for i, _ in of_capacity.missing_signins("aws", ml)], ["acme/cf"])
                self.assertEqual(of_capacity.missing_signins("aws", cl), [])
                self.assertEqual(of_capacity.missing_signins("net", ml), [])  # the labelled one is there
                self.assertEqual([i for i, _ in of_capacity.missing_signins("net", cl)], ["acme/cf-keys"])
                with self.assertRaisesRegex(of_capacity.MoveError, "not signed in on .*acme/cf"):
                    of_capacity.move({"name": "aws", "holder": {"session": "s1"}}, ml,
                                     ask=lambda t, task=None: asks.append((t, task)), log=lambda x: None)
        self.assertEqual(asks, [])  # signing in does not fix it: capacity.yaml `signins:` decides



class OtherHarnesses(unittest.TestCase):
    """The founder, prompt 01a1ee3c-3b01 (2026-11-18): use the Codex and Muse subscriptions too, since the Claude
    accounts keep running out. `of capacity status` shows them beside the Claude accounts (RFC 0003 §4.7)."""
    T0 = 1_800_000_000

    def rollout(self, home, used=12.5, minutes=10080, at=None, extra=None, account=None, filename="rollout-1.jsonl"):
        d = os.path.join(home, "sessions", "2026", "11", "18")
        os.makedirs(d, exist_ok=True)
        rl = {"limit_id": "codex", "primary": {"used_percent": used, "window_minutes": minutes,
                                               "resets_at": self.T0 + 3 * 86400},
              "secondary": extra, "credits": {"has_credits": True, "unlimited": False, "balance": "1"},
              "plan_type": "pro", "rate_limit_reached_type": None}
        path = os.path.join(d, filename)
        with open(path, "w") as fh:
            fh.write(json.dumps({"timestamp": "x", "type": "session_meta", "payload": {"creator_account_id": account,
                                "creator_user_id": "another-id"}}) + "\n")
            fh.write(json.dumps({"timestamp": of_capacity._iso(at or self.T0), "type": "event_msg",
                                 "payload": {"type": "token_count", "rate_limits": rl}}) + "\n")
        return path

    def test_the_codex_bucket_wins_over_a_later_reading_of_another_bucket(self):
        with tempfile.TemporaryDirectory() as home:
            path = self.rollout(home, used=30)
            other = {"limit_id": "other-model", "primary": {"used_percent": 95, "window_minutes": 10080,
                                                            "resets_at": self.T0 + 86400}, "plan_type": "pro"}
            with open(path, "a") as fh:
                fh.write(json.dumps({"timestamp": of_capacity._iso(self.T0 + 60), "type": "event_msg",
                                     "payload": {"type": "token_count", "rate_limits": other}}) + "\n")
            r = of_capacity.codex_reading(home)
        self.assertEqual(r["windows"]["seven_day"]["utilization"], 30)

    def test_seat_identity_reads_only_the_first_line_of_the_newest_rollout(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertIsNone(of_capacity.codex_seat_account(home))
            old = self.rollout(home, account="old", filename="z.jsonl")
            newest = self.rollout(home, account="current", filename="a.jsonl")
            os.utime(old, (1, 1))
            os.utime(newest, (2, 2))
            with open(newest, "rb") as fh:
                first_line = fh.readline()
            opened = mock.mock_open(read_data=first_line)

            def guarded(path, *args, **kwargs):
                self.assertFalse(os.fspath(path).endswith("auth.json"))
                self.assertEqual(path, newest)
                return opened(path, *args, **kwargs)

            with mock.patch("builtins.open", guarded):
                self.assertEqual(of_capacity.codex_seat_account(home), hashlib.sha256(b"current").hexdigest()[:12])
            opened.return_value.readline.assert_called_once_with()
            opened.return_value.read.assert_not_called()
            for bad in (b"[]", b"not json", b'{"type":"session_meta","payload":{}}'):
                with open(newest, "wb") as fh:
                    fh.write(bad + b"\n" + first_line)
                self.assertIsNone(of_capacity.codex_seat_account(home))

    def test_seat_rows_probe_their_own_home_and_display_shared_limits_and_training(self):
        with tempfile.TemporaryDirectory() as home, \
                mock.patch.multiple(of_capacity, STATE_DIR=home, codex_bin=lambda: "codex"):
            seats = [{"name": n, "home": os.path.join(home, n)} for n in ("work", "alias", "other", "absent")]
            for seat in seats[:3]:
                self.rollout(seat["home"], account="separate" if seat["name"] == "other" else "shared")
                with tempfile.NamedTemporaryFile(dir=seat["home"], delete=False) as marker:
                    pass
                os.rename(marker.name, os.path.join(seat["home"], "auth.json"))
            runner = mock.Mock()
            rows = of_capacity.codex_seat_rows(seats, now=self.T0, fresh=True, runner=runner)
        self.assertEqual([r["seat"] for r in rows], ["work", "alias", "other"])
        self.assertEqual([call.kwargs["env"]["CODEX_HOME"] for call in runner.call_args_list], [r["home"] for r in rows])
        self.assertEqual(rows[0]["account"], rows[1]["account"])
        training = {rows[0]["account"]: {"training": "on"}, rows[2]["account"]: {"training": "off"}}
        lines = of_capacity.format_other(rows, None, training).splitlines()
        self.assertTrue(lines[1].startswith("codex:work"))
        self.assertIn("ChatGPT pro", lines[1])
        self.assertNotIn("same account", lines[1])
        self.assertIn("same account as work: one seat's limits", lines[2])
        self.assertIn("training on", lines[2])
        self.assertIn("training off", lines[3])

    def test_codex_placement_uses_the_tightest_window_and_checks_the_seats_card(self):
        def row(account, short, week, **kwargs):
            return dict(account=account, windows={"five_hour": {"utilization": short},
                                                  "seven_day": {"utilization": week}}, **kwargs)

        busy = row("one", 90, 10)
        private = row("two", 50, 30)
        public = row("three", 0, 40)
        rows = [busy, row("one", 0, 0), private, public, row("four", 0, 0, error="failed"),
                row("five", 0, 0, limit_reached="week"), {"account": "six"}]
        self.assertEqual(of_capacity.codex_headroom(busy), 10)
        self.assertIsNone(of_capacity.codex_headroom(rows[-1]))
        self.assertIs(of_capacity.codex_seat_for(rows), public)
        card = {"ref": "OF-27", "event_id": "ev", "task": True, "claimed_at": "t", "state": "done", "org": "team"}
        docs = {"team": {"training_cards": {"codex": "OF-27"}}}
        readings = {"two": {"training": "off", "card_event": "ev"}, "three": {"training": "on"}}
        with mock.patch.object(of_capacity, "_asks", return_value=[card]), \
                mock.patch.object(of_capacity, "tool_docs_all", return_value=docs):
            self.assertIs(of_capacity.codex_seat_for(rows, public=False, training=readings), private)
            card["state"] = "open"
            self.assertIsNone(of_capacity.codex_seat_for(rows, public=False, training=readings))
        self.assertIsNone(of_capacity.codex_seat_for(rows[-3:]))

    def test_training_by_seat_and_legacy_readings_from_the_recording_machine(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, CODEX_HOME=home), \
                mock.patch.object(of_capacity.of_events, "machine_id", return_value="local"):
            self.rollout(home, account="legacy")
            default = of_capacity.codex_seat_account(home)
            conn = sqlite3.connect(":memory:")
            self.addCleanup(conn.close)
            conn.execute("CREATE TABLE event (name TEXT, ts TEXT, payload TEXT, host_id TEXT)")

            def emit(kind, payload, host="local"):
                conn.execute("INSERT INTO event VALUES (?, ?, ?, ?)",
                             ("of." + kind, of_capacity._iso(self.T0 + conn.total_changes), json.dumps(payload), host))

            for account in ("aaaa11112222", "bbbb11112222"):
                of_capacity.record_training("codex", "on", "plan", account, emit=emit, seat_account=account)
            of_capacity.record_training("codex", "on", "plan", "updated", emit=emit, seat_account="aaaa11112222")
            legacy = {"harness": "codex", "training": "off", "account": "plan"}
            emit("harness_training", dict(legacy, source="local"))
            emit("harness_training", dict(legacy, source="remote"), host="remote")
            emit("harness_training", dict(legacy, source="unidentified", seat_account=None))
            emit("harness_training", {"harness": "muse", "training": "on"})
            by_seat = of_capacity.training_readings_by_seat(conn)
            self.assertEqual({k: r["source"] for k, r in by_seat.items()},
                             {default: "local", "aaaa11112222": "updated", "bbbb11112222": "bbbb11112222"})
            self.assertEqual(by_seat["aaaa11112222"]["account"], "plan")
            self.assertEqual(of_capacity.training_readings(conn)["codex"]["source"], "unidentified")

    def test_training_cli_records_the_selected_seat_and_defaults_to_main(self):
        import of_accounts
        import runpy
        main = runpy.run_path(os.path.join(HERE, "..", "bin", "of"))["main"]
        with tempfile.TemporaryDirectory() as home:
            seats = [{"name": n, "home": os.path.join(home, n)} for n in ("work", "main")]
            for seat in seats:
                self.rollout(seat["home"], account=seat["name"])
            args = ["capacity", "training", "codex", "--set", "off", "--source", "OF-27", "--account", "plan"]
            card = {"ref": "OF-27", "event_id": "ev", "task": True, "claimed_at": "t", "state": "done", "org": "team"}
            with mock.patch.dict(main.__globals__, _load_quiet=lambda: []), \
                    mock.patch.object(of_accounts, "codex_seats", return_value=seats), \
                    mock.patch.object(of_capacity, "training_readings", return_value={}), \
                    mock.patch.object(of_capacity, "_asks", return_value=[card]), \
                    mock.patch.object(of_capacity, "tool_docs_all", return_value={"team": {"training_cards": {"codex": "OF-27"}}}), \
                    mock.patch.object(of_capacity.of_events, "emit") as emit, mock.patch("builtins.print"):
                for seat, flags in zip(seats, (["--seat", "work"], [])):
                    self.assertEqual(main(args + flags), 0)
                    kind, payload = emit.call_args.args
                    self.assertEqual(kind, "harness_training")
                    self.assertEqual(payload["account"], "plan")
                    self.assertEqual(payload["card_event"], "ev")
                    self.assertEqual(payload["seat_account"], of_capacity.codex_seat_account(seat["home"]))

    def test_the_newest_rollout_reading_names_its_windows_by_length(self):
        with tempfile.TemporaryDirectory() as h:
            self.assertIsNone(of_capacity.codex_reading(h))
            self.rollout(h, extra={"used_percent": 40, "window_minutes": 300, "resets_at": self.T0 + 3600})
            r = of_capacity.codex_reading(h)
        self.assertEqual(r["plan"], "pro")
        self.assertEqual(r["windows"]["seven_day"]["utilization"], 12)
        self.assertEqual(r["windows"]["five_hour"], {"utilization": 40, "resets_at": of_capacity._iso(self.T0 + 3600)})
        self.assertTrue(r["credits"])

    def test_a_probe_runs_only_when_the_reading_is_old_and_codex_is_signed_in(self):
        from unittest import mock
        runs = []
        with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as st, \
                mock.patch.multiple(of_capacity, STATE_DIR=st, codex_bin=lambda: "/bin/codex"):
            self.assertIsNone(of_capacity.codex_status(now=self.T0, home=h, runner=lambda *a, **k: runs.append((a, k))))
            open(os.path.join(h, "auth.json"), "w").close()
            self.rollout(h, at=self.T0 - 60)
            r = of_capacity.codex_status(now=self.T0, home=h, runner=lambda *a, **k: runs.append((a, k)))
            self.assertEqual((runs, r["windows"]["seven_day"]["utilization"]), ([], 12))  # fresh: no probe
            of_capacity.codex_status(now=self.T0 + 3600, home=h, runner=lambda *a, **k: runs.append((a, k)))
            of_capacity.codex_status(now=self.T0 + 3600, home=h, probe=False, runner=lambda *a, **k: runs.append((a, k)))
        self.assertEqual(len(runs), 1)
        argv, kw = runs[0]
        self.assertEqual(argv[0][:6], ["/bin/codex", "exec", "--ignore-user-config", "--skip-git-repo-check",
                                       "--sandbox", "read-only"])  # no MCP server, notify or hook of the account
        self.assertEqual((kw["env"]["OF_PROBE"], kw["env"]["OF_AUTOMATION"], kw["env"]["SSH_AUTH_SOCK"]), ("1", "1", ""))
        self.assertFalse([k for k in kw["env"] if k.startswith("HERDR_")])

    def test_training_readings_are_recorded_with_their_source_and_muse_follows_its_settings(self):
        # RFC 0008 §3.1: of subagent run sends Codex material that is not public only while the latest reading is off
        out = []
        emit = lambda kind, payload: out.append((kind, payload))  # noqa: E731
        pol = {"ofteam": {"card_prefix": "OF", "training_cards": {"codex": "OF-27"},
                          "positions": [{"name": "of_capacity"}]}, "other": {"positions": []}}
        cards = [{"ref": "OF-27", "event_id": "01a1ee41-3cdd-74bc", "task": True, "state": "done", "closed_by": "e9",
                  "claimed_at": "2026-11-18T10:56:22Z", "position": "of_capacity"},
                 {"ref": "OF-30", "event_id": "01a1ee50-0000-7000", "task": True, "state": "open", "position": "of_capacity"},
                 {"ref": "OF-26", "event_id": "01a1ee40-c757-743d", "task": None, "state": "answered", "closed_by": "e8",
                  "position": "of_capacity"},
                 {"ref": "OF-31", "event_id": "01a1ee91-961b-0000", "task": None, "state": "answered", "closed_by": "e7",
                  "position": "of_capacity"}]
        rec = lambda src, cs=cards, p=pol: of_capacity.record_training("codex", "off", "ChatGPT pro", src, emit=emit,  # noqa: E731
                                                                        cards=cs, docs=p)
        rec("of-27")
        self.assertEqual(out[0], ("harness_training", {"harness": "codex", "training": "off", "account": "ChatGPT pro",
                                                       "source": "of-27", "card": "OF-27", "seat_account": None,
                                                       "card_event": "01a1ee41-3cdd-74bc", "closed_by": "e9"}))
        # of_review and of_secrets-review, PR 334: only the card named for the setting, closed by the owner's own claim
        for src in ("OF-99999", "the founder said so in my session", "x", "OF-30", "OF-26", "OF-31"):
            with self.assertRaisesRegex(ValueError, "training_cards"):
                rec(src)
        unclaimed = [dict(cards[0], claimed_at=None)] + cards[1:]  # its asker closed it on its own word
        with self.assertRaisesRegex(ValueError, "training_cards"):
            rec("OF-27", unclaimed)
        with self.assertRaisesRegex(ValueError, "training_cards"):
            rec("OF-27", cards, {})  # no registry names a card for the setting
        other_done = cards + [{"ref": "OF-32", "event_id": "01a1ef00-0000-7000", "task": True, "state": "done",
                               "claimed_at": "2026-11-18T13:00:00Z", "closed_by": "e6", "position": "of_capacity"}]
        from unittest import mock
        with mock.patch.object(of_capacity, "load_policy", lambda: {"training_cards": {"codex": "OF-32"}}):
            with self.assertRaisesRegex(ValueError, "training_cards"):  # capacity.yaml names nothing
                rec("OF-32", other_done)
        of_capacity.record_training("codex", "on", None, "a reading", emit=emit, cards=cards, docs=pol)  # locks only
        for bad in (("gemini", "off", "x"), ("codex", "maybe", "x"), ("codex", "off", " ")):
            with self.assertRaises(ValueError):
                of_capacity.record_training(bad[0], bad[1], None, bad[2], emit=emit, cards=cards, docs=pol)
        # the reader re-checks the card on each read
        good = {"codex": {"training": "off", "card_event": "01a1ee41-3cdd-74bc"}}
        self.assertEqual(of_capacity.training_verified("codex", good, cards, pol), "off")
        self.assertIsNone(of_capacity.training_verified("codex", {"codex": {"training": "off"}}, cards, pol))
        self.assertIsNone(of_capacity.training_verified("codex", good, unclaimed, pol))
        self.assertEqual(of_capacity.training_verified("codex", {"codex": {"training": "on"}}, cards, pol), "on")
        # a card's organization comes from the card itself (its filer's registry), never from a declared prefix
        b12 = {"ref": "BE-9", "event_id": "01a1ef0f-0000-7000", "task": True, "state": "done", "claimed_at": "t",
               "org": "b"}
        as1 = {"ref": "AS-1", "event_id": "01a1ef0f-1111-7000", "task": True, "state": "open", "org": "a"}
        two = {"a": {"card_prefix": "AS", "training_cards": {"codex": "AS-1"}},
               "b": {"card_prefix": "BE", "training_cards": {"codex": "BE-9"}}}
        rb = {"codex": {"training": "off", "card_event": "01a1ef0f-0000-7000"}}
        self.assertIsNone(of_capacity.training_verified("codex", rb, [as1, b12], two))  # a names an open card: unknown
        # of_review's repro: a third registry declaring prefix AS with no card changes nothing
        self.assertIsNone(of_capacity.training_verified("codex", rb, [as1, b12], dict(two, c={"card_prefix": "AS"})))
        self.assertEqual(of_capacity.training_verified("codex", rb, [as1, b12], two, org="b"), "off")  # b's own runs
        self.assertIsNone(of_capacity.training_verified("codex", rb, [as1, b12], two, org="a"))
        # a second organization may name the card its own organization names for the same harness...
        lean = dict(pol, orgb={"training_cards": {"codex": "OF-27"}})
        self.assertEqual(of_capacity.training_cards_named("codex", lean, cards=cards), {"ofteam": "OF-27", "orgb": "OF-27"})
        self.assertEqual(of_capacity.training_verified("codex", good, cards, lean), "off")
        # ...never one its own organization does not name for it, nor a card no registry can place
        stolen = dict(pol, orgb={"training_cards": {"codex": "OF-30"}})
        self.assertEqual(of_capacity.training_cards_named("codex", stolen, cards=cards), {"ofteam": "OF-27"})
        self.assertIsNone(of_capacity.training_verified("codex", good, cards, stolen))  # a registry's entry is bad
        self.assertEqual(of_capacity.training_cards_named("codex", {"orgb": {"training_cards": {"codex": "OF-27"}}},
                                                          cards=cards), {})
        self.assertEqual(of_capacity.training_cards_named("codex", {"x": {"training_cards": "OF-27"}}, cards=cards), {})
        out.clear()
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "settings.json")
            with open(path, "w") as fh:
                json.dump({"contributor_mode_enabled": False}, fh)
            self.assertEqual(of_capacity.sync_muse_training(latest={}, path=path, emit=emit), "off")
            self.assertIsNone(of_capacity.sync_muse_training(latest={"muse": {"training": "off"}}, path=path, emit=emit))
            with open(path, "w") as fh:
                json.dump({"contributor_mode_enabled": True}, fh)
            self.assertEqual(of_capacity.sync_muse_training(latest={"muse": {"training": "off"}}, path=path, emit=emit),
                             "on")
        self.assertEqual([p["training"] for k, p in out], ["off", "on"])
        self.assertIn("not read", of_capacity._training_text(None))
        self.assertIn("training off since", of_capacity._training_text({"training": "off", "ts": "2026-11-18T11:40:28Z",
                                                                         "source": "s"}))

    def test_a_plugin_inventory_of_another_shape_is_unreadable_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "plugins"))
            with open(os.path.join(d, "plugins", "installed_plugins.json"), "w") as fh:
                fh.write("[]")
            self.assertIsNone(of_capacity.plugin_ids_installed(d))
            entry = {"kind": "plugin", "plugin": "cloudflare@claude-plugins-official", "url": "https://x/mcp"}
            from unittest import mock
            with mock.patch.object(of_capacity, "mcp_servers", lambda dd, now=None, run=None: [("p", "https://x/mcp", True)]):
                self.assertIsNone(of_capacity.signed_in(entry, d, labels={}))  # connected: the url cannot tell the plugin

    def test_registry_check_reads_training_cards(self):
        # of_architect, PR 334: a known harness to a task card of this organization, or one its own organization names
        cards = [{"ref": "OF-27", "task": True, "org": "ofb"}, {"ref": "OF-31", "task": None, "org": "ofb"},
                 {"ref": "AC-5", "task": True, "org": "acme"}, {"ref": "OF-40", "task": True, "org": "ofb"}]
        docs = {"ofb": {"card_prefix": "OF", "training_cards": {"codex": "OF-27"}}, "acme": {"card_prefix": "AC"}}
        ok = lambda tc, o="ofb": of_capacity.training_cards_problems(o, dict(docs[o], training_cards=tc), cards,  # noqa: E731
                                                                      docs=dict(docs, **{o: dict(docs[o], training_cards=tc)}))
        self.assertEqual(ok({"codex": "OF-27"}), [])
        self.assertEqual(of_capacity.training_cards_problems("ofb", {}, cards), [])
        # a second organization may name the card its own organization names for the same harness (one setting)
        self.assertEqual(ok({"codex": "OF-27"}, "acme"), [])
        for tc, o, why in (({"gemini": "OF-27"}, "ofb", "not a harness"), ({"codex": "27"}, "ofb", "no card id"),
                           ({"codex": "AC-5"}, "ofb", "is acme's card"), ({"codex": "OF-99"}, "ofb", "no card OF-99"),
                           ({"codex": "OF-31"}, "ofb", "a question"), (["OF-27"], "ofb", "a mapping"),
                           ({"codex": "OF-40"}, "acme", "does not name it")):
            self.assertIn(why, " ".join(ok(tc, o)), (tc, o))

    def test_registry_check_names_a_shared_card_prefix(self):
        cards = [{"ref": "AS-1", "task": True, "org": "acme-shop"}]
        docs = {"acme-shop": {"card_prefix": "AS", "training_cards": {"codex": "AS-1"}},
                "alpha-store": {"card_prefix": "AS"}}
        lines = of_capacity.training_registry_problems(docs, cards)
        self.assertEqual(len(lines), 1)
        self.assertIn("card prefix AS is also alpha-store's", lines[0])
        self.assertEqual(of_capacity.training_registry_problems({"acme-shop": docs["acme-shop"]}, cards), [])

    def test_an_unreadable_inventory_with_a_signed_out_server_is_missing_not_unknown(self):
        # of_review, PR 338: unknown only when a server at the url is connected; signed out or absent is missing
        from unittest import mock
        entry = {"kind": "plugin", "plugin": "cloudflare@claude-plugins-official", "url": "https://mcp.cloudflare.com/mcp"}
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "plugins"))
            with open(os.path.join(d, "plugins", "installed_plugins.json"), "w") as fh:
                fh.write("{not json")
            for servers, want in (([("plugin:cloudflare:cloudflare", entry["url"], False)], False),
                                  ([("plugin:cloudflare:cloudflare", entry["url"], True)], None), ([], False), (None, None)):
                with mock.patch.object(of_capacity, "mcp_servers", lambda dd, now=None, run=None, v=servers: v):
                    self.assertEqual(of_capacity.signed_in(entry, d, labels={}), want, servers)

    def test_a_failed_mcp_list_is_no_reading(self):
        from unittest import mock
        cache = {}
        failed = mock.Mock(returncode=1, stdout="")
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as st, mock.patch.multiple(
                of_capacity, load_cache=lambda: cache, save_cache=lambda c: None, STATE_DIR=st), \
                mock.patch.object(of_capacity.subprocess, "run", lambda *a, **k: failed):
            self.assertIsNone(of_capacity.connectors_seen(d, now=1000))
        self.assertEqual(cache, {})

    def test_the_lines_say_what_each_harness_reports(self):
        r = {"plan": "pro", "observed_at": of_capacity._iso(self.T0),
             "windows": {"seven_day": {"utilization": 0, "resets_at": of_capacity._iso(self.T0 + 86400)}}}
        text = of_capacity.format_other(r, {"model": "muse-wonder-full-1.0", "contributor_mode": True})
        self.assertIn("ChatGPT pro", text)
        self.assertIn("seven_day 0%", text)
        self.assertIn("contributor mode ON", text)
        self.assertEqual(of_capacity.format_other(None, None), "")
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "settings.json")
            with open(path, "w") as fh:
                json.dump({"contributor_mode_enabled": False, "model": "m"}, fh)
            self.assertEqual(of_capacity.muse_status(path), {"model": "m", "contributor_mode": False})
            with open(path, "w") as fh:
                json.dump({}, fh)
            self.assertEqual(of_capacity.muse_status(path)["model"], "default")  # no model named: no guess



class StartFolder(unittest.TestCase):
    """2026-11-18: a user organization's structure keeper ran in the OF team's repository, so OF wrote no binding-rules
    file for it; it asked to resume in its own folder at its next move or restart. capacity.yaml `start_in` names
    that folder, and the move carries the transcript under the folder's project key."""

    def test_the_start_folder_is_the_given_one_else_of_spawns_for_a_listed_position_else_where_it_runs(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ra, rb = os.path.realpath(a), os.path.realpath(b)
            spawn = lambda p: b  # noqa: E731  the folder of spawn would choose, from the registry
            self.assertEqual(of_capacity.start_folder("k", ra, policy={}, folder_of=spawn), ra)
            self.assertEqual(of_capacity.start_folder("k", ra, policy={"start_in": ["k"]}, folder_of=spawn), rb)
            self.assertEqual(of_capacity.start_folder("k", ra, policy={"start_in": ["other"]}, folder_of=spawn), ra)
            self.assertEqual(of_capacity.start_folder("k", ra, cwd=b, policy={"start_in": ["k"]},
                                                      folder_of=lambda p: a), rb)
            self.assertEqual(of_capacity.start_folder("k", a, policy={"start_in": ["k"]}, folder_of=lambda p: a), a)
            # of_server, PR 330: capacity.yaml never names the folder; one written there is not used
            self.assertEqual(of_capacity.start_folder("k", ra, policy={"start_in": {"k": a}}, folder_of=spawn), rb)
            with self.assertRaisesRegex(of_capacity.MoveError, "does not exist"):
                of_capacity.start_folder("k", ra, policy={"start_in": ["k"]}, folder_of=lambda p: os.path.join(b, "gone"))
            with self.assertRaisesRegex(of_capacity.MoveError, "names no folder"):
                of_capacity.start_folder("k", ra, policy={"start_in": ["k"]}, folder_of=lambda p: None)

    def test_an_untrusted_start_folder_never_stops_a_move(self):
        def untrusted(src, dst, cwd, dry_run=False):
            raise of_capacity.MoveError(f"folder {cwd} is trusted in no config", untrusted=cwd)
        logs = []
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ra, rb = os.path.realpath(a), os.path.realpath(b)
            pol, spawn = {"start_in": ["k"]}, (lambda p: b)
            self.assertEqual(of_capacity.resume_folder("k", ra, "/s", "/t", policy=pol, log=logs.append,
                                                       trust=untrusted, folder_of=spawn), ra)
            self.assertIn("trusted in no config", logs[0])
            self.assertEqual(of_capacity.resume_folder("k", ra, "/s", "/t", policy=pol, folder_of=spawn,
                                                       trust=lambda *a, **k: ("trusted", b)), rb)
            self.assertEqual(of_capacity.resume_folder("k", ra, "/s", "/t", cwd=b, policy={}, trust=untrusted), rb)
            gone = lambda p: os.path.join(b, "gone")  # noqa: E731  of_review, PR 330: a gone folder never stops a move
            self.assertEqual(of_capacity.resume_folder("k", ra, "/s", "/t", policy=pol, log=logs.append,
                                                       folder_of=gone), ra)
            self.assertIn("does not exist", logs[-1])

    def test_the_session_moves_under_the_new_key_and_one_copy_stays(self):
        import of_carry
        self.assertEqual(of_capacity.folder_key("/Users/alex/.claude/w_y"), of_carry.key_of("/Users/alex/.claude/w_y"))
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as bk:
            old = os.path.join(d, "projects", "-old")
            os.makedirs(os.path.join(old, "s1", "subagents"))
            for f, text in ((os.path.join(old, "s1.jsonl"), "main"), (os.path.join(old, "s1", "subagents", "a.jsonl"), "sub")):
                with open(f, "w") as fh:
                    fh.write(text)
            os.makedirs(os.path.join(d, "projects", "-new"))
            with open(os.path.join(d, "projects", "-new", "s1.jsonl"), "w") as fh:
                fh.write("stale")
            os.utime(os.path.join(d, "projects", "-new", "s1.jsonl"), (1000, 1000))  # older than the session's own
            out = of_capacity.rekey_session(d, "s1", "-new", bk)
            self.assertEqual(out, [os.path.join(d, "projects", "-new", "s1.jsonl"), os.path.join(d, "projects", "-new", "s1")])
            self.assertEqual(of_capacity.find_transcript(d, "s1"), out[0])  # one copy in the account
            with open(out[0]) as fh:
                self.assertEqual(fh.read(), "main")
            self.assertTrue(os.path.exists(os.path.join(out[1], "subagents", "a.jsonl")))
            with open(os.path.join(bk, "projects", "-new", "s1.jsonl")) as fh:
                self.assertEqual(fh.read(), "stale")  # what was there is kept aside, not lost
            self.assertEqual(of_capacity.rekey_session(d, "s1", "-new", bk), [])  # already there: nothing to do

    def test_the_freshest_copy_wins_and_the_others_go_aside(self):
        # of_review, PR 330: two copies in one account (a session moved back where it had run under its start folder);
        # the stale one must not become the session
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as bk:
            for k, text, t in (("-aaa-stale", "stale", 1000), ("-zzz-fresh", "fresh", 2000)):
                os.makedirs(os.path.join(d, "projects", k))
                f = os.path.join(d, "projects", k, "s1.jsonl")
                with open(f, "w") as fh:
                    fh.write(text)
                os.utime(f, (t, t))
            out = of_capacity.rekey_session(d, "s1", "-new", bk)
            with open(out[0]) as fh:
                self.assertEqual(fh.read(), "fresh")
            self.assertEqual(glob.glob(os.path.join(d, "projects", "*", "s1.jsonl")), [out[0]])  # one copy left
            with open(os.path.join(bk, "projects", "-aaa-stale", "s1.jsonl")) as fh:
                self.assertEqual(fh.read(), "stale")
            f = os.path.join(d, "projects", "-other", "s1.jsonl")  # a stale copy beside the one at the key: aside
            os.makedirs(os.path.dirname(f))
            with open(f, "w") as fh:
                fh.write("old")
            os.utime(f, (500, 500))
            self.assertEqual(of_capacity.rekey_session(d, "s1", "-new", bk), [])
            self.assertEqual(glob.glob(os.path.join(d, "projects", "*", "s1.jsonl")), [out[0]])



class Resting(unittest.TestCase):
    """2026-11-18: on the founder's OF-31 answer a user organization parked 27 positions to 11-23, their sessions still
    live; the balancer neither moves nor wakes them."""

    def test_fill_relief_and_rescue_leave_a_parked_position(self):
        b = Balance()
        b.setUp()
        rows = b.rows()
        sess = {"/cl": [], "/ez": [], "/gg": [{"session": "aws", "status": "idle"}]}
        for status, n in (("active", 1), ("parked", 0), ("finished", 0), ("parked   # to 2026-11-23", 0)):
            pos = [{"name": "aws", "holder": {"session": "aws"}, "status": status}]
            m = of_capacity.balance_plan(rows, sess, pos, {"aws": 50}, {"/ez": 12.0, "/cl": 12.0}, dict(POLICY), Balance.NOW)
            self.assertEqual(len(m), n, status)
        r = Rescue()
        pos = [dict(p, status="parked") if p["name"] == "st1" else p for p in r.POS]
        m = of_capacity.rescue_plan(r.rows(), r.SESS, pos, {"st1": r.NOW - 600}, r.STOPS_ONLY, r.NOW)
        self.assertNotIn("st1", [x["position"] for x in m])

    def test_a_parked_session_stopped_on_a_spent_account_is_no_stall(self):
        # of_review, PR 333: rescue leaves it, so the stall report must too, or invariant_step escalates it
        r = Rescue()
        stopped = {"st1": r.NOW - 600}
        pos = [dict(p, status="parked") if p["name"] == "st1" else p for p in r.POS]
        self.assertIn("st1", [x["position"] for x in of_capacity.stall_report(r.rows(), r.SESS, r.POS, stopped, [], r.NOW)])
        self.assertNotIn("st1", [x["position"] for x in of_capacity.stall_report(r.rows(), r.SESS, pos, stopped, [], r.NOW)])

    def test_a_parked_positions_stopped_session_is_not_woken(self):
        w = WakeStopped()
        out, pressed, typed, _ = w.run_it(w.DIALOG, positions=[{"name": "sec", "holder": {"session": "s1"},
                                                                  "status": "parked"}])
        self.assertEqual((pressed, typed), ([], []))


class ConnectorsCache(unittest.TestCase):
    def test_a_plugin_installed_after_the_list_was_cached_reads_it_again(self):
        from unittest import mock
        cache, runs = {}, []
        text = "plugin:cloudflare:cloudflare: https://mcp.cloudflare.com/mcp (HTTP) - ✔ Connected\n"
        with tempfile.TemporaryDirectory() as d, mock.patch.multiple(
                of_capacity, load_cache=lambda: cache, save_cache=lambda c: None, STATE_DIR=d):
            os.makedirs(os.path.join(d, "plugins"))
            path = os.path.join(d, "plugins", "installed_plugins.json")
            with open(path, "w") as fh:
                fh.write("{}")
            os.utime(path, (1000, 1000))
            run = lambda x: runs.append(x) or text
            of_capacity.connectors_seen(d, now=2000, run=run)
            of_capacity.connectors_seen(d, now=2100, run=run)
            self.assertEqual(len(runs), 1)  # fresh and unchanged: the cache answers
            os.utime(path, (2050, 2050))  # a plugin installed after the list was taken
            of_capacity.connectors_seen(d, now=2200, run=run)
            self.assertEqual(len(runs), 2)
            self.assertEqual(of_capacity.mcp_servers(d, now=2300, run=run),
                             [("plugin:cloudflare:cloudflare", "https://mcp.cloudflare.com/mcp", True)])


class WakeStopped(unittest.TestCase):
    """A reset can reopen an account while its sessions are still in the limit dialog."""
    DIALOG = ["   What do you want to do?", "   ❯ 1. Stop and wait for limit to reset",
              "     2. Wait here, then continue automatically at Nov 23 at 12am", "     3. Switch to usage credits",
              "   Enter to confirm · Esc to cancel"]
    T0 = 1_800_000_000

    def run_it(self, screen, status="idle", stop=-60, probe_at=0, account_ok=True, dry_run=False, cache=None,
               positions=None, deliver=None, later=60, other=False):
        from unittest import mock
        rows = [{"config_dir": "/m", "logged_in": True, "observed_at": of_capacity._iso(self.T0 + probe_at),
                 "windows": {"seven_day": {"utilization": 0 if account_ok else 100}}}]
        sessions = {"/m": [{"session": "s1", "name": "sec", "status": status}]}
        if other:
            rows.append(dict(rows[0], config_dir="/other"))
        pressed, typed, store = [], [], dict(cache or {})
        with mock.patch.object(of_capacity, "load_cache", side_effect=lambda: dict(store)), \
                mock.patch.object(of_capacity, "save_cache", side_effect=store.update), \
                mock.patch.object(of_capacity, "actor", return_value="of_capacity"):
            out = of_capacity.wake_stopped(
                rows, sessions, [{"name": "sec", "holder": {"session": "s1"}}] if positions is None else positions,
                now=self.T0 + later, log=lambda m: None, dry_run=dry_run,
                agent=lambda sid: {"pane_id": "p1"}, read=lambda pane: screen,
                keys=lambda pane, k: pressed.append(k),
                deliver=deliver or (lambda pane, text, sid: typed.append(text) or (True, None)),
                stopped=lambda d, sid: self.T0 + stop if stop is not None else None, sleep=lambda s: None)
        return out, pressed, typed, store

    def test_the_dialog_gets_stop_and_wait_then_a_prompt(self):
        out, pressed, typed, store = self.run_it(self.DIALOG)
        self.assertEqual((pressed, len(typed), out), (["enter"], 1, [("sec", (True, None))]))
        self.assertIn("accepts work again", typed[0])
        self.assertIn("s1", store["_woken"])

    def test_an_unsuccessful_wake_keeps_its_result_for_the_next_run(self):
        for response, result in ((("deferred", "a dialog is open in the pane"), "deferred: a dialog is open in the pane"),
                                 ((False, "pane closed"), "refused: pane closed"),
                                 (OSError("relay unavailable"), "exception: relay unavailable")):
            with self.subTest(result=result):
                relay = mock.Mock(side_effect=[response])
                _, _, _, store = self.run_it([], deliver=relay)
                self.assertEqual(store["_woken"]["s1"], {"at": self.T0 + 60, "dir": "/m", "result": result})
                due = 61 + of_capacity.WAKE_AGAIN_S
                self.assertEqual(self.run_it([], cache=store, later=due, other=True)[0], [])
                self.assertEqual(self.run_it([], cache=store, later=100)[0], [])
                _, _, typed, retried = self.run_it([], cache=store, later=due)
                self.assertEqual(len(typed), 1)
                self.assertEqual(retried["_woken"]["s1"]["result"], "delivered")

    def test_a_dry_run_leaves_no_wake_record(self):
        _, pressed, typed, store = self.run_it(self.DIALOG, dry_run=True)
        self.assertEqual((pressed, typed, store), ([], [], {}))

    def test_an_old_timestamp_can_trigger_rescue_only_on_the_original_account(self):
        cache = {"_woken": {"s1": self.T0}}
        out, _, _, store = self.run_it([], cache=cache, later=of_capacity.WAKE_AGAIN_S + 1, other=True)
        self.assertEqual(out, [])
        self.assertEqual(store["_woken"]["s1"], {"at": self.T0, "dir": "/m", "result": None})
        self.assertEqual(of_capacity.wake_history(store, {"/other": [{"session": "s1"}]}), {})

    def test_the_balancer_can_wake_its_own_holder_from_outside_that_session(self):
        pos = [{"name": "of_capacity", "holder": {"session": "s1"}}]
        for caller, pane, count in (("", "", 1), ("s2", "p2", 1), ("s1", "", 0), ("", "p1", 0)):
            with self.subTest(caller=caller, pane=pane), mock.patch.dict(
                    os.environ, CLAUDE_CODE_SESSION_ID=caller, HERDR_PANE_ID=pane):
                self.assertEqual(len(self.run_it([], positions=pos)[2]), count)

    def test_never_on_usage_credits_and_never_twice(self):
        credits = [ln.replace("❯ 1.", "  1.").replace("  3. Switch", "❯ 3. Switch") for ln in self.DIALOG]
        self.assertEqual(self.run_it(credits)[1:3], ([], []))  # cursor elsewhere: left as it is
        self.assertEqual(self.run_it(self.DIALOG, cache={"_woken": {"s1": self.T0}})[1:3], ([], []))

    def test_only_a_stop_older_than_the_probe_on_an_open_account(self):
        self.assertEqual(self.run_it(self.DIALOG, stop=30)[2], [])  # stopped after the probe said it accepts work
        self.assertEqual(self.run_it(self.DIALOG, account_ok=False)[2], [])  # still exhausted: rescue's case
        self.assertEqual(self.run_it(self.DIALOG, stop=None)[2], [])  # not stopped
        self.assertEqual(self.run_it(self.DIALOG, status="busy")[2], [])

    def test_the_screen_is_read_again_before_the_key(self):
        from unittest import mock
        credits = [ln.replace("❯ 1.", "  1.").replace("  3. Switch", "❯ 3. Switch") for ln in self.DIALOG]
        screens = [self.DIALOG, credits]
        rows = [{"config_dir": "/m", "logged_in": True, "observed_at": of_capacity._iso(self.T0),
                 "windows": {"seven_day": {"utilization": 0}}}]
        pressed, typed = [], []
        with mock.patch.object(of_capacity, "load_cache", return_value={}), \
                mock.patch.object(of_capacity, "save_cache"), mock.patch.object(of_capacity, "actor", return_value="x"):
            of_capacity.wake_stopped(rows, {"/m": [{"session": "s1", "status": "idle"}]},
                                     [{"name": "sec", "holder": {"session": "s1"}}], now=self.T0 + 60, log=lambda m: None,
                                     agent=lambda sid: {"pane_id": "p1"}, read=lambda pane: screens.pop(0),
                                     keys=lambda pane, k: pressed.append(k),
                                     deliver=lambda pane, text, sid: typed.append(text) or (True, None),
                                     stopped=lambda d, sid: self.T0 - 60, sleep=lambda s: None)
        self.assertEqual((pressed, typed), ([], []))  # the cursor moved to the paid option: no key

    def test_only_an_agent_positions_holder(self):
        # of_review on PR 318: a session no position claims (a person's own) or a position a person holds is theirs
        self.assertEqual(self.run_it(self.DIALOG, positions=[])[1:3], ([], []))
        held = [{"name": "sec", "holder": {"session": "s1", "runtime": "human", "person": "someone"}}]
        self.assertEqual(self.run_it(self.DIALOG, positions=held)[1:3], ([], []))
        other = [{"name": "sec", "holder": {"session": "s2"}}]
        self.assertEqual(self.run_it(self.DIALOG, positions=other)[1:3], ([], []))

    def test_a_limit_wait_is_cancelled_and_a_dry_run_touches_nothing(self):
        wait = ["Usage limit reached · continuing automatically at Nov 23 12am · Esc to cancel"]
        self.assertEqual(self.run_it(wait)[1], ["esc"])
        out, pressed, typed, store = self.run_it(self.DIALOG, dry_run=True)
        self.assertEqual((out, pressed, typed, store), ([("sec", "would wake (enter)")], [], [], {}))


class SigninTaskReconcile(unittest.TestCase):
    """2026-11-17: two of the founder's sign-in tasks named an account both positions had left days before
    (of_architect, msg 01a1e8df-76f1). The balancer withdraws a task once nobody needs it."""

    def run_it(self, sessions, missing, dry_run=False, moved=True):
        from unittest import mock
        title = "Sign in to linear in the account a@x"
        cache = {"_signin_tasks": {"/ap": {"acme/linear": 1.0}},
                 "_signin_open": [{"dir": "/ap", "position": "disp", "tools": ["acme/linear"], "title": title,
                                   "at": 1.0, "moved": moved}]}
        asks = [{"event_id": "e-1", "state": "open", "kind": "task", "position": "of_capacity", "text": title,
                 "session_id": "s"},
                {"event_id": "e-2", "state": "open", "kind": "task", "position": "other", "text": title}]
        emitted = []
        with mock.patch.object(of_capacity, "load_cache", side_effect=lambda: json.loads(json.dumps(cache))), \
                mock.patch.object(of_capacity, "save_cache", side_effect=lambda c: cache.update(c)), \
                mock.patch.object(of_capacity, "actor", return_value="of_capacity"), \
                mock.patch.object(of_capacity.of_events, "emit", side_effect=lambda *a, **k: emitted.append(a)):
            out = of_capacity.reconcile_signin_tasks(sessions, now=5.0, log=lambda s: None, dry_run=dry_run,
                                                     asks=asks,
                                                     missing=None if missing is None else lambda p, d, now=None: missing)
        return out, emitted, cache

    def test_an_unknown_sign_in_keeps_the_task(self):
        # of_review, PR 338: a failed claude mcp list reads unknown; the task is withdrawn as signed in only when
        # every tool reads signed in
        from unittest import mock
        entry = {"kind": "plugin", "plugin": "linear@x", "url": "https://mcp.linear.app/mcp"}
        for state, withdrawn in ((None, False), (False, False), (True, True)):
            with mock.patch.object(of_capacity, "bound_tools", lambda name, docs=None: [("acme/linear", entry)]), \
                    mock.patch.object(of_capacity, "signed_in", lambda e, d, now=None, run=None, v=state: v):
                out, _, _ = self.run_it({"/ap": [{"name": "disp"}]}, None)
            self.assertEqual(bool(out), withdrawn, state)

    def test_a_position_that_left_the_account_drops_its_task(self):
        out, emitted, cache = self.run_it({"/ap": [{"name": "else"}], "/ez": [{"name": "disp"}]}, [])
        self.assertEqual(out, [("e-1", "disp no longer runs on /ap")])
        self.assertEqual([(n, p["ask_id"]) for n, p in emitted], [("ask_withdrawn", "e-1")])  # only its own ask
        self.assertEqual(cache["_signin_open"], [])
        self.assertNotIn("acme/linear", cache["_signin_tasks"]["/ap"])  # a new need files at once

    def test_a_tool_now_signed_in_drops_its_task(self):
        out, emitted, _ = self.run_it({"/ap": [{"name": "disp"}]}, [])
        self.assertEqual(out, [("e-1", "linear signed in on /ap")])

    def test_a_task_still_needed_stays(self):
        out, emitted, cache = self.run_it({"/ap": [{"name": "disp"}]}, [("acme/linear", {})])
        self.assertEqual((out, emitted), ([], []))
        self.assertEqual(len(cache["_signin_open"]), 1)

    def test_a_refused_moves_task_stays_until_signed_in_or_the_position_is_gone(self):
        # of_review on PR 277: a move refused for want of the sign-in leaves the position where it was; its task
        # names an account it never ran on, and must not be withdrawn for that
        out, emitted, cache = self.run_it({"/ez": [{"name": "disp"}]}, [("acme/linear", {})], moved=False)
        self.assertEqual((out, emitted, len(cache["_signin_open"])), ([], [], 1))
        self.assertIn("acme/linear", cache["_signin_tasks"]["/ap"])  # the once-a-day mark stays
        out, _, _ = self.run_it({"/ez": [{"name": "disp"}]}, [], moved=False)
        self.assertEqual(out, [("e-1", "linear signed in on /ap")])
        out, _, _ = self.run_it({"/ez": [{"name": "else"}]}, [("acme/linear", {})], moved=False)
        self.assertEqual(out, [("e-1", "disp no longer runs on any account")])

    def test_a_dry_run_withdraws_nothing(self):
        out, emitted, cache = self.run_it({}, [], dry_run=True)
        self.assertEqual((len(out), emitted, len(cache["_signin_open"])), (1, [], 1))


class LabelledSignins(unittest.TestCase):
    """2026-11-16: a sign-in that can create Cloudflare API tokens as the founder, on one account, for one position
    (capacity.yaml `signins:`, catalogue `signin:`; names agreed with of_tools). The accounts' other Cloudflare
    sign-ins answer at the same url, so placement must tell them apart."""
    CF = "https://mcp.cloudflare.com/mcp"
    PLUGIN = "cloudflare@anthropic-plugin-directory"
    ENTRY = {"kind": "plugin", "plugin": PLUGIN, "url": CF, "account_bound": True, "acts_as": "the owner",
             "scope": "read", "keeper": "acme_org"}
    DOCS = {"acme": {"tools": {"cloudflare": dict(ENTRY), "cloudflare-keys": dict(ENTRY, signin="keys")},
                     "positions": [{"name": "net", "tools": ["cloudflare", "cloudflare-keys"]},
                                   {"name": "cloud", "tools": ["cloudflare"]}, {"name": "plain"}]}}

    def servers(self, d, now=None, run=None):
        return {"/mail": [("plugin:cloudflare:cloudflare", self.CF, True)],
                "/ez": [("plugin:cloudflare:cloudflare", self.CF, True)], "/ap": []}.get(d)

    def patched(self, key=PLUGIN, **kw):
        from unittest import mock
        return mock.patch.multiple(of_capacity, tool_docs=lambda: self.DOCS, mcp_servers=self.servers,
                                   load_policy=lambda: dict(POLICY, signins={"/mail": {key: "keys"}}), **kw)

    def test_a_labelled_sign_in_serves_only_entries_with_its_label(self):
        with self.patched():
            missing = {(n, d): [i for i, _ in of_capacity.missing_signins(n, d)]
                       for n in ("net", "cloud", "plain") for d in ("/mail", "/ez")}
        self.assertEqual(missing, {("net", "/mail"): [], ("net", "/ez"): ["acme/cloudflare-keys"],
                                   ("cloud", "/mail"): ["acme/cloudflare"], ("cloud", "/ez"): [],
                                   ("plain", "/mail"): [], ("plain", "/ez"): []})

    def test_the_balancer_keeps_the_labelled_plugin_to_its_positions(self):
        needs = {"n1": {"plugin:cloudflare"}, "c1": {"plugin:cloudflare"}, "o1": {"plugin:cloudflare"}}
        pos = [{"name": "net", "holder": {"session": "n1"}}, {"name": "cloud", "holder": {"session": "c1"}},
               {"name": "uncatalogued", "holder": {"session": "o1"}}]  # an organization with no catalogue
        with self.patched():
            seen = {d: of_capacity.account_tools(None, of_capacity.with_labels({"plugin:cloudflare"}, d))
                    for d in ("/mail", "/ez")}
            of_capacity.add_bound(needs, seen, pos, ["/mail", "/ez"])
        fits = {(s, d): not of_capacity.lacking(needs[s], seen[d]) for s in needs for d in seen}
        self.assertEqual(fits, {("n1", "/mail"): True, ("n1", "/ez"): False, ("c1", "/mail"): False,
                                ("c1", "/ez"): True, ("o1", "/mail"): False, ("o1", "/ez"): True})

    def test_a_symlinked_account_folder_reads_the_same_label(self):
        with tempfile.TemporaryDirectory() as t:  # of_tools keys by realpath too (PR 243 review)
            real, link = os.path.join(t, "acct"), os.path.join(t, "link")
            os.mkdir(real)
            os.symlink(real, link)
            labels = of_capacity.signin_labels({"signins": {link: {self.PLUGIN: "keys"}}})
            self.assertEqual(of_capacity.label_on(self.ENTRY, real, labels), "keys")
            self.assertEqual(of_capacity.labelled_plugins(link, labels), {"plugin:cloudflare": "keys"})

    def test_a_url_label_names_the_plugin_whose_server_it_is(self):
        with self.patched(key=self.CF + "/"):
            self.assertEqual(of_capacity.labelled_plugins("/mail"), {"plugin:cloudflare": "keys"})
            self.assertEqual(of_capacity.labelled_plugins("/ez"), {})

    def test_a_move_by_hand_away_from_the_label_stops_and_files_no_sign_in_task(self):
        from unittest import mock
        cache, asks = {}, []
        with tempfile.TemporaryDirectory() as mail, tempfile.TemporaryDirectory() as ez, mock.patch.multiple(
                of_capacity, tool_docs=lambda: self.DOCS, load_cache=lambda: cache, save_cache=lambda c: None,
                load_policy=lambda: dict(POLICY, signins={mail: {self.PLUGIN: "keys"}}),
                mcp_servers=lambda d, now=None, run=None: self.servers("/mail" if d == mail else "/ez"),
                config_dirs=lambda: [mail, ez], live_sessions=lambda d: [],
                dir_of_position=lambda p, s: mail if p["name"] == "net" else ez,
                account_of=lambda d: "x@example.com"):
            with self.assertRaisesRegex(of_capacity.MoveError, "not signed in on .*acme/cloudflare-keys [^;]*$"):
                of_capacity.move({"name": "net", "holder": {"session": "s1"}}, ez,
                                 ask=lambda t, task=None: asks.append((t, task)), log=lambda x: None)
            with self.assertRaisesRegex(of_capacity.MoveError, "not signed in on .*acme/cloudflare [^;]*$"):
                of_capacity.move({"name": "cloud", "holder": {"session": "s2"}}, mail,
                                 ask=lambda t, task=None: asks.append((t, task)), log=lambda x: None)
        self.assertEqual(asks, [])  # signing in again changes no label: nothing for the founder's hands


class McpList(unittest.TestCase):
    def test_parse_connected_connectors(self):
        text = """Checking MCP server health…

claude.ai Claude Docs: https://api.anthropic.com/v1/pages/mcp - ✔ Connected
claude.ai Slack: https://mcp.slack.com/mcp - ✔ Connected
claude.ai Google Drive: https://drivemcp.googleapis.com/mcp/v1 - ✔ Connected
claude.ai Gmail: https://gmailmcp.googleapis.com/mcp/v1 - ✘ Needs authentication
plugin:linear:linear: https://mcp.linear.app/mcp (HTTP) - ✔ Connected
"""
        self.assertEqual(of_capacity.parse_mcp_list(text), {"Claude_Docs", "Slack", "Google_Drive"})


    def test_plugins_signed_out_count_as_missing(self):
        from unittest import mock
        text = """claude.ai Claude Docs: https://api.anthropic.com/v1/pages/mcp - ✔ Connected
plugin:cloudflare:cloudflare: https://bindings.mcp.cloudflare.com/mcp (HTTP) - ! Needs authentication
plugin:linear:linear: https://mcp.linear.app/mcp (HTTP) - ✔ Connected
plugin:slack:slack: https://mcp.slack.com/mcp (HTTP) - ✘ Failed to connect
"""
        self.assertEqual(of_capacity.parse_mcp_plugins_down(text), {"plugin:cloudflare", "plugin:slack"})
        cache = {}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(of_capacity, "STATE_DIR", d), \
                mock.patch.object(of_capacity, "load_cache", lambda: cache), \
                mock.patch.object(of_capacity, "save_cache", lambda c: None), \
                mock.patch.object(of_capacity, "plugins_seen",
                                  lambda x: {"plugin:cloudflare", "plugin:linear", "plugin:superpowers"}):
            self.assertEqual(of_capacity.plugins_ready("/acct", run=lambda x: text),
                             {"plugin:linear", "plugin:superpowers"})  # a plugin without a server keeps counting
            for k in ("plugins_down", "servers"):  # a cache from before these fields: read again
                cache["_connectors"]["/acct"].pop(k)
            calls = []
            self.assertEqual(of_capacity.plugins_ready("/acct", run=lambda x: calls.append(x) or ""),
                             {"plugin:cloudflare", "plugin:linear", "plugin:superpowers"})
            self.assertEqual(calls, ["/acct"])
            self.assertEqual(of_capacity.plugins_down("/acct", run=lambda x: 1 / 0), set())  # cached now


class VaultByServiceAccount(unittest.TestCase):
    """Broker step 3: op runs only with the service account's token, values go on stdin, never in argv."""
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.tok = os.path.join(self.d.name, "sa.tok")
        with open(os.open(self.tok, os.O_WRONLY | os.O_CREAT, 0o600), "w") as fh:
            fh.write("ops_example\n")
        self.calls = []

    def tearDown(self):
        self.d.cleanup()

    def run_fake(self, out):
        def run(args, input=None, env=None, **k):
            self.calls.append((args, input, env))
            return subprocess.CompletedProcess(args, 0, out, "")
        return run

    def test_env_uses_the_service_account_only(self):
        os.environ["OP_SESSION_x"] = "desktop"
        try:
            env = of_capacity._op_env(self.tok)
        finally:
            del os.environ["OP_SESSION_x"]
        self.assertEqual(env["OP_SERVICE_ACCOUNT_TOKEN"], "ops_example")
        self.assertNotIn("OP_SESSION_x", env)
        self.assertEqual(env["SSH_AUTH_SOCK"], "")
        os.chmod(self.tok, 0o644)
        with self.assertRaises(of_capacity.MoveError):
            of_capacity._op_env(self.tok)  # readable by others: refused
        with self.assertRaises(of_capacity.MoveError):
            of_capacity._op_env(os.path.join(self.d.name, "missing"))

    def test_put_sends_the_value_on_stdin(self):
        item = of_capacity.vault_put("setup-token a@x", "sk-ant-oat01-value", {"account": "a@x"},
                                     run=self.run_fake('{"id": "it1"}'), token_file=self.tok)
        args, stdin, _ = self.calls[0]
        self.assertEqual(item, "it1")
        self.assertNotIn("sk-ant-oat01-value", " ".join(args))
        self.assertIn("sk-ant-oat01-value", stdin)
        self.assertIn(of_capacity.VAULT, args)

    def test_check_wants_one_vault_and_a_service_account(self):
        outs = iter(['{"user_type": "SERVICE_ACCOUNT"}', '[{"name": "OF Capacity"}]'])

        def run(args, **k):
            return subprocess.CompletedProcess(args, 0, next(outs), "")
        self.assertTrue(of_capacity.vault_check(run=run, token_file=self.tok)["ok"])


class Mint(unittest.TestCase):
    """Broker step 4: the token is checked against the account by probe windows before it is stored."""
    W = {"seven_day": {"resets_at": "2026-11-13T16:00:00Z"}, "five_hour": {"resets_at": "2026-11-07T12:30:00Z"}}

    def test_capture_token_through_ansi_and_wraps(self):
        tok = "sk-ant-oat01-" + "A" * 40 + "_b-C" * 5
        out = "\x1b[32mYour OAuth token (valid for 1 year):\x1b[0m\r\n\r\n" + tok[:50] + "\r\n" + tok[50:] + "\r\n\r\nStore"
        self.assertEqual(of_capacity.capture_token(out), tok)
        self.assertIsNone(of_capacity.capture_token("Open https://claude.ai/oauth/authorize?x=1"))

    def test_same_account_by_window_resets(self):
        other = {"seven_day": {"resets_at": "2026-11-12T21:00:00Z"}}
        self.assertTrue(of_capacity.same_account({"windows": self.W}, {"windows": dict(self.W)}))
        self.assertFalse(of_capacity.same_account({"windows": self.W}, {"windows": other}))
        self.assertFalse(of_capacity.same_account({"windows": {}}, {"windows": {}}))  # no reading: no proof

    def test_mint_stores_only_a_checked_token_and_never_logs_it(self):
        from unittest import mock
        tok = "sk-ant-oat01-" + "Z" * 60
        logs, stored, events = [], [], []
        with tempfile.TemporaryDirectory() as h:
            d = os.path.join(h, ".claude-x")
            os.makedirs(d)
            with open(os.path.join(d, ".claude.json"), "w") as fh:
                json.dump({"oauthAccount": {"emailAddress": "x@y"}}, fh)
            with mock.patch.object(of_capacity.of_events, "emit", lambda k, p, **kw: events.append((k, p))):
                rec = of_capacity.mint(d, log=logs.append, spawn=lambda d, log: tok,
                                       probe_fn=lambda d: {"windows": self.W},
                                       token_probe_fn=lambda t: {"windows": dict(self.W)},
                                       put=lambda title, value, fields: stored.append(value) or "item9")
                self.assertEqual((rec["vault_item"], rec["fingerprint"]), ("item9", of_capacity.fingerprint(tok)))
                self.assertEqual(stored, [tok])
                with self.assertRaises(of_capacity.MoveError):  # the browser was signed in as someone else
                    of_capacity.mint(d, log=logs.append, spawn=lambda d, log: tok,
                                     probe_fn=lambda d: {"windows": self.W},
                                     token_probe_fn=lambda t: {"windows": {"seven_day": {"resets_at": "2026-11-08T09:00:00Z"}}},
                                     put=lambda *a: stored.append("WRONG"))
        self.assertEqual(stored, [tok])
        self.assertNotIn(tok, json.dumps(events) + "".join(logs))

    def test_due_accounts(self):
        rows = [{"kind": "login", "account": "a@x"}, {"kind": "login", "account": "b@x"},
                {"kind": "setup_token", "account": "a@x", "expires_at": "2027-11-07"},
                {"kind": "setup_token", "account": "b@x", "expires_at": "2026-11-09"}]
        self.assertEqual([r["account"] for r in of_capacity.due_accounts(rows, now=Balance.NOW)], ["b@x"])


class VaultSetup(unittest.TestCase):
    """The founder's one act: his own terminal and op session; never from automation; the token only in the file."""
    def test_refuses_automation_and_writes_the_token_file_mode_600(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as h:
            target = os.path.join(h, "cap", "sa.tok")
            calls = []

            def run(args, **k):
                calls.append(args)
                if args[:3] == ["op", "vault", "get"]:
                    return subprocess.CompletedProcess(args, 1, "", "not found")
                if args[:3] == ["op", "service-account", "create"]:
                    return subprocess.CompletedProcess(args, 0, "ops_new_token\n", "")
                return subprocess.CompletedProcess(args, 0, "", "")
            with mock.patch.object(of_capacity, "SA_TOKEN_FILE", target):
                with mock.patch.dict(os.environ, {"OF_AUTOMATION": "1"}):
                    with self.assertRaises(of_capacity.MoveError):
                        of_capacity.vault_setup(run=run, interactive=True)
                env = {k: v for k, v in os.environ.items() if k not in ("OF_AUTOMATION", "HERDR_PANE_ID")}
                with mock.patch.dict(os.environ, env, clear=True):
                    with self.assertRaises(of_capacity.MoveError):
                        of_capacity.vault_setup(run=run, interactive=False)  # not a terminal
                    with mock.patch.object(of_capacity, "load_policy", return_value={}):
                        with self.assertRaises(of_capacity.MoveError):
                            of_capacity.vault_setup(run=run, interactive=True)  # no op_account set
                    with mock.patch.object(of_capacity, "load_policy", return_value={"op_account": "acme.1password.com"}):
                        self.assertEqual(of_capacity.vault_setup(run=run, interactive=True), target)
                    self.assertIn(["op", "vault", "get", "OF Capacity", "--account", "acme.1password.com"], calls)
                    with self.assertRaises(of_capacity.MoveError):
                        of_capacity.vault_setup(run=run, interactive=True)  # never overwrites
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(os.path.dirname(target)).st_mode & 0o777, 0o700)
            sa = next(c for c in calls if c[:3] == ["op", "service-account", "create"])
            self.assertIn("OF Capacity:read_items,write_items", sa)
            self.assertNotIn("share_items", " ".join(sa))


class CarriedSettings(unittest.TestCase):
    def test_carries_missing_owner_settings_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as h:
            src, dst = os.path.join(h, "a"), os.path.join(h, "b")
            os.makedirs(src); os.makedirs(dst)
            with open(os.path.join(src, "settings.json"), "w") as fh:
                json.dump({"hooks": {"Stop": []}, "skipDangerousModePermissionPrompt": True, "theme": "dark",
                           "modelSettings": {"m": 1}, "enabledPlugins": {"x": True}}, fh)
            with open(os.path.join(dst, "settings.json"), "w") as fh:
                json.dump({"theme": "light", "modelSettings": {"m": 2}}, fh)
            self.assertEqual(of_capacity.ensure_settings(src, dst, dry_run=True), ["hooks", "skipDangerousModePermissionPrompt"])
            with open(os.path.join(dst, "settings.json")) as fh:
                self.assertNotIn("hooks", json.load(fh))  # a dry run writes nothing
            self.assertEqual(of_capacity.ensure_settings(src, dst), ["hooks", "skipDangerousModePermissionPrompt"])
            with open(os.path.join(dst, "settings.json")) as fh:
                b = json.load(fh)
            self.assertEqual((b["theme"], b["modelSettings"], b["skipDangerousModePermissionPrompt"]), ("light", {"m": 2}, True))
            self.assertNotIn("enabledPlugins", b)  # plugins need an install, not a flag
            self.assertEqual(of_capacity.ensure_settings(src, dst), [])


class WeekRoom(unittest.TestCase):
    def test_a_small_account_takes_only_what_lasts_until_its_week_resets(self):
        rows = UncalibratedSink().rows()
        for r in rows:
            if r["config_dir"] == "/pro":
                r["windows"]["seven_day"]["resets_at"] = of_capacity._iso(Balance.NOW + 35 * 3600)
            if r["config_dir"] == "/ez":  # Pro's week resets first, so it is the sink
                r["windows"]["seven_day"]["resets_at"] = of_capacity._iso(Balance.NOW + 100 * 3600)
        pos = [{"name": n, "holder": {"session": n}} for n in ("mid", "small")]
        sess = {"/ez": [{"session": "mid", "status": "idle"}, {"session": "small", "status": "idle"}],
                "/cl": [], "/gg": [], "/pro": []}
        pol = dict(POLICY, min_rate=0.5)
        # Ultra: $8.6 per 1% of 5 hours and $26 per 1% of the week, so Pro gets about $1.3 per 1% of its week:
        # about $3.2/h for 35 hours; a $7/h session would hit the weekly limit, a $1/h one fits
        m = of_capacity.balance_plan(rows, sess, pos, {"mid": 7, "small": 1}, {"/ez": 8.6}, pol, Balance.NOW,
                                     contexts={"mid": 91_000, "small": 91_000}, wcalib={"/ez": 26.0})
        self.assertEqual([x["position"] for x in m], ["small"])


class UncalibratedSink(unittest.TestCase):
    """2026-11-07: a new Pro account (week resetting first, no calibration yet) looked infinite to fill, and the
    balancer was about to move two 500k-700k-context sessions at $8-10/h onto it."""
    def rows(self):
        r = Balance().rows()
        r.append({"config_dir": "/pro", "logged_in": True, "status": "allowed", "tier": "default_claude_ai", "windows": {
            "five_hour": {"utilization": 0, "resets_at": of_capacity._iso(Balance.NOW + 5 * 3600)},
            "seven_day": {"utilization": 0, "resets_at": of_capacity._iso(Balance.NOW + 3600)}}})
        for x in r:
            x.setdefault("tier", "default_claude_max_20x")
        return r

    def test_no_calibration_and_no_tier_means_no_fill(self):
        rows = [dict(x, tier=None) if x["config_dir"] == "/pro" else x for x in self.rows()]
        pos = [{"name": "big", "holder": {"session": "big"}}]
        sess = {"/ez": [{"session": "big", "status": "idle"}], "/cl": [], "/gg": [], "/pro": []}
        m = of_capacity.balance_plan(rows, sess, pos, {"big": 10}, {}, dict(POLICY), Balance.NOW)
        self.assertEqual(m, [])

    def test_a_pro_sink_is_sized_from_its_tier_and_takes_only_small_contexts(self):
        pos = [{"name": n, "holder": {"session": n}} for n in ("big", "small")]
        sess = {"/ez": [{"session": "big", "status": "idle"}, {"session": "small", "status": "idle"}],
                "/cl": [], "/gg": [], "/pro": []}
        pol = dict(POLICY, min_rate=0.5)
        calib = {"/ez": 8.6}  # Ultra: $8.6 per 1% of its 5-hour window, so Pro is about $0.43
        m = of_capacity.balance_plan(self.rows(), sess, pos, {"big": 6, "small": 1}, calib, pol, Balance.NOW,
                                     contexts={"big": 708_000, "small": 91_000})
        self.assertEqual([(x["position"], x["to"]) for x in m], [("small", "/pro")])


class Babysitting(unittest.TestCase):
    """RFC 0005 step 5: what a session spends on runner commands and monitor-woken turns, by model."""
    def test_runner_commands_are_kubectl_plus_the_organizations_own(self):
        bare = of_capacity.runner_cmd({})
        self.assertTrue(bare.search("kubectl -n shop get pods"))
        self.assertFalse(bare.search("bash $S/kx.sh runner-w16 tail -5 log"))
        self.assertTrue(of_capacity.runner_cmd({"runner_commands": [r"kx\.sh"]}).search("bash $S/kx.sh x"))

    def test_classifies_runner_and_monitor_turns_and_counts_each_response_once(self):
        def a(mid, model, tools, out=100, read=400_000):
            content = [{"type": "tool_use", "name": n, "input": {"command": c} if c else {}} for n, c in tools]
            return {"type": "assistant", "timestamp": "2026-11-03T10:00:00Z", "message": {
                "id": mid, "model": model, "content": content,
                "usage": {"input_tokens": 0, "output_tokens": out, "cache_read_input_tokens": read}}}
        rows = [
            {"type": "user", "message": {"content": "please look at the gate run"}},
            a("m1", "claude-fable-5-1", [("Bash", "bash $S/kx.sh runner-w16 tail -5 log")]),
            a("m1", "claude-fable-5-1", [("Bash", "bash $S/kx.sh runner-w16 tail -5 log")]),  # same response
            a("m2", "claude-fable-5-1", [("Edit", None)]),
            {"type": "user", "message": {"content": "<task-notification> <summary>Monitor event: run done</summary>"}},
            a("m3", "claude-fable-5-1", [("Monitor", None)]),
            a("m4", "claude-fable-5-1", []),  # its text answer, in the monitor-woken turn
        ]
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, "s.jsonl")
            with open(t, "w") as fh:
                fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
            r = of_capacity.babysit_measure([t], "2026-11-01", "2026-11-08",
                                            policy={"runner_commands": [r"kx\.sh"]})  # an organization's wrapper
        one = (100 * 50 + 400_000 * 0.25) / 1e6  # Fable: $50 per M output, $0.25 per M cache read
        self.assertEqual(r["responses"], 4)
        self.assertAlmostEqual(r["usd"], 4 * one, places=6)
        self.assertAlmostEqual(r["babysit_usd"], 3 * one, places=6)   # m1 (runner), m3 and m4 (monitor turn)
        self.assertAlmostEqual(r["monitor_turn_usd"], 2 * one, places=6)
        self.assertEqual((r["runner_commands"], r["monitor_calls"]), (1, 1))
        self.assertEqual(r["by_model"], {"claude-fable-5-1": round(4 * one, 6)})
        self.assertLess(r["small_context_estimate_usd"], r["babysit_usd"])  # a babysitter with its own small context


class LoopCost(unittest.TestCase):
    """RFC 0005 step 5: each finished of.loop_run gets an of.loop_cost, the doer's spend in the run's window."""
    def store(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE event (event_id TEXT, ts TEXT, name TEXT, session_id TEXT, position TEXT, payload TEXT)")
        conn.execute("CREATE TABLE transcript_own (session_id TEXT, message_id TEXT, type TEXT, timestamp TEXT, model TEXT, "
                     "input_tokens INT, output_tokens INT, cache_read_tokens INT, cache_write_tokens INT, "
                     "cache_write_5m_tokens INT, cache_write_1h_tokens INT)")
        for mid, ts in (("m1", "2026-11-03T10:10:00Z"), ("m1", "2026-11-03T10:10:00Z"), ("m2", "2026-11-03T11:00:00Z"),
                        ("m3", "2026-11-03T13:00:00Z")):
            conn.execute("INSERT INTO transcript_own VALUES ('s1', ?, 'assistant', ?, 'claude-fable-5-1', 0, 1000, 100000, 0, 0, 0)",
                         (mid, ts))
        run = {"work": "w", "run": "r1", "rung": "gate", "started_at": "2026-11-03T10:00:00Z", "ended_at": "2026-11-03T12:00:00Z"}
        conn.execute("INSERT INTO event VALUES ('e1', '2026-11-03T12:00:01Z', 'of.loop_run', 's1', 'doer', ?)", (json.dumps(run),))
        nostart = {"work": "w", "run": "r2", "rung": "gate", "ended_at": "2026-11-03T12:30:00Z"}
        conn.execute("INSERT INTO event VALUES ('e2', '2026-11-03T12:30:01Z', 'of.loop_run', 's1', 'doer', ?)", (json.dumps(nostart),))
        return conn

    def test_cost_of_a_run_window_once_per_run(self):
        from unittest import mock
        conn, seen = self.store(), []
        now = of_capacity._ts("2026-11-03T14:00:00Z")
        with mock.patch.object(of_capacity.of_events, "emit", lambda k, p, **kw: seen.append((k, p))):
            n = of_capacity.loop_costs(conn, now)
        one = (1000 * 50 + 100000 * 0.25) / 1e6
        costs = {p["run"]: p for k, p in seen if k == "loop_cost"}
        self.assertEqual(n, 2)
        self.assertAlmostEqual(costs["r1"]["usd"], 2 * one, places=6)  # m1 counted once, m2 in, m3 after the end
        self.assertEqual(costs["r1"]["responses"], 2)
        self.assertIsNone(costs["r2"]["usd"])  # no start: unknown, never zero
        for k, p in seen:  # once written, a run is not costed again
            conn.execute("INSERT INTO event VALUES ('x', '2026-11-03T14:00:00Z', 'of.loop_cost', '', 'of_capacity', ?)",
                         (json.dumps(p),))
        with mock.patch.object(of_capacity.of_events, "emit", lambda k, p, **kw: seen.append((k, p))):
            self.assertEqual(of_capacity.loop_costs(conn, now), 0)

    def test_only_runs_of_the_last_two_weeks_are_read(self):
        from unittest import mock
        conn, seen = self.store(), []
        later = of_capacity._ts("2026-11-03T14:00:00Z") + of_capacity.LOOP_COST_WINDOW_S + 86400
        with mock.patch.object(of_capacity.of_events, "emit", lambda k, p, **kw: seen.append(k)):
            self.assertEqual(of_capacity.loop_costs(conn, later), 0)  # past the window: not read at all

    def test_a_locate_of_a_run_costed_before_the_window_is_not_a_run(self):
        # PR 199 (of_review): `of loop locate` wrote a later of.loop_run with the same run id and no rung; read as a
        # run, a locate of a run costed before the window got a second, empty of.loop_cost.
        from unittest import mock
        conn, seen = self.store(), []
        later = of_capacity._ts("2026-11-03T14:00:00Z") + of_capacity.LOOP_COST_WINDOW_S + 86400
        locate = {"work": "w", "run": "r1", "observed_at": "proxy", "proxy": True}
        conn.execute("INSERT INTO event VALUES ('e3', ?, 'of.loop_run', 's1', 'doer', ?)",
                     (of_capacity._iso(later - 7200), json.dumps(locate)))
        with mock.patch.object(of_capacity.of_events, "emit", lambda k, p, **kw: seen.append(k)):
            self.assertEqual(of_capacity.loop_costs(conn, later), 0)
        self.assertEqual(seen, [])

    def test_a_recent_run_waits_for_its_records(self):
        from unittest import mock
        conn, seen = self.store(), []
        with mock.patch.object(of_capacity.of_events, "emit", lambda k, p, **kw: seen.append(k)):
            self.assertEqual(of_capacity.loop_costs(conn, of_capacity._ts("2026-11-03T12:10:00Z")), 0)


class ReliefWaitsAndChecksRoom(unittest.TestCase):
    """2026-11-07 17:00-17:30Z: e at 97% of a 5-hour window reopening at 17:30Z. Relief moved 14 sessions onto a new
    Pro account, which overflowed at once (104%) and held them until 19:30Z."""
    def rows(self, reopen_min):
        r = UncalibratedSink().rows()
        for x in r:
            if x["config_dir"] == "/ez":
                x["windows"]["five_hour"] = {"utilization": 97, "resets_at": of_capacity._iso(Balance.NOW + reopen_min * 60)}
                x["windows"]["seven_day"] = {"utilization": 58, "resets_at": of_capacity._iso(Balance.NOW + 140 * 3600)}
            if x["config_dir"] == "/cl":
                x["windows"]["seven_day"]["utilization"] = 100
        return r

    def plan(self, rows):
        pos = [{"name": n, "holder": {"session": n}} for n in ("a", "b", "c")]
        sess = {"/ez": [{"session": n, "status": "idle"} for n in ("a", "b", "c")], "/cl": [], "/gg": [], "/pro": []}
        return of_capacity.balance_plan(rows, sess, pos, {"a": 10, "b": 10, "c": 1}, {"/ez": 8.6},
                                        dict(POLICY, max_moves=5), Balance.NOW,
                                        contexts={"a": 450_000, "b": 90_000, "c": 90_000}, wcalib={"/ez": 26.0})

    def test_a_window_reopening_soon_is_waited_out(self):
        self.assertEqual(self.plan(self.rows(reopen_min=30)), [])

    def test_relief_moves_only_what_the_target_can_take(self):
        m = self.plan(self.rows(reopen_min=300))
        self.assertEqual([(x["position"], x["to"]) for x in m], [("c", "/pro")])  # $10/h ones do not fit a Pro


class WatchSkipsWindowsReopeningSoon(unittest.TestCase):
    def test_no_ask_for_a_five_hour_window_that_reopens_within_the_wait(self):
        now = Balance.NOW
        row = {"config_dir": "/ez", "logged_in": True, "positions": ["a"], "windows": {
            "five_hour": {"utilization": 97, "resets_at": of_capacity._iso(now + 20 * 60)},
            "seven_day": {"utilization": 50, "resets_at": of_capacity._iso(now + 100 * 3600)}}}
        self.assertEqual(of_capacity.proposals([row], set(), now=now, policy=POLICY), [])
        row["windows"]["five_hour"]["resets_at"] = of_capacity._iso(now + 4 * 3600)
        self.assertEqual(len(of_capacity.proposals([row], set(), now=now, policy=POLICY)), 1)


class StoreSecret(unittest.TestCase):
    def test_founder_only_value_to_vault_fingerprint_to_events(self):
        from unittest import mock
        stored, events = [], []
        env = {k: v for k, v in os.environ.items() if k not in ("OF_AUTOMATION", "HERDR_PANE_ID")}
        with mock.patch.object(of_capacity.of_events, "emit", lambda k, p, **kw: events.append((k, p))):
            with mock.patch.dict(os.environ, {"OF_AUTOMATION": "1"}):
                with self.assertRaises(of_capacity.MoveError):
                    of_capacity.store_secret("tax", "api_key", "a@x", read=lambda: "sk-ant-api03-" + "x" * 40,
                                             interactive=True, put=lambda *a: stored.append(a))
            with mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(of_capacity.MoveError):
                    of_capacity.store_secret("tax", "api_key", "a@x", read=lambda: "  ", interactive=True,
                                             put=lambda *a: stored.append(a))
                r = of_capacity.store_secret("tax", "api_key", "a@x", read=lambda: "sk-ant-api03-" + "y" * 40,
                                             interactive=True, put=lambda title, value, fields: stored.append(value) or "it7")
        self.assertEqual(stored, ["sk-ant-api03-" + "y" * 40])
        self.assertEqual((r["vault_item"], events[0][0]), ("it7", "credential_adopted"))
        self.assertNotIn("yyyy", json.dumps(events))


class ReliefAhead(unittest.TestCase):
    """2026-11-08: e at 95% of its week with 39 sessions spending about $100/h, a fresh d empty."""
    def test_a_week_running_out_within_hours_is_relieved_before_it_stalls(self):
        now = Balance.NOW
        iso = lambda h: of_capacity._iso(now + h * 3600)  # noqa: E731
        rows = [{"config_dir": "/ez", "logged_in": True, "status": "allowed", "tier": "default_claude_max_20x",
                 "windows": {"five_hour": {"utilization": 27, "resets_at": iso(1)}, "seven_day": {"utilization": 95, "resets_at": iso(130)}}},
                {"config_dir": "/ap", "logged_in": True, "status": "allowed", "tier": "default_claude_max_20x",
                 "windows": {"seven_day": {"utilization": 0, "resets_at": iso(166)}}}]
        pos = [{"name": n, "holder": {"session": n}} for n in ("a", "b", "c")]
        sess = {"/ez": [{"session": n, "status": "idle"} for n in ("a", "b", "c")], "/ap": []}
        rates = {"a": 40, "b": 30, "c": 30}
        pol = dict(POLICY, relief_moves=4)
        m = of_capacity.balance_plan(rows, sess, pos, rates, {"/ez": 8.6}, pol, now,
                                     contexts={"a": 200_000, "b": 200_000, "c": 200_000}, wcalib={"/ez": 26.0})
        self.assertEqual([(x["position"], x["to"]) for x in m], [("a", "/ap"), ("b", "/ap"), ("c", "/ap")])
        rows[0]["windows"]["seven_day"]["utilization"] = 60  # plenty of week left: nothing moves
        self.assertEqual(of_capacity.balance_plan(rows, sess, pos, rates, {"/ez": 8.6}, pol, now,
                                                  contexts={"a": 200_000}, wcalib={"/ez": 26.0}), [])


class AfterAWeekReset(unittest.TestCase):
    """2026-11-12: the founder reset ~/.claude-g's week to 0%. It reports no 5-hour window and had no week
    measure of its own yet, so it looked to have no room; ~/.claude-a, whose week resets 3 hours earlier, had none
    left; and fill moved nothing while ~/.claude spent $127/h toward its weekly limit."""
    def test_a_fresh_week_is_sized_from_the_others_and_fill_passes_to_it(self):
        now = Balance.NOW
        iso = lambda h: of_capacity._iso(now + h * 3600)  # noqa: E731
        acct = lambda d, used, reset_h: {"config_dir": d, "logged_in": True, "status": "allowed",  # noqa: E731
                                          "tier": "default_claude_max_20x",
                                          "windows": {"seven_day": {"utilization": used, "resets_at": iso(reset_h)}}}
        rows = [acct("/ap", 59, 67), acct("/gg", 0, 70), acct("/cl", 51, 160)]
        pos = [{"name": n, "holder": {"session": n}} for n in ("ap1", "c1", "c2", "c3")]
        sess = {"/ap": [{"session": "ap1", "status": "busy"}], "/gg": [],
                "/cl": [{"session": n, "status": "idle"} for n in ("c1", "c2", "c3")]}
        rates = {"ap1": 67, "c1": 18, "c2": 15, "c3": 2}
        wcalib = {"/ap": 31.2, "/cl": 20.5}  # $ per 1% of the week; /gg has none since its reset
        m = of_capacity.balance_plan(rows, sess, pos, rates, {}, dict(POLICY), now,
                                     contexts={"c1": 200_000, "c2": 200_000, "c3": 200_000}, wcalib=wcalib)
        self.assertEqual([(x["position"], x["from"], x["to"]) for x in m], [("c1", "/cl", "/gg"), ("c2", "/cl", "/gg")])

    def test_a_week_reading_without_its_reset_is_still_not_infinite(self):
        # of_review, PR 55: a Max 20x relief target at 90% of its week with no resets_at took $408/h from a rejected
        # Max 5x account, because the others' week prior made it week-only and the week cap needs the reset
        now = Balance.NOW
        iso = lambda h: of_capacity._iso(now + h * 3600)  # noqa: E731
        rows = [{"config_dir": "/mm", "logged_in": True, "status": "rejected", "tier": "default_claude_max_5x",
                 "windows": {"five_hour": {"utilization": 100, "resets_at": iso(0.33)},
                             "seven_day": {"utilization": 55, "resets_at": iso(124)}}},
                {"config_dir": "/t", "logged_in": True, "status": "allowed", "tier": "default_claude_max_20x",
                 "windows": {"seven_day": {"utilization": 90}}}]
        pos = [{"name": n, "holder": {"session": n}} for n in ("big", "small")]
        sess = {"/mm": [{"session": "big", "status": "idle"}, {"session": "small", "status": "idle"}], "/t": []}
        m = of_capacity.balance_plan(rows, sess, pos, {"big": 400, "small": 8}, {"/mm": 0.94}, dict(POLICY), now,
                                     wcalib={"/mm": 7.2})
        self.assertNotIn("big", [x["position"] for x in m])
        rows[1]["windows"] = {}  # no reading at all: no target, and no TypeError on its headroom
        self.assertEqual(of_capacity.balance_plan(rows, sess, pos, {"big": 400, "small": 8}, {"/mm": 0.94},
                                                  dict(POLICY), now, wcalib={"/mm": 7.2}), [])

    def test_a_five_hour_reading_with_no_five_hour_measure_is_sized_from_the_week(self):
        # 2026-11-20: an account just upgraded to Max 20x read 0% of its 5-hour window and 0% of its week; no account
        # was past 3% of a 5-hour window, so none had a 5-hour measure, and relief gave it nothing while ~/.claude
        # spent $270/h at 98% of its week
        now = Balance.NOW
        iso = lambda h: of_capacity._iso(now + h * 3600)  # noqa: E731
        rows = [{"config_dir": "/cl", "logged_in": True, "status": "allowed_warning", "tier": "default_claude_max_20x",
                 "windows": {"seven_day": {"utilization": 98, "resets_at": iso(106)}}},
                {"config_dir": "/m1", "logged_in": True, "status": "allowed", "tier": "default_claude_max_20x",
                 "windows": {"five_hour": {"utilization": 0, "resets_at": iso(4.5)},
                             "seven_day": {"utilization": 0, "resets_at": iso(13.5)}}}]
        pos = [{"name": n, "holder": {"session": n}} for n in ("c1", "c2", "c3")]
        sess = {"/cl": [{"session": n, "status": "idle"} for n in ("c1", "c2", "c3")], "/m1": []}
        rates = {"c1": 17, "c2": 15, "c3": 6}
        ctx = {n: 300_000 for n in ("c1", "c2", "c3")}
        m = of_capacity.balance_plan(rows, sess, pos, rates, {}, dict(POLICY, relief_moves=6), now, contexts=ctx,
                                     wcalib={"/cl": 34.9})
        self.assertEqual([(x["position"], x["to"]) for x in m], [("c1", "/m1"), ("c2", "/m1"), ("c3", "/m1")])
        # no week measure anywhere either: unknown is still no room
        self.assertEqual(of_capacity.balance_plan(rows, sess, pos, rates, {}, dict(POLICY, relief_moves=6), now,
                                                  contexts=ctx, wcalib={}), [])

    def test_fill_takes_one_sink_a_run(self):
        now = Balance.NOW
        iso = lambda h: of_capacity._iso(now + h * 3600)  # noqa: E731
        acct = lambda d, used, reset_h: {"config_dir": d, "logged_in": True, "status": "allowed",  # noqa: E731
                                          "tier": "default_claude_max_20x",
                                          "windows": {"seven_day": {"utilization": used, "resets_at": iso(reset_h)}}}
        # /s1 has $35/h of week left for its 50 hours ((94 - 6.5) * $20 / 50), /s2 $31/h
        rows = [acct("/s1", 6.5, 50), acct("/s2", 0, 60), acct("/cl", 40, 160)]
        pos = [{"name": n, "holder": {"session": n}} for n in ("c1", "c2", "c3")]
        sess = {"/s1": [], "/s2": [], "/cl": [{"session": n, "status": "idle"} for n in ("c1", "c2", "c3")]}
        m = of_capacity.balance_plan(rows, sess, pos, {"c1": 20, "c2": 15, "c3": 10}, {}, dict(POLICY, max_moves=3),
                                     now, wcalib={"/s1": 20.0, "/s2": 20.0, "/cl": 20.0})
        self.assertEqual([(x["position"], x["to"]) for x in m], [("c1", "/s1"), ("c2", "/s1")])  # c3 waits a run


class HerdrIntegration(unittest.TestCase):
    def test_installs_only_where_the_hook_is_missing(self):
        with tempfile.TemporaryDirectory() as h:
            with_hook, without = os.path.join(h, "a"), os.path.join(h, "b")
            os.makedirs(with_hook); os.makedirs(without)
            with open(os.path.join(with_hook, "settings.json"), "w") as fh:
                json.dump({"hooks": {"SessionStart": [{"hooks": [{"command": "bash '/x/hooks/herdr-agent-state.sh' session"}]}]}}, fh)
            with open(os.path.join(without, "settings.json"), "w") as fh:
                json.dump({"hooks": {"SessionStart": [{"hooks": [{"command": "python3 of hook SessionStart"}]}]}}, fh)
            calls = []

            def run(args, env=None, **k):
                calls.append((args, env.get("CLAUDE_CONFIG_DIR")))
                return subprocess.CompletedProcess(args, 0, "", "")
            self.assertFalse(of_capacity.ensure_herdr_integration(with_hook, run=run))
            self.assertTrue(of_capacity.ensure_herdr_integration(without, run=run))
            self.assertEqual(calls, [(["herdr", "integration", "install", "claude"], without)])


class ExitDialogComesBack(unittest.TestCase):
    def test_a_dialog_that_reappears_after_esc_is_closed_again_or_reported_stuck(self):
        from unittest import mock
        state = {"screen": exit_dialog(), "esc": 0}

        def key(k):
            if k == "Esc":
                state["esc"] += 1
                # the first Esc closes it, then it comes back once; the second Esc closes it for good
                state["screen"] = INPUT_BOX if state["esc"] != 1 else INPUT_BOX
        reads = {"after_esc": 0}

        def read():
            if state["esc"] == 1:
                reads["after_esc"] += 1
                if reads["after_esc"] == 2:
                    state["screen"] = exit_dialog()  # it reappears a moment after the first Esc closed it
            return list(state["screen"])
        with mock.patch.object(of_capacity.time, "sleep", lambda *_: None), \
                mock.patch.object(of_capacity, "_alive", lambda pid: True):
            with self.assertRaises(of_capacity.MoveError) as e:
                of_capacity.exit_session(7, lambda: None, read, key, allow_shells=False, wait=0.05, settle=0.01,
                                         step=0.001, log=lambda *_: None)
        self.assertGreaterEqual(state["esc"], 2)
        self.assertFalse(e.exception.stuck)  # closed for good in the end


class PositionMemory(unittest.TestCase):
    """RFC 0006: a position's memory lives in its own directory, seeded from every account's folder memory."""

    def mem(self, root, acct, folder, name, text):
        d = os.path.join(root, acct, "projects", folder, "memory")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w") as fh:
            fh.write(text)
        return d

    def doc(self, sid, body, modified="2026-11-08T10:00:00Z", name="x"):
        return (f"---\nname: {name}\ndescription: about {name}\nmetadata:\n  originSessionId: {sid}\n"
                f"  modified: {modified}\n---\n{body}")

    @staticmethod
    def settings_of(cmd):
        with open(shlex.split(cmd.split("--settings ", 1)[1])[0]) as fh:
            return json.load(fh)

    def test_launch_command_points_auto_memory_at_the_position(self):
        with tempfile.TemporaryDirectory() as t:
            cmd = of_capacity.launch_command("/w", "/h/.claude-b", "S", "net_keeper", "/n.md",
                                             memory_dir="/s/memory/acme/net_keeper", state_dir=t)
            self.assertEqual(self.settings_of(cmd), {"feedbackDrafts": "off", "env": of_spawn.agent_env(),
                                                     "autoMemoryDirectory": "/s/memory/acme/net_keeper"})
            self.assertEqual(of_capacity.flags_from_args(cmd)[3], "/s/memory/acme/net_keeper")

    def test_launch_command_carries_the_credential_deny_rules_in_a_file(self):
        # of_platform, 2026-11-11: of spawn writes the deny rules; a move or resume must keep them. In a
        # file: a fresh pane's shell takes 1,024 bytes a line, and 26 credential paths made 4.9 KB.
        deny = [f"Read(/k/credential-{i:02d}/**)" for i in range(60)]
        with tempfile.TemporaryDirectory() as t:
            cmd = of_capacity.launch_command("/w", "/h/.claude-b", "S", "net_keeper", "/n.md",
                                             memory_dir="/s/m", deny=deny, state_dir=t)
            self.assertLess(len(cmd.encode()), 1024)
            self.assertEqual({"feedbackDrafts": "off", "env": of_spawn.agent_env(), "autoMemoryDirectory": "/s/m",
                              "permissions": {"deny": deny}}, self.settings_of(cmd))
            path = shlex.split(cmd.split("--settings ", 1)[1])[0]
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            only = of_capacity.launch_command("/w", "/h/.claude-b", "S", "n", "/n.md", deny=["Read(/k/**)"],
                                              state_dir=t)
            self.assertEqual({"feedbackDrafts": "off", "env": of_spawn.agent_env(),
                              "permissions": {"deny": ["Read(/k/**)"]}}, self.settings_of(only))

    def test_every_launch_keeps_feedback_drafts_off_as_spawn_does(self):
        # PR 185 (of_review): a session spawned with drafts off had them on again after its first move, and a
        # queued draft is what held up a production keeper's /exit on 2026-11-14.
        with tempfile.TemporaryDirectory() as t:
            for kw in ({}, {"memory_dir": "/s/m"}, {"deny": ["Read(/k/**)"]},
                       {"tools": (["--mcp-config", "/t.json"], {"allowedMcpServers": [{"serverName": "x"}]})}):
                cmd = of_capacity.launch_command("/w", "/h/.claude-b", "S", "n", "/n.md", state_dir=t, **kw)
                self.assertEqual(self.settings_of(cmd).get("feedbackDrafts"), "off", kw)
                self.assertEqual(self.settings_of(cmd).get("env"), of_spawn.agent_env(), kw)

    def test_a_moved_session_keeps_the_agents_env_joined_with_its_tool_profiles(self):
        # of_platform, PR 216 (of_conductor's incident 01a1de68-8fec): every agent session starts with
        # GIT_CEILING_DIRECTORIES at the home folder, so a stray .git there is never climbed into; a move kept only
        # AGENT_SETTINGS and dropped it
        with tempfile.TemporaryDirectory() as t:
            cmd = of_capacity.launch_command("/w", "/h/.claude-b", "S", "n", "/n.md", state_dir=t,
                                             tools=([], {"env": {"X_TOOL": "1"}, "enabledPlugins": {"p@m": True}}))
            got = self.settings_of(cmd)
            self.assertEqual(got["env"], dict(of_spawn.agent_env(), X_TOOL="1"))
            self.assertEqual(got["env"]["GIT_CEILING_DIRECTORIES"], os.path.expanduser("~"))
            self.assertEqual(got["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "1000000")
            self.assertEqual(got["enabledPlugins"], {"p@m": True})

    def test_deny_for_follows_spawn_and_fails_open_to_the_guard(self):
        from unittest import mock
        import of_spawn
        logs = []
        with mock.patch.object(of_spawn, "deny_rules", return_value=["Read(/k/**)"]) as dr, \
                mock.patch("of_credentials.load", return_value={}):
            self.assertEqual(["Read(/k/**)"], of_capacity.deny_for({"name": "net_keeper", "_org": "acme"}, logs.append))
            dr.assert_called_once_with("net_keeper", "acme", {})
        with mock.patch.object(of_spawn, "deny_rules", return_value=[]):
            self.assertIsNone(of_capacity.deny_for({"name": "n", "_org": "acme"}, logs.append))
        with mock.patch("of_credentials.load", side_effect=ValueError("bad yaml")):
            self.assertIsNone(of_capacity.deny_for({"name": "n", "_org": "acme"}, logs.append))
        self.assertIn("the guard still applies", logs[-1])
        self.assertIsNone(of_capacity.deny_for(None, logs.append))

    def test_owners_by_store_aliases_and_registry(self):
        pos = [{"name": "wholesale_keeper", "_org": "acme", "aliases": ["wholesale_gitops"]},
               {"name": "net_keeper", "_org": "acme", "holder": {"session": "H"}}]
        rows = [("2026-11-08T01", "A", "acme", "wholesale_gitops"), ("2026-11-08T02", "B", None, "gone_position"),
                ("2026-11-08T03", "H", "acme", "wholesale_keeper")]
        own = of_capacity.session_owners(rows, pos)
        self.assertEqual(own["A"], ("acme", "wholesale_keeper"))   # a renamed position answers by its canonical name
        self.assertNotIn("B", own)                             # no registry knows it: its files stay in the folder
        self.assertEqual(own["H"], ("acme", "net_keeper"))      # the registry's holder.session wins

    def test_union_of_two_copies_keeps_every_line_once(self):
        a = self.doc("S", "one\ntwo\nthree\n", "2026-11-08T10:00:00Z").encode()
        b = self.doc("S", "one\ntwo, edited\nthree\nfour\n", "2026-11-09T10:00:00Z").encode()
        out = of_capacity.union2(a, b).decode()
        self.assertEqual(out.count("one\n"), 1)
        for ln in ("two\n", "two, edited\n", "three\n", "four\n"):
            self.assertIn(ln, out)
        self.assertIn("2026-11-09T10:00:00Z", out)            # the later frontmatter
        self.assertNotIn("2026-11-08T10:00:00Z", out)
        self.assertEqual(of_capacity.merge_copy(a, a, b), (b, "update"))
        self.assertEqual(of_capacity.merge_copy(b, a, a), (b, "keep"))
        self.assertEqual(of_capacity.merge_copy(None, None, a), (a, "copy"))

    def test_seed_merges_accounts_and_rebuilds_the_index_newest_first(self):
        with tempfile.TemporaryDirectory() as t:
            accts = [os.path.join(t, ".claude-a"), os.path.join(t, ".claude-b")]
            self.mem(t, ".claude-a", "-w", "old.md", self.doc("S1", "lesson old\n", "2026-11-01T00:00:00Z", "old"))
            self.mem(t, ".claude-a", "-w", "new.md", self.doc("S1", "lesson new\n", "2026-11-08T00:00:00Z", "new"))
            self.mem(t, ".claude-b", "-w", "new.md",
                     self.doc("S2", "lesson new\nand more\n", "2026-11-09T00:00:00Z", "new"))
            self.mem(t, ".claude-b", "-w", "other.md", self.doc("S9", "someone else's\n", name="other"))
            d = self.mem(t, ".claude-b", "-w", "loose.md", "no frontmatter at all\n")
            with open(os.path.join(d, "MEMORY.md"), "w") as fh:
                fh.write("- [Old lesson](old.md) — kept wording\n")
            owners = {"S1": ("acme", "p"), "S2": ("acme", "p")}
            by, loose = of_capacity.attribute_memory(accts, owners)
            self.assertEqual(sorted(by[("acme", "p")]), ["new.md", "old.md"])
            self.assertEqual(sorted(os.path.basename(x) for x in loose), ["loose.md", "other.md"])
            root, base = os.path.join(t, "memory"), os.path.join(t, "base")
            pdir = os.path.join(root, "acme", "p")
            acts = of_capacity.seed_position_memory(pdir, by[("acme", "p")], accts, base_root=base)
            self.assertEqual(dict(acts), {"new.md": "union", "old.md": "copy"})
            self.assertEqual(os.stat(pdir).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(os.path.join(root, "acme")).st_mode & 0o777, 0o700)
            with open(os.path.join(pdir, "new.md")) as fh:
                self.assertIn("and more", fh.read())
            with open(os.path.join(pdir, "MEMORY.md")) as fh:
                idx = fh.read().splitlines()
            self.assertEqual(idx, ["- [new](new.md) — about new", "- [Old lesson](old.md) — kept wording"])
            # a second run with nothing new changes nothing; an edit on one account comes through as an update
            self.assertEqual(of_capacity.seed_position_memory(pdir, by[("acme", "p")], accts, base_root=base), [])
            self.mem(t, ".claude-a", "-w", "old.md", self.doc("S1", "lesson old\nrevised\n", "2026-11-09T12:00:00Z", "old"))
            acts = of_capacity.seed_position_memory(pdir, by[("acme", "p")], accts, base_root=base)
            self.assertEqual(acts, [("old.md", "update")])
            with open(os.path.join(pdir, "MEMORY.md")) as fh:
                self.assertTrue(fh.readline().startswith("- [Old lesson](old.md)"))   # now the newest

    def test_seed_groups_a_shared_scope_into_one_directory(self):
        with tempfile.TemporaryDirectory() as t:
            accts = [os.path.join(t, ".claude-a")]
            self.mem(t, ".claude-a", "-w", "a.md", self.doc("S1", "from a\n", name="a"))
            self.mem(t, ".claude-a", "-w", "b.md", self.doc("S2", "from b\n", name="b"))
            units = {"bot": {"memory": "unit", "owner": "a"}}
            pos = [{"name": "a", "_org": "acme", "_unit": "bot", "_units": units, "holder": {"session": "S1"}},
                   {"name": "b", "_org": "acme", "_unit": "bot", "_units": units, "holder": {"session": "S2"}}]
            orig = of_capacity._session_rows
            of_capacity._session_rows = lambda: []
            try:
                r = of_capacity.seed_memory(pos, dirs=accts, root=os.path.join(t, "m"))
            finally:
                of_capacity._session_rows = orig
            shared = os.path.join(t, "m", "acme", "unit-bot")
            self.assertEqual(list(r["positions"]), [shared])
            self.assertEqual(sorted(r["members"][shared]), ["a", "b"])
            self.assertEqual(sorted(os.listdir(shared)), ["MEMORY.md", "a.md", "b.md"])

    def test_transcripts_attribute_files_with_no_origin(self):
        with tempfile.TemporaryDirectory() as t:
            acct = os.path.join(t, ".claude-a")
            m = self.mem(t, ".claude-a", "-w", "old.md", "---\nname: old\ntype: feedback\n---\nno origin here\n")
            self.mem(t, ".claude-a", "-w", "orphan.md", "---\nname: orphan\ntype: project\n---\nnobody\n")
            self.mem(t, ".claude-a", "-w", "rule.md", "---\nname: rule\ntype: feedback\n---\nnobody either\n")
            tr = os.path.join(acct, "projects", "-w", "S1.jsonl")
            cmd = f"cd /x && cat > {m}/old.md <<'EOF'\nbody\nEOF\ncp {m}/orphan.md /tmp/y"
            with open(tr, "w") as fh:
                for ts, sid_cmd in (("2026-11-01T00:00:00Z", cmd),):
                    fh.write(json.dumps({"type": "assistant", "timestamp": ts, "cwd": "/w", "message": {"content": [
                        {"type": "tool_use", "name": "Bash", "input": {"command": sid_cmd}}]}}) + "\n")
            writes = of_capacity.memory_writes([acct])
            self.assertEqual(list(writes), ["-w/memory/old.md"])     # a heredoc counts; a cp from it does not
            by, loose = of_capacity.attribute_memory([acct], {"S1": ("acme", "p")}, writes)
            self.assertEqual(list(by[("acme", "p")]), ["old.md"])
            left = of_capacity.leftover_memory(loose, writes, {}, [acct], org_paths=[("-w", "acme")])
            self.assertEqual([(r["file"], r["type"]) for r in left["acme"]], [("rule.md", "feedback"), ("orphan.md", "project")])
            path = of_capacity.write_leftover_report("acme", left["acme"], root=os.path.join(t, "m"))
            with open(path) as fh:
                text = fh.read()
            self.assertLess(text.index("## feedback"), text.index("## project"))


class DryRunPastTheLimitWait(unittest.TestCase):
    """2026-11-09: `move --dry-run` refused a session waiting out its limit; the real move presses Esc on it."""

    def test_the_limit_wait_notice_hides_no_input_box(self):
        screen = ["⏺ Usage limit reached · continuing automatically at 11:10pm · esc to cancel", "",
                  "✻ Worked for 0s · done 10:13 PM", "─" * 40, "❯ ", "─" * 40,
                  "  ⚠ Usage limit reached · limit resets 11:10pm",
                  "    Continuing automatically at 11:10pm · Esc to cancel · /usage-credits to continue now",
                  "  ⏵⏵ bypass permissions on (shift+tab to cycle)"]
        self.assertIsNone(of_capacity.input_draft(screen))
        self.assertEqual(of_capacity.input_draft(of_capacity.without_auto_continue(screen)), "")

    def test_after_the_wait_is_cancelled_the_box_is_found_and_a_failure_retries_soon(self):
        # 2026-11-11: a move pressed Esc on a session's wait, read once, saw no box and left it cancelled
        screen = ["⏺ Usage limit reached · continuing automatically at 6:40pm · esc to cancel",
                  "⏺ Automatic continue cancelled · /rate-limit-options to re-arm", "─" * 40, "❯ ", "─" * 40,
                  "    Continuing automatically at 6:40pm · Esc to cancel · /usage-credits to continue now",
                  "  ⏵⏵ bypass permissions on (shift+tab to cycle)"]
        self.assertEqual(of_capacity.input_draft(of_capacity.without_auto_continue(screen)), "")
        err = of_capacity.MoveError
        self.assertEqual(of_capacity.failed_hold_s(err("x", cancelled_wait=True)), 0)
        self.assertEqual(of_capacity.failed_hold_s(err("x")), of_capacity.FAILED_RETRY_S)
        self.assertEqual(of_capacity.failed_hold_s(err("x", untrusted="/w")), 6 * 3600)

    def test_a_paste_not_yet_sent_is_retried_on_the_next_run(self):
        # 2026-11-15: the move read the box between the relay's paste of a 64-line message and its Enter, and the
        # stopped session then sat out FAILED_RETRY_S on a spent account
        e = of_capacity.draft_error("p", "w1:p1", "[Pasted text #425 +59 lines]")
        self.assertIn("holds a draft", str(e))
        self.assertEqual(of_capacity.failed_hold_s(e), 0)
        self.assertEqual(of_capacity.failed_hold_s(of_capacity.draft_error("p", "w1:p1", "half a sentence")),
                         of_capacity.FAILED_RETRY_S)  # a person's own words: left alone as before
        self.assertEqual(of_capacity.failed_hold_s(of_capacity.draft_error("p", "w1:p1", "words", True)), 0)


class ReportRoute(unittest.TestCase):
    """2026-11-15, a user organization's structure keeper (message 01a1de9c-570e): a move note told a position of
    another organization to `of send of_capacity`, which resolves inside its own organization and has no grant to
    reach the capacity position. Framework matters come through the structure keeper (AGENTS.md)."""
    POS = [{"name": "of_capacity", "role": "capacity", "_org": "of-team"},
           {"name": "acme_org", "role": "org", "_org": "acme"},
           {"name": "acme_keeper", "role": "keeper", "_org": "acme"},
           {"name": "solo_keeper", "role": "keeper", "_org": "solo"}]
    POL = {"position": "of-team:of_capacity"}

    def route(self, name, positions=None, policy=None):
        p = next(x for x in self.POS if x["name"] == name)
        return of_capacity.report_route(p, self.POS if positions is None else positions,
                                        self.POL if policy is None else policy)

    def test_a_position_of_another_organization_tells_its_structure_keeper(self):
        self.assertEqual(self.route("acme_keeper"), "acme_org (of send acme_org), who relays it to of_capacity")
        # the keeper itself, or an organization with none: the capacity position by its organization
        self.assertEqual(self.route("acme_org"), "of_capacity (of send of-team:of_capacity)")
        self.assertEqual(self.route("solo_keeper"), "of_capacity (of send of-team:of_capacity)")

    def test_a_position_of_the_capacity_positions_organization_tells_it_directly(self):
        self.assertEqual(self.route("of_capacity"), "of_capacity (of send of_capacity)")
        self.assertEqual(self.route("acme_keeper", policy={}), "of_capacity (of send of_capacity)")  # none set


class HoldAfterMove(unittest.TestCase):
    """2026-11-11: a move by hand (to the account that can read a shared artifact) must not be undone by fill."""

    def test_a_move_holds_the_session_for_the_cooldown(self):
        from unittest import mock
        cache = {"_balanced": {"other": [1.0, 60.0]}}
        with mock.patch.object(of_capacity, "load_cache", lambda: cache), \
                mock.patch.object(of_capacity, "save_cache", lambda c: None), \
                mock.patch.object(of_capacity, "load_policy", lambda: {"cooldown_h": 2}):
            of_capacity.hold_after_move("s1", now=1000.0)
        self.assertEqual(cache["_balanced"], {"other": [1.0, 60.0], "s1": [1000.0, 7200.0]})


class Reboot(unittest.TestCase):
    """2026-11-10: the founder's OS update restarts the Mac; every session stops and /private/tmp is cleared."""

    def write_plan(self, plan):
        current = of_capacity._reboot_current()
        os.makedirs(current)
        with open(os.path.join(current, "plan.json"), "w") as fh:
            json.dump(plan, fh)

    def test_pause_expires_unless_the_machine_restarted(self):
        prepared = 1795121580  # 2026-11-19 at 20:53 UTC
        plan = {"prepared_at": of_capacity._iso(prepared), "herdr_resume_before": True}
        limit = of_capacity.REBOOT_PAUSE_MAX_H * 3600
        cases = ((limit - 1, prepared - 60, True), (limit, prepared - 60, False), (limit * 3, prepared - 60, False),
                 (limit * 3, None, False), (limit * 3, prepared, True), (limit * 3, prepared + 60, True))
        for age, boot, paused in cases:
            with self.subTest(age=age, boot=boot), tempfile.TemporaryDirectory() as d, \
                    mock.patch.object(of_capacity, "REBOOT_DIR", d), \
                    mock.patch.object(of_capacity, "_last_boot_epoch", return_value=boot), \
                    mock.patch.object(of_capacity, "set_herdr_resume") as resume, \
                    mock.patch.object(of_capacity.of_events, "emit") as emit:
                self.write_plan(plan)
                messages = []
                self.assertEqual(of_capacity._reboot_pauses_balance(prepared + age, messages.append), paused)
                if paused:
                    self.assertIn("paused", messages[-1])
                    self.assertEqual(of_capacity.load_reboot_plan(), plan)
                    emit.assert_not_called()
                    resume.assert_not_called()
                elif boot is None:
                    self.assertEqual(of_capacity.load_reboot_plan(), plan)
                    self.assertIn("boot time is unknown", messages[-1])
                    self.assertIn("for restore", messages[-1])
                    self.assertIn("of capacity reboot cancel", messages[-1])
                    emit.assert_not_called()
                    resume.assert_not_called()
                else:
                    self.assertIn("stale", messages[-1])
                    self.assertIn("balancing resumes", messages[-1])
                    self.assertFalse(os.path.exists(of_capacity._reboot_current()))
                    retired, = glob.glob(os.path.join(d, "cancelled-*"))
                    self.assertEqual(of_capacity.load_reboot_plan(os.path.join(retired, "plan.json")), plan)
                    self.assertFalse(of_capacity._reboot_pauses_balance(prepared + age + 1, messages.append))
                    emit.assert_called_once_with("capacity_reboot_cancelled", {"prepared_at": plan["prepared_at"],
                                                                               "sessions": 0}, position=mock.ANY)
                    resume.assert_called_once_with(True, log=messages.append)

    def test_pause_expires_when_herdr_config_is_unwritable(self):
        prepared = 1795121580
        plan = {"prepared_at": of_capacity._iso(prepared), "herdr_resume_before": True}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(of_capacity, "REBOOT_DIR", d), \
                mock.patch.object(of_capacity, "_last_boot_epoch", return_value=prepared - 60), \
                mock.patch.object(of_capacity, "set_herdr_resume", side_effect=PermissionError("read-only")) as resume, \
                mock.patch.object(of_capacity.of_events, "emit"):
            self.write_plan(plan)
            messages = []
            self.assertFalse(of_capacity._reboot_pauses_balance(prepared + of_capacity.REBOOT_PAUSE_MAX_H * 3600,
                                                               messages.append))
            resume.assert_called_once_with(True, log=messages.append)
            self.assertIn("[session] resume_agents_on_restore = true", messages[0])
            self.assertIn("~/.config/herdr/config.toml", messages[0])
            self.assertFalse(os.path.exists(of_capacity._reboot_current()))
            retired, = glob.glob(os.path.join(d, "cancelled-*"))
            self.assertEqual(of_capacity.load_reboot_plan(os.path.join(retired, "plan.json")), plan)

    def test_cancel_keeps_a_plan_with_a_different_prepared_at(self):
        plan = {"prepared_at": "2026-11-19T21:53:00Z", "herdr_resume_before": True}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(of_capacity, "REBOOT_DIR", d), \
                mock.patch.object(of_capacity, "set_herdr_resume") as resume, \
                mock.patch.object(of_capacity.of_events, "emit") as emit:
            self.write_plan(plan)
            messages = []
            of_capacity.reboot_cancel(expect_prepared_at="2026-11-19T20:53:00Z", log=messages.append)
            self.assertEqual(of_capacity.load_reboot_plan(), plan)
            self.assertIn(plan["prepared_at"], messages[-1])
            resume.assert_not_called()
            emit.assert_not_called()

    def test_last_boot_epoch(self):
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(subprocess, "check_output") as sysctl:
            sysctl.return_value = "{ sec = 1795121600, usec = 53903 } Thu Nov 19 20:53:20 2026"
            self.assertEqual(of_capacity._last_boot_epoch(), 1795121600)
            sysctl.side_effect = OSError()
            self.assertIsNone(of_capacity._last_boot_epoch())
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch("builtins.open", mock.mock_open(read_data="intr 123\nbtime 1795121600\n")):
            self.assertEqual(of_capacity._last_boot_epoch(), 1795121600)

    def test_cancel_retires_the_plan_with_its_tmp_keep(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(of_capacity, "REBOOT_DIR", d), \
                mock.patch.object(of_capacity, "set_herdr_resume") as resume, \
                mock.patch.object(of_capacity.of_events, "emit") as emit:
            current = of_capacity._reboot_current()
            tmp_root, keep = os.path.join(d, "live-tmp"), os.path.join(current, "tmp")
            os.makedirs(tmp_root)
            os.makedirs(keep)
            plan = {"prepared_at": "2026-11-19T20:53:00Z", "entries": [{"session": "s1"}], "tmp_root": tmp_root}
            with open(os.path.join(current, "plan.json"), "w") as fh:
                json.dump(plan, fh)
            with open(os.path.join(keep, "deleted-since-prepare"), "w") as fh:
                fh.write("kept")
            messages = []
            of_capacity.reboot_cancel(log=messages.append)
            retired, = glob.glob(os.path.join(d, "cancelled-*"))
            self.assertIn(retired, messages[0])
            self.assertFalse(os.path.exists(current))
            self.assertEqual(of_capacity.load_reboot_plan(os.path.join(retired, "plan.json")), plan)
            with open(os.path.join(retired, "tmp", "deleted-since-prepare")) as fh:
                self.assertEqual(fh.read(), "kept")
            self.assertEqual(os.listdir(tmp_root), [])
            resume.assert_not_called()
            emit.assert_called_once_with("capacity_reboot_cancelled", {"prepared_at": plan["prepared_at"], "sessions": 1},
                                         position=mock.ANY)

    def test_cancel_restores_the_recorded_herdr_setting(self):
        for before in (None, True, False):
            with self.subTest(before=before), tempfile.TemporaryDirectory() as d, \
                    mock.patch.object(of_capacity, "REBOOT_DIR", d), \
                    mock.patch.object(of_capacity, "set_herdr_resume") as resume, \
                    mock.patch.object(of_capacity.of_events, "emit") as emit:
                plan = {"herdr_resume_before": before}
                self.write_plan(plan)
                messages = []
                of_capacity.reboot_cancel(dry_run=True, log=messages.append)
                self.assertEqual(of_capacity.load_reboot_plan(), plan)
                self.assertEqual("herdr: would turn resume_agents_on_restore back on" in messages, before is not False)
                resume.assert_not_called()
                emit.assert_not_called()

                of_capacity.reboot_cancel(log=messages.append)
                if before is False:
                    resume.assert_not_called()
                else:
                    resume.assert_called_once_with(True, log=messages.append)
                    self.assertIn("herdr: resume_agents_on_restore back on", messages)

    def test_cancel_without_a_plan(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(of_capacity, "REBOOT_DIR", d), \
                mock.patch.object(of_capacity.of_events, "emit") as emit:
            messages = []
            of_capacity.reboot_cancel(log=messages.append)
            self.assertEqual(messages, ["no restart prepared"])
            self.assertEqual(os.listdir(d), [])
            emit.assert_not_called()

    def test_flags_from_args(self):
        args = ('claude --resume 1234 -n k8s_keeper --settings {"autoMemoryDirectory": "/Users/alex/.local/state/of/'
                'memory/acme/k8s_keeper"} --model claude-opus-5-5 --effort xhigh --dangerously-skip-permissions hi')
        self.assertEqual(of_capacity.flags_from_args(args),
                         ("claude-opus-5-5", "xhigh", True, "/Users/alex/.local/state/of/memory/acme/k8s_keeper"))
        self.assertEqual(of_capacity.flags_from_args("claude"), (None, None, False, None))
        with tempfile.TemporaryDirectory() as t:  # of spawn and moves pass a settings file
            f = os.path.join(t, "s1.settings.json")
            with open(f, "w") as fh:
                json.dump({"autoMemoryDirectory": "/m/k8s_keeper", "permissions": {"deny": ["Read(/k/**)"]}}, fh)
            self.assertEqual(of_capacity.flags_from_args(f"claude --resume s1 --settings {f} --model m hi")[3],
                             "/m/k8s_keeper")
            self.assertIsNone(of_capacity.flags_from_args(f"claude --settings {f}.gone hi")[3])

    def test_plan_puts_production_first_and_carries_place_and_flags(self):
        positions = [{"name": "k8s_keeper", "_org": "acme", "holder": {"session": "s1"}},
                     {"name": "of_studio", "_org": "ofb", "holder": {"session": "s2"}}]
        sessions = {"/h/.claude-a": [{"session": "s2", "status": "idle", "cwd": "/w", "pid": 2},
                                     {"session": "s3", "status": "idle", "cwd": "/w", "pid": 3, "name": "loose"}],
                    "/h/.claude-b": [{"session": "s1", "status": "shell", "cwd": "/k", "pid": 1}]}
        panes = [{"pane_id": "w1:p1", "workspace_id": "w1", "tab_id": "w1:t1",
                  "agent_session": {"value": "s1"}}]
        info = {"s1": {"args": "claude --model m1 --dangerously-skip-permissions",
                       "continuation": {"session_crons": [{"cron": "*/5"}],
                                        "background_tasks": [{"status": "running", "description": "watch"},
                                                             {"status": "done"}]},
                       "running": ["bash watch.sh"]},
                "s2": {"args": "claude", "model": "m2", "effort": "high", "bypass": True}}
        plan = of_capacity.reboot_plan(positions, sessions, panes, [{"workspace_id": "w1", "label": "ac·platform"}],
                                       [{"tab_id": "w1:t1", "label": "k8s"}], info, hold=["k8s_keeper"])
        self.assertEqual([e["name"] for e in plan], ["k8s_keeper", "of_studio", "loose"])
        k = plan[0]
        self.assertEqual((k["pane"], k["workspace"], k["tab"], k["model"], k["bypass"], k["hold"]),
                         ("w1:p1", "ac·platform", "k8s", "m1", True, True))
        self.assertEqual((len(k["crons"]), len(k["tasks"]), k["running"]), (1, 1, ["bash watch.sh"]))
        self.assertEqual((plan[1]["model"], plan[1]["effort"], plan[1]["bypass"], plan[1]["pane"]),
                         ("m2", "high", True, None))
        self.assertIsNone(plan[2]["position"])

    def test_tmp_is_kept_by_hard_links_and_put_back(self):
        with tempfile.TemporaryDirectory() as d:
            src, keep = os.path.join(d, "tmp"), os.path.join(d, "keep")
            os.makedirs(os.path.join(src, "proj", "sess", "work"))
            with open(os.path.join(src, "proj", "sess", "work", "a.output"), "w") as fh:
                fh.write("one\n")
            os.makedirs(os.path.join(src, "real"))
            os.symlink("real", os.path.join(src, "link"))
            os.symlink("proj/sess/work/a.output", os.path.join(src, "f.lnk"))
            os.makedirs(os.path.join(src, "proj", "sess", "tasks"))
            task_out = os.path.join(src, "proj", "sess", "tasks", "b.output")
            with open(task_out, "w") as fh:
                fh.write("task")
            k = of_capacity.keep_tree(src, keep)
            self.assertEqual((k["files"], k["failed"]), (4, 0))
            self.assertEqual(os.stat(task_out).st_nlink, 1)  # copied: Claude Code kills a task whose file gains a link
            out = os.path.join(src, "proj", "sess", "work", "a.output")
            with open(out, "a") as fh:  # written in place after prepare: the kept name sees it
                fh.write("two\n")
            os.makedirs(os.path.join(src, "survivor"))
            with open(os.path.join(src, "survivor", "x"), "w") as fh:
                fh.write("x")
            import shutil
            for x in ("proj", "real", "link", "f.lnk"):  # the restart clears the rest
                p = os.path.join(src, x)
                os.unlink(p) if os.path.islink(p) else shutil.rmtree(p)
            r = of_capacity.put_back_tree(keep, src)
            self.assertEqual(r["restored"], 4)
            with open(out) as fh:
                self.assertEqual(fh.read(), "one\ntwo\n")
            self.assertEqual(os.readlink(os.path.join(src, "link")), "real")
            self.assertTrue(os.path.islink(os.path.join(src, "f.lnk")))
            self.assertFalse(os.path.exists(keep))
            self.assertTrue(os.path.exists(os.path.join(src, "survivor", "x")))

    def test_put_back_leaves_a_file_that_survived(self):
        with tempfile.TemporaryDirectory() as d:
            src, keep = os.path.join(d, "tmp"), os.path.join(d, "keep")
            os.makedirs(src)
            with open(os.path.join(src, "a"), "w") as fh:
                fh.write("old")
            of_capacity.keep_tree(src, keep)
            os.unlink(os.path.join(src, "a"))
            with open(os.path.join(src, "a"), "w") as fh:
                fh.write("new")
            self.assertEqual(of_capacity.put_back_tree(keep, src), {"restored": 0, "there": 1})
            with open(os.path.join(src, "a")) as fh:
                self.assertEqual(fh.read(), "new")

    def test_note_lists_what_died_and_a_cut_turn(self):
        e = {"name": "obs_keeper", "session": "s1", "config_dir": os.path.expanduser("~/.claude-d"),
             "status": "busy", "crons": [{"cron": "0 * * * *", "prompt": "hourly sweep"}],
             "tasks": [{"type": "shell", "description": "gw1 watcher", "command": "python3 w.py"}],
             "running": ["python3 w.py"]}
        n = of_capacity.reboot_note(e, "a@b", "2026-11-10T14:00:00Z", "2026-11-10T13:50:00Z", True)
        for want in ("The Mac restarted", "hourly sweep", "gw1 watcher", "`python3 w.py`", "cut it off",
                     "put back", "of status", "not_holder"):
            self.assertIn(want, n)
        e.update(status="idle", crons=[], tasks=[], running=[])
        n = of_capacity.reboot_note(e, "a@b", "t", "p", False)
        self.assertIn("no crons, monitors or background tasks", n)
        self.assertNotIn("cut it off", n)
        self.assertNotIn("put back", n)

    def test_launch_command_takes_the_reboot_prompt(self):
        cmd = of_capacity.launch_command("/w", os.path.expanduser("~/.claude-d"), "s1", "k8s_keeper", "/n.md",
                                         resumed=True, prompt="The Mac restarted. Read /n.md")
        self.assertTrue(cmd.endswith("'The Mac restarted. Read /n.md'"))

    def test_free_pane_takes_a_restored_shell_in_its_tab_and_folder(self):
        ws = [{"workspace_id": "w1", "label": "ac·platform"}, {"workspace_id": "w2", "label": "desk"}]
        tabs = [{"tab_id": "w1:t1", "label": "k8s ❓2"}, {"tab_id": "w2:t1", "label": "k8s"}]
        panes = [{"pane_id": "w1:a", "workspace_id": "w1", "tab_id": "w1:t1", "cwd": "/k", "agent": "claude"},
                 {"pane_id": "w1:b", "workspace_id": "w1", "tab_id": "w1:t1", "cwd": "/other"},
                 {"pane_id": "w2:c", "workspace_id": "w2", "tab_id": "w2:t1", "cwd": "/k"},
                 {"pane_id": "w1:d", "workspace_id": "w1", "tab_id": "w1:t1", "cwd": "/k"},
                 {"pane_id": "w1:e", "workspace_id": "w1", "tab_id": "w1:t1", "cwd": "/k"}]
        f = of_capacity.free_pane
        self.assertEqual(f(panes, ws, tabs, "ac·platform", "k8s", "/k"), "w1:d")
        self.assertEqual(f(panes, ws, tabs, "ac·platform", "k8s", "/k", claimed={"w1:d"}), "w1:e")
        self.assertEqual(f(panes, ws, tabs, "ac·platform", "k8s", "/k", claimed={"w1:d"}, own="w1:e"), None)
        self.assertIsNone(f(panes, ws, tabs, "ac·platform", "obs", "/k"))

    def test_herdr_resume_setting(self):
        f = of_capacity.herdr_resume_setting
        t, prev = f("[ui]\nx = 1\n", False)
        self.assertIsNone(prev)
        self.assertIn("[session]\nresume_agents_on_restore = false\n", t)
        self.assertTrue(t.startswith("[ui]\nx = 1\n"))
        t2, prev2 = f(t, True)
        self.assertFalse(prev2)
        self.assertIn("resume_agents_on_restore = true", t2)
        self.assertEqual(t2.count("[session]"), 1)
        t3, prev3 = f("[session]\nstartup_per_agent_delay_ms = 100\n[remote]\n", False)
        self.assertIsNone(prev3)
        self.assertTrue(t3.startswith("[session]\nresume_agents_on_restore = false\nstartup_per_agent_delay_ms"))
        self.assertEqual(f("[session]\n  resume_agents_on_restore = true\n", False),
                         ("[session]\nresume_agents_on_restore = false\n", True))

    def test_stray_holder_is_herdrs_own_resume(self):
        e = {"config_dir": "/h/.claude-d", "bypass": True}
        prepared = 1_000_000
        f = of_capacity.stray_holder
        self.assertIsNone(f(e, "/h/.claude-d", "claude --resume s --dangerously-skip-permissions", 2e9, prepared))
        self.assertIn("instead of", f(e, "/h/.claude", "claude --resume s", 1_000_500_000, prepared))
        self.assertIn("without bypass", f(e, "/h/.claude-d", "claude --resume s", 1_000_500_000, prepared))
        self.assertTrue(f(e, "/h/.claude", "claude --resume s", 999_000_000, prepared).startswith("before"))
        self.assertIsNone(f(dict(e, bypass=False), "/h/.claude-d", "claude --resume s", 2e9, prepared))
