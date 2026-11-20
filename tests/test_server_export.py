"""`of server export` and `import`, the server store's part: one organization's rows moved between of-servers and
checked the same way on both sides (bin/of_server_export.py; 2026-11-19, of_server)."""

from contextlib import redirect_stderr, redirect_stdout
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_server  # noqa: E402
import of_server_export as ex  # noqa: E402



def insert_row(conn, table, **values):
    """A row with VALUES and a plain value in each required column the test does not care about."""
    row = {r[1]: (0 if "INT" in (r[2] or "").upper() or "REAL" in (r[2] or "").upper() else "")
           for r in conn.execute(f"PRAGMA table_info({table})") if r[3] and r[4] is None and not r[5]}
    row.update(values)
    conn.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))


def export_store(*a, **k):
    """The tests not about encryption say --plaintext, as an operator must (sec_keeper, msg 01a1f638-5862)."""
    k.setdefault("plaintext", True)
    return ex.export_store(*a, **k)


def import_store(*a, **k):
    k.setdefault("plaintext", True)
    return ex.import_store(*a, **k)


def check_import(*a, **k):
    k.setdefault("plaintext", True)
    return ex.check_import(*a, **k)

def store(tables=True):
    """A server store; with TABLES, also the tables its modules make when they first serve."""
    d = tempfile.mkdtemp(prefix="ofx-")
    of_server.Store(d)
    c = sqlite3.connect(os.path.join(d, "server.db"), isolation_level=None)
    if tables:
        ex.ensure_schema(c)
    return c, d


def fill(c):
    rows = [
        ("messages", {"seq": 1, "msg_id": "01a1-0001", "to_org": "acme", "to_position": "acme_ops", "from_org": "acme",
                      "from_position": "acme_org", "text": "hello", "created_at": "2026-11-18T10:00:00Z", "state": "done"}),
        ("messages", {"seq": 2, "msg_id": "01a1-0002", "to_org": "other", "to_position": "other_org", "from_org": "acme",
                      "from_position": "acme_org", "text": "across", "created_at": "2026-11-19T10:00:00Z",
                      "state": "queued", "grant_id": "g1"}),
        ("messages", {"seq": 3, "msg_id": "01a1-0003", "to_org": "other", "to_position": "other_org",
                      "from_org": "other", "from_position": "other_ops", "text": "theirs",
                      "created_at": "2026-11-19T11:00:00Z", "state": "queued"}),
        ("coord", {"seq": 1, "org": "acme", "event_id": "01a1-e1", "name": "of.ask", "ts": "2026-11-19T09:00:00Z",
                   "record": "{}", "added": 1795130346.5}),
        ("coord", {"seq": 2, "org": "other", "event_id": "01a1-e2", "name": "of.ask", "ts": "2026-11-19T09:00:00Z",
                   "record": "{}", "added": 1795130347.5}),
        ("position_aliases", {"org": "acme", "alias": "ops", "position": "acme_ops", "updated": "2026-11-19T09:00:00Z"}),
        ("secret_sightings", {"batch_id": "b1", "kind": "k", "hmac": "h", "org": "acme", "tbl": "t", "count": 2,
                              "detected_at": "2026-11-19T09:00:00Z"}),
    ]
    for table, r in rows:
        c.execute(f"INSERT INTO {table} ({', '.join(r)}) VALUES ({', '.join('?' * len(r))})", list(r.values()))


def age_key(d):
    """An age identity made for the test, and its recipient."""
    import subprocess
    ident = os.path.join(d, "key.txt")
    subprocess.run(["age-keygen", "-o", ident], check=True, capture_output=True)
    rec = subprocess.run(["age-keygen", "-y", ident], check=True, capture_output=True, text=True).stdout.strip()
    return ident, rec


def rebuild(bundle, table, rows, identity=None):
    """Rewrite one plain table file of BUNDLE with ROWS and make its manifest and index agree, as a bundle changed by
    someone who recomputed every hash would."""
    m = ex.load_manifest(bundle, identity)
    ext = ".age" if m["encrypted"] else ""
    rel = f"store/{table}.jsonl.gz{ext}"
    path = os.path.join(bundle, rel)
    os.unlink(path)
    m["files"][rel] = ex._write_file(path, (json.dumps(r).encode() + b"\n" for r in rows))
    m["store"][table] = ex.summarize(table, rows)
    mp = os.path.join(bundle, "manifest.json.gz" + ext)
    os.unlink(mp)
    w = ex._write_file(mp, [json.dumps(m, sort_keys=True).encode()])
    ix = ex.load_index(bundle)
    ix["files"][rel], ix["files"]["manifest.json.gz" + ext] = m["files"][rel]["sha256"], w["sha256"]
    ip = os.path.join(bundle, "index.json")
    os.unlink(ip)
    with open(ip, "w") as fh:
        json.dump(ix, fh)
    return ex._file_sha(ip)


