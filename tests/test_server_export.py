"""`of server export` and `import`, the server store's part: one organization's rows moved between of-servers and
checked the same way on both sides (bin/of_server_export.py; 2026-11-19, of_server)."""

import gzip
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_server  # noqa: E402
import of_server_export as ex  # noqa: E402


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


def rebuild(bundle, table, rows):
    """Rewrite one plain table file of BUNDLE with ROWS and make its manifest and index agree, as a bundle changed by
    someone who recomputed every hash would."""
    m = ex.load_manifest(bundle)
    rel = f"store/{table}.jsonl.gz"
    path = os.path.join(bundle, rel)
    os.unlink(path)
    m["files"][rel] = ex._write_file(path, (json.dumps(r).encode() + b"\n" for r in rows))
    m["store"][table] = ex.summarize(table, rows)
    mp = os.path.join(bundle, "manifest.json.gz")
    os.unlink(mp)
    w = ex._write_file(mp, [json.dumps(m, sort_keys=True).encode()])
    ix = ex.load_index(bundle)
    ix["files"][rel], ix["files"]["manifest.json.gz"] = m["files"][rel]["sha256"], w["sha256"]
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
        return ex.export_store(self.src, org, out or self.out, **kw)

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
        self.assertEqual(ex.import_store(dst, self.out, "acme", sha)["messages"], 2)
        self.assertEqual(ex.check_store(dst, self.out), [])
        self.assertEqual(ex.check_store(self.src, self.out), [])
        got = dst.execute("SELECT seq, state FROM messages ORDER BY seq").fetchall()
        self.assertEqual(got, [(1, "done"), (2, "queued")])               # sequence numbers kept: feed cursors hold
        self.assertEqual(ex.import_store(dst, self.out, "acme", sha)["messages"], 2)   # again: replaced, not doubled
        self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)

    def test_the_operator_names_the_organization_and_carries_the_index_hash(self):
        """of_secrets-review on organic-framework PR 425: an honest bundle of A labelled B replaced B, and age
        authenticates no sender."""
        _, sha = self.export()
        dst, _ = store()
        for org, given, words in (("other", sha, "not other's"), ("acme", None, "--index-sha"),
                                  ("acme", "0" * 64, "not the one the export printed")):
            with self.subTest(org=org, given=given), self.assertRaises(ex.ExportError) as e:
                ex.import_store(dst, self.out, org, given)
            self.assertIn(words, str(e.exception))
        self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_a_row_of_another_organization_or_an_unknown_column_refuses_the_import_before_any_change(self):
        """PR 425: a changed bundle with every hash recomputed wrote another organization's rows, and a column the
        destination lacks was dropped silently."""
        dst, _ = store()
        dst.execute("INSERT INTO position_aliases (org, alias, position) VALUES ('other', 'x', 'other_x')")
        dst.execute("INSERT INTO position_aliases (org, alias, position) VALUES ('acme', 'old', 'acme_old')")
        for rows, words in (([{"org": "other", "alias": "x", "position": "taken"}], "not acme's"),
                            ([{"org": "acme", "alias": "y", "position": "p", "planted": 1}], "no column planted")):
            out = self.out + str(len(words))
            self.export(out=out)
            sha = rebuild(out, "position_aliases", rows)
            self.assertEqual(ex.check_bundle(out, index_sha=sha), [])  # the hashes all agree: only the rows tell
            with self.subTest(words=words), self.assertRaises(ex.ExportError) as e:
                ex.import_store(dst, out, "acme", sha)
            self.assertIn(words, str(e.exception))
        self.assertEqual(sorted(dst.execute("SELECT org, alias, position FROM position_aliases").fetchall()),
                         [("acme", "old", "acme_old"), ("other", "x", "other_x")])     # nothing changed

    def test_a_difference_found_inside_the_transaction_rolls_it_back(self):
        _, sha = self.export()
        dst, _ = store()
        dst.execute("INSERT INTO position_aliases (org, alias, position) VALUES ('acme', 'old', 'acme_old')")
        from unittest import mock
        with mock.patch.object(ex, "check_store", return_value=["messages: the store differs"]):
            with self.assertRaises(ex.ExportError):
                ex.import_store(dst, self.out, "acme", sha)
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
            ex.import_store(dst, self.out, "acme", sha)
        self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_a_key_another_organization_holds_refuses_the_whole_import(self):
        _, sha = self.export()
        dst, _ = store()
        dst.execute("INSERT INTO coord (seq, org, event_id, name, ts, record, added) VALUES "
                    "(1, 'other', 'z', 'of.ask', '2026-11-19T09:00:00Z', '{}', 1795130346.5)")
        with self.assertRaises(ex.ExportError) as e:
            ex.import_store(dst, self.out, "acme", sha)
        self.assertIn("another organization", str(e.exception))
        self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)   # rolled back whole

    def test_a_new_server_that_served_nothing_and_a_store_a_column_ahead_both_take_the_import(self):
        """of_review on organic-framework PR 425: a fresh server had no feed or finance table, so the import refused;
        and a store one column ahead never matched, since its own columns entered the digest."""
        _, sha = self.export()
        fresh, _ = store(tables=False)                                       # the server's own tables only
        self.assertEqual(ex.import_store(fresh, self.out, "acme", sha)["coord"], 1)
        self.assertEqual(ex.check_store(fresh, self.out), [])
        ahead, _ = store()
        ahead.execute("ALTER TABLE position_aliases ADD COLUMN newer TEXT DEFAULT 'x'")
        self.assertEqual(ex.import_store(ahead, self.out, "acme", sha)["position_aliases"], 1)
        self.assertEqual(ex.check_store(ahead, self.out), [])
        bare = sqlite3.connect(os.path.join(tempfile.mkdtemp(prefix="ofx-"), "x.db"), isolation_level=None)
        with self.assertRaises(ex.ExportError) as e:
            ex.import_store(bare, self.out, "acme", sha)
        self.assertIn("start the new server once", str(e.exception))

    def test_a_source_that_never_made_a_table_exports_it_empty(self):
        self.src.execute("DROP TABLE finance_facts")
        m, sha = self.export()
        self.assertEqual(m["store"]["finance_facts"]["rows"], 0)
        self.assertEqual(m["columns"]["finance_facts"], [])
        self.assertEqual(ex.check_bundle(self.out, index_sha=sha), [])
        dst, _ = store()
        self.assertEqual(ex.import_store(dst, self.out, "acme", sha)["finance_facts"], 0)

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
            ex.import_store(dst, self.out, "acme", sha)                        # no key: nothing imported
        other, _ = age_key(tempfile.mkdtemp(prefix="ofk-"))
        with self.assertRaises(ex.ExportError):
            ex.import_store(dst, self.out, "acme", sha, other)                 # another key: refused
        self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
        self.assertEqual(ex.import_store(dst, self.out, "acme", sha, ident)["messages"], 2)
        self.assertEqual(ex.check_store(dst, self.out, ident), [])
        with self.assertRaises(ex.ExportError):
            self.export(out=self.out + "-x", recipient="age1short")

    def test_an_export_refuses_a_folder_that_is_not_empty(self):
        os.makedirs(self.out)
        open(os.path.join(self.out, "x"), "w").close()
        with self.assertRaises(ex.ExportError):
            self.export()


if __name__ == "__main__":
    unittest.main()
