#!/usr/bin/env python3
"""Hook load step (docs/lab.md, section 10; of_platform's ask (d)).

Runs N synthetic sessions in parallel inside a lab machine. Each session pipes a
realistic sequence of Claude Code hook payloads into `of hook <Event>`, as the
harness does (one process per hook). Measures wall time per hook call, counts
the events written, and reads hook-errors.log. No model is called.

Usage: load.py [--sessions 30] [--turns 10] [--org-dir ~/lab-org]
"""

import argparse
import glob
import sqlite3
import random
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import uuid

OF = os.path.expanduser("~/organic-framework/bin/of")
STATE = os.environ.get("OF_STATE") or os.path.expanduser("~/.local/state/of")
WRITTEN = {}  # transcript record uuid -> time written (just before its Stop hook)


def sampler(stop, samples, seen):
    """Every 0.5 s: which transcript records are in the DB (worker lag) and spool sizes."""
    db = os.path.join(STATE, "of.db")
    while not stop.is_set():
        now = time.time()
        rows = set()
        try:
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.2)
            rows = {r[0] for r in c.execute("select record_uuid from transcript_record")}
            c.close()
        except Exception:
            pass
        for u in rows:
            if u in WRITTEN and u not in seen:
                seen[u] = now - WRITTEN[u]
        def lines(name):
            try:
                with open(os.path.join(STATE, "spool", name)) as fh:
                    return sum(1 for _ in fh)
            except FileNotFoundError:
                return 0
        samples.append({"t": round(now, 1), "spool_events": lines("events.jsonl"), "spool_raw": lines("hook-raw.jsonl"),
                        "pending": len(glob.glob(os.path.join(STATE, "pending", "*"))),
                        "ingested": sum(1 for u in rows if u in WRITTEN), "written": len(WRITTEN)})
        time.sleep(0.5)


