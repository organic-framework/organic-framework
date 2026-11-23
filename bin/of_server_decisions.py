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
- Every accepted record carries a receipt: an HMAC, under a key derived from the tenant's master key (which lives
  outside the state folder a lost disk takes), over the organization, the proposal, the stream, the seq, the stream
  version, the time and the proposal's content. A restoration (a proposal resent after this server lost its disk)
  counts only with a receipt this server issued for exactly that proposal (of_secrets-review on PR 614: an unproven
  restore let any machine supersede an accepted answer and set seq at will). It keeps its seq unless another
  decision of the organization holds that number, then takes the next one. It fills only a stream with no
  decision: a decided stream refuses it with the decision it holds, as any proposal, so the server and every
  machine's reader keep one answer per stream (of_review on PR 614). Whether a restoration should win over a later
  decision waits for the client's resend and readers that apply it. No new proposal waits for it.
- Decisions are off for an organization until `of_server.py decisions --org <org> on` writes its cutover.
- The answer's text is masked before it is stored, as a relay message is at post.
"""

import hashlib
import hmac
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
CREATE UNIQUE INDEX IF NOT EXISTS decisions_org_seq ON decisions (org, seq);
CREATE TABLE IF NOT EXISTS proposals (proposal TEXT PRIMARY KEY, org TEXT NOT NULL, stream TEXT NOT NULL,
  body_sha TEXT NOT NULL, state TEXT NOT NULL, status INTEGER NOT NULL, answer TEXT NOT NULL, machine TEXT NOT NULL,
  at TEXT NOT NULL);
"""

ACCEPTED, CUTOVER = "of.decision_accepted", "of.decision_cutover"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
STREAM = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})@v([1-9][0-9]{0,5})\Z")
ORG = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
MAX_BODY = 64 * 1024
MAX_DEPTH = 8              # how deep a payload's values may nest
MAX_SEQ = 2 ** 53          # a seq a receipt may name
RECEIPT_LABEL = b"of/decision-receipt/v1"
MAX_TEXT = 16 * 1024
KEYS = {"v", "id", "org", "kind", "stream", "payload", "proposer", "signature", "restore"}
# the payload: the answer and its fields as the client records them today, plus any further key of its own (the
# chat's proof that the person answered, a status's note: of_platform, message 01a2089d-006b), but never one the server
# sets on the accepted record or one the client never sends
RESERVED = {"proposal", "org", "kind", "stream", "stream_version", "seq", "step", "machine", "proposer", "signed",
            "accepted_at", "restored", "supersedes", "version", "ask_ids", "decision"}
PAYLOAD_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")   # as the client checks them (bin/of_decisions.py)
MAX_KEYS = 32
PROPOSER = {"position", "session", "person"}
# via and by are the client's words, kept on the record; no authority is read from them in step 1, so any short name
# passes (today's answers carry asks-ui, conductor, relay, pulse and more), and a proposal is never lost to a word the
# server did not list
WORD = re.compile(r"[a-z][a-z0-9_.:-]{0,63}\Z")


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
    for k in ("via", "by"):
        if p.get(k) is not None and not (isinstance(p[k], str) and WORD.fullmatch(p[k])):
            raise bad(f"payload.{k}: a short lowercase name")
    if _depth(p) > MAX_DEPTH:
        raise bad(f"payload: nested at most {MAX_DEPTH} deep")
    if p.get("verdict") not in (None, "not_a_question"):
        raise bad("payload.verdict: not_a_question or none")
    who = body.get("proposer")
    if who is not None and (not isinstance(who, dict) or set(who) - PROPOSER or not all(
            x is None or (isinstance(x, str) and 0 < len(x) <= 64 and "\x00" not in x) for x in who.values())):
        raise bad("proposer: {position, session, person}, each a short text or null")
    if body.get("signature") is not None:
        raise bad("signature: none until a person's key exists (step 1 accepts unsigned steps and marks them)")
    r = body.get("restore")
    if r is not None and not (isinstance(r, dict) and set(r) == {"seq", "stream_version", "accepted_at", "receipt"}
                              and all(isinstance(r[k], int) and not isinstance(r[k], bool) and 1 <= r[k] <= MAX_SEQ
                                      for k in ("seq", "stream_version"))
                              and isinstance(r["accepted_at"], str) and 0 < len(r["accepted_at"]) <= 40
                              and isinstance(r["receipt"], str) and re.fullmatch(r"[0-9a-f]{64}", r["receipt"])):
        raise bad("restore: the accepted record's {seq, stream_version, accepted_at, receipt}")
    return body


