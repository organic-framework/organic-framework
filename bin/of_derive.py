"""Typed events derived from history (gap #0, RFC 0001 v3 §5.1).

Reads the transcript records already ingested into the local store and writes the same event
kinds the live collector writes, with the same classifiers (bin/of_classify.py), so that
history and live data are measured alike (docs/events.md, docs/studio.md):

  human_prompt (origin from the harness's metadata, classifications fa + of)
  agent_message_in (peer prompts) · agent_message_out (SendMessage) with delivery
  agent_question (AskUserQuestion; heuristic on turn end, q-v0 and q-v1) · agent_question_answered
  turn_end (last assistant text, masked) · compaction · work_record (pr-link)

Deterministic ids (UUIDv5 over session, record and kind) make a re-run idempotent. For a
session that also has live (hook) events, only records older than its first live event are used.
"""

import json
import os
import uuid

import of_classify
import of_events
import of_hook
import of_store

NS = uuid.UUID("6f1d3c4e-0f5a-4c1e-9a7b-0f0f0f0f0f0f")
TEXT_MAX, SHORT = of_hook.TEXT_MAX, of_hook.SHORT


def _eid(session, record, kind):
    return str(uuid.uuid5(NS, f"{session}:{record}:{kind}"))


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


class _Session:
    def __init__(self, sid, index, cutoff, path):
        self.sid, self.index, self.cutoff, self.path = sid, index, cutoff, path
        self.name, self.org, self.serves = (((index.get("sessions") or {}).get(sid) or []) + [None, None, None])[:3]
        self.prompts = 0
        self.system = ((index.get("labels") or {}).get(self.name) or [None, None, None, None])[3]
        self.fa_seen = False  # the baseline's `seen_human`: any earlier extracted prompt in this session
        self.last_text, self.last_ts, self.last_uuid, self.last_model = "", None, None, None
        self.open_ask = None
        self.pending = {}  # tool_use_id -> (kind, event_id, extra)
        self.out = []

    def emit(self, kind, rec, payload, ts=None):
        ruuid = rec.get("uuid") or rec.get("promptId") or f"{rec.get('timestamp')}"
        r = of_events.record(kind, payload, position=self.name, org=self.org, session=self.sid, source="import",
                             extra={"of.harness.name": "claude-code", "of.harness.version": rec.get("version"),
                                    "gen_ai.provider.name": "anthropic", "gen_ai.request.model": self.last_model,
                                    "of.cwd": of_hook._home(rec.get("cwd")),
                                    "of.transcript": {"path": of_hook._home(self.path), "uuid": ruuid},
                                    "of.resolution": f"registry@{of_events.now_iso()[:10]}",
                                    **({"of.serves": self.serves} if self.serves and self.serves != self.org else {})})
        r["of.event.id"] = _eid(self.sid, ruuid, kind)
        r["timestamp"] = ts or rec.get("timestamp") or r["timestamp"]
        self.out.append(r)
        return r["of.event.id"]

    def turn_end(self, rec):
        if self.last_ts is None:
            return
        text, cut = of_hook.clip(self.last_text, SHORT)
        anchor = {"uuid": f"{self.last_uuid}:end", "timestamp": self.last_ts, "cwd": rec.get("cwd"),
                  "version": rec.get("version")}
        self.emit("turn_end", anchor, {"last_message": text, "last_message_chars": len(self.last_text),
                                       **({"truncated": True} if cut else {})}, ts=self.last_ts)
        qs = of_classify.questions(self.last_text)
        if any(qs.values()):
            eid = self.emit("agent_question", {**anchor, "uuid": f"{self.last_uuid}:q"},
                            {"detected_by": "heuristic", "heuristic": True, "classifier_version": of_classify.ASK_VERSION,
                             "tags": of_classify.tags_of(self.last_text[-1200:]),
                             "classifications": qs, "questions": [{"question": of_hook.clip(self.last_text[-600:], SHORT)[0]}]},
                            ts=self.last_ts)
            if qs.get(of_classify.REPLY_VERSION):  # reply_to pairing stays on the gate's pinned version
                self.open_ask = eid
        self.last_ts = None

    def feed(self, rec):
        ts = rec.get("timestamp") or ""
        if self.cutoff and ts and ts >= self.cutoff:
            return
        typ = rec.get("type")
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
        if typ == "assistant":
            self.last_model = msg.get("model") or self.last_model
            for c in msg.get("content") or []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text" and c.get("text"):
                    self.last_text, self.last_ts, self.last_uuid = c["text"], ts, rec.get("uuid")
                elif c.get("type") == "tool_use" and c.get("name") == "AskUserQuestion":
                    qs = []
                    for q in (c.get("input") or {}).get("questions") or []:
                        qs.append({"header": q.get("header"), "question": of_hook.clip(q.get("question"), SHORT)[0],
                                   "options": [o.get("label") for o in q.get("options") or []]})
                    eid = self.emit("agent_question", {**rec, "uuid": f"{rec.get('uuid')}:{c.get('id')}"},
                                    {"detected_by": "tool", "tool_use_id": c.get("id"), "questions": qs,
                                     "classifications": {"q-v0": True, "q-v1": True}})
                    self.open_ask = eid
                    self.pending[c.get("id")] = ("ask", eid, None)
                elif c.get("type") == "tool_use" and c.get("name") == "SendMessage":
                    inp = c.get("input") or {}
                    self.pending[c.get("id")] = ("send", None, (rec, inp))
            import of_reads
            for payload, tuid in of_reads.detect(rec, self.name, self.system, self.index.get("systems") or []):
                self.emit("artifact_read", {**rec, "uuid": f"{rec.get('uuid')}:{tuid}"}, payload)
            return
        if typ == "system":
            if rec.get("subtype") == "turn_duration":
                self.turn_end(rec)
            elif rec.get("subtype") == "compact_boundary":
                self.emit("compaction", rec, {"trigger": (rec.get("compactMetadata") or {}).get("trigger")})
            return
        if typ == "pr-link":
            self.emit("work_record", {**rec, "uuid": f"pr:{rec.get('prRepository')}#{rec.get('prNumber')}"},
                      {"source": "pr-link", "repo": rec.get("prRepository"), "number": rec.get("prNumber"),
                       "url": rec.get("prUrl"), "attribution": "pr-link" if self.name else None})
            return
        if typ != "user" or rec.get("isCompactSummary") or rec.get("isVisibleInTranscriptOnly"):
            return
        content = msg.get("content")
        if isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            for c in content:
                if not isinstance(c, dict) or c.get("type") != "tool_result":
                    continue
                p = self.pending.pop(c.get("tool_use_id"), None)
                if not p:
                    continue
                if p[0] == "ask":
                    answers = rec.get("toolUseResult", {}).get("answers") if isinstance(rec.get("toolUseResult"), dict) else None
                    self.emit("agent_question_answered", {**rec, "uuid": f"{rec.get('uuid')}:{c.get('tool_use_id')}"},
                              {"tool_use_id": c.get("tool_use_id"), "reply_to": p[1],
                               "answers": json.loads(of_hook.mask(json.dumps(answers, ensure_ascii=False))) if answers else None})
                    self.open_ask = None
                else:
                    srec, inp = p[2]
                    msgtxt = str(inp.get("message") or "")
                    to = str(inp.get("to") or "")
                    bare = to.split(" [")[0].strip().lower()
                    text, cut = of_hook.clip(msgtxt, SHORT)
                    self.emit("agent_message_out", {**srec, "uuid": f"{srec.get('uuid')}:{c.get('tool_use_id')}"},
                              {"to": to, "to_position": (self.index.get("names") or {}).get(bare),
                               "delivery": "failed" if c.get("is_error") else "delivered",
                               "carries_owner_words": of_classify.carries_owner_words(msgtxt),
                               "classifier_version": "ow-v1", "heuristic": True, "text": text, "chars": len(msgtxt),
                               **({"truncated": True} if cut else {})})
            return
        text = _text(content)
        if not text.strip():
            return
        self.turn_end(rec)  # a new prompt also closes the previous turn when no turn_duration was written
        origin = of_classify.origin_from_record(rec)
        if of_classify.relay_sender(text):
            origin = "peer"  # a relay delivery typed into the pane
        if origin == "peer":
            o = dict(rec.get("origin") or {})
            if of_classify.relay_sender(text):
                o["from"] = of_classify.relay_sender(text)
            body, cut = of_hook.clip(text, SHORT)
            self.emit("agent_message_in", rec, {"from": o.get("from"), "from_kind": of_classify.from_kind(o.get("from")),
                                                "text": body, "chars": len(text),
                                                "classifications": {of_classify.FA: of_classify.fa_message_in(text),
                                                                    of_classify.RQ: of_classify.rq_v0(text)},
                                                **({"truncated": True} if cut else {})})
            return
        o = rec.get("origin") if isinstance(rec.get("origin"), dict) else {}
        labels = of_classify.prompt_labels(text, first=not self.fa_seen, origin=origin,
                                           origin_kind=o.get("kind"), is_meta=bool(rec.get("isMeta")),
                                           project=os.path.basename(os.path.dirname(self.path or "")))
        if labels[of_classify.FA]["extracted"]:
            self.fa_seen = True
        body, cut = of_hook.clip(text, TEXT_MAX)
        payload = {"origin": labels[of_classify.OF]["origin"], "channel": rec.get("entrypoint") or "unknown",
                   "input_mode": "pasted" if ("<pasted_content" in text or rec.get("imagePasteIds")) else "typed",
                   "reply_to": self.open_ask if labels[of_classify.OF]["origin"] in ("human", "paste") else None,
                   "text": body, "chars": len(text), "classifications": labels, **({"truncated": True} if cut else {})}
        self.emit("human_prompt", rec, payload)
        if labels[of_classify.OF]["origin"] in ("human", "paste"):
            self.prompts += 1
            self.open_ask = None


