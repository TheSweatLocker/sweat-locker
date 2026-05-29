"""Tonight's Sweat Card — server-generated structured card for the app.

Pulls together the highest-conviction plays across all signal types (POTD
NRFI lock, top hits + K props with audited tier rates, Dawg of the Day,
bucket angle from inning splits, skip alerts for volatile NRFI 95+ tier)
and stores as a structured JSON row in jerry_cache. App reads via the
get_todays_sweat_card RPC and renders at the top of the MLB tab.

This replaces the user's manual content-card workflow — same picks they'd
write up by hand, generated automatically from pipeline output. Refreshes
on every cron run so lineup confirmations / ump landings / NRFI re-derives
flow through to the card.

Run after generate_props.py + play_of_day.py + generate_dawg_of_day.py
in the workflow so the card has fresh upstream data.

Usage: python generate_sweat_card.py
"""
import os
import json
import sys
from datetime import datetime, timedelta, timezone
import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def sb_get(path, params=None):
    qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = f"{SUPABASE_URL}/rest/v1/{path}{'?' if qs else ''}{qs}"
    r = requests.get(url, headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}, timeout=15)
    return r.json() if r.status_code == 200 else []


def fetch_tier_rates():
    """Pull live audited tier rates from mlb_tier_calibration (30d window).

    2026-05-22 fix: previously this query hit PostgREST's 1000-row default
    limit (1,337 active 30d rows across all cohorts × dates), randomly
    truncating cohorts like yrfi_lean_le40 from the result. The sweat card
    then shipped `secondary_lock.audited_rate=0, audited_n=null` because
    the dict lookup returned None, surfacing as "0% audited (n=)" in the
    YRFI LEAN card section.

    Fix: filter to TODAY's computed_date (audit_tier_calibration.py runs
    daily and writes one row per cohort per window per date — so today's
    rows are exactly N cohorts × 3 windows = well under any limit).
    """
    today = today_et()
    rows = sb_get("mlb_tier_calibration", {
        "window_label": "eq.30d",
        "sport": "eq.mlb",
        "computed_date": f"eq.{today}",
        "select": "tier,hits,total,hit_rate",
    })
    # Fallback to most-recent rows if today's calibration hasn't run yet
    # (e.g. cron hasn't fired). Order by computed_date desc + dedupe by tier.
    if not rows:
        rows = sb_get("mlb_tier_calibration", {
            "window_label": "eq.30d",
            "sport": "eq.mlb",
            "select": "tier,hits,total,hit_rate,computed_date",
            "order": "computed_date.desc",
            "limit": "500",  # bounded so we don't hit the pagination wall again
        })
    seen = {}
    for r in rows:
        # First row wins (already sorted desc by date when in fallback path;
        # in primary path all rows share the same date so order doesn't matter)
        seen.setdefault(r["tier"], r)
    return seen


def count_mlb_games_today():
    """Count MLB games on today's slate via mlb_game_context."""
    today = today_et()
    rows = sb_get("mlb_game_context", {"game_date": f"eq.{today}", "select": "game_id"})
    return len(rows)


def count_nba_games_today():
    """Count NBA games today via nba_game_results (unresolved = today's slate)."""
    today = today_et()
    rows = sb_get("nba_game_results", {
        "game_date": f"eq.{today}",
        "home_score": "is.null",
        "select": "game_id",
    })
    return len(rows)


def count_ufc_events_within(days=3):
    """Count UFC events with cards within the next `days` days."""
    today = today_et()
    horizon = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
    rows = sb_get("ufc_picks", {
        "event_date": f"gte.{today}",
        "select": "event_date",
        "order": "event_date.asc",
        "limit": "5",
    })
    # Filter horizon client-side since sb_get's dict-based query string
    # collapses duplicate keys.
    within = [r for r in rows if r.get("event_date") and r["event_date"] <= horizon]
    return 1 if within else 0


def compute_slate_density():
    """Determine which content branch the Sweat Card should render.

    Returns a dict with:
      mode: 'empty' | 'thin' | 'standard' | 'overload'
      active_sports: list[str]
      total_games: int
      counts: dict[sport, int]

    The mode drives content padding decisions in build_card():
      - empty: no slate at all → audit roll-up + next event preview
      - thin: 1 sport active, ≤8 games → existing content + audit padding
      - standard: 2-3 sports OR 9-24 games → current behavior, no padding needed
      - overload: 4+ sports OR 25+ games → cap N picks per sport, sport-filter UI hint

    NCAAB, NFL, NCAAF, NHL not yet wired — added when those pipelines ship.
    """
    counts = {
        "MLB": count_mlb_games_today(),
        "NBA": count_nba_games_today(),
    }
    ufc_pending = count_ufc_events_within(days=3)
    active = [s for s, n in counts.items() if n > 0]
    if ufc_pending:
        active.append("UFC")
    total = sum(counts.values())

    if total == 0 and not ufc_pending:
        mode = "empty"
    elif len(active) <= 1 and total <= 8:
        mode = "thin"
    elif len(active) >= 4 or total >= 25:
        mode = "overload"
    else:
        mode = "standard"

    return {
        "mode": mode,
        "active_sports": active,
        "total_games": total,
        "counts": counts,
    }


def fetch_audit_roll_up():
    """Pull a roll-up of the most-bettable audited cohorts across windows.

    Returns the cohort summaries the app can lead with on thin/empty days.
    Filters to cohorts with n >= 10 (enough sample to be meaningful)."""
    rows = sb_get("mlb_tier_calibration", {
        "window_label": "in.(7d,30d,std)",
        "sport": "eq.mlb",
        "tier": "in.(nrfi_prime_90_94,yrfi_lean_le40,confluence_prime_ge4,autofade_dog_high_conv,total_extreme_under_ge3)",
        "select": "tier,window_label,hits,total,hit_rate",
    })
    by_tier = {}
    for r in rows:
        if (r.get("total") or 0) < 10:
            continue
        by_tier.setdefault(r["tier"], {})[r["window_label"]] = {
            "hits": r["hits"],
            "total": r["total"],
            "hit_rate": r["hit_rate"],
        }
    return by_tier