def setup_org(org_dir, sids):
    os.makedirs(os.path.join(org_dir, "registry"), exist_ok=True)
    lines = ["tenant: lab", f"organization: {os.environ.get('OF_LAB_ORG', 'lab-load')}", "positions:"]
    for i, sid in enumerate(sids):
        lines += [f"  - name: lab_s{i:02d}", "    system: lab", f"    role: s{i:02d}", "    kind: project",
                  "    throughput: raise", "    owner: founder", "    summary: load", "    status: active",
                  "    holder:", "      runtime: claude-code", f"      session: {sid}", "    aliases: []"]
    with open(os.path.join(org_dir, "registry", "positions.yaml"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    orgs = os.path.join(org_dir, "orgs.yaml")
    with open(orgs, "w") as fh:
        fh.write(f"orgs:\n  - name: {os.environ.get('OF_LAB_ORG', 'lab-load')}\n    path: {org_dir}\n")
    return orgs


def payloads(sid, transcript, turns):
    base = {"session_id": sid, "transcript_path": transcript, "cwd": os.path.expanduser("~"),
            "permission_mode": "bypassPermissions"}
    yield "SessionStart", {**base, "hook_event_name": "SessionStart", "source": "startup", "model": "claude-sonnet-5-5"}
    for t in range(turns):
        yield "UserPromptSubmit", {**base, "hook_event_name": "UserPromptSubmit",
                                   "prompt": f"turn {t}: please list the files and fix the test " + "x" * 400}
        for k in range(3):
            tid = f"toolu_{uuid.uuid4().hex[:12]}"
            ti = {"command": f"ls -la /tmp/{k}"}
            yield "PreToolUse", {**base, "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": ti,
                                 "tool_use_id": tid}
            yield "PostToolUse", {**base, "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": ti,
                                  "tool_use_id": tid, "tool_response": {"stdout": "ok\n" * 50}}
        with open(transcript, "a") as fh:  # the harness appends transcript lines between hooks
            ru = str(uuid.uuid4())
            WRITTEN[ru] = time.time()
            fh.write(json.dumps({"type": "assistant", "uuid": ru, "sessionId": sid,
                                 "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 "message": {"role": "assistant", "content": [{"type": "text", "text": "done " * 50}]}}) + "\n")
        yield "Stop", {**base, "hook_event_name": "Stop", "stop_hook_active": False,
                       "last_assistant_message": f"Turn {t} done. Shall I continue?",
                       "session_crons": [], "background_tasks": []}
    yield "SessionEnd", {**base, "hook_event_name": "SessionEnd", "reason": "other"}


def run_session(sid, transcript, turns, env, out, think=0.0):
    rng = random.Random(sid)
    for event, p in payloads(sid, transcript, turns):
        if think:
            time.sleep(rng.uniform(0, 2 * think))
        t0 = time.perf_counter()
        r = subprocess.run([OF, "hook", event], input=json.dumps(p).encode(), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        out.append((event, (time.perf_counter() - t0) * 1000, r.returncode))


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=30)
    ap.add_argument("--turns", type=int, default=10)
    ap.add_argument("--think", type=float, default=0.0, help="mean seconds between hooks per session")
    ap.add_argument("--org-dir", default=os.path.expanduser("~/lab-org"))
    a = ap.parse_args()
    sids = [str(uuid.uuid4()) for _ in range(a.sessions)]
    env = dict(os.environ, OF_ORGS=setup_org(a.org_dir, sids))
    tdir = os.path.expanduser("~/lab-transcripts")
    os.makedirs(tdir, exist_ok=True)
    out, threads = [], []
    stop, samples, seen = threading.Event(), [], {}
    smp = threading.Thread(target=sampler, args=(stop, samples, seen), daemon=True)
    smp.start()
    t0 = time.time()
    for sid in sids:
        th = threading.Thread(target=run_session, args=(sid, os.path.join(tdir, sid + ".jsonl"), a.turns, env, out, a.think))
        th.start()
        threads.append(th)
    for th in threads:
        th.join()
    wall = time.time() - t0
    deadline = time.time() + 180  # let the worker drain
    while time.time() < deadline and len(seen) < len(WRITTEN):
        time.sleep(1)
    drain_s = round(time.time() - t0 - wall, 1)
    stop.set()
    smp.join()
    ms = [m for _, m, _ in out]
    by = {}
    for e, m, _ in out:
        by.setdefault(e, []).append(m)
    report = {
        "sessions": a.sessions, "think_s": a.think, "hook_calls": len(out), "wall_s": round(wall, 1),
        "nonzero_exit": sum(1 for *_, rc in out if rc != 0),
        "latency_ms": {"p50": round(pct(ms, .5)), "p95": round(pct(ms, .95)), "p99": round(pct(ms, .99)),
                       "max": round(max(ms)), "mean": round(statistics.mean(ms))},
        "over_200ms": sum(1 for m in ms if m > 200),
        "by_event_p99": {e: round(pct(v, .99)) for e, v in sorted(by.items())},
    }
    lags = sorted(seen.values())
    report["worker_lag_s"] = ({"p50": round(pct(lags, .5), 1), "p95": round(pct(lags, .95), 1), "max": round(max(lags), 1)}
                              if lags else None)
    report["transcript_lines"] = {"written": len(WRITTEN), "ingested": len(seen), "drain_after_run_s": drain_s}
    report["spool_max"] = {k: max(s_[k] for s_ in samples) for k in ("spool_events", "spool_raw", "pending")} if samples else None
    report["spool_end"] = {k: samples[-1][k] for k in ("spool_events", "spool_raw", "pending")} if samples else None
    state = STATE
    try:
        c = sqlite3.connect(os.path.join(state, "of.db"))
        report["events_in_db"] = dict(c.execute("select name, count(*) from event group by 1").fetchall())
        report["raw_hook_records"] = dict(c.execute("select hook_event, count(*) from hook_record group by 1").fetchall())
        c.close()
    except Exception as e:
        report["events_in_db"] = str(e)
    try:
        with open(os.path.join(state, "events.jsonl")) as fh:
            ev = [json.loads(l) for l in fh]  # legacy fallback file
        mine = [e for e in ev if e.get("gen_ai.conversation.id") in set(sids)]
        report["events_written"] = len(mine)
        report["events_with_position"] = sum(1 for e in mine if e.get("gen_ai.agent.name"))
        kinds = {}
        for e in mine:
            kinds[e["event.name"]] = kinds.get(e["event.name"], 0) + 1
        report["events_by_kind"] = kinds
    except FileNotFoundError:
        report["events_written"] = 0
    try:
        with open(os.path.join(state, "hook-errors.log")) as fh:
            report["hook_errors"] = fh.read().splitlines()[-10:]
    except FileNotFoundError:
        report["hook_errors"] = []
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    sys.exit(main())
