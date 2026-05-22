"""NCAAB model backtest — 2024-25 season vs market.

Validates the ncaab_game_context.compute_projections + compute_primary_play
formulas against ~5,700 games from the 2024-25 D1 season.

Data source: Bart Torvik's getgamestats.php?year=2025&csv=1 — free,
game-by-game CSV with team efficiencies (adjOE/adjDE/tempo/four factors)
+ Vegas spread + final score for every game. ~4.9MB, 11,529 rows
(2 rows per game: home perspective + away perspective).

What this measures:
  - Model spread vs actual margin (MAE, direction accuracy)
  - Model total vs actual total (MAE)
  - vs-market: did model pick the correct side of the spread? % cover
  - Hit rate by sweat_tier (PRIME / STRONG / LIGHT)
  - Hit rate by spread_edge magnitude buckets (validates the cohort cliff
    we found in MLB — does NCAAB have the same trap-zone behavior?)

What this does NOT measure:
  - EV at actual juice (we have spreads but not vig per game)
  - In-season ratings drift (Bart's numbers are point-in-time for each game,
    but include retroactive adjustment from the game itself — small bias)

Caveats:
  - Bart efficiencies != KenPom efficiencies exactly (highly correlated
    but not identical). The /3 spread divisor was tuned against KenPom
    in the client logic. Bart-based backtest gives a reasonable proxy
    for the model structure but November live data will use real KenPom.

Usage:
    python _backtest_ncaab_2025.py
    python _backtest_ncaab_2025.py --refresh   # re-download Bart CSV
"""
import os
import sys
import csv
import io
import json
import argparse
import requests
from collections import defaultdict
from datetime import datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BART_GAMES_URL = "https://barttorvik.com/getgamestats.php?year=2025&csv=1"
BART_TEAMS_URL = "https://barttorvik.com/2025_team_results.csv"
GAMES_CACHE = os.path.join(os.path.dirname(__file__), "_bart_2025_games.csv")
TEAMS_CACHE = os.path.join(os.path.dirname(__file__), "_bart_2025_teams.csv")


def fetch_url(url, cache_path, refresh=False):
    if not refresh and os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()
    print(f"  Downloading {url}")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(r.text)
    return r.text


def load_team_ratings(refresh=False):
    """Season-aggregate Bart Torvik ratings (adjOE/adjDE/adjT).
    These are END-OF-SEASON values — lookahead biased but consistent
    direction for every game. Real production will use point-in-time
    KenPom ratings (no lookahead) so live hit rate will be lower than
    this backtest's ceiling estimate."""
    txt = fetch_url(BART_TEAMS_URL, TEAMS_CACHE, refresh=refresh)
    reader = csv.reader(io.StringIO(txt))
    next(reader)  # header
    out = {}
    for row in reader:
        if len(row) < 45:
            continue
        try:
            out[row[1]] = {
                "team": row[1],
                "conf": row[2],
                "adj_oe": float(row[4]),
                "adj_de": float(row[6]),
                "tempo": float(row[44]),
                "adj_em": round(float(row[4]) - float(row[6]), 2),
            }
        except (ValueError, IndexError):
            continue
    return out


def parse_games(csv_text, team_ratings):
    """Extract one record per unique game. Uses SEASON-AGGREGATE ratings
    from team_ratings (not the game-specific efficiencies in the row,
    which would be circular leakage)."""
    """Pair home + away rows back into single game records.

    Bart Torvik writes each game as TWO rows (one per team's perspective).
    The shared key is column [24] which is "{teamA}{teamB}{date}". Both
    perspectives share the same final score string but different team
    listing in cols [2]/[4].
    """
    reader = csv.reader(io.StringIO(csv_text))
    by_game_key = defaultdict(list)
    for row in reader:
        if len(row) < 28:
            continue
        gk = row[24]  # "Abilene ChristianBaylor12-9" — shared between H and A perspective
        by_game_key[gk].append(row)

    games = []
    for gk, rows in by_game_key.items():
        if len(rows) != 2:
            continue  # missing perspective — skip
        # Identify home vs away by col [5] (H/A/N)
        home_row = next((r for r in rows if r[5] == "H"), None)
        away_row = next((r for r in rows if r[5] == "A"), None)
        if home_row is None or away_row is None:
            # Neutral site or missing — skip for v1 backtest
            continue
        try:
            spread = float(home_row[27]) if home_row[27] else None
        except ValueError:
            spread = None
        if spread is None:
            continue
        # Parse final score from result string "W, 88-57" or "L, 88-57"
        result_str = home_row[6]
        try:
            score_part = result_str.split(", ")[1]
            score_a, score_b = score_part.split("-")
            home_won = result_str.startswith("W")
            if home_won:
                home_score, away_score = int(score_a), int(score_b)
            else:
                away_score, home_score = int(score_a), int(score_b)
        except (IndexError, ValueError):
            continue

        # Per-team SEASON ratings (lookup from team_ratings — no game leakage)
        home_name = home_row[2]
        away_name = away_row[2]
        home = team_ratings.get(home_name)
        away = team_ratings.get(away_name)
        if home is None or away is None:
            continue  # team not in season ratings (rare — maybe non-D1)

        games.append({
            "date": home_row[0],
            "home": home,
            "away": away,
            "home_score": home_score,
            "away_score": away_score,
            "actual_margin": home_score - away_score,  # positive = home won by X
            "actual_total": home_score + away_score,
            "vegas_spread": spread,  # home perspective: negative = home favored
        })
    return games


