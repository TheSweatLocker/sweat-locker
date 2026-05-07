"""NFL Weekly Slate Card — Phase 2 skeleton (Thursday drop generator).

Generates the structured "This Week's NFL Card" payload that the app surfaces
during NFL season. Drops Thursday 8am ET (lines stabilized post-TNF lookahead)
and refreshes Fri/Sat/Sun morning to track line movement + injury news.

Output structure (mirrors the MLB Sweat Card pattern):
  {
    "week_id": "2026_W1",
    "week_number": 1,
    "season": 2026,
    "generated_at": ISO timestamp,
    "lock_of_week": {...},          # highest-conviction single play
    "underdog_of_week": {...},      # heavy_home_dog +7 audit hits 65.4%
    "slate": [...],                 # all games ranked by sweat score
    "audit_cohort_summary": {...},  # live tier rates from mlb_tier_calibration
    "skip_alerts": [...],           # plays the model rejects (chalk traps)
  }

Phase 2 dependencies (June-July build):
  - Odds API NFL game pull (when 2026 schedule + lines appear in Odds API,
    typically June for Week 1 lines)
  - nfl_props pipeline (passing/rushing/receiving yards, anytime TD)
  - Player rolling-window joins via nfl_player_stats

This skeleton wires the structure now so Phase 2 just plugs in the data
sources. In offseason (May-July), no upcoming games → no-op gracefully.

Usage:
    python nfl_weekly_card.py
"""
import os
import sys
import json
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
WRITE_HEADERS = {**HEADERS, "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"}


def today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def horizon_et(days=10):
    """Game lookahead — Thursday card covers TNF + Sunday + MNF, ~10-day window."""
    return today_et() + timedelta(days=days)


def sb_get(table, params=None):
    qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = f"{SUPABASE_URL}/rest/v1/{table}{'?' if qs else ''}{qs}"
    r = requests.get(url, headers=HEADERS, timeout=15)
    return r.json() if r.status_code == 200 else []


def fetch_upcoming_games():
    """Pull upcoming NFL games from nfl_game_results within horizon."""
    today = today_et()
    horizon = horizon_et()
    rows = sb_get("nfl_game_results", {
        "game_date": f"gte.{today}",
        "select": "game_id,season,week,game_type,game_date,weekday,home_team,away_team,close_spread,close_total,close_home_ml,close_away_ml,roof,temp,wind,div_game,home_rest,away_rest,home_qb_name,away_qb_name",
        "order": "game_date.asc",
        "limit": "30",
    })
    return [r for r in rows if r.get("game_date") and r["game_date"] <= horizon.isoformat()]


def fetch_audit_cohorts():
    """Pull live NFL audit cohort rates from mlb_tier_calibration (STD window)."""
    rows = sb_get("mlb_tier_calibration", {
        "window_label": "eq.std",
        "sport": "eq.nfl",
        "select": "tier,hits,total,hit_rate",
    })
    return {r["tier"]: r for r in rows}


def fetch_team_stats(season):
    """Pull team-level stats for the active season (REG type)."""
    rows = sb_get("nfl_team_stats", {
        "season": f"eq.{season}",
        "season_type": "eq.REG",
        "select": "team,games,pass_epa,pass_cpoe,rush_epa,def_sacks,def_ints,def_pass_def",
    })
    return {r["team"]: r for r in rows}


def detect_current_week(games):
    """Pick the upcoming NFL week from the closest game's week field."""
    if not games:
        return None, None
    g = games[0]
    return g.get("season"), g.get("week")


def find_underdog_of_week(games, cohorts):
    """heavy_home_dog cohort hit 65.4% in Phase 1 audit (n=81). When a game
    has home dog of +7 or more, surface as Underdog of the Week with the
    audit-validated rate. Direction: bet HOME side (the dog)."""
    candidates = [g for g in games
                  if g.get("close_spread") is not None and float(g["close_spread"]) <= -7]
    if not candidates:
        return None
    # Sort by spread magnitude (biggest dog first)
    candidates.sort(key=lambda g: float(g["close_spread"]))
    g = candidates[0]
    audit = cohorts.get("nfl_heavy_home_dog", {})
    rate = round((audit.get("hit_rate") or 0) * 100, 1)
    n = audit.get("total")
    return {
        "game_id": g["game_id"],
        "matchup": f"{g['away_team']} @ {g['home_team']}",
        "pick": f"{g['home_team']} +{abs(float(g['close_spread']))}",
        "tier": "STRONG",  # audit-validated PRIME until Phase 2 layered with player data
        "audited_rate": rate,
        "audited_n": n,
        "rationale": f"Home dog +{abs(float(g['close_spread']))} cohort hits {rate}% on {n} historical games (2022-2025). Classic letdown spot for road favorite.",
    }


