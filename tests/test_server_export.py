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
    """A server store with the tables its modules make at the server's start."""
    import of_server_coord
    import of_server_finance
    import of_server_passkey
    import of_server_relay
    import of_server_secrets
    d = tempfile.mkdtemp(prefix="ofx-")
    of_server.Store(d)
    c = sqlite3.connect(os.path.join(d, "server.db"), isolation_level=None)
    if tables:
        for ddl in (of_server_relay.DDL, of_server_coord.DDL, of_server_finance.DDL, of_server_passkey.DDL,
                    of_server_secrets.DDL):
            c.executescript(ddl)
        have = [r[1] for r in c.execute("PRAGMA table_info(messages)")]
        for col, decl in (("priority", "TEXT DEFAULT 'normal'"), ("moved_from", "TEXT"), ("moved_by", "TEXT")):
            if col not in have:
                c.execute(f"ALTER TABLE messages ADD COLUMN {col} {decl}")
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


class ExportTest(unittest.TestCase):

    def setUp(self):
        self.src, _ = store()
        fill(self.src)
        self.out = os.path.join(tempfile.mkdtemp(prefix="ofb-"), "bundle")

    def test_an_export_holds_the_organizations_rows_alone_and_a_cross_message_in_both(self):
        m = ex.export_store(self.src, "acme", self.out, version="t")
        self.assertEqual(m["store"]["messages"]["rows"], 2)                 # its own, and the one it sent across
        self.assertEqual(m["store"]["coord"]["rows"], 1)
        self.assertEqual(m["store"]["coord"]["days"], {"2026-11-19": [1, "01a1-e1", "01a1-e1"]})
        other = ex.export_store(self.src, "other", self.out + "-other")
        self.assertEqual(other["store"]["messages"]["rows"], 2)           # its own, and the one it received
        self.assertEqual(os.stat(self.out).st_mode & 0o777, 0o700)
        for rel in m["files"]:
            self.assertEqual(os.stat(os.path.join(self.out, rel)).st_mode & 0o777, 0o600)
        text = open(os.path.join(self.out, "manifest.json")).read()
        self.assertNotIn("hello", text)                                     # no row content in the manifest
        self.assertEqual(ex.check_bundle(self.out), [])

    def test_an_import_reproduces_the_rows_and_the_check_matches_on_both_sides(self):
        ex.export_store(self.src, "acme", self.out)
        dst, _ = store()
        counts = ex.import_store(dst, self.out)
        self.assertEqual(counts["messages"], 2)
        self.assertEqual(ex.check_store(dst, self.out), [])
        self.assertEqual(ex.check_store(self.src, self.out), [])
        got = dst.execute("SELECT seq, state FROM messages ORDER BY seq").fetchall()
        self.assertEqual(got, [(1, "done"), (2, "queued")])               # sequence numbers kept: feed cursors hold
        self.assertEqual(ex.import_store(dst, self.out)["messages"], 2)   # again: replaced, not doubled
        self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)

    def test_a_changed_row_or_file_is_caught_and_nothing_is_imported(self):
        ex.export_store(self.src, "acme", self.out)
        self.src.execute("UPDATE messages SET state = 'delivered' WHERE msg_id = '01a1-0002'")
        self.assertTrue(ex.check_store(self.src, self.out))                 # the source moved on since the export
        path = os.path.join(self.out, "store", "coord.jsonl.gz")
        os.chmod(path, 0o600)
        with gzip.open(path, "wb") as fh:
            fh.write(json.dumps({"seq": 9, "org": "acme", "event_id": "x"}).encode() + b"\n")
        self.assertIn("store/coord.jsonl.gz: sha256 differs", ex.check_bundle(self.out))
        dst, _ = store()
        with self.assertRaises(ex.ExportError):
            ex.import_store(dst, self.out)
        self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_a_key_another_organization_holds_refuses_the_whole_import(self):
        ex.export_store(self.src, "acme", self.out)
        dst, _ = store()
        dst.execute("INSERT INTO coord (seq, org, event_id, name, ts, record, added) VALUES "
                    "(1, 'other', 'z', 'of.ask', '2026-11-19T09:00:00Z', '{}', 1795130346.5)")
        with self.assertRaises(ex.ExportError) as e:
            ex.import_store(dst, self.out)
        self.assertIn("another organization", str(e.exception))
        self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)   # rolled back whole

    def test_an_import_into_a_server_never_started_is_refused(self):
        ex.export_store(self.src, "acme", self.out)
        dst, _ = store(tables=False)
        with self.assertRaises(ex.ExportError) as e:
            ex.import_store(dst, self.out)
        self.assertIn("start the new server once", str(e.exception))

    @unittest.skipUnless(__import__("shutil").which("age") and __import__("shutil").which("age-keygen"), "age")
    def test_an_encrypted_bundle_holds_no_plaintext_and_imports_with_its_key_alone(self):
        """The user organization's sec_keeper (through of_architect, message 01a1f5ff-f009): encrypt to its key, with
        no plaintext temp file, and carry both hashes of each file."""
        ident, rec = age_key(tempfile.mkdtemp(prefix="ofk-"))
        m = ex.export_store(self.src, "acme", self.out, recipient=rec)
        self.assertTrue(m["encrypted"])
        self.assertEqual(sorted(os.listdir(os.path.join(self.out, "store"))),
                         sorted(f"{t}.jsonl.gz.age" for t in ex.STORE))           # nothing else ever written there
        for rel, f in m["files"].items():
            self.assertNotEqual(f["sha256"], f["plain_sha256"])
            with open(os.path.join(self.out, rel), "rb") as fh:
                data = fh.read()
            self.assertTrue(data.startswith(b"age-encryption.org/v1"))
            self.assertNotIn(b"hello", data)
        self.assertEqual(ex.check_bundle(self.out), [])                            # ciphertext hashes alone
        self.assertEqual(ex.check_bundle(self.out, ident), [])                     # and everything, decrypted
        dst, _ = store()
        with self.assertRaises(ex.ExportError):
            ex.import_store(dst, self.out)                                         # no key: nothing imported
        other, _ = age_key(tempfile.mkdtemp(prefix="ofk-"))
        with self.assertRaises(ex.ExportError):
            ex.import_store(dst, self.out, other)                                  # another key: refused
        self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
        self.assertEqual(ex.import_store(dst, self.out, ident)["messages"], 2)
        self.assertEqual(ex.check_store(dst, self.out), [])
        with self.assertRaises(ex.ExportError):
            ex.export_store(self.src, "acme", self.out + "-x", recipient="age1short")

    def test_an_export_refuses_a_folder_that_is_not_empty(self):
        os.makedirs(self.out)
        open(os.path.join(self.out, "x"), "w").close()
        with self.assertRaises(ex.ExportError):
            ex.export_store(self.src, "acme", self.out)


if __name__ == "__main__":
    unittest.main()
