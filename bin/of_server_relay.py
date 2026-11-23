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
  done_at TEXT, reason TEXT, priority TEXT DEFAULT 'normal', moved_from TEXT, moved_by TEXT);
CREATE INDEX IF NOT EXISTS messages_to ON messages (to_org, to_position, state);
CREATE INDEX IF NOT EXISTS messages_from ON messages (from_org, from_position, state);
CREATE TABLE IF NOT EXISTS position_aliases (org TEXT, alias TEXT, position TEXT, machine_id TEXT, updated REAL,
  PRIMARY KEY (org, alias));
CREATE TABLE IF NOT EXISTS presented (org TEXT, position TEXT, last_seen REAL, PRIMARY KEY (org, position));
CREATE TABLE IF NOT EXISTS sent_across (msg_id TEXT PRIMARY KEY, from_org TEXT, from_position TEXT, to_org TEXT,
  to_position TEXT, sender_machine TEXT, created REAL);
"""
ALIAS_TTL = 30 * 86400  # an alias a holder presented within this many seconds still readdresses a new message
ALIAS_QUIET = 86400     # a name a holder presented within this many seconds is never taken as an alias


def registered_aliases(path, position):
    """The aliases a registry file lists for a position, or None when the file is not there."""
    import os
    import yaml
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        doc = yaml.safe_load(fh) or {}
    return next((list(p.get("aliases") or []) for p in doc.get("positions") or [] if p.get("name") == position), [])


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


LIVE_STATUS_OFF = ("finished", "parked")


def _live(positions):
    """The registry's active positions: status `active` or none; an unknown one (a typo such as `parkd`) is not."""
    return [p for p in positions if isinstance(p, dict) and (p.get("status") or "active") == "active"]


def registry_entry(positions, name):
    """The registry's entry NAME names (its name, or an alias it lists), or None."""
    return next((p for p in positions if isinstance(p, dict)
                 and (p.get("name") == name or name in [str(a) for a in p.get("aliases") or []])), None)


def held_by_person(entry):
    """A registry entry whose holder is a person (holder.person, or runtime human): its mail is that person's."""
    h = (entry or {}).get("holder") or {}
    return bool(isinstance(h, dict) and (h.get("person") or h.get("runtime") == "human"))


def queue_managers(positions, units, receiver):
    """Who may move or drop RECEIVER's queued messages, from its organization's registry entries (POSITIONS, as the
    file lists them) and its `units:`: the structure keeper (its first active position of role `org`, else of role
    `architect`, as of_pulse.structure_keeper reads it) and the owner of the receiver's unit (its `unit:`, else the
    unit whose `systems` list its system) while that owner is active. One function for the server and `of inbox`
    (of_secrets-review on organic-framework PR 375: the two chose different keepers)."""
    live = _live(positions)
    names = {p.get("name") for p in live}
    out = set()
    for role in ("org", "architect"):
        keeper = next((p.get("name") for p in live if p.get("role") == role), None)
        if keeper:
            out.add(keeper)
            break
    units = units if isinstance(units, dict) else {}
    p = next((x for x in positions if isinstance(x, dict)
              and (x.get("name") == receiver or receiver in (x.get("aliases") or []))), None)
    if p is not None:
        uid = p.get("unit") or next((k for k, u in units.items() if isinstance(u, dict)
                                     and p.get("system") in (u.get("systems") or [])), None)
        owner = (units.get(uid) or {}).get("owner") if uid else None
        if owner in names:
            out.add(owner)
    return out


def live_position(positions, name):
    """The active position NAME names (its name, or an alias it lists), or None: a moved queue goes only there."""
    return next((p.get("name") for p in _live(positions)
                 if p.get("name") == name or name in [str(a) for a in p.get("aliases") or []]), None)


def registry_file(cfg, org, orgs_file=None):
    """ORG's registry/positions.yaml: `positions_files` in the server's config, else the organization's checkout in
    orgs.yaml, as the server finds grants. None when neither names one."""
    import os
    import yaml
    files = cfg.get("positions_files") or {}
    if org in files:
        return os.path.expanduser(files[org]) if isinstance(files[org], str) else None
    orgs_file = orgs_file or os.environ.get("OF_ORGS") or os.path.expanduser("~/.config/of/orgs.yaml")
    try:
        with open(orgs_file) as fh:
            doc = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return None
    base = os.path.dirname(os.path.abspath(orgs_file))
    hits = [o for o in doc.get("orgs") or [] if isinstance(o, dict) and o.get("name") == org and o.get("path")]
    if not hits:
        return None  # the last entry wins, as when the server loads grants (of_grants.load)
    return os.path.join(base, os.path.expanduser(str(hits[-1]["path"])), "registry", "positions.yaml")


