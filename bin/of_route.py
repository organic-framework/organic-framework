"""Routing and fan-out of records to organization databases (docs/storage.md).

Shared by of-server (the normal path) and the shipper's direct mode. Pure apart from
the ClickHouse client and the registry index it reads.
"""

import base64
import json
import os
import urllib.error
import urllib.request

class ClickHouse:
    """Inserts go in as `<database>_ingest` (insert-only) when its credential exists;
    the tenant's user `of` is the fallback (RFC 0001 §5.2, roles)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.auth = self._auth(cfg["user"], os.path.expanduser(cfg["password_file"]))

    @staticmethod
    def _auth(user, path):
        with open(path) as fh:
            pw = fh.read().strip()
        return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()

    def auth_for(self, database):
        path = os.path.join(os.path.dirname(os.path.expanduser(self.cfg["password_file"])), f"{database}_ingest.password")
        return self._auth(f"{database}_ingest", path) if os.path.exists(path) else self.auth

    def redact_auth(self, database):
        """`<database>_redact` (ALTER DELETE on the four tables only, docs/server.md Redaction).
        A server that inserts as the tenant's own user (no `_ingest` credentials, profile 1 on the
        tenant's machine) deletes as that user; with `_ingest` users there must be a `_redact` one."""
        d = os.path.dirname(os.path.expanduser(self.cfg["password_file"]))
        path = os.path.join(d, f"{database}_redact.password")
        if os.path.exists(path):
            return self._auth(f"{database}_redact", path)
        if os.path.exists(os.path.join(d, f"{database}_ingest.password")):
            raise RuntimeError(f"no {database}_redact credential: redaction needs that role (deploy/clickhouse/schema.py)")
        return self.auth

    def delete(self, database, table, where):
        """Rows matching `where` (an SQL condition this module built) leave the table for good: an
        ALTER ... DELETE mutation, waited for, which rewrites the parts. Returns the rows it matched."""
        auth = self.redact_auth(database)
        n = int(self.post(f"SELECT count() FROM {database}.{table} WHERE {where}", auth=auth,
                          query_in_body=True).strip() or 0)
        if n:
            self.post(f"ALTER TABLE {database}.{table} DELETE WHERE {where}", auth=auth, params={"mutations_sync": 1},
                      query_in_body=True, timeout=600)
        return n

    def post(self, query, body=b"", timeout=120, auth=None, params=None, query_in_body=False):
        """`query_in_body`: send the statement as the request body (a long key list does not fit a URL)."""
        from urllib.parse import urlencode
        params = {"date_time_input_format": "best_effort", "input_format_skip_unknown_fields": 1, **(params or {})}
        if query_in_body:
            body = query.encode()
        else:
            params["query"] = query
        req = urllib.request.Request(f"{self.cfg['url']}/?{urlencode(params)}", data=body, method="POST",
                                     headers={"Authorization": auth or self.auth})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"ClickHouse {e.code}: {e.read()[:500].decode(errors='replace')}")

    def insert(self, database, table, rows):
        body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows).encode()
        self.post(f"INSERT INTO {database}.{table} FORMAT JSONEachRow", body, auth=self.auth_for(database))


