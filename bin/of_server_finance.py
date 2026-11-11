"""of-server: finance facts, written only here (docs/events.md, Finance facts; docs/server.md).

`POST /v1/finance_facts {"record": <the of.finance_fact event record>}`. The writing machine sends the fact
straight to its organization's server and keeps only a stub; batches never carry one. The server checks:
- the payload (period, kind, category, product, account, amount, currency, amount_usd, fx, basis, source,
  supersedes, grant_id; no other field, so nothing per person rides along); kind B, a balance at the end of one day,
  has a day for its period and an account instead of a category;
- the grant: it verifies, its subject is the writing position, it is an allow with `data: [finance]` in its
  scope and a write tier (act_*), and no valid deny with `data: [finance]` covers the position;
- the writer: this machine presents the position now with the record's session, and that session is the
  position's registered holder where the server has the registry;
- `supersedes`: a fact of the same (org, period, kind, category, product), for B of the same (org, period,
  account), that no other fact supersedes yet.
Then the event is queued like a batch (durable, masked, written to the organization's database only: never a
shape or publication copy, never the coordination feed), and its key, never its amounts, is kept in server.db
for the next `supersedes` check.
"""
import datetime
import hashlib
import json
import re
import time

FINANCE = "of.finance_fact"
KINDS = ("T", "OE", "I", "B")
OE_CATEGORIES = ("claude_accounts", "servers", "node_providers", "saas", "people", "other")
BASES = ("actual", "estimate")
WRITE_TIERS = ("act_with_approval", "act_and_report")
FIELDS = ("period", "kind", "category", "product", "account", "amount", "currency", "amount_usd", "fx", "basis",
          "source", "supersedes", "grant_id")
_PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DECIMAL = re.compile(r"^-?\d{1,18}(\.\d{1,18})?$")
_CURRENCY = re.compile(r"^[A-Z0-9]{2,12}$")  # an ISO 4217 code or a token symbol (USDC, ETH)
_NAME = re.compile(r"^[A-Za-z0-9_.:/-]{1,64}$")
_ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.:/-]{0,63}$")   # the treasury's own name for an account or wallet
# what an account number or an address looks like: never in a fact (docs/events.md, Finance facts, `account`)
_ACCOUNT_NUMBER = re.compile(r"\d{6,}|^0x[0-9a-fA-F]{8,}|^[A-Z]{2}\d{2}[A-Z0-9]{10,}$|[1-9A-HJ-NP-Za-km-z]{25,}")

DDL = """
CREATE TABLE IF NOT EXISTS finance_facts (event_id TEXT PRIMARY KEY, org TEXT NOT NULL, period TEXT NOT NULL,
  kind TEXT NOT NULL, category TEXT NOT NULL, product TEXT NOT NULL DEFAULT '', position TEXT, sha TEXT NOT NULL,
  supersedes TEXT, batch_id TEXT, created_at TEXT, account TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS finance_facts_key ON finance_facts (org, period, kind, category, product);
CREATE INDEX IF NOT EXISTS finance_facts_supersedes ON finance_facts (supersedes);
"""


class FinanceError(Exception):
    def __init__(self, status, typ, title):
        super().__init__(title)
        self.status, self.typ, self.title = status, typ, title


