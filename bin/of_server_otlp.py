"""OTLP/HTTP logs receiver for of-server (docs/server.md, Ingest). Owned by of_server.

Harnesses and tools that already speak OpenTelemetry (Claude Code's and Codex's OTel export)
post logs to `POST /v1/otlp/v1/logs` in OTLP/JSON. Each log record becomes a wire record and
goes through the same path as a machine's batch (validation, routing, consent, masking):
- a record with GenAI attributes or a known event name becomes an `event` (events.md: OTel
  names are kept, so one OTel log record maps to one event without renaming);
- anything else is kept as a `runtime_record` with `runtime: "otlp"`.
Protobuf bodies are not accepted yet (415): configure the exporter for http/json.
"""

import hashlib
import json
import time

EVENT_PREFIXES = ("gen_ai.", "claude_code.", "codex.", "of.")


def _value(v):
    """An OTLP AnyValue as a plain JSON value."""
    if not isinstance(v, dict):
        return v
    for k in ("stringValue", "boolValue", "doubleValue"):
        if k in v:
            return v[k]
    if "intValue" in v:
        try:
            return int(v["intValue"])
        except (TypeError, ValueError):
            return v["intValue"]
    if "arrayValue" in v:
        return [_value(x) for x in (v["arrayValue"].get("values") or [])]
    if "kvlistValue" in v:
        return _attrs(v["kvlistValue"].get("values"))
    if "bytesValue" in v:
        return v["bytesValue"]
    return None


def _attrs(kvs):
    return {kv.get("key"): _value(kv.get("value")) for kv in kvs or [] if kv.get("key")}


def _iso(nanos):
    try:
        n = int(nanos)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        n = time.time_ns()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(n // 1_000_000_000)) + f".{(n // 1_000_000) % 1000:03d}Z"


def _is_event(name, attrs):
    return bool(name and name.startswith(EVENT_PREFIXES)) or any(k.startswith("gen_ai.") for k in attrs)


def records(body, tenant, host_id):
    """Wire records ({type, data}) from an OTLP/JSON logs request."""
    out = []
    for rl in (body or {}).get("resourceLogs") or []:
        res = _attrs((rl.get("resource") or {}).get("attributes"))
        host = res.get("host.id") or res.get("host.name") or host_id
        for sl in rl.get("scopeLogs") or []:
            scope = (sl.get("scope") or {}).get("name") or ""
            for lr in sl.get("logRecords") or []:
                attrs = _attrs(lr.get("attributes"))
                ts = _iso(lr.get("timeUnixNano") or lr.get("observedTimeUnixNano"))
                name = lr.get("eventName") or attrs.get("event.name") or ""
                ident = hashlib.sha256(json.dumps([res, scope, lr], sort_keys=True).encode()).hexdigest()
                if _is_event(name, attrs):
                    eid = ident[:32]
                    session = (attrs.get("session.id") or attrs.get("gen_ai.conversation.id")
                               or attrs.get("conversation.id") or res.get("session.id") or "")
                    rec = {**{k: v for k, v in res.items()}, **attrs, "of.schema": 1, "of.event.id": eid,
                           "timestamp": ts, "event.name": name or "gen_ai.log", "of.source": "otlp",
                           "of.otlp.scope": scope}
                    if lr.get("body") is not None:
                        rec["of.otlp.body"] = _value(lr["body"])
                    org = res.get("of.org") or attrs.get("of.org")
                    out.append({"type": "event", "data": {
                        **({"org_id": org} if org else {}),
                        "tenant_id": tenant, "host_id": host, "event_id": eid, "org": res.get("of.org") or attrs.get("of.org"),
                        "ts": ts, "name": name or "gen_ai.log", "session_id": session,
                        "position": attrs.get("gen_ai.agent.name") or res.get("of.position") or "",
                        "source": "otlp", "harness_name": res.get("service.name") or "",
                        "harness_version": res.get("service.version") or "",
                        "model": attrs.get("gen_ai.request.model") or attrs.get("model") or "",
                        "payload": json.dumps(attrs, ensure_ascii=False, default=str), "raw_ref": "",
                        "record": json.dumps(rec, ensure_ascii=False, default=str)}})
                else:
                    org = res.get("of.org") or attrs.get("of.org")
                    out.append({"type": "runtime_record", "data": {
                        **({"org_id": org} if org else {}),
                        "tenant_id": tenant, "host_id": host, "received_at": ts, "runtime": "otlp",
                        "type": name or scope or (lr.get("severityText") or "log"), "pane_id": ident[:16],
                        "raw": json.dumps({"resource": res, "scope": scope, "record": lr}, ensure_ascii=False)}})
    return out
