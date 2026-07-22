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
    """Pull live NFL audit cohort rates from mlb_tier_calibration.

    nfl_cohort_backfill.py writes window_label='lifetime' with columns
    (tier, wins, losses, pushes, hit_pct, sample_n). Latest computed_date wins.
    """
    rows = sb_get("mlb_tier_calibration", {
        "window_label": "eq.lifetime",
        "sport": "eq.nfl",
        "select": "tier,wins,losses,pushes,hit_pct,sample_n,computed_date",
        "order": "computed_date.desc",
        "limit": "50",
    })
    latest = {}
    for r in rows:
        key = r["tier"]
        if key not in latest:
            latest[key] = r
    return latest


def _cohort_rate(cohort_key: str, cohorts: dict) -> tuple:
    """Return (hit_pct, sample_n) for a cohort tag; handles both new and old shapes."""
    key = cohort_key.upper().replace("NFL_", "")
    r = cohorts.get(key) or cohorts.get(cohort_key) or {}
    return (r.get("hit_pct") or r.get("hit_rate") or 0,
            r.get("sample_n") or r.get("total") or 0)


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
    rate, n = _cohort_rate("nfl_heavy_home_dog", cohorts)
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
    rate_under, n = _cohort_rate("nfl_outdoor_under", cohorts)
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


def find_lock_of_week(games, cohorts, underdog):
    """Highest-conviction play of the week. Prefers heavy_home_dog when the
    audit rate is >= 60% AND spread >= +7. Otherwise falls back to the biggest
    audited-cohort play the model finds.
    """
    dog_rate, dog_n = _cohort_rate("nfl_heavy_home_dog", cohorts)
    if underdog and dog_rate >= 60 and dog_n >= 40:
        return {
            "game_id": underdog["game_id"],
            "matchup": underdog["matchup"],
            "pick": underdog["pick"],
            "tier": "PRIME",
            "conviction": min(100, int(dog_rate + 15)),  # audit + narrative boost
            "audited_rate": dog_rate,
            "audited_n": dog_n,
            "rationale": (f"Lock: {underdog['pick']} — home-dog +7 cohort "
                          f"hits {dog_rate}% ({dog_n} games, 2022-2025). "
                          "Audit-anchored highest-conviction of the week."),
        }
    # Fallback: strongest weather-driven UNDER when no lock-worthy dog
    under_rate, under_n = _cohort_rate("nfl_outdoor_under", cohorts)
    for g in games:
        if (g.get("roof") or "").lower() != "outdoors": continue
        if g.get("close_total") is None: continue
        temp = g.get("temp"); wind = g.get("wind")
        if not ((temp is not None and int(temp) <= 35) or
                (wind is not None and int(wind) >= 18)):
            continue
        if under_rate < 55 or under_n < 100: continue
        return {
            "game_id": g["game_id"],
            "matchup": f"{g['away_team']} @ {g['home_team']}",
            "pick": f"UNDER {g['close_total']}",
            "tier": "STRONG",
            "conviction": int(under_rate + 10),
            "audited_rate": under_rate,
            "audited_n": under_n,
            "rationale": (f"Lock: UNDER {g['close_total']} — outdoor + "
                          f"{'cold '+str(temp)+'°F' if temp is not None and int(temp) <= 35 else ''}"
                          f"{' + ' if temp is not None and wind is not None else ''}"
                          f"{'wind '+str(wind)+'mph' if wind is not None and int(wind) >= 18 else ''}. "
                          f"Cohort {under_rate}% ({under_n} games)."),
        }
    return None


