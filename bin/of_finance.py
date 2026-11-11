"""Finance facts, the client side (docs/events.md "Finance facts"; of_platform's part, the server's is of_server's).

`of event finance_fact` validates one month's T, OE or I fact, or one day's balance (B), sends the event straight to the organization's
server, and keeps only a stub here: its event id and the sha256 of its payload, in the table `finance_stub`. The
amounts never enter the local `event` table, its full-text index, a shipped batch or a shape copy. If the server
is unreachable, or does not accept finance facts yet, the command fails and nothing is queued.

Acme's finance unit asked for it on 2026-11-10 (acme_org msg 01a1c752).
"""

import hashlib
import json
import re

import of_events

KINDS = ("T", "OE", "I", "B")  # B: a balance at the end of one day (of_architect, 2026-11-11, for a treasury)
OE_CATEGORIES = ("claude_accounts", "servers", "node_providers", "saas", "people", "other")
BASES = ("actual", "estimate")
PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
AMOUNT = re.compile(r"^-?\d+(\.\d+)?$")
CURRENCY = re.compile(r"^[A-Z0-9]{2,10}$")
CAPABILITY = "finance_facts"
ENDPOINT = "/v1/finance_facts"

STUB_DDL = """CREATE TABLE IF NOT EXISTS finance_stub (event_id TEXT PRIMARY KEY, ts TEXT, org TEXT, position TEXT,
  sha256 TEXT, server TEXT)"""


def validate(p, systems=None):
    """Problems with a finance fact's payload; [] when it may be sent. The rules are the server's own
    (of_server_finance.problems, of_server f5f46c0), so the client and the server agree; the client adds what only it
    knows: a T category must be a system id of the writer's registry."""
    import of_server_finance
    out = list(of_server_finance.problems(p))
    if p.get("kind") == "T" and systems is not None and p.get("category") and p["category"] not in systems:
        out.append(f"a T category is a system id of the registry; {p['category']!r} is not one")
    return out


def build(kind, period, amount, currency, basis, source, grant_id=None, category=None, product=None, account=None,
          amount_usd=None, fx_rate=None, fx_source=None, fx_date=None, supersedes=None):
    """The payload of one fact from the command's fields. T, OE and I: a month's flow by category. B: the amount
    held at the end of one day (period YYYY-MM-DD) in one account, a stock; no category or product."""
    p = {"period": period, "kind": kind, "amount": amount, "currency": currency, "basis": basis, "source": source,
         "grant_id": grant_id}
    if kind == "B":
        p["account"] = account
    else:
        p["category"] = category
        if product:
            p["product"] = product
    if currency and currency != "USD":
        p["amount_usd"] = amount_usd
        p["fx"] = {"rate": fx_rate, "source": fx_source, "date": fx_date}
    if supersedes:
        p["supersedes"] = supersedes
    return p


def payload_sha(p):
    return hashlib.sha256(json.dumps(p, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def record(p, position, org, session=None):
    """The full event, as the server stores it."""
    return of_events.record("finance_fact", p, position=position, org=org, session=session)


def accepts(client):
    """Whether the server says it takes finance facts (its terms list the capability)."""
    try:
        terms = client.terms() or {}
    except Exception:  # noqa: BLE001
        return False
    return CAPABILITY in (terms.get("capabilities") or [])


def send(client, rec):
    """POST the event to the organization's server; its answer, or raises with the server's reason."""
    return client._authed("POST", ENDPOINT, body={"record": rec})[1]


def keep_stub(conn, rec, server):
    conn.execute(STUB_DDL)
    conn.execute("INSERT OR IGNORE INTO finance_stub VALUES (?,?,?,?,?,?)",
                 (rec["of.event.id"], rec["timestamp"], rec.get("of.org"), rec.get("gen_ai.agent.name"),
                  payload_sha(rec.get("of.payload") or {}), server))


def write(p, position, org, server_url, client, conn, systems=None, session=None):
    """Validate, send, keep the stub. Returns (event id, the server's answer); raises ValueError on a problem."""
    problems = validate(p, systems)
    if problems:
        raise ValueError("; ".join(problems))
    if not accepts(client):
        raise ValueError(f"the server {server_url} does not accept finance facts yet (of_server's side); nothing "
                         "was sent or kept")
    rec = record(p, position, org, session)
    answer = send(client, rec)
    keep_stub(conn, rec, server_url)
    return rec["of.event.id"], answer