class ExportTest(unittest.TestCase):

    def setUp(self):
        self.src, _ = store()
        fill(self.src)
        self.out = os.path.join(tempfile.mkdtemp(prefix="ofb-"), "bundle")

    def export(self, org="acme", out=None, **kw):
        return export_store(self.src, org, out or self.out, **kw)

    def test_an_export_holds_the_organizations_rows_alone_and_a_cross_message_in_both(self):
        m, sha = self.export(version="t")
        self.assertEqual(len(sha), 64)
        self.assertEqual(m["store"]["messages"]["rows"], 2)                 # its own, and the one it sent across
        self.assertEqual(m["store"]["coord"]["days"], {"2026-11-19": [1, "01a1-e1", "01a1-e1"]})
        other, _ = self.export("other", self.out + "-other")
        self.assertEqual(other["store"]["messages"]["rows"], 2)           # its own, and the one it received
        self.assertEqual(os.stat(self.out).st_mode & 0o777, 0o700)
        for name in os.listdir(self.out):
            if name != "store":
                self.assertEqual(os.stat(os.path.join(self.out, name)).st_mode & 0o777, 0o600)
        self.assertNotIn("hello", open(os.path.join(self.out, "index.json")).read())
        self.assertEqual(ex.check_bundle(self.out), [])
        self.assertEqual(ex.check_bundle(self.out, index_sha=sha), [])
        self.assertTrue(ex.check_bundle(self.out, index_sha="0" * 64))

    def test_an_import_reproduces_the_rows_and_the_check_matches_on_both_sides(self):
        _, sha = self.export()
        dst, _ = store()
        self.assertEqual(import_store(dst, self.out, "acme", sha)["messages"], 2)
        self.assertEqual(ex.check_store(dst, self.out), [])
        self.assertEqual(ex.check_store(self.src, self.out), [])
        got = dst.execute("SELECT seq, state FROM messages ORDER BY seq").fetchall()
        self.assertEqual(got, [(1, "done"), (2, "queued")])               # sequence numbers kept: feed cursors hold
        self.assertEqual(import_store(dst, self.out, "acme", sha)["messages"], 2)   # again: replaced, not doubled
        self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)

    def test_the_operator_names_the_organization_and_carries_the_index_hash(self):
        """of_secrets-review on organic-framework PR 425: an honest bundle of A labelled B replaced B, and age
        authenticates no sender."""
        _, sha = self.export()
        dst, _ = store()
        for org, given, words in (("other", sha, "not other's"), ("acme", None, "--index-sha"),
                                  ("acme", "0" * 64, "not the one the export printed")):
            with self.subTest(org=org, given=given), self.assertRaises(ex.ExportError) as e:
                import_store(dst, self.out, org, given)
            self.assertIn(words, str(e.exception))
        self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_a_row_of_another_organization_or_an_unknown_column_refuses_the_import_before_any_change(self):
        """PR 425: a changed bundle with every hash recomputed wrote another organization's rows, and a column the
        destination lacks was dropped silently."""
        dst, _ = store()
        dst.execute("INSERT INTO position_aliases (org, alias, position) VALUES ('acme', 'old', 'acme_old')")
        for rows, words in (([{"org": "other", "alias": "x", "position": "taken"}], "not acme's"),
                            ([{"org": "acme", "alias": "y", "position": "p", "planted": 1}], "no column planted")):
            out = self.out + str(len(words))
            self.export(out=out)
            sha = rebuild(out, "position_aliases", rows)
            self.assertEqual(ex.check_bundle(out, index_sha=sha), [])  # the hashes all agree: only the rows tell
            with self.subTest(words=words), self.assertRaises(ex.ExportError) as e:
                import_store(dst, out, "acme", sha)
            self.assertIn(words, str(e.exception))
        self.assertEqual(dst.execute("SELECT org, alias, position FROM position_aliases").fetchall(),
                         [("acme", "old", "acme_old")])                                  # nothing changed

    def test_a_server_holding_another_organizations_records_takes_no_import(self):
        """sec_keeper, msg 01a1f638-5862: a message between two organizations is in both bundles, so on a server that
        serves the other one too the import would replace that organization's copy."""
        _, sha = self.export()
        dst, _ = store()
        dst.execute("INSERT INTO messages (seq, msg_id, to_org, from_org) VALUES (7, 'z', 'other', 'other')")
        with self.assertRaises(ex.ExportError) as e:
            import_store(dst, self.out, "acme", sha)
        self.assertIn("another organization's records", str(e.exception))
        self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)

    def test_an_export_waits_for_the_organizations_queued_batches(self):
        info = list(self.src.execute("PRAGMA table_info(batches)"))
        cols = [r[1] for r in info]
        row = {r[1]: (0 if "INT" in (r[2] or "").upper() else "") for r in info if r[3] and r[4] is None and not r[5]}
        row.update({c: v for c, v in (("batch_id", "b1"), ("machine_id", "m"), ("state", "queued"),
                                      ("by_org", json.dumps({"acme": 3}))) if c in cols})
        self.src.execute(f"INSERT INTO batches ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                         list(row.values()))
        with self.assertRaises(ex.ExportError) as e:
            self.export()
        self.assertIn("still queued", str(e.exception))
        self.export(org="other", out=self.out + "-other")     # another organization's export is not held

    def test_every_exported_table_shows_another_organizations_records(self):
        _, sha = self.export()
        dst, _ = store()
        insert_row(dst, "finance_facts", event_id="f", org="other")
        with self.assertRaisesRegex(ex.ExportError, "another organization's records"):
            import_store(dst, self.out, "acme", sha)

    def test_round_three_hold_dead_batches_every_redaction_and_a_recheck_before_the_index(self):
        """The user organization's sec_keeper, round 3 (through of_architect, msg 01a1f657-aeaa), and of_review on
        PR 433: a dead batch passed, a delete-only redaction (by_org {}) passed, a cancelled one blocked forever, and
        the hold was read only at the start."""
        insert_row(self.src, "batches", batch_id="dead", machine_id="m", state="dead", by_org=json.dumps({"acme": 1}))
        with self.assertRaisesRegex(ex.ExportError, "queued or dead"):
            self.export(out=self.out + "-1")
        self.src.execute("UPDATE batches SET by_org = NULL")                    # an org-less queued or dead batch
        with self.assertRaisesRegex(ex.ExportError, "queued or dead"):
            self.export(out=self.out + "-1b")
        self.src.execute("DELETE FROM batches")
        insert_row(self.src, "batches", batch_id="del", machine_id="m", state="committed", by_org="{}")
        insert_row(self.src, "redactions", redaction_id="r", batch_id="del", state="queued")
        with self.assertRaisesRegex(ex.ExportError, "redactions on this server are not done"):
            self.export(out=self.out + "-2")
        self.src.execute("UPDATE redactions SET state = 'cancelled'")
        self.export(out=self.out + "-3")                                     # a cancelled one is no work
        from unittest import mock
        calls = []

        def second_fails(*a, **k):  # what holds at the start may not hold when the export ends
            calls.append(a)
            if len(calls) == 2:
                raise ex.ExportError("1 of acme's batches are still queued or dead")
        with mock.patch.object(ex, "_hold", second_fails), self.assertRaisesRegex(ex.ExportError, "queued"):
            self.export(out=self.out + "-4")
        self.assertEqual(len(calls), 2)                                     # read at the start and before the index
        self.assertFalse(os.path.exists(os.path.join(self.out + "-4", "index.json")))   # never marked ready

    def test_round_four_the_hold_sees_a_change_between_its_two_readings_and_a_young_flag(self):
        """The user organization's sec_keeper, round 4 (through of_architect, msg 01a1f6ea-b786): the second reading
        re-read the state, so a batch committed between the two, or the flag going off and on, passed unseen."""
        state = tempfile.mkdtemp(prefix="ofs-")
        cfg = {"state_dir": state}
        snap = ex._hold(self.src, "acme", cfg, None, None)
        insert_row(self.src, "batches", batch_id="new", machine_id="m", state="committed",
                   by_org=json.dumps({"acme": 1}))
        with self.assertRaisesRegex(ex.ExportError, "batch of acme arrived"):
            ex._hold(self.src, "acme", cfg, None, None, snap)
        snap = ex._hold(self.src, "acme", cfg, None, None)
        insert_row(self.src, "batches", batch_id="theirs", machine_id="m", state="committed",
                   by_org=json.dumps({"other": 1}))
        ex._hold(self.src, "acme", cfg, None, None, snap)                       # another organization's: no change
        insert_row(self.src, "batches", batch_id="nobody", machine_id="m", state="committed", by_org="{}")
        with self.assertRaisesRegex(ex.ExportError, "batch of acme arrived"):   # an org-less batch: every org's
            ex._hold(self.src, "acme", cfg, None, None, snap)                    # (sec_keeper, round 5)
        self.src.execute("DELETE FROM batches WHERE batch_id = 'nobody'")
        insert_row(self.src, "redactions", redaction_id="r9", batch_id="theirs", state="done")
        with self.assertRaisesRegex(ex.ExportError, "redaction arrived"):
            ex._hold(self.src, "acme", cfg, None, None, snap)
        flag = os.path.join(state, "moving", "acme")
        os.makedirs(os.path.dirname(flag))
        open(flag, "w").close()
        with self.assertRaisesRegex(ex.ExportError, "marked moving .* ago: wait"):
            ex._hold(self.src, "acme", cfg, "bulk", "0" * 64)                   # a final delta on a fresh flag
        old = time.time() - ex.MOVING_SETTLE_S - 5
        os.utime(flag, (old, old))
        snap = ex._hold(self.src, "acme", cfg, "bulk", "0" * 64)
        os.unlink(flag)
        open(flag, "w").close()
        os.utime(flag, (old, old))                                              # off and on again
        with self.assertRaisesRegex(ex.ExportError, "moving flag changed"):
            ex._hold(self.src, "acme", cfg, "bulk", "0" * 64, snap)

    def test_records_travel_encrypted_unless_plaintext_is_said(self):
        with self.assertRaises(ex.ExportError) as e:
            ex.export_store(self.src, "acme", self.out)
        self.assertIn("--plaintext", str(e.exception))
        _, sha = self.export()
        dst, _ = store()
        with self.assertRaises(ex.ExportError) as e:
            ex.import_store(dst, self.out, "acme", sha)
        self.assertIn("not encrypted", str(e.exception))

    def test_a_difference_found_inside_the_transaction_rolls_it_back(self):
        _, sha = self.export()
        dst, _ = store()
        dst.execute("INSERT INTO position_aliases (org, alias, position) VALUES ('acme', 'old', 'acme_old')")
        from unittest import mock
        with mock.patch.object(ex, "check_store", return_value=["messages: the store differs"]):
            with self.assertRaises(ex.ExportError):
                import_store(dst, self.out, "acme", sha)
        self.assertEqual(dst.execute("SELECT alias FROM position_aliases").fetchall(), [("old",)])
        self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_a_changed_file_is_caught_and_nothing_is_imported(self):
        _, sha = self.export()
        self.src.execute("UPDATE messages SET state = 'delivered' WHERE msg_id = '01a1-0002'")
        self.assertTrue(ex.check_store(self.src, self.out))                 # the source moved on since the export
        path = os.path.join(self.out, "store", "coord.jsonl.gz")
        with gzip.open(path, "wb") as fh:
            fh.write(json.dumps({"seq": 9, "org": "acme", "event_id": "x"}).encode() + b"\n")
        self.assertIn("store/coord.jsonl.gz: sha256 differs", ex.check_bundle(self.out))
        dst, _ = store()
        with self.assertRaises(ex.ExportError):
            import_store(dst, self.out, "acme", sha)
        self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_a_key_another_organization_holds_refuses_the_whole_import(self):
        _, sha = self.export()
        dst, _ = store()
        dst.execute("INSERT INTO coord (seq, org, event_id, name, ts, record, added) VALUES "
                    "(1, 'other', 'z', 'of.ask', '2026-11-19T09:00:00Z', '{}', 1795130346.5)")
        with self.assertRaises(ex.ExportError) as e:
            import_store(dst, self.out, "acme", sha)
        self.assertIn("another organization", str(e.exception))
        self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)   # rolled back whole

    def test_a_new_server_that_served_nothing_and_a_store_a_column_ahead_both_take_the_import(self):
        """of_review on organic-framework PR 425: a fresh server had no feed or finance table, so the import refused;
        and a store one column ahead never matched, since its own columns entered the digest."""
        _, sha = self.export()
        fresh, _ = store(tables=False)                                       # the server's own tables only
        self.assertEqual(import_store(fresh, self.out, "acme", sha)["coord"], 1)
        self.assertEqual(ex.check_store(fresh, self.out), [])
        ahead, _ = store()
        ahead.execute("ALTER TABLE position_aliases ADD COLUMN newer TEXT DEFAULT 'x'")
        self.assertEqual(import_store(ahead, self.out, "acme", sha)["position_aliases"], 1)
        self.assertEqual(ex.check_store(ahead, self.out), [])
        bare = sqlite3.connect(os.path.join(tempfile.mkdtemp(prefix="ofx-"), "x.db"), isolation_level=None)
        with self.assertRaises(ex.ExportError) as e:
            import_store(bare, self.out, "acme", sha)
        self.assertIn("start the new server once", str(e.exception))

    def test_a_source_that_never_made_a_table_exports_it_empty(self):
        self.src.execute("DROP TABLE finance_facts")
        m, sha = self.export()
        self.assertEqual(m["store"]["finance_facts"]["rows"], 0)
        self.assertEqual(m["columns"]["finance_facts"], [])
        self.assertEqual(ex.check_bundle(self.out, index_sha=sha), [])
        dst, _ = store()
        self.assertEqual(import_store(dst, self.out, "acme", sha)["finance_facts"], 0)

    @unittest.skipUnless(__import__("shutil").which("age") and __import__("shutil").which("age-keygen"), "age")
    def test_an_encrypted_bundle_holds_no_plaintext_and_imports_with_its_key_alone(self):
        """The user organization's sec_keeper (through of_architect, message 01a1f5ff-f009): encrypt to its key, with
        no plaintext temp file; and the manifest's row keys encrypted too (of_secrets-review on PR 425)."""
        ident, rec = age_key(tempfile.mkdtemp(prefix="ofk-"))
        m, sha = self.export(recipient=rec)
        self.assertTrue(m["encrypted"])
        self.assertEqual(sorted(os.listdir(self.out)), ["index.json", "manifest.json.gz.age", "store"])
        self.assertEqual(sorted(os.listdir(os.path.join(self.out, "store"))),
                         sorted(f"{t}.jsonl.gz.age" for t in ex.STORE))
        for rel, f in m["files"].items():
            self.assertNotEqual(f["sha256"], f["plain_sha256"])
            with open(os.path.join(self.out, rel), "rb") as fh:
                data = fh.read()
            self.assertTrue(data.startswith(b"age-encryption.org/v1"))
            self.assertNotIn(b"hello", data)
        index = open(os.path.join(self.out, "index.json")).read()
        for key in ("01a1-0001", "acme_ops", '"h"', "ops"):
            self.assertNotIn(key, index)                                       # no row key in the plain file
        self.assertEqual(ex.check_bundle(self.out, index_sha=sha), [])         # ciphertext hashes alone
        self.assertEqual(ex.check_bundle(self.out, ident, sha), [])            # and everything, decrypted
        dst, _ = store()
        with self.assertRaises(ex.ExportError):
            import_store(dst, self.out, "acme", sha)                        # no key: nothing imported
        other, _ = age_key(tempfile.mkdtemp(prefix="ofk-"))
        with self.assertRaises(ex.ExportError):
            import_store(dst, self.out, "acme", sha, other)                 # another key: refused
        self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
        self.assertEqual(import_store(dst, self.out, "acme", sha, ident)["messages"], 2)
        self.assertEqual(ex.check_store(dst, self.out, ident), [])
        with self.assertRaises(ex.ExportError):
            self.export(out=self.out + "-x", recipient="age1short")

    def test_an_export_from_a_file_that_is_no_server_store_is_refused(self):
        """of_review on organic-framework PR 425 at 9e0f7d4: an empty bundle with a valid index, made from a file that
        was no server's store, wiped the organization's rows when imported."""
        bare = sqlite3.connect(os.path.join(tempfile.mkdtemp(prefix="ofx-"), "x.db"), isolation_level=None)
        with self.assertRaises(ex.ExportError) as e:
            export_store(bare, "acme", self.out)
        self.assertIn("no server's store", str(e.exception))
        self.assertFalse(os.path.exists(os.path.join(self.out, "index.json")))

    def test_an_export_refuses_a_server_whose_blob_store_holds_files(self):
        """A bundle does not carry the file blob store yet, so an export never silently leaves files behind."""
        state = tempfile.mkdtemp(prefix="ofs-")
        os.makedirs(os.path.join(state, "blobs", "ab"))
        with open(os.path.join(state, "blobs", "ab", "ab12"), "wb") as fh:
            fh.write(b"x")
        with self.assertRaises(ex.ExportError) as e:
            export_store(self.src, "acme", self.out, config={"state_dir": state})
        self.assertIn("blob store holds 1 files", str(e.exception))
        empty = tempfile.mkdtemp(prefix="ofs-")
        os.makedirs(os.path.join(empty, "blobs"))
        export_store(self.src, "acme", self.out, config={"state_dir": empty})   # an empty store: no refusal

    def test_a_bundle_is_deleted_only_by_its_own_hash_and_only_with_the_files_it_names(self):
        _, sha = self.export()
        with self.assertRaises(ex.ExportError):
            ex.delete_bundle(self.out, "0" * 64)
        stray = os.path.join(self.out, "store", "planted")
        open(stray, "w").close()
        with self.assertRaises(ex.ExportError) as e:
            ex.delete_bundle(self.out, sha)
        self.assertIn("does not name", str(e.exception))
        self.assertTrue(os.path.exists(os.path.join(self.out, "index.json")))   # nothing removed
        os.unlink(stray)
        self.assertEqual(ex.delete_bundle(self.out, sha), len(ex.STORE) + 2)  # the tables, the manifest, the index
        self.assertFalse(os.path.exists(self.out))

    def test_a_symbolic_link_in_the_bundle_and_an_older_finance_table(self):
        """of_review's notes on organic-framework PR 425."""
        _, sha = self.export()
        rel = os.path.join(self.out, "store", "coord.jsonl.gz")
        os.rename(rel, rel + ".real")
        os.symlink(rel + ".real", rel)
        self.assertEqual(ex.check_bundle(self.out, index_sha=sha), ["the bundle holds a symbolic link"])
        old, _ = store(tables=False)
        old.execute("CREATE TABLE finance_facts (event_id TEXT PRIMARY KEY, org TEXT NOT NULL, period TEXT NOT NULL, "
                    "kind TEXT NOT NULL, category TEXT, product TEXT, position TEXT, sha TEXT, supersedes TEXT, "
                    "batch_id TEXT, created_at TEXT)")
        ex.ensure_schema(old)
        self.assertIn("account", ex._columns(old, "finance_facts"))

    def test_an_export_refuses_a_folder_that_is_not_empty(self):
        os.makedirs(self.out)
        open(os.path.join(self.out, "x"), "w").close()
        with self.assertRaises(ex.ExportError):
            self.export()


