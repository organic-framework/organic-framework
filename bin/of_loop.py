"""Development loops (RFC 0005, docs/rfc/0005-development-loops.md, steps 1 and 2): the `loop:` of a work and its check,
and the hypothesis register and decision log as events.

- check_loop(loop, wid): the schema of RFC 0005 §4 (target, measures, ladder, checkpoint_days, wip_arms, babysit_model).
- of.loop_hypothesis {work, hypothesis, change, predict, cost, cost_h, voi}: recorded before its test.
- of.loop_run {work, rung, hypothesis, result, evidence, source}: recorded when a run on a rung ends.
- of.loop_decision {work, decision, hypothesis, rung, money, decided, note}: keep, drop, park, reopen, step_up, step_back,
  change_objective (which names its money metric, or on a target the owner decided, the owner's yes on the new one).
- register(rows, work): the hypotheses with their derived status (open, testing, kept, dropped, parked), the next one
  first by value of information per runner-hour (an absent voi counts as med). No agent reports progress: status
  comes from runs and decisions.
- decided(loop): the id a target names as the owner's yes on its measure (docs/registry.md, Works, "The owner's
  measure"). It counts only once resolve_decided finds that yes in the log (of_review on PR 168: any text had lifted
  the money requirement); then money is a reading, measures chain to the target or to money, and no position asks the
  owner for a money proof.
"""

import hashlib
import re
import time

RUNG_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
VOI = {"low": 1, "med": 2, "high": 3}
DECISIONS = ("keep", "drop", "park", "reopen", "step_up", "step_back", "change_objective")
# reopen puts a parked, dropped or kept hypothesis back to open, with a note naming what changed or whose word reopened
# it (RFC 0005 §4.1; 2026-11-14: a revived hypothesis was recorded as keep, which reads as one that won)
STATUS_OF = {"keep": "kept", "drop": "dropped", "park": "parked", "reopen": "open"}
REOPENS = ("parked", "dropped", "kept")  # the statuses reopen leaves; open and testing are open already
MODELS = ("haiku", "sonnet", "opus", "fable")
LOOP_KEYS = {"target", "measures", "ladder", "checkpoint_days", "wip_arms", "babysit_model"}
SEGMENT_KEYS = {"start", "end", "context"}  # the target's controlled segment (docs/registry.md, Works)
CONTEXT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
THROUGHPUT = ("raise", "sustain", "expense", "measure", "coordinate")


def decided(loop):
    """The owner's yes on the target's measure (target.decided: an ask id or the event id of the owner's own prompt),
    or None. Only a non-empty text counts (docs/registry.md, Works, "The owner's measure")."""
    t = (loop or {}).get("target") if isinstance(loop, dict) else None
    d = t.get("decided") if isinstance(t, dict) else None
    return d.strip() if isinstance(d, str) and d.strip() else None


UNREAD = ("unread", None)  # a resolver's answer when this machine's log cannot be read: no exception, only a warning
HUMAN_ANSWER = "owner"  # `by` of an answer a person gave
# the person's own channels: the popup they click at the machine, and their conductor's chat, whose prompt `of asks
# answer` checks. `by: owner` alone is no proof: `of asks answered` writes it from any process without an agent's
# session variable (of_server on organic-framework PR 168; the diff approval reads asks-ui only, PR 154)
PERSON_CHANNELS = ("asks-ui", "conductor")
_ID = re.compile(r"^[0-9a-f][0-9a-f-]{7,35}$")


class Yes(tuple):
    """(kind, event id) of a person's yes, equal to that pair, with `words`: what they said, for a reader to check that
    it names the measure (of_review on organic-framework PR 168: no code can)."""

    def __new__(cls, kind, eid, words=""):
        y = super().__new__(cls, (kind, eid))
        y.words = words
        return y


def _short(text, n=160):
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[:n - 1] + "…"


def _option_text(o):
    return " ".join(str(o.get("label") if isinstance(o, dict) else o).split()).casefold()


