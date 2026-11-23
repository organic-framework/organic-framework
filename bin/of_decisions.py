"""Decisions the organization's server accepts once: the client's side (event store step 1; organic-framework-build
reports/framework/event-store/04-design.md §2 and step1-accept-contract.md, agreed by of_server and of_platform;
the founder's choice on card OF-294; hand-over d0ef67ce).

A decision (in step 1, an answer to a card at one version) is first a proposal on the machine where a person or a
position makes it: `propose` writes it to the local log as of.decision_proposed, under an id made before the first
send, and sends nothing. `send_pending`, run by the background child that ships an answer today, sends each proposal
not yet settled to its organization's server (POST /v1/decisions) and records what came back:

- accepted: the server's of.decision_accepted, which the coordination feed also brings to every machine, this one
  included; its id is stable_id("decision", proposal), so the two copies are one row;
- refused: the stream already had an accepted decision; of.decision_refused, on this machine only, naming the one
  accepted, so the screen can say who answered first;
- off: the organization's decisions are not turned on (404 decisions_off): of.decision_off, and today's
  of.agent_question_answered written from the proposal, so an organization keeps today's path until its cutover.

Until a machine has seen its organization's of.decision_cutover, a proposal that cannot reach the server is also
written as today's answer at once (as an answer was before), and stays pending; the asks fold ignores such an answer
once it is recorded after the cutover, so only the accepted decision counts there. An organization with no server in
orgs.yaml runs the same accept on this machine (host "of-local", a counter per organization).

Readers: `accepted(conn, ask_id, version)` from the log, for whatever acts on an answer (delivery, redelivery, the
task claim, a diff's approval); the view `decisions` (one row per proposal: sending, accepted, refused, off) for
what shows them, never for a decision of trust (bin/of_fold.py).
"""

import fcntl
import json
import os
import re
import time
import uuid

import of_events
import of_fold

KINDS = ("answer",)
STREAM_RE = re.compile(r"^(?P<ask>[0-9a-fA-F-]{8,64})@v(?P<v>[1-9][0-9]{0,5})$")
WINDOW_S = 30 * 86400   # proposals older than this are not sent again (the feed keeps 30 days)
LOCAL_HOST = "of-local"
# the server's own keys and the stream's (step1-accept-contract.md §2, of_server, message 01a208a4-7e35): never in a
# proposal's payload; every other key rides on the accepted record, at most MAX_KEYS, each a lowercase name
RESERVED = ("proposal", "org", "kind", "stream", "stream_version", "seq", "step", "machine", "proposer", "signed",
            "accepted_at", "restored", "supersedes", "version", "ask_ids", "decision")
MAX_KEYS = 32
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _fields(payload):
    """A proposal's answer fields as the person's interface gave them, every key but the reserved ones: the chat's
    proof that the person answered (option, prompt, prompt_ts, quote, conductor, evidence, relayed_by), a status's
    note, and today's answer, via, by, verdict (of_asks, message 01a20896-c4dc)."""
    return {k: v for k, v in (payload or {}).items() if k not in RESERVED and v is not None}


class DecisionError(Exception):
    pass


def answer_stream(ask_id, version=None):
    """The stream of an answer to ask ASK_ID at its amendment VERSION (1 when never amended): "<ask id>@v<n>"."""
    v = int(version or 1)
    if v < 1:
        raise DecisionError(f"no version {version!r}")
    return f"{ask_id}@v{v}"


def parse_stream(stream):
    """(ask id, version) of an answer's stream, or (None, None)."""
    m = STREAM_RE.match(str(stream or ""))
    return (m.group("ask"), int(m.group("v"))) if m else (None, None)


def proposal_id(act_id, ask_id):
    """A proposal's id for one ask of one act of a screen (the contract's rule), so the screen and its sending thread
    make the same id."""
    return str(uuid.uuid5(of_events.STABLE_NS, f"proposal:{act_id}:{ask_id}"))


def accepted_id(proposal):
    return of_events.stable_id("decision", proposal)


def refused_id(proposal):
    return of_events.stable_id("refused", proposal)


def off_id(proposal):
    return of_events.stable_id("off", proposal)


def legacy_id(proposal):
    return of_events.stable_id("legacy", proposal)


