"""of-server end to end: real HTTP, real ssh-keygen signatures, a fake ClickHouse."""

import json
import os
import sys
import sqlite3
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_client  # noqa: E402
import of_events  # noqa: E402
import of_route  # noqa: E402
import of_server  # noqa: E402

HOME = os.path.expanduser("~")


class FakeCH:
    def __init__(self):
        self.inserts = []
        self.fail = False

    def insert(self, database, table, rows):
        if self.fail:
            raise RuntimeError("ClickHouse down")
        self.inserts.append((database, table, rows))


def event(eid, org, ts="2026-11-05T10:00:00.000Z", **kw):
    rec = {"of.schema": 1, "of.event.id": eid, "timestamp": ts, "event.name": "of.turn_end", "of.org": org}
    d = {"event_id": eid, "org": org, "ts": ts, "name": "of.turn_end", "session_id": "S", "position": "p",
         "source": "hook", "payload": "{}", "raw_ref": "", "record": json.dumps(rec)}
    d.update(kw)
    return {"type": "event", "data": d}


def transcript(uuid, project, raw='{"x": 1}'):
    return {"type": "transcript_record", "data": {
        "session_id": "T", "record_uuid": uuid, "ts": "2026-11-05T10:00:00Z", "line_offset": 0, "sha256": "a" * 64,
        "source_path": f"{HOME}/.claude-x/projects/{project}/T.jsonl", "raw": raw, "tool_names": ""}}


class ServerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.saved_state = of_events.STATE_DIR
        of_events.STATE_DIR = os.path.join(root, "state")
        orgs = os.path.join(root, "orgs.yaml")
        with open(orgs, "w") as fh:
            fh.write(f"orgs:\n  - {{name: mt, database: db_mt, paths: ['{HOME}/acme']}}\n"
                     f"  - {{name: ofb, database: db_ofb, paths: ['{HOME}/organic-framework']}}\n")
        self.ch = FakeCH()
        self.ship_cfg = {"unassigned_database": "db_un", "vault_database": "db_vault", "raw_to_org": False,
                         "tenant": "t"}

        def router():
            r = of_route.Router(self.ship_cfg, orgs)
            r.org_by_session, r.own_org_by_session = {}, {}
            return r
        cfg = dict(of_server.DEFAULTS, state_dir=os.path.join(root, "server"), port=0, flush_every=3600)
        import of_grants
        self.gkey = of_grants.purpose_key("of/grant/v1", master=b"test")
        g = {"id": "g1", "subject": "of_a", "issuer": "tenant", "tier": "propose", "scope": {"repos": ["~/x"]}}
        g["signature"] = of_grants.sign(g, self.gkey)
        self.app = of_server.App(cfg, ch=self.ch, router_factory=router, ship_cfg=self.ship_cfg,
                                 grants_loader=lambda: {"ofb": [g], "mt": []}, grant_key=lambda: self.gkey,
                                 mask_key=b"m" * 32)
        self.httpd = self.app.serve(host="127.0.0.1", port=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.key = os.path.join(root, "key")
        pub = of_client.ensure_key(self.key)
        of_server.enroll(self.app.store, of_events.machine_id(), pub, ["ofb"], [])
        self.client = of_client.Client(self.url, key_path=self.key, state_dir=of_events.STATE_DIR)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.writer.stop.set()
        of_events.STATE_DIR = self.saved_state
        self.tmp.cleanup()


class ServerTest(ServerBase):
    def test_batch_accept_route_reject_idempotent_commit(self):
        ofb_project = "".join(c if c.isalnum() else "-" for c in os.path.realpath(f"{HOME}/organic-framework"))
        acme_project = "".join(c if c.isalnum() else "-" for c in os.path.realpath(f"{HOME}/acme"))
        records = [event("e1", "ofb"),
                   {"type": "event", "data": {"event_id": "e2", "name": "x", "record": "{}"}},   # no ts
                   transcript("u1", acme_project + "-storefront"),                                 # not joined
                   transcript("u2", ofb_project, raw='{"secret": "content"}'),
                   {"type": "nonsense", "data": {}}]
        r = self.client.send_batch(records, batch_id="b1")
        self.assertEqual((r["state"], r["accepted"]), ("queued", 2))
        reasons = {x["index"]: x["reason"] for x in r["rejected"]}
        self.assertIn("missing ts", reasons[1])
        self.assertIn("mt not joined", reasons[2])
        self.assertIn("known type", reasons[4])
        self.assertEqual(self.client.send_batch(records, batch_id="b1"), r)   # idempotent retry
        with self.assertRaises(of_client.ServerError) as cm:
            self.client.send_batch(records[:1], batch_id="b1")                  # same id, other body
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(self.app.writer.drain(), 1)
        dbs = {(d, t): rows for d, t, rows in self.ch.inserts}
        self.assertEqual(set(dbs), {("db_ofb", "event"), ("db_ofb", "transcript_record")})
        self.assertEqual(dbs[("db_ofb", "transcript_record")][0]["raw"], "")    # no raw consent, no vault
        rc = self.client.receipts()["receipts"]
        self.assertEqual((rc[0]["batch_id"], rc[0]["state"], rc[0]["by_org"]), ("b1", "committed", {"ofb": 2}))

    def test_a_resent_record_is_not_written_again(self):
        r = self.client.send_batch([event("e1", "ofb"), event("e2", "ofb")], batch_id="r1")
        self.assertEqual((r["accepted"], r["duplicates"]), (2, 0))
        r = self.client.send_batch([event("e1", "ofb"), event("e3", "ofb"), event("e3", "ofb")], batch_id="r2")
        self.assertEqual((r["accepted"], r["duplicates"]), (1, 2))       # e1 seen before, e3 twice in one batch
        r = self.client.send_batch([event("e1", "ofb")], batch_id="r3")
        self.assertEqual((r["state"], r["accepted"], r["duplicates"]), ("committed", 0, 1))
        self.app.writer.drain()
        self.assertEqual(sorted(x["event_id"] for _, _, rows in self.ch.inserts for x in rows), ["e1", "e2", "e3"])
        of_server.enroll(self.app.store, of_events.machine_id(), of_client.ensure_key(self.key), ["ofb"], [], tenant=True)
        body = json.dumps({"batch_id": "r4", "schema": 1, "replace": True, "records": [event("e1", "ofb")]}).encode()
        _, rc = self.client._request("POST", "/v1/batches", raw=body, token=self.client.token())
        self.assertEqual((rc["accepted"], rc["duplicates"]), (1, 0))       # the tenant may re-send on purpose

    def test_writer_retries_in_order_when_clickhouse_is_down(self):
        self.client.send_batch([event("e1", "ofb")], batch_id="b1")
        self.client.send_batch([event("e2", "ofb")], batch_id="b2")
        h = of_server.health(self.app.store, self.app.writer)
        self.assertGreater(h["queue_bytes"], 0)
        self.assertIsNone(h["drain_eta_s"])                                 # no rate measured yet
        self.ch.fail = True
        self.assertEqual(self.app.writer.drain(), 0)
        self.assertEqual([x["state"] for x in self.client.receipts()["receipts"]], ["queued", "queued"])
        self.ch.fail = False
        self.assertEqual(self.app.writer.drain(), 2)
        self.assertEqual([r[0]["event_id"] for _, _, r in self.ch.inserts], ["e1", "e2"])
        self.assertGreater(self.app.writer.rate_bps, 0)
        self.client.send_batch([event("e3", "ofb")], batch_id="b3")
        self.assertGreaterEqual(of_server.health(self.app.store, self.app.writer)["drain_eta_s"], 0)

    def test_dead_batch_alerts_and_shows_in_health(self):
        alerts = []
        old = of_server.alert_dead
        of_server.alert_dead = lambda bid, n, e: alerts.append(bid)
        try:
            self.app.cfg["max_attempts"] = 2
            self.client.send_batch([event("e1", "ofb")], batch_id="bd")
            self.ch.fail = True
            self.app.writer.drain()
            self.app.writer.drain()
        finally:
            of_server.alert_dead = old
        self.assertEqual(alerts, ["bd"])
        h = of_server.health(self.app.store, self.app.writer)
        self.assertEqual((h["ok"], h["dead"]), (False, 1))
        self.assertEqual((h["queue_bytes"], h["drain_eta_s"]), (0, 0))
        self.assertTrue(os.path.exists(self.app.buffer.path("bd", dead=True)))  # kept for replay
        self.ch.fail = False
        self.assertEqual(of_server.replay_dead(self.app.store, self.app.buffer), ["bd"])
        self.assertEqual(self.app.writer.drain(), 1)
        self.assertEqual(self.client.receipts()["receipts"][0]["state"], "committed")
        self.assertEqual(of_server.replay_dead(self.app.store, self.app.buffer), [])

    def test_tenant_machine_keeps_raw_in_the_vault(self):
        of_server.enroll(self.app.store, of_events.machine_id(), of_client.ensure_key(self.key), ["ofb"], ["ofb"], tenant=True)
        ofb_project = "".join(c if c.isalnum() else "-" for c in os.path.realpath(f"{HOME}/organic-framework"))
        self.client.send_batch([transcript("u9", ofb_project, raw="RAW"), event("e9", None)], batch_id="b9")
        self.app.writer.drain()
        dbs = {(d, t): rows for d, t, rows in self.ch.inserts}
        self.assertEqual(dbs[("db_vault", "transcript_record")][0]["raw"], "RAW")
        self.assertEqual(dbs[("db_ofb", "transcript_record")][0]["raw"], "")
        self.assertIn(("db_un", "event"), dbs)  # tenant may ship unassigned records

    def test_masking_at_the_boundary(self):
        import of_mask
        of_server.enroll(self.app.store, of_events.machine_id(), of_client.ensure_key(self.key), ["ofb"], ["ofb"], tenant=True)
        self.app.ship_cfg["raw_to_org"] = True          # the gate opened, as after lab-secrets-v0
        key = "ghp_" + "Zq3vX9kLm2Pw7RtY4nB8cD1fGh6Jk5Ls0QaE"
        ofb_project = "".join(c if c.isalnum() else "-" for c in os.path.realpath(f"{HOME}/organic-framework"))
        raw = json.dumps({"cwd": "/x", "message": f"use GITHUB_TOKEN={key} please"})
        ev = event("e5", "ofb", payload=json.dumps({"text": f"token {key}"}))
        recs = [transcript("u5", ofb_project, raw=raw), transcript("u6", ofb_project, raw=raw), ev]
        self.client.send_batch(recs[:2], batch_id="m1")
        self.client.send_batch(recs[2:], batch_id="m2")
        self.assertEqual(self.app.writer.drain(), 2)
        dbs = {}
        for d, t, rows in self.ch.inserts:
            dbs.setdefault((d, t), []).extend(rows)
        self.assertEqual(dbs[("db_vault", "transcript_record")][0]["raw"], raw)          # the vault keeps it whole
        for (d, t), rows in dbs.items():
            if d != "db_vault":
                self.assertNotIn(key, json.dumps(rows), (d, t))                           # nothing else sees it
        org_raw = dbs[("db_ofb", "transcript_record")][0]["raw"]
        tag = of_mask.TAG_RX.search(org_raw).group(0)
        self.assertIn(tag, dbs[("db_ofb", "event")][0]["payload"])                        # one secret, one tag
        found = [r for r in dbs[("db_vault", "event")] if r["name"] == "of.secret_detected"]
        self.assertEqual({r["event_id"] for r in found}, {found[0]["event_id"]})          # one finding per secret
        last = json.loads(found[-1]["payload"])
        self.assertEqual((last["kind"], last["count"], last["batches"], last["org"]), (tag.split(":")[1], 3, 2, "ofb"))
        self.assertNotIn(key, json.dumps(found))
        self.assertFalse(any(r["name"] == "of.secret_detected" for r in dbs[("db_ofb", "event")]))
        n = len(self.ch.inserts)                                                          # a retried batch adds nothing
        with self.app.store.conn() as c:
            c.execute("UPDATE batches SET state = 'queued' WHERE batch_id = 'm1'")
        self.app.buffer.put("m1", self.app.buffer.get("m1") if os.path.exists(self.app.buffer.path("m1")) else
                            {"batch_id": "m1", "records": [{"type": r["type"], "data": dict(r["data"], org_id="ofb")}
                                                           for r in recs[:2]]})
        self.app.writer.drain()
        again = [r for _, t, rows in self.ch.inserts[n:] for r in rows if r.get("name") == "of.secret_detected"]
        self.assertEqual(json.loads(again[-1]["payload"])["count"], 3)

    def test_otlp_logs(self):
        import urllib.request
        of_server.enroll(self.app.store, of_events.machine_id(), of_client.ensure_key(self.key), ["ofb"], ["ofb"],
                         tenant=True)
        kv = lambda k, v: {"key": k, "value": {"stringValue": v}}  # noqa: E731
        body = {"resourceLogs": [{"resource": {"attributes": [kv("service.name", "codex"), kv("of.org", "ofb")]},
                                  "scopeLogs": [{"scope": {"name": "codex_otel"}, "logRecords": [
            {"timeUnixNano": "1793880000000000000", "eventName": "codex.tool_decision",
             "attributes": [kv("gen_ai.request.model", "gpt-x"), kv("conversation.id", "C1"),
                            kv("note", "token " + "ghp_" + "Zq3vX9kLm2Pw7RtY4nB8cD1fGh6Jk5Ls0QaE")]},
            {"timeUnixNano": "1793880000500000000", "severityText": "INFO", "body": {"stringValue": "plain log"}}]}]}]}
        raw = json.dumps(body).encode()

        def post(data, ctype="application/json"):
            req = urllib.request.Request(self.url + "/v1/otlp/v1/logs", data=data, method="POST",
                                         headers={"Authorization": "Bearer " + self.client.token(),
                                                  "Content-Type": ctype})
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        self.assertEqual(post(raw), (200, {}))
        self.assertEqual(post(raw), (200, {}))                             # same body: the same batch, nothing new
        with self.assertRaises(Exception) as cm:
            post(b"\x0a\x00", "application/x-protobuf")
        self.assertEqual(cm.exception.code, 415)
        self.app.writer.drain()
        rows = {(d, t): r for d, t, r in self.ch.inserts}
        ev = rows[("db_ofb", "event")][0]
        self.assertEqual((ev["name"], ev["session_id"], ev["model"], ev["source"]),
                         ("codex.tool_decision", "C1", "gpt-x", "otlp"))
        self.assertNotIn("ghp_Zq3v", ev["payload"])                          # masked like any other event
        self.assertEqual(rows[("db_ofb", "runtime_record")][0]["runtime"], "otlp")
        self.assertEqual(rows[("db_vault", "runtime_record")][0]["runtime"], "otlp")

    def test_no_descriptor_leak_under_concurrent_requests(self):
        import concurrent.futures
        import urllib.request

        def health(_):
            with urllib.request.urlopen(self.url + "/v1/health", timeout=10) as r:
                return r.status

        def fds():
            return len(os.listdir("/dev/fd"))
        with concurrent.futures.ThreadPoolExecutor(32) as ex:
            list(ex.map(health, range(64)))                      # warm up
            before = fds()
            self.assertEqual(set(ex.map(health, range(400))), {200})
        time.sleep(0.2)
        self.assertLess(fds() - before, 20)

    def test_grants_verify(self):
        r = self.client.verify_grant("g1", "ofb:of_a", {"repos": "~/x"})
        self.assertEqual((r["valid"], r["decision"]), (True, "admit"))
        self.assertFalse(self.client.verify_grant("g1", "ofb:of_b")["valid"])
        with self.assertRaises(of_client.ServerError) as cm:   # an organization the machine has not joined
            self.client.verify_grant("g1", "mt:of_a")
        self.assertEqual(cm.exception.status, 403)

    def test_auth_failures(self):
        import urllib.request
        req = urllib.request.Request(self.url + "/v1/batches", data=b"{}", method="POST",
                                     headers={"Authorization": "Bearer nope"})
        with self.assertRaises(Exception) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 401)
        other = os.path.join(self.tmp.name, "other")
        of_client.ensure_key(other)                       # a key the server does not know
        with self.assertRaises(of_client.ServerError) as cm:
            of_client.Client(self.url, key_path=other, state_dir=os.path.join(self.tmp.name, "o")).token()
        self.assertEqual(cm.exception.status, 401)
        of_server.revoke(self.app.store, of_events.machine_id())
        with self.assertRaises(of_client.ServerError):
            self.client.send_batch([event("e1", "ofb")])


