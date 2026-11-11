"""Relay and presence (docs/server.md, "Relay and presence"; mandate M4). Owned by of_server.

Addresses are `<org>:<position>`. A machine publishes the positions it holds (presence, TTL
120 s); messages to a position wait on the server until a machine presenting that position
long-polls, then are leased to it for 60 s until acknowledged (at-least-once; receivers dedupe
by msg_id). Message text is masked before it is stored or delivered: values never enter context
(RFC 0002 principle 1). Authority across organizations comes only from grants that verify.
"""

import calendar
import hashlib
import json
import re
import threading
import time

import of_events

PRESENCE_TTL = 120
LEASE = 60
MAX_WAIT = 30
MAX_FAILURES = 5
BACKOFF = 30          # seconds after the first failed delivery, doubling, at most BACKOFF_MAX
BACKOFF_MAX = 600
MESSAGE_TTL = 14 * 24 * 3600
MAX_TEXT = 64 * 1024
POSITION_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")  # AGENTS.md, agent names (herdr's rule)
KINDS = ("agent", "human")
PRIORITIES = ("normal", "fyi", "urgent")  # fyi: never offered to an agent's relay, read with `of inbox --mine`

DDL = """
CREATE TABLE IF NOT EXISTS presence (machine_id TEXT, org TEXT, position TEXT, session_id TEXT, state TEXT,
  pane TEXT, kind TEXT, updated REAL, PRIMARY KEY (machine_id, org, position));
CREATE TABLE IF NOT EXISTS messages (seq INTEGER PRIMARY KEY AUTOINCREMENT, msg_id TEXT UNIQUE, body_sha TEXT,
  to_org TEXT, to_position TEXT, from_org TEXT, from_position TEXT, text TEXT, reply_to TEXT, grant_id TEXT,
  authority TEXT, authority_reason TEXT, sender_machine TEXT, sender_person TEXT, created_at TEXT, created REAL,
  state TEXT, leased_to TEXT, lease_until REAL, attempts INTEGER DEFAULT 0, failures INTEGER DEFAULT 0,
  done_at TEXT, reason TEXT, priority TEXT DEFAULT 'normal');
CREATE INDEX IF NOT EXISTS messages_to ON messages (to_org, to_position, state);
CREATE INDEX IF NOT EXISTS messages_from ON messages (from_org, from_position, state);
"""


def registered_holder(path, position):
    """holder.session of a position (by name or alias) in a registry file, or None."""
    import os
    import yaml
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        doc = yaml.safe_load(fh) or {}
    for p in doc.get("positions") or []:
        if p.get("name") == position or position in (p.get("aliases") or []):
            return ((p.get("holder") or {}).get("session")) or None
    return None


_TAKE_COLS = ("msg_id, to_org, to_position, from_org, from_position, text, reply_to, authority, authority_reason,"
              " grant_id, sender_machine, sender_person, created_at, attempts, state, lease_until, priority")


def _taken_fields(r):
    """A message row as `GET /v1/messages/queued` and `take` return it: the long-poll's fields,
    plus `state` (`leased` only under a live lease) and `lease_s`."""
    (msg_id, to_org, to_pos, f_org, f_pos, text, reply_to, auth, why, grant, s_machine, s_person, created_at,
     attempts, state, lease_until, priority) = r
    left = (lease_until or 0) - time.time()
    live = state == "leased" and left > 0
    return {"msg_id": msg_id, "to": f"{to_org}:{to_pos}", "from": f"{f_org}:{f_pos}", "text": text,
            "reply_to": reply_to, "authority": auth, "authority_reason": why, "grant_id": grant,
            "sender_machine": s_machine, "sender_person": s_person, "created_at": created_at, "attempt": attempts,
            "kind": "agent", "state": "leased" if live else ("queued" if state == "leased" else state),
            "lease_s": round(left, 1) if live else 0, "priority": priority or "normal"}


class RelayError(Exception):
    def __init__(self, status, typ, title, extra=None):
        super().__init__(title)
        self.status, self.typ, self.title, self.extra = status, typ, title, extra or {}


def parse_address(a):
    org, sep, pos = (a or "").partition(":")
    if not sep or not org or not pos:
        raise RelayError(400, "address", f"address must be <org>:<position>, got {a!r}")
    return org, pos


