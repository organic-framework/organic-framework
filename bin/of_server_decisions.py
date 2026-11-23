"""of-server: decisions accepted once, in the organization's order (event store, step 1).

The design is organic-framework-build reports/framework/event-store/04-design.md §2 and §6 step 1, the founder's
choice on card OF-294 (option B, no off-host copy); the wire contract, agreed with of_platform, is
step1-accept-contract.md beside it. A machine proposes a decision; this server accepts the first proposal that keeps
the stream's rule and refuses the others with the accepted one; every machine folds the accepted decisions in this
server's sequence for the organization. Step 1 has one kind, `answer`: the stream is one card at one version
(`<ask id>@v<version>`), and its one step is open -> answered.

- The check and the append are one BEGIN IMMEDIATE at synchronous FULL, with fullfsync and checkpoint_fullfsync on
  macOS, where fsync alone leaves the drive's cache unflushed. The answer goes out after the commit.
- The proposal's id is its receipt: the same id with the same content gets the same answer, with other content 409.
- A refused proposal is kept, and its 409 carries the accepted record.
- The accepted record, of.decision_accepted, is written by the server and enters the coordination feed in the same
  transaction, so every machine of the organization gets it, the proposer included.
- A restoration (a receipt resent after this server lost its disk) is accepted with its own seq and stream version,
  and wins over a later decision on its stream that is no restoration. No new proposal waits for it.
- Decisions are off for an organization until `of_server.py decisions --org <org> on` writes its cutover.
- The answer's text is masked before it is stored, as a relay message is at post.
"""

import hashlib
import json
import re
import sys
import time
import uuid

import of_events

DDL = """
CREATE TABLE IF NOT EXISTS decision_orgs (org TEXT PRIMARY KEY, on_at TEXT NOT NULL, first_seq INTEGER NOT NULL,
  next_seq INTEGER NOT NULL, cutover_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (org TEXT NOT NULL, seq INTEGER NOT NULL, proposal TEXT NOT NULL UNIQUE,
  event_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, stream TEXT NOT NULL, stream_version INTEGER NOT NULL,
  state TEXT NOT NULL, restored INTEGER NOT NULL DEFAULT 0, supersedes TEXT, machine TEXT NOT NULL,
  accepted_at TEXT NOT NULL, record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS decisions_stream ON decisions (org, stream, state);
CREATE INDEX IF NOT EXISTS decisions_seq ON decisions (org, seq);
CREATE TABLE IF NOT EXISTS proposals (proposal TEXT PRIMARY KEY, org TEXT NOT NULL, stream TEXT NOT NULL,
  body_sha TEXT NOT NULL, state TEXT NOT NULL, status INTEGER NOT NULL, answer TEXT NOT NULL, machine TEXT NOT NULL,
  at TEXT NOT NULL);
"""

ACCEPTED, CUTOVER = "of.decision_accepted", "of.decision_cutover"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
STREAM = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})@v([1-9][0-9]{0,5})\Z")
ORG = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
MAX_BODY = 64 * 1024
MAX_TEXT = 16 * 1024
KEYS = {"v", "id", "org", "kind", "stream", "payload", "proposer", "signature", "restore"}
# the payload: the answer and its fields as the client records them today, plus any further key of its own (the
# chat's proof that the person answered, a status's note: of_platform, message 01a2089d-006b), but never one the server
# sets on the accepted record or one the client never sends
RESERVED = {"proposal", "org", "kind", "stream", "stream_version", "seq", "step", "machine", "proposer", "signed",
            "accepted_at", "restored", "supersedes", "version", "ask_ids", "decision"}
PAYLOAD_KEY = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
MAX_KEYS = 32
PROPOSER = {"position", "session", "person"}
VIA = {"asks-ui", "conductor", "cli", "unit", "status"}


class DecisionError(Exception):
    def __init__(self, status, typ, title, extra=None):
        super().__init__(title)
        self.status, self.typ, self.title, self.extra = status, typ, title, extra or {}

    def body(self):
        return dict({"type": self.typ, "title": self.title, "status": self.status}, **self.extra)


