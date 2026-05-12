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


def fetch_all_resolved_ks_under_props():
    """Pull resolved K-Under props from mlb_pipeline_props for cohort audit.

    K-Under fired three losses 5/3-5/6 (McCullers, Woo, Singer) and Andy flagged
    the cohort as suspect 5/6 night. This audit grades it. PRIME tier hit rate
    is the load-bearing metric — if it falls below 60%, may need to add a
    season K% gate or tighter L3 fade requirement."""
    rows = []
    offset = 0
    while True:
        page = sb_get("mlb_pipeline_props", {
            "prop_type": "eq.ks_under",
            "result": "in.(Win,Loss)",
            "select": "tier,result,conviction,game_date",
            "order": "game_date.asc",
            "limit": "1000",
            "offset": str(offset),
        })
        if not page:
            break
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    return rows


def compute_k_under_window_rates(rows, days_back, end_date):
    """Compute K-Under hit rates per tier within rolling window."""
    cutoff = end_date - timedelta(days=days_back)
    in_window = [
        r for r in rows
        if r.get("game_date") and datetime.strptime(r["game_date"], "%Y-%m-%d").date() >= cutoff
    ]
    tier_stats = defaultdict(lambda: {"hits": 0, "total": 0})
    for r in in_window:
        tier = (r.get("tier") or "").upper()
        if tier not in ("PRIME", "STRONG", "LEAN"):
            continue
        key = f"k_under_{tier.lower()}"
        tier_stats[key]["total"] += 1
        if r.get("result") == "Win":
            tier_stats[key]["hits"] += 1
    return tier_stats


def fetch_all_resolved_ks_over_props():
    """Pull resolved K-Over props for alt-line + correlation cohorts.

    Returns rows with projected_ks (model expectation), prop_line (audit
    threshold), final_value (actual Ks recorded), game_id, player_team,
    plus signals dict for fallback projection extraction.
    """
    rows = []
    offset = 0
    while True:
        page = sb_get("mlb_pipeline_props", {
            "prop_type": "eq.ks_over",
            "result": "in.(Win,Loss)",
            "select": "tier,result,prop_line,final_value,signals,game_id,game_date,player_team",
            "order": "game_date.asc",
            "limit": "1000",
            "offset": str(offset),
        })
        if not page:
            break
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    return rows


def compute_k_over_alt_window_rates(rows, days_back, end_date):
    """Bucket K-Over picks by model's projected_ks magnitude, compute hit
    rate at common book-line thresholds (5.5 / 6.5 / 7.5 / 8.5).

    Output a single combined cohort dict keyed `k_over_proj_<bucket>_clears_<line>`
    (one cell per magnitude × threshold). User reads as "if model projects
    8.3 Ks (8_to_9 bucket), how often does the actual count clear 7.5?".

    Picks with no projected_ks (older rows pre-display-swap) are skipped.
    """
    cutoff = end_date - timedelta(days=days_back)
    in_window = [
        r for r in rows
        if r.get("game_date") and datetime.strptime(r["game_date"], "%Y-%m-%d").date() >= cutoff
    ]
    tier_stats = defaultdict(lambda: {"hits": 0, "total": 0})

    def proj_bucket(p):
        try:
            p = float(p)
        except (TypeError, ValueError):
            return None
        if p < 6: return None  # below threshold floor — too noisy
        if p < 7: return "6_to_7"
        if p < 8: return "7_to_8"
        if p < 9: return "8_to_9"
        return "9_plus"

    THRESHOLDS = [5.5, 6.5, 7.5, 8.5]

    for r in in_window:
        sig = r.get("signals") or {}
        proj = sig.get("_projected_ks") if isinstance(sig, dict) else None
        bucket = proj_bucket(proj)
        if bucket is None:
            continue
        actual = r.get("final_value")
        try:
            actual = float(actual)
        except (TypeError, ValueError):
            continue
        for thr in THRESHOLDS:
            key = f"k_over_proj_{bucket}_clears_{str(thr).replace('.', '_')}"
            tier_stats[key]["total"] += 1
            if actual > thr:
                tier_stats[key]["hits"] += 1
    return tier_stats


def compute_k_over_correlation_rates(rows, days_back, end_date, game_results_map, game_context_map):
    """Cross-correlation cohorts: K-Over hit rate conditioned on game total
    outcome and starter's team bullpen state at pick time.

    - k_over_with_total_under / over / push : did the game total go U/O/P?
    - k_over_starter_pen_rested / normal / gassed : pitcher's pen workload L3d

    Hypothesis: K-Over correlates positively with UNDER (starter went deep)
    and with gassed pen (manager extends starter). Validates or refutes."""
    cutoff = end_date - timedelta(days=days_back)
    in_window = [
        r for r in rows
        if r.get("game_date") and datetime.strptime(r["game_date"], "%Y-%m-%d").date() >= cutoff
    ]
    tier_stats = defaultdict(lambda: {"hits": 0, "total": 0})

    for r in in_window:
        gid = r.get("game_id")
        if not gid:
            continue
        result = r.get("result")
        is_win = result == "Win"

        # Total correlation
        gr = game_results_map.get(gid)
        if gr:
            total_result = gr.get("total_result")  # 'Over' | 'Under' | 'Push'
            if total_result in ("Over", "Under", "Push"):
                key = f"k_over_with_total_{total_result.lower()}"
                tier_stats[key]["total"] += 1
                if is_win:
                    tier_stats[key]["hits"] += 1

        # Starter pen workload correlation. Prefer snapshot stored on the
        # prop signals (added 2026-05-10) since mlb_game_context is transient.
        # Fall back to gc_map join for any rows that happen to still have
        # current context.
        sig = r.get("signals") or {}
        pen_count = sig.get("_starter_pen_relievers_3d") if isinstance(sig, dict) else None
        if pen_count is None:
            gc = game_context_map.get(gid)
            if gc:
                team = r.get("player_team")
                home_team = gc.get("home_team")
                away_team = gc.get("away_team")
                if team == home_team:
                    pen_count = gc.get("home_bp_relievers_3d")
                elif team == away_team:
                    pen_count = gc.get("away_bp_relievers_3d")
        if pen_count is not None:
            try:
                pc = int(pen_count)
            except (TypeError, ValueError):
                pc = None
            if pc is not None:
                if pc <= 6:
                    key = "k_over_starter_pen_rested"
                elif pc >= 12:
                    key = "k_over_starter_pen_gassed"
                else:
                    key = "k_over_starter_pen_normal"
                tier_stats[key]["total"] += 1
                if is_win:
                    tier_stats[key]["hits"] += 1
    return tier_stats