if __name__ == "__main__":
    unittest.main()


class EnrollTest(ServerBase):
    """Remote enrollment: invite → enroll with consent → consent changes → revocation by person."""

    def colleague(self, name):
        d = os.path.join(self.tmp.name, name)
        os.makedirs(d)
        c = of_client.Client(self.url, key_path=os.path.join(d, "key"), state_dir=d)
        c.machine_id = f"m-{name}"
        return c

    def drained_events(self, name):
        self.app.writer.drain()
        return [json.loads(r["payload"]) for _, t, rows in self.ch.inserts if t == "event" for r in rows
                if r["name"] == name]

    def test_invite_enroll_consent_revoke(self):
        of_server.enroll(self.app.store, of_events.machine_id(), of_client.ensure_key(self.key), ["ofb"], ["ofb"],
                         tenant=True)
        terms = self.client.terms()["current"]
        inv = self.client._authed("POST", "/v1/invites", body={"orgs": ["ofb"], "raw_allowed": [],
                                                                "person": "alice"})[1]
        self.assertRegex(inv["expires_at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d UTC$")
        bob = self.colleague("bob")
        with self.assertRaises(of_client.ServerError) as cm:                  # raw is not offered
            bob.enroll(inv["code"], {"ofb": {"tiers": ["typed", "raw"], "terms": terms}})
        self.assertEqual(cm.exception.status, 403)
        r = bob.enroll(inv["code"], {"ofb": {"tiers": ["typed"], "terms": terms}})
        self.assertEqual((r["orgs"], r["raw_orgs"], r["person"]), (["ofb"], [], "alice"))
        with self.assertRaises(of_client.ServerError) as cm:                  # single use
            self.colleague("carol").enroll(inv["code"], {"ofb": {"tiers": ["typed"], "terms": terms}})
        self.assertEqual(cm.exception.status, 410)
        ofb_project = "".join(c if c.isalnum() else "-" for c in os.path.realpath(f"{HOME}/organic-framework"))
        rc = bob.send_batch([transcript("u1", ofb_project, raw='{"x": 1}')], batch_id="bb1")
        self.assertEqual(rc["accepted"], 1)
        self.assertEqual(bob.receipts()["receipts"][0]["tiers"], {"ofb": ["typed"]})
        with self.assertRaises(of_client.ServerError) as cm:                  # cannot widen beyond the invite
            bob.set_consent({"ofb": {"tiers": ["typed", "raw"], "terms": terms}})
        self.assertEqual(cm.exception.status, 403)
        consents = self.drained_events("of.consent")
        self.assertEqual([(c["machine_id"], c["tiers"], c["via"]) for c in consents], [("m-bob", ["typed"], "enroll")])
        self.assertEqual(self.drained_events("of.machine_enrolled")[0]["person"], "alice")
        # a second machine of the same person, then one command withdraws the person
        inv2 = self.client._authed("POST", "/v1/invites", body={"orgs": ["ofb"], "person": "alice"})[1]
        dan = self.colleague("dan")
        dan.enroll(inv2["code"], {"ofb": {"tiers": ["typed"], "terms": terms}})
        out = self.client._authed("DELETE", "/v1/machines?person=alice")[1]
        self.assertEqual(sorted(out["revoked"]), ["m-bob", "m-dan"])
        revoked = self.drained_events("of.machine_revoked")
        self.assertEqual(sorted(e["machine_id"] for e in revoked), ["m-bob", "m-dan"])
        with self.assertRaises(of_client.ServerError):
            bob.send_batch([event("e9", "ofb")])

    def test_narrowing_consent_records_effect_on_shipped(self):
        of_server.enroll(self.app.store, of_events.machine_id(), of_client.ensure_key(self.key), ["ofb"], ["ofb"],
                         tenant=True)
        terms = self.client.terms()["current"]
        inv = self.client._authed("POST", "/v1/invites", body={"orgs": ["ofb"], "raw_allowed": ["ofb"]})[1]
        eve = self.colleague("eve")
        self.assertEqual(eve.enroll(inv["code"], {"ofb": {"tiers": ["typed", "raw"], "terms": terms}})["raw_orgs"], ["ofb"])
        self.assertEqual(eve.set_consent({"ofb": {"tiers": ["typed"], "terms": terms}})["ofb"]["tiers"], ["typed"])
        last = self.drained_events("of.consent")[-1]
        self.assertEqual((last["via"], last["withdrawn"], last["effect_on_shipped"]), ("update", ["raw"], "remain"))

    def test_bad_codes_are_rate_limited(self):
        x = self.colleague("x")
        for _ in range(10):
            with self.assertRaises(of_client.ServerError) as cm:
                x.enroll("NOPE", {"ofb": {"tiers": ["typed"], "terms": "2026-11-05"}})
            self.assertEqual(cm.exception.status, 404)
        with self.assertRaises(of_client.ServerError) as cm:
            x.enroll("NOPE", {"ofb": {"tiers": ["typed"], "terms": "2026-11-05"}})
        self.assertEqual(cm.exception.status, 429)

    def test_only_the_tenant_invites(self):
        with self.assertRaises(of_client.ServerError) as cm:
            self.client._authed("POST", "/v1/invites", body={"orgs": ["ofb"]})
        self.assertEqual(cm.exception.status, 403)


class StudioMountTest(ServerBase):
    """/studio/: a one-time link, a session cookie, security headers, of_studio.handle() behind it."""

    def test_link_session_and_headers(self):
        import http.client
        import of_studio
        seen = []
        orig = of_studio.handle
        of_studio.handle = lambda method, path, query=None, viewer=None, data=None: (
            seen.append((method, path, viewer)) or ((200, "text/css", b"x") if path.endswith(".css")
                                                    else (200, "text/html; charset=utf-8", b"<p>studio</p>")))
        try:
            with self.assertRaises(of_client.ServerError) as cm:          # only the tenant mints links
                self.client._authed("POST", "/v1/studio/link")
            self.assertEqual(cm.exception.status, 403)
            of_server.enroll(self.app.store, of_events.machine_id(), of_client.ensure_key(self.key), ["ofb"], [],
                             tenant=True)
            url = self.client._authed("POST", "/v1/studio/link")[1]["url"]
            self.assertIn("/studio/open?t=", url)
            path = url.split(self.url, 1)[1]
            conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1])

            def get(p, cookie=None):
                conn.request("GET", p, headers={"Cookie": cookie} if cookie else {})
                r = conn.getresponse()
                return r, r.read()
            r, _ = get("/studio/")
            self.assertEqual(r.status, 401)                                # no cookie
            r, _ = get(path)
            self.assertEqual((r.status, r.getheader("Location")), (303, "/studio/"))
            cookie = r.getheader("Set-Cookie")
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Strict", cookie)
            r, _ = get(path)
            self.assertEqual(r.status, 401)                                # single use
            r, body = get("/studio/flow?x=1", cookie.split(";")[0])
            self.assertEqual((r.status, body), (200, b"<p>studio</p>"))
            self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy"))
            self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
            self.assertEqual(seen[-1][:2], ("GET", "/studio/flow"))
            self.assertTrue(seen[-1][2]["tenant"])
            emitted = []
            orig_emit = of_events.emit
            of_events.emit = lambda kind, payload, **kw: emitted.append((kind, payload, kw.get("org")))
            try:
                get("/studio/owner?org=ofb&secret=x", cookie.split(";")[0])
                get("/studio/static/studio.css", cookie.split(";")[0])     # not a page view
                get("/studio/", "of_studio=forged")                          # 401: not a view
            finally:
                of_events.emit = orig_emit
            self.assertEqual(len(emitted), 1)
            kind, payload, org = emitted[0]
            self.assertEqual((kind, payload["view"], payload["org"], org, payload["tenant"]),
                             ("studio_viewed", "owner", "ofb", "ofb", True))
            self.assertNotIn("secret", json.dumps(payload))
            r, _ = get("/studio/flow", "of_studio=forged")
            self.assertEqual(r.status, 401)
            nxt = "/studio/works?org=acme&w=acme-k8s"                  # the popup's Studio key
            url = self.client._authed("POST", "/v1/studio/link", body={"next": nxt})[1]["url"]
            r, _ = get(url.split(self.url, 1)[1])
            self.assertEqual((r.status, r.getheader("Location")), (303, nxt))
            url = self.client._authed("POST", "/v1/studio/link", body={"next": "https://evil.example/"})[1]["url"]
            r, _ = get(url.split(self.url, 1)[1])
            self.assertEqual((r.status, r.getheader("Location")), (303, "/studio/"))
            self.assertEqual([of_server.studio_requester(v) for v in
                              (None, "founder", "agent:of_platform", "agent:session:5f2e-ab", "agent:a b", "agent:" + "x" * 80)],
                             [None, None, "agent:of_platform", "agent:session:5f2e-ab", "agent:invalid", "agent:invalid"])
            url = self.client._authed("POST", "/v1/studio/link", body={"requester": "agent:of_platform"})[1]["url"]
            r, _ = get(url.split(self.url, 1)[1])
            emitted = []
            of_events.emit = lambda kind, payload, **kw: emitted.append(payload)
            try:
                get("/studio/works?org=ofb", r.getheader("Set-Cookie").split(";")[0])
                get("/studio/owner?org=ofb", cookie.split(";")[0])               # the owner's own session
            finally:
                of_events.emit = orig_emit
            self.assertEqual([(p["view"], p["requester"]) for p in emitted], [("works", "agent:of_platform"),
                                                                               ("owner", None)])
        finally:
            of_studio.handle = orig

    def test_next_is_a_path_under_studio_only(self):
        ok = ["/studio/works?org=acme&w=acme-k8s", "/studio/review", "/studio/"]
        bad = [None, 7, "", "/studio", "https://x/studio/", "//evil/studio/", "/studio//evil", "/studio/\\evil",
               "/studio/../v1/health", "/studio/%2e%2e/v1/health", "/studio/%2F%2Fevil", "/studio/a\r\nSet-Cookie: x",
               "/studio/open?t=x", "/v1/studio/link", "/studio/" + "a" * 2000]
        self.assertEqual([of_server.studio_next(p) for p in ok], ok)
        self.assertEqual({of_server.studio_next(p) for p in bad}, {"/studio/"})


