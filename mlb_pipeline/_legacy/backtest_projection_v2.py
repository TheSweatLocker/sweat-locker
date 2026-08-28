"""Backtest projection_v2 against historical mlb_game_results.

Runs the v2 projection on every resolved game and compares its outputs to:
  - actual game outcomes (home_win, total_runs, spread)
  - v1 stored projections (projected_total, projected_spread)
  - market closing lines (close_total, close_spread, close ML)

Metrics:
  - Spread MAE  : v1 vs v2 vs market (lower is better)
  - Total MAE   : v1 vs v2 vs market (lower is better)
  - ML hit rate by edge threshold (model_p_home - market_p_home):
      ≥3pt edge:  W-L-rate
      ≥4pt edge:  W-L-rate
      ≥5pt edge:  W-L-rate
      ≥7pt edge:  W-L-rate (PRIME-grade)
  - Total OVER hit rate when model_total - close_total >= threshold
  - Total UNDER hit rate when close_total - model_total >= threshold

A v2 model is shippable when:
  - Spread MAE ≤ market MAE (matches market on average)
  - ML hit rate at ≥4pt edge ≥ 53% (clears break-even after juice)
  - Total OVER/UNDER hit rates at ≥1.5 run delta ≥ 53%

Usage: python backtest_projection_v2.py
"""
import os
import sys
import json
import urllib.parse
import urllib.request
from collections import defaultdict
from dotenv import load_dotenv