def fetch_resolved_game_results_map():
    """Map game_id → {total_result, home_score, away_score}."""
    rows = []
    offset = 0
    while True:
        page = sb_get("mlb_game_results", {
            "total_result": "not.is.null",
            "select": "game_id,total_result,total_runs,close_total",
            "limit": "1000",
            "offset": str(offset),
        })
        if not page:
            break
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    return {r["game_id"]: r for r in rows if r.get("game_id")}


def fetch_game_context_pen_map():
    """Map game_id → {home_team, away_team, home_bp_relievers_3d, away_bp_relievers_3d}."""
    rows = []
    offset = 0
    while True:
        page = sb_get("mlb_game_context", {
            "select": "game_id,home_team,away_team,home_bp_relievers_3d,away_bp_relievers_3d",
            "limit": "1000",
            "offset": str(offset),
        })
        if not page:
            break
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    return {r["game_id"]: r for r in rows if r.get("game_id")}


def fetch_resolved_with_total_features():
    """Pull resolved games with feature fields used for total-result cohorts.

    Single-factor conditional probability cohorts: given condition X (combined
    wRC+ band, avg starter ERA band, avg bullpen ERA band, combined recency
    drift), what % of games went OVER the close_total?

    Also includes ML/spread outcome fields (home_win, home_spread_covered,
    close_spread) so the spread/ML factor cohort can reuse the same fetch.
    """
    rows = []
    offset = 0
    while True:
        page = sb_get("mlb_game_results", {
            "total_result": "in.(Over,Under,Push)",
            "select": "game_date,total_result,close_total,total_runs,home_win,home_spread_covered,spread_result,close_spread,home_wrc_plus,away_wrc_plus,home_sp_xera,away_sp_xera,home_bullpen_era,away_bullpen_era,home_last5_run_diff,away_last5_run_diff,home_runs_per_game,away_runs_per_game",
            "order": "game_date.asc",
            "limit": "1000",
            "offset": str(offset),
        })
        if not page:
            break
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    return rows


def compute_total_factor_window_rates(rows, days_back, end_date):
    """Single-factor cohorts: given condition X, what % went OVER?

    Cohorts:
      Combined offense (sum of both teams' wRC+):
        total_over_wrc_combined_high  ≥ 215
        total_over_wrc_combined_mid   185-214
        total_over_wrc_combined_low   ≤ 184

      Starter quality (avg of both starters' xERA — pipeline uses xERA not ERA):
        total_over_starter_xera_low    avg ≤ 3.5 (both aces)
        total_over_starter_xera_mid    3.5-4.5
        total_over_starter_xera_high   > 4.5 (both soft)

      Bullpen quality (avg of both pens' ERA):
        total_over_bp_era_low         avg ≤ 3.5
        total_over_bp_era_mid         3.5-4.5
        total_over_bp_era_high        > 4.5

      Recency (combined last5 run diff):
        total_over_l5_combined_hot    sum ≥ +1.0
        total_over_l5_combined_cold   sum ≤ -1.0
    """
    cutoff = end_date - timedelta(days=days_back)
    in_window = [
        r for r in rows
        if r.get("game_date") and datetime.strptime(r["game_date"], "%Y-%m-%d").date() >= cutoff
    ]
    tier_stats = defaultdict(lambda: {"hits": 0, "total": 0})

    def fnum(v):
        try: return float(v)
        except (TypeError, ValueError): return None

    for r in in_window:
        result = r.get("total_result")
        if result == "Push":
            continue  # exclude pushes from over rate denominator
        is_over = result == "Over"

        h_wrc = fnum(r.get("home_wrc_plus"))
        a_wrc = fnum(r.get("away_wrc_plus"))
        if h_wrc is not None and a_wrc is not None:
            combined_wrc = h_wrc + a_wrc
            if combined_wrc >= 215:
                key = "total_over_wrc_combined_high"
            elif combined_wrc >= 185:
                key = "total_over_wrc_combined_mid"
            else:
                key = "total_over_wrc_combined_low"
            tier_stats[key]["total"] += 1
            if is_over:
                tier_stats[key]["hits"] += 1

        h_era = fnum(r.get("home_sp_xera"))
        a_era = fnum(r.get("away_sp_xera"))
        if h_era is not None and a_era is not None:
            avg_era = (h_era + a_era) / 2
            if avg_era <= 3.5:
                key = "total_over_starter_xera_low"
            elif avg_era <= 4.5:
                key = "total_over_starter_xera_mid"
            else:
                key = "total_over_starter_xera_high"
            tier_stats[key]["total"] += 1
            if is_over:
                tier_stats[key]["hits"] += 1

        h_bp = fnum(r.get("home_bullpen_era"))
        a_bp = fnum(r.get("away_bullpen_era"))
        if h_bp is not None and a_bp is not None:
            avg_bp = (h_bp + a_bp) / 2
            if avg_bp <= 3.5:
                key = "total_over_bp_era_low"
            elif avg_bp <= 4.5:
                key = "total_over_bp_era_mid"
            else:
                key = "total_over_bp_era_high"
            tier_stats[key]["total"] += 1
            if is_over:
                tier_stats[key]["hits"] += 1

        h_drift = fnum(r.get("home_last5_run_diff"))
        a_drift = fnum(r.get("away_last5_run_diff"))
        if h_drift is not None and a_drift is not None:
            combined = h_drift + a_drift
            if combined >= 1.0:
                key = "total_over_l5_combined_hot"
                tier_stats[key]["total"] += 1
                if is_over:
                    tier_stats[key]["hits"] += 1
            elif combined <= -1.0:
                key = "total_over_l5_combined_cold"
                tier_stats[key]["total"] += 1
                if is_over:
                    tier_stats[key]["hits"] += 1

        # xERA gap rule audit — game_context.py fires an OVER lean when the
        # starters' xERA gap ≥ 2.0, citing a hardcoded "59.3%". This cohort
        # checks whether that number still holds. Buckets: 2.0-3.0 and ≥3.0.
        h_xera = fnum(r.get("home_sp_xera"))
        a_xera = fnum(r.get("away_sp_xera"))
        if h_xera is not None and a_xera is not None:
            gap = abs(h_xera - a_xera)
            if gap >= 3.0:
                key = "xera_gap_ge3_over"
                tier_stats[key]["total"] += 1
                if is_over:
                    tier_stats[key]["hits"] += 1
            elif gap >= 2.0:
                key = "xera_gap_2_3_over"
                tier_stats[key]["total"] += 1
                if is_over:
                    tier_stats[key]["hits"] += 1
            # Also track the combined ≥2.0 bucket for direct comparison to the 59.3% claim
            if gap >= 2.0:
                key = "xera_gap_ge2_over"
                tier_stats[key]["total"] += 1
                if is_over:
                    tier_stats[key]["hits"] += 1

    return tier_stats