class PerOrgServerTest(unittest.TestCase):
    """A per-organization server (server.yaml `orgs`): the client routes, the server checks again."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.saved_state = of_events.STATE_DIR
        of_events.STATE_DIR = os.path.join(root, "state")
        self.ch = FakeCH()
        ship_cfg = {"unassigned_database": "db_un", "vault_database": "db_vault", "raw_to_org": False, "tenant": "t"}
        cfg = dict(of_server.DEFAULTS, state_dir=os.path.join(root, "server"), port=0, flush_every=3600,
                   orgs={"mt": "db_mt"})
        self.app = of_server.App(cfg, ch=self.ch, ship_cfg=ship_cfg, grants_loader=lambda: {},
                                 grant_key=lambda: b"k", mask_key=b"m" * 32)
        self.httpd = self.app.serve(host="127.0.0.1", port=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        key = os.path.join(root, "key")
        of_server.enroll(self.app.store, of_events.machine_id(), of_client.ensure_key(key), ["mt", "ofb"], [])
        self.client = of_client.Client(f"http://127.0.0.1:{self.httpd.server_address[1]}", key_path=key,
                                       state_dir=of_events.STATE_DIR)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.writer.stop.set()
        of_events.STATE_DIR = self.saved_state
        self.tmp.cleanup()

    def test_client_routed_records_and_copy_allow_list(self):
        shape_rec = {"of.schema": 1, "of.event.id": "s1", "timestamp": "2026-11-05T10:00:00.000Z",
                     "event.name": "of.turn_end", "of.org": "mt", "of.serves": "ofb", "of.shape": True,
                     "of.payload": {"chars": 10}}
        good_shape = event("s1", None, org_id="mt", record=json.dumps(shape_rec), payload=json.dumps({"chars": 10}))
        bad_shape = event("s2", None, org_id="mt", record=json.dumps(dict(shape_rec, **{"of.event.id": "s2"})),
                          payload=json.dumps({"text": "the founder's words"}))
        pub = {"from_org": "ofb", "to_org": "mt", "artifact": "a", "note": "n", "trace": "t1"}
        bad_pub = event("p1", None, org_id="mt", name="of.published", payload=json.dumps(pub))
        recs = [event("e1", None, org_id="mt"),                       # routed by the client: kept
                event("e2", None),                                    # no org_id: rejected
                event("e3", None, org_id="ofb"),                      # joined, but not served here
                good_shape, bad_shape, bad_pub,
                dict(transcript("u1", "x"), data=dict(transcript("u1", "x")["data"], org_id="mt", shape=True))]
        r = self.client.send_batch(recs, batch_id="b1")
        reasons = {x["index"]: x["reason"] for x in r["rejected"]}
        self.assertEqual(r["accepted"], 2, reasons)
        self.assertIn("missing not served", reasons[1])
        self.assertIn("ofb not served", reasons[2])
        self.assertIn("payload outside", reasons[4])
        self.assertIn("publication copy", reasons[5])
        self.assertIn("shape copy: carries content", reasons[6])
        self.app.writer.drain()
        self.assertEqual({(d, t) for d, t, _ in self.ch.inserts}, {("db_mt", "event")})   # no copies made here
        self.assertEqual(sorted(r["event_id"] for _, _, rows in self.ch.inserts for r in rows), ["e1", "s1"])


class ProfileOneTest(unittest.TestCase):
    """Profile 1 default: SQLite storage, no ClickHouse at all."""

    def test_sqlite_storage_end_to_end(self):
        import of_plugins
        with tempfile.TemporaryDirectory() as root:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = os.path.join(root, "state")
            try:
                orgs = os.path.join(root, "orgs.yaml")
                with open(orgs, "w") as fh:
                    fh.write("orgs:\n  - {name: ofb, database: db_ofb}\n")
                ship_cfg = {"unassigned_database": "db_un", "vault_database": "db_vault", "raw_to_org": False, "tenant": "t"}

                def router():
                    r = of_route.Router(ship_cfg, orgs)
                    r.org_by_session, r.own_org_by_session = {}, {}
                    return r
                cfg = dict(of_server.DEFAULTS, state_dir=os.path.join(root, "server"), port=0, flush_every=3600)
                app = of_server.App(cfg, router_factory=router, ship_cfg=ship_cfg,
                                    grants_loader=lambda: {}, grant_key=lambda: b"k", mask_key=b"m" * 32)
                self.assertEqual(app.plugins["storage"], "sqlite")
                self.assertIsInstance(app.storage, of_plugins.SQLiteStorage)
                httpd = app.serve(host="127.0.0.1", port=0)
                threading.Thread(target=httpd.serve_forever, daemon=True).start()
                try:
                    key = os.path.join(root, "key")
                    of_server.enroll(app.store, of_events.machine_id(), of_client.ensure_key(key), ["ofb"], [])
                    c = of_client.Client(f"http://127.0.0.1:{httpd.server_address[1]}", key_path=key,
                                         state_dir=of_events.STATE_DIR)
                    c.send_batch([event("e1", "ofb"), event("e1", "ofb")], batch_id="p1")   # same key twice
                    app.writer.drain()
                    self.assertEqual(app.storage.count("db_ofb", "event"), 1)              # replaced, not duplicated
                    self.assertEqual(c.receipts()["receipts"][0]["state"], "committed")
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    app.writer.stop.set()
            finally:
                of_events.STATE_DIR = saved



class HooksInstallTest(unittest.TestCase):
    def test_additive_idempotent_and_reversible(self):
        import of_init
        original = {"theme": "dark", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "/guard.sh", "timeout": 10}]}]}}
        with_ = of_init.with_hooks(original, "/x/of")
        self.assertEqual(with_["theme"], "dark")
        self.assertEqual(with_["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "/guard.sh")  # untouched
        self.assertEqual(len(with_["hooks"]), 12)
        self.assertEqual(of_init.with_hooks(with_, "/x/of"), with_)                             # idempotent
        self.assertEqual(of_init.without_hooks(with_, "/x/of"), original)                       # reversible
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "settings.json"), "w") as fh:
                json.dump(original, fh)
            lines = []
            self.assertEqual(of_init.hooks("install", d, yes=True, of_path="/x/of", out=lines.append), 0)
            self.assertTrue(any(n.startswith("settings.json.bak-of-") for n in os.listdir(d)))
            self.assertIn("nothing to change", (lambda o: (of_init.hooks("install", d, yes=True, of_path="/x/of",
                                                                          out=o.append), o)[1])([])[0])


class DecompressTest(unittest.TestCase):
    def test_bombs_are_bounded(self):
        import gzip
        self.assertEqual(of_server.decompress(gzip.compress(b"x" * 100), "gzip", 1000), b"x" * 100)
        with self.assertRaises(ValueError):
            of_server.decompress(gzip.compress(b"\0" * 5_000_000), "gzip", 100_000)
        if of_server.zstd:
            with self.assertRaises(ValueError):
                of_server.decompress(of_server.zstd.compress(b"\0" * 5_000_000), "zstd", 100_000)
        with self.assertRaises(ValueError):
            of_server.decompress(b"x", "br", 10)


class StudioDataTest(unittest.TestCase):
    """Studio's ClickHouse reader: the read user, the views, values as query parameters."""

    def test_query_shape_and_auth(self):
        import base64
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from urllib.parse import parse_qs, urlparse
        seen = {}

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                seen["q"] = parse_qs(urlparse(self.path).query)
                seen["auth"] = self.headers.get("Authorization")
                body = b'{"event_id":"e1","name":"of.turn_end"}\n{"event_id":"e2","name":"of.human_prompt"}\n'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "db_mt_read.password"), "w") as fh:
                fh.write("pw\n")
            cfg = {"clickhouse": {"url": f"http://127.0.0.1:{srv.server_address[1]}", "user": "db_mt_ingest",
                                  "password_file": os.path.join(d, "db_mt_ingest.password")}}
            sd = of_server.StudioData(cfg, {"mt": "db_mt"})
            rows = list(sd.events("mt", since_iso="2026-10-27T00:00:00Z", names=["of.turn_end", "x' OR 1=1 --"]))
            with self.assertRaises(KeyError):
                list(sd.events("other"))
        srv.shutdown()
        srv.server_close()
        self.assertEqual([r["event_id"] for r in rows], ["e1", "e2"])
        q = seen["q"]["query"][0]
        self.assertIn("FROM db_mt.v_event FINAL", q)
        self.assertNotIn("OR 1=1", q)                                   # values never spliced into SQL
        self.assertEqual(seen["q"]["param_names"][0], "['of.turn_end','x\\' OR 1=1 --']")
        self.assertEqual(seen["q"]["param_since"][0], "2026-10-27T00:00:00Z")
        self.assertEqual(base64.b64decode(seen["auth"].split()[1]).decode(), "db_mt_read:pw")


