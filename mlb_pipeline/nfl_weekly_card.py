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


def _fetch_jerry_game_picks(season, week):
    """Pull structured Jerry game-side picks (jerry_reads) for this NFL week.

    Empty today (NFL Jerry synthesis writes prose to jerry_cache, not
    structured to jerry_reads). Once generate_nfl_game_reads.py is
    upgraded to emit VERDICT/CONVICTION markers + write to jerry_reads,
    this function starts returning real picks and the weekly card gains
    a Jerry-driven primary source alongside the cohort audits.

    Filter mirrors MLB sweat card: BACK verdict, conv >= 70, not pass/lean.
    """
    try:
        # Use the week's approximate date window (Thu → Mon)
        today = today_et()
        window_start = today.isoformat()
        window_end = (today + timedelta(days=10)).isoformat()
        rows = sb_get("jerry_reads", {
            "sport": "eq.NFL",
            "game_date": f"gte.{window_start}",
            "call_market": "not.in.(pass,lean)",
            "select": "game_id,call_market,call_side,call_line,conviction,short_read",
            "order": "conviction.desc",
        })
        return [r for r in rows if (r.get("conviction") or 0) >= 70
                and r.get("game_date", "1970-01-01") <= window_end]
    except Exception as e:
        print(f"  ⚠ jerry_reads NFL fetch failed: {e}")
        return []


def _fetch_jerry_prop_picks(season, week):
    """Pull structured Jerry prop picks (prop_jerry_reads) for this NFL week.

    prop_jerry_reads already runs sport-universally through
    generate_prop_jerry_synthesis when NFL prop pipeline emits props.
    Once NFL props generate at scale (Week 1+), these picks populate
    the card's prop rail same as MLB.

    Filter: BACK verdict at conv >= 70 (matches MLB sweat card gate).
    Calibration flip signal in signals['_calibration_fade'] is respected
    downstream since we honor whatever direction Jerry stored (already
    calibration-flipped upstream).
    """
    try:
        today = today_et()
        window_start = today.isoformat()
        window_end = (today + timedelta(days=10)).isoformat()
        rows = sb_get("prop_jerry_reads", {
            "sport": "eq.NFL",
            "game_date": f"gte.{window_start}",
            "call_verdict": "eq.BACK",
            "select": "game_id,player_name,prop_type,direction,prop_line,"
                      "book_odds,conviction,short_read",
            "order": "conviction.desc",
        })
        return [r for r in rows if (r.get("conviction") or 0) >= 70
                and r.get("game_date", "1970-01-01") <= window_end]
    except Exception as e:
        print(f"  ⚠ prop_jerry_reads NFL fetch failed: {e}")
        return []


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


#: `mlb_tier_calibration.hit_rate` is a FRACTION (0.6364, 0.5, 1.0) --
#: verified against live rows 2026-11-07. Every gate in this file compares
#: against a PERCENT (`>= 60`, `< 55`, `100 - div_rate`) and
#: `conviction = int(dog_rate + 15)` only makes sense on a percent. The same
#: column name is a percent in `signal_registry`
#: ([[feedback_hit_rate_column_is_a_percent]]), so the unit is per table and
#: has to be converted explicitly, here, once.
#: Andy's standing rule: every % shows its n, and n>=30 gates a
#: publishable claim ([[feedback_sample_size_with_pct]]).
COHORT_MIN_N = 30

_TIER_CAL_RATE_IS_FRACTION = True

#: Window labels that actually exist on the table, widest sample first.
#: There is NO 'lifetime' label for any sport.
_TIER_CAL_WINDOWS = ("std", "30d", "7d")