import projection_v2 as v2

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def sb_get(table, params):
    qs = urllib.parse.urlencode(params, safe=",.()")
    url = f"{SUPABASE_URL}/rest/v1/{table}?{qs}"
    req = urllib.request.Request(
        url, headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fetch_resolved():
    """Pull all resolved games with the fields v2 needs."""
    all_rows = []
    offset = 0
    select_fields = (
        "game_date,home_team,away_team,"
        "home_sp_xera,away_sp_xera,home_sp_name,away_sp_name,"
        "home_runs_per_game,away_runs_per_game,"
        "home_wrc_plus,away_wrc_plus,"
        "home_wrc_vs_opp_hand,away_wrc_vs_opp_hand,"
        "home_first_inning_era,away_first_inning_era,"
        "home_first_inning_whip,away_first_inning_whip,"
        "home_bullpen_era,away_bullpen_era,"
        "home_team_oaa,away_team_oaa,"
        "home_catcher_framing,away_catcher_framing,"
        "park_run_factor,temperature,"
        "projected_total,projected_spread,"
        "close_total,close_spread,"
        "home_ml_close,away_ml_close,"
        "home_score,away_score,home_win,total_runs,"
        "nrfi_score,nrfi_result,total_result,"
        "home_last5_run_diff,away_last5_run_diff"
    )
    while True:
        rows = sb_get("mlb_game_results", {
            "home_win": "not.is.null",
            "select": select_fields,
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


def fetch_pitcher_buckets():
    """Snapshot of mlb_pitcher_stats for inning-bucket cross-reference.
    Note: this is current-state, not snapshot-at-game-time. Introduces
    forward-look bias but it's the data we have."""
    out = {}
    for offset in range(0, 5000, 1000):
        rows = sb_get("mlb_pitcher_stats", {
            "select": "player_name,innings_1_3_era,innings_1_3_ip,innings_4_6_era,innings_4_6_ip,innings_7_9_era,innings_7_9_ip",
            "limit": "1000",
            "offset": str(offset),
        })
        if not rows:
            break
        for r in rows:
            out[r["player_name"]] = r
        if len(rows) < 1000:
            break
    return out


def fetch_team_buckets():
    """Team-offense inning bucket data per team."""
    out = {}
    rows = sb_get("mlb_team_offense", {
        "select": "team,innings_1_3_runs_per_game,innings_4_6_runs_per_game,innings_7_9_runs_per_game,last10_runs_per_game,last5_runs_per_game",
    })
    for r in rows:
        out[r["team"]] = r
    return out


def fetch_bullpen_buckets():
    """Bullpen inning-bucket data per team."""
    out = {}
    rows = sb_get("mlb_bullpen_stats", {
        "select": "team,pitching_1_3_era,pitching_4_6_era,pitching_7_9_era",
    })
    for r in rows:
        out[r["team"]] = r
    return out


def build_ctx(row, pitcher_buckets, team_buckets, bullpen_buckets):
    """Merge a mlb_game_results row + cross-table data into a v2 ctx dict."""
    ctx = dict(row)
    # Map field names that differ between schemas
    home_team = row["home_team"]
    away_team = row["away_team"]

    # Pitcher inning buckets — look up by name
    home_p = row.get("home_sp_name")
    away_p = row.get("away_sp_name")
    if home_p and home_p in pitcher_buckets:
        pb = pitcher_buckets[home_p]
        ctx["home_innings_1_3_era"] = pb.get("innings_1_3_era")
        ctx["home_innings_4_6_era"] = pb.get("innings_4_6_era")
        ctx["home_innings_7_9_era"] = pb.get("innings_7_9_era")
        ctx["home_sp_ip"] = (
            (pb.get("innings_1_3_ip") or 0)
            + (pb.get("innings_4_6_ip") or 0)
            + (pb.get("innings_7_9_ip") or 0)
        )
    if away_p and away_p in pitcher_buckets:
        pb = pitcher_buckets[away_p]
        ctx["away_innings_1_3_era"] = pb.get("innings_1_3_era")
        ctx["away_innings_4_6_era"] = pb.get("innings_4_6_era")
        ctx["away_innings_7_9_era"] = pb.get("innings_7_9_era")
        ctx["away_sp_ip"] = (
            (pb.get("innings_1_3_ip") or 0)
            + (pb.get("innings_4_6_ip") or 0)
            + (pb.get("innings_7_9_ip") or 0)
        )

    # Team offense buckets
    if home_team in team_buckets:
        tb = team_buckets[home_team]
        ctx["home_innings_1_3_runs_per_game"] = tb.get("innings_1_3_runs_per_game")
        ctx["home_innings_4_6_runs_per_game"] = tb.get("innings_4_6_runs_per_game")
        ctx["home_innings_7_9_runs_per_game"] = tb.get("innings_7_9_runs_per_game")
        ctx["home_last10_runs_per_game"] = tb.get("last10_runs_per_game")
        ctx["home_last5_runs_per_game"] = tb.get("last5_runs_per_game")
    if away_team in team_buckets:
        tb = team_buckets[away_team]
        ctx["away_innings_1_3_runs_per_game"] = tb.get("innings_1_3_runs_per_game")
        ctx["away_innings_4_6_runs_per_game"] = tb.get("innings_4_6_runs_per_game")
        ctx["away_innings_7_9_runs_per_game"] = tb.get("innings_7_9_runs_per_game")
        ctx["away_last10_runs_per_game"] = tb.get("last10_runs_per_game")
        ctx["away_last5_runs_per_game"] = tb.get("last5_runs_per_game")

    # Bullpen buckets
    if home_team in bullpen_buckets:
        bb = bullpen_buckets[home_team]
        ctx["home_pitching_1_3_era"] = bb.get("pitching_1_3_era")
        ctx["home_pitching_4_6_era"] = bb.get("pitching_4_6_era")
        ctx["home_pitching_7_9_era"] = bb.get("pitching_7_9_era")
    if away_team in bullpen_buckets:
        bb = bullpen_buckets[away_team]
        ctx["away_pitching_1_3_era"] = bb.get("pitching_1_3_era")
        ctx["away_pitching_4_6_era"] = bb.get("pitching_4_6_era")
        ctx["away_pitching_7_9_era"] = bb.get("pitching_7_9_era")

    # Map close ML
    ctx["home_ml_odds"] = row.get("home_ml_close")
    ctx["away_ml_odds"] = row.get("away_ml_close")
    return ctx


def _safe(v, default=None):
    if v is None:
        return default
    try:
        f = float(v)
        if f != f:  # NaN
            return default
        return f
    except (TypeError, ValueError):
        return default


def _ml_to_prob(ml):
    if ml is None:
        return None
    if ml < 0:
        return abs(ml) / (abs(ml) + 100)
    return 100 / (ml + 100)


def main():
    print("Fetching resolved games...")
    rows = fetch_resolved()
    print(f"  {len(rows)} resolved games")
    if not rows:
        print("No resolved games — abort.")
        return

    print("Fetching pitcher inning-bucket data...")
    pitcher_buckets = fetch_pitcher_buckets()
    print(f"  {len(pitcher_buckets)} pitchers with bucket data")

    print("Fetching team-offense bucket data...")
    team_buckets = fetch_team_buckets()
    print(f"  {len(team_buckets)} teams")

    print("Fetching bullpen bucket data...")
    bullpen_buckets = fetch_bullpen_buckets()
    print(f"  {len(bullpen_buckets)} teams\n")

    spread_errors_v1 = []
    spread_errors_v2 = []
    spread_errors_market = []
    total_errors_v1 = []
    total_errors_v2 = []
    total_errors_market = []

    # ML edge tracking by threshold
    ml_buckets = {3: [], 4: [], 5: [], 7: []}  # each list: (edge_pct, won)
    nrfi_buckets = []  # (model_p_nrfi, actual NRFI)
    over_buckets = {1.0: [], 1.5: [], 2.0: []}
    under_buckets = {1.0: [], 1.5: [], 2.0: []}

    skipped = 0
    processed = 0
    for row in rows:
        # Sanity: skip if missing key fields
        if row.get("home_score") is None or row.get("away_score") is None:
            skipped += 1
            continue
        try:
            ctx = build_ctx(row, pitcher_buckets, team_buckets, bullpen_buckets)
            proj = v2.project_game(ctx)
        except Exception as e:
            skipped += 1
            continue
        processed += 1

        actual_spread = float(row["home_score"]) - float(row["away_score"])
        actual_total = float(row["home_score"]) + float(row["away_score"])

        # Spread MAE
        v1_spread = _safe(row.get("projected_spread"))
        v2_spread = proj.model_spread
        market_spread = _safe(row.get("close_spread"))
        # close_spread sign convention: negative = home favored. Compare to actual_spread.
        if market_spread is not None:
            spread_errors_market.append(abs(actual_spread - (-market_spread)))
        if v1_spread is not None:
            spread_errors_v1.append(abs(actual_spread - v1_spread))
        spread_errors_v2.append(abs(actual_spread - v2_spread))

        # Total MAE
        v1_total = _safe(row.get("projected_total"))
        v2_total = proj.model_total
        market_total = _safe(row.get("close_total"))
        if market_total is not None:
            total_errors_market.append(abs(actual_total - market_total))
        if v1_total is not None:
            total_errors_v1.append(abs(actual_total - v1_total))
        total_errors_v2.append(abs(actual_total - v2_total))

        # ML edge — model vs no-vig market
        h_ml = _safe(row.get("home_ml_close"))
        a_ml = _safe(row.get("away_ml_close"))
        if h_ml is not None and a_ml is not None:
            p_h_raw = _ml_to_prob(h_ml)
            p_a_raw = _ml_to_prob(a_ml)
            norm = p_h_raw + p_a_raw
            if norm > 0:
                p_h_market = p_h_raw / norm
                home_won = bool(row.get("home_win"))
                edge_home_pct = (proj.p_home_win - p_h_market) * 100
                edge_away_pct = ((1 - proj.p_home_win) - (1 - p_h_market)) * 100
                # Bet whichever side has the bigger edge (if any)
                if edge_home_pct >= edge_away_pct:
                    edge_pct = edge_home_pct
                    won = home_won
                else:
                    edge_pct = edge_away_pct
                    won = not home_won
                for thr in ml_buckets:
                    if edge_pct >= thr:
                        ml_buckets[thr].append((edge_pct, won))

        # NRFI calibration
        if row.get("nrfi_result") in ("NRFI", "YRFI"):
            nrfi_buckets.append((proj.p_nrfi, row["nrfi_result"] == "NRFI"))

        # Total OVER/UNDER edges
        if market_total is not None:
            delta = proj.model_total - market_total
            actual_over = actual_total > market_total
            for thr in over_buckets:
                if delta >= thr:
                    over_buckets[thr].append(actual_over)
            for thr in under_buckets:
                if delta <= -thr:
                    under_buckets[thr].append(not actual_over and actual_total != market_total)

    def mae(lst):
        return sum(lst) / len(lst) if lst else None

    print(f"=== Backtest results: {processed} processed, {skipped} skipped ===\n")

    print("Spread MAE (lower = closer to actual spread):")
    print(f"  Market : {mae(spread_errors_market):.3f}  (n={len(spread_errors_market)})" if spread_errors_market else "  Market : —")
    print(f"  v1     : {mae(spread_errors_v1):.3f}  (n={len(spread_errors_v1)})" if spread_errors_v1 else "  v1     : —")
    print(f"  v2     : {mae(spread_errors_v2):.3f}  (n={len(spread_errors_v2)})" if spread_errors_v2 else "  v2     : —")

    print("\nTotal MAE (lower = closer to actual total):")
    print(f"  Market : {mae(total_errors_market):.3f}  (n={len(total_errors_market)})" if total_errors_market else "  Market : —")
    print(f"  v1     : {mae(total_errors_v1):.3f}  (n={len(total_errors_v1)})" if total_errors_v1 else "  v1     : —")
    print(f"  v2     : {mae(total_errors_v2):.3f}  (n={len(total_errors_v2)})" if total_errors_v2 else "  v2     : —")

    print("\nML hit rate by v2 edge threshold (model − market no-vig %):")
    for thr in sorted(ml_buckets):
        bucket = ml_buckets[thr]
        if not bucket:
            print(f"  ≥{thr}pt edge: 0 picks")
            continue
        wins = sum(1 for _, w in bucket if w)
        n = len(bucket)
        rate = wins / n * 100
        print(f"  ≥{thr}pt edge: {wins}-{n - wins} ({rate:.1f}%)  n={n}")

    print("\nTotal OVER hit rate by v2 delta threshold (model_total − market_total):")
    for thr in sorted(over_buckets):
        bucket = over_buckets[thr]
        if not bucket:
            print(f"  ≥+{thr}: 0 picks")
            continue
        wins = sum(1 for w in bucket if w)
        n = len(bucket)
        rate = wins / n * 100
        print(f"  ≥+{thr}: {wins}-{n - wins} ({rate:.1f}%)  n={n}")

    print("\nTotal UNDER hit rate by v2 delta threshold (market_total − model_total):")
    for thr in sorted(under_buckets):
        bucket = under_buckets[thr]
        if not bucket:
            print(f"  ≥+{thr}: 0 picks")
            continue
        wins = sum(1 for w in bucket if w)
        n = len(bucket)
        rate = wins / n * 100
        print(f"  ≥+{thr}: {wins}-{n - wins} ({rate:.1f}%)  n={n}")

    print("\nNRFI calibration (Brier-style — model_p_nrfi vs actual):")
    if nrfi_buckets:
        # Bucket into 5 deciles
        buckets = defaultdict(list)
        for p, w in nrfi_buckets:
            decile = min(int(p * 10), 9)
            buckets[decile].append(w)
        print(f"  {'p_NRFI':10s} {'Actual':>10s} {'n':>5s}")
        for d in sorted(buckets):
            n = len(buckets[d])
            actual = sum(1 for w in buckets[d] if w) / n * 100
            print(f"  {d/10:.1f}-{(d+1)/10:.1f}      {actual:>5.1f}%   {n:>5d}")

    print("\n=== SHIPPABILITY GATE ===")
    v2_mae = mae(spread_errors_v2)
    market_mae = mae(spread_errors_market)
    if v2_mae and market_mae and v2_mae <= market_mae * 1.05:
        print(f"  ✅ Spread MAE: v2 {v2_mae:.3f} within 5% of market {market_mae:.3f}")
    else:
        print(f"  ❌ Spread MAE: v2 {v2_mae:.3f} vs market {market_mae:.3f}")

    ml_4pt = ml_buckets.get(4, [])
    if ml_4pt:
        rate_4pt = sum(1 for _, w in ml_4pt if w) / len(ml_4pt)
        n_4pt = len(ml_4pt)
        if rate_4pt >= 0.53 and n_4pt >= 30:
            print(f"  ✅ ML hit rate at ≥4pt edge: {rate_4pt*100:.1f}% (n={n_4pt}) clears 53% gate")
        else:
            print(f"  ❌ ML hit rate at ≥4pt edge: {rate_4pt*100:.1f}% (n={n_4pt}) — needs ≥53% with n≥30")
    else:
        print("  ❌ No ML picks at ≥4pt edge — model too conservative or no ML odds in data")


if __name__ == "__main__":
    main()