class ExternalWriterTest(unittest.TestCase):
    """The writer as a child process reports through its heartbeat file."""

    def test_heartbeat_drives_health(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = dict(of_server.DEFAULTS, state_dir=d)
            ext = of_server.ExternalWriter(cfg)
            self.assertIn("not reported", ext.last_error)
            w = of_server.Writer(None, None, None, None, cfg, {}, None)
            w.heartbeat()
            self.assertIsNone(ext.last_error)
            w.last_error = "RuntimeError: ClickHouse down"
            w.heartbeat()
            self.assertEqual(ext.last_error, "RuntimeError: ClickHouse down")
            with open(os.path.join(d, "writer.json")) as fh:
                hb = json.load(fh)
            hb["ts"] -= 120
            with open(os.path.join(d, "writer.json"), "w") as fh:
                json.dump(hb, fh)
            self.assertIn("silent", ext.last_error)


class WriterSafetyTest(ServerBase):
    def test_an_orphaned_writer_stops_and_never_buries_a_committed_batch(self):
        self.client.send_batch([event("e1", "ofb")], batch_id="b1")
        w = self.app.writer
        w.parent_pid = -1                                    # "the parent is gone"
        self.assertTrue(w.orphaned())
        self.assertTrue(w.stop.is_set())
        w.parent_pid, w.stop = None, threading.Event()
        self.app.cfg["max_attempts"] = 1                     # one failure would make it dead
        self.ch.fail = True
        old = of_server.alert_dead
        of_server.alert_dead = lambda *a: None
        try:
            orig = self.app.buffer.get

            def get_and_commit_elsewhere(bid):           # another writer commits it meanwhile
                with self.app.store.conn() as c:
                    c.execute("UPDATE batches SET state = 'committed' WHERE batch_id = ?", (bid,))
                return orig(bid)
            self.app.buffer.get = get_and_commit_elsewhere
            w.drain()
        finally:
            of_server.alert_dead = old
        with self.app.store.conn() as c:
            self.assertEqual(c.execute("SELECT state FROM batches WHERE batch_id = 'b1'").fetchone()[0], "committed")


class DaemonTest(unittest.TestCase):
    """`of server start|stop` on a machine without a service manager (of_lab's quickstart, Q7)."""

    def test_start_is_idempotent_and_stop_removes_the_pid_file(self):
        import socket
        with tempfile.TemporaryDirectory() as root:
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            conf = os.path.join(root, "server.yaml")
            with open(conf, "w") as fh:
                fh.write(f"state_dir: {root}/state\nport: {port}\ntenant_key_file: {root}/tenant.key\n"
                         "plugins: {storage: sqlite}\n")
            log = os.path.join(root, "server.log")
            saved = os.environ.get("OF_STATE")
            os.environ["OF_STATE"] = os.path.join(root, "of")
            try:
                pid, msg = of_server.start_daemon(config=conf, log_path=log, wait=20)
                self.assertTrue(pid, msg + "\n" + open(log).read())
                self.assertIn("started", msg)
                self.assertEqual(of_server._read_pid(os.path.join(root, "state", "server.pid")), pid)
                pid2, msg2 = of_server.start_daemon(config=conf, log_path=log)
                self.assertEqual((pid2, "already" in msg2), (pid, True))
                ok, msg3 = of_server.stop_daemon(config=conf, wait=20)
                self.assertTrue(ok, msg3)
                self.assertFalse(os.path.exists(os.path.join(root, "state", "server.pid")))
                self.assertFalse(of_server.stop_daemon(config=conf)[0])
            finally:
                if saved is None:
                    os.environ.pop("OF_STATE", None)
                else:
                    os.environ["OF_STATE"] = saved


    def test_start_kicks_the_launchd_job_where_it_is_installed(self):
        from unittest import mock
        calls, answers = [], iter([False, True])
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(of_server, "launchd_job", lambda: "/x.plist"), \
                mock.patch.object(of_server, "load_config", lambda *a: dict(of_server.DEFAULTS, state_dir=root)), \
                mock.patch.object(of_server, "_health", lambda cfg, timeout=None: next(answers)), \
                mock.patch.object(of_server.subprocess, "run", lambda cmd, **kw: calls.append(cmd)), \
                mock.patch.object(of_server.subprocess, "Popen", side_effect=AssertionError("no detached copy")):
            pid, msg = of_server.start_daemon(wait=5)
        self.assertEqual(calls, [["launchctl", "kickstart", f"gui/{os.getuid()}/{of_server.LAUNCHD_LABEL}"]])
        self.assertIn("under launchd", msg)


class StoreTxTest(unittest.TestCase):
    """No exception leaves the server store's write lock held (of_architect, 2026-11-06)."""

    def other_write(self, path):
        o = sqlite3.connect(path, timeout=0.2, isolation_level=None)
        try:
            o.execute("INSERT OR IGNORE INTO seen VALUES (?)", (b"other",))
        finally:
            o.close()

    def test_an_error_mid_transaction_rolls_back_and_frees_the_lock(self):
        with tempfile.TemporaryDirectory() as d:
            st = of_server.Store(d)
            path = os.path.join(d, "server.db")
            with self.assertRaises(RuntimeError):
                with st.tx() as c:
                    c.execute("INSERT INTO seen VALUES (?)", (b"k1",))
                    raise RuntimeError("mid-transaction")
            self.assertFalse(st.conn().c.in_transaction)
            self.other_write(path)                                   # would raise "database is locked"
            with st.conn() as c:
                self.assertEqual(c.execute("SELECT count(*) FROM seen WHERE k = ?", (b"k1",)).fetchone()[0], 0)

    def test_a_bare_begin_left_open_by_an_error_is_rolled_back_on_exit(self):
        with tempfile.TemporaryDirectory() as d:
            st = of_server.Store(d)
            with self.assertRaises(KeyError):
                with st.conn() as c:
                    c.execute("BEGIN IMMEDIATE")
                    c.execute("INSERT INTO seen VALUES (?)", (b"k2",))
                    raise KeyError("between BEGIN and COMMIT")
            self.other_write(os.path.join(d, "server.db"))

    def test_relay_presence_failing_mid_write_keeps_the_store_writable(self):
        with tempfile.TemporaryDirectory() as d:
            st = of_server.Store(d)
            with self.assertRaises(sqlite3.Error):
                with st.tx() as c:
                    c.execute("DELETE FROM presence")
                    c.execute("INSERT INTO presence VALUES (?)", ("too few columns",))
            self.other_write(os.path.join(d, "server.db"))


class CodeWatchTest(unittest.TestCase):
    """A changed module under bin/ restarts the server as a whole (2026-11-07: Studio answered 500 when
    of_asks, loaded fresh, called a function the server's old of_classify lacked)."""

    def test_a_changed_loaded_module_is_named_once_stable_and_compiling(self):
        import importlib
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "of_cw_probe.py")
            with open(path, "w") as fh:
                fh.write("X = 1\n")
            old = time.time() - 60
            os.utime(path, (old, old))
            sys.path.insert(0, d)
            try:
                importlib.import_module("of_cw_probe")
                cw = of_server.CodeWatch(here=d, settle=2.0)
                self.assertEqual(cw.changed(), [])                       # first sight: the loaded version
                with open(path, "w") as fh:
                    fh.write("X = 2\n")
                self.assertEqual(cw.changed(), [])                       # just written: wait for the deploy
                os.utime(path, (old + 10, old + 10))
                self.assertEqual([os.path.basename(f) for f in cw.changed()], ["of_cw_probe.py"])
                with open(path, "w") as fh:                              # broken: the old code keeps serving
                    fh.write("X = (\n")
                os.utime(path, (old + 20, old + 20))
                self.assertEqual(cw.changed(), [])
                self.assertEqual(of_server.CodeWatch(here=os.path.join(d, "elsewhere")).changed(), [])
            finally:
                sys.path.remove(d)
                sys.modules.pop("of_cw_probe", None)