def resolve_decided(conn, did, typed_by_person=None, work=None, goal_yes=None, kinds=("ask", "prompt")):
    """Yes(kind, event id, words) of the person's yes that DID names in the log, or None (docs/registry.md, Works,
    "Where it is read, and how it is checked"; of_review on organic-framework PR 168):
    - 'ask': DID is `<ask id>#<option number>`, the option the ask was filed with as its yes (`of ask --yes <n>`,
      `yes_option`; of_review on PR 168: a refusal cited by its own number had counted). The ask was filed for WORK
      (`of ask --work`), or it is the yes GOAL_YES (the decided of the goal the work serves) records; the owner's latest answer through their own channel (`by:
      owner` with `via` asks-ui or conductor, PERSON_CHANNELS; never an agent's closure or `of asks answered`) chose
      exactly that option: its number (`option`, through the conductor's chat) or its text (the popup).
      A free-text answer, another option, a refusal or an ask of another work is no yes. Its words are the question
      and the chosen option;
    - 'prompt': an of.human_prompt with origin human, not corrected to another origin by of.prompt_origin, recorded
      where only the person types: TYPED_BY_PERSON(position, host id) says so (bin/of: the session holding a
      conductor); without it, any such prompt. No code can tell whether its words are a yes; its first words are shown.
    KINDS limits what counts (a change of objective takes an ask only). The id part is an event id or its prefix of at
    least its first two groups, or an ask's short id (its last 8 characters, as `of asks` prints it); one that names no
    event, or several, is None. Reads by the primary key, and by one indexed name from the event's time."""
    import json
    did = str(did or "").strip().lower()
    m = re.fullmatch(r"(.+)#(\d{1,2})", did)
    did, option = (m.group(1), int(m.group(2))) if m else (did, None)
    if not _ID.match(did) or (len(did) < 13 and not re.fullmatch(r"[0-9a-f]{8}", did)):
        return None
    cols = "event_id, name, ts, payload, position, host_id"
    found = {r[0]: r for r in conn.execute(f"SELECT {cols} FROM event WHERE event_id >= ? AND event_id < ? LIMIT 2",
                                           (did, did + "~"))}
    if re.fullmatch(r"[0-9a-f]{8}", did):
        found.update({r[0]: r for r in conn.execute(
            f"SELECT {cols} FROM event WHERE name = 'of.agent_question' AND event_id LIKE ? LIMIT 2", ("%-%" + did,))
            if r[0].endswith(did)})
    if len(found) != 1:
        return None
    eid, name, ts, pl, position, host = next(iter(found.values()))
    p = json.loads(pl or "{}")
    if name == "of.human_prompt" and "prompt" in kinds and option is None:
        origin = p.get("origin")
        for (cp,) in conn.execute("SELECT payload FROM event WHERE name = 'of.prompt_origin' AND ts >= ? ORDER BY ts, "
                                  "rowid", (ts,)):
            c = json.loads(cp or "{}")
            if c.get("event_id") == eid:
                origin = c.get("origin")
        where = typed_by_person is None or typed_by_person(position, host)
        return Yes("prompt", eid, _short(p.get("text"))) if origin == "human" and where else None
    if name != "of.agent_question" or "ask" not in kinds or option is None:
        return None
    goal_ask = re.sub(r"#.*$", "", str(goal_yes or "").strip().lower())
    by_goal = bool(goal_ask) and (eid.startswith(goal_ask) or eid.endswith(goal_ask))
    if not (by_goal or (work is not None and p.get("work") == work)):
        return None
    qs = p.get("questions") if isinstance(p.get("questions"), list) else []
    q = qs[0] if qs and isinstance(qs[0], dict) else {}
    opts = q.get("options") if isinstance(q.get("options"), list) else []
    if not 1 <= option <= len(opts) or p.get("yes_option") != option:  # only the option filed as the yes (of ask
        return None                                                   # --yes): a refusal is never one, whoever cites it
    last = None
    for (ap,) in conn.execute("SELECT payload FROM event WHERE name = 'of.agent_question_answered' AND ts >= ? "
                              "ORDER BY ts, rowid", (ts,)):
        a = json.loads(ap or "{}")
        if eid in (a.get("ask_ids") or [a.get("ask_id")]) and a.get("by") == HUMAN_ANSWER \
                and a.get("via") in PERSON_CHANNELS:
            last = a
    if not last:
        return None
    chosen = last.get("option") if isinstance(last.get("option"), int) else next(
        (i + 1 for i, o in enumerate(opts) if _option_text(o) == _option_text(last.get("answer") or "")), None)
    if chosen != option:
        return None
    label = opts[option - 1].get("label") if isinstance(opts[option - 1], dict) else opts[option - 1]
    said = (f', in their words "{_short(last.get("quote"), 80)}"'  # the chat's option is the conductor's reading
            if last.get("via") == "conductor" and last.get("quote") else "")
    return Yes("ask", eid, f'asked "{_short(q.get("question"), 100)}", chose option {option} "{_short(label, 80)}"'
               + said)


def owners_yes(loop, resolve):
    """The target's decided id when RESOLVE (an id -> truthy) finds it in the log as the owner's yes, else None."""
    d = decided(loop)
    found = resolve(d) if d and resolve else None
    return d if found and found != UNREAD else None


