"""paste_source, ps-v0: whose output the owner pasted into another session (ap.owner-relay).

For an owner's prompt that carries a pasted block (`<pasted_content …>`), the pasted body is matched
against the full assistant texts of OTHER sessions in the previous 24 h, read from the transcript
records (the stored last_message is clipped). Matching: the body is normalized (case, spaces,
markdown marks), PROBES shingles of SHINGLE characters are taken at evenly spaced offsets, and a
candidate's score is the share of probes found in its normalized text. The best candidate wins
(ties: the latest); below FLOOR the source is null: no weak guesses.

Each evaluated prompt gets one `of.paste_sourced` event (deterministic id from the prompt's id):
{prompt_id, session, position, record_uuid, score, probes, method: ps-v0}, session null when no
source cleared the floor. The listener runs it live; `of paste backfill` runs it over history.
"""

import json
import re
import uuid

METHOD = "ps-v0"
SHINGLE = 40
PROBES = 8
FLOOR = 0.5
WINDOW_S = 24 * 3600
NS = uuid.UUID("7c2e1b9a-4d3f-4a6b-9c8d-1e2f3a4b5c6d")
PASTE = re.compile(r"<pasted_content[^>]*>(.*?)(?:</pasted_content[^>]*>|$)", re.S)


def normalize(text):
    """Letters and digits only, one space between words: a terminal's rendering of markdown (bullets,
    ⏺, wrapped lines, bold marks, link syntax) and its source normalize to the same string."""
    t = re.sub(r"[^\w]+", " ", (text or "").lower().replace("_", " "))
    return re.sub(r"\s+", " ", t).strip()


def pasted_body(prompt_text):
    return "\n".join(m.strip() for m in PASTE.findall(prompt_text or "") if m.strip())


def probes(norm, n=PROBES, size=SHINGLE):
    if len(norm) < 2 * size:
        return []
    step = max(1, (len(norm) - size) // max(1, n - 1))
    return list(dict.fromkeys(norm[i:i + size] for i in range(0, len(norm) - size + 1, step)))[:n]


def _assistant_text(rec):
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else None
    if msg:
        return "\n".join(c.get("text", "") for c in msg.get("content") or [] if isinstance(c, dict) and c.get("type") == "text")
    p = rec.get("payload") or {}  # Codex: response_item, role assistant
    if p.get("type") == "message" and p.get("role") == "assistant":
        return " ".join(c.get("text", "") for c in p.get("content") or [] if isinstance(c, dict))
    return ""


class Corpus:
    """Normalized assistant texts by blob, decoded once per run."""

    def __init__(self, conn):
        self.conn, self.cache = conn, {}

    def candidates(self, ts_from, ts_to, exclude_session):
        rows = self.conn.execute(
            "SELECT session_id, record_uuid, timestamp, blob_sha256 FROM transcript_record WHERE timestamp >= ? "
            "AND timestamp <= ? AND type IN ('assistant', 'response_item') AND session_id != ?",
            (ts_from, ts_to, exclude_session or "")).fetchall()
        import of_store
        for sid, ruuid, ts, sha in rows:
            if sha not in self.cache:
                try:
                    self.cache[sha] = normalize(_assistant_text(json.loads(of_store.get_blob(self.conn, sha) or b"{}")))
                except ValueError:
                    self.cache[sha] = ""
            if self.cache[sha]:
                yield sid, ruuid, ts, self.cache[sha]


def _iso_minus(ts, seconds):
    import calendar
    import time
    t = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")) - seconds
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t))


def find_source(corpus, body, ts, exclude_session):
    """(session, record_uuid, score, n_probes) of the best source, or (None, None, best score, n)."""
    ps = probes(normalize(body))
    if not ps:
        return None, None, 0.0, 0
    best = (None, None, 0.0, "")
    for sid, ruuid, rts, text in corpus.candidates(_iso_minus(ts, WINDOW_S), ts, exclude_session):
        score = sum(1 for p in ps if p in text) / len(ps)
        if score > best[2] or (score == best[2] and score > 0 and rts > best[3]):
            best = (sid, ruuid, score, rts)
    if best[2] < FLOOR:
        return None, None, round(best[2], 3), len(ps)
    return best[0], best[1], round(best[2], 3), len(ps)


def event_id(prompt_id):
    return str(uuid.uuid5(NS, f"{prompt_id}:{METHOD}"))


def evaluate(conn, corpus, prompt, sessions_index, emit):
    """prompt: {event_id, ts, session_id, position, payload}. Writes one of.paste_sourced."""
    body = pasted_body((prompt.get("payload") or {}).get("text") or "")
    if not body:
        return None
    sid, ruuid, score, n = find_source(corpus, body, prompt["ts"], prompt.get("session_id"))
    pos = ((sessions_index or {}).get(sid) or [None])[0] if sid else None
    payload = {"prompt_id": prompt["event_id"], "session": sid, "position": pos, "record_uuid": ruuid,
               "score": score, "probes": n, "method": METHOD, "receiver_session": prompt.get("session_id"),
               "receiver_position": prompt.get("position")}
    emit(payload, prompt)
    return payload


def pending(conn, since=None):
    """Owner prompts with a pasted block that have no of.paste_sourced under this method yet."""
    done = {r[0] for r in conn.execute("SELECT json_extract(payload, '$.prompt_id') FROM event "
                                       "WHERE name = 'of.paste_sourced' AND json_extract(payload, '$.method') = ?",
                                       (METHOD,))}
    sql = ("SELECT event_id, ts, session_id, position, payload FROM event WHERE name = 'of.human_prompt' "
           "AND payload LIKE '%pasted_content%'" + (" AND ts >= ?" if since else "") + " ORDER BY ts")
    out = []
    for e, ts, s, pos, pl in conn.execute(sql, (since,) if since else ()):
        if e in done:
            continue
        p = json.loads(pl or "{}")
        if p.get("origin") in ("human", "paste", None) and not framed(pasted_body(p.get("text") or "")):
            out.append({"event_id": e, "ts": ts, "session_id": s, "position": pos, "payload": p})
    return out


FRAMES = ("Message from ", "Answer from the founder", "Side question from the founder", "Question from ")


def framed(body):
    """A relay delivery, the popup's answer or a routed question typed into the pane: Claude Code wraps
    long typed text as pasted, but it is none of the owner's pastes."""
    b = (body or "").lstrip()
    return b.startswith(FRAMES) and ("(via relay" in b[:300] or b.startswith(FRAMES[1:]))


def run(conn, since=None, log=print):
    """Evaluate every pending paste (history or the latest). Returns (evaluated, with a source)."""
    import of_events
    import of_hook
    index = of_hook._registry_index()
    corpus = Corpus(conn)

    def emit(payload, prompt):
        rec = of_events.record("paste_sourced", payload, position=prompt.get("position"),
                               session=prompt.get("session_id"), source="of")
        rec["of.event.id"] = event_id(prompt["event_id"])
        rec["timestamp"] = prompt["ts"]
        import of_store
        of_store.add_event(conn, rec)
    n = found = 0
    for p in pending(conn, since):
        r = evaluate(conn, corpus, p, index.get("sessions") or {}, emit)
        if r:
            n += 1
            found += bool(r["session"])
    log(f"paste_source {METHOD}: {n} evaluated, {found} with a source")
    return n, found