def propose(org, kind, stream, payload, proposal, proposer=None):
    """Write the proposal to the local log, sending nothing; returns its id. PAYLOAD: today's answer fields (answer,
    via, by, ask_id, verdict for "not a question"), no version (it is in STREAM). PROPOSER: {position, session,
    person}, the claim it carries."""
    if kind not in KINDS:
        raise DecisionError(f"no decision kind {kind!r} in step 1")
    ask_id, _ = parse_stream(stream)
    if kind == "answer" and not ask_id:
        raise DecisionError(f"{stream!r}: not <ask id>@v<version>")
    if not org:
        raise DecisionError("a proposal names its organization")
    keys = list(_fields(payload))
    bad = [k for k in keys if not KEY_RE.match(k)]
    if bad or len(keys) > MAX_KEYS:
        raise DecisionError(f"a proposal's payload takes at most {MAX_KEYS} keys, each a lowercase name"
                            + (f" (not {', '.join(map(repr, bad[:3]))})" if bad else ""))
    body = {"v": 1, "proposal": str(proposal), "org": org, "kind": kind, "stream": stream,
            "payload": dict(payload or {}, ask_id=ask_id), "proposer": dict(proposer or {})}
    p = body["proposer"]
    got = of_events.emit("decision_proposed", body, event_id=str(proposal), org=org, position=p.get("position"),
                         session=p.get("session"))
    if not got:
        raise DecisionError("the proposal could not be written")
    return str(proposal)


# ------------------------------------------------------------------ sending

def _conn():
    import of_store
    return of_store.connect()


def _has(conn, event_id):
    return conn.execute("SELECT 1 FROM event WHERE event_id = ?", (event_id,)).fetchone() is not None


def _cut_over(conn, org):
    return conn.execute("SELECT 1 FROM event WHERE name = 'of.decision_cutover' AND (org = ? OR "
                        "json_extract(payload, '$.org') = ?) LIMIT 1", (org, org)).fetchone() is not None


def pending(conn, now=None):
    """The proposals of the last WINDOW_S not yet accepted, refused or found off: [(proposal body, ts)], oldest first."""
    since = of_events.now_iso((now or time.time()) - WINDOW_S)
    out = []
    for eid, ts, pl in conn.execute("SELECT event_id, ts, payload FROM event WHERE name = 'of.decision_proposed' "
                                    "AND ts >= ? ORDER BY ts, rowid", (since,)):
        if any(_has(conn, f(eid)) for f in (accepted_id, refused_id, off_id)):
            continue
        try:
            out.append((json.loads(pl or "{}"), ts))
        except ValueError:
            continue
    return out


def _legacy(p, off=False):
    """Today's of.agent_question_answered for proposal P, under a stable id (the popup's answer before decisions).
    OFF: the server said its organization's decisions are off, which the payload says (decisions_off), so the asks
    fold counts it even after a cutover; written only because the server could not be reached, it does not."""
    ask_id, version = parse_stream(p.get("stream"))
    pl = _fields(p.get("payload"))
    pl.update(ask_id=ask_id, ask_ids=[ask_id], version=version, proposal=p.get("proposal"))
    if off:
        pl["decisions_off"] = True
    who = p.get("proposer") or {}
    return of_events.emit("agent_question_answered", pl, event_id=legacy_id(p["proposal"]), org=p.get("org"),
                          position=who.get("position"), session=who.get("session"))


def _store_record(rec):
    """Keep a record the server made, as it made it (the feed brings the same id again; the store keeps one)."""
    if isinstance(rec, dict) and rec.get("of.event.id"):
        return of_events.append(rec)
    return False


def _accepted_record(p, seq, stream_version, host, signed=None, restored=False, accepted_at=None):
    """An of.decision_accepted record for proposal P, as the server's contract shapes it (§6)."""
    pl = _fields(p.get("payload"))
    pl.update(proposal=p["proposal"], org=p["org"], kind=p["kind"], stream=p["stream"],
              stream_version=stream_version, seq=seq, step="open→answered", machine=host,
              proposer=p.get("proposer") or {}, signed=signed, restored=restored,
              accepted_at=accepted_at or of_events.now_iso())
    rec = of_events.record("decision_accepted", pl, org=p["org"], source="of-decisions")
    rec["of.event.id"] = accepted_id(p["proposal"])
    rec["host.id"] = host
    return rec


def _lock():
    fd = of_events.open_own(os.path.join(of_events.STATE_DIR, "decisions.lock"))
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def accept_local(conn, p):
    """The server's accept for an organization with no server, on this machine: the first proposal of a stream is
    accepted with the organization's next seq; a later one is refused naming it. One at a time (a lock file)."""
    fd = _lock()
    try:
        won = accepted(conn, *parse_stream(p["stream"]), org=p["org"]) if p.get("kind") == "answer" else None
        if won and won.get("proposal") != p["proposal"]:
            return _refused(p, won, "the stream already has an accepted decision")
        if won:
            return "accepted"
        (top,) = conn.execute("SELECT COALESCE(MAX(CAST(json_extract(payload, '$.seq') AS INTEGER)), 0) FROM event "
                              "WHERE name = 'of.decision_accepted' AND json_extract(payload, '$.org') = ?",
                              (p["org"],)).fetchone()
        of_events.append(_accepted_record(p, int(top) + 1, 1, LOCAL_HOST))
        return "accepted"
    finally:
        os.close(fd)