_TAKE_COLS = ("msg_id, to_org, to_position, from_org, from_position, text, reply_to, authority, authority_reason,"
              " grant_id, sender_machine, sender_person, created_at, attempts, state, lease_until, priority,"
              " moved_from, moved_by")


def _taken_fields(r):
    """A message row as `GET /v1/messages/queued` and `take` return it: the long-poll's fields,
    plus `state` (`leased` only under a live lease) and `lease_s`."""
    (msg_id, to_org, to_pos, f_org, f_pos, text, reply_to, auth, why, grant, s_machine, s_person, created_at,
     attempts, state, lease_until, priority, moved_from, moved_by) = r
    left = (lease_until or 0) - time.time()
    live = state == "leased" and left > 0
    return {"msg_id": msg_id, "to": f"{to_org}:{to_pos}", "from": f"{f_org}:{f_pos}", "text": text,
            "reply_to": reply_to, "authority": auth, "authority_reason": why, "grant_id": grant,
            "sender_machine": s_machine, "sender_person": s_person, "created_at": created_at, "attempt": attempts,
            "kind": "agent", "state": "leased" if live else ("queued" if state == "leased" else state),
            "lease_s": round(left, 1) if live else 0, "priority": priority or "normal", "moved_from": moved_from,
            "moved_by": moved_by}


class RelayError(Exception):
    def __init__(self, status, typ, title, extra=None):
        super().__init__(title)
        self.status, self.typ, self.title, self.extra = status, typ, title, extra or {}


def parse_address(a):
    org, sep, pos = (a or "").partition(":")
    if not sep or not org or not pos:
        raise RelayError(400, "address", f"address must be <org>:<position>, got {a!r}")
    return org, pos


MIGRATIONS = (("priority", "TEXT DEFAULT 'normal'"), ("moved_from", "TEXT"), ("moved_by", "TEXT"))  # on messages


def migrate(c):
    """The columns added to messages after its DDL, in an older store (shared with of server import)."""
    have = [r[1] for r in c.execute("PRAGMA table_info(messages)")]
    for col, decl in MIGRATIONS:
        if col not in have:
            c.execute(f"ALTER TABLE messages ADD COLUMN {col} {decl}")