def find_outdoor_under_angles(games, cohorts):
    """Outdoor totals trended UNDER at 52.1% in Phase 1 audit. When game is
    outdoors AND total ≥ 47 AND weather is moderate-to-bad (cold or windy),
    surface as a UNDER lean."""
    audit = cohorts.get("nfl_outdoor_over", {})
    rate_under = 100 - round((audit.get("hit_rate") or 0) * 100, 1)
    n = audit.get("total")
    plays = []
    for g in games:
        if (g.get("roof") or "").lower() != "outdoors":
            continue
        if g.get("close_total") is None or float(g["close_total"]) < 45:
            continue
        temp = g.get("temp")
        wind = g.get("wind")
        weather_flag = []
        if temp is not None and int(temp) <= 40:
            weather_flag.append(f"cold ({temp}°F)")
        if wind is not None and int(wind) >= 12:
            weather_flag.append(f"wind {wind}mph")
        if not weather_flag:
            continue  # no weather edge, skip
        plays.append({
            "game_id": g["game_id"],
            "matchup": f"{g['away_team']} @ {g['home_team']}",
            "pick": f"UNDER {g['close_total']}",
            "tier": "LEAN",
            "audited_rate": rate_under,
            "audited_n": n,
            "rationale": f"Outdoor totals UNDER cohort {rate_under}% on {n} games. Weather flags: {', '.join(weather_flag)}.",
        })
    return plays[:3]


def build_card():
    print(f"=== NFL Weekly Slate Card ({today_et()}) ===")
    games = fetch_upcoming_games()
    print(f"  Upcoming games (next 10 days): {len(games)}")

    if not games:
        print("  No NFL games in horizon — likely offseason. Skipping card generation.")
        return

    season, week = detect_current_week(games)
    cohorts = fetch_audit_cohorts()
    team_stats = fetch_team_stats(season)
    print(f"  Season: {season} | Week: {week}")
    print(f"  Audit cohorts loaded: {len(cohorts)} | Team stats loaded: {len(team_stats)}")

    underdog = find_underdog_of_week(games, cohorts)
    outdoor_unders = find_outdoor_under_angles(games, cohorts)

    card = {
        "week_id": f"{season}_W{week}",
        "season": season,
        "week_number": week,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "underdog_of_week": underdog,
        "outdoor_under_angles": outdoor_unders,
        # Phase 2 will populate these when props pipeline + Odds API integration ships:
        "lock_of_week": None,           # highest-conviction prop or ML
        "weekly_parlay": None,          # 3-5 leg conviction parlay
        "slate": [
            {
                "game_id": g["game_id"],
                "matchup": f"{g['away_team']} @ {g['home_team']}",
                "kickoff": f"{g.get('weekday','?')} {g.get('game_date')}",
                "spread": g.get("close_spread"),
                "total": g.get("close_total"),
                "div_game": g.get("div_game"),
                "roof": g.get("roof"),
            }
            for g in games[:16]
        ],
        "audit_cohort_summary": {
            tier: {"hits": v.get("hits"), "total": v.get("total"), "rate": v.get("hit_rate")}
            for tier, v in cohorts.items()
        },
        "skip_alerts": [],  # populated when chalk-trap detector is built in Phase 2
    }

    # Upsert to jerry_cache with NFL-specific cache_key
    cache_key = f"nfl_weekly_card_{season}_w{week}"
    payload = {
        "cache_key": cache_key,
        "game_id": cache_key,
        "sport": "NFL",
        "narrative": f"NFL Week {week} ({season}) Slate Card",
        "data": card,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_cache",
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"},
        params={"on_conflict": "cache_key"},
        json=payload, timeout=15,
    )
    if r.status_code in (200, 201, 204):
        print(f"\n✅ NFL weekly card stored: underdog={underdog['matchup'] if underdog else '—'}, "
              f"outdoor_under_angles={len(outdoor_unders)}, slate_size={len(card['slate'])}")
    else:
        print(f"\n❌ Card upsert failed {r.status_code}: {r.text[:200]}")


if __name__ == "__main__":
    build_card()