def fetch_yesterday_recap():
    """Yesterday's POTD + Dawg + full top_8 results for the Receipts tab.

    2026-05-24 extended to include the full top_8 grade (was POTD+DotD
    only). Receipts tab now shows the actual W/L breakdown not just the
    two anchor picks. Pulls from yesterday's sweat_card cache which the
    resolver writes top_8 results into nightly."""
    today = today_et()
    yesterday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    recap = {"date": yesterday}

    # Dawg result (daily_dawg keeps result_status after resolve)
    dawg_rows = sb_get("daily_dawg", {
        "game_date": f"eq.{yesterday}",
        "select": "team,matchup,result_status,tier",
    })
    if dawg_rows:
        recap["dawg"] = dawg_rows[0]

    # POTD result via best_bet cache row
    potd_rows = sb_get("jerry_cache", {
        "game_id": f"eq.best_bet_{yesterday}",
        "select": "data,narrative",
    })
    if potd_rows:
        d = potd_rows[0].get("data") or {}
        recap["potd"] = {
            "matchup": d.get("matchup") or d.get("game"),
            "pick": d.get("pick") or d.get("recommended"),
            "result": d.get("result_status") or d.get("result"),
        }

    # Full top_8 graded list — populated by resolve_game_results.py nightly.
    # Lets the app show the full receipts unit (W/L per pick) not just
    # the two anchor picks (POTD + DotD).
    y_card_rows = sb_get("jerry_cache", {
        "cache_key": f"eq.sweat_card_{yesterday}",
        "select": "data",
    })
    if y_card_rows:
        y_data = y_card_rows[0].get("data") or {}
        if isinstance(y_data.get("top_8"), list) and y_data["top_8"]:
            recap["top_8"] = [
                {
                    "rank": p.get("rank"),
                    "tier": p.get("tier"),
                    "label": p.get("label"),
                    "result": p.get("result"),
                    "game": p.get("game"),
                }
                for p in y_data["top_8"]
            ]
            # Backfill POTD + DotD results from the top_8 entries. The
            # best_bet cache row doesn't get its result field populated
            # by the resolver (the resolver writes per-pick into top_8),
            # so the standalone POTD/DotD lines on the recap previously
            # showed without a W/L. 2026-05-24: pull from top_8 by type.
            potd_pk = next((p for p in y_data["top_8"] if p.get("type") == "POTD"), None)
            if potd_pk and recap.get("potd"):
                recap["potd"]["result"] = potd_pk.get("result")
            dawg_pk = next((p for p in y_data["top_8"] if p.get("type") == "DotD"), None)
            if dawg_pk and recap.get("dawg"):
                recap["dawg"]["result_status"] = dawg_pk.get("result")
        if y_data.get("top_8_summary"):
            recap["top_8_summary"] = y_data["top_8_summary"]

    return recap


def fetch_upcoming_events():
    """Next ~5 days of high-signal events the app can preview on quiet days.

    Currently UFC card + tomorrow's MLB pitcher matchups. Extends naturally
    as NFL / NCAAB / NHL pipelines come online — each gets its own probe.

    2026-05-24: tightened UFC window 7d → 5d. Earlier filter just took the
    next 12 UFC fights with no upper date bound, so a fight 7 days out
    showed as "upcoming" right next to today's MLB card. 5d catches the
    typical UFC Sat card from Tuesday onward without surfacing fights
    that are not user-relevant for several days."""
    today = today_et()
    UFC_DAYS_AHEAD = 5
    ufc_horizon = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=UFC_DAYS_AHEAD)).strftime("%Y-%m-%d")
    events = []

    # Upcoming UFC card — bounded window so a fight 7+ days out doesn't
    # render on today's card.
    ufc_rows = sb_get("ufc_picks", {
        "event_date": f"gte.{today}",
        "select": "event_name,event_date,fight_order,fighter_a,fighter_b,tier_winner,recommended_side",
        "order": "event_date.asc,fight_order.asc",
        "limit": "12",
    })
    # Trim to within UFC_DAYS_AHEAD — Supabase only supports a single
    # filter per column without and/or chaining via .or; cleaner to slice
    # client-side post-fetch.
    if ufc_rows:
        ufc_rows = [r for r in ufc_rows if (r.get("event_date") or "") <= ufc_horizon]
    if ufc_rows:
        events.append({
            "type": "ufc_card",
            "event_name": ufc_rows[0]["event_name"],
            "event_date": ufc_rows[0]["event_date"],
            "fight_count": len(ufc_rows),
            "prime_picks": [r for r in ufc_rows if r.get("tier_winner") == "PRIME"],
        })

    # Tomorrow's MLB pitcher matchups (preview during slate gaps)
    tomorrow = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    mlb_tomorrow = sb_get("mlb_game_context", {
        "game_date": f"eq.{tomorrow}",
        "select": "home_team,away_team,home_pitcher,away_pitcher,home_sp_xera,away_sp_xera",
        "limit": "20",
    })
    if mlb_tomorrow:
        events.append({
            "type": "mlb_preview",
            "date": tomorrow,
            "game_count": len(mlb_tomorrow),
            "matchups": [
                {
                    "game": f"{g.get('away_team')} @ {g.get('home_team')}",
                    "home_sp": g.get("home_pitcher"),
                    "away_sp": g.get("away_pitcher"),
                    "home_xera": g.get("home_sp_xera"),
                    "away_xera": g.get("away_sp_xera"),
                }
                for g in mlb_tomorrow[:5]
            ],
        })

    return events


def fetch_potd():
    today = today_et()
    rows = sb_get("jerry_cache", {"game_id": f"eq.best_bet_{today}", "select": "data,narrative"})
    return rows[0] if rows else None


def fetch_dawg():
    today = today_et()
    rows = sb_get("daily_dawg", {"game_date": f"eq.{today}", "select": "*"})
    return rows[0] if rows else None


def fetch_top_props():
    today = today_et()
    rows = sb_get(
        "mlb_pipeline_props",
        {
            "game_date": f"eq.{today}",
            "select": "player_name,player_team,prop_type,prop_line,direction,tier,conviction,signals,matchup",
            "order": "conviction.desc",
            "limit": "20",
        },
    )
    return rows


def fetch_game_context():
    today = today_et()
    return sb_get("mlb_game_context", {"game_date": f"eq.{today}", "select": "*"})