_ready = set()


def ensure(store):
    if store.dir not in _ready:
        with store.conn() as c:
            c.executescript(DDL)
        _ready.add(store.dir)


def _durable(c):
    """The connection's commits reach the disk: FULL, and on macOS a full flush of the drive's cache too."""
    c.execute("PRAGMA synchronous = FULL")
    if sys.platform == "darwin":
        c.execute("PRAGMA fullfsync = ON")
        c.execute("PRAGMA checkpoint_fullfsync = ON")


def validate(body):
    """The proposal as the contract gives it (step1-accept-contract.md §2), or DecisionError 400."""
    def bad(why):
        return DecisionError(400, "bad_request", why)
    if not isinstance(body, dict) or set(body) - KEYS or not {"v", "id", "org", "kind", "stream", "payload"} <= set(body):
        raise bad(f"a proposal is {{{', '.join(sorted(KEYS))}}}, v, id, org, kind, stream and payload required")
    if body["v"] != 1 or body["kind"] != "answer":
        raise bad("only v 1 and kind answer")
    if not isinstance(body["id"], str) or not UUID.fullmatch(body["id"]):
        raise bad("id: the proposal's own UUID")
    if not isinstance(body["org"], str) or not ORG.fullmatch(body["org"]):
        raise bad("org: an organization's name")
    m = STREAM.fullmatch(body["stream"]) if isinstance(body["stream"], str) else None
    if not m:
        raise bad("stream: <ask id>@v<version>")
    p = body["payload"]
    if not isinstance(p, dict) or not isinstance(p.get("answer"), str):
        raise bad("payload: an object with the answer's text")
    if len(p) > MAX_KEYS or not all(isinstance(k, str) and PAYLOAD_KEY.fullmatch(k) for k in p):
        raise bad(f"payload: at most {MAX_KEYS} keys, each a lowercase name")
    if set(p) & RESERVED:
        raise bad(f"payload: {', '.join(sorted(set(p) & RESERVED))} is the server's to set, or no client's to send")
    if len(p["answer"].encode()) > MAX_TEXT or "\x00" in p["answer"]:
        raise bad(f"payload.answer: at most {MAX_TEXT} bytes, no NUL")
    if p.get("ask_id") not in (None, m.group(1)):
        raise bad("payload.ask_id: the stream's ask")
    if p.get("via") is not None and p["via"] not in VIA:
        raise bad(f"payload.via: one of {', '.join(sorted(VIA))}")
    if p.get("by") is not None and not (isinstance(p["by"], str) and (p["by"] in ("owner", "founder")
                                                                         or NAME.fullmatch(p["by"]))):
        raise bad("payload.by: owner or a position's name")
    if p.get("verdict") not in (None, "not_a_question"):
        raise bad("payload.verdict: not_a_question or none")
    who = body.get("proposer")
    if who is not None and (not isinstance(who, dict) or set(who) - PROPOSER or not all(
            x is None or (isinstance(x, str) and 0 < len(x) <= 64 and "\x00" not in x) for x in who.values())):
        raise bad("proposer: {position, session, person}, each a short text or null")
    if body.get("signature") is not None:
        raise bad("signature: none until a person's key exists (step 1 accepts unsigned steps and marks them)")
    r = body.get("restore")
    if r is not None and not (isinstance(r, dict) and set(r) <= {"seq", "stream_version", "accepted_at"}
                              and all(isinstance(r.get(k), int) and not isinstance(r.get(k), bool) and r[k] >= 1
                                      for k in ("seq", "stream_version"))):
        raise bad("restore: the receipt, {seq, stream_version, accepted_at}")
    return body