class Router:
    """One ClickHouse database per organization (founder, 2026-11-05). A row's organization
    comes from its position (registry), else from the working directory (orgs.yaml `paths`).
    Rows of no known organization go to the unassigned database, which only the tenant reads."""

    def __init__(self, cfg, orgs_file=None):
        import yaml
        orgs_file = orgs_file or os.environ.get("OF_ORGS") or os.path.expanduser("~/.config/of/orgs.yaml")
        with open(orgs_file) as fh:
            doc = yaml.safe_load(fh) or {}
        self.unassigned = doc.get("unassigned_database") or cfg["unassigned_database"]
        self.db_by_org, self.prefixes = {}, []
        for o in doc.get("orgs") or []:
            if not o.get("database"):
                continue
            self.db_by_org[o["name"]] = o["database"]
            for path in o.get("paths") or []:
                full = os.path.realpath(os.path.expanduser(path))
                self.prefixes.append((full, self._encode(full), o["name"]))
        self.prefixes.sort(key=lambda t: -len(t[0]))
        self.org_by_session = _org_by_session()
        self.own_org_by_session = _own_org_by_session()

    def builder_of(self, routed_org, session=None, own_org=None):
        """The position's own organization when its data was routed to another (it serves that
        one): that organization gets the shape of the work, never its content (decision 7)."""
        own = own_org or self.own_org_by_session.get(session or "")
        return own if own and own != routed_org and own in self.db_by_org else None

    @staticmethod
    def _encode(path):  # Claude Code's project directory name for a cwd
        return "".join(c if c.isalnum() else "-" for c in path)

    def org_of(self, org=None, session=None, cwd=None, source_path=None, serves=None):
        if serves and serves in self.db_by_org:  # decision 7: data follows its source
            return serves
        by_session = self.org_by_session.get(session or "")
        if by_session in self.db_by_org:  # the position's `serves`, else its own organization
            return by_session
        if org and org in self.db_by_org:
            return org
        if cwd:
            cwd = os.path.expanduser(cwd.replace("~", os.path.expanduser("~"), 1) if cwd.startswith("~") else cwd)
            for full, _, name in self.prefixes:
                if cwd == full or cwd.startswith(full + os.sep):
                    return name
        if source_path:
            project = os.path.basename(os.path.dirname(source_path))
            if "/subagents/" in source_path or project in ("subagents",):
                project = source_path.split("/projects/")[-1].split("/")[0] if "/projects/" in source_path else project
            for _, enc, name in self.prefixes:
                if project == enc or project.startswith(enc + "-"):
                    return name
            for _, enc, name in self.prefixes:  # scratchpads: -private-tmp-claude-<uid>-<encoded cwd>-<id>-scratchpad
                if project.startswith("-private-tmp-") and ("-" + enc + "-") in project:
                    return name
        return None

    def database(self, org):
        return self.db_by_org.get(org or "", self.unassigned)

    def org_of_events(self, event_ids):
        """The organization of the first of these events that routes to one, from this machine's
        event store (an ask's closure follows the ask it names). None when the store has none."""
        ids = [i for i in event_ids if isinstance(i, str) and i][:50]
        if not ids:
            return None
        conn = getattr(self, "_store", None)
        if conn is None:
            try:
                import of_store
                path = of_store.db_path()
                if not os.path.exists(path):
                    return None
                import sqlite3
                conn = self._store = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            except Exception:
                return None
        try:
            rows = conn.execute(f"SELECT event_id, org, session_id, cwd, record FROM event WHERE event_id IN"
                                f" ({','.join('?' * len(ids))})", ids).fetchall()
        except Exception:
            return None
        by_id = {r[0]: r for r in rows}
        for i in ids:
            if i not in by_id:
                continue
            _, org, session, cwd, record = by_id[i]
            try:
                serves = (json.loads(record or "{}") or {}).get("of.serves")
            except ValueError:
                serves = None
            found = self.org_of(org, session, cwd, serves=serves)
            if found:
                return found
        return None


class StaticRouter:
    """A per-organization server's router: organizations and databases from server.yaml
    (`orgs: {name: database}`), no registries and no client code. The client has routed."""

    def __init__(self, orgs, unassigned):
        self.db_by_org, self.unassigned = dict(orgs), unassigned
        self.org_by_session, self.own_org_by_session = {}, {}

    def org_of(self, *a, **kw):
        return None

    def builder_of(self, *a, **kw):
        return None

    def database(self, org):
        return self.db_by_org.get(org or "", self.unassigned)


def _org_by_session():
    try:
        import of_hook
        sessions = of_hook._registry_index().get("sessions") or {}
        return {s: (v[2] if len(v) > 2 and v[2] else v[1]) for s, v in sessions.items()}
    except Exception:
        return {}


def _own_org_by_session():
    try:
        import of_hook
        return {s: v[1] for s, v in (of_hook._registry_index().get("sessions") or {}).items()}
    except Exception:
        return {}


# The shape of work: what the builder's organization may see of sessions that served another one.
SHAPE_KEYS = {"chars", "origin", "channel", "input_mode", "reply_to", "detected_by", "heuristic",
              "classifier_version", "delivery", "carries_owner_words", "to_position", "from", "tool_name",
              "notification_type", "last_message_chars", "source", "reason", "trigger", "state", "action",
              "status", "truncated", "tool_use_id", "position", "previous_session", "new_session"}
SHAPE_RECORD = ("of.schema", "of.event.id", "timestamp", "event.name", "host.id", "gen_ai.conversation.id",
                "gen_ai.agent.name", "gen_ai.provider.name", "gen_ai.request.model", "of.source",
                "of.harness.name", "of.harness.version")