def problems(p):
    """What is wrong with an of.finance_fact payload ([] when nothing): docs/events.md, Finance facts."""
    if not isinstance(p, dict):
        return ["the payload must be an object"]
    out = [f"unknown field {k}" for k in sorted(set(p) - set(FIELDS))]
    kind = p.get("kind")
    if kind not in KINDS:
        out.append("kind must be T, OE, I or B")
    period = p.get("period")
    if kind == "B":
        try:
            ok = isinstance(period, str) and bool(_DATE.match(period)) and bool(datetime.date.fromisoformat(period))
        except ValueError:
            ok = False
        if not ok:
            out.append("period must be the day YYYY-MM-DD for a balance (B)")
    elif not isinstance(period, str) or not _PERIOD.match(period):
        out.append("period must be YYYY-MM")
    cat, acc = p.get("category"), p.get("account")
    if kind == "B":
        if cat not in (None, ""):
            out.append("category is absent for a balance (B)")
        if not isinstance(acc, str) or not _ACCOUNT.match(acc.strip()) or acc != acc.strip():
            out.append("account must be the treasury's own short name for the account or wallet (B)")
        elif _ACCOUNT_NUMBER.search(acc):
            out.append("account must be a name, never an account number or an address")
    else:
        if acc not in (None, ""):
            out.append("account is for a balance (B) only")
        if not isinstance(cat, str) or not _NAME.match(cat):
            out.append("category must be a short id")
        elif kind == "OE" and cat not in OE_CATEGORIES:
            out.append(f"an OE category is one of {', '.join(OE_CATEGORIES)}")
    if p.get("product") not in (None, ""):
        if kind != "T":
            out.append("product is for kind T only")
        elif not isinstance(p["product"], str) or not _NAME.match(p["product"]):
            out.append("product must be a short id")
    for f in ("amount",) + (("amount_usd",) if p.get("amount_usd") is not None else ()):
        if not isinstance(p.get(f), str) or not _DECIMAL.match(p[f]):
            out.append(f"{f} must be a decimal string (no floats)")
    cur = p.get("currency")
    if not isinstance(cur, str) or not _CURRENCY.match(cur):
        out.append("currency must be an ISO 4217 code or a token symbol")
    elif cur != "USD":
        fx = p.get("fx")
        if p.get("amount_usd") is None:
            out.append("amount_usd is required when currency is not USD")
        if not (isinstance(fx, dict) and set(fx) <= {"rate", "source", "date"} and isinstance(fx.get("rate"), str)
                and _DECIMAL.match(fx["rate"]) and isinstance(fx.get("source"), str) and fx["source"].strip()
                and isinstance(fx.get("date"), str) and _DATE.match(fx["date"])):
            out.append("fx must be {rate: decimal string, source, date: YYYY-MM-DD} when currency is not USD")
    if p.get("basis") not in BASES:
        out.append("basis must be actual or estimate")
    src = p.get("source")
    if not isinstance(src, str) or not src.strip() or len(src) > 300:
        out.append("source must name the document (at most 300 characters), never hold it")
    if p.get("supersedes") is not None and (not isinstance(p["supersedes"], str) or not p["supersedes"]):
        out.append("supersedes must be an event id")
    if not isinstance(p.get("grant_id"), str) or not p["grant_id"]:
        out.append("grant_id is required")
    return out


def grant_refusal(pool, key, now_iso, org, position, grant_id):
    """Why this grant does not let `position` write finance facts, or None. `pool`: {org: [grants]}."""
    import of_grants
    r = of_grants.verify_request(pool, key, now_iso, grant_id, f"{org}:{position}")
    if not r.get("valid"):
        return f"grant {grant_id}: {r.get('reason')}"
    g = next(x for x in pool.get(org, []) if x.get("id") == grant_id)
    if (g.get("effect") or "allow") != "allow":
        return f"grant {grant_id} is a deny"
    if "finance" not in ((g.get("scope") or {}).get("data") or []):
        return f"grant {grant_id} has no data: [finance] in its scope"
    if g.get("tier") not in WRITE_TIERS:
        return f"grant {grant_id} has tier {g.get('tier')}: it reads finance facts; writing needs act_with_approval or act_and_report"
    by_subject = {}
    for x in pool.get(org, []):
        by_subject.setdefault(x.get("subject"), []).append(x)
    for x in pool.get(org, []):
        if x.get("effect") == "deny" and "finance" in ((x.get("scope") or {}).get("data") or []) \
                and of_grants.applies_to(x, position) and of_grants.verify_chain(x, by_subject, key, now_iso)[0]:
            return f"denied by grant {x.get('id')}"
    return None


def _ensure(store):
    with store.conn() as c:
        c.executescript(DDL)
        if "account" not in {r[1] for r in c.execute("PRAGMA table_info(finance_facts)")}:  # a server.db before B
            c.execute("ALTER TABLE finance_facts ADD COLUMN account TEXT NOT NULL DEFAULT ''")