def find_weekly_parlay(games, cohorts, lock, underdog, outdoor_unders):
    """3-leg conviction parlay. Legs must NOT duplicate lock or come from
    the same game (correlation risk). Anchored by highest-audited-rate cohorts.
    """
    used_games = set()
    if lock: used_games.add(lock["game_id"])
    if underdog and underdog.get("game_id") != (lock or {}).get("game_id"):
        pass  # underdog may still qualify as a leg

    legs = []

    # Leg 1: heavy home dog (if not the lock)
    if underdog and underdog["game_id"] not in used_games:
        legs.append({
            "matchup": underdog["matchup"],
            "pick": underdog["pick"],
            "cohort": "heavy_home_dog",
            "audited_rate": underdog["audited_rate"],
        })
        used_games.add(underdog["game_id"])

    # Leg 2: strongest outdoor UNDER
    for u in outdoor_unders:
        if u["game_id"] in used_games: continue
        legs.append({
            "matchup": u["matchup"],
            "pick": u["pick"],
            "cohort": "outdoor_under",
            "audited_rate": u["audited_rate"],
        })
        used_games.add(u["game_id"])
        break

    # Leg 3: division road dog (nfl_div_home_cover fades home) — pick away spread
    div_rate, div_n = _cohort_rate("nfl_div_home_cover", cohorts)
    away_edge = 100 - div_rate if div_rate < 50 else 0
    if away_edge >= 2 and div_n >= 200:
        for g in games:
            if g["game_id"] in used_games: continue
            if g.get("div_game") is not True: continue
            if g.get("close_spread") is None or float(g["close_spread"]) <= 0: continue
            # spread > 0 → home fav; away is the dog in a division game
            legs.append({
                "matchup": f"{g['away_team']} @ {g['home_team']}",
                "pick": f"{g['away_team']} +{g['close_spread']}",
                "cohort": "div_home_cover_fade",
                "audited_rate": 100 - div_rate,
            })
            used_games.add(g["game_id"])
            break

    if len(legs) < 2:
        return None  # parlay needs >=2 legs

    return {
        "legs": legs,
        "leg_count": len(legs),
        "combined_rationale": (
            f"{len(legs)}-leg parlay anchored by audit-validated cohorts. "
            "No two legs share a game (correlation control). "
            "Not a lock — parlay math means all legs must hit."
        ),
    }


def find_skip_alerts(games, cohorts):
    """Chalk-trap detector. Surface games the pipeline recommends AVOIDING."""
    alerts = []
    # Trap 1: mid-range home favorite (-3.5 to -6.5) in division game — 48.6% audit
    div_rate, div_n = _cohort_rate("nfl_div_home_cover", cohorts)
    for g in games:
        sp = g.get("close_spread")
        if sp is None: continue
        sp = float(sp)
        if 3.5 <= sp <= 6.5 and g.get("div_game") is True:
            alerts.append({
                "game_id": g["game_id"],
                "matchup": f"{g['away_team']} @ {g['home_team']}",
                "reason": "chalk_div_home_fav",
                "message": (f"Skip {g['home_team']} -{sp} chalk. Division home favs "
                            f"cover {div_rate}% ({div_n} games). Coinflip trap."),
            })
    # Trap 2: dome + total >= 50 (marketplace already priced up)
    for g in games:
        if (g.get("roof") or "").lower() not in ("dome", "closed"): continue
        tot = g.get("close_total")
        if tot is None or float(tot) < 50: continue
        alerts.append({
            "game_id": g["game_id"],
            "matchup": f"{g['away_team']} @ {g['home_team']}",
            "reason": "dome_over_juiced",
            "message": (f"Skip OVER {tot}. Dome with 50+ total is market-priced; "
                        f"dome_over cohort ~51% (coinflip). No edge."),
        })
    return alerts


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
    lock = find_lock_of_week(games, cohorts, underdog)
    parlay = find_weekly_parlay(games, cohorts, lock, underdog, outdoor_unders)
    skip_alerts = find_skip_alerts(games, cohorts)

    # Schedule-only mode: no lines yet (Odds API empty). MVP for Aug 7 preseason
    # + offseason weeks where slate exists but market isn't loaded.
    lines_loaded = any(g.get("close_spread") is not None for g in games)
    card_mode = "full" if lines_loaded else "schedule_only"

    card = {
        "week_id": f"{season}_W{week}",
        "season": season,
        "week_number": week,
        "mode": card_mode,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lock_of_week": lock,
        "underdog_of_week": underdog,
        "outdoor_under_angles": outdoor_unders,
        "weekly_parlay": parlay,
        "skip_alerts": skip_alerts,
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
            tier: {"wins": v.get("wins"), "losses": v.get("losses"),
                   "pushes": v.get("pushes"), "hit_pct": v.get("hit_pct"),
                   "sample_n": v.get("sample_n")}
            for tier, v in cohorts.items()
        },
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