def _refused(p, won, reason):
    w = won or {}
    of_events.emit("decision_refused", {"proposal": p["proposal"], "org": p["org"], "stream": p["stream"],
                                        "reason": reason,
                                        "accepted": {k: w.get(k) for k in ("proposal", "seq", "stream_version", "by",
                                                                           "via", "answer", "accepted_at")}
                                        if won else None},
                   event_id=refused_id(p["proposal"]), org=p["org"])
    return "refused"


def _default_post(url, body):
    """(status, body) of POST /v1/decisions at the organization's server URL."""
    import of_client
    c = of_client.Client(url)
    try:
        if hasattr(c, "decide"):   # of_server's: (status, the body read whole), a refusal's winner included
            return c.decide(body)
        return c._authed("POST", "/v1/decisions", body=body)
    except of_client.ServerError as e:
        try:
            return e.status, json.loads(e.body) if isinstance(e.body, str) else e.body
        except ValueError:
            return e.status, {"title": str(e.body)[:200]}


def send_one(conn, p, post=None, server_url=None):
    """Settle proposal P: 'accepted', 'refused', 'off', or 'waiting' (no answer now; sent again later)."""
    return settle(conn, p, post, server_url)[0]


def settle(conn, p, post=None, server_url=None):
    """(state, record) for proposal P: the state as send_one gives it, and the accepted record (accepted: P's own;
    refused: the one that won), else None."""
    url = server_url if server_url is not None else of_events._org_server_url(p.get("org"))
    if not url:
        state = accept_local(conn, p)
        return state, (accepted(conn, *parse_stream(p["stream"]), org=p["org"]) if state != "off" else None)
    body = {"v": 1, "id": p["proposal"], "org": p["org"], "kind": p["kind"], "stream": p["stream"],
            "payload": _fields(p.get("payload")),
            "proposer": p.get("proposer") or {}, "signature": None, "restore": None}
    try:
        status, out = (post or _default_post)(url, body)
    except Exception:  # noqa: BLE001 - unreachable: sent again later
        status, out = 0, None
    out = out if isinstance(out, dict) else {}
    if status == 200 and out.get("state") == "accepted":
        rec = out.get("decision") or {}
        _store_record(rec)
        return "accepted", (rec.get("of.payload") if rec.get("of.event.id") else rec)
    if status == 409 and out.get("type") == "refused":
        rec = out.get("accepted") or {}
        _store_record(rec)
        won = rec.get("of.payload") if rec.get("of.event.id") else rec
        return _refused(p, won, "already decided"), won
    if status == 409:
        return _refused(p, None, "the proposal's id was used with other content"), None
    if status in (400, 413):   # malformed for the server: never accepted, so not sent again
        return _refused(p, None, f"the server refused it as malformed: {str(out.get('title') or '')[:200]}"), None
    if status in (404, 405):   # decisions off for it, or a server without them: today's answer
        if not _has(conn, legacy_id(p["proposal"])):
            _legacy(p, off=True)
        of_events.emit("decision_off", {"proposal": p["proposal"], "org": p["org"]}, event_id=off_id(p["proposal"]),
                       org=p["org"])
        return "off", None
    if not _cut_over(conn, p["org"]) and not _has(conn, legacy_id(p["proposal"])):
        _legacy(p)   # before its cutover is seen here, an answer that cannot be sent counts as today's
    return "waiting", None


def send_pending(conn=None, post=None, log=print, now=None, only=None):
    """Send every pending proposal, or those of ONLY (proposal ids: the popup's own, from its sending thread, so it
    types an answer into a pane as soon as it is accepted; of_asks, message 01a20896-c4dc). Returns {proposal:
    {"state": accepted|refused|off|waiting, "record": the accepted record (its own, or the one that won) or None}}."""
    own = conn is None
    conn = conn or _conn()
    want = set(only) if only is not None else None
    got = {}
    try:
        for p, _ in pending(conn, now):
            if want is not None and p.get("proposal") not in want:
                continue
            try:
                state, rec = settle(conn, p, post)
            except Exception as e:  # noqa: BLE001 - one proposal never stops the others
                log(f"of decisions: {p.get('proposal')}: {type(e).__name__}: {e}")
                state, rec = "waiting", None
            got[p["proposal"]] = {"state": state, "record": rec}
        for pid in (want or set()) - set(got):   # settled before: what it came to
            row = conn.execute("SELECT name FROM event WHERE event_id IN (?, ?, ?)",
                               (accepted_id(pid), refused_id(pid), off_id(pid))).fetchone()
            if row:
                state = {"of.decision_accepted": "accepted", "of.decision_refused": "refused"}.get(row[0], "off")
                got[pid] = {"state": state, "record": None}
    finally:
        if own:
            conn.close()
    return got