def write(app, m, body, accept):
    """`POST /v1/finance_facts`. `accept`: of_server.accept. Returns (status, answer); raises FinanceError."""
    rec = body.get("record") if isinstance(body, dict) else None
    if not isinstance(rec, dict) or rec.get("event.name") != FINANCE:
        raise FinanceError(400, "bad_request", f"record must be an {FINANCE} event record")
    eid, org, ts = rec.get("of.event.id"), rec.get("of.org"), rec.get("timestamp")
    position, session = rec.get("gen_ai.agent.name"), rec.get("gen_ai.conversation.id")
    if not (eid and org and ts and position and session):
        raise FinanceError(400, "bad_request", "the record needs of.event.id, of.org, timestamp, its position"
                                               " (gen_ai.agent.name) and session (gen_ai.conversation.id)")
    p = rec.get("of.payload")
    bad = problems(p)
    if bad:
        raise FinanceError(400, "invalid_fact", "; ".join(bad))
    served = app.served if app.served is not None else set(app.router().db_by_org)
    if org not in served:
        raise FinanceError(403, "forbidden", f"organization {org} is not served here")
    if not m["tenant"] and org not in set(json.loads(m["orgs"])):
        raise FinanceError(403, "forbidden", f"organization {org} not joined by this machine")
    if app._grants is None or time.time() - app._grants_at > 30:
        app._grants, app._grants_at = app._grants_loader(), time.time()
    pool = {k: list(v) for k, v in (app._grants or {}).items()}
    why = grant_refusal(pool, app._grant_key(), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), org, position,
                        p["grant_id"])
    if why:
        raise FinanceError(403, "no_finance_grant", why)
    holds, why = app.relay._holds(m, org, position, session)
    if not holds:
        raise FinanceError(403, "not_holder", why.replace("sender not holder", "writer not holder"))
    sha = hashlib.sha256(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    key = (org, p["period"], p["kind"], p.get("category") or "", p.get("product") or "", p.get("account") or "")
    _ensure(app.store)
    with app.store.tx() as c:
        row = c.execute("SELECT sha, batch_id FROM finance_facts WHERE event_id = ?", (eid,)).fetchone()
        if row:
            if row[0] != sha:
                raise FinanceError(409, "conflict", f"fact {eid} was written with another body")
            return 200, {"event_id": eid, "accepted": True, "already": True, "batch_id": row[1]}
        sup = p.get("supersedes")
        if sup:
            old = c.execute("SELECT org, period, kind, category, product, account FROM finance_facts WHERE "
                            "event_id = ?", (sup,)).fetchone()
            if not old:
                raise FinanceError(400, "invalid_fact", f"supersedes {sup}: no finance fact with that id here")
            if tuple(old) != key:
                raise FinanceError(400, "invalid_fact", f"supersedes {sup}: a fact of another (org, period, kind,"
                                                        " category, product), or for B (org, period, account)")
            later = c.execute("SELECT event_id FROM finance_facts WHERE supersedes = ?", (sup,)).fetchone()
            if later:
                raise FinanceError(409, "superseded", f"{sup} is already superseded by {later[0]}: supersede that one")
        bid = f"finance-{eid}"
        c.execute("INSERT INTO finance_facts (event_id, org, period, kind, category, product, account, position, sha,"
                  " supersedes, batch_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (eid, *key, position, sha, sup, bid, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    data = {"tenant_id": app.ship_cfg.get("tenant"), "host_id": m["machine_id"], "event_id": eid, "org": org,
            "org_id": org, "ts": ts, "name": FINANCE, "session_id": session, "position": position,
            "source": rec.get("of.source") or "of", "harness_name": "", "harness_version": "", "model": "",
            "payload": json.dumps(p, ensure_ascii=False), "raw_ref": "", "record": json.dumps(rec, ensure_ascii=False)}
    status, receipt = accept(app.store, app.buffer, app.router(), m,
                             {"batch_id": bid, "records": [{"type": "event", "data": data}]}, sha, app.served,
                             finance=True)
    if status != 202 or receipt.get("accepted") != 1:
        with app.store.conn() as c:
            c.execute("DELETE FROM finance_facts WHERE event_id = ?", (eid,))
        reason = "; ".join(r.get("reason", "") for r in receipt.get("rejected") or []) or receipt.get("title") or "refused"
        raise FinanceError(400 if status == 202 else status, "refused", reason)
    return 202, {"event_id": eid, "accepted": True, "state": receipt.get("state"), "batch_id": bid}
