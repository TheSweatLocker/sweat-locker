"""Auto-compute rolling tier hit rates from resolved games.

Replaces manual audits ("352-game NRFI tier audit", "PRIME confluence ML
backtest") with automated weekly reports. Stores per-tier hit rates in
mlb_tier_calibration so the app + Jerry's Track Record can show LIVE rates
instead of stale numbers.

Computes:
- NRFI tier rolling hit rates (90-94 PRIME, 80-89, 70-79, 60-69, 50-59, <=40, 95+)
- ML tier rolling hit rates (PRIME confluence ≥+4, STRONG ≥+2, LEAN ≥+1, vs zero/negative)
- Spread tier rolling hit rates (delta ≥ 2, 1.5-2, 1.0-1.5, <1.0)
- Bucketed by 7-day, 30-day, season-to-date windows

Usage:
    python audit_tier_calibration.py
"""

import os
import sys
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def sb_get(table, params, range_header=None):
    qs = urllib.parse.urlencode(params, safe=",.()")
    url = f"{SUPABASE_URL}/rest/v1/{table}?{qs}"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    if range_header:
        headers["Range"] = range_header
        headers["Prefer"] = "count=exact"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"  Supabase {table} error {e.code}: {e.read().decode()[:200]}")
        return []


def sb_upsert(table, rows, on_conflict):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    req = urllib.request.Request(
        url, method="POST", headers=headers,
        data=json.dumps(rows).encode("utf-8"),
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status in (200, 201, 204)
    except urllib.error.HTTPError as e:
        print(f"  Upsert {table} error {e.code}: {e.read().decode()[:300]}")
        return False


def fetch_all_resolved_nba():
    """Pull all resolved NBA games from nba_game_results."""
    all_rows = []
    offset = 0
    while True:
        rows = sb_get("nba_game_results", {
            "home_score": "not.is.null",
            "select": "game_date,home_team,away_team,home_score,away_score,home_win,total_points,net_rating_gap,close_spread,open_spread,close_total,open_total",
            "order": "game_date.asc",
            "limit": "1000",
            "offset": str(offset),
        })
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000
    return all_rows


def classify_nba_nr_tier(nr_gap):
    """Net rating gap cohort (absolute magnitude). Bet direction is the
    team with higher net rating (positive gap = home better)."""
    if nr_gap is None:
        return None
    try:
        g = abs(float(nr_gap))
    except (TypeError, ValueError):
        return None
    if g >= 8:
        return "nba_nr_gap_ge8"
    if g >= 5:
        return "nba_nr_gap_5_8"
    return None


def compute_nba_window_rates(rows, days_back, end_date):
    """Compute hit rate per NBA cohort within rolling window."""
    cutoff = end_date - timedelta(days=days_back)
    in_window = [
        r for r in rows
        if r.get("game_date") and datetime.strptime(r["game_date"], "%Y-%m-%d").date() >= cutoff
    ]
    tier_stats = defaultdict(lambda: {"hits": 0, "total": 0})

    for r in in_window:
        nr = r.get("net_rating_gap")
        sp = r.get("close_spread") if r.get("close_spread") is not None else r.get("open_spread")
        hw = r.get("home_win")
        hs = r.get("home_score")
        as_ = r.get("away_score")

        # NR gap cohort — ML hit (direction = team with higher NR)
        nr_tier = classify_nba_nr_tier(nr)
        if nr_tier and nr is not None and hw is not None:
            try:
                pick_home = float(nr) > 0
                hit_ml = (pick_home and hw) or (not pick_home and not hw)
                ml_key = nr_tier + "_ml"
                tier_stats[ml_key]["total"] += 1
                if hit_ml:
                    tier_stats[ml_key]["hits"] += 1

                # Same cohort, ATS hit — pick the higher-NR team against spread
                if sp is not None and hs is not None and as_ is not None:
                    margin = float(hs) - float(as_)
                    home_covered = margin > -float(sp)
                    if margin == -float(sp):
                        pass  # push, exclude
                    else:
                        hit_ats = home_covered if pick_home else (not home_covered)
                        ats_key = nr_tier + "_ats"
                        tier_stats[ats_key]["total"] += 1
                        if hit_ats:
                            tier_stats[ats_key]["hits"] += 1
            except (TypeError, ValueError):
                pass

        # Home favorite ATS cohort
        if sp is not None and hs is not None and as_ is not None:
            try:
                margin = float(hs) - float(as_)
                line = float(sp)
                if margin == -line:
                    continue  # push
                home_covered = margin > -line
                key = "nba_home_fav_ats" if line < 0 else "nba_home_dog_ats"
                tier_stats[key]["total"] += 1
                if home_covered:
                    tier_stats[key]["hits"] += 1
            except (TypeError, ValueError):
                pass

    return tier_stats


def fetch_all_resolved():
    """Pull all resolved games with NRFI + ML + confluence + spread fields."""
    all_rows = []
    offset = 0
    while True:
        rows = sb_get("mlb_game_results", {
            "nrfi_result": "not.is.null",
            "select": "game_date,nrfi_score,nrfi_result,signal_confluence_net,spread_delta,projected_spread,home_win,close_spread,home_spread_covered,game_id,home_team,away_team,projected_total,close_total,total_runs,total_result",
            "order": "game_date.asc",
            "limit": "1000",
            "offset": str(offset),
        })
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000
    return all_rows


def classify_nrfi_tier(score):
    if score is None:
        return None
    s = int(score)
    if s >= 95:
        return "nrfi_volatile_95plus"
    if s >= 90:
        return "nrfi_prime_90_94"
    if s >= 80:
        return "nrfi_dead_80_89"
    if s >= 70:
        return "nrfi_lean_70_79"
    if s >= 60:
        return "nrfi_60_69"
    if s >= 50:
        return "nrfi_neutral_50_59"
    if s <= 40:
        return "yrfi_lean_le40"
    return None


def classify_confluence_tier(net):
    if net is None:
        return None
    n = int(net)
    # Track the rare extreme-confluence band separately. The +4-5 PRIME
    # cohort hits 27% live (fade tier), but +6+ may behave differently
    # — first-ever +7 (Yankees 5/4) cashed by 10 runs in a 12-1 blowout,
    # n=1. Splitting so the cohort can calibrate independently.
    if n >= 6:
        return "confluence_extreme_ge6"
    if n >= 4:
        return "confluence_prime_ge4"
    if n >= 2:
        return "confluence_strong_2_3"
    if n >= 1:
        return "confluence_lean_1"
    if n <= -1:
        return "confluence_negative"
    return "confluence_zero"


def classify_spread_delta_tier(delta):
    if delta is None:
        return None
    try:
        d = abs(float(delta))
    except (TypeError, ValueError):
        return None
    if d >= 2.0:
        return "spread_delta_ge2"
    if d >= 1.5:
        return "spread_delta_1_5_2"
    if d >= 1.0:
        return "spread_delta_1_1_5"
    return "spread_delta_lt1"


def classify_total_cohort(projected_total, close_total):
    """Total-edge cohort by model-vs-market delta. Returns (cohort_name, bet_dir)
    where bet_dir is 'over' or 'under'. None when within ±1.5 of market.

    Tracks the user's question from 5/3: when v1 stats model projects 3.1
    runs vs market 7.5, does that extreme Under signal actually pay out?
    Earlier sample (Reds/Pirates 3.1 → actual 1, Astros/RedSox 3.8 → actual 4)
    suggests yes but n was tiny."""
    if projected_total is None or close_total is None:
        return None, None
    try:
        pt = float(projected_total)
        ct = float(close_total)
    except (TypeError, ValueError):
        return None, None
    delta = pt - ct
    abs_d = abs(delta)
    if abs_d < 1.5:
        return None, None
    bet_dir = 'over' if delta > 0 else 'under'
    if abs_d >= 3.0:
        return f"total_extreme_{bet_dir}_ge3", bet_dir
    return f"total_edge_{bet_dir}_1_5_to_3", bet_dir


def classify_recency_cohort(breakdown):
    """Read signal_confluence_breakdown for recency votes.

    Returns a (cohort_name, recency_side) tuple where recency_side is
    'home' or 'away' indicating the side recency favors. Returns
    (None, None) when neither recency nor recency_extreme fired.

    Two cohorts: recency_normal (|net| ≥ 0.8) and recency_extreme
    (one team HOT, opposite COLD). Extreme is a stronger signal."""
    if not breakdown or not isinstance(breakdown, dict):
        return None, None
    if breakdown.get("recency_extreme") in ("home", "away"):
        return "recency_extreme", breakdown["recency_extreme"]
    if breakdown.get("recency") in ("home", "away"):
        return "recency_normal", breakdown["recency"]
    return None, None


def classify_autofade_cohort(spread_delta, close_spread, confluence_net):
    """Mirrors auto_fade.cohort_for_pick using only fields available in
    mlb_game_results. Without ml odds we bucket on RL direction agreement
    only — this matches what auto_fade does when ml_market_home is None.

    spread_delta sign convention (per game_context.py): positive = model
    favors home; close_spread negative = home is RL favorite.
    """
    if spread_delta is None or close_spread is None:
        return None
    try:
        sd = float(spread_delta)
        cs = float(close_spread)
    except (TypeError, ValueError):
        return None
    cn = int(confluence_net) if confluence_net is not None else 0
    model_home = sd > 0
    rl_market_home = cs < 0
    agrees = model_home == rl_market_home
    corrected_delta_abs = abs(sd + cs)
    if not agrees:
        if cn >= 2:
            return "autofade_dog_high_conv"
        return "autofade_dog"
    if corrected_delta_abs >= 1.5:
        return "autofade_chalk_high_mag"
    return "autofade_chalk"


def _gkey(date_str, home, away):
    """Composite key (game_date, home_team, away_team) for joining
    mlb_game_results to mlb_game_context. game_id columns use different
    hash inputs across tables so direct ID matching doesn't work."""
    return (date_str, (home or "").strip(), (away or "").strip())


def fetch_breakdowns_for_games(rows):
    """Pull signal_confluence_breakdown from mlb_game_context for each
    resolved game. Returns {(game_date, home_team, away_team): breakdown_dict}.

    Joins on (date, home, away) since game_id schemes differ across tables.
    Page through context rows in big batches by date range to keep URLs
    manageable."""
    if not rows:
        return {}
    dates = sorted({r.get("game_date") for r in rows if r.get("game_date")})
    if not dates:
        return {}
    out = {}
    # Pull all context rows from earliest result date forward
    earliest = dates[0]
    offset = 0
    while True:
        ctx_rows = sb_get("mlb_game_context", {
            "game_date": f"gte.{earliest}",
            "select": "game_date,home_team,away_team,signal_confluence_breakdown",
            "limit": "1000",
            "offset": str(offset),
        })
        if not ctx_rows:
            break
        for c in ctx_rows:
            key = _gkey(c.get("game_date"), c.get("home_team"), c.get("away_team"))
            if c.get("signal_confluence_breakdown") is not None:
                out[key] = c["signal_confluence_breakdown"]
        if len(ctx_rows) < 1000:
            break
        offset += 1000
    return out


def compute_window_rates(rows, days_back, end_date, breakdowns=None):
    """Compute hit rate per tier within rolling window ending end_date.
    breakdowns is {game_id: confluence_breakdown_dict} for recency lookup."""
    breakdowns = breakdowns or {}
    cutoff = end_date - timedelta(days=days_back)
    in_window = [
        r for r in rows
        if r.get("game_date") and datetime.strptime(r["game_date"], "%Y-%m-%d").date() >= cutoff
    ]

    tier_stats = defaultdict(lambda: {"hits": 0, "total": 0})

    for r in in_window:
        # NRFI tier
        nrfi_tier = classify_nrfi_tier(r.get("nrfi_score"))
        if nrfi_tier:
            res = (r.get("nrfi_result") or "").upper()
            if res in ("NRFI", "YRFI"):
                tier_stats[nrfi_tier]["total"] += 1
                # NRFI tiers in 70+ band hit if NRFI; <=40 yrfi lean hits if YRFI
                expected = "YRFI" if nrfi_tier == "yrfi_lean_le40" else "NRFI"
                if res == expected:
                    tier_stats[nrfi_tier]["hits"] += 1

        # Confluence tier — direction must come from projected_spread, NOT
        # spread_delta or signal_confluence_net.
        #   - signal_confluence_net is computed as (support - against) where
        #     "support" counts signals aligning with model_pick. So net is
        #     always non-negative for the model's preferred side, regardless
        #     of whether that side is home or away. Cannot infer direction.
        #   - spread_delta in mlb_game_results stores values that diverge
        #     from mlb_game_context (e.g. Yankees 5/4: context +1.11, results
        #     -2.89). Unreliable as a direction source.
        #   - projected_spread is in run-differential terms (positive = home
        #     wins by X) and is consistent across context + results.
        conf_net = r.get("signal_confluence_net")
        conf_tier = classify_confluence_tier(conf_net)
        ps = r.get("projected_spread")
        hw = r.get("home_win")
        if conf_tier and ps is not None and hw is not None:
            try:
                bet_home = float(ps) > 0
                hit = (bet_home and hw) or (not bet_home and not hw)
                tier_stats[conf_tier]["total"] += 1
                if hit:
                    tier_stats[conf_tier]["hits"] += 1
            except (TypeError, ValueError):
                pass

        # Spread delta tier — magnitude bucket from sd, but direction MUST
        # come from projected_spread (sd unreliable in resolved rows).
        sd = r.get("spread_delta")
        sd_tier = classify_spread_delta_tier(sd)
        if sd_tier and sd is not None and ps is not None and hw is not None:
            try:
                bet_home = float(ps) > 0
                hit = (bet_home and hw) or (not bet_home and not hw)
                tier_stats[sd_tier]["total"] += 1
                if hit:
                    tier_stats[sd_tier]["hits"] += 1
            except (TypeError, ValueError):
                pass

        # Total-edge cohort — when model and market disagree on total by
        # 1.5+ runs, does the model side cash? Two bands per direction.
        total_cohort, bet_dir = classify_total_cohort(r.get("projected_total"), r.get("close_total"))
        if total_cohort and bet_dir:
            tr = r.get("total_runs")
            ct = r.get("close_total")
            if tr is not None and ct is not None:
                try:
                    actual = float(tr)
                    line = float(ct)
                    if actual != line:  # exclude pushes
                        tier_stats[total_cohort]["total"] += 1
                        went_over = actual > line
                        hit = (bet_dir == 'over' and went_over) or (bet_dir == 'under' and not went_over)
                        if hit:
                            tier_stats[total_cohort]["hits"] += 1
                except (TypeError, ValueError):
                    pass

        # Recency cohort — does the recency vote actually predict winners?
        # Read from signal_confluence_breakdown JSON. recency_normal fires
        # when |L10 R/G - season R/G| differential ≥ 0.8 between teams;
        # recency_extreme fires when one team genuinely HOT and other COLD.
        rec_cohort, rec_side = classify_recency_cohort(
            breakdowns.get(_gkey(r.get("game_date"), r.get("home_team"), r.get("away_team")))
        )
        if rec_cohort and rec_side and hw is not None:
            tier_stats[rec_cohort]["total"] += 1
            # recency favored 'home' → hit if home won
            hit = (rec_side == "home" and hw) or (rec_side == "away" and not hw)
            if hit:
                tier_stats[rec_cohort]["hits"] += 1

        # Auto-fade cohort — direction from projected_spread (same fix
        # as confluence + spread_delta cohorts above).
        af_cohort = classify_autofade_cohort(
            sd, r.get("close_spread"), r.get("signal_confluence_net")
        )
        if af_cohort and ps is not None and hw is not None:
            try:
                bet_home = float(ps) > 0
                hit = (bet_home and hw) or (not bet_home and not hw)
                tier_stats[af_cohort]["total"] += 1
                if hit:
                    tier_stats[af_cohort]["hits"] += 1
            except (TypeError, ValueError):
                pass

    return tier_stats


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Missing SUPABASE env vars.")
        sys.exit(1)

    print("Pulling resolved games...")
    rows = fetch_all_resolved()
    print(f"Total resolved games: {len(rows)}")
    if not rows:
        return

    print("Pulling confluence breakdowns for recency audit...")
    breakdowns = fetch_breakdowns_for_games(rows)
    print(f"Breakdowns fetched: {len(breakdowns)}")

    et_today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()

    upsert_rows = []
    print(f"\n{'TIER':35s} {'7d':>14s} {'30d':>14s} {'STD':>14s}")
    print("-" * 80)

    # Compute for each window
    windows = [("7d", 7), ("30d", 30), ("std", 9999)]
    all_tiers = {
        "nrfi_volatile_95plus", "nrfi_prime_90_94", "nrfi_dead_80_89",
        "nrfi_lean_70_79", "nrfi_60_69", "nrfi_neutral_50_59", "yrfi_lean_le40",
        "confluence_extreme_ge6", "confluence_prime_ge4", "confluence_strong_2_3",
        "confluence_lean_1", "confluence_zero", "confluence_negative",
        "spread_delta_ge2", "spread_delta_1_5_2", "spread_delta_1_1_5", "spread_delta_lt1",
        "autofade_chalk_high_mag", "autofade_chalk", "autofade_dog", "autofade_dog_high_conv",
        "recency_normal", "recency_extreme",
        "total_extreme_under_ge3", "total_extreme_over_ge3",
        "total_edge_under_1_5_to_3", "total_edge_over_1_5_to_3",
    }

    window_data = {}
    for label, days in windows:
        window_data[label] = compute_window_rates(rows, days, et_today, breakdowns)

    for tier in sorted(all_tiers):
        cells = []
        for label, _ in windows:
            stats = window_data[label].get(tier, {"hits": 0, "total": 0})
            if stats["total"] == 0:
                cells.append("—".rjust(14))
            else:
                rate = stats["hits"] / stats["total"] * 100
                cells.append(f"{stats['hits']}-{stats['total']-stats['hits']} ({rate:.1f}%)".rjust(14))
        print(f"{tier:35s} {cells[0]} {cells[1]} {cells[2]}")

        # Build upsert rows for storage (one row per tier × window)
        for label, days in windows:
            stats = window_data[label].get(tier, {"hits": 0, "total": 0})
            total = stats["total"]
            if total == 0:
                continue
            upsert_rows.append({
                "tier": tier,
                "window_label": label,
                "computed_date": et_today.isoformat(),
                "hits": stats["hits"],
                "total": total,
                "hit_rate": round(stats["hits"] / total, 4),
                "sport": "mlb",  # multi-sport column added 2026-05-04 — set explicitly
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })

    # ── NBA cohorts (added 2026-05-06) ─────────────────────────────────
    # Audit-driven downgrade trigger: if nba_nr_gap_ge8_ats hit rate stays
    # below 45%, the app's NR-gap LEAN should be suppressed entirely.
    print("\nPulling resolved NBA games...")
    nba_rows = fetch_all_resolved_nba()
    print(f"Total resolved NBA games: {len(nba_rows)}")
    nba_tiers = {
        "nba_nr_gap_ge8_ml", "nba_nr_gap_ge8_ats",
        "nba_nr_gap_5_8_ml", "nba_nr_gap_5_8_ats",
        "nba_home_fav_ats", "nba_home_dog_ats",
    }
    if nba_rows:
        nba_window_data = {label: compute_nba_window_rates(nba_rows, days, et_today) for label, days in windows}
        print(f"\n{'NBA TIER':35s} {'7d':>14s} {'30d':>14s} {'STD':>14s}")
        print("-" * 80)
        for tier in sorted(nba_tiers):
            cells = []
            for label, _ in windows:
                stats = nba_window_data[label].get(tier, {"hits": 0, "total": 0})
                if stats["total"] == 0:
                    cells.append("—".rjust(14))
                else:
                    rate = stats["hits"] / stats["total"] * 100
                    cells.append(f"{stats['hits']}-{stats['total']-stats['hits']} ({rate:.1f}%)".rjust(14))
            print(f"{tier:35s} {cells[0]} {cells[1]} {cells[2]}")
            for label, _ in windows:
                stats = nba_window_data[label].get(tier, {"hits": 0, "total": 0})
                if stats["total"] == 0:
                    continue
                upsert_rows.append({
                    "tier": tier,
                    "window_label": label,
                    "computed_date": et_today.isoformat(),
                    "hits": stats["hits"],
                    "total": stats["total"],
                    "hit_rate": round(stats["hits"] / stats["total"], 4),
                    "sport": "nba",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })

    if upsert_rows:
        print(f"\nUpserting {len(upsert_rows)} tier-window rows to mlb_tier_calibration...")
        ok = sb_upsert("mlb_tier_calibration", upsert_rows, on_conflict="tier,window_label,computed_date")
        print("✅ Upsert complete" if ok else "❌ Upsert failed")
    else:
        print("\nNo tier rows to upsert.")


if __name__ == "__main__":
    main()