def find_bucket_angle(games):
    """Identify the strongest bucket-bet angle across the slate.
    Looks for: starter with very bad innings 4-6 ERA + offense with strong
    4-6 R/G + opposing bullpen rested. Returns dict or None."""
    best = None
    best_score = 0
    for g in games:
        # Need pitcher names + recent splits
        for side in ("home", "away"):
            opp = "away" if side == "home" else "home"
            try:
                pitcher = g.get(f"{side}_pitcher")
                if not pitcher:
                    continue
                # Look for late-bucket starter weakness driving an over angle
                # Pull pitcher inning_4_6 ERA
                pr = sb_get("mlb_pitcher_stats", {
                    "player_name": f"eq.{pitcher}",
                    "select": "innings_4_6_era,innings_4_6_ip",
                    "limit": "1",
                })
                if not pr:
                    continue
                era_46 = pr[0].get("innings_4_6_era")
                ip_46 = pr[0].get("innings_4_6_ip") or 0
                if era_46 is None or ip_46 < 8:
                    continue
                if float(era_46) < 6.0:
                    continue
                # Opponent's bullpen workload
                opp_team = g.get(f"{opp}_team")
                opp_pen_relievers = g.get(f"{opp}_bp_relievers_3d") or 0
                # Score the angle
                score = float(era_46) * 5
                if opp_pen_relievers <= 7:
                    score += 10  # opposing pen rested = late innings carry
                if score > best_score:
                    best_score = score
                    best = {
                        "game": f"{g.get('away_team')} @ {g.get('home_team')}",
                        "type": "innings_4_6_over",
                        "headline": f"{opp_team} team total OVER innings 4-6",
                        "reason": f"{pitcher} 4-6 ERA {era_46} over {ip_46} IP — bucket disaster",
                        "extra": f"Opp pen rested ({opp_pen_relievers} relievers used last 3d)" if opp_pen_relievers <= 7 else None,
                    }
            except Exception:
                continue
    return best


def find_nrfi_lock(games, tier_rates):
    """Find PRIME tier (90-94) NRFI game. Returns dict or None."""
    candidates = [g for g in games if 90 <= (g.get("nrfi_score") or 0) <= 94]
    if not candidates:
        return None
    candidates.sort(key=lambda g: -(g.get("nrfi_score") or 0))
    g = candidates[0]
    rate_row = tier_rates.get("nrfi_prime_90_94", {})
    return {
        "game": f"{g.get('away_team')} @ {g.get('home_team')}",
        "score": g.get("nrfi_score"),
        "tier": "PRIME",
        "audited_rate": round((rate_row.get("hit_rate") or 0) * 100, 1),
        "audited_n": rate_row.get("total"),
        "context": {
            "home_pitcher": g.get("home_pitcher"),
            "away_pitcher": g.get("away_pitcher"),
            "home_first_inn_era": g.get("home_first_inning_era"),
            "away_first_inn_era": g.get("away_first_inning_era"),
        },
    }


def find_yrfi_lock(games, tier_rates):
    """Find YRFI lean (≤40 score) games — also a 70%+ tier."""
    candidates = [g for g in games if (g.get("nrfi_score") or 100) <= 40]
    if not candidates:
        return None
    candidates.sort(key=lambda g: g.get("nrfi_score") or 100)  # lowest = strongest YRFI
    g = candidates[0]
    rate_row = tier_rates.get("yrfi_lean_le40", {})
    return {
        "game": f"{g.get('away_team')} @ {g.get('home_team')}",
        "score": g.get("nrfi_score"),
        "tier": "YRFI",
        "audited_rate": round((rate_row.get("hit_rate") or 0) * 100, 1),
        "audited_n": rate_row.get("total"),
    }


_V4_OVER_SUPPRESSED_CARD_CACHE = None


def _is_v4_over_suppressed_card():
    """Reads model_health.over_suppressed (auto-flipped nightly by
    audit_v4_health.py). Cached per-run. Defaults to True (safe) if
    table unreadable."""
    global _V4_OVER_SUPPRESSED_CARD_CACHE
    if _V4_OVER_SUPPRESSED_CARD_CACHE is not None:
        return _V4_OVER_SUPPRESSED_CARD_CACHE
    try:
        rows = sb_get("model_health", {
            "model_version": "eq.v4",
            "order": "computed_date.desc",
            "limit": "1",
            "select": "over_suppressed",
        }) or []
        if rows and rows[0].get("over_suppressed") is not None:
            _V4_OVER_SUPPRESSED_CARD_CACHE = bool(rows[0]["over_suppressed"])
            return _V4_OVER_SUPPRESSED_CARD_CACHE
    except Exception:
        pass
    _V4_OVER_SUPPRESSED_CARD_CACHE = True
    return True


def find_total_edges(games, min_delta=1.5):
    """Find games where the model projects a total meaningfully different
    from the market line. |projected_total - close_total| >= min_delta.
    Returns top 2 by absolute delta. No calibrated cohort yet — these go
    in the Sweat Card with a neutral 60% prior.

    Direction follows model: model > market => OVER lean, < market => UNDER.

    2026-05-24: v4 OVER picks audit 43.2% (30d) / 40.9% (7d) / 25% (3d) per
    audit_v4_totals — model has calibration drift toward over-projecting
    runs in May. UNDER picks audit 55% (30d, healthy). Suppression now
    AUTO-THROTTLED via model_health.over_suppressed flag (flipped nightly
    by audit_v4_health.py with hysteresis).
    """
    OVER_SUPPRESSED = _is_v4_over_suppressed_card()
    candidates = []
    for g in games:
        pt = g.get("projected_total")
        ct = g.get("close_total")
        if pt is None or ct is None:
            continue
        try:
            delta = float(pt) - float(ct)
        except (TypeError, ValueError):
            continue
        if abs(delta) < min_delta:
            continue
        direction = "OVER" if delta > 0 else "UNDER"
        if direction == "OVER" and OVER_SUPPRESSED:
            continue  # suppressed per audit_v4_totals
        candidates.append({
            "game": f"{g.get('away_team')} @ {g.get('home_team')}",
            "projected_total": round(float(pt), 1),
            "close_total": round(float(ct), 1),
            "delta": round(delta, 2),
            "direction": direction,
        })
    candidates.sort(key=lambda c: -abs(c["delta"]))
    return candidates[:2]


def collect_skip_alerts(games):
    """Surface games in skip-tier (NRFI 95+) so card can warn against them."""
    volatile = [g for g in games if (g.get("nrfi_score") or 0) >= 95]
    return [
        {"game": f"{g.get('away_team')} @ {g.get('home_team')}", "nrfi_score": g.get("nrfi_score")}
        for g in volatile
    ]


def top_props_by_type(props, target_type, n=2):
    filtered = [p for p in props if p.get("prop_type") == target_type]
    return filtered[:n]