class Relay:
    """State lives in server.db; `notify` wakes long-polls when a message arrives."""

    def __init__(self, app):
        self.app = app
        self.cond = threading.Condition()
        with app.store.conn() as c:
            c.executescript(DDL)
            migrate(c)

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
        merge = (body or {}).get("merge") is True
        with self.app.store.tx() as c:
            put = rows
            if merge:  # `of send`: add or update these rows, drop none; a row with no session never replaces one this
                # machine presents now (a job sending as a position its live agent holds; of_review on PR 251)
                live = time.time() - PRESENCE_TTL
                put = [r for r in rows if r[3] or not c.execute(
                    "SELECT 1 FROM presence WHERE machine_id = ? AND org = ? AND position = ? AND updated > ?",
                    (r[0], r[1], r[2], live)).fetchone()]
            else:
                c.execute("DELETE FROM presence WHERE machine_id = ?", (m["machine_id"],))
            c.executemany("INSERT OR REPLACE INTO presence VALUES (?,?,?,?,?,?,?,?)", put)
            # a name a holder presents is a position, never an alias: one taken while it was away is void at once
            # (of_secrets-review on organic-framework PR 164)
            c.executemany("INSERT OR REPLACE INTO presented VALUES (?, ?, ?)", [(r[1], r[2], r[7]) for r in rows])
            c.executemany("DELETE FROM position_aliases WHERE org = ? AND alias = ?", [(r[1], r[2]) for r in rows])
        self._aliases(m, holders)
        with self.cond:
            self.cond.notify_all()  # a holder appeared: waiting messages may now be deliverable
        return {"holders": len(rows), "ttl": PRESENCE_TTL, "queued": self._queued_counts(rows),
                "unsent": self._unsent(rows)}

    def _aliases(self, m, holders):
        """A position's older names (its registry `aliases`, AGENTS.md: older names live only as aliases): a message
        sent to one reaches the position. Each alias a holder presents is kept for its organization, and the
        messages still queued to it are readdressed to the position; a new message to it is stored under the
        position (post). A user organization's issue 07ba3788a3c3, 2026-11-14: a message to a name that had become
        an alias stayed queued, and its holder's take was refused not_holder. An alias is taken only for an
        organization this machine has joined, never over a name a holder presents now, and, when the server has
        the organization's registry, only as that registry lists it."""
        joined, now = self._machine_orgs(m), time.time()
        files = self.app.cfg.get("positions_files") or {}
        with self.app.store.tx() as c:
            # a name presented within ALIAS_QUIET is taken by no alias: a holder away for a restart, a move or a
            # night keeps it (of_secrets-review on PR 164); without the registry this is what limits a joined
            # machine, which could present the name itself anyway
            presented = {(o, p) for o, p in c.execute("SELECT org, position FROM presented WHERE last_seen > ?",
                                                      (now - ALIAS_QUIET,))}
            presented |= {(o, p) for o, p in c.execute("SELECT org, position FROM presence WHERE updated > ?",
                                                       (now - PRESENCE_TTL,))}
            for h in holders:
                org, pos, aliases = h.get("org"), h.get("position"), h.get("aliases")
                if not isinstance(aliases, list) or not (m["tenant"] or org in joined):
                    continue
                listed = registered_aliases(files[org], pos) if org in files else None
                for a in aliases:
                    if not isinstance(a, str) or not POSITION_NAME.match(a) or a == pos or (org, a) in presented \
                            or (listed is not None and a not in listed):
                        continue
                    c.execute("INSERT OR REPLACE INTO position_aliases VALUES (?, ?, ?, ?, ?)",
                              (org, a, pos, m["machine_id"], now))
                    c.execute("UPDATE messages SET to_position = ?, reason = COALESCE(reason, ?) WHERE to_org = ?"
                              " AND to_position = ? AND state = 'queued'",
                              (pos, f"sent to {a}, an alias of {pos}", org, a))

    def _canonical(self, org, pos):
        """The position an alias names, or the name itself (always while a holder presents that name)."""
        now = time.time()
        with self.app.store.conn() as c:
            if c.execute("SELECT 1 FROM presented WHERE org = ? AND position = ? AND last_seen > ?",
                         (org, pos, now - ALIAS_QUIET)).fetchone():
                return pos
            r = c.execute("SELECT position FROM position_aliases WHERE org = ? AND alias = ? AND updated > ?",
                          (org, pos, now - ALIAS_TTL)).fetchone()
        return r[0] if r else pos

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
            # The deciding grant must be one for messages: it names the organization a message crosses into
            # (`to_org`), or, within its own organization, the positions (`to_positions`). A grant for something
            # else (finance data, a keeper's line) leaves both open, so it matched every message (2026-11-19,
            # of_server, found while adding a keeper scope: a data grant verified a message to any organization).
            dim = "to_org" if to_org != from_org else "to_positions"
            d = next((x for gs in pool.values() for x in gs
                      if x.get("id") == r.get("deciding_grant") and x.get("subject") == from_pos), None)
            if not (d and (d.get("scope") or {}).get(dim)):
                return "unverified_claim", f"grant {r.get('deciding_grant')} names no {dim}: it is not for messages"
            holds, why = self._holds(m, from_org, from_pos)
            return ("verified", "ok") if holds else ("unverified_claim", why)
        return "unverified_claim", r.get("reason") or r.get("decision") or "not admitted"

    REPLY_WINDOW_S = 7 * 86400
    # A message reaches its holder before the relay marks it delivered: `of inbox --mine` lists queued and leased
    # messages in full, and a take under a busy pane is kept on the machine until the relay's next ack. Only an
    # expired or failed one never reached it (2026-11-18: a founder question relayed across organizations sat leased
    # from 10:56 to 11:03Z while its busy receiver read it, took it and replied three times at 10:59Z, each refused;
    # a user organization's structure keeper, msg 01a1ee56-f4f7).
    NEVER_REACHED = {"expired": "it expired unread", "dead": "its delivery failed"}

    def _reply_across(self, m, body, from_org, from_pos, to_org, to_pos):
        """The reason a message across organizations may go without a grant, or None (docs/registry.md, Grants,
        "Where they live"; of_architect 2eeabd5, 2026-11-09): its reply_to names a message from to_org:to_pos that
        was delivered to from_org:from_pos at most 7 days ago, the sender holds from_pos, and its machine joined
        from_org. It goes only back to that sender, and it carries no authority. Returns (reason, None) or
        (None, why not): a 403 says why a reply did not count (2026-11-09: a shortened reply_to read as "no grant").
        A queued or leased message counts as well as a delivered one (NEVER_REACHED)."""
        rid = body.get("reply_to")
        if not rid:
            return None, None
        if not (m["tenant"] or from_org in self._machine_orgs(m)):
            return None, f"this machine has not joined {from_org}"
        with self.app.store.conn() as c:
            r = c.execute("SELECT from_org, from_position, to_org, to_position, state, done_at, created FROM messages"
                          " WHERE msg_id = ?", (rid,)).fetchone()
            h = None if r else c.execute("SELECT from_org, from_position, to_org, to_position, created FROM sent_across"
                                         " WHERE msg_id = ?", (rid,)).fetchone()
        if not r and h:
            return self._reply_to_header(m, rid, h, from_org, from_pos, to_org, to_pos)
        if not r:
            return None, f"reply_to {rid} names no message (give its full id)"
        if (r[0], r[1], r[2], r[3]) != (to_org, to_pos, from_org, from_pos):
            return None, f"reply_to {rid} is not a message from {to_org}:{to_pos} to {from_org}:{from_pos}"
        if r[4] not in ("queued", "leased", "delivered"):
            return None, (f"reply_to {rid} never reached {from_org}:{from_pos}: "
                          f"{self.NEVER_REACHED.get(r[4], f'its state is {r[4]}')}")
        at = r[6]
        if r[5]:
            try:
                at = calendar.timegm(time.strptime(r[5][:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                pass
        if not at or time.time() - at > self.REPLY_WINDOW_S:
            return None, f"reply_to {rid} was sent or delivered more than {self.REPLY_WINDOW_S // 86400} days ago"
        holds, held_why = self._holds(m, from_org, from_pos)
        if not holds:
            return None, held_why
        return f"a reply to {rid} (no grant needed; carries no authority)", None

    def _reply_to_header(self, m, rid, h, from_org, from_pos, to_org, to_pos):
        """_reply_across for a reply_to this server knows only by its header (`sent`): a message its sender's machine
        posted to the receiver's server, another one. The header has no delivery state, so "not expired or failed"
        cannot be checked here: the 7 days count from the header's arrival (of_architect, msg 01a206a9-0644)."""
        if (h[0], h[1], h[2], h[3]) != (to_org, to_pos, from_org, from_pos):
            return None, f"reply_to {rid} is not a message from {to_org}:{to_pos} to {from_org}:{from_pos}"
        if not h[4] or time.time() - h[4] > self.REPLY_WINDOW_S:
            return None, f"reply_to {rid} was sent more than {self.REPLY_WINDOW_S // 86400} days ago"
        holds, held_why = self._holds(m, from_org, from_pos)
        if not holds:
            return None, held_why
        return f"a reply to {rid}, sent from here to another server (no grant needed; carries no authority)", None

    def sent(self, m, body):
        """Keep the header of a message this machine posted to another organization's server, on the server of its
        sender's organization: its id, its two ends and when the header came, never its text. A reply to it comes
        here, and the message itself is not here (of_architect, msg 01a206a9-0644, 2026-11-23: since one
        organization's server moved to another machine, every reply to it from a position with no grant was refused,
        "names no message"). Each server checks only its own records (docs/contours.md §2)."""
        body = body or {}
        msg_id = body.get("msg_id")
        if not isinstance(msg_id, str) or not msg_id or len(msg_id) > 64:
            raise RelayError(400, "bad_request", "msg_id is required")
        to_org, to_pos = parse_address(body.get("to"))
        from_org, from_pos = parse_address(body.get("from"))
        if from_org == to_org:
            raise RelayError(400, "bad_request", "a header is kept only for a message to another organization")
        if not self._org_known(from_org):
            raise RelayError(404, "unknown_org", f"organization {from_org} is not served here")
        if not (m["tenant"] or from_org in self._machine_orgs(m)):
            raise RelayError(403, "forbidden", f"this machine has not joined {from_org}")
        holds, why = self._holds(m, from_org, from_pos)
        if not holds:
            raise RelayError(403, "forbidden", why)
        ends = (from_org, from_pos, to_org, to_pos)
        with self.app.store.conn() as c:
            c.execute("DELETE FROM sent_across WHERE created < ?", (time.time() - self.REPLY_WINDOW_S,))
            old = c.execute("SELECT from_org, from_position, to_org, to_position FROM sent_across WHERE msg_id = ?",
                            (msg_id,)).fetchone()
            if old and tuple(old) != ends:
                raise RelayError(409, "conflict", "msg_id kept already with other ends")
            if not old:
                if c.execute("SELECT 1 FROM messages WHERE msg_id = ?", (msg_id,)).fetchone():
                    raise RelayError(409, "conflict", "msg_id names a message on this server")
                c.execute("INSERT INTO sent_across VALUES (?,?,?,?,?,?,?)",
                          (msg_id, *ends, m["machine_id"], time.time()))
        return {"msg_id": msg_id, "kept": True}

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
        to_pos = self._canonical(to_org, to_pos)  # an older name, kept as an alias, reaches the position
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

    def system_post(self, to, text, priority="fyi", sender="of_server"):
        """A message of-server sends itself (docs/health.md §3: its own checks told to their owner): from SENDER of the
        receiver's organization, with no authority, machine "of-server", queued and delivered like any other. The
        message id, or None when the receiver's organization is not served here."""
        to_org, to_pos = parse_address(to)
        if priority not in PRIORITIES or not POSITION_NAME.match(to_pos) or not self._org_known(to_org):
            return None
        msg_id = of_events._uuid7()
        body = {"msg_id": msg_id, "to": f"{to_org}:{to_pos}", "from": f"{to_org}:{sender}", "text": text,
                "priority": priority}
        sha = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        why = "of-server's own message (docs/health.md §3)"
        with self.app.store.conn() as c:
            c.execute("INSERT INTO messages (msg_id, body_sha, to_org, to_position, from_org, from_position, text,"
                      " authority, authority_reason, sender_machine, created_at, created, state, priority)"
                      " VALUES (?,?,?,?,?,?,?, 'none', ?, 'of-server', ?, ?, 'queued', ?)",
                      (msg_id, sha, to_org, to_pos, to_org, sender, text, why, of_events.now_iso(), time.time(),
                       priority))
        self._queue([self._event(to_org, "of.message_sent", {
            "msg_id": msg_id, "to": body["to"], "from": body["from"], "authority": "none", "reply_to": None,
            "sender_machine": "of-server", "sender_person": None, "chars": len(text), "secrets_masked": 0,
            "priority": priority, "text": text})])
        with self.cond:
            self.cond.notify_all()
        return msg_id

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
        moving = sorted(getattr(self.app, "moving", lambda: set())())
        hold = (f" AND m.to_org NOT IN ({','.join('?' * len(moving))}) AND m.from_org NOT IN "
                f"({','.join('?' * len(moving))})") if moving else ""  # a moving organization's rows stay as they are
        with self.app.store.tx() as c:
            rows = c.execute(
                "SELECT m.seq, m.msg_id, m.to_org, m.to_position, m.from_org, m.from_position, m.text, m.reply_to,"
                " m.authority, m.authority_reason, m.grant_id, m.sender_machine, m.sender_person, m.created_at, m.attempts, p.kind,"
                " m.priority, m.moved_from, m.moved_by"
                " FROM messages m JOIN presence p ON p.org = m.to_org AND p.position = m.to_position"
                " WHERE p.machine_id = ? AND p.updated > ? AND m.state IN ('queued', 'leased')"
                " AND " + self._OFFERED.format(m="m") +
                " AND (" + self._DUE.format(m="m") +
                " OR (m.state = 'queued' AND m.leased_to IS NULL AND m.failures = 0 AND EXISTS (SELECT 1 FROM messages d"
                " WHERE d.to_org = m.to_org AND d.to_position = m.to_position AND d.state IN ('queued', 'leased')"
                " AND " + self._DUE.format(m="d") + " AND " + self._OFFERED.format(m="d") + ")))"
                + hold + " ORDER BY m.seq LIMIT 100",
                (machine_id, now - PRESENCE_TTL, now, machine_id, now, machine_id, *moving, *moving)).fetchall()
            for r in rows:
                c.execute("UPDATE messages SET state = 'leased', leased_to = ?, lease_until = ?, attempts = attempts + 1"
                          " WHERE seq = ?", (machine_id, now + LEASE, r[0]))
        keys = ("msg_id", "to_org", "to_position", "from_org", "from_position", "text", "reply_to", "authority",
                "authority_reason", "grant_id", "sender_machine", "sender_person", "created_at", "attempt", "kind", "priority",
                "moved_from", "moved_by")
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

    QUEUED_LIMIT = 100

    def queued(self, m, to, session, newest=False):
        """The messages waiting for a position this caller holds, at most QUEUED_LIMIT: the urgent ones first, then
        the oldest (or, with NEWEST, the newest) of the rest; nothing is leased. `total` and `urgent` count the whole
        queue, so a backlog past the limit never hides a newer or an urgent message behind a count that reads as
        all (a user organization through its structure keeper, msg 01a203b5-c16a: a guard STOP and a runner hold
        sat past the hundred oldest)."""
        org, pos = self._holder(m, to, session)
        where = " FROM messages WHERE to_org = ? AND to_position = ? AND state IN ('queued', 'leased')"
        with self.app.store.conn() as c:
            total, urgent = c.execute("SELECT count(*), COALESCE(sum(priority = 'urgent'), 0)" + where,
                                      (org, pos)).fetchone()
            rows = c.execute(f"SELECT {_TAKE_COLS}" + where + " ORDER BY COALESCE(priority, 'normal') = 'urgent' DESC, "
                             + ("seq DESC" if newest else "seq") + " LIMIT ?", (org, pos, self.QUEUED_LIMIT)).fetchall()
        return {"messages": [_taken_fields(r) for r in rows], "total": int(total or 0), "urgent": int(urgent or 0)}

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
            row = c.execute(f"SELECT {_TAKE_COLS} FROM messages WHERE msg_id = ? AND to_org = ? AND to_position = ?",
                            (msg_id, org, pos)).fetchone()
            if row is None:  # moved to another position since the holder was checked (of_secrets-review on PR 375)
                raise RelayError(409, "moved", "the message was moved to another position meanwhile")
            state, lease_until = row[14], row[15]
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

    # -------------------------------- a structure keeper or a unit owner moves or drops a position's queued messages

    def _registry(self, org):
        """(positions, units) of ORG's registry, or a 403: without it the server cannot tell who keeps a queue, and a
        move or a drop must not fall back to any holder in the organization (of_secrets-review on organic-framework
        PR 375: the role check failed open on a server set up by default)."""
        import yaml
        path, doc, why = registry_file(self.app.cfg, org), None, None
        if not path:
            why = "neither positions_files nor this server's orgs.yaml names it"
        else:
            try:
                with open(path) as fh:
                    doc = yaml.safe_load(fh) or {}
            except OSError as e:
                why = f"it is not readable ({type(e).__name__})"
            except yaml.YAMLError:
                why = "it is not valid YAML now (an edit in progress?)"
        if why is None and not (isinstance(doc, dict) and isinstance(doc.get("positions"), list)):
            why = "it lists no positions"
        if why:
            raise RelayError(403, "no_registry", f"this server cannot read {org}'s registry, so it cannot tell who keeps"
                                                 f" its queues: {why}")
        return doc["positions"], doc.get("units")

    def _manager(self, m, org, by, session, receiver):
        """`by` may act on RECEIVER's queue in ORG: ORG's registry, which the server must be able to read, names `by`
        (or an alias of it) ORG's structure keeper or RECEIVER's unit owner (queue_managers); this machine presents
        `by` with SESSION; and no person holds RECEIVER, whose mail is that person's (of_review on organic-framework
        PR 375: a keeper could move a person's queued texts into an agent's pane). The registry is read before the
        holder check, so a broken file answers 403 no_registry, never 500. Returns (org, by's canonical position,
        RECEIVER's canonical position, the registry's positions)."""
        b_org, b_pos = parse_address(by)
        if b_org != org:
            raise RelayError(403, "forbidden", f"{by} is not a position of {org}: only its own organization moves or "
                                               "drops its messages")
        if not session:
            raise RelayError(400, "bad_request", "session is required")
        positions, units = self._registry(org)
        b_pos = (registry_entry(positions, b_pos) or {}).get("name") or b_pos
        entry = registry_entry(positions, receiver)
        receiver = (entry or {}).get("name") or receiver
        ok, why = self._holds(m, b_org, b_pos, session)
        if not ok:
            raise RelayError(403, "not_holder", why)
        registered = ((registry_entry(positions, b_pos) or {}).get("holder") or {})
        registered = registered.get("session") if isinstance(registered, dict) else None
        if registered and registered != session:  # the same registry as the role check (of_secrets-review, PR 375)
            raise RelayError(403, "not_holder", f"{b_org}:{b_pos}'s registry names another holder session")
        if b_pos not in queue_managers(positions, units, receiver):
            raise RelayError(403, "forbidden", f"{by} is neither {org}'s structure keeper nor the owner of "
                                               f"{receiver}'s unit")
        with self.app.store.conn() as c:
            human = c.execute("SELECT 1 FROM presence WHERE org = ? AND position = ? AND kind = 'human' AND updated > ?",
                              (org, receiver, time.time() - PRESENCE_TTL)).fetchone()
        if human or held_by_person(entry):
            raise RelayError(403, "person_held", f"a person holds {org}:{receiver}: its messages are theirs, kept in "
                                                 "their own of inbox; nobody else moves or drops them")
        return b_org, b_pos, receiver, positions

    def move(self, m, body):
        """Move a position's queued messages (all, or `msg_ids`) to another position of its organization, for its
        structure keeper or its unit's owner (a user organization's structure keeper, msg 01a1f3a8-b16b: mail piled
        up on parked positions that kept their sessions). Each keeps its id, sender, reply_to, time and attempts; it
        is offered at once; it carries no authority, which was resolved for its first receiver, and no grant;
        `moved_from` keeps its first receiver. One under a live lease stays. `dry_run` lists them without text."""
        body = body or {}
        s_org, s_pos = parse_address(body.get("from"))
        d_org, d_pos = parse_address(body.get("to"))
        if s_org != d_org:
            raise RelayError(400, "bad_request", "messages move only within their organization")
        if not POSITION_NAME.match(d_pos):
            raise RelayError(400, "bad_position", f"position {d_pos[:80]!r} breaks the name rule")
        s_pos = self._canonical(s_org, s_pos)
        b_org, b_pos, s_pos, positions = self._manager(m, s_org, body.get("by"), body.get("session"), s_pos)
        by = f"{b_org}:{b_pos}"
        live = live_position(positions, d_pos)  # the registry alone names the destination
        if live is None:
            raise RelayError(400, "bad_destination", f"{d_org}:{d_pos} is not an active position of {d_org}'s registry"
                                                     " (parked, finished or unknown): nobody would read the messages")
        d_pos = live
        if d_pos == s_pos:
            raise RelayError(400, "bad_request", "the messages are already that position's")
        ids = body.get("msg_ids")
        if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
            raise RelayError(400, "bad_request", "msg_ids must be a list of message ids")
        src, dst, now = f"{s_org}:{s_pos}", f"{d_org}:{d_pos}", time.time()
        out = {"from": src, "to": dst, "by": by, "moved": [], "leased": [], "not_queued": []}
        with self.app.store.tx() as c:
            rows = c.execute("SELECT msg_id, from_org, from_position, created_at, priority, length(text), reply_to,"
                             " state, lease_until, authority, authority_reason, grant_id FROM messages"
                             " WHERE to_org = ? AND to_position = ? AND state IN ('queued', 'leased') ORDER BY seq",
                             (s_org, s_pos)).fetchall()
            if ids is not None:
                have = {r[0] for r in rows}
                out["not_queued"] = [i for i in ids if i not in have]
                rows = [r for r in rows if r[0] in set(ids)]
            for (msg_id, f_org, f_pos, created_at, priority, chars, reply_to, state, lease_until, auth, why,
                 grant) in rows:
                item = {"msg_id": msg_id, "from": f"{f_org}:{f_pos}", "created_at": created_at,
                        "priority": priority or "normal", "chars": chars, "reply_to": reply_to}
                if state == "leased" and (lease_until or 0) > now:
                    out["leased"].append(item)
                    continue
                out["moved"].append(dict(item, authority_was=auth, grant_was=grant))
                if body.get("dry_run"):
                    continue
                c.execute("UPDATE messages SET to_position = ?, state = 'queued', leased_to = NULL, lease_until = NULL,"
                          " failures = 0, moved_from = COALESCE(moved_from, ?), moved_by = ?, authority = 'none',"
                          " authority_reason = ?, grant_id = NULL WHERE msg_id = ? AND to_org = ? AND to_position = ?",
                          (d_pos, src, by, why if auth == "none" else f"moved: its authority ({auth}) was for {src}",
                           msg_id, s_org, s_pos))
        if body.get("dry_run"):
            for i in out["moved"]:
                i.pop("authority_was"), i.pop("grant_was")
            return dict(out, dry_run=True)
        self._queue([self._event(s_org, "of.message_moved", {
            "msg_id": i["msg_id"], "from_receiver": src, "to": dst, "by": by, "machine_id": m["machine_id"],
            "authority_was": i["authority_was"], "grant_was": i["grant_was"]}) for i in out["moved"]])
        with self.cond:
            self.cond.notify_all()
        for i in out["moved"]:
            i.pop("authority_was"), i.pop("grant_was")
        return out

    def drop_by(self, m, msg_id, body):
        """Expire one message queued for `to`, for the receiver's organization's structure keeper or its unit's owner:
        its reason, masked like a message's text, becomes `dropped by <by>: <reason>`; the server host's
        `bin/of_server.py relay-requeue --state expired --msg-id <id>` on the server host undoes it. The check and the update hold the same address, and the
        event is written only when the update took a row (of_secrets-review on organic-framework PR 375)."""
        body = body or {}
        to_org, to_pos = parse_address(body.get("to"))
        to_pos = self._canonical(to_org, to_pos)
        reason = str(body.get("reason") or "").strip()
        if not reason:
            raise RelayError(400, "bad_request", "a reason is required")
        if body.get("dry_run"):
            raise RelayError(400, "bad_request", "dry_run is for move; a drop has no preview")
        b_org, b_pos, to_pos, _ = self._manager(m, to_org, body.get("by"), body.get("session"), to_pos)
        with self.app.store.conn() as c:
            r = c.execute("SELECT 1 FROM messages WHERE msg_id = ? AND to_org = ? AND to_position = ?",
                          (msg_id, to_org, to_pos)).fetchone()
        if not r:
            raise RelayError(404, "not_found", f"no message {msg_id} for {to_org}:{to_pos} (give its full id)")
        masked = self.app.masker.text(reason)[:300]  # the whole reason first: a cut could split what a rule needs
        found = self.app.masker.take()
        why, now = f"dropped by {b_org}:{b_pos}: {masked}", time.time()
        with self.app.store.tx() as c:
            hit = c.execute("UPDATE messages SET state = 'expired', done_at = ?, leased_to = NULL, lease_until = NULL,"
                            " reason = ? WHERE msg_id = ? AND to_org = ? AND to_position = ? AND state = 'queued'",
                            (of_events.now_iso(), why, msg_id, to_org, to_pos)).rowcount
            st = c.execute("SELECT state, lease_until, to_org, to_position FROM messages WHERE msg_id = ?",
                           (msg_id,)).fetchone()
        if hit != 1:
            if (st[2], st[3]) != (to_org, to_pos):
                raise RelayError(409, "moved", "the message was moved to another position meanwhile")
            if st[0] == "leased":  # also past its lease: the relay types before it acks, so it may be in the pane
                raise RelayError(409, "leased", "the relay may be typing this message into the pane now; it stays",
                                 {"retry_after": max(5, int((st[1] or now) - now + 0.999))})
            return {"msg_id": msg_id, "state": st[0], "already": True}
        ts = of_events.now_iso()
        self._queue([self._event(to_org, "of.message_expired", {"msg_id": msg_id, "to": f"{to_org}:{to_pos}",
                                                                "reason": why, "by": f"{b_org}:{b_pos}"})],
                    [(k, h, to_org, "message", {"count": n, "first_ts": ts, "last_ts": ts,
                                                "location": f"message:{msg_id}:drop_reason"})
                     for (k, h), n in found.items()])
        return {"msg_id": msg_id, "state": "expired", "already": False}

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
        moving = sorted(getattr(self.app, "moving", lambda: set())())
        hold = (f" AND to_org NOT IN ({','.join('?' * len(moving))}) AND from_org NOT IN "
                f"({','.join('?' * len(moving))})") if moving else ""  # not while its records are copied (sec_keeper)
        with self.app.store.conn() as c:
            rows = c.execute("SELECT msg_id, to_org, to_position FROM messages WHERE state IN ('queued', 'leased')"
                             " AND created < ?" + hold, (cutoff, *moving, *moving)).fetchall()
            for msg_id, _, _ in rows:
                c.execute("UPDATE messages SET state = 'expired', done_at = ? WHERE msg_id = ?",
                          (of_events.now_iso(), msg_id))
        self._queue([self._event(o, "of.message_expired", {"msg_id": i, "to": f"{o}:{p}"}) for i, o, p in rows])
        return len(rows)