class FakeClickHouse:
    """Execute the actual ownership and day predicates in SQLite. Physical duplicates stay visible until DELETE."""

    schema = {
        "event": ("org_id, name, ts, event_id", "payload"),
        "transcript_record": ("session_id, record_uuid", "org_id, ts"),
        "hook_record": ("host_id, local_id", "received_at, session_id"),
        "runtime_record": ("host_id, received_at, type, pane_id", "raw"),
    }

    def __init__(self, prefix, org_cols=False):
        """ORG_COLS: hook_record and runtime_record carry org_id, as after deploy/clickhouse/schema.py's ALTER."""
        self.conn = sqlite3.connect(":memory:")
        self.calls = []
        self.inserts = []
        self.conn.create_function("toDate", 1, lambda ts: ts[:10])
        self.conn.create_function("tuple", -1, lambda *key: json.dumps(key))
        self.conn.create_function("toString", 1, str)
        self.conn.create_function("notEmpty", 1, bool)
        self.conn.create_function("cityHash64", -1, lambda *v: int(hashlib.sha256(json.dumps(v).encode()).hexdigest()[:15], 16))

        class Xor:
            def __init__(self):
                self.v = 0

            def step(self, x):
                self.v ^= x

            def finalize(self):
                return self.v
        self.conn.create_aggregate("groupBitXor", 1, Xor)
        for role in ("org", "vault"):
            db = prefix + "_" + role
            self.conn.execute(f"ATTACH ':memory:' AS {db}")
            for t, (keys, extra) in self.schema.items():
                more = ", org_id" if org_cols and t in ("hook_record", "runtime_record") else ""
                self.conn.execute(f"CREATE TABLE {db}.{t} ({keys}, {extra}{more})")

    def add(self, table, *row):
        self.conn.execute(f"INSERT INTO {table} VALUES ({', '.join('?' * len(row))})", row)

    def rows(self, table):
        return self.conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()

    def query(self, sql, params):
        self.calls.append((sql, dict(params)))
        assert all(value not in sql for value in params.values())
        assert set(re.findall(r"\{(\w+):\w+\}", sql)) == params.keys()
        sql = re.sub(r"\{(\w+):\w+\}", r":\1", sql)
        if "FROM system.columns" in sql:
            db = params["db"]
            if "t" in params:  # one table's columns, in order
                yield from (f"{r[1]}\n".encode() for r in self.conn.execute(f"PRAGMA {db}.table_info({params['t']})"))
                return
            yield from (f"{t}\n".encode() for t in self.schema
                        if "org_id" in [r[1] for r in self.conn.execute(f"PRAGMA {db}.table_info({t})")])
            return
        made = re.fullmatch(r"CREATE TABLE IF NOT EXISTS (\S+) AS (\S+)", sql)
        if made:
            self.conn.execute(f"CREATE TABLE IF NOT EXISTS {made[1]} AS SELECT * FROM {made[2]} WHERE 0")
            return
        if sql.startswith(("TRUNCATE TABLE ", "DROP TABLE ", "INSERT INTO ")):
            self.conn.execute(sql.replace("TRUNCATE TABLE ", "DELETE FROM ", 1), params)
            return
        if sql.startswith("ALTER TABLE"):
            assert sql.endswith(" SETTINGS mutations_sync = 2")
            delete = sql.removesuffix(" SETTINGS mutations_sync = 2")
            self.conn.execute(re.sub(r"^ALTER TABLE (\w+\.\w+) DELETE", r"DELETE FROM \1", delete), params)
            return
        sql, fmt = sql.rsplit(" FORMAT ", 1)
        if not sql.startswith("SELECT DISTINCT"):
            # FINAL models a ReplacingMergeTree selecting the last physical row for each sorting tuple.
            source = re.search(r"FROM (\w+\.(\w+)) FINAL", sql)
            if source:
                table, keys = source[1], self.schema[source[2].removesuffix("__import")][0]
                final = f"(SELECT * FROM {table} WHERE rowid IN (SELECT max(rowid) FROM {table} GROUP BY {keys}))"
                sql = sql.replace(f"FROM {table} FINAL", "FROM " + final, 1)
        rows = self.conn.execute(sql, params).fetchall()
        if fmt == "Native":
            yield b"\x00\xff"
            yield json.dumps(rows).encode()
        elif fmt == "TabSeparated":
            yield from (f"{r[0]}\n".encode() for r in rows)
        else:
            assert fmt == "JSONCompactEachRow"
            if "min(tuple(" in sql:  # a day summary; a count of rows left behind has no keys
                assert f"min(tuple({keys}))" in sql and f"max(tuple({keys}))" in sql
            yield from (json.dumps(r).encode() + b"\n" for r in rows)

    def insert(self, sql, chunks):
        table = re.fullmatch(r"INSERT INTO (\w+\.\w+) FORMAT Native", sql)[1]
        self.inserts.append(table)
        assert iter(chunks) is chunks
        data = b"".join(chunks)
        assert data[:2] == b"\x00\xff"
        for row in json.loads(data[2:]):
            self.add(table, *row)


