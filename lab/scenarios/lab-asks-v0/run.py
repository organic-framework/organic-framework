#!/usr/bin/env python3
"""lab-asks-v0: score the asks judge (bin/of_judge.py) and the question detectors (q-v0..q-v5) on labelled turns.

A question to the founder is found in two stages: a detector (bin/of_classify.py) picks closing messages as
candidates, and the judge (a small model) decides whether the agent waits on its owner. A miss at either stage
loses the question, so both are scored on the same labels.

real_cases.json names closing messages in this machine's tenant store by event id (an of.agent_question candidate,
or an of.turn_end no detector picked), with the label a person gave and its source. It reads the real store and
runs on the Mac only (docs/lab.md, Level 2); it prints only scores and event ids, never texts.

    run.py detectors                       every q-v version on the labels (free)
    run.py judge [--model haiku] [--jobs 4] [--out F]
                                           the judge's prompt on the labels (a paid run: about $0.0005 a case)
    run.py pipeline [--gate detectors|all] [--ids asks-117] [--runs 3]
                                           would the stages as built put each case on the founder's list (gate,
                                           rules, judge by majority); exit 1 when a labelled question is lost
    run.py cards [--since D]               the stop rule: pane cards OF filed that ended withdrawn or "not a question"
                                           (exit 1 above 1 in 5: tighten the judge before the gate opens)
    run.py sample [--since D] [--n 60] [--seed S]
                                           propose cases: every judged owner_question and skipped candidate since D,
                                           and n judged not_a_question at random (prints event ids)
    run.py silver --ids F [--model opus]   a stronger model's labels for the ids in F, to a local file for review;
                                           never committed as labels without a person's check

The judge's rules before the model (a candidate in a turn that filed an of ask is skipped; a message naming only
filed asks is cleared) are replayed with the queue as it stood at the case's time; --model-only scores the model
alone.
"""

import argparse
import concurrent.futures
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "bin"))
import of_classify  # noqa: E402
import of_judge  # noqa: E402

STATE = os.path.expanduser("~/.local/state/of/lab/lab-asks-v0")


def connect():
    import of_store
    return of_store.connect()


def _texts(line):
    try:
        j = json.loads(line)
    except ValueError:
        return []
    if j.get("type") != "assistant":
        return []
    return ["\n".join(b.get("text") or "" for b in ((j.get("message") or {}).get("content") or [])
                      if isinstance(b, dict) and b.get("type") == "text")]


def _text_from_head(transcript, head):
    """A turn_end's closing text: its stored last_message keeps the start, so match by the start near the offset."""
    key = (head or "")[:80]
    if not key:
        return ""
    try:
        t = json.loads(transcript)
        path, offset = os.path.expanduser(t["path"]), int(t["offset"])
        with open(path, "rb") as fh:
            start = max(0, offset - of_judge.TEXT_AT_BEFORE)
            fh.seek(start)
            chunk = fh.read(offset - start + of_judge.TEXT_AT_AFTER)
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    best, best_d, pos = "", None, start
    for line in chunk.split(b"\n"):
        here, pos = pos, pos + len(line) + 1
        if b'"assistant"' not in line:
            continue
        for text in _texts(line):
            if of_judge._mask(text).startswith(key):
                d = abs(here - offset)
                if best_d is None or d < best_d:
                    best, best_d = text, d
    return best


def case_input(conn, event_id):
    """What the judge read (or would have read) for this closing message: text, position, origin, peer, filed."""
    row = conn.execute("SELECT name, ts, session_id, position, transcript, payload FROM event WHERE event_id = ?",
                       (event_id,)).fetchone()
    if not row:
        return None
    name, ts, sid, pos, tr, pl = row
    p = json.loads(pl or "{}")
    if name == "of.agent_question":
        tail = ((p.get("questions") or [{}])[0] or {}).get("question") or ""
        text = of_judge._mask(of_judge.text_at(tr, tail)) or tail
    else:
        head = p.get("last_message") or ""
        text = of_judge._mask(_text_from_head(tr, head)) or head
    origin, peer, start = of_judge.turn_of(conn, sid, ts)
    asks = asks_at(conn, ts)
    filed = [a for a in asks if a.get("position") == pos and a["state"] == "open"
             and a["kind"].startswith(("question (declared", "task"))]
    founders = [a for a in asks if a["state"] == "open" and (a.get("to") or "founder") in ("founder", "owner")]
    return {"event_id": event_id, "ts": ts, "position": pos, "text": text, "origin": origin, "peer": peer,
            "filed": [(a["event_id"], a["text"]) for a in filed],
            "named": of_judge.named_asks(founders, text, pos),
            "rule": rule_at(conn, text, ts, asks, filed, start)}


