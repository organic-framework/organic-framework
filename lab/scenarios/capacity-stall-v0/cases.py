"""capacity-stall-v0 cases: the balancer's inputs, an observation of which sessions stopped on a usage limit, and the
ground truth of which accounts accept work at NOW. The oracle (run.py) never uses the code under test to decide
what is right."""
import datetime as dt


def ts(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


POLICY = {"relief_at": 95, "week_max": 97, "fill_to": 85, "relief_moves": 6, "max_moves": 2, "min_rate": 5.0,
          "relief_horizon_h": 24, "cooldown_h": 2,
          "hold": ["k8s_keeper", "storefront-hosts_keeper", "forecasting_keeper", "storefront_operator"]}
M20, M5 = "default_claude_max_20x", "default_claude_max_5x"


def acct(d, tier, seen, status="allowed", five=None, week=None):
    w = {}
    if five:
        w["five_hour"] = dict(zip(("utilization", "resets_at", "status"), five))
    if week:
        w["seven_day"] = dict(zip(("utilization", "resets_at", "status"), week))
    return {"config_dir": d, "logged_in": True, "status": status, "tier": tier, "observed_at": seen, "windows": w}


def fleet(prefix, n, busy, total_rate):
    """n sessions on one account, `busy` of them mid-turn, spending total_rate $/h between them."""
    out, rates = [], {}
    for i in range(n):
        sid = f"{prefix}-{i:02d}"
        out.append({"session": sid, "status": "busy" if i < busy else "idle"})
        rates[sid] = total_rate / n
    return out, rates


def real_2026_11_10():
    """20:10:20Z: of_conductor and acme_code-auditor stopped on mail-acme's 5-hour limit (Max 5x, week 55%) at
    20:08Z; founder (59%) and apple (75%) accepted work. Rows are the probe snapshot of 20:10:20Z; the five
    mail-acme sessions, their 30-minute rates and contexts are as the balancer read them; the other two
    accounts' sessions are anonymous, with their real counts, busy counts and total rates."""
    now = ts("2026-11-10T20:12:00Z")
    rows = [acct("/claude", M20, "2026-11-10T20:10:20Z", "rejected", week=(100, "2026-11-11T21:00:00Z", "rejected")),
            acct("/apple", M20, "2026-11-10T20:10:20Z", "allowed_warning", week=(75, "2026-11-15T06:00:00Z", "allowed_warning")),
            acct("/example", M20, "2026-11-10T20:10:20Z", "rejected", week=(100, "2026-11-13T16:00:00Z", "rejected")),
            acct("/ggg", M20, "2026-11-10T20:10:20Z", "allowed", week=(59, "2026-11-15T09:00:00Z", "allowed")),
            acct("/mail", M5, "2026-11-10T20:10:20Z", "rejected", five=(100, "2026-11-10T20:30:00Z", "rejected"),
                 week=(55, "2026-11-15T20:00:00Z", "allowed"))]
    gg, gr = fleet("gg", 27, 5, 115.6)
    ap, ar = fleet("ap", 20, 4, 99.4)
    mail = [{"session": s, "status": "idle"} for s in ("onb", "aud", "arch", "asks", "cond")]
    rates = dict(gr, **ar, onb=1.81, aud=57.39, arch=0.0, asks=0.86, cond=7.85)
    names = {"onb": "of_onboarding-research", "aud": "acme_code-auditor", "arch": "acme_system-architect",
             "asks": "of_asks", "cond": "of_conductor"}
    positions = [{"name": names.get(s, s), "holder": {"session": s}} for s in list(rates)]
    return {"name": "real-2026-11-10", "now": now, "rows": rows, "policy": POLICY,
            "sessions": {"/claude": [], "/apple": ap, "/example": [], "/ggg": gg, "/mail": mail},
            "positions": positions, "rates": rates, "calib": {"/mail": 0.940870032},
            "wcalib": {"/apple": 32.82, "/ggg": 28.83, "/mail": 7.24},
            "contexts": {"onb": 205767, "aud": 0, "arch": 331828, "asks": 115938, "cond": 0},
            "stopped": {"cond": ts("2026-11-10T20:08:15Z"), "aud": ts("2026-11-10T20:08:38Z")},
            "accepting": ["/apple", "/ggg"]}


def base(name, now, src_row, sessions, stopped, accepting, targets=None, rates=None, wcalib=None, calib=None):
    targets = targets if targets is not None else [
        acct("/b", M20, iso(now - 300), week=(80, iso(now + 113 * 3600))),
        acct("/c", M20, iso(now - 300), week=(82, iso(now + 90 * 3600)))]
    ss = {r["config_dir"]: [] for r in targets}
    ss.update(sessions)
    sids = [s["session"] for xs in ss.values() for s in xs]
    return {"name": name, "now": now, "rows": [src_row] + targets, "policy": POLICY, "sessions": ss,
            "positions": [{"name": s, "holder": {"session": s}} for s in sids],
            "rates": rates or {}, "calib": calib if calib is not None else {"/a": 0.94},
            "wcalib": wcalib if wcalib is not None else {"/a": 7.2, "/b": 30.0, "/c": 30.0}, "contexts": {},
            "stopped": stopped, "accepting": accepting}


NOW = ts("2026-11-12T12:00:00Z")


def probe_lag():
    """The probe 24 minutes ago said the source was at 92% of its 5-hour window, allowed; its session stopped
    5 minutes ago. Predictive relief sees no stall; the stop is a fact."""
    src = acct("/a", M5, iso(NOW - 24 * 60), five=(92, iso(NOW + 3 * 3600)), week=(40, iso(NOW + 100 * 3600)))
    return base("probe-lag", NOW, src, {"/a": [{"session": "s1", "status": "idle"}],
                                        "/b": [{"session": "w", "status": "busy"}]},
                {"s1": NOW - 300}, ["/b", "/c"], rates={"s1": 6, "w": 40})


def long_reopen_short_room():
    """The source's 5-hour window reopens in 4.5 hours; both targets accept work (80%, 82%) but at $150/h each
    their predicted week room over 4.5 hours is negative. A stopped session still gains hours of work there."""
    src = acct("/a", M5, iso(NOW - 120), "rejected", five=(100, iso(NOW + 4.5 * 3600), "rejected"),
               week=(55, iso(NOW + 124 * 3600)))
    return base("long-reopen-short-room", NOW, src,
                {"/a": [{"session": "s1", "status": "idle"}], "/b": [{"session": "w", "status": "busy"}],
                 "/c": [{"session": "v", "status": "busy"}]},
                {"s1": NOW - 600}, ["/b", "/c"], rates={"s1": 8, "w": 150, "v": 150})


def uncalibrated_target():
    """The only account that accepts work has no calibration and no known plan (a new login): unknown room is
    zero to the predictive balancer."""
    src = acct("/a", M20, iso(NOW - 120), "rejected", week=(100, iso(NOW + 30 * 3600), "rejected"))
    tgt = [dict(acct("/n", None, iso(NOW - 120), week=(5, iso(NOW + 160 * 3600))))]
    return base("uncalibrated-target", NOW, src, {"/a": [{"session": "s1", "status": "idle"}]},
                {"s1": NOW - 900}, ["/n"], targets=tgt, rates={"s1": 10}, wcalib={}, calib={})


def hold_production_stalled():
    """A production (`hold`) position stopped 5 minutes ago, behind a probe 24 minutes old that still says the source
    is allowed at 92% of its 5-hour window: the predictive balancer sees no stall and leaves it, only the rescue
    (trusting the stop) moves it. The invariant has no exception for hold — the rescue even moves hold first — so it
    must be moved. Guards both the blind spot and that production is never spared from the rescue and stranded on a
    dead account (the holders that most must not be stranded)."""
    src = acct("/a", M5, iso(NOW - 24 * 60), five=(92, iso(NOW + 3 * 3600)), week=(40, iso(NOW + 100 * 3600)))
    return base("hold-production-stalled", NOW, src, {"/a": [{"session": "k8s_keeper", "status": "idle"}]},
                {"k8s_keeper": NOW - 300}, ["/b", "/c"], rates={"k8s_keeper": 12})


def cheap_stalled():
    """A stopped session spends below `min_rate` (a balancing threshold, not a rescue one): it must still be moved.
    Guards against a spend gate leaking into the rescue path and leaving a cheap holder stalled on a dead account."""
    src = acct("/a", M20, iso(NOW - 120), "rejected", week=(100, iso(NOW + 30 * 3600), "rejected"))
    return base("cheap-stalled", NOW, src, {"/a": [{"session": "s1", "status": "idle"}]},
                {"s1": NOW - 900}, ["/b", "/c"], rates={"s1": 1.0})


def nothing_accepts():
    """Negative: every account is stopped. No move can help; none may go to a stopped account."""
    src = acct("/a", M20, iso(NOW - 120), "rejected", week=(100, iso(NOW + 30 * 3600), "rejected"))
    tgt = [acct("/b", M20, iso(NOW - 120), "rejected", five=(100, iso(NOW + 2 * 3600), "rejected"),
                week=(70, iso(NOW + 90 * 3600)))]
    return base("nothing-accepts", NOW, src, {"/a": [{"session": "s1", "status": "idle"}],
                                              "/b": [{"session": "s2", "status": "idle"}]},
                {"s1": NOW - 900, "s2": NOW - 600}, [], targets=tgt, rates={"s1": 10, "s2": 10})


def reset_and_busy():
    """Negative: s1 stopped before its account's window reset (a probe after the stop says allowed: it can go on
    where it is); s2 is mid-turn (never moved). Nothing must move."""
    src = acct("/a", M20, iso(NOW - 60), week=(40, iso(NOW + 100 * 3600)))
    tgt = [acct("/b", M20, iso(NOW - 60), "rejected", five=(100, iso(NOW + 2 * 3600), "rejected"),
                week=(70, iso(NOW + 90 * 3600)))]
    return base("reset-and-busy", NOW, src, {"/a": [{"session": "s1", "status": "idle"}],
                                             "/b": [{"session": "s2", "status": "busy"}]},
                {"s1": NOW - 3600, "s2": NOW - 300}, ["/a"], targets=tgt, rates={"s1": 10, "s2": 10})


CASES = [real_2026_11_10, probe_lag, long_reopen_short_room, uncalibrated_target, hold_production_stalled,
         cheap_stalled, nothing_accepts, reset_and_busy]