def _own(conn):
    """transcript_own (a fork's copies of its parent's records left out) where the store has it."""
    has = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'transcript_own'").fetchone()
    return "transcript_own" if has else "transcript_record"


def derive(conn, sessions=None, log=print):
    """Derive events for the given sessions (default: every session in transcript_record)."""
    index = of_hook._registry_index()
    if sessions is None:
        sessions = [r[0] for r in conn.execute("SELECT DISTINCT session_id FROM transcript_record WHERE harness = 'claude-code'")]
        derive_codex(conn, log=log)
    total = 0
    for sid in sessions:
        live = conn.execute("SELECT min(ts) FROM event WHERE session_id = ? AND source = 'hook'", (sid,)).fetchone()[0]
        rows = conn.execute(f"SELECT source_path, blob_sha256 FROM {_own(conn)} WHERE session_id = ? "  # a fork's copies
                            "AND source_path NOT LIKE '%/subagents/%' ORDER BY timestamp, line_offset", (sid,)).fetchall()
        if not rows:
            continue
        s = _Session(sid, index, live, rows[0][0])
        for path, sha in rows:
            raw = of_store.get_blob(conn, sha)
            try:
                s.feed(json.loads(raw))
            except Exception:
                continue
        s.turn_end({})
        # The typed layer is rebuildable: a re-derivation replaces this session's imported events, so
        # improved classifiers take effect (same ids; the shipper re-sends them and the stores upsert).
        with of_store.write_tx(conn):
            conn.execute("DELETE FROM event_fts WHERE event_id IN (SELECT event_id FROM event WHERE session_id = ? "
                         "AND source = 'import')", (sid,))
            conn.execute("DELETE FROM event WHERE session_id = ? AND source = 'import'", (sid,))
        for ev in s.out:
            of_store.add_event(conn, ev)
        total += len(s.out)
    log(f"derived {total} events from {len(sessions)} session(s)")
    return total