def fetch_umpire_stats_map():
    """Pull umpire stats from mlb_umpires. Returns dict keyed by lowercased
    ump name → {k_rate_above_avg, over_rate, nrfi_rate, games_sampled}."""
    rows = sb_get("mlb_umpires", {"select": "ump_name,k_rate_above_avg,over_rate,nrfi_rate,games_sampled,run_factor"})
    if not rows:
        return {}
    return {r["ump_name"].lower(): r for r in rows if r.get("ump_name")}


def fetch_resolved_with_umpire():
    """Pull resolved games with umpire name, total result, NRFI result."""
    rows = []
    offset = 0
    while True:
        page = sb_get("mlb_game_results", {
            "umpire": "not.is.null",
            "select": "game_id,game_date,umpire,total_result,total_runs,close_total,nrfi_result,home_win",
            "order": "game_date.asc",
            "limit": "1000",
            "offset": str(offset),
        })
        if not page:
            break
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    return rows


def compute_umpire_window_rates(rows, ko_props, ump_map, days_back, end_date):
    """Three cohort families crossed against umpire characteristics:
      1. K-Over × ump K rate (k_friendly / neutral / k_hostile)
      2. Total Over × ump over_rate (over_friendly / neutral / over_hostile)
      3. NRFI × ump nrfi_rate (nrfi_friendly / neutral / nrfi_hostile)
    """
    cutoff = end_date - timedelta(days=days_back)
    tier_stats = defaultdict(lambda: {"hits": 0, "total": 0})

    # Filter game results by window and index by game_id
    games_in_window = [
        g for g in rows
        if g.get("game_date") and datetime.strptime(g["game_date"], "%Y-%m-%d").date() >= cutoff
    ]
    games_by_id = {g["game_id"]: g for g in games_in_window if g.get("game_id")}

    # 1. K-Over cohorts (join props → game → umpire)
    ko_in_window = [
        p for p in ko_props
        if p.get("game_date") and datetime.strptime(p["game_date"], "%Y-%m-%d").date() >= cutoff
    ]
    for prop in ko_in_window:
        gid = prop.get("game_id")
        game = games_by_id.get(gid)
        if not game:
            continue
        ump_name = (game.get("umpire") or "").strip().lower()
        ump = ump_map.get(ump_name)
        if not ump:
            continue
        k_rate = ump.get("k_rate_above_avg")
        if k_rate is None:
            continue
        try:
            kr = float(k_rate)
        except (TypeError, ValueError):
            continue
        if kr >= 0.2:
            key = "k_over_with_ump_k_friendly"
        elif kr <= -0.2:
            key = "k_over_with_ump_k_hostile"
        else:
            key = "k_over_with_ump_neutral"
        tier_stats[key]["total"] += 1
        if prop.get("result") == "Win":
            tier_stats[key]["hits"] += 1

    # 2. Total Over cohorts (game → umpire)
    # 3. NRFI cohorts (same join)
    for g in games_in_window:
        ump_name = (g.get("umpire") or "").strip().lower()
        ump = ump_map.get(ump_name)
        if not ump:
            continue

        # Total Over bucket
        over_rate = ump.get("over_rate")
        if over_rate is not None and g.get("total_result") in ("Over", "Under"):
            try:
                orate = float(over_rate)
                if orate >= 0.55:
                    key = "total_over_with_ump_over_friendly"
                elif orate <= 0.45:
                    key = "total_over_with_ump_over_hostile"
                else:
                    key = "total_over_with_ump_neutral"
                tier_stats[key]["total"] += 1
                if g["total_result"] == "Over":
                    tier_stats[key]["hits"] += 1
            except (TypeError, ValueError):
                pass

        # NRFI bucket (uses NRFI rate)
        nrfi_rate = ump.get("nrfi_rate")
        nrfi_result = g.get("nrfi_result")
        if nrfi_rate is not None and nrfi_result in ("NRFI", "YRFI"):
            try:
                nrate = float(nrfi_rate)
                if nrate >= 0.55:
                    key = "nrfi_with_ump_nrfi_friendly"
                elif nrate <= 0.45:
                    key = "nrfi_with_ump_nrfi_hostile"
                else:
                    key = "nrfi_with_ump_neutral"
                tier_stats[key]["total"] += 1
                if nrfi_result == "NRFI":
                    tier_stats[key]["hits"] += 1
            except (TypeError, ValueError):
                pass

    return tier_stats


def fetch_resolved_with_rest_features():
    """Pull resolved games with pitcher days_rest + outcome fields.
    Tests hypothesis that rest deviation from norm (4-6 days) shifts outcomes."""
    rows = []
    offset = 0
    while True:
        page = sb_get("mlb_game_results", {
            "total_result": "in.(Over,Under,Push)",
            "select": "game_date,total_result,home_win,home_spread_covered,spread_result,home_sp_days_rest,away_sp_days_rest,home_sp_xera,away_sp_xera",
            "order": "game_date.asc",
            "limit": "1000",
            "offset": str(offset),
        })
        if not page:
            break
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    return rows