def shape_payload(payload):
    out = {}
    for k, v in (payload or {}).items():
        if k in SHAPE_KEYS and (v is None or isinstance(v, (bool, int, float)) or isinstance(v, str) and len(v) <= 64):
            out[k] = v
        elif isinstance(v, list):
            out[k + "_count"] = len(v)
    return out


def shape_event(out, builder):
    rec = json.loads(out["record"])
    payload = shape_payload(json.loads(out["payload"] or "{}"))
    slim = {k: rec.get(k) for k in SHAPE_RECORD if k in rec}
    slim.update({"of.org": builder, "of.serves": out["org_id"], "of.shape": True, "of.payload": payload})
    return dict(out, org_id=builder, raw_ref="", payload=json.dumps(payload, ensure_ascii=False),
                record=json.dumps(slim, ensure_ascii=False))


def shape_transcript(out, builder):
    return dict(out, org_id=builder, raw="", source_path="", sha256="0" * 64, tool_names=out["tool_names"])


def shape_hook(out, builder):
    return dict(out, org_id=builder, raw="", sha256="0" * 64)


PUBLISHED_KEEP = ("from_org", "to_org", "artifact", "grant", "note")


def published_copy(out, payload):
    """The receiving organization's copy of a publication: the artifact reference, the grant
    and the cleaned note only. References into the source's traces stay with the source."""
    slim = {k: payload.get(k) for k in PUBLISHED_KEEP if payload.get(k) is not None}
    rec = json.loads(out["record"])
    keep = ("of.schema", "of.event.id", "timestamp", "event.name", "host.id", "gen_ai.agent.name", "of.source")
    rec = {k: rec.get(k) for k in keep}
    rec.update({"of.org": payload.get("to_org"), "of.payload": slim})
    return dict(out, org_id=payload.get("to_org") or "", session_id="", raw_ref="",
                payload=json.dumps(slim, ensure_ascii=False), record=json.dumps(rec, ensure_ascii=False))




def route_org(table, d, router):
    """The organization of one record (wire `data` dict), or None (unassigned)."""
    if table == "event":
        rec = json.loads(d["record"]) if isinstance(d.get("record"), str) else (d.get("record") or {})
        org = router.org_of(d.get("org"), d.get("session_id"), rec.get("of.cwd"), serves=rec.get("of.serves"))
        if org is None and hasattr(router, "org_of_events"):
            # An ask's closure written with no organization, session or directory (a reconcile, an
            # owner's withdrawal) follows the ask it names (of_studio, 2026-11-06).
            refs = _ask_refs(d.get("payload"))
            if refs:
                org = router.org_of_events(refs)
        return org
    if table == "transcript_record":
        org = router.org_of(None, d.get("session_id"), None, d.get("source_path"))
        if org is None:
            try:
                org = router.org_of(None, None, json.loads(d.get("raw") or "{}").get("cwd"))
            except Exception:
                pass
        return org
    try:
        cwd = json.loads(d.get("raw") or "{}").get("cwd")
    except Exception:
        cwd = None
    return router.org_of(None, d.get("session_id"), cwd)


def _ask_refs(payload):
    try:
        p = json.loads(payload) if isinstance(payload, str) else (payload or {})
    except ValueError:
        return []
    if not isinstance(p, dict):
        return []
    refs = [p.get("ask_id")] + list(p.get("ask_ids") or [])
    return [r for r in refs if isinstance(r, str) and r]


RAW_TABLES = ("transcript_record", "hook_record", "runtime_record")
SHAPE_RECORD_KEYS = set(SHAPE_RECORD) | {"of.org", "of.serves", "of.shape", "of.payload"}
PUBLISHED_RECORD_KEYS = {"of.schema", "of.event.id", "timestamp", "event.name", "host.id", "gen_ai.agent.name",
                         "of.source", "of.org", "of.payload"}


def _shape_payload_ok(p):
    for k, v in p.items():
        if k in SHAPE_KEYS and (v is None or isinstance(v, (bool, int, float)) or isinstance(v, str) and len(v) <= 64):
            continue
        if k.endswith("_count") and isinstance(v, int) and not isinstance(v, bool):
            continue
        return False
    return True