def _depth(v):
    """How deep V nests, without recursion."""
    deepest, todo = 0, [(v, 0)]
    while todo:
        x, d = todo.pop()
        deepest = max(deepest, d)
        if d > MAX_DEPTH:
            return d
        if isinstance(x, dict):
            todo.extend((y, d + 1) for y in x.values())
        elif isinstance(x, list):
            todo.extend((y, d + 1) for y in x)
    return deepest


def _receipt_key(app):
    """The receipts' key: purpose of/decision-receipt/v1 of the masking key, itself derived from the tenant's master
    key, so a server restored from its backup or rebuilt on a new disk with the same tenant key checks old receipts."""
    k = getattr(app, "_decision_receipt_key", None)
    if k is None:
        k = app._decision_receipt_key = hmac.new(app.masker.key, RECEIPT_LABEL, hashlib.sha256).digest()
    return k


def _content(body):
    """The proposal's own content as it was sent (the receipt binds it: a receipt seen on the feed restores only the
    proposal it was issued for, never another answer under its id)."""
    return hashlib.sha256(json.dumps({"kind": body["kind"], "payload": body["payload"],
                                      "proposer": body.get("proposer") or {}}, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


def receipt(key, org, proposal, stream, seq, version, accepted_at, content):
    msg = "\n".join(("of-decision-receipt/v1", org, proposal, stream, str(seq), str(version), accepted_at, content))
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()


def _sha(body):
    """The proposal's digest for its id's receipt: a resend with `restore` of one this server still holds is the same
    proposal and gets its first answer."""
    return hashlib.sha256(json.dumps({k: v for k, v in body.items() if k != "restore"}, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


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
        if had:   # the same record, rebuilt from the row: the feed keeps it 30 days only
            return _record(had[2], CUTOVER, org, had[0], {"org": org, "on_at": had[0], "first_seq": had[1]}), False
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


def _off(store, org, c=None):
    """DecisionError 404 decisions_off unless ORG's decisions are on."""
    if c is None:
        with store.conn() as c2:
            on = c2.execute("SELECT 1 FROM decision_orgs WHERE org = ?", (org,)).fetchone()
    else:
        on = c.execute("SELECT 1 FROM decision_orgs WHERE org = ?", (org,)).fetchone()
    if not on:
        raise DecisionError(404, "decisions_off", f"decisions are not on for {org}: the client records the answer as "
                                                  "before")


def _refusal(c, org, stream):
    """The 409 a refused proposal gets: the stream's accepted decision as it stands now. A refused proposal keeps no
    copy of it (of_secrets-review on PR 614: many small proposals against one stream grew the table by the winner's
    size each)."""
    row = c.execute("SELECT record FROM decisions WHERE org = ? AND stream = ? AND state = 'accepted'",
                    (org, stream)).fetchone()
    return {"type": "refused", "state": "refused", "status": 409, "title": f"{stream} was already decided",
            "accepted": json.loads(row[0]) if row else None}


def propose(app, m, body, joined, served):
    """POST /v1/decisions: (status, body). M is the authenticated machine, JOINED its organizations, SERVED whether
    this server serves the proposal's organization."""
    import of_server
    import of_server_coord
    ensure(app.store)
    of_server_coord.ensure(app.store)
    org = body.get("org") if isinstance(body, dict) else None
    if not isinstance(org, str) or not ORG.fullmatch(org):
        raise DecisionError(400, "bad_request", "org: an organization's name")
    if not served:
        raise DecisionError(404, "unknown_org", f"organization {org} is not served here")
    if not m["tenant"] and org not in joined:
        raise DecisionError(403, "forbidden", f"organization {org} not joined by this machine")
    # off before anything else is checked: until the cutover a proposal the server would read otherwise takes
    # today's path on the client, never a refusal for its form
    _off(app.store, org)
    if len(json.dumps(body, ensure_ascii=False).encode()) > MAX_BODY:
        raise DecisionError(413, "too_large", f"a proposal is at most {MAX_BODY} bytes")
    validate(body)
    pid, stream = body["id"], body["stream"]
    sha = _sha(body)
    key = _receipt_key(app)
    content = _content(body)
    restore = body.get("restore")
    if restore and not hmac.compare_digest(receipt(key, org, pid, stream, restore["seq"], restore["stream_version"],
                                                   restore["accepted_at"], content), restore["receipt"]):
        raise DecisionError(400, "bad_receipt", "restore: no receipt this server issued for this proposal")
    # every text of the payload and of the proposer masked before it is stored, the answer's and any proof's or
    # note's, as a relay message's is at post (of_secrets-review on PR 614: the proposer went in as sent)
    masked = json.loads(app.masker.json_text(json.dumps({"payload": body["payload"],
                                                          "proposer": body.get("proposer") or {}},
                                                         ensure_ascii=False)))
    payload, proposer = masked["payload"], masked["proposer"]
    found = app.masker.take()   # this thread's findings only (of_mask.Masker)
    now = time.time()
    with app.store.conn() as c:
        _durable(c)
    accepted_rec, sightings = None, []
    with app.store.tx() as c:
        _off(app.store, org, c)
        had = c.execute("SELECT body_sha, status, answer, state, stream FROM proposals WHERE proposal = ?",
                        (pid,)).fetchone()
        if had:
            if had[0] != sha:
                raise DecisionError(409, "conflict", "proposal id reused with other content")
            if had[3] == "refused":   # kept without the winner's copy; the stream's accepted one now
                return 409, _refusal(c, org, had[4])
            return had[1], json.loads(had[2])
        if c.execute("SELECT 1 FROM decisions WHERE org = ? AND stream = ? AND state = 'accepted'",
                     (org, stream)).fetchone():
            # the stream is decided, a restoration's too: refused, with the accepted (one answer per stream, the
            # same on the server and in every reader)
            c.execute("INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?)",
                      (pid, org, stream, sha, "refused", 409, "{}", m["machine_id"], of_events.now_iso()))
            return 409, _refusal(c, org, stream)
        supersedes = None   # kept in the record's shape; no decision supersedes another in step 1
        nxt = c.execute("SELECT next_seq FROM decision_orgs WHERE org = ?", (org,)).fetchone()[0]
        held_seq = None
        if restore:
            version, accepted_at = restore["stream_version"], restore["accepted_at"]
            if c.execute("SELECT 1 FROM decisions WHERE org = ? AND seq = ?", (org, restore["seq"])).fetchone():
                seq, held_seq = nxt, restore["seq"]   # its number was given again after the loss: the next one,
                nxt += 1                              # so the organization keeps one order
            else:
                seq, nxt = restore["seq"], max(nxt, restore["seq"] + 1)
        else:
            seq, nxt, version, accepted_at = nxt, nxt + 1, 1, None
        c.execute("UPDATE decision_orgs SET next_seq = ? WHERE org = ?", (nxt, org))
        ts = of_events.now_iso()
        accepted_at = accepted_at or ts
        eid = decision_id(pid)
        fields = dict(payload, proposal=pid, org=org, kind="answer", stream=stream, stream_version=version, seq=seq,
                      step="open\u2192answered", machine=m["machine_id"], proposer=proposer,
                      signed=False if payload.get("by") in ("owner", "founder") else None, accepted_at=accepted_at,
                      restored=bool(restore), supersedes=supersedes,
                      receipt=receipt(key, org, pid, stream, seq, version, accepted_at, content))
        if restore:
            fields.update(restored_at=ts, receipt_seq=held_seq)
        accepted_rec = _record(eid, ACCEPTED, org, ts, fields)
        c.execute("INSERT INTO decisions (org, seq, proposal, event_id, kind, stream, stream_version, state, restored,"
                  " supersedes, machine, accepted_at, record) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (org, seq, pid, eid, "answer", stream, version, "accepted", 1 if restore else 0, supersedes,
                   m["machine_id"], accepted_at, json.dumps(accepted_rec, ensure_ascii=False)))
        answer = {"state": "accepted", "decision": accepted_rec}
        c.execute("INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?)", (pid, org, stream, sha, "accepted", 200,
                                                                     json.dumps(answer), m["machine_id"], ts))
        _feed(c, org, accepted_rec, now)
        sightings = [(k, h, org, "decision", {"count": n, "first_ts": ts, "last_ts": ts,
                                               "location": f"decision:{pid}"}) for (k, h), n in found.items()]
    of_server.queue_server_events(app.store, app.buffer, [_row(app.ship_cfg["tenant"], accepted_rec)], sightings)
    return 200, answer