def compute_pitcher_rest_window_rates(rows, days_back, end_date):
    """Pitcher days_rest cohorts. Buckets by deviation from norm (4-6 days):
        rest_short  : ≤4 days (short rest)
        rest_normal : 5-6 days (standard)
        rest_long   : 7+ days (extra rest — rusty signal)

    For each bucket, track BOTH the pitcher's team total + ML/ATS outcomes
    so we can see if rusty/short-rest pitchers correlate with OVER + their
    team losing.
    """
    cutoff = end_date - timedelta(days=days_back)
    in_window = [
        r for r in rows
        if r.get("game_date") and datetime.strptime(r["game_date"], "%Y-%m-%d").date() >= cutoff
    ]
    tier_stats = defaultdict(lambda: {"hits": 0, "total": 0})

    def fnum(v):
        try: return float(v)
        except (TypeError, ValueError): return None

    def bucket(rest):
        if rest is None: return None
        if rest <= 4: return "short"
        if rest <= 6: return "normal"
        return "long"

    for r in in_window:
        result = r.get("total_result")
        is_over = result == "Over"
        home_win = r.get("home_win")
        spread_result = r.get("spread_result")
        has_cover = spread_result in ("home_covered", "away_covered")
        cover_home = spread_result == "home_covered"

        # Home pitcher rest cohort
        h_rest = bucket(fnum(r.get("home_sp_days_rest")))
        if h_rest and result != "Push":
            key = f"home_sp_rest_{h_rest}_over"
            tier_stats[key]["total"] += 1
            if is_over:
                tier_stats[key]["hits"] += 1
        if h_rest and home_win is not None:
            key = f"home_sp_rest_{h_rest}_team_ml"
            tier_stats[key]["total"] += 1
            if home_win:
                tier_stats[key]["hits"] += 1
        if h_rest and has_cover:
            key = f"home_sp_rest_{h_rest}_team_ats"
            tier_stats[key]["total"] += 1
            if cover_home:
                tier_stats[key]["hits"] += 1

        # Away pitcher rest cohort
        a_rest = bucket(fnum(r.get("away_sp_days_rest")))
        if a_rest and result != "Push":
            key = f"away_sp_rest_{a_rest}_over"
            tier_stats[key]["total"] += 1
            if is_over:
                tier_stats[key]["hits"] += 1
        if a_rest and home_win is not None:
            key = f"away_sp_rest_{a_rest}_team_ml"
            tier_stats[key]["total"] += 1
            # away team wins = home loses
            if not home_win:
                tier_stats[key]["hits"] += 1

    return tier_stats


def compute_spread_ml_factor_window_rates(rows, days_back, end_date):
    """Single-factor cohorts for ML (home_win) and spread cover rates,
    bucketed by directional disparity (home advantage / even / away advantage).

    Tells user: 'When home team has X advantage over away, home wins/covers Y%
    of the time historically.' Combines with market prices to find edge.

    Cohorts (pairs of ML + ATS for each factor):
      wRC+ disparity         (home_wrc - away_wrc)
      Starter xERA disparity (away_xera - home_xera; positive = home pitcher better)
      Bullpen ERA disparity  (away_bp - home_bp; positive = home pen better)
      L5 drift disparity     (home_drift - away_drift)
    """
    cutoff = end_date - timedelta(days=days_back)
    in_window = [
        r for r in rows
        if r.get("game_date") and datetime.strptime(r["game_date"], "%Y-%m-%d").date() >= cutoff
    ]
    tier_stats = defaultdict(lambda: {"hits": 0, "total": 0})

    def fnum(v):
        try: return float(v)
        except (TypeError, ValueError): return None

    def bucket_label(diff, hi_thresh, lo_thresh):
        """Returns 'home_adv' / 'away_adv' / None (neutral). Asymmetric thresholds
        let us focus on actionable disparities, skipping the noisy middle."""
        if diff >= hi_thresh: return "home_adv"
        if diff <= lo_thresh: return "away_adv"
        return None

    def record(prefix, bucket, ml_win, cover_win, has_cover):
        """Emit ML cohort (always) + ATS cohort (when spread data present)."""
        ml_key = f"{prefix}_{bucket}_ml"
        tier_stats[ml_key]["total"] += 1
        if ml_win:
            tier_stats[ml_key]["hits"] += 1
        if has_cover:
            ats_key = f"{prefix}_{bucket}_ats"
            tier_stats[ats_key]["total"] += 1
            if cover_win:
                tier_stats[ats_key]["hits"] += 1

    for r in in_window:
        home_win = r.get("home_win")
        if home_win is None:
            continue
        spread_result = r.get("spread_result")
        # Cover only counts if not push
        has_cover = spread_result in ("home_covered", "away_covered")
        cover_home = spread_result == "home_covered"

        h_wrc = fnum(r.get("home_wrc_plus"))
        a_wrc = fnum(r.get("away_wrc_plus"))
        if h_wrc is not None and a_wrc is not None:
            diff = h_wrc - a_wrc
            b = bucket_label(diff, 15, -15)
            if b:
                record("wrc_diff", b, bool(home_win), cover_home, has_cover)

        h_xera = fnum(r.get("home_sp_xera"))
        a_xera = fnum(r.get("away_sp_xera"))
        if h_xera is not None and a_xera is not None:
            # away_xera - home_xera: positive = home pitcher better
            diff = a_xera - h_xera
            b = bucket_label(diff, 0.75, -0.75)
            if b:
                record("starter_diff", b, bool(home_win), cover_home, has_cover)

        h_bp = fnum(r.get("home_bullpen_era"))
        a_bp = fnum(r.get("away_bullpen_era"))
        if h_bp is not None and a_bp is not None:
            # away_bp - home_bp: positive = home pen better
            diff = a_bp - h_bp
            b = bucket_label(diff, 0.5, -0.5)
            if b:
                record("bp_diff", b, bool(home_win), cover_home, has_cover)

        h_drift = fnum(r.get("home_last5_run_diff"))
        a_drift = fnum(r.get("away_last5_run_diff"))
        if h_drift is not None and a_drift is not None:
            diff = h_drift - a_drift
            b = bucket_label(diff, 1.5, -1.5)
            if b:
                record("l5_diff", b, bool(home_win), cover_home, has_cover)

    return tier_stats