def copy_violation(table, d, copy=None):
    """Defence in depth on the receiving server: a record that presents itself as a shape copy
    or as the receiving side of a publication must carry only the content-free allow-list.
    `copy` is the client's explicit marker on the wire record ("shape" | "published"); without
    it the record is still checked if it looks like a copy. Returns a reason, or None."""
    if copy not in (None, "shape", "published"):
        return f"unknown copy marker {copy!r}"
    if copy == "published" and (table != "event" or d.get("name") != "of.published"):
        return "publication copy: only of.published events can be one"
    if copy == "shape" and table == "event":
        try:
            rec = json.loads(d["record"]) if isinstance(d.get("record"), str) else (d.get("record") or {})
        except ValueError:
            rec = {}
        if not rec.get("of.shape"):
            return "shape copy: record lacks of.shape"
    if copy == "shape" and table != "event":
        d = dict(d, shape=True)
    if table == "event":
        try:
            rec = json.loads(d["record"]) if isinstance(d.get("record"), str) else (d.get("record") or {})
            payload = json.loads(d.get("payload") or "{}")
        except ValueError:
            return None  # validate() reports malformed records
        if rec.get("of.shape"):
            if set(rec) - SHAPE_RECORD_KEYS:
                return "shape copy: record keys outside the allow-list"
            if not _shape_payload_ok(payload) or not _shape_payload_ok(rec.get("of.payload") or {}):
                return "shape copy: payload outside the allow-list"
            if d.get("raw_ref"):
                return "shape copy: carries raw_ref"
        elif copy == "published" or (d.get("name") == "of.published" and payload.get("to_org")
                                     and d.get("org_id") == payload.get("to_org")
                                     and payload.get("to_org") != payload.get("from_org")):
            if set(payload) - set(PUBLISHED_KEEP) or set(rec) - PUBLISHED_RECORD_KEYS:
                return "publication copy: keys outside the allow-list"
            if d.get("raw_ref") or d.get("session_id"):
                return "publication copy: carries references into the source's traces"
        return None
    if d.get("shape"):
        if d.get("raw") or d.get("source_path") or (table == "transcript_record" and d.get("tool_names")):
            return "shape copy: carries content"
    return None


def fanout(table, rows, router, cfg, mask=None, copies=True):
    """Rows carry `org_id`. Return {database: [rows]} for one table: the vault, the raw gate,
    publication copies to both sides, shape copies to the builder's organization (decision 7).

    `mask(table, row) -> row` is of-server's boundary masker (RFC 0002 §4.2). Unmasked raw goes
    only to the vault; every row bound for an organization is masked first, so its shape and
    publication copies are derived from masked content. Without a masker (the shipper's direct
    mode) raw never reaches an organization, whatever `raw_to_org` says.

    `copies=False` on a per-organization server: the client already made and reduced the
    copies for other organizations and sent them to those organizations' servers."""
    groups = {}
    raw_to_org = bool(cfg.get("raw_to_org")) and mask is not None
    for out in rows:
        out = {k: v for k, v in out.items() if k != "org"}
        if table in RAW_TABLES and out.get("raw"):
            groups.setdefault(cfg["vault_database"], []).append(out)
        if mask is not None:
            out = mask(table, out)
        if table in RAW_TABLES and not raw_to_org:
            out = dict(out, raw="")  # the gate: organizations get the record without its content
        if table == "event" and out["name"] == "of.finance_fact":  # owner-only: its organization only, never a copy
            groups.setdefault(router.database(out["org_id"]), []).append(out)
            continue
        if table == "event" and out["name"] == "of.published" and copies:  # both sides of the boundary see it
            p = json.loads(out["payload"] or "{}")
            source = router.database(p.get("from_org"))
            groups.setdefault(source, []).append(out)
            for o in (p.get("from_org"), p.get("to_org")):
                if o in router.db_by_org and router.database(o) != source:
                    groups.setdefault(router.database(o), []).append(published_copy(out, p))
            continue
        groups.setdefault(router.database(out["org_id"]), []).append(out)
        if not copies:
            continue
        if table == "event":
            rec = json.loads(out["record"])
            own = router.builder_of(out["org_id"], out["session_id"], rec.get("of.org") if rec.get("of.serves") else None)
            shaped = shape_event(out, own) if own else None
        elif table == "transcript_record":
            own = router.builder_of(out["org_id"], out["session_id"])
            shaped = shape_transcript(out, own) if own else None
        else:
            own = router.builder_of(out["org_id"], out.get("session_id"))
            shaped = shape_hook(out, own) if own else None
        if shaped:
            groups.setdefault(router.database(own), []).append(shaped)
    if table in ("hook_record", "runtime_record"):
        groups = {db: [{k: v for k, v in x.items() if k != "org_id"} for x in rs] for db, rs in groups.items()}
    return groups