class StorageChoiceTest(unittest.TestCase):
    def test_clickhouse_follows_the_storage_plugin_not_server_yaml_alone(self):
        """The laptop's server keeps its ClickHouse connection in ship.yaml, with no `clickhouse` in server.yaml."""
        self.assertTrue(ex._uses_clickhouse({"plugins": {"storage": "clickhouse"}}))
        self.assertFalse(ex._uses_clickhouse({"clickhouse": {"url": "http://localhost:8123"}}))   # sqlite storage
        self.assertFalse(ex._uses_clickhouse({}))


class OrgColumnTest(unittest.TestCase):
    """Vault hook and runtime rows written from 601dacfe on name their organization; older ones do not."""

    def test_new_vault_rows_go_by_their_organization_and_old_ones_by_session_or_stay(self):
        src = FakeClickHouse("src", org_cols=True)
        self.addCleanup(src.conn.close)
        d = "2026-11-20"
        src.add("src_vault.transcript_record", "S-own", "1", "acme", d)
        src.add("src_vault.transcript_record", "S-two", "2", "acme", d)
        src.add("src_vault.transcript_record", "S-two", "3", "other", d)
        src.add("src_vault.hook_record", "h", 1, d, "S-two", "acme")      # names acme: acme's, whatever its session
        src.add("src_vault.hook_record", "h", 2, d, "S-own", "")          # names none: its session's one organization
        src.add("src_vault.hook_record", "h", 3, d, "S-two", "")          # names none, session of two: stays behind
        src.add("src_vault.runtime_record", "h", d, "t", "p1", "x", "acme")
        src.add("src_vault.runtime_record", "h", d, "t", "p2", "x", "")   # names none: stays behind
        dbs = {"org": "src_org", "vault": "src_vault"}
        cols = ex._org_columns(src, dbs)
        self.assertTrue({"hook_record", "runtime_record"} <= cols)
        tables = {t.name: t for t in ex._ch_tables(dbs, "acme", cols)}
        hook = ex._ch_days(src, tables["vault/hook_record"], ex._ch_columns(src, "src_vault.hook_record"))
        runtime = ex._ch_days(src, tables["vault/runtime_record"], ex._ch_columns(src, "src_vault.runtime_record"))
        self.assertEqual(hook[d][0], 2)
        self.assertEqual(runtime[d][0], 1)
        left = ex._left_behind(src, dbs, cols)
        self.assertEqual((left["vault/runtime_record"], left["vault/hook_record"]), ({d: 1}, {d: 1}))
        legacy = {t.name for t in ex._ch_tables(dbs, "acme", frozenset({"event", "transcript_record"}))}
        self.assertNotIn("vault/runtime_record", legacy)   # a vault with no org_id there: every runtime row stays


