"""Private keys never reach the store (a user organization, 2026-11-06: a GitHub App key printed through kubectl)."""

import base64
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_store  # noqa: E402

HEAD, TAIL = "-----BEGIN RSA " + "PRIVATE KEY-----", "-----END RSA " + "PRIVATE KEY-----"
BODY = "MIIEowIBAAKCAQEAsynthetic0123456789abcdefABCDEF+/" * 3
PEM = f"{HEAD}\n{BODY}\n{BODY}\n{TAIL}\n"


class RedactTest(unittest.TestCase):
    def check(self, record):
        line = json.dumps(record).encode()
        out = of_store.redact_keys(line)
        self.assertNotIn(BODY[:30].encode(), out)
        self.assertIn(of_store.REDACTED, out)
        json.loads(out)  # still valid JSON
        return json.loads(out)

    def test_plain_escaped_twice_and_base64(self):
        self.check({"toolUseResult": {"stdout": "key:\n" + PEM + "done"}})
        annotation = json.dumps({"data": {"private-key.pem": PEM}})  # kubectl last-applied: JSON in JSON
        self.check({"toolUseResult": {"stdout": json.dumps({"metadata": {"annotations": {"last-applied": annotation}}})}})
        b64 = base64.b64encode(PEM.encode()).decode()
        got = self.check({"toolUseResult": {"stdout": f"data:\n  private-key.pem: {b64}\nkind: Secret"}})
        self.assertIn("kind: Secret", got["toolUseResult"]["stdout"])  # the rest of the line stays
        self.check({"message": {"content": HEAD + "\n" + BODY}})  # cut off before its END

    def test_ingest_stores_the_redacted_line(self):
        with tempfile.TemporaryDirectory() as d:
            conn = of_store.connect(os.path.join(d, "of.db"))
            tr = os.path.join(d, "S.jsonl")
            with open(tr, "w") as fh:
                fh.write(json.dumps({"type": "user", "uuid": "u1", "sessionId": "S",
                                     "toolUseResult": {"stdout": PEM}}) + "\n")
            self.assertEqual(of_store.ingest_file(conn, tr), 1)
            sha = conn.execute("SELECT blob_sha256 FROM transcript_record").fetchone()[0]
            blob = of_store.get_blob(conn, sha)
            self.assertNotIn(BODY[:30].encode(), blob)
            self.assertIn(of_store.REDACTED, blob)
            conn.close()

    def test_credentials_in_dsns_and_environment_dumps(self):
        pw = "Zq8" + "x" * 30 + "Kd93Lm"
        env = f"HOME=/root\nPGPASSWORD={pw}\nOPENAI_API_KEY=sk-{pw}\nAWS_SECRET_ACCESS_KEY={pw}\nPATH=/usr/bin\nREF=$TOKEN"
        rec = {"toolUseResult": {"stdout": f"dsn=postgresql://runners_ro:{pw}@pg.internal:5432/db\n" + env}}
        out = of_store.redact_keys(json.dumps(rec).encode())
        self.assertNotIn(pw.encode(), out)
        text = json.loads(out)["toolUseResult"]["stdout"]
        self.assertIn("postgresql://runners_ro:[OF-REDACTED]@pg.internal:5432/db", text)
        self.assertIn("PGPASSWORD=[OF-REDACTED]", text)
        self.assertIn("HOME=/root", text)
        self.assertIn("PATH=/usr/bin", text)
        keep = json.dumps({"t": "postgres://u:***@h/db and postgres://u:$PGPASSWORD@h and GITHUB_TOKEN=${GH}"}).encode()
        self.assertEqual(of_store.redact_keys(keep), keep)  # masks and references stay

    def test_kubernetes_secret_objects(self):
        """A user organization: a failed go-template dumped whole Secret objects (base64 data and plain stringData)."""
        v1 = base64.b64encode(b"client-secret-" + b"q" * 24).decode()
        plain = "pl41n-" + "w" * 20
        as_json = json.dumps({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "airflow-keycloak"},
                              "data": {"client-secret": v1}, "type": "Opaque"})
        as_yaml = f"apiVersion: v1\nkind: Secret\nmetadata:\n  name: oauth2-proxy\nstringData:\n  cookie-secret: {plain}\ntype: Opaque\n"
        as_map = f"map[apiVersion:v1 data:map[client-secret:{v1}] kind:Secret metadata:map[name:solana-dashboard-secrets]]"
        for out_text in (as_json, as_yaml, as_map):
            rec = {"type": "user", "toolUseResult": {"stdout": out_text}}
            out = of_store.redact_keys(json.dumps(rec).encode())
            self.assertNotIn(v1.encode(), out)
            self.assertNotIn(plain.encode(), out)
            got = json.loads(out)["toolUseResult"]["stdout"]  # still valid JSON
            self.assertIn("Secret", got)
        self.assertIn("name: oauth2-proxy", json.loads(of_store.redact_keys(json.dumps({"t": as_yaml}).encode()))["t"])
        cm = json.dumps({"t": "kind: ConfigMap\ndata:\n  blob: " + v1}).encode()
        self.assertEqual(of_store.redact_keys(cm), cm)  # not a Secret: untouched

    def test_redact_rows_repoints_and_drops_the_old_blob(self):
        pw = "Qw7" + "y" * 30
        with tempfile.TemporaryDirectory() as d:
            conn = of_store.connect(os.path.join(d, "of.db"))
            tr = os.path.join(d, "S.jsonl")
            line = json.dumps({"type": "user", "uuid": "u1", "sessionId": "S", "toolUseResult": {"stdout": "ok"}})
            with open(tr, "w") as fh:
                fh.write(line + "\n")
            of_store.ingest_file(conn, tr)
            rid, old = conn.execute("SELECT id, blob_sha256 FROM transcript_record").fetchone()
            leaked = json.dumps({"type": "user", "uuid": "u1", "sessionId": "S",
                                 "toolUseResult": {"stdout": f"postgres://ro:{pw}@h/db"}}).encode()
            conn.execute("UPDATE blob SET zdata = ?, codec = 'raw' WHERE sha256 = ?", (leaked, old))  # stored before the rule
            changed = of_store.redact_rows(conn, [rid], "test")
            self.assertEqual([c[0] for c in changed], [rid])
            new = conn.execute("SELECT blob_sha256 FROM transcript_record WHERE id = ?", (rid,)).fetchone()[0]
            self.assertNotIn(pw.encode(), of_store.get_blob(conn, new))
            self.assertIsNone(of_store.get_blob(conn, old))  # no row refers to it: gone
            self.assertEqual(of_store.redact_rows(conn, [rid], "test"), [])  # idempotent
            conn.close()

    def test_of_redact_masks_here_and_asks_the_server(self):
        import of_events
        import of_redact
        pw = "Zz9" + "k" * 30
        with tempfile.TemporaryDirectory() as d:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = d
            try:
                conn = of_store.connect()
                tr = os.path.join(d, "S7.jsonl")
                with open(tr, "w") as fh:
                    fh.write(json.dumps({"type": "user", "uuid": "u7", "sessionId": "S7",
                                         "toolUseResult": {"stdout": "ok"}}) + "\n")
                of_store.ingest_file(conn, tr)
                rid, old = conn.execute("SELECT id, blob_sha256 FROM transcript_record").fetchone()
                leaked = json.dumps({"type": "user", "uuid": "u7", "sessionId": "S7",
                                     "toolUseResult": {"stdout": f"PGPASSWORD={pw}"}}).encode()
                conn.execute("UPDATE blob SET zdata = ?, codec = 'raw' WHERE sha256 = ?", (leaked, old))
                conn.execute("INSERT OR REPLACE INTO ship_cursor VALUES ('transcript_record', ?)", (rid,))  # shipped
                self.assertEqual(of_redact.would_change(conn, of_redact.select(conn, ["S7"])), [rid])
                sent = []

                class Server:
                    def redact(self, body):
                        sent.append(body)
                        return {"redaction_id": body["redaction_id"], "state": "queued"}
                import of_ship
                saved_consent = of_ship.raw_consent
                of_ship.raw_consent = lambda *a, **k: {"": False}  # typed-only consent: the server still holds raw
                try:
                    out = of_redact.run(conn, "test", [rid], client_for=lambda url: Server(), log=lambda m: None, wait=False)
                finally:
                    of_ship.raw_consent = saved_consent
                self.assertEqual(out["transcript_record"]["local"], 1)
                self.assertEqual(len(sent), 1)
                body = sent[0]
                self.assertEqual((body["table"], body["ids"]), ("transcript_record", [["S7", "u7"]]))
                self.assertTrue(body["records"])
                self.assertNotIn(pw, json.dumps(body))  # the replacement is masked
                d0 = body["records"][0]["data"]
                self.assertIn("[OF-REDACTED]", d0["raw"])  # the masked raw replaces the shipped one
                self.assertTrue(body["sha_new"] and d0["sha256"].startswith(body["sha_new"]))
                n = conn.execute("SELECT count(*) FROM event WHERE name = 'of.redacted'").fetchone()[0]
                self.assertEqual(n, 1)
                conn.close()
            finally:
                of_events.STATE_DIR = saved

    def file_bytes(self, path):
        """The main file and its WAL as they lie on disk, read while the store is still open: no checkpoint of the
        test's own, since a last close would checkpoint and delete the WAL (of_secrets-review on PR 248)."""
        out = b""
        for p in (path, path + "-wal"):
            if os.path.exists(p):
                with open(p, "rb") as fh:
                    out += fh.read()
        return out

    def run_local(self, conn, **ids):
        import of_events
        import of_redact
        saved = of_events.STATE_DIR
        of_events.STATE_DIR = os.path.dirname(self.path)   # of.redacted goes to the test's own store
        try:
            return of_redact.run(conn, "test", send_to_servers=False, log=lambda m: None, **ids)
        finally:
            of_events.STATE_DIR = saved

    def test_a_redacted_blob_leaves_no_bytes_in_the_file_or_its_wal(self):
        """of_server on organic-framework PR 244 (2026-11-16): a deleted row's bytes stayed on SQLite's free list, and
        the frames written before stayed in the WAL (of_secrets-review on PR 248). A redaction writes with
        secure_delete and then truncates the WAL."""
        pw = "Zk9" + "q" * 30
        with tempfile.TemporaryDirectory() as d:
            self.path = os.path.join(d, "of.db")
            conn = of_store.connect(self.path)
            tr = os.path.join(d, "S.jsonl")
            with open(tr, "w") as fh:
                fh.write(json.dumps({"type": "user", "uuid": "u1", "sessionId": "S", "toolUseResult": {"stdout": "ok"}})
                         + "\n")
            of_store.ingest_file(conn, tr)
            rid, old = conn.execute("SELECT id, blob_sha256 FROM transcript_record").fetchone()
            leaked = json.dumps({"type": "user", "uuid": "u1", "sessionId": "S",
                                 "toolUseResult": {"stdout": f"postgres://ro:{pw}@h/db"}}).encode()
            conn.execute("UPDATE blob SET zdata = ?, codec = 'raw' WHERE sha256 = ?", (leaked, old))
            self.assertIn(pw.encode(), self.file_bytes(self.path))   # in the WAL now
            default = conn.execute("PRAGMA secure_delete").fetchone()[0]   # a build's default: 1 on GitHub's Linux
            out = self.run_local(conn, transcript_ids=[rid])
            self.assertEqual((out["transcript_record"]["local"], out["_wal"]), (1, True))
            self.assertEqual(conn.execute("PRAGMA secure_delete").fetchone()[0], default)   # restored after
            self.assertNotIn(pw.encode(), self.file_bytes(self.path))
            conn.close()

    def test_a_redacted_event_leaves_its_search_index_and_no_bytes(self):
        """of_server, 2026-11-16: `event_fts` keeps its own copy of an event's text, and of redact rewrote only the
        event row, so the value stayed searchable and in the file."""
        import of_events
        key = "7a6b5c4d" * 4
        url = f"https://books-1.booksmcp.example/mcp/{key}/message"
        with tempfile.TemporaryDirectory() as d:
            self.path = os.path.join(d, "of.db")
            conn = of_store.connect(self.path)
            rec = of_events.record("human_prompt", {"origin": "human", "text": f"use {url} now"}, position="p",
                                   session="S1", org="ofb")
            of_store.add_event(conn, rec)
            rowid = conn.execute("SELECT rowid FROM event WHERE event_id = ?", (rec["of.event.id"],)).fetchone()[0]
            found = "SELECT count(*) FROM event_fts WHERE event_fts MATCH ?"
            self.assertEqual(conn.execute(found, (f'"{key}"',)).fetchone()[0], 1)
            out = self.run_local(conn, event_ids=[rowid])
            self.assertEqual((out["event"]["local"], out["_wal"]), (1, True))
            self.assertEqual(conn.execute(found, (f'"{key}"',)).fetchone()[0], 0)       # no longer searchable
            self.assertEqual(conn.execute(found, ('"now"',)).fetchone()[0], 1)          # the rest still is
            self.assertEqual(conn.execute("SELECT v FROM event_fts_config WHERE k = 'secure-delete'").fetchone()[0], 0)
            self.assertNotIn(key.encode(), self.file_bytes(self.path))
            conn.close()

    def test_fast_is_restored_as_fast(self):
        """of_review on PR 248: `PRAGMA secure_delete = 2` reads as ON, so a FAST connection was left on ON."""
        with tempfile.TemporaryDirectory() as d:
            conn = of_store.connect(os.path.join(d, "of.db"))
            for mode, want in (("FAST", 2), ("OFF", 0), ("ON", 1)):
                conn.execute(f"PRAGMA secure_delete = {mode}")
                with of_store.secure_deletes(conn):
                    self.assertEqual(conn.execute("PRAGMA secure_delete").fetchone()[0], 1)
                self.assertEqual(conn.execute("PRAGMA secure_delete").fetchone()[0], want, mode)
            conn.close()

    def test_a_rerun_with_nothing_to_change_still_truncates_the_wal(self):
        """of_secrets-review on PR 248: the advice "run of redact again later" did nothing, since a rerun found no row
        to change and returned before any truncation."""
        import contextlib
        import importlib.machinery
        import importlib.util
        import io
        import types
        from unittest import mock
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
        of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli", loader))
        loader.exec_module(of)
        with tempfile.TemporaryDirectory() as d:
            calls = []
            args = types.SimpleNamespace(reason="test", transcripts=False, targets=[], from_scan=None, resend=False,
                                         event=None, dry_run=False, local_only=True, apply=False, json=False)
            with mock.patch.dict(os.environ, {"OF_DB": os.path.join(d, "of.db")}), \
                    mock.patch.object(of_store, "truncate_wal", lambda conn, **kw: calls.append(1) or True):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(of.cmd_redact(args), 0)
                self.assertEqual(calls, [1])
                self.assertIn("of.db-wal truncated", out.getvalue())
                args.dry_run = True
                with contextlib.redirect_stdout(io.StringIO()):
                    of.cmd_redact(args)
                self.assertEqual(calls, [1])   # a dry run changes nothing

    def test_a_reader_on_an_older_snapshot_holds_the_truncation_off_briefly(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "of.db")
            conn = of_store.connect(path)
            of_store.put_blob(conn, b"one")
            reader = sqlite3.connect(path, isolation_level=None)
            reader.execute("BEGIN")
            reader.execute("SELECT count(*) FROM blob").fetchone()      # holds this snapshot
            of_store.put_blob(conn, b"two")
            self.assertFalse(of_store.truncate_wal(conn, tries=2, pause_s=0, busy_ms=20))
            reader.execute("COMMIT")
            self.assertTrue(of_store.truncate_wal(conn, tries=2, pause_s=0, busy_ms=20))
            self.assertEqual(os.path.getsize(path + "-wal"), 0)
            reader.close()
            conn.close()

    def test_of_redact_masks_an_event_here_and_asks_the_server(self):
        """A user organization's sec_keeper, 2026-11-11: a founder's prompt event held a capability URL. Its payload and
        record are masked here, and the server replaces the shipped event by its event_id."""
        import of_events
        import of_redact
        import of_ship
        key = "0f1e2d3c" * 4
        url = f"https://books-1.booksmcp.example/mcp/{key}/message"
        with tempfile.TemporaryDirectory() as d:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = d
            try:
                conn = of_store.connect()
                rec = {"of.event.id": "e1", "event.name": "of.human_prompt", "of.payload": {"text": f"use {url}"}}
                conn.execute("INSERT INTO event (event_id, ts, name, session_id, org, source, payload, record) "
                             "VALUES ('e1', '2026-09-28T16:39:47.660Z', 'of.human_prompt', 'S1', 'ofb', 'import', ?, ?)",
                             (json.dumps(rec["of.payload"]), json.dumps(rec)))
                rowid = conn.execute("SELECT rowid FROM event WHERE event_id = 'e1'").fetchone()[0]
                conn.execute("INSERT OR REPLACE INTO ship_cursor VALUES ('event', ?)", (rowid,))  # shipped
                conn.commit()
                sent = []

                class Server:
                    def redact(self, body):
                        sent.append(body)
                        return {"redaction_id": body["redaction_id"], "state": "queued"}
                saved_consent = of_ship.raw_consent
                of_ship.raw_consent = lambda *a, **k: {"": False}
                try:
                    out = of_redact.run(conn, "test", event_ids=[rowid], client_for=lambda url_: Server(),
                                        log=lambda m: None, wait=False)
                finally:
                    of_ship.raw_consent = saved_consent
                self.assertEqual(out["event"]["local"], 1)
                payload, record = conn.execute("SELECT payload, record FROM event WHERE event_id = 'e1'").fetchone()
                for text in (payload, record):
                    self.assertNotIn(key, text)
                    self.assertIn("booksmcp.example/mcp/[OF-REDACTED]/message", text)
                    json.loads(text)  # still JSON
                self.assertEqual(len(sent), 1)
                self.assertEqual((sent[0]["table"], sent[0]["ids"]), ("event", ["e1"]))
                self.assertTrue(sent[0]["records"])
                self.assertNotIn(key, json.dumps(sent[0]))
                self.assertEqual(of_redact.redact_events(conn, [rowid]), [])  # a second run changes nothing
                conn.close()
            finally:
                of_events.STATE_DIR = saved

    def test_option_a_rewrites_idle_transcripts_in_place(self):
        import time as _t
        import of_redact
        pw = "Rr5" + "m" * 30
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, "projects", "-x")
            os.makedirs(proj)
            idle, live, recent = (os.path.join(proj, f"{n}.jsonl") for n in ("IDLE", "LIVE", "RECENT"))
            for p in (idle, live, recent):
                with open(p, "w") as fh:
                    fh.write(json.dumps({"t": f"PGPASSWORD={pw}"}) + "\n" + json.dumps({"t": "ok"}) + "\n")
            old = _t.time() - 3600
            for p in (idle, live):
                os.utime(p, (old, old))
            files = of_redact.transcript_files([os.path.join(d, "projects")])
            rep = of_redact.redact_transcripts(files, live_sessions={"LIVE"}, log=lambda m: None)
            self.assertEqual((rep["changed"], rep["lines"]), (1, 1))
            with open(idle) as fh:
                self.assertIn(pw, fh.read())  # a dry run changes nothing
            ino = os.stat(idle).st_ino
            import of_events
            saved_state, of_events.STATE_DIR = of_events.STATE_DIR, d
            try:
                rep = of_redact.redact_transcripts(files, apply=True, live_sessions={"LIVE"}, log=lambda m: None)
                root = os.path.join(d, "redact-backup")
                self.assertEqual(oct(os.stat(root).st_mode & 0o777), "0o700")
                (stamp,) = os.listdir(root)
                copies = [f for f in os.listdir(os.path.join(root, stamp)) if f.endswith(".jsonl")]
                with open(os.path.join(root, stamp, copies[0])) as fh:
                    self.assertIn(pw, fh.read())  # the byte-for-byte copy, before the rewrite
                self.assertEqual(of_redact.purge_backups(), [])  # kept for 24 h
                self.assertEqual(of_redact.purge_backups(now=_t.time() + 25 * 3600), [stamp])
            finally:
                of_events.STATE_DIR = saved_state
            with open(idle) as fh:
                text = fh.read()
            self.assertNotIn(pw, text)
            self.assertEqual(os.stat(idle).st_ino, ino)  # the same file: a running harness keeps appending to it
            self.assertEqual([json.loads(x)["t"] for x in text.splitlines()], ["PGPASSWORD=[OF-REDACTED]", "ok"])
            for p in (live, recent):
                with open(p) as fh:
                    self.assertIn(pw, fh.read())  # live and just-written files are left alone
            self.assertEqual((len(rep["skipped_live"]), len(rep["skipped_recent"])), (1, 1))

    def test_rpc_provider_keys_in_url_paths(self):
        """A user organization: a QuickNode URL with its key in the path went unflagged. Only the path token goes."""
        tok = "Qn" + "x" * 38
        rec = {"t": f"RPC=https://twilight-omniscient-paper.ethereum-mainnet.quiknode.pro/{tok}/ and "
                    f"https://eth-mainnet.g.alchemy.com/v2/{tok[:24]} and https://mainnet.infura.io/v3/{'ab' * 16}"}
        out = of_store.redact_keys(json.dumps(rec).encode())
        text = json.loads(out)["t"]
        self.assertNotIn(tok, text)
        self.assertNotIn("ab" * 16, text)
        self.assertIn("twilight-omniscient-paper.ethereum-mainnet.quiknode.pro/[OF-REDACTED]/", text)
        self.assertIn("eth-mainnet.g.alchemy.com/v2/[OF-REDACTED]", text)
        plain = json.dumps({"t": "see https://docs.alchemy.com/reference and https://example.com/v2/abc"}).encode()
        self.assertEqual(of_store.redact_keys(plain), plain)

    def test_capability_tokens_in_mcp_urls(self):
        """A user organization's sec_keeper, 2026-11-11: an accounting MCP URL (<host>/mcp/<32 hex>/message) went
        unmasked, plain and percent-encoded inside another URL. Only the token goes; slugs and file paths stay."""
        hexkey, tok = "0f1e2d3c" * 4, "Ab3dEf6hIj9kLm2nOp5qRs8t"
        rec = {"t": f"MCP https://books-60012345678.booksmcp.example/mcp/{hexkey}/message and "
                    f"redirect=https%3A%2F%2Fbooks-60012345678.booksmcp.example%2Fmcp%2F{hexkey}%2Fmessage%22 and "
                    f"https://api.example.com/v1/mcp/{tok}/stream and wss://mcp.example.net/s/{tok} and "
                    f"https://mcp.example.org/sse?api_key=k{tok}&x=1 and "
                    f"bare books-1.booksmcp.example/mcp/{hexkey}/message."}
        text = json.loads(of_store.redact_keys(json.dumps(rec).encode()))["t"]
        self.assertNotIn(hexkey, text)
        self.assertNotIn(tok, text)
        self.assertIn("https://books-60012345678.booksmcp.example/mcp/[OF-REDACTED]/message", text)
        self.assertIn("%2Fmcp%2F[OF-REDACTED]%2Fmessage", text)
        self.assertIn("https://api.example.com/v1/mcp/[OF-REDACTED]/stream", text)
        self.assertIn("sse?api_key=[OF-REDACTED]&x=1", text)
        plain = json.dumps({"t": "https://mcp.directory/servers/books-mcp-server-2025-edition-x and "
                                 "p=Path('work/mcp/inbox-latest-20261023T1234-note.json') and "
                                 "https://docs.example.com/mcp/getting-started-with-servers"}).encode()
        self.assertEqual(of_store.redact_keys(plain), plain)

    def test_prefixed_tokens_anywhere(self):
        glsa = "glsa_" + "A1b2" * 8 + "_" + "0f" * 4
        rec = {"t": f"header file:\nAuthorization: Bearer {glsa}\nother ghp_{'Z' * 36} xoxb-{'1' * 12}-{'2' * 12}-{'a' * 24}"}
        out = of_store.redact_keys(json.dumps(rec).encode())
        self.assertNotIn(glsa[5:], out.decode())
        self.assertIn("glsa_[OF-REDACTED]", out.decode())
        self.assertIn("ghp_[OF-REDACTED]", out.decode())
        self.assertNotIn("a" * 24, out.decode())
        keep = json.dumps({"t": "the glsa_ prefix and ghp_ tokens are documented; xoxb-… is a placeholder"}).encode()
        self.assertEqual(of_store.redact_keys(keep), keep)
        blob = json.dumps({"t": "data:" + "Qx9-" * 8 + "hf_" + "aZ09" * 200 + " and " + "k" * 9 + "-ghp_" + "Z" * 36}).encode()
        self.assertEqual(of_store.redact_keys(blob), blob)  # inside base64url data, or longer than 255: not a token

    def test_ordinary_lines_unchanged(self):
        line = json.dumps({"text": "a public key: ssh-ed25519 AAAA... and the word PRIVATE"}).encode()
        self.assertIs(of_store.redact_keys(line), line)


if __name__ == "__main__":
    unittest.main()
