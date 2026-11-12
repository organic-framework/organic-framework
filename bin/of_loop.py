"""Development loops (RFC 0005, docs/rfc/0005-development-loops.md, steps 1 and 2): the `loop:` of a work and its check,
and the hypothesis register and decision log as events.

- check_loop(loop, wid): the schema of RFC 0005 §4 (target, measures, ladder, checkpoint_days, wip_arms, babysit_model).
- of.loop_hypothesis {work, hypothesis, change, predict, cost, cost_h, voi}: recorded before its test.
- of.loop_run {work, rung, hypothesis, result, evidence, source}: recorded when a run on a rung ends.
- of.loop_decision {work, decision, hypothesis, rung, money, note}: keep, drop, park, step_up, step_back,
  change_objective (which names its money metric).
- register(rows, work): the hypotheses with their derived status (open, testing, kept, dropped, parked), the next one
  first by value of information per runner-hour. No agent reports progress: status comes from runs and decisions.
"""

import hashlib
import re
import time

RUNG_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
VOI = {"low": 1, "med": 2, "high": 3}
DECISIONS = ("keep", "drop", "park", "step_up", "step_back", "change_objective")
STATUS_OF = {"keep": "kept", "drop": "dropped", "park": "parked"}
MODELS = ("haiku", "sonnet", "opus", "fable")
LOOP_KEYS = {"target", "measures", "ladder", "checkpoint_days", "wip_arms", "babysit_model"}
THROUGHPUT = ("raise", "sustain", "expense", "measure", "coordinate")


def check_loop(loop, wid, warn="warning: "):
    """Problems with a work's loop (RFC 0005 §4); warnings start with `warn`."""
    tag = f"{wid}: loop"
    if not isinstance(loop, dict):
        return [f"{tag} must be a mapping"]
    out = [f"{warn}{tag}: unknown key {k!r}" for k in sorted(set(loop) - LOOP_KEYS)]
    t = loop.get("target")
    if not isinstance(t, dict):
        out.append(f"{tag}: target is required (metric, money)")
    else:
        for k in ("metric", "money"):
            if not isinstance(t.get(k), str) or not t[k].strip():
                out.append(f"{tag}: target.{k} is required")
        if t.get("throughput_class") is not None and t["throughput_class"] not in THROUGHPUT:
            out.append(f"{tag}: target.throughput_class {t['throughput_class']!r} is not one of {', '.join(THROUGHPUT)}")
        if t.get("better") is not None and t["better"] not in ("higher", "lower"):
            out.append(f"{tag}: target.better is higher or lower")
    ms = loop.get("measures") or []
    if not isinstance(ms, list):
        out.append(f"{tag}: measures must be a list")
    else:
        for i, m in enumerate(ms):
            if not isinstance(m, dict) or not m.get("name"):
                out.append(f"{tag}: measures[{i}] needs a name")
            elif not str(m.get("to_money") or "").strip():
                out.append(f"{tag}: measure {m['name']!r} needs to_money (its chain to money, RFC 0005 §6)")
    lad = loop.get("ladder")
    if not isinstance(lad, list) or not lad:
        out.append(f"{tag}: ladder is required, cheapest rung first")
    else:
        seen = set()
        for i, r in enumerate(lad):
            rid = (r or {}).get("rung") if isinstance(r, dict) else None
            if not rid or not RUNG_ID.match(str(rid)):
                out.append(f"{tag}: ladder[{i}].rung must match {RUNG_ID.pattern}")
                continue
            if rid in seen:
                out.append(f"{tag}: rung {rid!r} appears twice")
            seen.add(rid)
            if not str(r.get("accept") or "").strip():
                out.append(f"{warn}{tag}: rung {rid!r} has no accept criterion")
            if r.get("production") is not None and not isinstance(r["production"], bool):
                out.append(f"{tag}: rung {rid!r}: production is true or false (shadow and higher are production)")
            if r.get("since") is not None and not re.match(r"^\d{4}-\d{2}-\d{2}", str(r["since"])):
                out.append(f"{tag}: rung {rid!r}: since is a date (YYYY-MM-DD)")
    for k in ("checkpoint_days", "wip_arms"):
        v = loop.get(k)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 1):
            out.append(f"{tag}: {k} must be a positive whole number")
    bm = loop.get("babysit_model")
    if bm is not None and str(bm).split("-")[0] not in MODELS and not str(bm).startswith("claude-"):
        out.append(f"{warn}{tag}: babysit_model {bm!r} is not a known model family ({', '.join(MODELS)})")
    return out