_ASKS = {}


def asks_at(conn, ts):
    """The asks projection as it stood at ts."""
    if ts not in _ASKS:
        import of_asks
        rows = [r for r in of_asks.load(conn, since_iso=_days_before(ts, 7)) if r["ts"] <= ts]
        _ASKS[ts] = of_asks.project(rows)
    return _ASKS[ts]


def rule_at(conn, text, ts, asks, filed, turn_start):
    """The judge's rules before the model (of_judge.judge_one), with the queue as it stood at ts: an of ask filed in
    the same turn skips the candidate; a message naming only known asks, one at least open for the founder, no "?",
    and nothing else asked of the owner is cleared (of_judge.only_filed_ids)."""
    if turn_start and any(a["ts"] >= turn_start for a in filed):
        return "skipped"
    if "?" in text:
        return None
    ids = set(of_judge.ASK_ID.findall(text))
    if not ids or of_judge.says_more(text, ids):
        return None
    known = {x for (e,) in conn.execute("SELECT event_id FROM event WHERE name = 'of.agent_question' AND ts >= ? "
                                        "AND ts <= ?", (_days_before(ts, of_judge.KNOWN_DAYS), ts))
             for x in of_judge._short(e)}
    if not ids <= known:
        return None
    founders = {x for a in asks if a["state"] == "open" and (a.get("to") or "founder") in ("founder", "owner")
                for x in of_judge._short(a["event_id"])}
    return "filed-ids" if ids & founders else None


def _days_before(ts, days):
    from datetime import datetime, timedelta
    d = datetime.fromisoformat(ts.replace("Z", "+00:00")) - timedelta(days=days)
    return d.strftime("%Y-%m-%dT%H:%M:%S")


def load_cases():
    """The real labelled cases live outside the public tree, under lab-private/ (release README §2). Load them when
    present; without them the scenario runs its synthetic cases only. Override the base with OF_LAB_PRIVATE."""
    base = os.environ.get("OF_LAB_PRIVATE") or os.path.expanduser("~/organic-framework-build/lab-private")
    p = os.path.join(base, "scenarios", "lab-asks-v0", "real_cases.json")
    return json.load(open(p)) if os.path.exists(p) else []


def prf(pairs):
    """pairs of (want, got) booleans -> (precision, recall, tp, fp, fn, tn)."""
    tp = sum(1 for w, g in pairs if w and g)
    fp = sum(1 for w, g in pairs if g and not w)
    fn = sum(1 for w, g in pairs if w and not g)
    tn = sum(1 for w, g in pairs if not w and not g)
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    return prec, rec, tp, fp, fn, tn


def cmd_detectors(args):
    conn = connect()
    cases = load_cases()
    rows = []
    for c in cases:
        ci = case_input(conn, c["event_id"])
        if ci:
            rows.append((c, of_classify.questions(ci["text"])))
    print(f"lab-asks-v0 detectors: {len(rows)} cases, {sum(c['label'] == 'owner_question' for c, _ in rows)} positive")
    for v in sorted(rows[0][1]) if rows else []:
        p, r, tp, fp, fn, tn = prf([(c["label"] == "owner_question", q[v]) for c, q in rows])
        print(f"  {v}: precision {p:.2f} recall {r:.2f}  (tp {tp} fp {fp} fn {fn} tn {tn})")
    union = [(c["label"] == "owner_question", any(q.values())) for c, q in rows]
    p, r, tp, fp, fn, tn = prf(union)
    print(f"  any version (the candidate gate): precision {p:.2f} recall {r:.2f}  (tp {tp} fp {fp} fn {fn} tn {tn})")
    for c, q in rows:
        if c["label"] == "owner_question" and not any(q.values()):
            print(f"  missed by every version: {c['id']} {c['event_id']}")