class AskClosureRoutingTest(unittest.TestCase):
    """An ask's closure with no organization follows the ask it names (of_studio, 2026-11-06)."""

    def test_closure_routes_to_the_asks_organization(self):
        import of_store
        with tempfile.TemporaryDirectory() as root:
            root = os.path.realpath(root)
            orgs = os.path.join(root, "orgs.yaml")
            with open(orgs, "w") as fh:
                fh.write(f"orgs:\n  - {{name: mt, database: db_mt, paths: ['{root}/mt']}}\n")
            saved = os.environ.get("OF_DB")
            os.environ["OF_DB"] = os.path.join(root, "of.db")
            try:
                conn = of_store.connect()
                conn.execute("INSERT INTO event (event_id, ts, name, session_id, cwd, record) VALUES (?,?,?,?,?,?)",
                             ("ask-1", "2026-11-06T10:00:00Z", "of.agent_question", "S1", f"{root}/mt/x", "{}"))
                conn.close()
                r = of_route.Router({"unassigned_database": "db_un"}, orgs)
                r.org_by_session = {}

                def closure(payload):
                    return {"record": "{}", "payload": json.dumps(payload), "session_id": ""}
                self.assertEqual(of_route.route_org("event", closure({"ask_id": "ask-1", "via": "reconcile"}), r), "mt")
                self.assertEqual(of_route.route_org("event", closure({"ask_ids": ["nope", "ask-1"]}), r), "mt")
                self.assertIsNone(of_route.route_org("event", closure({"ask_id": "nope"}), r))
                self.assertIsNone(of_route.route_org("event", closure({"answer": "x"}), r))
                self.assertIsNone(of_route.route_org("event", closure({}), of_route.StaticRouter({"mt": "db_mt"}, "u")))
            finally:
                if saved is None:
                    os.environ.pop("OF_DB", None)
                else:
                    os.environ["OF_DB"] = saved


