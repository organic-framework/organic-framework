"""of_studio's views of the event log, on the shared fold contract (bin/of_fold.py; organic-framework-build
reports/of-log-readers-2026-11-14.md, 3.1 and 3.2). The listener keeps each by its own cursor; Studio's pages read them
with of_fold.read, never the log's whole history of these names.

- pr_states: the latest state of each open or closed PR (of.pr_state, one event per change), by organization and PR:
  an open PR quiet for weeks still shows on the Works list and in code activity.
- diffs_applied: each applied structural diff's id, kind, organization and time (of.diff_applied): when each delegation
  took effect, and which diffs a review counts. The diff's text stays in the log.
- loop_events: every development-loop event (of_loops.KINDS): a loop runs longer than the page's window.

Each row is an event as Studio's rows carry it (event_id, ts, name, session_id, position, org, source, payload), so the
readers use it as they used the log's rows.
"""

import of_fold

LOOP_KINDS = ("of.loop_hypothesis", "of.loop_run", "of.loop_decision", "of.loop_cost")  # of_loops.KINDS


def as_row(e, payload=None):
    """An event of the fold as a Studio row; `payload` replaces the event's own when the view keeps less of it."""
    return {"event_id": e.get("event_id"), "ts": e.get("ts"), "name": e.get("name"), "session_id": e.get("session_id"),
            "position": e.get("position"), "org": e.get("org"), "source": e.get("source"),
            "payload": (e.get("payload") or {}) if payload is None else payload}


class PrStates(of_fold.View):
    """Key: "<org>|<repo>#<number>". Row: the PR's newest of.pr_state, newest by (updated_at, ts) as of_works reads it."""
    name = "pr_states"
    owner = "of_studio"
    version = "1"
    names = ("of.pr_state",)
    lag_ok_s = 1800   # the GitHub import runs every 30 minutes

    def fold(self, rows, events):
        import of_model
        changed = {}
        for e in events:
            pl = e.get("payload") or {}
            k = of_model.pr_key(pl)
            if not k:
                continue
            key = f"{e.get('org') or ''}|{k[0]}#{k[1]}"
            cur = changed.get(key) or rows.get(key)
            when = (str(pl.get("updated_at") or ""), str(e.get("ts") or ""))
            if cur is None or when >= (str((cur.get("payload") or {}).get("updated_at") or ""), str(cur.get("ts") or "")):
                changed[key] = as_row(e)
        return changed


class DiffsApplied(of_fold.View):
    """Key: the event id. Row: the application's time with the diff's id, kind and organization only."""
    name = "diffs_applied"
    owner = "of_studio"
    version = "1"
    names = ("of.diff_applied",)
    lag_ok_s = 600

    def fold(self, rows, events):
        changed = {}
        for e in events:
            pl = e.get("payload") or {}
            changed[e.get("event_id")] = as_row(e, {k: pl.get(k) for k in ("id", "kind", "org") if k in pl})
        return changed


class LoopEvents(of_fold.View):
    """Key: the event id. Row: the loop event as it is (they are small: 288 on 2026-11-14)."""
    name = "loop_events"
    owner = "of_studio"
    version = "1"
    names = LOOP_KINDS
    lag_ok_s = 600

    def fold(self, rows, events):
        return {e.get("event_id"): as_row(e) for e in events}


PR_STATES = of_fold.register(PrStates())
DIFFS_APPLIED = of_fold.register(DiffsApplied())
LOOP_EVENTS = of_fold.register(LoopEvents())


def rows_of(view, keep=None, conn=None):
    """The view's rows in time order, those `keep` accepts; None while the listener has not built it (the caller then
    reads as before)."""
    r = of_fold.read(view, conn=conn)
    if r.age_s is None:
        return None
    out = [x for x in r.rows.values() if keep is None or keep(x)]
    return sorted(out, key=lambda x: (str(x.get("ts") or ""), str(x.get("event_id") or "")))