def rungs(loop):
    return [r["rung"] for r in (loop or {}).get("ladder") or [] if isinstance(r, dict) and r.get("rung")]


def cost_hours(cost):
    """Runner-hours in a cost text ('day_pass, 6 runner-h', '40 min', '0'); 1 when it says none."""
    t = str(cost or "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:runner-)?h\b", t)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*min\b", t)
    if m:
        return float(m.group(1)) / 60
    return 0.0 if re.fullmatch(r"\s*0\s*", t) else 1.0


def cost_of(text, ladder_rungs=()):
    """{rung, runner_h, text} from a cost text such as 'day_pass, 6 runner-h' (docs/studio.md 5.6)."""
    t = str(text or "")
    first = re.split(r"[,;\s]", t.strip(), 1)[0] if t.strip() else ""
    return {"rung": first if first in ladder_rungs else None, "runner_h": cost_hours(t), "text": t or None}


def hypothesis_id(work, change, at=None):
    return hashlib.sha256(f"{work}|{change}|{at or time.time()}".encode()).hexdigest()[:8]


def register(rows, work):
    """{hypotheses: [...], runs: [...], decisions: [...]} for a work from its loop events (dicts: name, ts,
    payload). Each hypothesis: id, change, predict, cost, voi, status, runs, last, last_run ({rung, accepted, value,
    metric, ts} of its latest run) and unmet_open (still open or testing while its latest run did not meet its rung's
    accept criterion); open and testing ones first,
    by VOI per runner-hour (higher first), then oldest."""
    hyps, runs, decs = {}, [], []
    for r in sorted(rows, key=lambda x: x["ts"]):
        p = r.get("payload") or {}
        if p.get("work") != work:
            continue
        if r["name"] == "of.loop_hypothesis":
            hid = p.get("id") or p.get("hypothesis")
            if hid in hyps:  # a later event with the same id updates the hypothesis (its status)
                if p.get("status"):
                    hyps[hid]["status"], hyps[hid]["last"] = p["status"], r["ts"]
                continue
            cost = p.get("cost") if isinstance(p.get("cost"), dict) else {"text": p.get("cost"), "runner_h": p.get("cost_h", 1.0)}
            hyps[hid] = {"id": hid, "change": p.get("change"), "predict": p.get("predict"), "cost": cost.get("text"),
                         "cost_h": cost.get("runner_h", 1.0), "voi": p.get("voi"), "status": p.get("status") or "open",
                         "runs": 0, "since": r["ts"], "last": r["ts"]}
        elif r["name"] == "of.loop_run":
            runs.append(dict(p, ts=r["ts"]))
            h = hyps.get(p.get("hypothesis"))
            if h:
                h["runs"] += 1
                h["last"] = r["ts"]
                h["last_run"] = {k: p.get(k) for k in ("rung", "accepted", "value", "metric")} | {"ts": r["ts"]}
                if h["status"] == "open":
                    h["status"] = "testing"
        elif r["name"] == "of.loop_decision":
            decs.append(dict(p, ts=r["ts"]))
            h = hyps.get(p.get("hypothesis"))
            if h and p.get("decision") in STATUS_OF:
                h["status"], h["last"] = STATUS_OF[p["decision"]], r["ts"]
    live = {"open": 0, "testing": 0}
    for h in hyps.values():  # accepted is the rung's accept criterion (docs/events.md), not the prediction: say only
        # that the latest run missed it while no decision closed the hypothesis (of_conductor, 2026-11-12: an open
        # hypothesis was read as a finding; of_review on PR 61)
        h["unmet_open"] = h["status"] in live and (h.get("last_run") or {}).get("accepted") is False

    def key(h):
        per = VOI.get(h["voi"], 1) / max(h.get("cost_h") or 0.0, 0.05)
        return (h["status"] not in live, -per, h["since"])
    return {"hypotheses": sorted(hyps.values(), key=key), "runs": runs, "decisions": decs}