class RedactionTest(unittest.TestCase):
    """POST /v1/redactions on profile 1 storage (SQLite): docs/server.md, Redaction."""

    def setUp(self):
        import of_plugins
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = os.path.join(root, "state")
        orgs = os.path.join(root, "orgs.yaml")
        with open(orgs, "w") as fh:
            fh.write(f"orgs:\n  - {{name: ofb, database: db_ofb, paths: ['{HOME}/organic-framework']}}\n")
        ship_cfg = {"unassigned_database": "db_un", "vault_database": "db_vault", "raw_to_org": False, "tenant": "t"}

        def router():
            r = of_route.Router(ship_cfg, orgs)
            r.org_by_session, r.own_org_by_session = {}, {}
            return r
        cfg = dict(of_server.DEFAULTS, state_dir=os.path.join(root, "server"), port=0, flush_every=3600)
        self.app = of_server.App(cfg, router_factory=router, ship_cfg=ship_cfg, grants_loader=lambda: {},
                                 grant_key=lambda: b"k", mask_key=b"m" * 32)
        self.assertIsInstance(self.app.storage, of_plugins.SQLiteStorage)
        self.httpd = self.app.serve(host="127.0.0.1", port=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.tenant = self.client(url, "tenant", tenant=True)
        self.bob = self.client(url, "bob")

    def client(self, url, name, tenant=False):
        d = os.path.join(self.tmp.name, name)
        os.makedirs(d)
        c = of_client.Client(url, key_path=os.path.join(d, "key"), state_dir=d)
        c.machine_id = f"m-{name}"
        of_server.enroll(self.app.store, c.machine_id, of_client.ensure_key(c.key_path), ["ofb"], ["ofb"],
                         tenant=tenant, person=None if tenant else "bob")
        return c

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.writer.stop.set()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def rec(self, uuid, raw, host):
        r = transcript(uuid, "-Users-x-organic-framework", raw)
        r["data"].update(host_id=host, org_id="ofb")
        return r

    def rows(self, db, table="transcript_record"):
        c = self.app.storage._conn(db)
        return {k: json.loads(r) for k, r in c.execute(f"SELECT key, row FROM {table}")}

    def post(self, c, body):
        try:
            return 200, c._authed("POST", "/v1/redactions", body=body)[1]
        except of_client.ServerError as e:
            return e.status, json.loads(e.body)

    def test_replace_everywhere_with_an_event_and_no_trace_on_disk(self):
        self.tenant.send_batch([self.rec("u1", '{"t": "OLD-VALUE one"}', "m-tenant"),
                                self.rec("u2", '{"t": "keep"}', "m-tenant")], batch_id="b1")
        self.app.writer.drain()
        self.assertEqual({r["raw"] for r in self.rows("db_vault").values()}, {'{"t": "OLD-VALUE one"}', '{"t": "keep"}'})
        # A dead batch and a queued one still hold the old row: both are rewritten.
        dead = os.path.join(self.app.buffer.dead_dir, "old.json")
        with open(dead, "w") as fh:
            json.dump({"batch_id": "old", "records": [self.rec("u1", '{"t": "OLD-VALUE one"}', "m-tenant")]}, fh)
        body = {"redaction_id": "r1", "table": "transcript_record", "ids": ["u1"], "session": "T", "sha_old": "a",
                "sha_new": "b", "reason": "key in transcript",
                "records": [self.rec("u1", '{"t": "[secret:x:1234abcd] one"}', "m-tenant")]}
        st, out = self.post(self.tenant, body)
        self.assertEqual((st, out["state"]), (200, "queued"))
        self.assertEqual(self.tenant._authed("GET", "/v1/redactions/r1")[1]["state"], "queued")
        self.app.writer.drain()
        got = self.tenant._authed("GET", "/v1/redactions/r1")[1]
        self.assertEqual(got["state"], "done")
        self.assertEqual((got["counts"]["db_vault"], got["counts"]["disk"]), (1, 1))
        for db in ("db_vault",):
            raws = sorted(r["raw"] for r in self.rows(db).values())
            self.assertEqual(raws, ['{"t": "[secret:x:1234abcd] one"}', '{"t": "keep"}'])
        with open(dead) as fh:
            self.assertNotIn("OLD-VALUE", fh.read())
        ev = [r for r in self.rows("db_vault", "event").values() if r["name"] == "of.redacted"]
        self.assertEqual(len(ev), 1)
        p = json.loads(ev[0]["payload"])
        self.assertEqual((p["redaction_id"], p["table"], p["ids"], p["reason"]), ("r1", "transcript_record", ["u1"],
                                                                                 "key in transcript"))
        self.assertNotIn("OLD-VALUE", json.dumps(ev))
        self.assertEqual(self.post(self.tenant, body)[1]["state"], "done")                 # idempotent
        # of.redacted is never redactable.
        st, _ = self.post(self.tenant, {"redaction_id": "r2", "table": "event", "ids": [ev[0]["event_id"]],
                                        "reason": "hide it"})
        self.assertEqual(st, 200)
        self.app.writer.drain()
        self.assertEqual(len([r for r in self.rows("db_vault", "event").values() if r["name"] == "of.redacted"]), 2)

    def test_a_replacement_goes_where_its_row_was_and_queued_redactions_keep_theirs(self):
        self.tenant.send_batch([self.rec("u1", '{"t": "OLD one"}', "m-tenant")], batch_id="b1")
        self.app.writer.drain()
        self.assertIn("ofb\x1fT\x1fu1".split("\x1f", 1)[1], self.rows("db_ofb"))
        loose = self.rec("u1", '{"t": "NEW one"}', "m-tenant")
        loose["data"].update(org_id="", source_path="/nowhere/T.jsonl")         # routes to no organization
        loose2 = json.loads(json.dumps(loose))
        loose2["data"]["raw"] = '{"t": "NEWER one"}'
        base = {"table": "transcript_record", "ids": [["T", "u1"]], "reason": "r"}
        self.assertEqual(self.post(self.tenant, {**base, "redaction_id": "a", "records": [loose]})[0], 200)
        self.assertEqual(self.post(self.tenant, {**base, "redaction_id": "b", "records": [loose2]})[0], 200)
        self.app.writer.drain()
        self.assertEqual([r["raw"] for r in self.rows("db_vault").values()], ['{"t": "NEWER one"}'])
        self.assertIn("T\x1fu1", self.rows("db_ofb"))                          # its organization keeps it
        self.assertEqual(self.rows("db_un"), {})
        for rid in ("a", "b"):
            self.assertEqual(self.tenant._authed("GET", f"/v1/redactions/{rid}")[1]["state"], "done")

    def test_a_colleague_redacts_only_its_own_rows_and_never_deletes_bare(self):
        self.tenant.send_batch([self.rec("u1", '{"t": "tenant row"}', "m-tenant")], batch_id="b1")
        self.bob.send_batch([self.rec("u9", '{"t": "OLD bob"}', "m-bob")], batch_id="b2")
        self.app.writer.drain()
        base = {"table": "transcript_record", "session": "T", "reason": "r"}
        self.assertEqual(self.post(self.bob, {**base, "redaction_id": "x1", "ids": ["u9"]})[0], 403)   # bare delete
        st, _ = self.post(self.bob, {**base, "redaction_id": "x2", "ids": ["u1"],
                                     "records": [self.rec("u1", '{"t": "mine now"}', "m-tenant")]})
        self.assertEqual(st, 403)                                                             # another machine's row
        st, _ = self.post(self.bob, {**base, "redaction_id": "x3", "ids": ["u9"],
                                     "records": [self.rec("u7", '{"t": "elsewhere"}', "m-bob")]})
        self.assertEqual(st, 400)                                                             # not one of ids
        empty = self.rec("u9", "", "m-bob")
        self.assertEqual(self.post(self.bob, {**base, "redaction_id": "x6", "ids": ["u9"], "records": [empty]})[0], 400)
        self.assertEqual(self.post(self.bob, {**base, "redaction_id": "x4", "ids": ["u9"], "reason": " ",
                                              "records": [self.rec("u9", '{"t": "new"}', "m-bob")]})[0], 400)
        st, _ = self.post(self.bob, {**base, "redaction_id": "x5", "ids": ["u9"],
                                     "records": [self.rec("u9", '{"t": "NEW bob"}', "m-bob")]})
        self.assertEqual(st, 200)
        self.app.writer.drain()
        raws = sorted(r["raw"] for r in self.rows("db_vault").values())
        self.assertEqual(raws, ['{"t": "NEW bob"}', '{"t": "tenant row"}'])
        with self.assertRaises(of_client.ServerError) as cm:                                  # not its redaction
            self.bob._authed("GET", "/v1/redactions/nope")
        self.assertEqual(cm.exception.status, 404)
        # Many sessions in one request: [session_id, record_uuid] pairs.
        self.tenant.send_batch([dict(self.rec("p1", '{"t": "OLD p1"}', "m-tenant"))], batch_id="b3")
        r2 = self.rec("p2", '{"t": "OLD p2"}', "m-tenant")
        r2["data"]["session_id"] = "T2"
        self.tenant.send_batch([r2], batch_id="b4")
        self.app.writer.drain()
        new2 = self.rec("p2", '{"t": "NEW p2"}', "m-tenant")
        new2["data"]["session_id"] = "T2"
        st, _ = self.post(self.tenant, {"redaction_id": "pairs", "table": "transcript_record", "reason": "sweep",
                                        "ids": [["T", "p1"], ["T2", "p2"]],
                                        "records": [self.rec("p1", '{"t": "NEW p1"}', "m-tenant"), new2]})
        self.assertEqual(st, 200)
        self.app.writer.drain()
        raws = {r["raw"] for r in self.rows("db_vault").values()}
        self.assertTrue({'{"t": "NEW p1"}', '{"t": "NEW p2"}'} <= raws and not any("OLD p" in r for r in raws))
        self.assertEqual(self.post(self.tenant, {"redaction_id": "bad", "table": "transcript_record", "reason": "x",
                                                 "ids": ["p1"]})[0], 400)            # a bare uuid needs session
        # The tenant may delete without a replacement.
        self.assertEqual(self.post(self.tenant, {**base, "redaction_id": "t1", "ids": ["u1"]})[0], 200)
        self.app.writer.drain()
        self.assertEqual(sorted(r["raw"] for r in self.rows("db_vault").values()),
                         ['{"t": "NEW bob"}', '{"t": "NEW p1"}', '{"t": "NEW p2"}'])


class ClickHouseRedactTest(unittest.TestCase):
    def test_delete_uses_the_redact_role_and_a_waited_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("of", "db_ofb_ingest"):
                with open(os.path.join(d, f"{name}.password"), "w") as fh:
                    fh.write("pw")
            ch = of_route.ClickHouse({"url": "http://ch", "user": "of", "password_file": os.path.join(d, "of.password")})
            calls = []
            ch.post = lambda q, body=b"", timeout=120, auth=None, params=None, query_in_body=False: \
                (calls.append((q, auth, params)), b"2")[1]
            with self.assertRaises(RuntimeError):                       # ingest users but no redact role
                ch.delete("db_ofb", "event", "event_id IN ('e1')")
            with open(os.path.join(d, "db_ofb_redact.password"), "w") as fh:
                fh.write("pw")
            self.assertEqual(ch.delete("db_ofb", "event", "event_id IN ('e1')"), 2)
            import base64
            users = {base64.b64decode(a.split()[1]).decode().split(":")[0] for _, a, _ in calls}
            self.assertEqual(users, {"db_ofb_redact"})
            self.assertTrue(calls[0][0].startswith("SELECT count() FROM db_ofb.event WHERE"))
            self.assertTrue(calls[1][0].startswith("ALTER TABLE db_ofb.event DELETE WHERE"))
            self.assertEqual(calls[1][2], {"mutations_sync": 1})