def fetch_audit_cohorts():
    """Pull live NFL audit cohort rates from mlb_tier_calibration.

    == 2026-11-07 - THIS FUNCTION RETURNED NOTHING, ALWAYS =================
    Three independent faults, each sufficient on its own:

      1. It selected `wins,losses,pushes,hit_pct,sample_n`. The table has
         `hits,total,hit_rate` and NONE of those five columns. PostgREST
         answers 400/42703 and a missing column fails the WHOLE fetch
         ([[feedback_select_star_before_concluding_absent]]), so `rows` came
         back empty.
      2. It filtered `window_label=eq.lifetime`. The live labels are
         `std`, `30d` and `7d` -- 'lifetime' exists for no sport, so even
         with the right columns this returns zero rows.
      3. `_cohort_rate` upper-cased and stripped the `nfl_` prefix while the
         stored tiers are lowercase `nfl_outdoor_over`, so the lookup missed
         on both case and prefix.

    Consequence, measured on the live surface: every cohort rate resolved to
    `0` on `n=0` via `or 0`, so `lock_of_week`, `outdoor_under_angles`,
    `weekly_parlay` and `audit_cohort_summary` were all EMPTY in
    jerry_cache (their gates compare to 55/60/40/100/200), while
    `find_underdog_of_week` -- which did not gate on n -- published
    `TEN +10.0` tier STRONG for w9 and `BAL +8.5` tier STRONG for w8, each
    with the rationale "cohort hits 0% on 0 historical games". A publishable
    tier backed by a stated sample of zero.

    Returns {tier_key_lower: {rate_pct, n, window_label, computed_date}}.
    """
    rows = sb_get("mlb_tier_calibration", {
        "sport": "eq.nfl",
        "select": "tier,hits,total,hit_rate,window_label,computed_date",
        "order": "computed_date.desc",
        "limit": "500",
    })
    if not rows:
        print("  [cohorts] mlb_tier_calibration returned 0 nfl rows -- "
              "WIRING ERROR, not an empty history")
        return {}
    # newest computed_date first (the order above), then widest window
    rank = {w: i for i, w in enumerate(_TIER_CAL_WINDOWS)}
    best = {}
    for r in rows:
        key = str(r.get("tier") or "").strip().lower()
        if not key:
            continue
        n = r.get("total") or 0
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 0
        rate = r.get("hit_rate")
        if rate is None and n:
            try:
                rate = float(r.get("hits") or 0) / n
            except (TypeError, ValueError, ZeroDivisionError):
                rate = None
        if rate is None:
            continue
        rate = float(rate)
        if _TIER_CAL_RATE_IS_FRACTION and rate <= 1.0:
            rate *= 100.0
        cand = {"rate_pct": round(rate, 2), "n": n,
                "window_label": str(r.get("window_label") or ""),
                "computed_date": r.get("computed_date")}
        prev = best.get(key)
        if prev is None or rank.get(cand["window_label"], 9) < rank.get(
                prev["window_label"], 9):
            best[key] = cand
    print(f"  [cohorts] {len(best)} nfl cohort(s): "
          + ", ".join(f"{k}={v['rate_pct']}% n={v['n']}({v['window_label']})"
                      for k, v in sorted(best.items())[:8]))
    return best


def _cohort_rate(cohort_key: str, cohorts: dict) -> tuple:
    """Return (hit_pct, sample_n) for a cohort tag, or (None, 0) if unmeasured.

    2026-11-07: this upper-cased and stripped the `nfl_` prefix, but the
    stored tiers are lowercase AND carry the prefix (`nfl_outdoor_over`), so
    it missed on both counts. Match on the lowercase key, with and without
    the prefix, and return `None` rather than `0` for "no measurement" --
    `or 0` made an absent cohort indistinguishable from a cohort that hits
    0% ([[feedback_falsy_zero_invented_a_tier_and_a_confidence]]).
    """
    k = str(cohort_key or "").strip().lower()
    r = (cohorts.get(k)
         or cohorts.get(k.replace("nfl_", ""))
         or cohorts.get("nfl_" + k)
         or {})
    if not r:
        return (None, 0)
    return (r.get("rate_pct"), r.get("n") or 0)


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
    # == 2026-11-07 - THIS PUBLISHED A STRONG PICK ON A SAMPLE OF ZERO =====
    # Alone among the finders here this one had no n gate, so when the
    # cohort query was broken it still emitted a pick -- w9 `TEN +10.0` and
    # w8 `BAL +8.5`, both tier STRONG, both carrying the rationale "cohort
    # hits 0% on 0 historical games". The docstring's 65.4% on n=81 was a
    # Phase 1 audit figure that the live table does not contain.
    #
    # A tier is a stake instruction, so it needs a measured rate behind it
    # ([[feedback_sample_size_with_pct]], [[feedback_tier_demotion_needs_stake_boundary]]).
    # No measurement, no pick -- and say so rather than going quiet
    # ([[feedback_zero_rows_is_a_wiring_error]]).
    if rate is None:
        print("  [underdog] nfl_heavy_home_dog is UNMEASURED on "
              "mlb_tier_calibration -- no Underdog of the Week (will not "
              "publish a STRONG tier on a sample of zero)")
        return None
    if n < COHORT_MIN_N:
        print(f"  [underdog] nfl_heavy_home_dog n={n} < {COHORT_MIN_N} "
              f"({rate}%) -- no Underdog of the Week")
        return None
    return {
        "game_id": g["game_id"],
        "matchup": f"{g['away_team']} @ {g['home_team']}",
        "pick": f"{g['home_team']} +{abs(float(g['close_spread']))}",
        "tier": "STRONG",  # audit-validated PRIME until Phase 2 layered with player data
        "audited_rate": rate,
        "audited_n": n,
        "rationale": f"Home dog +{abs(float(g['close_spread']))} cohort hits {rate}% on {n} historical games. Classic letdown spot for road favorite.",
    }