def _sha(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def decision_id(proposal):
    """The accepted record's id: the same on every machine and every rebuild."""
    return str(uuid.uuid5(of_events.STABLE_NS, "decision:" + proposal))


def _record(eid, name, org, ts, payload):
    return {"of.schema": 1, "of.event.id": eid, "timestamp": ts, "event.name": name, "of.source": "of-server",
            "host.id": "of-server", "of.org": org, "of.payload": payload}


def _feed(c, org, rec, now):
    """The record into the organization's coordination feed, in the caller's transaction."""
    c.execute("INSERT OR IGNORE INTO coord (org, event_id, name, ts, host_id, record, added) VALUES (?,?,?,?,?,?,?)",
              (org, rec["of.event.id"], rec["event.name"], rec["timestamp"], "of-server",
               json.dumps(rec, ensure_ascii=False), now))


def _row(tenant, rec):
    """The record as a server event row, for the organization's analytics database through the writer."""
    payload = rec["of.payload"]
    return {"tenant_id": tenant, "org_id": rec["of.org"], "host_id": "of-server", "event_id": rec["of.event.id"],
            "ts": rec["timestamp"], "name": rec["event.name"], "session_id": "", "position": "", "source": "of-server",
            "harness_name": "", "harness_version": "", "model": "", "payload": json.dumps(payload, ensure_ascii=False),
            "raw_ref": "", "record": json.dumps(rec, ensure_ascii=False)}


def turn_on(app, org, now=None):
    """Turn decisions on for ORG: its cutover record, with its time and the first seq to come, in the feed. Returns
    (record, False) when it was on already, (record, True) when this call turned it on."""
    import of_server
    import of_server_coord
    ensure(app.store)
    of_server_coord.ensure(app.store)
    now = time.time() if now is None else now
    with app.store.conn() as c:
        _durable(c)
    with app.store.tx() as c:
        had = c.execute("SELECT on_at, first_seq, cutover_id FROM decision_orgs WHERE org = ?", (org,)).fetchone()
        if had:
            rec = json.loads(c.execute("SELECT record FROM coord WHERE org = ? AND event_id = ?", (org, had[2]))
                             .fetchone()[0])
            return rec, False
        ts = of_events.now_iso()
        eid = str(uuid.uuid5(of_events.STABLE_NS, f"decision-cutover:{org}"))
        rec = _record(eid, CUTOVER, org, ts, {"org": org, "on_at": ts, "first_seq": 1})
        c.execute("INSERT INTO decision_orgs (org, on_at, first_seq, next_seq, cutover_id) VALUES (?,?,?,?,?)",
                  (org, ts, 1, 1, eid))
        _feed(c, org, rec, now)
    of_server.queue_server_events(app.store, app.buffer, [_row(app.ship_cfg["tenant"], rec)])
    return rec, True


def status(store, org=None):
    ensure(store)
    with store.conn() as c:
        rows = c.execute("SELECT org, on_at, first_seq, next_seq FROM decision_orgs" + (" WHERE org = ?" if org else "")
                         + " ORDER BY org", (org,) if org else ()).fetchall()
        out = []
        for o, on_at, first, nxt in rows:
            n = c.execute("SELECT count(*), sum(state = 'accepted') FROM decisions WHERE org = ?", (o,)).fetchone()
            r = c.execute("SELECT count(*) FROM proposals WHERE org = ? AND state = 'refused'", (o,)).fetchone()[0]
            out.append({"org": o, "on_at": on_at, "first_seq": first, "next_seq": nxt, "decisions": n[0],
                        "accepted": n[1] or 0, "refused": r})
    return out


def propose(app, m, body, joined, served):
    """POST /v1/decisions: (status, body). M is the authenticated machine, JOINED its organizations, SERVED whether
    this server serves the proposal's organization."""
    import of_server
    import of_server_coord
    ensure(app.store)
    of_server_coord.ensure(app.store)
    if len(json.dumps(body, ensure_ascii=False).encode()) > MAX_BODY:
        raise DecisionError(413, "too_large", f"a proposal is at most {MAX_BODY} bytes")
    validate(body)
    org, pid, stream = body["org"], body["id"], body["stream"]
    if not served:
        raise DecisionError(404, "unknown_org", f"organization {org} is not served here")
    if not m["tenant"] and org not in joined:
        raise DecisionError(403, "forbidden", f"organization {org} not joined by this machine")
    sha = _sha(body)
    # every text of the payload masked before it is stored, the answer's and any proof's or note's, as a relay
    # message's is at post
    payload = json.loads(app.masker.json_text(json.dumps(body["payload"], ensure_ascii=False)))
    found = app.masker.take()
    now = time.time()
    with app.store.conn() as c:
        _durable(c)
    accepted_rec, sightings = None, []
    with app.store.tx() as c:
        if not c.execute("SELECT 1 FROM decision_orgs WHERE org = ?", (org,)).fetchone():
            raise DecisionError(404, "decisions_off", f"decisions are not on for {org}: the client records the answer "
                                                      "as before")
        had = c.execute("SELECT body_sha, status, answer FROM proposals WHERE proposal = ?", (pid,)).fetchone()
        if had:
            if had[0] != sha:
                raise DecisionError(409, "conflict", "proposal id reused with other content")
            return had[1], json.loads(had[2])
        cur = c.execute("SELECT proposal, record, restored FROM decisions WHERE org = ? AND stream = ? AND "
                        "state = 'accepted'", (org, stream)).fetchone()
        restore = body.get("restore")
        supersedes = None
        if cur and not (restore and not cur[2]):
            # the stream is decided (a restoration loses only to an earlier restoration): refused, with the accepted
            answer = {"type": "refused", "state": "refused", "status": 409, "accepted": json.loads(cur[1]),
                      "title": f"{stream} was already decided"}
            c.execute("INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?)", (pid, org, stream, sha, "refused", 409,
                                                                         json.dumps(answer), m["machine_id"],
                                                                         of_events.now_iso()))
            return 409, answer
        if cur:   # a restoration wins over a later decision on its stream that is no restoration
            supersedes = cur[0]
            c.execute("UPDATE decisions SET state = 'superseded' WHERE proposal = ?", (cur[0],))
        if restore:
            seq, version = restore["seq"], restore["stream_version"]
            c.execute("UPDATE decision_orgs SET next_seq = max(next_seq, ?) WHERE org = ?", (seq + 1, org))
        else:
            seq = c.execute("SELECT next_seq FROM decision_orgs WHERE org = ?", (org,)).fetchone()[0]
            c.execute("UPDATE decision_orgs SET next_seq = ? WHERE org = ?", (seq + 1, org))
            version = 1
        ts = of_events.now_iso()
        eid = decision_id(pid)
        accepted_rec = _record(eid, ACCEPTED, org, ts, dict(
            payload, proposal=pid, org=org, kind="answer", stream=stream, stream_version=version, seq=seq,
            step="open→answered", machine=m["machine_id"], proposer=body.get("proposer") or {},
            signed=False if payload.get("by") in ("owner", "founder") else None, accepted_at=ts,
            restored=bool(restore), supersedes=supersedes))
        c.execute("INSERT INTO decisions (org, seq, proposal, event_id, kind, stream, stream_version, state, restored,"
                  " supersedes, machine, accepted_at, record) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (org, seq, pid, eid, "answer", stream, version, "accepted", 1 if restore else 0, supersedes,
                   m["machine_id"], ts, json.dumps(accepted_rec, ensure_ascii=False)))
        answer = {"state": "accepted", "decision": accepted_rec}
        c.execute("INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?)", (pid, org, stream, sha, "accepted", 200,
                                                                     json.dumps(answer), m["machine_id"], ts))
        _feed(c, org, accepted_rec, now)
        sightings = [(k, h, org, "decision", {"count": n, "first_ts": ts, "last_ts": ts,
                                               "location": f"decision:{pid}"}) for (k, h), n in found.items()]
    of_server.queue_server_events(app.store, app.buffer, [_row(app.ship_cfg["tenant"], accepted_rec)], sightings)
    return 200, answer