def cmd_judge(args):
    conn = connect()
    cases = load_cases()
    inputs = [(c, case_input(conn, c["event_id"])) for c in cases]
    inputs = [(c, i) for c, i in inputs if i]
    cfg = dict(of_judge.config(), model=args.model, command=None)
    account = of_judge.accounts()[0]

    def one(pair):
        c, i = pair
        if i["rule"] and not args.model_only:
            return {"verdict": "not_a_question", "rule": i["rule"]}
        prompt = of_judge.user_prompt(i["text"], i["position"], i["origin"], i["peer"], i["filed"],
                                      i.get("named") or ())
        return of_judge.call(prompt, cfg=cfg, config_dir=account)

    with concurrent.futures.ThreadPoolExecutor(args.jobs) as ex:
        verdicts = list(ex.map(one, inputs))
    pairs, cost, err, wrong = [], 0.0, 0, []
    for (c, i), v in zip(inputs, verdicts):
        cost += v.get("cost_usd") or 0
        if v["verdict"] == "error":
            err += 1
            continue
        want, got = c["label"] == "owner_question", v["verdict"] == "owner_question"
        pairs.append((want, got))
        if want != got:
            wrong.append(f"{'fn' if want else 'fp'} {c['id']} {c['event_id']}")
    p, r, tp, fp, fn, tn = prf(pairs)
    print(f"lab-asks-v0 judge {of_judge.VERSION} on {args.model}: {len(pairs)} cases ({err} errors), "
          f"precision {p:.2f} recall {r:.2f} (tp {tp} fp {fp} fn {fn} tn {tn}), ${cost:.3f}")
    for w in wrong:
        print("  " + w)
    if args.out:
        with open(args.out, "w") as fh:
            for (c, _), v in zip(inputs, verdicts):
                fh.write(json.dumps({"id": c["id"], "event_id": c["event_id"], "label": c["label"],
                                     "verdict": v["verdict"], "reason": v.get("reason")}, ensure_ascii=False) + "\n")


def cmd_sample(args):
    conn = connect()
    judged = {}
    for (pl,) in conn.execute("SELECT payload FROM event WHERE name = 'of.agent_question_judged' AND ts >= ?",
                              (args.since,)):
        p = json.loads(pl or "{}")
        if p.get("verdict") in ("owner_question", "not_a_question", "skipped"):
            judged[p["ask_id"]] = p["verdict"]
    pos = sorted(a for a, v in judged.items() if v in ("owner_question", "skipped"))
    neg = sorted(a for a, v in judged.items() if v == "not_a_question")
    random.Random(args.seed).shuffle(neg)
    for a in pos + neg[:args.n]:
        print(a, judged[a])


def cmd_silver(args):
    conn = connect()
    ids = [line.split()[0] for line in open(args.ids) if line.strip()]
    cfg = dict(of_judge.config(), model=args.model, command=None)
    os.makedirs(STATE, mode=0o700, exist_ok=True)
    out = os.path.join(STATE, f"silver-{args.model}.jsonl")
    done = set()
    if os.path.exists(out):
        done = {json.loads(x)["event_id"] for x in open(out) if x.strip()}
    todo = [i for i in ids if i not in done]
    inputs = {eid: case_input(conn, eid) for eid in todo}  # the store in this thread; the calls in parallel
    account = of_judge.accounts()[0]

    def one(eid):
        i = inputs[eid]
        if not i:
            return eid, {"verdict": "error", "error": "not in store"}
        prompt = of_judge.user_prompt(i["text"], i["position"], i["origin"], i["peer"], i["filed"],
                                      i.get("named") or ())
        return eid, of_judge.call(prompt, cfg=cfg, config_dir=account)

    cost = 0.0
    with open(out, "a") as fh, concurrent.futures.ThreadPoolExecutor(args.jobs) as ex:
        for eid, v in ex.map(one, todo):
            cost += v.get("cost_usd") or 0
            fh.write(json.dumps(dict(v, event_id=eid), ensure_ascii=False) + "\n")
    print(f"silver labels by {args.model}: {len(todo)} new in {out}, ${cost:.3f}")