def find_outdoor_under_angles(games, cohorts):
    """Outdoor totals trended UNDER at 52.1% in Phase 1 audit. When game is
    outdoors AND total ≥ 47 AND weather is moderate-to-bad (cold or windy),
    surface as a UNDER lean."""
    rate_under, n = _cohort_rate("nfl_outdoor_under", cohorts)
    # 2026-11-07: no gate here at all, so with the cohort query broken these
    # LEAN plays would have published "Outdoor totals UNDER cohort 0% on 0
    # games". The only reason jerry_cache showed none is that no game met the
    # weather flags -- absence of output was luck, not a guard.
    if rate_under is None:
        print("  [outdoor] nfl_outdoor_under is UNMEASURED -- no UNDER leans")
        return []
    if n < COHORT_MIN_N:
        print(f"  [outdoor] nfl_outdoor_under n={n} < {COHORT_MIN_N} "
              f"({rate_under}%) -- no UNDER leans")
        return []
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
    if underdog and dog_rate is not None and dog_rate >= 60 and dog_n >= 40:
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
        if under_rate is None or under_rate < 55 or under_n < 100: continue
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
    #
    # == 2026-11-07 - `away_edge >= 2` MEASURES AGAINST 50%, NOT BREAK-EVEN ==
    # This gate never ran while the cohort query was broken (div_rate was 0,
    # so `div_rate < 50` held but n was 0). With the query fixed the cohort
    # is real -- nfl_div_home_cover 47.52% on n=2,704 -- and the old gate
    # would pass instantly: away_edge = 52.48, against a threshold of 2.
    #
    # But 52.48% is the cover rate of the side we would BACK, and a spread
    # at -110 needs 52.38% just to break even. The real edge is +0.10pp,
    # not +2.48pp. Grading a fade against 50% instead of against the price
    # is the same error as quoting a moneyline record at flat -110
    # ([[feedback_flat_110_inflates_every_ml_figure]],
    # [[feedback_slope_is_not_a_betting_rule]]).
    #
    # So the gate is now stated in break-even terms, which is Andy's
    # documented standard rather than a new policy. Cost, said out loud:
    # at 52.48% this leg stays SUPPRESSED, so the parlay section remains
    # empty on the division angle until the cohort clears break-even by the
    # stated margin. That is a smaller card and a defensible one.
    BE_PCT_AT_MINUS_110 = 52.38
    EDGE_OVER_BE_PP = 1.0
    div_rate, div_n = _cohort_rate("nfl_div_home_cover", cohorts)
    away_cover = (100 - div_rate) if div_rate is not None else None
    if away_cover is not None and div_n >= 200:
        _margin = away_cover - BE_PCT_AT_MINUS_110
        if _margin < EDGE_OVER_BE_PP:
            print(f"  [parlay] div fade {away_cover:.2f}% on n={div_n} is "
                  f"{_margin:+.2f}pp vs -110 break-even "
                  f"({BE_PCT_AT_MINUS_110}%) -- under the "
                  f"{EDGE_OVER_BE_PP}pp requirement, leg SUPPRESSED")
    if (away_cover is not None
            and div_n >= 200
            and away_cover - BE_PCT_AT_MINUS_110 >= EDGE_OVER_BE_PP):
        for g in games:
            if g["game_id"] in used_games: continue
            if g.get("div_game") is not True: continue
            if g.get("close_spread") is None or float(g["close_spread"]) <= 0: continue
            # spread > 0 → home fav; away is the dog in a division game
            legs.append({
                "matchup": f"{g['away_team']} @ {g['home_team']}",
                "pick": f"{g['away_team']} +{g['close_spread']}",
                "cohort": "div_home_cover_fade",
                "audited_rate": away_cover,
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
    # 2026-11-07: this asserted "cover {div_rate}% ({div_n} games)" with no
    # gate, so a broken cohort query put "cover 0% (0 games)" into live copy
    # ([[feedback_comment_asserting_a_measurement]]). A skip alert is useful
    # advice without the number, so the number is dropped rather than the
    # alert -- but it is never invented.
    _div_measured = div_rate is not None and div_n >= COHORT_MIN_N
    if not _div_measured:
        print(f"  [skip] nfl_div_home_cover unmeasured or thin "
              f"(rate={div_rate} n={div_n}) -- alerts omit the cohort stat")
    for g in games:
        sp = g.get("close_spread")
        if sp is None: continue
        sp = float(sp)
        if 3.5 <= sp <= 6.5 and g.get("div_game") is True:
            _stat = (f"Division home favs cover {div_rate}% ({div_n} games). "
                     if _div_measured else "")
            alerts.append({
                "game_id": g["game_id"],
                "matchup": f"{g['away_team']} @ {g['home_team']}",
                "reason": "chalk_div_home_fav",
                "message": (f"Skip {g['home_team']} -{sp} chalk. {_stat}"
                            f"Coinflip trap."),
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

    # == 2026-11-07 - A WEEK CARD WAS PICKING FROM TWO WEEKS ==============
    # The fetch above is a 10-day horizon, which on a Saturday holds the
    # current week AND the next one: 28 games today for a 16-game week. Every
    # finder received that unfiltered list, so `find_underdog_of_week` chose
    # the biggest home dog in TEN DAYS rather than in the week the card is
    # keyed to. Measured on the card written at 22:40Z: cache_key
    # `nfl_weekly_card_2026_w9` carried `TEN +10.0`, and JAX @ TEN is
    # 2026-11-15, **week 10** -- eight days out, on a line that will move
    # several times before kickoff. `slate` had the same defect by a
    # different route: `games[:16]` is an arbitrary slice of the horizon, not
    # a week.
    #
    # The functions are all named _of_week and the cache key is per week, so
    # the week is the scope. Filter once, here.
    week_games = [g for g in games
                  if g.get("week") == week and g.get("season") == season]
    if not week_games:
        print(f"  [week] no games carry season={season} week={week} -- "
              f"falling back to the full horizon, and the card's week label "
              f"cannot be trusted (WIRING ERROR, check nfl_game_context.week)")
        week_games = games
    else:
        print(f"  [week] scoped to season {season} week {week}: "
              f"{len(week_games)} of {len(games)} games in horizon")

    underdog = find_underdog_of_week(week_games, cohorts)
    outdoor_unders = find_outdoor_under_angles(week_games, cohorts)
    lock = find_lock_of_week(week_games, cohorts, underdog)
    parlay = find_weekly_parlay(week_games, cohorts, lock, underdog, outdoor_unders)
    skip_alerts = find_skip_alerts(week_games, cohorts)

    # 2026-08-06 refactor prep: pull Jerry-driven picks (jerry_reads sport=NFL
    # BACK conv>=70 + prop_jerry_reads sport=NFL) to sit alongside cohort
    # picks. When empty (offseason, or before Jerry NFL structured output
    # ships), cohort picks stay primary. Once populated (Week 1+, once
    # generate_nfl_game_reads.py starts emitting VERDICT/CONVICTION), these
    # become the same primary-source pattern MLB uses.
    #
    # Cohort audits (heavy_home_dog, outdoor_under, div_home_cover_fade)
    # remain valuable — they become inputs Jerry considers, not competing
    # picks, once Jerry NFL synthesis is fully wired. This just adds the
    # pulling infrastructure now so the card structure is ready.
    jerry_game_picks = _fetch_jerry_game_picks(season, week)
    jerry_prop_picks = _fetch_jerry_prop_picks(season, week)
    if jerry_game_picks or jerry_prop_picks:
        print(f"  Jerry-driven picks: {len(jerry_game_picks)} games, "
              f"{len(jerry_prop_picks)} props")

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
            for g in week_games
        ],
        # 2026-11-07: these read wins/losses/pushes/hit_pct/sample_n, which
        # are the column names the broken query asked for and which
        # mlb_tier_calibration does not have. With cohorts now keyed
        # {rate_pct, n, window_label, computed_date} the old shape would
        # publish five nulls per cohort, so it is restated. `n` travels with
        # every percentage ([[feedback_sample_size_with_pct]]) and so does
        # the window it was measured over, because `std`, `30d` and `7d` are
        # not the same claim.
        "audit_cohort_summary": {
            tier: {"hit_pct": v.get("rate_pct"),
                   "sample_n": v.get("n"),
                   "window_label": v.get("window_label"),
                   "computed_date": v.get("computed_date"),
                   "meets_min_n": bool((v.get("n") or 0) >= COHORT_MIN_N)}
            for tier, v in cohorts.items()
        },
        # 2026-08-06: Jerry-driven picks surfaced alongside cohort picks.
        # Empty during offseason and until generate_nfl_game_reads.py emits
        # structured VERDICT/CONVICTION to jerry_reads. Once populated,
        # UI treats these as the primary card fill and cohort picks become
        # supplemental / diagnostic.
        "jerry_game_picks": jerry_game_picks,
        "jerry_prop_picks": jerry_prop_picks,
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