def _is_high_juice_hits_over(prop):
    """Heuristic: hits_over 0.5 lines are nearly always priced -250 to -400
    because 70-80% of MLB starters get a hit. At -300+ juice, a 75-77% hit
    rate is barely break-even; at -350+ it's -EV. We don't pull prop prices
    yet, so use the line-based heuristic — hits_over @ 0.5 = high juice.
    Higher hits_over lines (1.5, 2.5) are +money and unaffected.

    Why: 30d audit (2026-05-23) showed hits_over PRIME at 77.1% (n=96), which
    sounds great but at -350 juice you need 77.8% to break even — the apparent
    edge evaporates once you factor in price. Card-surface gate: require
    conviction ≥ 95 to publish a PRIME hits_over @ 0.5 (vs 82 elsewhere).
    """
    if prop.get('prop_type') != 'hits_over':
        return False
    try:
        return float(prop.get('prop_line') or 0) <= 0.5
    except (TypeError, ValueError):
        return False


# 30d audit (2026-05-23): outs_OVER STRONG is 0-3 lifetime — three pieces of
# board ammo for anyone screenshotting the card. Hard skip from public
# surfacing. Internal scoring still surfaces it for personal-use review.
#
# Note: This is now a FALLBACK / override on top of the data-driven gate
# below — the calibration-driven path auto-suppresses any cohort that
# falls below break-even. This set stays for cases where we want
# editorial control regardless of recent hit rate (e.g. known broken
# cohorts that haven't accumulated enough sample to auto-skip yet).
PROP_TYPES_SUPPRESSED_FROM_CARD = {'outs_over'}

# Conviction floor for hits_over 0.5 when promoting to a public card slot.
# See _is_high_juice_hits_over docstring.
HITS_OVER_05_CARD_CONV_FLOOR = 95

# Per-prop-type break-even hit rates at typical juice. Used by the
# data-driven auto-suppression gate (2026-05-23). Values come from the
# typical-juice context cited in the 30d prop audit:
#   hits_over @ 0.5  -> -300 typical → 75%
#   hits_over @ 1.5+ -> +130 typical → ~43.5% (use 50% safety margin)
#   hits_under @ 0.5 -> -120 typical → 54.5%
#   ks_over/under    -> -110 typical → 52.4%
#   ha_under         -> -120 → 54.5%
#   ha_over          -> +110 → 47.6%
#   bb_under         -> -120 → 54.5%
#   bb_over          -> +110 → 47.6%
#   er_over          -> -130 typical → 56.5%
#   er_under         -> +110 → 47.6%
#   outs_under       -> -120 → 54.5%
#   outs_over        -> +100 typical (often +money) → 50%
#
# Cohorts hitting BELOW these thresholds on the 30d window are
# auto-suppressed from the card unless 60d also clears. See
# _cohort_eligibility below.
COHORT_BREAK_EVEN_PCT = {
    "hits_over":   {"high_juice": 75.0, "low_juice": 50.0},  # split by line in code
    # 2026-05-23 audit: hits_under typical juice is closer to -130 than -110
    # (sportsbook prices the under at less juice but it's still juiced when
    # the model agrees with the book). Raised from 54.5% → 56.5% to catch
    # the PRIME hits_under cohort at 55.7% — which barely clears -110 but
    # loses money at typical -130 prices. Drives auto-suppression to do
    # what the manual eye was already seeing in the integrity audit.
    "hits_under":  56.5,
    "ks_over":     52.4,
    "ks_under":    52.4,
    "ha_under":    54.5,
    "ha_over":     47.6,
    "bb_under":    54.5,
    "bb_over":     47.6,
    "er_over":     56.5,
    "er_under":    47.6,
    "outs_under":  54.5,
    "outs_over":   50.0,
}

# Minimum graded sample per cohort before we trust the rate enough to
# auto-suppress. Below this, default to "eligible" (insufficient data
# is not evidence of failure).
COHORT_MIN_N = 10

# Card must always fill to at least this many picks. If suppression
# drops the count below this, the gate auto-loosens to 60d-only
# eligibility, then 90d, then no-gate.
MIN_CARD_PICKS = 5


def _break_even_for(prop):
    """Return the break-even hit_rate (0..100) for a given prop.
    Splits hits_over by line since 0.5 is heavily juiced vs 1.5+ which
    is typically plus-money."""
    pt = prop.get("prop_type")
    if pt == "hits_over":
        try:
            line = float(prop.get("prop_line") or 0)
            return COHORT_BREAK_EVEN_PCT["hits_over"]["high_juice"] if line <= 0.5 \
                else COHORT_BREAK_EVEN_PCT["hits_over"]["low_juice"]
        except (TypeError, ValueError):
            return 75.0
    return COHORT_BREAK_EVEN_PCT.get(pt)