def cmd_pipeline(args):
    """Would the stages as built put each labelled closing message on the founder's list? The gate (a detector
    fires, or every closing message with --gate all), the judge's rules before the model, then the judge (--runs
    calls, a majority opens it). A position with owner_chat counts as seen: the founder reads it in that pane."""
    conn = connect()
    want = set(args.ids.split(",")) if args.ids else None
    cases = [c for c in load_cases() if want is None or c["id"] in want]
    inputs = [(c, case_input(conn, c["event_id"])) for c in cases]
    inputs = [(c, i) for c, i in inputs if i]
    cfg = dict(of_judge.config(), model=args.model, command=None)
    account = of_judge.accounts()[0]
    chat = of_judge.owner_chat()

    def one(pair):
        c, i = pair
        if args.gate == "detectors" and not any(of_classify.questions(i["text"]).values()):
            return "gate", 0, 0, 0.0
        if i["position"] in chat:
            return "owner-chat", 0, 0, 0.0
        if i["rule"]:
            return f"rule {i['rule']}", 0, 0, 0.0
        prompt = of_judge.user_prompt(i["text"], i["position"], i["origin"], i["peer"], i["filed"],
                                      i.get("named") or ())
        vs = [of_judge.call(prompt, cfg=cfg, config_dir=account) for _ in range(args.runs)]
        ok = [v for v in vs if v["verdict"] != "error"]
        yes = sum(v["verdict"] == "owner_question" for v in ok)
        cost = sum(v.get("cost_usd") or 0 for v in vs)
        return ("listed" if ok and yes * 2 > len(ok) else "judge"), yes, len(ok), cost

    with concurrent.futures.ThreadPoolExecutor(args.jobs) as ex:
        out = list(ex.map(one, inputs))
    pairs, cost = [], 0.0
    for (c, i), (stage, yes, n, usd) in zip(inputs, out):
        cost += usd
        positive, reached = c["label"] == "owner_question", stage in ("listed", "owner-chat")
        pairs.append((positive, reached))
        if positive != reached or args.ids:
            what = "reaches him" if reached else f"lost at the {stage}"
            print(f"  {'ok  ' if positive == reached else 'fn  ' if positive else 'fp  '}{c['id']} {c['event_id']}: "
                  f"{what}" + (f" ({yes}/{n} judge runs)" if n else ""))
    p, r, tp, fp, fn, tn = prf(pairs)
    print(f"lab-asks-v0 pipeline: gate {args.gate}, judge {of_judge.VERSION} on {args.model}, {args.runs} run(s): "
          f"{len(pairs)} cases, precision {p:.2f} recall {r:.2f} (tp {tp} fp {fp} fn {fn} tn {tn}), ${cost:.3f}")
    return 0 if not fn else 1


def cmd_cards(args):
    """The stop rule (of_architect, 2026-11-11): of the pane cards OF filed (source pane) since --since, the share
    that ended withdrawn or answered "not a question". Above 1 in 5, tighten the judge before the gate opens. Exit 1 then."""
    import collections
    import of_asks
    conn = connect()
    asks = of_asks.project(of_asks.load(conn, since_iso=args.since))
    cards = [a for a in asks if a.get("source") == "pane" and a["ts"] >= args.since]
    answer = {}
    for (pl,) in conn.execute("SELECT payload FROM event WHERE name = 'of.agent_question_answered' AND ts >= ?",
                              (args.since,)):
        p = json.loads(pl or "{}")
        for i in p.get("ask_ids") or [p.get("ask_id")]:
            if i:
                answer[i] = str(p.get("answer") or "")
    n = collections.Counter()
    for a in cards:
        st = a["state"]
        if st == "answered" and of_judge.is_not_a_question(answer.get(a["event_id"], "")):
            st = "not a question"
        n[st] += 1
    closed = len(cards) - n["open"]
    false = n["withdrawn"] + n["not a question"]
    rate = false / closed if closed else float("nan")
    print(f"lab-asks-v0 cards since {args.since}: {len(cards)} pane cards, {closed} closed, "
          f"{false} withdrawn or \"not a question\" ({rate:.2f} of closed; the stop rule is 0.20)  "
          + ", ".join(f"{k} {v}" for k, v in sorted(n.items())))
    return 1 if closed and rate > 0.2 else 0


def main(argv):
    ap = argparse.ArgumentParser(prog="lab-asks-v0")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("detectors")
    j = sub.add_parser("judge")
    j.add_argument("--model", default=of_judge.MODEL)
    j.add_argument("--jobs", type=int, default=4)
    j.add_argument("--out")
    j.add_argument("--model-only", action="store_true", help="skip the judge's rules before the model")
    s = sub.add_parser("sample")
    s.add_argument("--since", default="2026-11-08")
    s.add_argument("--n", type=int, default=60)
    s.add_argument("--seed", type=int, default=1110)
    cd = sub.add_parser("cards")
    cd.add_argument("--since", default="2026-11-10T20:45:00")  # pane cards from here (hand-over cc2ed950)
    pl = sub.add_parser("pipeline")
    pl.add_argument("--gate", choices=["detectors", "all"], default=getattr(of_judge, "GATE", "detectors"),
                    help="which closing messages reach the judge (default: as built, of_judge.GATE)")
    pl.add_argument("--ids", help="comma-separated case ids, e.g. asks-117")
    pl.add_argument("--runs", type=int, default=3)
    pl.add_argument("--model", default=of_judge.MODEL)
    pl.add_argument("--jobs", type=int, default=4)
    v = sub.add_parser("silver")
    v.add_argument("--ids", required=True)
    v.add_argument("--model", default="opus")
    v.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args(argv)
    return {"cards": cmd_cards, "detectors": cmd_detectors, "judge": cmd_judge, "pipeline": cmd_pipeline, "sample": cmd_sample,
            "silver": cmd_silver}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
