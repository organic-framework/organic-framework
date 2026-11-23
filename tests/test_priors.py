"""The priors tally (bin/of_priors.py, docs/diagnose.md §5.2): latest label, the current version's issues only,
distinct judgements, p; and a table that carries no issue id, note or case. Pure logic: no store, no replay."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import of_priors  # noqa: E402

CAT = {"rules": {"chatty": {"version": 4, "labels_carry_from": [3], "p": 0.6},
                 "off-map-work": {"version": 1, "p": {"default": 0.5}},
                 "lingering": {"version": 0, "p": 0.5}}}
TS = "2026-11-16T10:20:00Z"


def lab(rule, v, label, note=None, ts=TS):
    return {"rule": f"ap.{rule}", "rule_version": f"ap.{rule}@{v}", "label": label, "note": note, "ts": ts}


class TallyTest(unittest.TestCase):
    def run_tally(self, labels, raised=None, blocked=()):
        seen = []

        def r(b):
            seen.append(b)
            return {"ids": set(labels) if raised is None else raised, "blocked": blocked}
        out, meta = of_priors.tally(labels, CAT, r)
        return out, meta, seen

    def test_distinct_judgements_and_p(self):
        # 12 issues on one cause, one note: one judgement; then four more notes and one false positive
        labels = {f"c{i}": lab("chatty", 4, "true_positive", "a booking per pull request") for i in range(12)}
        labels.update({f"d{i}": lab("chatty", 4, "true_but_not_now", f"cause {i}") for i in range(4)})
        labels["e"] = lab("chatty", 4, "false_positive", "each nudged once")
        out, meta, _ = self.run_tally(labels)
        r = out["ap.chatty@4"]
        self.assertEqual((r["labels"], r["counted"], r["distinct"], r["false_positive"]), (17, 17, 6, 1))
        self.assertEqual(r["p"], 0.83)
        self.assertEqual(r["p_now"], 0.6)
        self.assertEqual(meta, {"labels": 17, "gone": 0})

    def test_floor_and_cap(self):
        few = {f"l{i}": lab("lingering", 0, "true_positive", f"n{i}") for i in range(4)}
        self.assertIsNone(self.run_tally(few)[0]["ap.lingering@0"]["p"])            # 4 judgements: too few
        five = {f"l{i}": lab("lingering", 0, "true_positive", f"n{i}") for i in range(5)}
        self.assertEqual(self.run_tally(five)[0]["ap.lingering@0"]["p"], 0.95)      # 1.0 held at 0.95

    def test_current_version_only(self):
        labels = {"old": lab("chatty", 2, "false_positive", "x"),      # @2 is not carried by @4
                  "carried": lab("chatty", 3, "true_positive", "y"),   # @3 is: replayed
                  "bare": dict(lab("chatty", 4, "true_positive", "z"), rule_version=None),   # replayed
                  "gone": lab("broker", 0, "true_positive", "w"),
                  "quiet": lab("chatty", 3, "false_positive", "fixed by @4"),               # @4 no longer raises it
                  "now": lab("chatty", 4, "true_positive", "v")}       # given on @4: @4 raised it, no replay
        out, meta, _ = self.run_tally(labels, raised={"carried", "bare", "old"})
        r = out["ap.chatty@4"]
        self.assertEqual((r["labels"], r["older_version"], r["not_raised"], r["counted"], r["false_positive"]),
                         (5, 1, 1, 3, 0))
        self.assertEqual(meta["gone"], 1)
        # a rule blocked in the replay judges nothing: its labels count apart, never as no longer raised
        out, _, _ = self.run_tally(labels, raised=set(), blocked={"ap.chatty"})
        r = out["ap.chatty@4"]
        self.assertEqual((r["older_version"], r["replay_blocked"], r["not_raised"], r["counted"]), (1, 3, 0, 1))

    def test_sittings_replay_only_carried_labels(self):
        h = "2026-11-16T"
        labels = {"a": lab("chatty", 3, "true_positive", ts=h + "09:10:00Z"),
                  "b": lab("lingering", 0, "true_positive", ts=h + "09:50:00Z"),   # on lingering@0, the current one
                  "c": lab("chatty", 3, "true_positive", ts=h + "10:05:00Z"),     # 55 min after a: the same sitting
                  "d": dict(lab("off-map-work", 1, "true_positive", ts=h + "12:00:00Z"), rule_version=None),
                  "e": lab("chatty", 2, "true_positive", ts=h + "13:00:00Z")}      # @2 is not carried: none
        plan, end_of = of_priors.sittings(labels, CAT)
        e = of_priors.of_model.epoch
        self.assertEqual(plan, [(e(h + "11:00:00Z"), {"chatty"}), (e(h + "12:00:00Z"), {"off-map-work"})])
        self.assertEqual(end_of, {"a": e(h + "11:00:00Z"), "c": e(h + "11:00:00Z"), "d": e(h + "12:00:00Z")})

    def test_replay_per_hour_after_the_label(self):
        labels = {"a": lab("chatty", 3, "true_positive", ts="2026-11-16T10:20:00Z"),
                  "b": lab("chatty", 3, "true_positive", ts="2026-11-16T10:59:59Z"),
                  "c": lab("lingering", 0, "true_positive", ts="2026-11-16T12:30:00Z")}   # current version: none
        _, _, seen = self.run_tally(labels)
        self.assertEqual(set(seen), {of_priors.of_model.epoch("2026-11-16T11:00:00Z")})

    def test_merged_issue_counts_as_raised(self):
        d = {"issues": [{"issue_id": "host", "affected": ["p1"],
                         "evidence": {"merged": {"ap.inbound-backlog@3": {}}}}]}
        d["rules"] = [{"rule": "ap.bottleneck-broker", "status": "fired"}, {"rule": "ap.chatty", "status": "blocked"},
                      {"rule": "ap.lingering", "status": "quiet"}, {"rule": "ap.loop-wip", "status": "failed"}]
        self.assertEqual(of_priors.replayed(d), {"ids": {"host", of_priors.of_model.issue_id("inbound-backlog", ["p1"])},
                                                 "blocked": {"ap.chatty", "ap.loop-wip"}})

    def test_table_carries_no_id_note_or_case(self):
        labels = {"9f3c2a1b7e44": lab("off-map-work", 1, "false_positive", "obs_keeper covers the alert rules"),
                  "1a2b3c4d5e6f": lab("off-map-work", 1, "true_positive", "a card id spanned the popup")}
        out, meta, _ = self.run_tally(labels)
        meta.update(org="acme", replays=1, at="2026-11-23T00:00:00Z")
        text = of_priors.table(out, meta)
        for secret in list(labels) + ["obs_keeper", "alert", "card id", "popup"]:
            self.assertNotIn(secret, text)
        self.assertIn("ap.off-map-work@1", text)
        self.assertIn('{"default":0.5}', text)
        self.assertIn("too few", text)

    def test_peak_memory_in_mib_on_linux_and_macos(self):
        class RU:
            ru_maxrss = 2000355328   # the first user organization's run, in bytes on macOS
        self.assertEqual(of_priors.peak_mib(RU, "darwin"), 1907)
        RU.ru_maxrss = 64512        # kibibytes on Linux
        self.assertEqual(of_priors.peak_mib(RU, "linux"), 63)


class MainTest(unittest.TestCase):
    """main over a scratch store and two organizations: only --org's labels count, a carried label is replayed against
    its registry as it stood, and the output names no issue, note or position (of_release on PR 581)."""

    def test_two_organizations_and_a_replay(self):
        import contextlib
        import io
        import json
        import subprocess
        import tempfile
        import of_store
        git = ["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@t"]
        with tempfile.TemporaryDirectory() as t:
            orgs = []
            for name, systems in (("acme", {"app": {"keeper": "acme_lead"}, "db": None, "queue": {"keeper": "acme_lead"}}),
                                  ("beta", {"web": {"keeper": "beta_lead"}, "cache": None})):
                repo = os.path.join(t, name)
                os.makedirs(os.path.join(repo, "registry"))
                with open(os.path.join(repo, "registry", "positions.yaml"), "w") as fh:
                    json.dump({"organization": name, "systems": systems,
                               "positions": [{"name": f"{name}_lead", "system": next(iter(systems)), "status": "active"}]}, fh)
                env = dict(os.environ, GIT_AUTHOR_DATE="2026-11-01T00:00:00Z", GIT_COMMITTER_DATE="2026-11-01T00:00:00Z")
                subprocess.run(git + ["init", "-q", repo], check=True)
                subprocess.run(git + ["-C", repo, "add", "-A"], check=True)
                subprocess.run(git + ["-C", repo, "commit", "-q", "-m", "registry"], check=True, env=env)
                orgs.append(f"  - name: {name}\n    path: {repo}\n")
            with open(os.path.join(t, "orgs.yaml"), "w") as fh:
                fh.write("orgs:\n" + "".join(orgs))
            conn = of_store.connect(os.path.join(t, "of.db"))
            iid = of_priors.of_model.issue_id
            now = of_priors.current(of_priors.of_diagnose.catalogue(), "ap.unkept-system")[0]   # @0 is carried by it
            for i, (org, rule, v, label, note, ts, issue) in enumerate((
                    ("acme", "unkept-system", 0, "true_positive", "db has no keeper", "2026-11-10T09:00:00Z",
                     iid("unkept-system", ["db"])),                               # @0, carried by @1: replayed, raised
                    ("acme", "unkept-system", 0, "false_positive", "kept by the vendor", "2026-11-10T09:20:00Z",
                     iid("unkept-system", ["app"])),                              # replayed: app has a keeper
                    ("acme", "unkept-system", now, "true_positive", "a note", "2026-11-12T09:00:00Z", "a1b2c3d4e5f6"),
                    ("beta", "unkept-system", 0, "false_positive", "beta's own", "2026-11-10T09:00:00Z",
                     iid("unkept-system", ["cache"])))):
                of_store.add_event(conn, {"of.event.id": f"e{i}", "timestamp": ts, "event.name": "of.issue_labelled",
                                          "of.org": org, "of.payload": {"issue_id": issue, "rule": f"ap.{rule}",
                                                                        "rule_version": f"ap.{rule}@{v}",
                                                                        "label": label, "note": note, "by": "owner"}})
            conn.close()
            saved = {k: os.environ.get(k) for k in ("OF_ORGS", "OF_DB")}
            os.environ.update(OF_ORGS=os.path.join(t, "orgs.yaml"), OF_DB=os.path.join(t, "of.db"))
            out, err = io.StringIO(), io.StringIO()
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = of_priors.main(["--org", "acme", "--json"])
                with contextlib.redirect_stdout(io.StringIO()) as text, contextlib.redirect_stderr(io.StringIO()):
                    of_priors.main(["--org", "acme"])
            finally:
                for k, v in saved.items():
                    os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
        self.assertEqual(rc, 0, err.getvalue())
        d = json.loads(out.getvalue())
        self.assertEqual((d["labels"], d["replays"]), (3, 1))                 # beta's label is not acme's
        r = d["rules"][f"ap.unkept-system@{now}"]
        self.assertEqual((r["labels"], r["older_version"], r["replay_blocked"], r["not_raised"], r["counted"],
                          r["distinct"], r["false_positive"], r["p"]), (3, 0, 0, 1, 2, 2, 0, None))
        self.assertIn("replay 1/1", err.getvalue())
        for secret in ("db has no keeper", "vendor", "a1b2c3d4e5f6", iid("unkept-system", ["db"]), "acme_lead", '"db"'):
            self.assertNotIn(secret, out.getvalue() + text.getvalue())


if __name__ == "__main__":
    unittest.main()