class Relay:
    """State lives in server.db; `notify` wakes long-polls when a message arrives."""

    def __init__(self, app):
        self.app = app
        self.cond = threading.Condition()
        with app.store.conn() as c:
            c.executescript(DDL)
            if "priority" not in [r[1] for r in c.execute("PRAGMA table_info(messages)")]:
                c.execute("ALTER TABLE messages ADD COLUMN priority TEXT DEFAULT 'normal'")

    # ------------------------------------------------------------ helpers

    def _machine_orgs(self, m):
        return set(json.loads(m["orgs"]))

    def _org_known(self, org):
        if self.app.served is not None:
            return org in self.app.served
        return org in self.app.router().db_by_org

    def _person(self, machine_id):
        with self.app.store.conn() as c:
            r = c.execute("SELECT person FROM machines WHERE machine_id = ?", (machine_id,)).fetchone()
        return r[0] if r else None

    def _event(self, org, name, payload):
        import of_server
        return of_server.server_event(self.app.ship_cfg["tenant"], org, name, payload)

    def _queue(self, rows, sightings=None):
        import of_server
        if rows or sightings:
            of_server.queue_server_events(self.app.store, self.app.buffer, rows, sightings)

    # ----------------------------------------------------------- presence

    def put_presence(self, m, body):
        holders = (body or {}).get("holders")
        if not isinstance(holders, list):
            raise RelayError(400, "bad_request", "holders must be a list")
        joined = self._machine_orgs(m)
        rows = []
        for h in holders:
            org, pos = h.get("org"), h.get("position")
            if not org or not pos:
                raise RelayError(400, "bad_request", "each holder needs org and position")
            if not m["tenant"] and org not in joined and self._org_known(org):
                raise RelayError(403, "forbidden", f"organization {org} not joined by this machine")
            # An organization not served here is accepted only as proof that this machine holds the
            # sender's position for messages across organizations; it is never listed.
            kind = h.get("kind") or "agent"
            if kind not in KINDS:
                raise RelayError(400, "bad_request", f"kind must be one of {KINDS}")
            rows.append((m["machine_id"], org, pos, h.get("session_id") or "", h.get("state") or "",
                         h.get("pane") or "", kind, time.time()))
        with self.app.store.tx() as c:
            c.execute("DELETE FROM presence WHERE machine_id = ?", (m["machine_id"],))
            c.executemany("INSERT OR REPLACE INTO presence VALUES (?,?,?,?,?,?,?,?)", rows)
        with self.cond:
            self.cond.notify_all()  # a holder appeared: waiting messages may now be deliverable
        return {"holders": len(rows), "ttl": PRESENCE_TTL, "queued": self._queued_counts(rows),
                "unsent": self._unsent(rows)}

    UNSENT_ITEMS = 10

    def _unsent(self, rows):
        """{"<org>:<position>": {"n", "items": [{"msg_id", "to", "created_at", "priority", "reason", "next_at",
        "present"}]}}: messages each position just presented sent that are not delivered yet, oldest first (at most
        UNSENT_ITEMS; fyi left out, its receiver reads it when it chooses). A sender sees a message that has not
        landed (2026-11-09, from a user organization: a GO waited 24 minutes unseen and a window was lost)."""
        out, now = {}, time.time()
        with self.app.store.conn() as c:
            for org, pos in sorted({(r[1], r[2]) for r in rows}):
                where = (" FROM messages WHERE from_org = ? AND from_position = ? AND state IN ('queued', 'leased')"
                         " AND COALESCE(priority, 'normal') != 'fyi'")
                n = c.execute("SELECT count(*)" + where, (org, pos)).fetchone()[0]
                if not n:
                    continue
                items = []
                for msg_id, to_org, to_pos, created_at, priority, state, reason, lease_until in c.execute(
                        "SELECT msg_id, to_org, to_position, created_at, priority, state, reason, lease_until"
                        + where + " ORDER BY seq LIMIT ?", (org, pos, self.UNSENT_ITEMS)).fetchall():
                    present = c.execute("SELECT 1 FROM presence WHERE org = ? AND position = ? AND updated > ?",
                                        (to_org, to_pos, now - PRESENCE_TTL)).fetchone() is not None
                    nxt = lease_until if state == "queued" and lease_until and lease_until > now else None
                    items.append({"msg_id": msg_id, "to": f"{to_org}:{to_pos}", "created_at": created_at,
                                  "priority": priority or "normal", "state": state, "reason": reason or "",
                                  "next_at": of_events.now_iso(nxt) if nxt else None, "present": present})
                out[f"{org}:{pos}"] = {"n": n, "items": items}
        return out

    def _queued_counts(self, rows):
        """{"<org>:<position>": {"n", "oldest"}} of messages not yet delivered, for the positions
        just presented (only those with any); the client's Stop-hook line reads it."""
        out = {}
        with self.app.store.conn() as c:
            for org, pos in sorted({(r[1], r[2]) for r in rows}):
                n, oldest = c.execute("SELECT count(*), min(created_at) FROM messages WHERE to_org = ? AND"
                                      " to_position = ? AND state IN ('queued', 'leased')", (org, pos)).fetchone()
                if n:
                    out[f"{org}:{pos}"] = {"n": n, "oldest": oldest}
        return out

    def get_presence(self, m, org):
        if not org:
            raise RelayError(400, "bad_request", "org is required")
        if not m["tenant"] and org not in self._machine_orgs(m):
            raise RelayError(403, "forbidden", f"organization {org} not joined by this machine")
        now = time.time()
        with self.app.store.conn() as c:
            rows = c.execute("SELECT p.machine_id, p.org, p.position, p.session_id, p.state, p.pane, p.kind, p.updated,"
                             " m.person FROM presence p JOIN machines m ON m.machine_id = p.machine_id"
                             " WHERE p.org = ? AND p.updated > ? AND m.revoked_at IS NULL ORDER BY p.position",
                             (org, now - PRESENCE_TTL)).fetchall()
        keys = ("machine_id", "org", "position", "session_id", "state", "pane", "kind")
        return {"holders": [dict(zip(keys, r[:7]), age_s=round(now - r[7], 1), person=r[8]) for r in rows]}

    # ----------------------------------------------------------- messages

    def _holds(self, m, org, position, session=None):
        """Does this machine present the position now (with `session`, when given), and (when the
        server has the organization's registry) is its session the position's registered holder?"""
        with self.app.store.conn() as c:
            r = c.execute("SELECT session_id FROM presence WHERE machine_id = ? AND org = ? AND position = ?"
                          " AND updated > ?", (m["machine_id"], org, position, time.time() - PRESENCE_TTL)).fetchone()
        if not r:
            return False, "sender not holder: this machine does not present the position"
        if session is not None and r[0] != session:
            return False, "not holder: this machine presents the position with another session"
        files = self.app.cfg.get("positions_files") or {}
        if org in files:
            holder = registered_holder(files[org], position)
            if holder is not None and holder != r[0]:
                return False, "sender not holder: the session is not the position's registered holder"
        return True, "ok"

    def _authority(self, m, body, from_org, from_pos, to_org, to_pos):
        """('verified' | 'unverified_claim' | 'none', reason)."""
        grant_id, inline = body.get("grant_id"), body.get("grants") or []
        if not grant_id and not inline:
            return "none", "no grant"
        import of_grants
        if self.app._grants is None or time.time() - self.app._grants_at > 30:
            self.app._grants, self.app._grants_at = self.app._grants_loader(), time.time()
        pool = {k: list(v) for k, v in (self.app._grants or {}).items()}
        if inline:
            if not isinstance(inline, list) or not all(isinstance(g, dict) for g in inline):
                return "unverified_claim", "grants must be a list of grant objects"
            pool.setdefault(from_org, [])
            pool[from_org] += inline
            grant_id = grant_id or inline[0].get("id")
        # The receiver always counts (`to_positions`: "<org>:<position>" patterns, a grant limited
        # to some positions admits no other, in its own organization too); the organization only
        # across a boundary (`to_org`).
        action = {"to_positions": f"{to_org}:{to_pos}"}
        if to_org != from_org:
            action["to_org"] = to_org
        r = of_grants.verify_request(pool, self.app._grant_key(), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                     grant_id, f"{from_org}:{from_pos}", action)
        if r.get("valid") and r.get("decision") == "admit":
            holds, why = self._holds(m, from_org, from_pos)
            return ("verified", "ok") if holds else ("unverified_claim", why)
        return "unverified_claim", r.get("reason") or r.get("decision") or "not admitted"

    REPLY_WINDOW_S = 7 * 86400

    def _reply_across(self, m, body, from_org, from_pos, to_org, to_pos):
        """The reason a message across organizations may go without a grant, or None (docs/registry.md, Grants,
        "Where they live"; of_architect 2eeabd5, 2026-11-09): its reply_to names a message from to_org:to_pos that
        was delivered to from_org:from_pos at most 7 days ago, the sender holds from_pos, and its machine joined
        from_org. It goes only back to that sender, and it carries no authority. Returns (reason, None) or
        (None, why not): a 403 says why a reply did not count (2026-11-09: a shortened reply_to read as "no grant")."""
        rid = body.get("reply_to")
        if not rid:
            return None, None
        if not (m["tenant"] or from_org in self._machine_orgs(m)):
            return None, f"this machine has not joined {from_org}"
        with self.app.store.conn() as c:
            r = c.execute("SELECT from_org, from_position, to_org, to_position, state, done_at, created FROM messages"
                          " WHERE msg_id = ?", (rid,)).fetchone()
        if not r:
            return None, f"reply_to {rid} names no message (give its full id)"
        if (r[0], r[1], r[2], r[3], r[4]) != (to_org, to_pos, from_org, from_pos, "delivered"):
            return None, f"reply_to {rid} is not a message from {to_org}:{to_pos} delivered to {from_org}:{from_pos}"
        at = r[6]
        if r[5]:
            try:
                at = calendar.timegm(time.strptime(r[5][:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                pass
        if not at or time.time() - at > self.REPLY_WINDOW_S:
            return None, f"reply_to {rid} was delivered more than {self.REPLY_WINDOW_S // 86400} days ago"
        holds, held_why = self._holds(m, from_org, from_pos)
        if not holds:
            return None, held_why
        return f"a reply to {rid} (no grant needed; carries no authority)", None

    def post(self, m, body):
        body = body or {}
        msg_id, text = body.get("msg_id"), body.get("text")
        if not msg_id or not isinstance(text, str):
            raise RelayError(400, "bad_request", "msg_id and text are required")
        if len(text.encode()) > MAX_TEXT:
            raise RelayError(413, "too_large", f"text over {MAX_TEXT} bytes")
        to_org, to_pos = parse_address(body.get("to"))
        from_org, from_pos = parse_address(body.get("from"))
        priority = body.get("priority") or "normal"
        if priority not in PRIORITIES:
            raise RelayError(400, "bad_request", f"priority must be one of {', '.join(PRIORITIES)}")
        if not POSITION_NAME.match(to_pos):
            raise RelayError(400, "bad_position", f"position {to_pos[:80]!r} breaks the name rule [a-z][a-z0-9_-]{{0,31}}:"
                                                  " no position could ever take the message")
        sha = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.app.store.conn() as c:
            old = c.execute("SELECT body_sha, state, authority FROM messages WHERE msg_id = ?", (msg_id,)).fetchone()
        if old:
            if old[0] != sha:
                raise RelayError(409, "conflict", "msg_id reused with a different body")
            return {"msg_id": msg_id, "state": old[1], "authority": old[2]}
        if not self._org_known(to_org):
            raise RelayError(404, "unknown_org", f"organization {to_org} is not served here")
        joined = self._machine_orgs(m)
        if not m["tenant"] and to_org not in joined and from_org not in joined:
            raise RelayError(403, "forbidden", "this machine has joined neither the sender's nor the recipient's organization")
        authority, why = self._authority(m, body, from_org, from_pos, to_org, to_pos)
        if from_org != to_org and authority != "verified":
            reply, not_reply = self._reply_across(m, body, from_org, from_pos, to_org, to_pos)
            if not reply:
                raise RelayError(403, "forbidden", f"a message across organizations needs a grant with to_org: {why}"
                                 + (f"; not a reply: {not_reply}" if not_reply else ""))
            authority, why = "none", reply  # a reply carries no authority: no grant is resolved in it
            body = dict(body, grant_id=None)
        if from_org == to_org and not m["tenant"] and from_org not in joined:
            raise RelayError(403, "forbidden", f"organization {from_org} not joined by this machine")
        masked = self.app.masker.text(text)
        found = self.app.masker.take()
        person = self._person(m["machine_id"])
        now = time.time()
        with self.app.store.conn() as c:
            c.execute("INSERT INTO messages (msg_id, body_sha, to_org, to_position, from_org, from_position, text,"
                      " reply_to, grant_id, authority, authority_reason, sender_machine, sender_person, created_at,"
                      " created, state, priority) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'queued', ?)",
                      (msg_id, sha, to_org, to_pos, from_org, from_pos, masked, body.get("reply_to"),
                       body.get("grant_id") or None, authority, why, m["machine_id"], person,
                       of_events.now_iso(), now, priority))
        sent = {"msg_id": msg_id, "to": f"{to_org}:{to_pos}", "from": f"{from_org}:{from_pos}",
                "authority": authority, "reply_to": body.get("reply_to"), "sender_machine": m["machine_id"],
                "sender_person": person, "chars": len(text), "secrets_masked": sum(found.values()), "priority": priority}
        rows = [self._event(to_org, "of.message_sent", dict(sent, text=masked))]
        if from_org != to_org and self._org_known(from_org):
            rows.append(self._event(from_org, "of.message_sent", sent))   # slim: no text
        ts = of_events.now_iso()
        self._queue(rows, [(k, h, to_org, "message", {"count": n, "first_ts": ts, "last_ts": ts,
                                                      "location": f"message:{msg_id}"}) for (k, h), n in found.items()])
        with self.cond:
            self.cond.notify_all()
        return {"msg_id": msg_id, "state": "queued", "authority": authority, "authority_reason": why,
                "priority": priority}

    # fyi is never offered to an agent's relay (never typed into a pane): the agent reads it with
    # `of inbox --mine`; a human's relay still gets it, for `of inbox`.
    _OFFERED = "(COALESCE({m}.priority, 'normal') != 'fyi' OR p.kind = 'human')"
    _DUE = "({m}.lease_until IS NULL OR {m}.lease_until < ? OR {m}.leased_to = ?)"

    def _deliverable(self, machine_id):
        """Lease the waiting messages for positions this machine presents now. A receiver's queue goes as a
        whole (2026-11-09, from a user organization: each deferred message had its own retry time, the
        first one due was typed alone and the client's 15-minute window held the rest, a GO among them, for
        another 15 minutes): when one message for a position is due, its other queued messages that only
        wait out a deferral (no failure counted) are leased with it, oldest first. A failed one keeps its
        back-off."""
        now = time.time()
        with self.app.store.tx() as c:
            rows = c.execute(
                "SELECT m.seq, m.msg_id, m.to_org, m.to_position, m.from_org, m.from_position, m.text, m.reply_to,"
                " m.authority, m.authority_reason, m.grant_id, m.sender_machine, m.sender_person, m.created_at, m.attempts, p.kind,"
                " m.priority"
                " FROM messages m JOIN presence p ON p.org = m.to_org AND p.position = m.to_position"
                " WHERE p.machine_id = ? AND p.updated > ? AND m.state IN ('queued', 'leased')"
                " AND " + self._OFFERED.format(m="m") +
                " AND (" + self._DUE.format(m="m") +
                " OR (m.state = 'queued' AND m.leased_to IS NULL AND m.failures = 0 AND EXISTS (SELECT 1 FROM messages d"
                " WHERE d.to_org = m.to_org AND d.to_position = m.to_position AND d.state IN ('queued', 'leased')"
                " AND " + self._DUE.format(m="d") + " AND " + self._OFFERED.format(m="d") + ")))"
                " ORDER BY m.seq LIMIT 100",
                (machine_id, now - PRESENCE_TTL, now, machine_id, now, machine_id)).fetchall()
            for r in rows:
                c.execute("UPDATE messages SET state = 'leased', leased_to = ?, lease_until = ?, attempts = attempts + 1"
                          " WHERE seq = ?", (machine_id, now + LEASE, r[0]))
        keys = ("msg_id", "to_org", "to_position", "from_org", "from_position", "text", "reply_to", "authority",
                "authority_reason", "grant_id", "sender_machine", "sender_person", "created_at", "attempt", "kind", "priority")
        out = []
        for r in rows:
            d = dict(zip(keys, r[1:]))
            d["to"], d["from"] = f"{d.pop('to_org')}:{d.pop('to_position')}", f"{d.pop('from_org')}:{d.pop('from_position')}"
            d["attempt"] += 1
            if d.get("reply_to"):  # who sent the message this one answers: the client types a reply to the
                with self.app.store.conn() as c:  # receiver's own message at once (docs/guard.md R9)
                    o = c.execute("SELECT from_org, from_position FROM messages WHERE msg_id = ?",
                                  (d["reply_to"],)).fetchone()
                d["reply_to_from"] = f"{o[0]}:{o[1]}" if o else None
            out.append(d)
        return out

    def poll(self, m, wait):
        wait = max(0.0, min(float(wait or 0), MAX_WAIT))
        deadline = time.time() + wait
        while True:
            msgs = self._deliverable(m["machine_id"])
            left = deadline - time.time()
            if msgs or left <= 0:
                return {"messages": msgs}
            with self.cond:
                self.cond.wait(min(left, 5))

    # ------------------------------------------------- an agent takes its own messages

    def _holder(self, m, to, session):
        org, pos = parse_address(to)
        if not session:
            raise RelayError(400, "bad_request", "session is required")
        ok, why = self._holds(m, org, pos, session)
        if not ok:
            raise RelayError(403, "not_holder", why)
        with self.app.store.conn() as c:
            kind = (c.execute("SELECT kind FROM presence WHERE machine_id = ? AND org = ? AND position = ?",
                              (m["machine_id"], org, pos)).fetchone() or ["agent"])[0]
        if kind == "human":
            raise RelayError(403, "not_holder", "a person's position keeps its local inbox (of inbox)")
        return org, pos

    def queued(self, m, to, session):
        """The messages waiting for a position this caller holds, oldest first; nothing is leased."""
        org, pos = self._holder(m, to, session)
        with self.app.store.conn() as c:
            rows = c.execute(f"SELECT {_TAKE_COLS} FROM messages WHERE to_org = ? AND to_position = ?"
                             " AND state IN ('queued', 'leased') ORDER BY seq LIMIT 100", (org, pos)).fetchall()
        return {"messages": [_taken_fields(r) for r in rows]}

    def take(self, m, msg_id, body):
        """Mark a message delivered because its receiver took it (`of inbox --mine`). Only without
        a live lease: a leased message is being injected by the relay right now (409 leased)."""
        session = (body or {}).get("session")
        with self.app.store.conn() as c:
            r = c.execute("SELECT to_org, to_position FROM messages WHERE msg_id = ?", (msg_id,)).fetchone()
        if not r:
            raise RelayError(404, "not_found", "unknown message")
        if (body or {}).get("to") not in (None, f"{r[0]}:{r[1]}"):
            raise RelayError(400, "bad_request", "the message is not addressed to that position")
        org, pos = self._holder(m, f"{r[0]}:{r[1]}", session)
        now = time.time()
        with self.app.store.tx() as c:
            row = c.execute(f"SELECT {_TAKE_COLS} FROM messages WHERE msg_id = ?", (msg_id,)).fetchone()
            state, lease_until = row[-3], row[-2]
            if state in ("delivered", "dead", "expired"):
                return {**_taken_fields(row), "state": state, "already": True}
            if state == "leased" and (lease_until or 0) > now:
                left = max(1, int(lease_until - now + 0.999))
                raise RelayError(409, "leased", "the relay is delivering this message into the pane now",
                                 {"retry_after": left})
            c.execute("UPDATE messages SET state = 'delivered', leased_to = ?, lease_until = NULL, reason = 'taken',"
                      " done_at = ? WHERE msg_id = ?", (m["machine_id"], of_events.now_iso(), msg_id))
        self._queue([self._event(org, "of.message_delivered", {
            "msg_id": msg_id, "to": f"{org}:{pos}", "machine_id": m["machine_id"], "reason": "taken",
            "failures": 0, "via": "taken", "session_id": session})])
        return {**_taken_fields(row), "state": "delivered", "already": False}

    def ack(self, m, msg_id, body):
        status, reason = (body or {}).get("status"), (body or {}).get("reason")
        if status not in ("delivered", "failed", "deferred"):
            raise RelayError(400, "bad_request", "status must be delivered, failed or deferred")
        with self.app.store.conn() as c:
            r = c.execute("SELECT to_org, leased_to, state, failures, to_position FROM messages WHERE msg_id = ?",
                          (msg_id,)).fetchone()
        if not r:
            raise RelayError(404, "not_found", "unknown message")
        to_org, leased_to, state, failures, to_pos = r
        if state in ("delivered", "dead", "expired"):
            return {"msg_id": msg_id, "state": state}
        if leased_to != m["machine_id"]:
            raise RelayError(409, "conflict", "the message is not leased to this machine")
        if status == "deferred":
            # The receiver is busy (not idle): offer it again later, without counting a failure.
            # A message deferred for MESSAGE_TTL expires as usual.
            try:
                after = float((body or {}).get("retry_after") or BACKOFF)
            except (TypeError, ValueError):
                after = BACKOFF
            with self.app.store.conn() as c:
                c.execute("UPDATE messages SET state = 'queued', leased_to = NULL, lease_until = ?, reason = ?"
                          " WHERE msg_id = ?", (time.time() + max(5.0, min(after, BACKOFF_MAX)),
                                                (reason or "busy")[:300], msg_id))
            return {"msg_id": msg_id, "state": "queued"}
        if status == "delivered":
            new, ev = "delivered", "of.message_delivered"
        else:
            failures += 1
            new, ev = ("dead", "of.message_failed") if failures >= MAX_FAILURES else ("queued", None)
        # A failed delivery is offered again only after a back-off, so a receiver that fails at
        # once (a bad tool call, a pane gone) cannot burn through every attempt in a second.
        retry_at = time.time() + min(BACKOFF * 2 ** (failures - 1), BACKOFF_MAX) if new == "queued" else None
        with self.app.store.conn() as c:
            c.execute("UPDATE messages SET state = ?, failures = ?, reason = ?, lease_until = ?, leased_to ="
                      " CASE WHEN ? = 'queued' THEN NULL ELSE leased_to END, done_at = CASE WHEN ? IN"
                      " ('delivered','dead') THEN ? ELSE done_at END WHERE msg_id = ?",
                      (new, failures, (reason or "")[:300], retry_at, new, new, of_events.now_iso(), msg_id))
        if ev:
            self._queue([self._event(to_org, ev, {"msg_id": msg_id, "to": f"{to_org}:{to_pos}",
                                                  "machine_id": m["machine_id"], "reason": (reason or "")[:300],
                                                  "failures": failures})])
        return {"msg_id": msg_id, "state": new}

    def requeue(self, msg_ids=None, state="dead"):
        """Offer dead (or expired) messages again, with their failures reset: after the receiving
        side is fixed. Returns the ids requeued."""
        with self.app.store.conn() as c:
            rows = [r[0] for r in c.execute("SELECT msg_id FROM messages WHERE state = ?", (state,))]
            ids = [i for i in rows if not msg_ids or i in msg_ids]
            for i in ids:
                c.execute("UPDATE messages SET state = 'queued', failures = 0, lease_until = NULL, leased_to = NULL,"
                          " done_at = NULL, reason = NULL WHERE msg_id = ?", (i,))
        with self.cond:
            self.cond.notify_all()
        return ids

    def drop(self, msg_ids, reason):
        """The server host drops queued messages nobody can ever take (a target typed wrong): they become
        `expired` with reason `dropped: <reason>`; `requeue(ids, state="expired")` undoes it."""
        with self.app.store.conn() as c:
            rows = [r for i in msg_ids for r in c.execute(
                "SELECT msg_id, to_org, to_position FROM messages WHERE msg_id = ? AND state IN ('queued', 'leased')",
                (i,)).fetchall()]
            for msg_id, _, _ in rows:
                c.execute("UPDATE messages SET state = 'expired', done_at = ?, leased_to = NULL, lease_until = NULL,"
                          " reason = ? WHERE msg_id = ?", (of_events.now_iso(), f"dropped: {reason}", msg_id))
        self._queue([self._event(o, "of.message_expired", {"msg_id": i, "to": f"{o}:{p}", "reason": f"dropped: {reason}"})
                     for i, o, p in rows])
        return [r[0] for r in rows]

    def expire(self):
        """Messages nobody received within MESSAGE_TTL."""
        cutoff = time.time() - MESSAGE_TTL
        with self.app.store.conn() as c:
            rows = c.execute("SELECT msg_id, to_org, to_position FROM messages WHERE state IN ('queued', 'leased')"
                             " AND created < ?", (cutoff,)).fetchall()
            for msg_id, _, _ in rows:
                c.execute("UPDATE messages SET state = 'expired', done_at = ? WHERE msg_id = ?",
                          (of_events.now_iso(), msg_id))
        self._queue([self._event(o, "of.message_expired", {"msg_id": i, "to": f"{o}:{p}"}) for i, o, p in rows])
        return len(rows)