def _fetch_cohort_rates(days_back):
    """Pull (prop_type, direction, tier) hit rates over the last N days.
    Returns dict keyed by (prop_type, direction, tier) -> (rate_pct, n).

    Computes on the fly from mlb_pipeline_props (rather than reading
    mlb_tier_calibration) because the calibration table doesn't yet
    have this granular slice. Backfill writer queued separately.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    rows = sb_get("mlb_pipeline_props", {
        "game_date": f"gte.{cutoff}",
        "result": "not.is.null",
        "select": "prop_type,direction,tier,result",
        "limit": "5000",
    }) or []
    from collections import defaultdict
    agg = defaultdict(lambda: {"W": 0, "L": 0, "P": 0})
    for r in rows:
        pt = r.get("prop_type") or "?"
        d = (r.get("direction") or "").lower() or "?"
        t = (r.get("tier") or "").upper() or "(none)"
        res = (r.get("result") or "").upper()
        if res == "WIN": agg[(pt, d, t)]["W"] += 1
        elif res == "LOSS": agg[(pt, d, t)]["L"] += 1
        elif res == "PUSH": agg[(pt, d, t)]["P"] += 1
    out = {}
    for key, s in agg.items():
        n = s["W"] + s["L"]  # pushes don't count
        out[key] = ((s["W"] / n * 100.0) if n > 0 else None, n)
    return out


# Module-level cache so we don't re-query for every prop. Reset per build.
_COHORT_RATES_30D = None
_COHORT_RATES_60D = None
_COHORT_RATES_90D = None


def _cohort_eligibility(prop, gate_window="30d"):
    """Return one of: 'eligible', 'demote', 'suppress'.

    Rules:
      - 30d hit_rate >= break_even (n>=10) -> 'eligible'
      - 30d below but 60d >= break_even (n>=10) -> 'demote' (drop tier one level)
      - 60d also below (n>=10) -> 'suppress'
      - Insufficient sample anywhere -> 'eligible' (no data is not failure)

    `gate_window` lets the caller loosen the gate when the card has
    been over-suppressed (graceful-degradation fallback).
    """
    global _COHORT_RATES_30D, _COHORT_RATES_60D, _COHORT_RATES_90D
    if _COHORT_RATES_30D is None:
        _COHORT_RATES_30D = _fetch_cohort_rates(30)

    pt = prop.get("prop_type")
    direction = (prop.get("direction") or "").lower()
    tier = (prop.get("tier") or "").upper()
    break_even = _break_even_for(prop)
    if break_even is None:
        return "eligible"  # unknown prop type — don't suppress

    def _check(rates):
        r = rates.get((pt, direction, tier))
        if r is None: return None
        rate, n = r
        if rate is None or n < COHORT_MIN_N:
            return None  # insufficient sample
        return rate >= break_even

    pass_30 = _check(_COHORT_RATES_30D)

    if gate_window == "30d":
        if pass_30 is True: return "eligible"
        if pass_30 is None: return "eligible"  # sample too small to suppress
        # 30d says fail — check 60d
        if _COHORT_RATES_60D is None:
            _COHORT_RATES_60D = _fetch_cohort_rates(60)
        pass_60 = _check(_COHORT_RATES_60D)
        if pass_60 is True: return "demote"
        if pass_60 is None: return "eligible"  # 60d sample too small either
        return "suppress"

    # gate_window in {"60d", "90d", "off"} — used by degradation fallback
    if gate_window == "60d":
        if _COHORT_RATES_60D is None:
            _COHORT_RATES_60D = _fetch_cohort_rates(60)
        pass_60 = _check(_COHORT_RATES_60D)
        if pass_60 is False: return "suppress"
        return "eligible"
    if gate_window == "90d":
        if _COHORT_RATES_90D is None:
            _COHORT_RATES_90D = _fetch_cohort_rates(90)
        pass_90 = _check(_COHORT_RATES_90D)
        if pass_90 is False: return "suppress"
        return "eligible"
    return "eligible"  # "off"


_DEMOTE_MAP = {"PRIME": "STRONG", "STRONG": "LEAN", "LEAN": "LEAN"}


def curate_top_8(games, props, potd, dawg, total_edges, gate_window="30d"):
    """Pick the 8 highest-conviction plays for tonight's social card.

    The 8 set IS the receipts unit. Each pick records source_table +
    source_key so the resolver can walk it later and mark Win/Loss.
    Returns ordered list of 8 dicts ready for the sweat_card payload.

    Curation order (priority high -> low, dedupe so same game doesn't
    appear twice unless props are different players):
      1. POTD (always include if present, unless noPlay)
      2. DotD (always include if present)
      3. PRIME confluence ML primary plays (sweat_tier == PRIME)
      4. v4 total edges >= 1.5 OR STRONG total leans from primary_play
      5. CATEGORY LOCKS (2026-05-23): if a ks_over PRIME or outs_under
         STRONG candidate exists, reserve a slot for it. Both cohorts
         crush their juice (ks_over PRIME 76.5%, outs_under STRONG 93.3%
         30d) but get crowded out by the higher-volume hits_over slot.
      6. PRIME mastery props (highest conviction, diversified by player)
      7. STRONG mastery props as fill

    Auto-suppression (2026-05-23): every prop's (prop_type, direction,
    tier) cohort is checked against its break-even hit rate on the
    `gate_window`. Failing-30d-but-passing-60d cohorts are demoted one
    tier (PRIME->STRONG, STRONG->LEAN). Failing both are suppressed.
    `build_card` orchestrates the fallback if suppression drops the
    card below MIN_CARD_PICKS (loosens to 60d, then 90d, then off).

    Editorial gates (kept as final-pass overrides):
      - outs_over hard-skipped (legacy ban; the calibration-driven
        gate would also catch it on current data)
      - hits_over @ 0.5 requires conviction >= 95 (price-aware floor)
    """
    # Pre-filter: drop the always-skip cohorts before any logic touches them
    props = [
        p for p in (props or [])
        if p.get('prop_type') not in PROP_TYPES_SUPPRESSED_FROM_CARD
    ]

    # Auto-suppression: annotate each prop with eligibility under the
    # current gate, drop the suppressed ones, demote the rest in place.
    annotated_props = []
    for p in props:
        eligibility = _cohort_eligibility(p, gate_window=gate_window)
        if eligibility == "suppress":
            continue
        if eligibility == "demote":
            # Modify a copy so we don't mutate caller's data
            p = dict(p)
            original_tier = (p.get("tier") or "").upper()
            new_tier = _DEMOTE_MAP.get(original_tier, original_tier)
            if new_tier != original_tier:
                p["tier"] = new_tier
                p["_demoted_from"] = original_tier  # audit trail
        annotated_props.append(p)
    props = annotated_props

    picks = []
    seen_keys = set()  # dedupe ("type:identifier")
    game_pick_count = {}  # 2026-05-24 — track per-game pick concentration

    # Max picks per single MLB game on the public card.
    # Yesterday's 5/23 card stacked 3 picks on WSH/ATL (POTD Over 8.5, ATL
    # ML PRIME, Irvin Outs Under STRONG). When ATL won 2-0, all three lost
    # in the same result — single-game variance wiped 3/8 of the card. Cap
    # at 2 picks per game. POTD + DotD are exempt (those are by-definition
    # the day's anchor selections regardless of game).
    MAX_PICKS_PER_GAME = 2

    def _game_key(pick):
        """Normalize a pick to its underlying game key for concentration
        counting. Returns the matchup string or None when we can't infer one."""
        g = pick.get("game")
        if g and isinstance(g, str):
            return g
        return None

    def add(pick):
        # Dedupe by a stable identifier
        key = f"{pick['source_table']}:{pick['source_key']}"
        if key in seen_keys:
            return False
        # Concentration cap — block 3rd+ pick on a single game.
        # POTD + DotD are by-design daily anchors so they bypass the BLOCK,
        # but their game still increments the counter so subsequent picks
        # on the same game are gated. Effect: POTD on WSH/ATL → 1 more
        # WSH/ATL pick allowed (game-side OR prop). Yesterday's POTD +
        # ATL ML + Irvin Outs stack would have been capped at 2.
        gkey = _game_key(pick)
        ptype = pick.get("type", "")
        is_anchor = ptype in ("POTD", "DotD")
        if gkey is not None and not is_anchor:
            if game_pick_count.get(gkey, 0) >= MAX_PICKS_PER_GAME:
                return False
        seen_keys.add(key)
        if gkey is not None:
            game_pick_count[gkey] = game_pick_count.get(gkey, 0) + 1
        pick["rank"] = len(picks) + 1
        pick["result"] = "Pending"
        picks.append(pick)
        return True

    # 1. POTD (highest priority — included only when we have a real play)
    # Skip when POTD wrote a noPlay marker (no audit-qualified cohort + no
    # value fallback) — placeholder slots in top_8 produce a permanent
    # "rank #1 Pending" row that never resolves.
    if potd and isinstance(potd.get("data"), dict) and not potd["data"].get("noPlay"):
        pd = potd["data"]
        pick = pd.get("pick") or {}
        confidence = pd.get("confidence")
        # 'value' tier = sub-audit model lean fallback (2026-05-23). Style softer
        # than PRIME but still included as the day's anchor pick.
        potd_tier = "VALUE" if confidence == "value" else "PRIME"
        add({
            "type": "POTD",
            "icon": "🏆",
            "label": (pick.get("label") or pd.get("leanDisplay") or "POTD"),
            "game": pd.get("matchup") or pd.get("game", {}).get("matchup"),
            "conviction": pd.get("score", {}).get("total"),
            "tier": potd_tier,
            "source_table": "daily_best_bet_history",
            "source_key": today_et(),  # bet_date is the lookup key
            "narrative_hint": (potd.get("narrative") or "")[:200],
        })

    # 2. DotD
    if dawg:
        add({
            "type": "DotD",
            "icon": "🐕",
            "label": f"{dawg.get('team')} ML",
            "game": dawg.get("matchup"),
            "conviction": dawg.get("conviction"),
            "tier": dawg.get("tier"),
            "source_table": "daily_dawg",
            "source_key": today_et(),  # game_date is the lookup key
            "narrative_hint": (dawg.get("narrative") or "")[:200],
        })

    # 3. PRIME confluence ML/RL primary plays from game contexts
    # We pull sorted by sweat_score so the strongest game-side plays go first.
    game_side_candidates = []
    for g in sorted(games, key=lambda x: -(x.get("sweat_score") or 0)):
        pp = g.get("primary_play")
        if not pp or not isinstance(pp, dict):
            continue
        tier = pp.get("tier")
        if tier not in ("PRIME", "STRONG"):
            continue
        # Skip the type that's already POTD (NRFI typically) — POTD already in list
        ptype = pp.get("type")
        if ptype == "nrfi" and any(p["type"] == "POTD" for p in picks):
            continue
        game_side_candidates.append({
            "type": (
                "ML" if ptype == "ml"
                else "Over/Under" if ptype == "over"
                else "NRFI" if ptype == "nrfi"
                else "YRFI" if ptype == "yrfi"
                else ptype
            ),
            "icon": (
                "📈" if ptype == "ml"
                else "📊" if ptype == "over"
                else "🔒" if ptype == "nrfi"
                else "🔥" if ptype == "yrfi"
                else "📊"
            ),
            "label": pp.get("label"),
            "game": f"{g.get('away_team')} @ {g.get('home_team')}",
            "conviction": g.get("sweat_score"),
            "tier": tier,
            "source_table": "mlb_game_results",
            "source_key": g.get("game_id"),
            # The resolver needs to know how to evaluate this — for ML we
            # check home_win, for total we compare actual_total vs line, etc.
            "eval": {
                "type": ptype,
                "side": pp.get("label"),
                "line": g.get("close_total") if ptype == "over" else g.get("close_spread"),
                "home_team": g.get("home_team"),
                "away_team": g.get("away_team"),
            },
            "narrative_hint": pp.get("sub"),
        })

    # 4. Total edges from v4 (already filtered to >=1.5 delta by find_total_edges).
    # Convert into the same shape so they can compete in conviction ranking.
    for te in (total_edges or []):
        # Find the game_id from games list by matching matchup
        match = next(
            (g for g in games if f"{g.get('away_team')} @ {g.get('home_team')}" == te.get("game")),
            None,
        )
        if not match:
            continue
        game_side_candidates.append({
            "type": "Over/Under",
            "icon": "📊",
            "label": f"{te['direction']} {te['close_total']}",
            "game": te["game"],
            "conviction": int(60 + min(20, abs(te["delta"]) * 6)),  # synthetic conviction
            "tier": "STRONG" if abs(te["delta"]) >= 2.0 else "LIGHT",
            "source_table": "mlb_game_results",
            "source_key": match.get("game_id"),
            "eval": {
                "type": "over" if te["direction"] == "OVER" else "under",
                "side": te["direction"],
                "line": te["close_total"],
                "home_team": match.get("home_team"),
                "away_team": match.get("away_team"),
            },
            "narrative_hint": f"v4 model {te['projected_total']} vs line {te['close_total']} ({te['delta']:+.1f})",
        })

    # Add game-side candidates in conviction order; cap at 3 game-side picks
    game_side_candidates.sort(key=lambda c: -(c.get("conviction") or 0))
    added_game_side = 0
    for cand in game_side_candidates:
        if len(picks) >= 8 or added_game_side >= 3:
            break
        if add(cand):
            added_game_side += 1

    seen_players = {p["source_key"].split("|")[0] if "|" in str(p.get("source_key", "")) else None for p in picks if p.get("type", "").startswith("prop")}

    def _build_prop_pick(prop, force_tier=None):
        """Shared shape for prop entries — used by category locks + fill."""
        player = prop.get("player_name")
        proj = (prop.get("signals") or {}).get("_projected_ks") if isinstance(prop.get("signals"), dict) else None
        narrative_hint = None
        if proj is not None:
            narrative_hint = f"proj {proj}"
        elif isinstance(prop.get("signals"), dict) and prop.get("signals"):
            try:
                narrative_hint = next(iter(prop["signals"].values()))
            except StopIteration:
                narrative_hint = None

        # Label: K-prop picks suggest a SAFE LINE under the model projection,
        # not the raw projected number. Reads as the actual bet to make.
        #
        # Backstory: Cease today projected 8.3 Ks. Earlier label said
        # "Expected 8.3 Ks (Over)" which a casual reader interprets as
        # "bet Over 8" — but the standard 8.5 line needs 9+ to win. Cease
        # finished with 8 Ks → would have LOST Over 8.5 / Over 8, but
        # WON Over 7.5 (which is what our projection actually supports).
        #
        # Formula (2026-05-24):
        #   ks_over   suggested = floor(proj) - 0.5   (needs floor(proj) to win)
        #   ks_under  suggested = ceil(proj)  + 0.5   (needs <=ceil(proj))
        # Always cushioned below/above the projection so the bet line
        # matches the model's actual confidence, not its precision-aware
        # raw number. Raw projection still shown as supporting context.
        ptype = prop.get("prop_type", "")
        direction = prop.get("direction", "").lower()
        if ptype in ("ks_over", "ks_under") and proj is not None:
            import math as _m
            try:
                pv = float(proj)
                if direction == "over":
                    suggested_line = max(0.5, _m.floor(pv) - 0.5)
                    label = f"{player} Over {suggested_line} Ks  ·  proj {pv:.1f}"
                else:
                    suggested_line = _m.ceil(pv) + 0.5
                    label = f"{player} Under {suggested_line} Ks  ·  proj {pv:.1f}"
            except (TypeError, ValueError):
                label = f"{player} {prop.get('direction', '').title()} {prop.get('prop_line')} {prop.get('prop_type', '').replace('_', ' ')}"
        else:
            label = f"{player} {prop.get('direction', '').title()} {prop.get('prop_line')} {prop.get('prop_type', '').replace('_', ' ')}"

        return {
            "type": f"prop_{prop.get('prop_type')}",
            "icon": "🎯",
            "label": label,
            "game": prop.get("matchup"),
            "conviction": prop.get("conviction"),
            "tier": force_tier or prop.get("tier"),
            "source_table": "mlb_pipeline_props",
            "source_key": f"{player}|{prop.get('prop_type')}|{prop.get('prop_line')}",
            "narrative_hint": narrative_hint,
        }

    # 5. CATEGORY LOCKS — reserve a slot for the two highest-edge cohorts
    # in the 30d audit (2026-05-23): ks_over PRIME hits 76.5% (+24pt edge
    # over -110 break-even) and outs_under STRONG hits 93.3% (+38.8pt edge
    # over -120 break-even). Both get crowded out by the much higher-volume
    # hits_over slot in the normal PRIME fill — explicit reservation fixes
    # that. Picks the highest-conviction qualifier of each type, if any.
    def _reserve_category(pred):
        if len(picks) >= 8:
            return
        candidates = [p for p in props if pred(p) and p.get("player_name") not in seen_players]
        if not candidates:
            return
        candidates.sort(key=lambda p: -(p.get("conviction") or 0))
        chosen = candidates[0]
        seen_players.add(chosen.get("player_name"))
        add(_build_prop_pick(chosen))

    _reserve_category(lambda p: p.get("tier") == "PRIME" and p.get("prop_type") == "ks_over")
    _reserve_category(lambda p: p.get("tier") == "STRONG" and p.get("prop_type") == "outs_under")

    # 6. PRIME mastery props — highest conviction, diversify by player so the
    # 8-set doesn't end up 4-player-Hedges-related-bets-stacked.
    for prop in props:
        if len(picks) >= 8:
            break
        if prop.get("tier") != "PRIME":
            continue
        # Juice gate: hits_over @ 0.5 needs conv >= 95 to make a public card
        # slot (otherwise the 77% hit rate is -EV at typical -350 price).
        if _is_high_juice_hits_over(prop) and (prop.get("conviction") or 0) < HITS_OVER_05_CARD_CONV_FLOOR:
            continue
        player = prop.get("player_name")
        if player in seen_players:
            continue
        seen_players.add(player)
        add(_build_prop_pick(prop))

    # 7. STRONG mastery props as fill if we still have room
    for prop in props:
        if len(picks) >= 8:
            break
        if prop.get("tier") != "STRONG":
            continue
        # Same juice gate on STRONG hits_over @ 0.5 (lower bar at -300 juice
        # ~75% break-even; STRONG hit rate is 71.3% which is -EV outright).
        if _is_high_juice_hits_over(prop) and (prop.get("conviction") or 0) < HITS_OVER_05_CARD_CONV_FLOOR:
            continue
        player = prop.get("player_name")
        if player in seen_players:
            continue
        seen_players.add(player)
        add(_build_prop_pick(prop))

    return picks[:8]