def counts(result):
    """{state: count} of a send_pending result."""
    out = {}
    for v in result.values():
        out[v["state"]] = out.get(v["state"], 0) + 1
    return out


# ------------------------------------------------------------------ reading

def accepted(conn, ask_id, version=None, org=None):
    """The accepted decision's payload for an answer to ASK_ID at VERSION (every version when None), from the log,
    lowest seq first; None when none. For whatever acts on an answer."""
    if not ask_id:
        return None
    streams = [answer_stream(ask_id, version)] if version else None
    q = ("SELECT payload FROM event WHERE name = 'of.decision_accepted' AND json_extract(payload, '$.stream') "
         + ("= ?" if streams else "LIKE ?") + (" AND json_extract(payload, '$.org') = ?" if org else "")
         + " ORDER BY CAST(json_extract(payload, '$.seq') AS INTEGER)")
    args = [streams[0] if streams else f"{ask_id}@v%"] + ([org] if org else [])
    for (pl,) in conn.execute(q, args):
        try:
            return json.loads(pl or "{}")
        except ValueError:
            continue
    return None


def newest_accepted(conn, ask_id):
    """The accepted decision for ASK_ID's newest answered version (an amended card's newer answer), or None."""
    best = None
    for (pl,) in conn.execute("SELECT payload FROM event WHERE name = 'of.decision_accepted' AND "
                              "json_extract(payload, '$.stream') LIKE ?", (f"{ask_id}@v%",)):
        try:
            p = json.loads(pl or "{}")
        except ValueError:
            continue
        a, v = parse_stream(p.get("stream"))
        if a == ask_id and (best is None or v > best[0]):
            best = (v, p)
    return best[1] if best else None


def cutover_of_ask(conn, ask_id):
    """The time decisions were turned on for ASK_ID's organization (its of.decision_cutover), or None."""
    r = conn.execute("SELECT org FROM event WHERE event_id = ?", (ask_id,)).fetchone()
    org = r[0] if r else None
    if not org:
        return None
    r = conn.execute("SELECT MIN(ts) FROM event WHERE name = 'of.decision_cutover' AND (org = ? OR "
                     "json_extract(payload, '$.org') = ?)", (org, org)).fetchone()
    return r[0] if r else None


class Decisions(of_fold.View):
    """One row per proposal (this machine's, and every accepted one the feed brings): its organization, stream, ask,
    state (sending, accepted, refused, off), seq once accepted, and the accepted proposal a refusal names. For the
    popup's "sending / accepted / refused"; never for a decision of trust."""
    name = "decisions"
    owner = "of_platform"
    version = "1"
    names = ("of.decision_proposed", "of.decision_accepted", "of.decision_refused", "of.decision_off")
    lag_ok_s = 120
    FINAL = ("accepted", "refused", "off")

    def fold(self, rows, events):
        changed = {}
        for e in events:
            p = e.get("payload") or {}
            key = p.get("proposal")
            if not key:
                continue
            cur = dict(changed.get(key) or rows.get(key) or {})
            ask_id, version = parse_stream(p.get("stream") or cur.get("stream"))
            cur.update(org=p.get("org") or e.get("org") or cur.get("org"), stream=p.get("stream") or cur.get("stream"),
                       ask_id=ask_id, version=version)
            name = e.get("name")
            if name == "of.decision_proposed":
                if cur.get("state") not in self.FINAL:
                    cur.update(state="sending", at=e.get("ts"))
                cur.setdefault("by", (p.get("payload") or {}).get("by"))
            elif name == "of.decision_accepted":
                cur.update(state="accepted", seq=p.get("seq"), by=p.get("by"), at=p.get("accepted_at") or e.get("ts"))
            elif name == "of.decision_refused":
                cur.update(state="refused", accepted=(p.get("accepted") or {}).get("proposal"), at=e.get("ts"),
                           reason=p.get("reason"))
            elif name == "of.decision_off" and cur.get("state") != "accepted":
                cur.update(state="off", at=e.get("ts"))
            changed[key] = cur
        return changed


of_fold.register(Decisions())


def states(ask_ids, conn=None):
    """{ask id: [the view's rows for its proposals]} for the popup."""
    want = set(a for a in ask_ids or () if a)
    r = of_fold.read("decisions", conn=conn) if conn is not None else of_fold.read("decisions")
    out = {}
    for key, row in (r.rows or {}).items():
        if row.get("ask_id") in want:
            out.setdefault(row["ask_id"], []).append(dict(row, proposal=key))
    return out
