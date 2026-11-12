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
SEGMENT_KEYS = {"start", "end", "context"}  # the target's controlled segment (docs/registry.md, Works)
CONTEXT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
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
        out += _check_segment(t, loop.get("measures"), tag, warn)
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


def _norm(text):
    return " ".join(str(text).split()).casefold()


def _check_segment(target, measures, tag, warn):
    """The target's controlled segment: the part of the chain the organization controls, named by the events that
    start and end it; the parts outside it are context, never a target or a measure (docs/registry.md, Works,
    "The target's controlled segment"; the founder's correction through of_conductor, 2026-11-12)."""
    seg = target.get("segment")
    if seg is None:
        return [f"{warn}{tag}: target has no segment (the part the organization controls, from its start event to "
                "its end event; docs/registry.md, Works)"]
    if not isinstance(seg, dict):
        return [f"{tag}: target.segment must be a mapping (start, end, context)"]
    out = [f"{warn}{tag}: target.segment: unknown key {k!r}" for k in sorted(set(seg) - SEGMENT_KEYS)]
    ends = {}
    for k in ("start", "end"):
        if not isinstance(seg.get(k), str) or not seg[k].strip():
            out.append(f"{tag}: target.segment.{k} is required (the event that {k}s the controlled segment)")
        else:
            ends[k] = _norm(seg[k])
    if len(ends) == 2 and ends["start"] == ends["end"]:
        out.append(f"{tag}: target.segment: start and end are the same event")
    ctx = seg.get("context")
    if ctx is None:
        return out
    if not isinstance(ctx, list):
        return out + [f"{tag}: target.segment.context must be a list (the parts outside the segment)"]
    names = set()
    for i, c in enumerate(ctx):
        if isinstance(c, str) and c.strip():
            continue
        if not isinstance(c, dict) or not isinstance(c.get("what"), str) or not c["what"].strip():
            out.append(f"{tag}: target.segment.context[{i}] is neither a text nor a mapping with what (and an "
                       "optional name)")
            continue
        if c.get("name") is not None:
            if not isinstance(c["name"], str) or not CONTEXT_NAME.match(c["name"]):
                out.append(f"{tag}: target.segment.context[{i}].name must match {CONTEXT_NAME.pattern}")
            elif c["name"] in names:
                out.append(f"{tag}: target.segment.context: the name {c['name']!r} appears twice")
            else:
                names.add(c["name"])
        for k in sorted(set(c) - {"name", "what"}):
            out.append(f"{warn}{tag}: target.segment.context[{i}]: unknown key {k!r}")
    metric = target.get("metric")
    for c in ctx:
        if isinstance(metric, str) and (context_matches(metric, c, prose=True) or context_matches(metric, c)):
            out.append(f"{tag}: target.metric {metric!r} matches the context part {part_label(c)!r}, outside the "
                       "controlled segment")
    for m in measures if isinstance(measures, list) else []:
        for c in ctx:
            if isinstance(m, dict) and m.get("name") and context_matches(m["name"], c):
                out.append(f"{tag}: measure {m['name']!r} is the context part {part_label(c)!r}, outside the "
                           "controlled segment")
    return out


STOP = frozenset("a an the of to in on at by for from and or with without after before into onto its it is are be "
                 "our their his her your my this that these those per via".split())


def _fold(w):
    """A plural as its singular, roughly: rules -> rule, parties -> party; process and bus stay."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def content_words(text):
    """The words of a text that carry its meaning: letters and digits, lowercased, plurals folded, without the small
    words and one-letter pieces (a possessive's s: of_field-analyst, 2026-11-12)."""
    return {_fold(w) for w in re.findall(r"[^\W_]+", str(text or "").casefold()) if len(w) > 1 and w not in STOP}


def part_label(part):
    return part.get("name") or part.get("what") if isinstance(part, dict) else part


def context_matches(field, part, prose=False):
    """True when a field matches a context part (docs/registry.md, Works, "How a check matches a context part"):
    a name (a measure's name, a metric that is a name) matches a part with the same name; a prose field (a
    hypothesis's change or prediction, a target's metric) matches when every content word of the part's text is
    among its words. For the loop rules too (of_field-analyst)."""
    if not prose:
        return isinstance(part, dict) and bool(part.get("name")) and str(field).strip() == part["name"]
    words = content_words(part.get("what") if isinstance(part, dict) else part)
    return bool(words) and words <= content_words(field)


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