# ------------------------------------------------------------------- Codex

# Codex's injected turns, by prefix after lstrip (extract_codex_prompts.py; acme/org
# reports/baselines/founder-load-2026-11-05.yaml scope.codex_extraction.skip_prefixes, 5d4632b).
# `<send_user_message_question_reply>` stays: that is the founder answering.
CODEX_SKIP = ("<environment_context", "<user_instructions", "# AGENTS", "<permissions", "<codex_internal_context",
              "<heartbeat", "<external_codex_apps_open_page")


def _codex_text(p):
    return p.get("message") or " ".join(x.get("text", "") for x in p.get("content") or [] if isinstance(x, dict))


def derive_codex(conn, sessions=None, log=print):
    """Codex rollouts: human prompts (deduplicated by minute + first 60 characters, as the baseline
    does across `user_message` and `message`/user), turn ends and heuristic questions."""
    index = of_hook._registry_index()
    if sessions is None:
        sessions = [r[0] for r in conn.execute("SELECT DISTINCT session_id FROM transcript_record WHERE harness = 'codex'")]
    total = 0
    for sid in sessions:
        rows = conn.execute("SELECT source_path, blob_sha256 FROM transcript_record WHERE session_id = ? AND harness = 'codex' "
                            "ORDER BY timestamp, line_offset", (sid,)).fetchall()
        if not rows:
            continue
        s = _Session(sid, index, None, rows[0][0])
        seen = set()
        for path, sha in rows:
            try:
                rec = json.loads(of_store.get_blob(conn, sha))
            except Exception:
                continue
            p = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
            ts = rec.get("timestamp") or ""
            if rec.get("type") == "turn_context":
                s.last_model = p.get("model") or s.last_model
                continue
            if p.get("type") == "message" and p.get("role") == "assistant":
                t = _codex_text(p)
                if t:
                    s.last_text, s.last_ts, s.last_uuid = t, ts, f"codex:{sha[:16]}"
                continue
            is_user = p.get("type") == "user_message" or (p.get("type") == "message" and p.get("role") == "user")
            if not is_user:
                continue
            text = _codex_text(p)
            if not text or text.lstrip().startswith(CODEX_SKIP):
                continue
            key = (ts[:16], text[:60])
            if key in seen:
                continue
            seen.add(key)
            anchor = {"uuid": f"codex:{sha[:16]}", "timestamp": ts, "cwd": p.get("cwd")}
            s.turn_end(anchor)
            labels = of_classify.prompt_labels(text, first=not s.fa_seen, origin="human")
            labels[of_classify.FA]["codex"] = True
            s.fa_seen = True
            body, cut = of_hook.clip(text, TEXT_MAX)
            trigger = p.get("turn_trigger") or rec.get("turn_trigger")
            ev = {"origin": "human", "channel": "phone" if trigger == "remote_ios" else "codex",
                  "input_mode": "typed", "reply_to": s.open_ask, "text": body, "chars": len(text),
                  "classifications": labels, **({"truncated": True} if cut else {})}
            eid = s.emit("human_prompt", anchor, ev)
            for e in s.out:
                if e["of.event.id"] == eid:
                    e["of.harness.name"] = "codex"
                    e["gen_ai.provider.name"] = "openai"
            s.open_ask = None
        s.turn_end({})
        with of_store.write_tx(conn):
            conn.execute("DELETE FROM event_fts WHERE event_id IN (SELECT event_id FROM event WHERE session_id = ? "
                         "AND source = 'import')", (sid,))
            conn.execute("DELETE FROM event WHERE session_id = ? AND source = 'import'", (sid,))
        for ev in s.out:
            ev["of.harness.name"] = "codex"
            ev["gen_ai.provider.name"] = "openai"
            of_store.add_event(conn, ev)
        total += len(s.out)
    log(f"derived {total} Codex events from {len(sessions)} session(s)")
    return total