def fetch_all_resolved_nfl():
    """Pull all resolved NFL games from nfl_game_results.

    nflverse spread_line convention: POSITIVE = home favored (opposite of
    standard sportsbook display). spread_result classification matches:
    home_covered when margin > close_spread.
    """
    all_rows = []
    offset = 0
    while True:
        rows = sb_get("nfl_game_results", {
            "home_score": "not.is.null",
            "select": "season,game_date,home_team,away_team,home_score,away_score,home_win,total_points,close_spread,close_total,spread_result,total_result,div_game,roof,home_rest,away_rest",
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


def compute_nfl_window_rates(rows, days_back, end_date):
    """Compute hit rate per NFL cohort within rolling window.

    Cohorts (audit-verified baselines on 1139 games 2022-2025):
      - nfl_home_fav_cover    — close_spread > 0 → home covers? ~51%
      - nfl_home_dog_cover    — close_spread < 0 → home covers? ~50%
      - nfl_heavy_home_fav    — close_spread >= 7 → home covers? ~47% (chalk fades slightly)
      - nfl_heavy_home_dog    — close_spread <= -7 → home covers? ~50%
      - nfl_div_home_cover    — div_game=true → home covers? ~59% (interesting baseline)
      - nfl_dome_over         — roof in dome/closed → total OVER? ~50%
      - nfl_outdoor_over      — roof=outdoors → total OVER? ~47% (weather suppresses)
      - nfl_rest_advantage    — |home_rest - away_rest| >= 5 → rested side covers? ~38% (counterintuitive)

    These are descriptive baselines, not betting tiers yet. Phase 2 builds
    actual conviction tiers on top of these audit-validated signals.
    """
    cutoff = end_date - timedelta(days=days_back)
    in_window = [
        r for r in rows
        if r.get("game_date") and datetime.strptime(r["game_date"], "%Y-%m-%d").date() >= cutoff
    ]
    tier_stats = defaultdict(lambda: {"hits": 0, "total": 0})

    for r in in_window:
        cs = r.get("close_spread")
        sr = r.get("spread_result")
        ct = r.get("close_total")
        tr = r.get("total_result")
        roof = (r.get("roof") or "").lower()
        div = r.get("div_game")
        h_rest = r.get("home_rest")
        a_rest = r.get("away_rest")

        # Spread cohorts (exclude pushes — only count home_covered / away_covered)
        if cs is not None and sr in ("home_covered", "away_covered"):
            try:
                cs_f = float(cs)
                hit_home = sr == "home_covered"
                # Home favored cohorts (positive close_spread = home favored)
                if cs_f > 0:
                    tier_stats["nfl_home_fav_cover"]["total"] += 1
                    if hit_home:
                        tier_stats["nfl_home_fav_cover"]["hits"] += 1
                    if cs_f >= 7:
                        tier_stats["nfl_heavy_home_fav"]["total"] += 1
                        if hit_home:
                            tier_stats["nfl_heavy_home_fav"]["hits"] += 1
                # Home dog cohorts (negative close_spread = home underdog)
                elif cs_f < 0:
                    tier_stats["nfl_home_dog_cover"]["total"] += 1
                    if hit_home:
                        tier_stats["nfl_home_dog_cover"]["hits"] += 1
                    if cs_f <= -7:
                        tier_stats["nfl_heavy_home_dog"]["total"] += 1
                        if hit_home:
                            tier_stats["nfl_heavy_home_dog"]["hits"] += 1
                # Division-game home cover (notable signal in baseline)
                if div is True:
                    tier_stats["nfl_div_home_cover"]["total"] += 1
                    if hit_home:
                        tier_stats["nfl_div_home_cover"]["hits"] += 1
                # Rest advantage — direction = side with more rest
                if h_rest is not None and a_rest is not None:
                    try:
                        gap = int(h_rest) - int(a_rest)
                        if abs(gap) >= 5:
                            rested_home = gap > 0
                            tier_stats["nfl_rest_advantage_cover"]["total"] += 1
                            # Hit if the rested side covered
                            rested_covered = (rested_home and hit_home) or (not rested_home and not hit_home)
                            if rested_covered:
                                tier_stats["nfl_rest_advantage_cover"]["hits"] += 1
                    except (TypeError, ValueError):
                        pass
            except (TypeError, ValueError):
                pass

        # Total cohorts (exclude pushes)
        if ct is not None and tr in ("over", "under"):
            went_over = tr == "over"
            if "dome" in roof or "closed" in roof:
                tier_stats["nfl_dome_over"]["total"] += 1
                if went_over:
                    tier_stats["nfl_dome_over"]["hits"] += 1
            elif "outdoor" in roof:
                tier_stats["nfl_outdoor_over"]["total"] += 1
                if went_over:
                    tier_stats["nfl_outdoor_over"]["hits"] += 1

    return tier_stats


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


def fetch_all_resolved_nba_picks():
    """Pull resolved NBA conviction-tier picks from nba_game_picks."""
    all_rows = []
    offset = 0
    while True:
        rows = sb_get("nba_game_picks", {
            "result": "in.(Win,Loss,Push)",
            "select": "game_date,pick_type,pick_side,tier,result,conviction",
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


def compute_nba_pick_window_rates(rows, days_back, end_date):
    """Hit rate per (tier, pick_type) within rolling window. Pushes excluded."""
    cutoff = end_date - timedelta(days=days_back)
    in_window = [
        r for r in rows
        if r.get("game_date") and datetime.strptime(r["game_date"], "%Y-%m-%d").date() >= cutoff
    ]
    tier_stats = defaultdict(lambda: {"hits": 0, "total": 0})
    for r in in_window:
        tier = (r.get("tier") or "").upper()
        ptype = r.get("pick_type")
        result = r.get("result")
        if tier not in ("PRIME", "STRONG", "LEAN"):
            continue
        if ptype not in ("ml", "ats", "total"):
            continue
        if result == "Push":
            continue
        key = f"nba_pick_{tier.lower()}_{ptype}"
        tier_stats[key]["total"] += 1
        if result == "Win":
            tier_stats[key]["hits"] += 1
    return tier_stats


def fetch_all_resolved():
    """Pull all resolved games with NRFI + ML + confluence + spread fields."""
    all_rows = []
    offset = 0
    while True:
        rows = sb_get("mlb_game_results", {
            "nrfi_result": "not.is.null",
            "select": "game_date,nrfi_score,nrfi_result,signal_confluence_net,spread_delta,projected_spread,home_win,close_spread,home_spread_covered,game_id,home_team,away_team,projected_total,close_total,total_runs,total_result,home_bp_relievers_3d,away_bp_relievers_3d",
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

        # Bullpen-gassed cohort (added 2026-05-07).
        # When EITHER team's bp_relievers_3d >= 12, late-inning bullpen quality
        # craters. Hypothesis: gassed pen → more late runs → game total OVER.
        # Surfaces in Sweat Card / content cards as "bucket angle" signal but
        # was firing without audit data — pulled 5/7 pending cohort validation.
        # Direction: bet OVER on game total. Hit: total_runs > close_total.
        h3d = r.get("home_bp_relievers_3d")
        a3d = r.get("away_bp_relievers_3d")
        try:
            h_gassed = h3d is not None and int(h3d) >= 12
            a_gassed = a3d is not None and int(a3d) >= 12
        except (TypeError, ValueError):
            h_gassed = a_gassed = False
        if h_gassed or a_gassed:
            tr = r.get("total_runs")
            ct = r.get("close_total")
            if tr is not None and ct is not None:
                try:
                    actual = float(tr)
                    line = float(ct)
                    if actual != line:  # exclude pushes
                        tier_stats["bullpen_gassed_game_over"]["total"] += 1
                        if actual > line:
                            tier_stats["bullpen_gassed_game_over"]["hits"] += 1
                except (TypeError, ValueError):
                    pass

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
        "bullpen_gassed_game_over",
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

    # ── K-Under prop cohorts (added 2026-05-07) ────────────────────────
    # Andy flagged K-Unders as "sus" 5/6 night after Woo + Singer losses
    # on top of McCullers earlier. Cohort audit grades it.
    print("\nPulling resolved K-Under props...")
    ks_rows = fetch_all_resolved_ks_under_props()
    print(f"Total resolved K-Under props: {len(ks_rows)}")
    ks_tiers = {"k_under_prime", "k_under_strong", "k_under_lean"}
    if ks_rows:
        ks_window_data = {label: compute_k_under_window_rates(ks_rows, days, et_today) for label, days in windows}
        print(f"\n{'K-UNDER TIER':35s} {'7d':>14s} {'30d':>14s} {'STD':>14s}")
        print("-" * 80)
        for tier in sorted(ks_tiers):
            cells = []
            for label, _ in windows:
                stats = ks_window_data[label].get(tier, {"hits": 0, "total": 0})
                if stats["total"] == 0:
                    cells.append("—".rjust(14))
                else:
                    rate = stats["hits"] / stats["total"] * 100
                    cells.append(f"{stats['hits']}-{stats['total']-stats['hits']} ({rate:.1f}%)".rjust(14))
            print(f"{tier:35s} {cells[0]} {cells[1]} {cells[2]}")
            for label, _ in windows:
                stats = ks_window_data[label].get(tier, {"hits": 0, "total": 0})
                if stats["total"] == 0:
                    continue
                upsert_rows.append({
                    "tier": tier,
                    "window_label": label,
                    "computed_date": et_today.isoformat(),
                    "hits": stats["hits"],
                    "total": stats["total"],
                    "hit_rate": round(stats["hits"] / stats["total"], 4),
                    "sport": "mlb",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })

    # ── K-Over alt-line cohorts (added 2026-05-10) ────────────────────
    # Tells users which book line is the right play given the model's
    # projected_ks. Pipeline says "PRIME 87" but pipeline threshold is
    # 5.1; books offer 5.5/6.5/7.5/8.5 with very different juice. This
    # cohort answers: when model projects X.X Ks, what % of the time
    # does the actual count clear book line Y?
    print("\nPulling resolved K-Over props...")
    ko_rows = fetch_all_resolved_ks_over_props()
    print(f"Total resolved K-Over props: {len(ko_rows)}")
    if ko_rows:
        ko_window_data = {label: compute_k_over_alt_window_rates(ko_rows, days, et_today) for label, days in windows}
        ko_buckets = ["6_to_7", "7_to_8", "8_to_9", "9_plus"]
        ko_thresholds = [("5_5", "5.5"), ("6_5", "6.5"), ("7_5", "7.5"), ("8_5", "8.5")]
        # Console grid: rows = projection bucket, cols = book-line threshold
        # using STD window for the published table (most stable sample)
        std_data = ko_window_data["std"]
        print(f"\n{'K-OVER ALT-LINE COHORTS (STD)':35s} {'vs O5.5':>10s} {'vs O6.5':>10s} {'vs O7.5':>10s} {'vs O8.5':>10s}")
        print("-" * 80)
        for bucket in ko_buckets:
            cells = []
            label_n = None
            for thr_key, _ in ko_thresholds:
                key = f"k_over_proj_{bucket}_clears_{thr_key}"
                stats = std_data.get(key, {"hits": 0, "total": 0})
                if label_n is None and stats["total"] > 0:
                    label_n = stats["total"]
                if stats["total"] == 0:
                    cells.append("—".rjust(10))
                else:
                    rate = stats["hits"] / stats["total"] * 100
                    cells.append(f"{rate:.1f}%".rjust(10))
            label = f"k_over_proj_{bucket} (n={label_n or 0})"
            print(f"{label:35s} {cells[0]} {cells[1]} {cells[2]} {cells[3]}")

        # Upsert all bucket × threshold cells across all windows
        for bucket in ko_buckets:
            for thr_key, _ in ko_thresholds:
                tier_name = f"k_over_proj_{bucket}_clears_{thr_key}"
                for label, _ in windows:
                    stats = ko_window_data[label].get(tier_name, {"hits": 0, "total": 0})
                    if stats["total"] == 0:
                        continue
                    upsert_rows.append({
                        "tier": tier_name,
                        "window_label": label,
                        "computed_date": et_today.isoformat(),
                        "hits": stats["hits"],
                        "total": stats["total"],
                        "hit_rate": round(stats["hits"] / stats["total"], 4),
                        "sport": "mlb",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })

    # ── K-Over × Total + Bullpen correlation cohorts (added 2026-05-10) ─
    # Hypothesis: K-Over correlates positively with UNDER (starter went
    # deep) and gassed pen (manager extends starter). Test the hypothesis
    # against real outcomes to inform conditional sizing.
    if ko_rows:
        print("\nFetching game results + bullpen state for K-Over correlation...")
        gr_map = fetch_resolved_game_results_map()
        gc_map = fetch_game_context_pen_map()
        print(f"  Game results: {len(gr_map)}, contexts: {len(gc_map)}")
        ko_corr_data = {label: compute_k_over_correlation_rates(ko_rows, days, et_today, gr_map, gc_map) for label, days in windows}
        ko_corr_tiers = {
            "k_over_with_total_under", "k_over_with_total_over", "k_over_with_total_push",
            "k_over_starter_pen_rested", "k_over_starter_pen_normal", "k_over_starter_pen_gassed",
        }
        print(f"\n{'K-OVER CORRELATION COHORTS':35s} {'7d':>14s} {'30d':>14s} {'STD':>14s}")
        print("-" * 80)
        for tier in sorted(ko_corr_tiers):
            cells = []
            for label, _ in windows:
                stats = ko_corr_data[label].get(tier, {"hits": 0, "total": 0})
                if stats["total"] == 0:
                    cells.append("—".rjust(14))
                else:
                    rate = stats["hits"] / stats["total"] * 100
                    cells.append(f"{stats['hits']}-{stats['total']-stats['hits']} ({rate:.1f}%)".rjust(14))
            print(f"{tier:35s} {cells[0]} {cells[1]} {cells[2]}")
            for label, _ in windows:
                stats = ko_corr_data[label].get(tier, {"hits": 0, "total": 0})
                if stats["total"] == 0:
                    continue
                upsert_rows.append({
                    "tier": tier,
                    "window_label": label,
                    "computed_date": et_today.isoformat(),
                    "hits": stats["hits"],
                    "total": stats["total"],
                    "hit_rate": round(stats["hits"] / stats["total"], 4),
                    "sport": "mlb",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })

    # ── Total-result single-factor cohorts (added 2026-05-10) ─────────
    # User asked: take wRC+, starter ERA, bullpen ERA, recency drift and
    # bounce against game total result. These cohorts answer "given
    # condition X, what % of games went OVER?" — direct conditional
    # probability for total betting decisions.
    print("\nPulling resolved games for total-factor cohorts...")
    tf_rows = fetch_resolved_with_total_features()
    print(f"Total resolved games with features: {len(tf_rows)}")
    tf_tiers = {
        "total_over_wrc_combined_high", "total_over_wrc_combined_mid", "total_over_wrc_combined_low",
        "total_over_starter_xera_low", "total_over_starter_xera_mid", "total_over_starter_xera_high",
        "total_over_bp_era_low", "total_over_bp_era_mid", "total_over_bp_era_high",
        "total_over_l5_combined_hot", "total_over_l5_combined_cold",
        "xera_gap_ge2_over", "xera_gap_2_3_over", "xera_gap_ge3_over",
    }
    if tf_rows:
        tf_window_data = {label: compute_total_factor_window_rates(tf_rows, days, et_today) for label, days in windows}
        print(f"\n{'TOTAL FACTOR COHORT':38s} {'7d':>14s} {'30d':>14s} {'STD':>14s}")
        print("-" * 80)
        for tier in sorted(tf_tiers):
            cells = []
            for label, _ in windows:
                stats = tf_window_data[label].get(tier, {"hits": 0, "total": 0})
                if stats["total"] == 0:
                    cells.append("—".rjust(14))
                else:
                    rate = stats["hits"] / stats["total"] * 100
                    cells.append(f"{stats['hits']}-{stats['total']-stats['hits']} ({rate:.1f}%)".rjust(14))
            print(f"{tier:38s} {cells[0]} {cells[1]} {cells[2]}")
            for label, _ in windows:
                stats = tf_window_data[label].get(tier, {"hits": 0, "total": 0})
                if stats["total"] == 0:
                    continue
                upsert_rows.append({
                    "tier": tier,
                    "window_label": label,
                    "computed_date": et_today.isoformat(),
                    "hits": stats["hits"],
                    "total": stats["total"],
                    "hit_rate": round(stats["hits"] / stats["total"], 4),
                    "sport": "mlb",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })

    # ── Spread/ML single-factor cohorts (added 2026-05-10) ────────────
    # Same framework as total cohorts but for directional outcomes:
    # given home/away advantage in factor X, what % of the time does
    # home win outright (ML) and cover (ATS)?
    if tf_rows:
        sml_window_data = {label: compute_spread_ml_factor_window_rates(tf_rows, days, et_today) for label, days in windows}
        sml_tiers = set()
        for prefix in ("wrc_diff", "starter_diff", "bp_diff", "l5_diff"):
            for bucket in ("home_adv", "away_adv"):
                for outcome in ("ml", "ats"):
                    sml_tiers.add(f"{prefix}_{bucket}_{outcome}")

        print(f"\n{'SPREAD/ML FACTOR COHORT':38s} {'7d':>14s} {'30d':>14s} {'STD':>14s}")
        print("-" * 80)
        for tier in sorted(sml_tiers):
            cells = []
            for label, _ in windows:
                stats = sml_window_data[label].get(tier, {"hits": 0, "total": 0})
                if stats["total"] == 0:
                    cells.append("—".rjust(14))
                else:
                    rate = stats["hits"] / stats["total"] * 100
                    cells.append(f"{stats['hits']}-{stats['total']-stats['hits']} ({rate:.1f}%)".rjust(14))
            print(f"{tier:38s} {cells[0]} {cells[1]} {cells[2]}")
            for label, _ in windows:
                stats = sml_window_data[label].get(tier, {"hits": 0, "total": 0})
                if stats["total"] == 0:
                    continue
                upsert_rows.append({
                    "tier": tier,
                    "window_label": label,
                    "computed_date": et_today.isoformat(),
                    "hits": stats["hits"],
                    "total": stats["total"],
                    "hit_rate": round(stats["hits"] / stats["total"], 4),
                    "sport": "mlb",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })

    # ── Pitcher rest cohorts (added 2026-05-10) ──────────────────────
    # Tests if pitcher rest deviation from norm (4-6 days) correlates with
    # OVER/UNDER + team ML/ATS outcomes. Hypothesis: short rest = tired
    # arm = ER Over + team loses; long rest = rusty = ER Over + team loses.
    print("\nPulling resolved games for pitcher rest cohorts...")
    rest_rows = fetch_resolved_with_rest_features()
    print(f"Total resolved games with rest data: {len(rest_rows)}")
    rest_tiers = set()
    for side in ("home_sp", "away_sp"):
        for bucket in ("short", "normal", "long"):
            rest_tiers.add(f"{side}_rest_{bucket}_over")
            rest_tiers.add(f"{side}_rest_{bucket}_team_ml")
        # Only home tracks team_ats (spread is from home perspective)
        for bucket in ("short", "normal", "long"):
            if side == "home_sp":
                rest_tiers.add(f"{side}_rest_{bucket}_team_ats")
    if rest_rows:
        rest_window_data = {label: compute_pitcher_rest_window_rates(rest_rows, days, et_today) for label, days in windows}
        print(f"\n{'PITCHER REST COHORT':38s} {'7d':>14s} {'30d':>14s} {'STD':>14s}")
        print("-" * 80)
        for tier in sorted(rest_tiers):
            cells = []
            for label, _ in windows:
                stats = rest_window_data[label].get(tier, {"hits": 0, "total": 0})
                if stats["total"] == 0:
                    cells.append("—".rjust(14))
                else:
                    rate = stats["hits"] / stats["total"] * 100
                    cells.append(f"{stats['hits']}-{stats['total']-stats['hits']} ({rate:.1f}%)".rjust(14))
            print(f"{tier:38s} {cells[0]} {cells[1]} {cells[2]}")
            for label, _ in windows:
                stats = rest_window_data[label].get(tier, {"hits": 0, "total": 0})
                if stats["total"] == 0:
                    continue
                upsert_rows.append({
                    "tier": tier,
                    "window_label": label,
                    "computed_date": et_today.isoformat(),
                    "hits": stats["hits"],
                    "total": stats["total"],
                    "hit_rate": round(stats["hits"] / stats["total"], 4),
                    "sport": "mlb",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })

    # ── Umpire cross-cohorts (added 2026-05-10) ──────────────────────
    # Quick win #2: take umpire K rate / over rate / NRFI rate and cross
    # them with our K-Over / Total / NRFI outcomes. Tests if ump
    # environment systematically shifts the model's expected hit rates.
    print("\nPulling umpire stats + games with umpire data...")
    ump_map = fetch_umpire_stats_map()
    ump_games = fetch_resolved_with_umpire()
    print(f"  Umpires in lookup: {len(ump_map)}, games with umpires: {len(ump_games)}")
    if ump_map and ump_games and ko_rows:
        ump_window_data = {label: compute_umpire_window_rates(ump_games, ko_rows, ump_map, days, et_today) for label, days in windows}
        ump_tiers = {
            "k_over_with_ump_k_friendly", "k_over_with_ump_neutral", "k_over_with_ump_k_hostile",
            "total_over_with_ump_over_friendly", "total_over_with_ump_neutral", "total_over_with_ump_over_hostile",
            "nrfi_with_ump_nrfi_friendly", "nrfi_with_ump_neutral", "nrfi_with_ump_nrfi_hostile",
        }
        print(f"\n{'UMPIRE CROSS-COHORT':38s} {'7d':>14s} {'30d':>14s} {'STD':>14s}")
        print("-" * 80)
        for tier in sorted(ump_tiers):
            cells = []
            for label, _ in windows:
                stats = ump_window_data[label].get(tier, {"hits": 0, "total": 0})
                if stats["total"] == 0:
                    cells.append("—".rjust(14))
                else:
                    rate = stats["hits"] / stats["total"] * 100
                    cells.append(f"{stats['hits']}-{stats['total']-stats['hits']} ({rate:.1f}%)".rjust(14))
            print(f"{tier:38s} {cells[0]} {cells[1]} {cells[2]}")
            for label, _ in windows:
                stats = ump_window_data[label].get(tier, {"hits": 0, "total": 0})
                if stats["total"] == 0:
                    continue
                upsert_rows.append({
                    "tier": tier,
                    "window_label": label,
                    "computed_date": et_today.isoformat(),
                    "hits": stats["hits"],
                    "total": stats["total"],
                    "hit_rate": round(stats["hits"] / stats["total"], 4),
                    "sport": "mlb",
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

    # ── NBA pick-tier cohorts (Phase 2, 2026-05-08) ────────────────────
    # Reads resolved rows from nba_game_picks (server-side conviction tier
    # output). Tiers: PRIME / STRONG / LEAN × pick_type (ml/ats/total).
    # Pre-audit thresholds in nba_picks_generator.py are informed-guess —
    # this cohort is the feedback loop that calibrates them.
    print("\nPulling resolved NBA picks...")
    nba_pick_rows = fetch_all_resolved_nba_picks()
    print(f"Total resolved NBA picks: {len(nba_pick_rows)}")
    nba_pick_tiers = {
        "nba_pick_prime_ats", "nba_pick_strong_ats", "nba_pick_lean_ats",
        "nba_pick_prime_total", "nba_pick_strong_total", "nba_pick_lean_total",
        "nba_pick_prime_ml", "nba_pick_strong_ml", "nba_pick_lean_ml",
    }
    if nba_pick_rows:
        nba_pick_window_data = {label: compute_nba_pick_window_rates(nba_pick_rows, days, et_today) for label, days in windows}
        print(f"\n{'NBA PICK TIER':35s} {'7d':>14s} {'30d':>14s} {'STD':>14s}")
        print("-" * 80)
        for tier in sorted(nba_pick_tiers):
            cells = []
            for label, _ in windows:
                stats = nba_pick_window_data[label].get(tier, {"hits": 0, "total": 0})
                if stats["total"] == 0:
                    cells.append("—".rjust(14))
                else:
                    rate = stats["hits"] / stats["total"] * 100
                    cells.append(f"{stats['hits']}-{stats['total']-stats['hits']} ({rate:.1f}%)".rjust(14))
            print(f"{tier:35s} {cells[0]} {cells[1]} {cells[2]}")
            for label, _ in windows:
                stats = nba_pick_window_data[label].get(tier, {"hits": 0, "total": 0})
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

    # ── NFL cohorts (added 2026-05-07) ─────────────────────────────────
    # Phase 1 foundation cohorts. With 1139 games backfilled (2022-2025),
    # baselines are stable enough to track. Phase 2 builds picks on top.
    print("\nPulling resolved NFL games...")
    nfl_rows = fetch_all_resolved_nfl()
    print(f"Total resolved NFL games: {len(nfl_rows)}")
    nfl_tiers = {
        "nfl_home_fav_cover", "nfl_home_dog_cover",
        "nfl_heavy_home_fav", "nfl_heavy_home_dog",
        "nfl_div_home_cover", "nfl_rest_advantage_cover",
        "nfl_dome_over", "nfl_outdoor_over",
    }
    if nfl_rows:
        # NFL is weekly so 7d/30d windows are usually empty mid-season; std is the
        # load-bearing window. Compute all three for consistency with other sports.
        nfl_window_data = {label: compute_nfl_window_rates(nfl_rows, days, et_today) for label, days in windows}
        print(f"\n{'NFL TIER':35s} {'7d':>14s} {'30d':>14s} {'STD':>14s}")
        print("-" * 80)
        for tier in sorted(nfl_tiers):
            cells = []
            for label, _ in windows:
                stats = nfl_window_data[label].get(tier, {"hits": 0, "total": 0})
                if stats["total"] == 0:
                    cells.append("—".rjust(14))
                else:
                    rate = stats["hits"] / stats["total"] * 100
                    cells.append(f"{stats['hits']}-{stats['total']-stats['hits']} ({rate:.1f}%)".rjust(14))
            print(f"{tier:35s} {cells[0]} {cells[1]} {cells[2]}")
            for label, _ in windows:
                stats = nfl_window_data[label].get(tier, {"hits": 0, "total": 0})
                if stats["total"] == 0:
                    continue
                upsert_rows.append({
                    "tier": tier,
                    "window_label": label,
                    "computed_date": et_today.isoformat(),
                    "hits": stats["hits"],
                    "total": stats["total"],
                    "hit_rate": round(stats["hits"] / stats["total"], 4),
                    "sport": "nfl",
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