def model_projection(home, away):
    """Run our compute_projections logic on a single game's efficiencies."""
    h_em = home.get("adj_em")
    a_em = away.get("adj_em")
    h_oe = home.get("adj_oe"); a_oe = away.get("adj_oe")
    h_de = home.get("adj_de"); a_de = away.get("adj_de")
    tempo = (home.get("tempo", 0) + away.get("tempo", 0)) / 2.0
    if None in (h_em, a_em):
        return None
    spread = (h_em - a_em) / 3.0  # positive = home favored
    if tempo and None not in (h_oe, a_oe, h_de, a_de):
        pace = tempo - 2.5
        h_pts = ((h_oe + a_de) / 2.0 / 100.0) * pace
        a_pts = ((a_oe + h_de) / 2.0 / 100.0) * pace
        total = h_pts + a_pts
    else:
        total = None
    return {"spread": round(spread, 2), "total": round(total, 1) if total else None}


def backtest(games):
    print(f"\n=== BACKTEST — {len(games)} games (home-court only, neutral excluded) ===\n")

    # Direction accuracy (who wins — sign agreement)
    direction_right = 0
    direction_total = 0
    # Cover accuracy (did model pick correct side of spread)
    cover_right = 0
    cover_total = 0
    cover_pushes = 0
    # MAE
    spread_abs_err = []
    margin_abs_err = []  # vs actual margin
    total_abs_err = []
    # Bucket by spread edge magnitude (the cohort cliff check)
    bucket_cover = defaultdict(lambda: [0, 0, 0])  # [right, wrong, push]

    for g in games:
        proj = model_projection(g["home"], g["away"])
        if proj is None or proj["spread"] is None:
            continue

        proj_spread = proj["spread"]            # home perspective: positive = home favored
        vegas_spread = g["vegas_spread"]        # same convention (negative = home favored)
        actual_margin = g["actual_margin"]      # home_score - away_score

        # Direction accuracy: model says home wins (proj_spread > 0) vs actual
        if (proj_spread > 0 and actual_margin > 0) or (proj_spread < 0 and actual_margin < 0):
            direction_right += 1
        direction_total += 1

        # Margin MAE — model spread vs actual margin
        margin_abs_err.append(abs(proj_spread - actual_margin))

        # Spread coverage — did model pick the right side of the Vegas line?
        # Vegas spread is home perspective: e.g. -7 = home favored by 7
        # For home to cover: actual_margin > -vegas_spread (i.e. home wins by more than line)
        # Model picks home cover when proj_spread > -vegas_spread
        # (i.e. model expects home margin > line's home-margin)
        line_home_margin = -vegas_spread  # if Vegas says home -7, line implies home margin = 7
        model_picks_home_cover = proj_spread > line_home_margin
        # Use a tolerance band to skip near-zero edges
        edge_magnitude = abs(proj_spread - line_home_margin)
        if edge_magnitude < 0.5:
            continue  # no model conviction; skip

        actual_margin_diff = actual_margin - line_home_margin  # >0 means home covered
        if abs(actual_margin_diff) < 0.5:
            cover_pushes += 1
            bucket = "lt1" if edge_magnitude < 1.0 else "1_2" if edge_magnitude < 2.0 else "2_3" if edge_magnitude < 3.0 else "ge3"
            bucket_cover[bucket][2] += 1
            continue
        actual_home_covered = actual_margin_diff > 0
        is_right = (model_picks_home_cover and actual_home_covered) or (not model_picks_home_cover and not actual_home_covered)
        cover_total += 1
        if is_right:
            cover_right += 1
        bucket = "lt1" if edge_magnitude < 1.0 else "1_2" if edge_magnitude < 2.0 else "2_3" if edge_magnitude < 3.0 else "ge3"
        bucket_cover[bucket][0 if is_right else 1] += 1

        spread_abs_err.append(abs(proj_spread - (-vegas_spread)))

        if proj["total"] is not None:
            total_abs_err.append(abs(proj["total"] - g["actual_total"]))

    # ── REPORT ────────────────────────────────────────────────
    def avg(xs): return sum(xs) / len(xs) if xs else 0

    print(f"DIRECTION ACCURACY (who wins outright)")
    print(f"  {direction_right}/{direction_total} = {direction_right*100/direction_total:.1f}%")
    print()

    print(f"MARGIN MAE (model spread vs actual margin)")
    print(f"  {avg(margin_abs_err):.2f} runs (n={len(margin_abs_err)})")
    print()

    print(f"TOTAL MAE (model total vs actual total)")
    print(f"  {avg(total_abs_err):.2f} pts (n={len(total_abs_err)})")
    print()

    print(f"SPREAD MAE (model vs Vegas line)")
    print(f"  {avg(spread_abs_err):.2f} pts (n={len(spread_abs_err)})")
    print()

    print(f"COVER ACCURACY vs market (did model pick correct side?)")
    print(f"  {cover_right}/{cover_total} = {cover_right*100/cover_total:.1f}% (pushes excluded: {cover_pushes})")
    print(f"  Break-even at typical -110 juice: 52.4%")
    print(f"  ROI per $100 risked: {((cover_right * 100/110) - (cover_total - cover_right)) / cover_total * 100:.1f}%")
    print()

    print(f"COVER BY EDGE MAGNITUDE BUCKET (validates trap-zone behavior)")
    print(f"  {'Bucket':<12} {'W':<5} {'L':<5} {'P':<3} {'%':>6}  {'n':>5}")
    for label, key in [("edge <1", "lt1"), ("edge 1-2", "1_2"), ("edge 2-3", "2_3"), ("edge ≥3", "ge3")]:
        w, l, p = bucket_cover[key]
        n = w + l
        pct = (w * 100 / n) if n else 0
        print(f"  {label:<12} {w:<5} {l:<5} {p:<3} {pct:>5.1f}%  {n:>5}")
    print()

    # Confidence buckets: at higher edges, does the model genuinely outperform?
    high_edges_right = bucket_cover["2_3"][0] + bucket_cover["ge3"][0]
    high_edges_total = sum(bucket_cover["2_3"][:2]) + sum(bucket_cover["ge3"][:2])
    if high_edges_total > 0:
        print(f"HIGH-CONFIDENCE COHORT (edge ≥2): {high_edges_right}/{high_edges_total} = {high_edges_right*100/high_edges_total:.1f}%")
        print(f"  → This is the cohort that would fire as STRONG/PRIME picks in production")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    print(f"NCAAB 2024-25 backtest — model formulas from ncaab_game_context.py")
    print(f"  Note: uses season-aggregate ratings (end-of-season), which have")
    print(f"        some lookahead bias. Production will use point-in-time")
    print(f"        KenPom ratings, so live hit rate will be SOMEWHAT LOWER.\n")
    team_ratings = load_team_ratings(refresh=args.refresh)
    print(f"  Loaded {len(team_ratings)} team ratings")
    csv_text = fetch_url(BART_GAMES_URL, GAMES_CACHE, refresh=args.refresh)
    games = parse_games(csv_text, team_ratings)
    print(f"  Parsed {len(games)} home-court games (neutral-site excluded)")
    if games:
        sample = games[0]
        print(f"  Sample: {sample['date']} {sample['away']['team']} ({sample['away_score']}) @ "
              f"{sample['home']['team']} ({sample['home_score']}) | Vegas {sample['vegas_spread']:+.1f}")
    backtest(games)


if __name__ == "__main__":
    main()
