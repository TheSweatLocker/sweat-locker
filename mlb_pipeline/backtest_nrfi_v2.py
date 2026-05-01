"""Backtest the NRFI v2 formula (with umpire + 1st-inning team offense additions).

Method:
1. Pull all resolved games from mlb_game_results (has nrfi_score, nrfi_result, umpire, teams)
2. For each game, compute the DELTA from the v2 additions:
   - Umpire adjustment: lookup ump nrfi_rate from mlb_umpires, scale to score units
   - 1st-inning offense adjustment: lookup team inning_1_runs_per_game from
     mlb_team_offense (current state as proxy for historical — introduces some
     look-ahead bias, but the backtest is directional)
3. new_score = old_score + ump_adj + home_off_adj + away_off_adj
4. Reclassify tier on the new score
5. Compare hit rates: old tier classification vs new tier classification

Limitations:
- Pitcher/team stats are CURRENT (not snapshot at game time)
- Only the v2 additions are recomputed; rest of formula unchanged
- Smaller sample for tier shifts (only games near tier boundaries move)

Usage:
    python backtest_nrfi_v2.py
"""

import os
import sys
import json
import urllib.parse
import urllib.request
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


def sb_get(table, params):
    qs = urllib.parse.urlencode(params, safe=",.()")
    url = f"{SUPABASE_URL}/rest/v1/{table}?{qs}"
    req = urllib.request.Request(
        url, headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return []


def fetch_all_games():
    """Pull all resolved NRFI games."""
    all_rows = []
    offset = 0
    while True:
        rows = sb_get("mlb_game_results", {
            "nrfi_result": "not.is.null",
            "nrfi_score": "not.is.null",
            "select": "game_date,home_team,away_team,nrfi_score,nrfi_result,umpire",
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


def build_ump_lookup():
    rows = sb_get("mlb_umpires", {"select": "ump_name,nrfi_rate"})
    return {r["ump_name"]: r.get("nrfi_rate") for r in rows if r.get("ump_name")}


def build_team_inn1_lookup():
    rows = sb_get("mlb_team_offense", {"select": "team,inning_1_runs_per_game"})
    return {r["team"]: r.get("inning_1_runs_per_game") for r in rows if r.get("team")}


def ump_adj(nrfi_rate):
    """Dialed-back: ±3 instead of ±6"""
    if nrfi_rate is None:
        return 0
    try:
        return max(-3, min(3, round((float(nrfi_rate) - 0.50) * 15)))
    except (TypeError, ValueError):
        return 0


def inn1_adj(rpg):
    """Dialed-back: ±2 per team instead of ±5"""
    if rpg is None:
        return 0
    try:
        delta = float(rpg) - 0.50
        return max(-2, min(2, round(-delta * 8)))
    except (TypeError, ValueError):
        return 0


def classify_tier(score):
    if score is None:
        return None
    s = int(round(score))
    if s >= 95: return "95+ volatile"
    if s >= 90: return "90-94 PRIME"
    if s >= 80: return "80-89 dead"
    if s >= 70: return "70-79 mild lean"
    if s >= 60: return "60-69 (skip)"
    if s >= 50: return "50-59 neutral"
    if s <= 40: return "<=40 YRFI lean"
    return "41-49 neutral"


def hit_check(tier, result):
    """Return 1 if the tier's expected outcome matches actual, 0 otherwise.
    NRFI tiers (50+) expect NRFI; YRFI tier (<=40) expects YRFI.
    Skip dead-zone / coinflip tiers from the win-rate calc.
    """
    res = (result or "").upper()
    if not tier:
        return None
    if tier == "<=40 YRFI lean":
        return 1 if res == "YRFI" else 0
    if tier in ("90-94 PRIME", "70-79 mild lean"):
        return 1 if res == "NRFI" else 0
    return None  # other tiers are not picks


def main():
    print("Loading data...")
    games = fetch_all_games()
    ump_lookup = build_ump_lookup()
    inn1_lookup = build_team_inn1_lookup()
    print(f"Resolved games: {len(games)} | umpires: {len(ump_lookup)} | teams: {len(inn1_lookup)}")

    # Track tier transitions and hit rate per tier (old vs new)
    old_tier_stats = defaultdict(lambda: {"hits": 0, "total": 0})
    new_tier_stats = defaultdict(lambda: {"hits": 0, "total": 0})
    transitions = defaultdict(int)  # (old_tier, new_tier) -> count
    upgrade_to_prime = 0  # games shifted into 90-94
    downgrade_from_prime = 0
    score_shifted = 0
    total_with_ump = 0

    for g in games:
        old_score = g.get("nrfi_score")
        result = g.get("nrfi_result")
        if old_score is None or not result:
            continue
        ump = g.get("umpire")
        home = g.get("home_team", "")
        away = g.get("away_team", "")
        ump_rate = ump_lookup.get(ump)
        u_adj = ump_adj(ump_rate)
        h_adj = inn1_adj(inn1_lookup.get(home))
        a_adj = inn1_adj(inn1_lookup.get(away))
        if u_adj != 0 or h_adj != 0 or a_adj != 0:
            score_shifted += 1
        if ump_rate is not None:
            total_with_ump += 1
        # Apply with PRIME tier guard (mirrors production formula)
        v2_adj = u_adj + h_adj + a_adj
        provisional = old_score + v2_adj
        base_in_prime = 90 <= old_score <= 94
        prov_in_prime = 90 <= provisional <= 94
        if base_in_prime and not prov_in_prime:
            new_score = max(90, min(94, provisional))
        elif not base_in_prime and prov_in_prime:
            new_score = 89 if old_score < 90 else 95
        else:
            new_score = provisional
        new_score = max(0, min(100, new_score))

        old_tier = classify_tier(old_score)
        new_tier = classify_tier(new_score)
        if old_tier != new_tier:
            transitions[(old_tier, new_tier)] += 1
            if new_tier == "90-94 PRIME" and old_tier != "90-94 PRIME":
                upgrade_to_prime += 1
            if old_tier == "90-94 PRIME" and new_tier != "90-94 PRIME":
                downgrade_from_prime += 1

        # Hit rate tracking
        old_hit = hit_check(old_tier, result)
        new_hit = hit_check(new_tier, result)
        if old_hit is not None:
            old_tier_stats[old_tier]["total"] += 1
            old_tier_stats[old_tier]["hits"] += old_hit
        if new_hit is not None:
            new_tier_stats[new_tier]["total"] += 1
            new_tier_stats[new_tier]["hits"] += new_hit

    print(f"\nGames where v2 score shifted vs v1: {score_shifted} of {len(games)}")
    print(f"Games with umpire data available: {total_with_ump}")
    print(f"Tier upgrades INTO 90-94 PRIME: {upgrade_to_prime}")
    print(f"Tier downgrades OUT OF 90-94 PRIME: {downgrade_from_prime}")

    # Per-tier hit rate comparison
    print(f"\n{'TIER':25s} {'OLD HIT RATE':>20s} {'NEW HIT RATE':>20s} {'DELTA':>10s}")
    print("-" * 80)
    tier_order = ["90-94 PRIME", "70-79 mild lean", "<=40 YRFI lean"]
    for t in tier_order:
        o = old_tier_stats.get(t, {"hits": 0, "total": 0})
        n = new_tier_stats.get(t, {"hits": 0, "total": 0})
        o_rate = (o["hits"] / o["total"] * 100) if o["total"] else 0
        n_rate = (n["hits"] / n["total"] * 100) if n["total"] else 0
        delta = n_rate - o_rate
        o_str = f"{o['hits']}-{o['total']-o['hits']} ({o_rate:.1f}%)"
        n_str = f"{n['hits']}-{n['total']-n['hits']} ({n_rate:.1f}%)"
        print(f"{t:25s} {o_str:>20s} {n_str:>20s} {delta:+.1f}pt")

    # Sample of biggest tier transitions (for inspection)
    print(f"\nTop 10 tier transitions (old → new):")
    for (old_t, new_t), count in sorted(transitions.items(), key=lambda x: -x[1])[:10]:
        print(f"  {old_t:25s} → {new_t:25s}  ×{count}")


if __name__ == "__main__":
    main()