def check_loop(loop, wid, warn="warning: ", resolve=None):
    """Problems with a work's loop (RFC 0005 §4); warnings start with `warn`. On a target the owner decided, money is
    optional and a measure chains to the target (to_target) or to money (docs/registry.md, Works, "The owner's
    measure"). With RESOLVE (`of registry check`: resolve_decided over this machine's log), a decided id the log does
    not hold as the owner's yes is a problem and counts as no decision; without it (every other `of` call, which reads
    no log), the id is taken as written."""
    tag = f"{wid}: loop"
    if not isinstance(loop, dict):
        return [f"{tag} must be a mapping"]
    out = [f"{warn}{tag}: unknown key {k!r}" for k in sorted(set(loop) - LOOP_KEYS)]
    t = loop.get("target")
    owners = decided(loop)
    found = resolve(owners) if owners and resolve is not None else True
    if found == UNREAD:
        out.append(f"{warn}{tag}: target.decided {owners!r} could not be looked up: this machine's event log does not "
                   "open, so the target gets no exception here")
        owners = None
    elif not found:
        out.append(f"{tag}: target.decided {owners!r} is not the owner's yes in this machine's log (an ask for this "
                   "work with the option the owner chose, <ask id>#<n>, or a prompt typed in their conductor's session): it lifts nothing until "
                   "it is")
        owners = None
    if not isinstance(t, dict):
        out.append(f"{tag}: target is required (metric, and money unless the owner decided the measure)")
    else:
        if t.get("decided") is not None and not decided(loop):
            out.append(f"{tag}: target.decided is the owner's yes on the measure: their ask id, or the event id of "
                       "their own prompt that names it")
        for k in ("metric",) if owners else ("metric", "money"):
            if not isinstance(t.get(k), str) or not t[k].strip():
                out.append(f"{tag}: target.{k} is required" + (" (or decided, the owner's yes on the measure; "
                                                                "docs/registry.md, Works)" if k == "money" else ""))
        if owners and t.get("money") is not None and (not isinstance(t["money"], str) or not t["money"].strip()):
            out.append(f"{tag}: target.money, when given, is a text (a reading shown beside the target)")
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
            elif owners:
                if not str(m.get("to_money") or "").strip() and not str(m.get("to_target") or "").strip():
                    out.append(f"{tag}: measure {m['name']!r} needs to_target (how it moves the target the owner "
                               "decided) or to_money")
            elif not str(m.get("to_money") or "").strip():
                out.append(f"{tag}: measure {m['name']!r} needs to_money (its chain to money, RFC 0005 §6"
                           + ("; to_target only on a target the owner decided" if m.get("to_target") else "") + ")")
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


def target_line(loop, resolve):
    """How `of loop show` and the session's start context name a target: its metric, and on a target the owner
    decided (its id found by RESOLVE), that yes's id and that its money is reported, never asked for as a proof. A
    decided id RESOLVE does not find is named as unresolved, never as the owner's decision."""
    t = (loop or {}).get("target") if isinstance(loop, dict) else None
    t = t if isinstance(t, dict) else {}
    line = f"target {t.get('metric')}" + (f" ({t['better']} is better)" if t.get("better") in ("higher", "lower") else "")
    d = decided(loop)
    if not d:
        return line
    if not owners_yes(loop, resolve):
        return (line + f"; it names {d} as the owner's yes, but this machine's log holds no such yes (an ask for this "
                       "work with the option the owner chose, or a prompt typed in their conductor's session): unresolved, so it is not the owner's decision and the loop "
                       "needs its chain to money")
    words = getattr(resolve(d), "words", "")
    line += (f"; the owner decided this measure ({d}" + (f": {words}" if words else "") + "): its money is reported beside it"
             + (f" as a reading ({' '.join(str(t['money']).split())})" if isinstance(t.get("money"), str) and t["money"].strip() else "")
             + ", never asked of the owner as a proof")
    return line


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
                h["status"], h["last"], h["decided_at"] = STATUS_OF[p["decision"]], r["ts"], r["ts"]
    live = {"open": 0, "testing": 0}
    for h in hyps.values():  # accepted is the rung's accept criterion (docs/events.md), not the prediction: say only
        # that the latest run missed it while no decision closed the hypothesis (of_conductor, 2026-11-12: an open
        # hypothesis was read as a finding; of_review on PR 61)
        lr = h.get("last_run") or {}  # a run before a reopen is answered by that decision
        h["unmet_open"] = (h["status"] in live and lr.get("accepted") is False
                           and lr.get("ts", "") > h.get("decided_at", ""))

    def key(h):  # an absent voi counts as med (docs/registry.md, Works, "The owner's measure"); money never enters it
        per = VOI.get(h["voi"] or "med", 1) / max(h.get("cost_h") or 0.0, 0.05)
        return (h["status"] not in live, -per, h["since"])
    return {"hypotheses": sorted(hyps.values(), key=key), "runs": runs, "decisions": decs}
