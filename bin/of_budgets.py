"""Goal and condition budgets: the warning at 80% and the card at 100% (docs/registry.md, Goals, Budgets).

OF reads and warns and never stops work. The month's spend per node comes from of_capacity's sums (of_spend.load(),
written by the listener's capacity tick): orgs.<org>.goals|conditions.<id> with total, budget, share and forecast.

check(spend, docs, positions, mem, now, fyi, log): run by the pulse.
- at 80% of either budget (tokens_month or usd_month): one `of.budget_warning` a month per node and level, and one
  `--fyi` to the organization's structure keeper;
- at 100%: one card a month per node to the person who holds the node's owner position, or to the organization's
  owner when an agent holds it: raise the budget (a goal_change) or narrow the work.
The events carry fixed ids (of_events.stable_id), so two machines record each fact once.
"""

import time

import of_events
from of_locale import tr

LEVELS = (100, 80)
SENDER = "organic-framework-build:of_platform"
CARD_SOURCE = "budget"


def _level(share):
    top = max([v for v in (share or {}).values() if isinstance(v, (int, float))] or [0])
    return next((lv for lv in LEVELS if top * 100 >= lv), None), top


def _owner(doc, kind, nid):
    """The node's owner position: its own `owner`, else its parent's (a goal) or its `under` goal's (a condition)."""
    goals = doc.get("goals") if isinstance(doc.get("goals"), dict) else {}
    node = (doc.get(kind) or {}).get(nid) if isinstance(doc.get(kind), dict) else None
    seen = set()
    while isinstance(node, dict):
        if node.get("owner"):
            return str(node["owner"])
        nxt = node.get("parent") if kind == "goals" else node.get("under")
        kind = "goals"
        if not nxt or nxt in seen:
            return None
        seen.add(nxt)
        node = goals.get(nxt)
    return None


def card_to(doc, kind, nid, positions, org):
    """Whom the 100% card goes to: the node's owner position when a person holds it, else the founder (the
    organization's owner)."""
    owner = _owner(doc, kind, nid)
    p = next((x for x in positions if x.get("name") == owner and org in (x.get("_org"), x.get("_orgid"))), None)
    if p and ((p.get("holder") or {}).get("runtime") == "human" or (p.get("holder") or {}).get("person")):
        return owner
    return "founder"


def _amounts(node):
    t, b = node.get("total") or {}, node.get("budget") or {}
    out = []
    if b.get("tokens_month"):
        out.append(tr("{used}M of {limit}M tokens", used=f"{(t.get('fresh') or 0) / 1e6:,.0f}",
                      limit=f"{b['tokens_month'] / 1e6:,.0f}"))
    if b.get("usd_month"):
        out.append(tr("${used} of ${limit}", used=f"{t.get('cost_usd') or 0:,.0f}", limit=f"{b['usd_month']:,.0f}"))
    return ", ".join(out)


def card_text(org, kind, nid, title, pct, amounts):
    what = tr("goal|dative") if kind == "goals" else tr("condition|dative")
    return tr("The {what} {node} ({title}) of {org} has used {pct}% of its monthly budget this month ({amounts}). "
              "Raise its budget, or narrow the work on it? OF only reads and warns: nothing stops by itself. A new "
              "budget is a goal_change diff that the structure keeper writes and you approve.",
              what=what, node=nid, title=title or nid, org=org, pct=pct, amounts=amounts)


def check(spend, docs, positions, mem, now, fyi, log=print, emit=None):
    """See the module's docstring. fyi(to, text) sends one --fyi message; emit is of_events.emit (tests replace it)."""
    emit = emit or of_events.emit
    if not spend or not isinstance(spend.get("orgs"), dict):
        return []
    month = spend.get("month") or time.strftime("%Y-%m", time.gmtime(now))
    if month != time.strftime("%Y-%m", time.gmtime(now)):
        return []  # last month's sums: nothing to say about this month yet
    told = mem.setdefault("budgets", {})
    for k in [k for k in told if not k.startswith(month + ":")]:
        told.pop(k)
    import of_pulse
    done = []
    for org, o in spend["orgs"].items():
        doc = docs.get(org) or {}
        for kind in ("goals", "conditions"):
            for nid, node in (o.get(kind) or {}).items():
                level, top = _level(node.get("share"))
                key = f"{month}:{org}:{kind}:{nid}"
                if not level or told.get(key, 0) >= level:
                    continue
                spec = (doc.get(kind) or {}).get(nid) if isinstance(doc.get(kind), dict) else {}
                pct, amounts = int(top * 100), _amounts(node)
                emit("budget_warning", {"org": org, "kind": kind[:-1], "node": nid, "month": month, "level": level,
                                        "share": node.get("share"), "budget": node.get("budget"),
                                        "total": node.get("total"), "forecast": node.get("forecast")},
                     event_id=of_events.stable_id("budget_warning", org, kind, nid, month, level),
                     position="of_platform", org=org)
                if level == 80 or told.get(key, 0) < 80:
                    keeper = of_pulse.structure_keeper(positions, org)
                    if keeper:
                        fc = node.get("forecast") or {}
                        fyi(f"{org}:{keeper}", f"Budget: the {kind[:-1]} {nid} is at {pct}% of its monthly budget "
                                               f"({amounts}); straight-line forecast to the month's end: "
                                               f"{(fc.get('fresh') or 0) / 1e6:,.0f}M tokens, ${fc.get('cost_usd') or 0:,.0f}. "
                                               "OF reads and warns, nothing stops (docs/registry.md, Budgets).")
                if level == 100:
                    to = card_to(doc, kind, nid, positions, org)
                    emit("agent_question", {
                        "detected_by": "declared", "source": CARD_SOURCE, "from_position": "of_platform", "to": to,
                        "topic_key": f"budget-{org}-{nid}-{month}", "blocking": False,
                        "questions": [{"question": card_text(org, kind, nid, (spec or {}).get("title"), pct, amounts),
                                       "options": [tr("raise the budget (a goal_change diff)"),
                                                   tr("narrow the work")]}]},
                         event_id=of_events.stable_id("budget_card", org, kind, nid, month),
                         position="of_platform", org=org)
                told[key] = level
                done.append((org, kind, nid, level))
                log(f"budget: {org} {kind[:-1]} {nid} at {pct}% ({level}%)")
    return done