def build_card():
    today = today_et()
    print(f"Building Sweat Card for {today}...")

    # Slate density check — drives content padding decisions below.
    # Standard MLB-season days (most days) hit `standard` mode and render
    # exactly as before. Thin / empty days pull in audit roll-up + recap +
    # upcoming events so the card never feels empty when slate is light.
    density = compute_slate_density()
    print(f"  Slate density: {density['mode']} | active={density['active_sports']} | "
          f"games={density['total_games']} | counts={density['counts']}")

    games = fetch_game_context()
    print(f"  {len(games)} MLB games on slate")

    tier_rates = fetch_tier_rates()
    potd = fetch_potd()
    dawg = fetch_dawg()
    props = fetch_top_props()

    nrfi_lock = find_nrfi_lock(games, tier_rates)
    yrfi_lock = find_yrfi_lock(games, tier_rates)
    bucket = find_bucket_angle(games)
    total_edges = find_total_edges(games, min_delta=1.5)
    skip_alerts = collect_skip_alerts(games)

    top_hits = top_props_by_type(props, "hits_over", 2)
    top_ks = top_props_by_type(props, "ks_over", 2)
    top_under_hits = top_props_by_type(props, "hits_under", 1)
    top_under_ks = top_props_by_type(props, "ks_under", 1)

    # Unified TOP PROPS — any type that grades PRIME/STRONG, ranked by
    # conviction. Replaces the limited hardcoded type buckets the frontend
    # was rendering. Now Mize H+A Under, Strider ER Under, etc. surface
    # alongside hits/Ks instead of being silently dropped (2026-05-21).
    # Frontend reads sweat_card.top_props directly — no client-side
    # filtering or grading, this is the canonical surface.
    top_props_all = [
        p for p in props
        if p.get("tier") in ("PRIME", "STRONG")
    ][:8]

    # ─── CURATED TOP 8 (Jerry's Best / Sweat Card lead picks) ────────────
    # The single source of truth for "what would we publish to social
    # tonight." Combines POTD + DotD + top game-side primary plays + top
    # mastery props, capped at 8. Every pick records source_table +
    # source_key so the nightly resolver can walk this list, look up each
    # pick's outcome from its source, and mark Win/Loss/Push — making the
    # 8-pick set a SINGLE auditable unit (e.g., "Sweat Card went 7-1 last
    # night" is now a real, queryable number, not a manual count).
    # Built 2026-05-22 to close the gap between social card receipts and
    # in-app receipts.
    # Graceful-degradation fallback: try 30d gate first; if auto-suppression
    # drops the card below MIN_CARD_PICKS (5), loosen to 60d, then 90d,
    # then off. Prevents empty cards on cold weeks while still respecting
    # the calibration data when it has signal. Returns the picks list +
    # which gate ended up being used (logged for transparency).
    top_8_curated = []
    gate_used = "30d"
    for window in ("30d", "60d", "90d", "off"):
        # Reset module-level cache so each window query is independent
        # (otherwise cached 30d rates would short-circuit the looser checks)
        global _COHORT_RATES_30D, _COHORT_RATES_60D, _COHORT_RATES_90D
        _COHORT_RATES_30D = _COHORT_RATES_60D = _COHORT_RATES_90D = None
        top_8_curated = curate_top_8(games, props, potd, dawg, total_edges, gate_window=window)
        gate_used = window
        if len(top_8_curated) >= MIN_CARD_PICKS:
            break
    if gate_used != "30d":
        print(f"  ⚠️  Card auto-loosened to gate_window={gate_used} "
              f"(strict 30d gate left only {len(top_8_curated)} picks)")

    # Stack alert detection — find games where 4+ hits picks are PRIME
    stack_games = {}
    for p in props:
        if p.get("prop_type") in ("hits_over", "hits_under") and p.get("conviction", 0) >= 82:
            mu = p.get("matchup", "")
            stack_games[mu] = stack_games.get(mu, 0) + 1
    stack_alerts = [{"matchup": mu, "prime_count": n} for mu, n in stack_games.items() if n >= 4]

    # Team-stack caution tag (added 2026-05-29). When 3+ same-team players have
    # the SAME high-conviction hits prop in the same matchup (e.g. SF@COL Coors
    # game — Adames + Arraez + Schmitt + Devers + Gilbert + Eldridge ALL hits
    # over 0.5 PRIME 100), users were burning juice 6 times on what is really
    # ONE underlying thesis (Coors + bad opposing pitcher + hot team offense).
    # We tag each prop with team_stack_caution so the app can render a warning
    # badge — pick the strongest 2-3 individual edges rather than stacking 6.
    team_groups = {}  # (matchup, prop_type, team) → list of prop refs
    for p in props:
        if p.get("prop_type") not in ("hits_over", "hits_under"):
            continue
        if p.get("conviction", 0) < 82:
            continue
        key = (p.get("matchup", ""), p.get("prop_type"), p.get("player_team", ""))
        team_groups.setdefault(key, []).append(p)
    for (mu, ptype, team), plist in team_groups.items():
        if len(plist) >= 3:
            for p in plist:
                sigs = p.get("signals") or {}
                sigs["team_stack_caution"] = (
                    f"⚠️ TEAM STACK: {len(plist)} {team} hitters share this signal set "
                    f"— consider top 2 individually rather than the full stack (juice multiplies)."
                )
                p["signals"] = sigs

    # Padding for thin / empty days — keeps the card useful when slate is
    # MLB-only-July-Tuesday or post-season-only-MLB. Standard days still
    # fetch yesterday_recap (it powers the Receipts tab regardless of
    # density). Skipping it 2026-05-24 caused the Receipts tab to show
    # only POTD/DotD on standard slate days, missing the full top_8.
    audit_roll_up = None
    yesterday_recap = fetch_yesterday_recap()
    upcoming_events = None
    if density["mode"] in ("thin", "empty"):
        audit_roll_up = fetch_audit_roll_up()
        upcoming_events = fetch_upcoming_events()

    card = {
        "slate_date": today,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # Density metadata — app reads this to pick render mode
        "slate_density": density["mode"],
        "active_sports": density["active_sports"],
        "total_games": density["total_games"],
        "sport_counts": density["counts"],
        "lock": nrfi_lock,                    # 🔒 the highest-audited play
        "secondary_lock": yrfi_lock,          # 🔒 second-tier audited play
        "potd": potd.get("data") if potd else None,
        "potd_narrative": (potd or {}).get("narrative"),
        "dawg": (
            {
                "team": dawg.get("team"),
                "matchup": dawg.get("matchup"),
                "conviction": dawg.get("conviction"),
                "tier": dawg.get("tier"),
                "narrative": dawg.get("narrative"),
                "signals": dawg.get("signals"),
            }
            if dawg else None
        ),
        "top_8": top_8_curated,               # 🎯 Jerry's Best — the curated 8-pick set
        "top_hits_over": top_hits,
        "top_ks_over": top_ks,
        "top_hits_under": top_under_hits,
        "top_ks_under": top_under_ks,
        "top_props": top_props_all,  # unified surface — see comment above
        # Bucket angle pulled from user-facing payload 2026-05-07 pending audit.
        # The signal (starter weak in innings 4-6 + opposing pen rested) hasn't
        # been cohort-tracked, so we don't know its actual hit rate. Same
        # discipline that just caught K-Under PRIME at 55.6% and OVER-lean at
        # 0-10 — don't surface unaudited PRIME-style flags. Keep computing
        # internally (find_bucket_angle still runs for logging) but null in
        # the public field until cohort matures.
        "bucket_angle": None,
        "total_edges": total_edges,           # 📈 model vs market total deltas >= 1.5
        "stack_alerts": stack_alerts,
        "skip_alerts": skip_alerts,
        "tier_rates_30d": {
            "nrfi_prime_90_94": tier_rates.get("nrfi_prime_90_94"),
            "yrfi_lean_le40": tier_rates.get("yrfi_lean_le40"),
            "nrfi_volatile_95plus": tier_rates.get("nrfi_volatile_95plus"),
            "spread_delta_ge2": tier_rates.get("spread_delta_ge2"),
        },
        # Thin / empty day padding — null on standard / overload days
        "audit_roll_up": audit_roll_up,
        "yesterday_recap": yesterday_recap,
        "upcoming_events": upcoming_events,
    }

    # Upsert to jerry_cache
    cache_key = f"sweat_card_{today}"
    payload = {
        "cache_key": cache_key,
        "game_id": cache_key,
        "sport": "MLB",
        "narrative": f"Sweat Card for {today}",
        "data": card,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_cache",
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
        params={"on_conflict": "cache_key"},
        json=payload,
        timeout=15,
    )
    if r.status_code in (200, 201, 204):
        print(f"✅ Sweat Card stored: lock={nrfi_lock['game'] if nrfi_lock else '—'}, "
              f"yrfi={yrfi_lock['game'] if yrfi_lock else '—'}, "
              f"bucket={'yes' if bucket else 'none'}, "
              f"total_edges={len(total_edges)}, stacks={len(stack_alerts)}, skips={len(skip_alerts)}")
    else:
        print(f"❌ Sweat Card upsert failed {r.status_code}: {r.text[:200]}")


if __name__ == "__main__":
    build_card()