FAKE_RECIPIENT = "age1" + "q" * 58
REAL_AGE_PATHS = ex.AGE_PATHS


def fake_age(folder):
    """An `age` that copies: `-r R` passes stdin through, `-d -i I F` prints F."""
    path = os.path.join(folder, "age")
    with open(path, "w") as fh:
        fh.write(f"#!{sys.executable}\nimport sys, shutil\na = sys.argv[1:]\n"
                 "src = open(a[-1], 'rb') if '-d' in a else sys.stdin.buffer\n"
                 "shutil.copyfileobj(src, sys.stdout.buffer)\n")
    os.chmod(path, 0o700)
    return path


class ClickHouseExportTest(unittest.TestCase):
    org = "owner's org"
    early, last, new = "2026-11-17", "2026-11-18", "2026-11-19"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bulk = Path(tmp.name) / "bulk"
        self.delta = self.bulk.with_name("delta")
        age = mock.patch.object(ex, "AGE_PATHS", (fake_age(tmp.name),))   # ClickHouse rows always travel encrypted
        age.start()
        self.addCleanup(age.stop)
        self.ident = os.path.join(tmp.name, "identity")
        self.store, self.state = store()
        self.addCleanup(self.store.close)
        self.src, self.dst = FakeClickHouse("src"), FakeClickHouse("dst")
        self.addCleanup(self.src.conn.close)
        self.addCleanup(self.dst.conn.close)
        self.cfg = {"state_dir": self.state, "plugins": {"storage": "clickhouse"},
                    "clickhouse": {"url": "http://localhost:8123"}}
        ship = mock.patch("of_ship.config", side_effect=dict)
        ship.start()
        self.addCleanup(ship.stop)
        for day in (self.early, self.last):
            self.src.add("src_org.event", "routed copy", "ask", day, "id", "private")
            self.src.add("src_vault.event", self.org, "ask", day, "id", "old")
            self.src.add("src_vault.event", self.org, "ask", day, "id", "private")
            self.src.add("src_vault.event", "other", "ask", day, "id", "theirs")
        self.src.add("src_org.hook_record", "host", 11, self.last, "no transcript")
        self.src.add("src_org.runtime_record", "host", self.last, "shell", "pane", "private")
        self.src.add("src_vault.runtime_record", "host", self.last, "shell", "pane", "left behind")
        for i, (session, org) in enumerate((("own", self.org), ("foreign", "other"), ("mixed", self.org),
                                            ("mixed", "other"), ("", self.org))):
            self.src.add("src_vault.transcript_record", session, str(i), org, self.early)
        for i, session in enumerate(("own", "foreign", "mixed", "unknown", "")):
            self.src.add("src_vault.hook_record", "host", i, self.last, session)

    def connection(self, prefix):
        self.cfg.update(orgs={self.org: prefix + "_org"}, vault_database=prefix + "_vault")
        return mock.patch.object(ex, "ClickHouseHTTP", return_value=self.src if prefix == "src" else self.dst)

    def export(self, path=None, **kw):
        kw.setdefault("recipient", FAKE_RECIPIENT)
        if kw.get("since") is not None:  # the final delta is made with the organization marked moving
            os.makedirs(os.path.join(self.state, "moving"), exist_ok=True)
            flag = os.path.join(self.state, "moving", self.org)
            if not os.path.exists(flag):
                open(flag, "w").close()
                old = time.time() - ex.MOVING_SETTLE_S - 5   # set long enough ago for every request to have seen it
                os.utime(flag, (old, old))
        with self.connection("src"):
            return export_store(self.store, self.org, path or self.bulk, config=self.cfg, **kw)

    def restore(self, sha, path=None, identity=None):
        with self.connection("dst"):
            return import_store(self.store, path or self.bulk, self.org, sha, identity or self.ident, config=self.cfg)

    def test_native_files_follow_routing_and_vault_ownership(self):
        m, sha = self.export()
        self.assertEqual(ex.check_bundle(self.bulk, index_sha=sha), [])
        self.assertEqual(m, ex.load_manifest(self.bulk, self.ident))
        ix = ex.load_index(self.bulk)
        self.assertEqual(set(ix["files"]), set(m["files"]) | {"manifest.json.gz.age"})
        for p in self.bulk.rglob("*"):
            self.assertEqual(p.stat().st_mode & 0o777, 0o700 if p.is_dir() else 0o600)

        def native(name, day):
            rel = f"clickhouse/{name}/{day}.native.gz.age"
            data = (self.bulk / rel).read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            self.assertEqual((m["files"][rel], ix["files"][rel]), ({"sha256": sha, "plain_sha256": sha}, sha))
            return json.loads(gzip.decompress(data)[2:])

        self.assertEqual(native("org/hook_record", self.last), [["host", 11, self.last, "no transcript"]])
        self.assertEqual(native("org/runtime_record", self.last), [["host", self.last, "shell", "pane", "private"]])
        self.assertEqual(native("org/event", self.last)[0][0], "routed copy")
        self.assertEqual(native("vault/event", self.last), [[self.org, "ask", self.last, "id", "private"]])
        self.assertEqual({r[2] for r in native("vault/transcript_record", self.early)}, {self.org})
        self.assertEqual(native("vault/hook_record", self.last), [["host", 0, self.last, "own"]])
        self.assertEqual(m["clickhouse"]["vault/runtime_record"], {"rows": 0, "days": {}})
        left = m["clickhouse_left_behind"]   # per table and UTC day (the user organization's keeper, msg 01a1f620-c225)
        self.assertEqual(left["vault/runtime_record"], {self.last: 1})
        self.assertTrue(all(type(n) is int for n in left["vault/hook_record"].values()))
        self.assertFalse((self.bulk / "clickhouse/vault/runtime_record").exists())

    def test_delta_replaces_only_its_days_and_keeps_other_vault_rows(self):
        _, sha = self.export()
        for session in ("mixed", "foreign"):
            self.dst.add("dst_vault.transcript_record", session, "other-id", "other", self.last)
            self.dst.add("dst_vault.hook_record", "other-host", 1, self.last, session)
        self.dst.add("dst_vault.event", "other", "ask", self.last, "id", "theirs")
        self.restore(sha)
        self.src.add("src_vault.event", self.org, "ask", self.last, "late", "arrival")
        self.src.add("src_vault.event", self.org, "ask", self.new, "new", "private")
        # The hook remains on the source but its ownership evidence vanished. Delete it while the target still
        # has that evidence, even though this delta also replaces its transcript day.
        self.src.conn.execute("DELETE FROM src_vault.transcript_record WHERE session_id = 'own'")
        m, sha = self.export(self.delta, since=self.bulk, since_sha=sha)
        self.assertEqual(set(m["clickhouse"]["vault/event"]["days"]), {self.last, self.new})   # changed or new only
        self.assertEqual(m["clickhouse"]["vault/hook_record"]["days"], {self.last: ex.EMPTY_DAY})
        for _ in range(2):
            self.restore(sha, self.delta)
            self.assertEqual(len(self.dst.rows("dst_vault.event")), 5)
            self.assertIn(("other", "ask", self.last, "id", "theirs"), self.dst.rows("dst_vault.event"))
            self.assertEqual([r[-1] for r in self.dst.rows("dst_vault.hook_record")], ["mixed", "foreign"])
            self.assertEqual(len(self.dst.rows("dst_org.runtime_record")), 1)
        self.dst.conn.execute("UPDATE dst_vault.event SET event_id = 'changed' WHERE ts = ?", (self.new,))
        with self.connection("dst"):
            self.assertEqual(check_import(self.store, self.delta, self.org, sha, self.ident, config=self.cfg),
                             [f"vault/event: ClickHouse differs on {self.new}"])

    def test_failure_after_a_native_file_leaves_no_published_bundle(self):
        original = self.src.query

        def fail(sql, params):
            if sql.endswith("FORMAT Native") and params["d"] == self.last:
                yield b"partial block"
                raise RuntimeError("private row or credential")
            yield from original(sql, params)

        with mock.patch.object(self.src, "query", fail), self.assertRaisesRegex(
                ex.ExportError, f"^org/event {self.last}: export failed$"):
            self.export()
        self.assertTrue(list(self.bulk.rglob("*.native.gz*")))
        self.assertFalse((self.bulk / "index.json").exists())
        self.assertFalse((self.bulk / "manifest.json.gz.age").exists())
        with self.assertRaisesRegex(ex.ExportError, "index.json"):
            self.restore("0" * 64)

    def test_clickhouse_waits_for_both_hashes_and_the_store_transaction(self):
        m, sha = self.export()
        self.store.execute("INSERT INTO position_aliases (org, alias, position) VALUES (?, 'old', 'p')", (self.org,))
        rel = f"clickhouse/vault/hook_record/{self.last}.native.gz.age"
        path = self.bulk / rel
        original = path.read_bytes()
        path.write_bytes(original + b"changed")
        with self.assertRaisesRegex(ex.ExportError, "sha256 differs"):
            self.restore(sha)
        path.write_bytes(original)
        with self.assertRaisesRegex(ex.ExportError, "not the one the export printed"):
            self.restore("0" * 64)
        with self.connection("dst"), self.assertRaisesRegex(ex.ExportError, "not other's"):
            import_store(self.store, self.bulk, "other", sha, self.ident, config=self.cfg)
        with mock.patch.object(ex, "check_store", return_value=["messages: mismatch"]), self.assertRaises(ex.ExportError):
            self.restore(sha)
        m["files"][rel]["plain_sha256"] = "0" * 64
        manifest = self.bulk / "manifest.json.gz.age"
        manifest.write_bytes(gzip.compress(json.dumps(m).encode()))
        ix = ex.load_index(self.bulk)
        ix["files"][manifest.name] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        index = self.bulk / "index.json"
        index.write_text(json.dumps(ix))
        with self.assertRaisesRegex(ex.ExportError, "decrypted stream's sha256 differs"):
            self.restore(hashlib.sha256(index.read_bytes()).hexdigest())
        self.assertEqual(self.store.execute("SELECT alias FROM position_aliases").fetchall(), [("old",)])
        self.assertFalse(self.dst.calls or self.dst.inserts)

    def test_rehashed_foreign_store_row_prevents_clickhouse_import(self):
        self.export()
        sha = rebuild(self.bulk, "position_aliases", [{"org": "other", "alias": "x", "position": "taken"}], self.ident)
        with self.assertRaisesRegex(ex.ExportError, "by the table's own rule"):
            self.restore(sha)
        self.assertFalse(self.dst.calls or self.dst.inserts)

    def test_index_cannot_omit_a_native_day(self):
        self.export()
        ix = ex.load_index(self.bulk)
        del ix["files"][f"clickhouse/vault/hook_record/{self.last}.native.gz.age"]
        index = self.bulk / "index.json"
        index.write_text(json.dumps(ix))
        with self.assertRaisesRegex(ex.ExportError, "index.json omits a Native file"):
            self.restore(hashlib.sha256(index.read_bytes()).hexdigest())
        self.assertFalse(self.dst.calls or self.dst.inserts)

    def test_http_escapes_bound_values_and_streams_insert_body(self):
        pw = self.bulk.with_name("password")
        pw.write_text("test-password")
        ch = ex.ClickHouseHTTP({"url": "http://localhost:8123", "user": "test", "password_file": pw})
        sql = "SELECT {org:String}"
        with mock.patch.object(ex, "_open", return_value=io.BytesIO(b"response")) as post:
            self.assertEqual(b"".join(ch.query(sql, {"org": "tab\tand\\slash"})), b"response")
            req = post.call_args.args[0]
            self.assertEqual(req.data, sql.encode())
            self.assertEqual(parse_qs(urlsplit(req.full_url).query), {"param_org": ["tab\\tand\\\\slash"]})
            self.assertNotIn("test-password", req.full_url)
        with mock.patch.object(ex, "_open", return_value=io.BytesIO()) as post:
            blocks = iter([b"one", b"two"])
            ch.insert("INSERT INTO dst_org.event FORMAT Native", blocks)
            self.assertIs(post.call_args.args[0].data, blocks)
        with mock.patch.object(ex, "_open", side_effect=OSError("private credential or row")):
            with self.assertRaisesRegex(ex.ExportError, "^ClickHouse request failed$"):
                list(ch.query(sql, {"org": self.org}))

    def test_cli_exports_imports_and_checks_with_the_printed_hash(self):
        output = io.StringIO()
        with mock.patch.dict(sys.modules, yaml=mock.Mock()), mock.patch.object(of_server, "load_config",
                return_value=self.cfg), redirect_stdout(output), redirect_stderr(output):
            cli = runpy.run_path(str(Path(ex.__file__).with_name("of")))["main"]
            with self.connection("src"):
                self.assertEqual(cli(["server", "export", "--org", self.org, "--to", FAKE_RECIPIENT, str(self.bulk)]), 0)
                bulk_sha = hashlib.sha256((self.bulk / "index.json").read_bytes()).hexdigest()
                os.makedirs(os.path.join(self.state, "moving"), exist_ok=True)   # the final delta: marked moving
                flag = os.path.join(self.state, "moving", self.org)
                open(flag, "w").close()
                old = time.time() - ex.MOVING_SETTLE_S - 5
                os.utime(flag, (old, old))
                self.src.add("src_vault.event", self.org, "ask", self.new, "new", "private")
                self.assertEqual(cli(["server", "export", "--org", self.org, "--to", FAKE_RECIPIENT, "--since", str(self.bulk),
                                      "--since-sha", bulk_sha, str(self.delta)]), 0)
            self.assertEqual(output.getvalue().count("index sha256 "), 2)
            self.assertEqual(cli(["server", "export", "--check", str(self.delta)]), 0)
            with self.connection("dst"):
                for bundle in (self.bulk, self.delta):
                    sha = hashlib.sha256((bundle / "index.json").read_bytes()).hexdigest()
                    args = ["server", "import", "--org", self.org, "--identity", self.ident, "--index-sha", sha,
                            str(bundle)]
                    self.assertEqual(cli(args), 0)
                    self.assertEqual(cli(args + ["--check"]), 0)
                self.dst.conn.execute("DELETE FROM dst_org.runtime_record")
                self.assertEqual(cli(args + ["--check"]), 1)
        self.assertIn(f"org/runtime_record: ClickHouse differs on {self.last}", output.getvalue())
        self.assertNotIn("private", output.getvalue())

    def test_a_redaction_on_an_earlier_day_reaches_the_delta_and_another_release_is_refused(self):
        """sec_keeper, msg 01a1f638-5862, 2(b) and 2(c): a delta by the bulk's last day never moved a redaction or a
        late row of an earlier day, and the release was written but never compared."""
        _, sha = self.export()
        self.src.conn.execute("DELETE FROM src_vault.event WHERE ts = ? AND payload = 'private'", (self.early,))
        m, dsha = self.export(self.delta, since=self.bulk, since_sha=sha)
        self.assertIn(self.early, m["clickhouse"]["vault/event"]["days"])         # the redacted day is carried
        self.assertNotIn(self.early, m["clickhouse"]["org/event"]["days"])        # an unchanged day is not
        with mock.patch.object(ex, "_code", return_value="0" * 64), self.assertRaisesRegex(
                ex.ExportError, "another release"):
            self.restore(dsha, self.delta)
        with mock.patch.object(ex, "_code", return_value="0" * 64), self.assertRaisesRegex(
                ex.ExportError, "another release"):
            self.export(self.bulk.with_name("delta2"), since=self.bulk, since_sha=sha)

    def test_staged_rows_that_differ_from_the_bundle_leave_the_target_unchanged(self):
        """sec_keeper, 5: the Native files were read again for the insert without a hash, and a failed insert left
        the target's days deleted."""
        _, sha = self.export()
        self.dst.add("dst_org.event", "kept", "ask", self.last, "kept", "kept")
        before = self.dst.rows("dst_org.event")
        original = self.dst.insert

        def short(sql, chunks):  # the stream arrives, but one row is lost on the way in
            data = b"".join(chunks)
            rows = json.loads(data[2:])[:-1]
            original(sql, iter([b"\x00\xff" + json.dumps(rows).encode()]))
        with mock.patch.object(self.dst, "insert", short), self.assertRaisesRegex(
                ex.ExportError, "staged rows differ|stream inserted"):
            self.restore(sha)
        self.assertEqual(self.dst.rows("dst_org.event"), before)

    def test_rows_naming_no_organization_are_counted_as_left_behind(self):
        """sec_keeper, 6: a session whose transcript rows all name no organization counted as taken, and vault event
        and transcript rows naming none were not counted."""
        self.src.add("src_vault.event", "", "ask", self.last, "x", "nobody's")
        self.src.add("src_vault.transcript_record", "S-none", "9", "", self.early)   # a session naming no organization
        self.src.add("src_vault.hook_record", "host", 9, self.last, "S-none")
        m, _ = self.export()
        left = m["clickhouse_left_behind"]
        self.assertEqual(left["vault/event"], {self.last: 1})
        self.assertEqual(left["vault/transcript_record"], {self.early: 1})        # the session-less row of no org
        self.assertGreaterEqual(sum(left["vault/hook_record"].values()), 2)      # unknown and the no-org session

    def test_pr433_refusals_unscopable_tables_foreign_staged_rows_and_clear_clickhouse(self):
        """of_review and of_secrets-review on organic-framework PR 433: a target vault runtime_record without org_id
        was skipped while the import reported success; staged vault rows went in unjudged; and raw vault rows could
        leave in clear with --plaintext."""
        dbs = {"org": "dst_org", "vault": "dst_vault"}
        empty = {f"{r}/{t}": {"rows": 0, "days": {}} for r in ("org", "vault") for t in ex.CH_TABLES}
        m = {"org": self.org, "clickhouse": dict(empty, **{"vault/runtime_record": {
            "rows": 1, "days": {self.last: [1, "a", "a", "1"]}}})}
        with self.assertRaisesRegex(ex.ExportError, "cannot take vault/runtime_record"):
            ex._target_tables(self.dst, dbs, m)
        self.dst.conn.execute("CREATE TABLE dst_vault.event__import AS SELECT * FROM dst_vault.event WHERE 0")
        self.dst.add("dst_vault.event__import", "other", "ask", self.last, "id", "theirs")
        table = next(t for t in ex._ch_tables(dbs, self.org) if t.name == "vault/event")
        self.assertEqual(ex._foreign_rows(self.dst, table, "dst_vault.event__import", self.org), 1)
        with self.assertRaisesRegex(ex.ExportError, "always encrypted"):
            self.export(recipient=None, plaintext=True)

    def test_a_bundle_path_that_is_a_link_is_not_removed(self):
        _, sha = self.export()
        link = self.bulk.with_name("link")
        link.symlink_to(self.bulk)
        with self.assertRaisesRegex(ex.ExportError, "symbolic link"):
            ex.delete_bundle(str(link), sha)
        self.assertTrue((self.bulk / "index.json").exists())

    def test_sec_keepers_second_round_redactions_moving_swap_and_links(self):
        """The user organization's sec_keeper on d24d5bc8 (through of_architect, msg 01a1f645-eae4)."""
        _, sha = self.export()
        # 1: a redaction not done refuses the export, and the final delta needs the organization marked moving
        insert_row(self.store, "batches", batch_id="rb", machine_id="m", state="committed",
                   by_org=json.dumps({self.org: 1}))
        self.store.execute("INSERT INTO redactions (redaction_id, batch_id, state) VALUES ('r1', 'rb', 'queued')")
        with self.assertRaisesRegex(ex.ExportError, "redactions .* are not done"):
            self.export(self.bulk.with_name("x1"))
        self.store.execute("UPDATE redactions SET state = 'done'")
        with self.connection("src"), self.assertRaisesRegex(ex.ExportError, "marked moving"):
            export_store(self.store, self.org, self.bulk.with_name("x2"), config=self.cfg, recipient=FAKE_RECIPIENT,
                         since=self.bulk, since_sha=sha)
        # 2: a swap's insert that fails twice names the day and keeps the staging table
        original = self.dst.query

        def failing(sql, params):
            if sql.startswith("INSERT INTO dst_vault.event SELECT"):
                raise RuntimeError("private row")
            yield from original(sql, params)
        with mock.patch.object(self.dst, "query", failing), self.assertRaisesRegex(
                ex.ExportError, "swap stopped: .* not inserted yet .*vault/event .*staging tables are kept"):
            self.restore(sha)
        self.assertTrue(self.dst.rows("dst_vault.event__import"))
        # 4: a link inside the bundle refuses --delete before any file goes
        os.symlink(self.bulk / "store", self.bulk / "clickhouse" / "link")
        with self.assertRaisesRegex(ex.ExportError, "symbolic link"):
            ex.delete_bundle(str(self.bulk), sha)
        self.assertTrue((self.bulk / "index.json").exists())

    def test_plain_http_only_on_the_loopback(self):
        with self.assertRaisesRegex(ex.ExportError, "loopback"):
            ex.ClickHouseHTTP({"url": "http://192.0.2.10:8123", "user": "u", "password_file": "/nonexistent"})

    @unittest.skipUnless(__import__("shutil").which("age") and __import__("shutil").which("age-keygen"), "age")
    def test_encrypted_native_and_bulk_manifest_streams(self):
        real = mock.patch.object(ex, "AGE_PATHS", REAL_AGE_PATHS)
        real.start()
        self.addCleanup(real.stop)
        ident, rec = age_key(self.bulk.parent)
        m, sha = self.export(recipient=rec)
        for path in self.bulk.rglob("*"):
            if path.is_file() and path.name != "index.json":
                self.assertEqual(path.suffix, ".age")
                self.assertTrue(path.read_bytes().startswith(b"age-encryption.org/v1"))
        self.assertTrue(all(v["sha256"] != v["plain_sha256"] for v in m["files"].values()))
        self.assertEqual(ex.check_bundle(self.bulk, index_sha=sha), [])
        with self.connection("dst"), self.assertRaisesRegex(ex.ExportError, "--identity"):
            import_store(self.store, self.bulk, self.org, sha, None, config=self.cfg)
        self.assertFalse(self.dst.calls or self.dst.inserts)
        self.restore(sha, identity=ident)
        self.src.add("src_vault.event", self.org, "ask", self.new, "new", "private")
        delta, dsha = self.export(self.delta, since=self.bulk, since_sha=sha, recipient=rec)   # no key needed here
        self.assertEqual(set(delta["clickhouse"]["vault/event"]["days"]), {self.new})
        self.restore(dsha, self.delta, ident)


if __name__ == "__main__":
    unittest.main()
