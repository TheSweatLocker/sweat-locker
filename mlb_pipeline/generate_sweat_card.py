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
    """Pull live audited tier rates from mlb_tier_calibration.

    2026-08-12 REWRITE — HONEST SAMPLE-SIZE FIX:
    Prior version hardcoded window_label='30d'. That fetched TINY samples
    for low-volume cohorts (NRFI PRIME 30d = n=8, YRFI 30d = n=41), then
    surfaced them as PRIME hit rates on the sweat card — 62.5% on n=8 has
    a ±30pp confidence interval. Misleading users into thinking a coin
    flip is a PRIME lock.

    New logic: prefer 'std' (lifetime) window as source of truth. Fall
    back to 30d only if lifetime is missing. Skip 7d entirely (n<10 for
    NRFI/YRFI cohorts). Result: sweat card shows honest lifetime hit
    rates on the samples that are statistically meaningful.

    Also filters to TODAY's computed_date to avoid the 1k-row pagination
    limit that killed yrfi_lean_le40 lookup in the 5/22 incident.
    """
    today = today_et()
    # Pull ALL windows for today — prefer std, fall back to 30d
    rows = sb_get("mlb_tier_calibration", {
        "window_label": "in.(std,30d)",
        "sport": "eq.mlb",
        "computed_date": f"eq.{today}",
        "select": "tier,hits,total,hit_rate,window_label",
    })
    if not rows:
        rows = sb_get("mlb_tier_calibration", {
            "window_label": "in.(std,30d)",
            "sport": "eq.mlb",
            "select": "tier,hits,total,hit_rate,window_label,computed_date",
            "order": "computed_date.desc",
            "limit": "500",
        })
    # Split by window, then prefer std
    std_rows = {r["tier"]: r for r in rows if r.get("window_label") == "std"}
    d30_rows = {r["tier"]: r for r in rows if r.get("window_label") == "30d"}
    seen = {}
    for tier in set(list(std_rows.keys()) + list(d30_rows.keys())):
        # std wins if it exists AND has meaningful sample (n>=20)
        s = std_rows.get(tier)
        if s and (s.get("total") or 0) >= 20:
            seen[tier] = s
        else:
            # fallback: 30d, or std even if small
            seen[tier] = d30_rows.get(tier) or s
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
            # Overlay live prop results so the recap reflects current
            # grading state. Yesterday's card top_8 was written at
            # midnight while many late games were still pending; props
            # graded by the morning resolve cron never flowed back into
            # the stored card. Without this overlay, the Recap tab
            # shows "Pending" for picks that actually won/lost hours
            # ago (6/13 Misiorowski ER Under stuck as Pending on a CG
            # shutout was the trigger). Match by player_name + prop
            # parsed from the original label.
            live_props = sb_get("mlb_pipeline_props", {
                "game_date": f"eq.{yesterday}",
                "result": "not.is.null",
                "select": "player_name,prop_type,prop_line,direction,result",
            }) or []
            # Build lookup keyed by (player_name_lower, prop_type_lower)
            prop_lookup = {}
            for p in live_props:
                k = ((p.get("player_name") or "").lower(),
                     (p.get("prop_type") or "").lower())
                prop_lookup[k] = p.get("result")

            def _resolved_result(pick):
                """Return the live-graded result if we can match this top_8
                pick back to mlb_pipeline_props; else fall through to stored."""
                stored = pick.get("result")
                if stored and stored not in ("Pending", "pending", None, ""):
                    return stored
                label = (pick.get("label") or "").strip()
                if not label:
                    return stored
                # Labels look like "Jacob Misiorowski Under 1.5 er under"
                # — try suffix prop_type match.
                low = label.lower()
                # Try each known prop_type as suffix
                for pt in ("er_over", "er_under", "ks_over", "ks_under",
                           "ha_over", "ha_under", "bb_over", "bb_under",
                           "outs_over", "outs_under", "hits_over",
                           "hits_under"):
                    pt_human = pt.replace("_", " ")  # "er over"
                    if low.endswith(pt_human):
                        # Player name is everything before the line + dir tokens.
                        # Strip trailing "Over/Under N.N <pt_human>" pattern.
                        name_part = label[: low.rfind(pt_human)].strip()
                        # Remove trailing "Over 1.5" / "Under 0.5"
                        toks = name_part.rsplit(" ", 2)
                        if len(toks) >= 2 and toks[-2] in ("Over", "Under"):
                            name_part = " ".join(toks[:-2]).strip()
                        live = prop_lookup.get((name_part.lower(), pt))
                        if live:
                            return live
                        break
                return stored

            recap["top_8"] = [
                {
                    "rank": p.get("rank"),
                    "tier": p.get("tier"),
                    "label": p.get("label"),
                    "result": _resolved_result(p),
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
    """Find PRIME tier (90-94) NRFI game. Returns dict or None.

    2026-08-12 sample-size + break-even discipline:
      * Require rate_row.total >= 30 (statistical significance)
      * Require lifetime hit_rate >= 55% (clears typical NRFI juice of
        -110 to -130 which needs 52.4-56.5% break-even)
      * If either fails, return None (don't publish as PRIME lock)
    Prevents surfacing n=8 62.5% coin-flip as a "PRIME" recommendation.
    """
    candidates = [g for g in games if 90 <= (g.get("nrfi_score") or 0) <= 94]
    if not candidates:
        return None
    candidates.sort(key=lambda g: -(g.get("nrfi_score") or 0))
    g = candidates[0]
    rate_row = tier_rates.get("nrfi_prime_90_94", {}) or {}
    n = rate_row.get("total") or 0
    hr = (rate_row.get("hit_rate") or 0) * 100
    # Discipline gates — suppress publication if signal is weak/small
    if n < 30 or hr < 55:
        print(f"  🚫 NRFI PRIME lock suppressed: n={n} hit={hr:.1f}% "
              f"(need n>=30 AND hit>=55% to publish)")
        return None
    _nrfi = g.get("nrfi_score")
    return {
        "game": f"{g.get('away_team')} @ {g.get('home_team')}",
        # Phase 1 of engine_clarity_refactor (2026-06-18): bare `score`
        # on LOCK was the NRFI 1st-inning signal score (0-100), NOT the
        # sweat score that appears elsewhere with the same key. Adding
        # explicit `nrfi_score` field while keeping `score` for back-
        # compat. Next refactor cycle removes `score` after app rolls
        # forward to the namespaced key.
        "score": _nrfi,           # deprecated — back-compat only
        "nrfi_score": _nrfi,       # canonical field, app should consume this
        "score_source": "nrfi_score",
        "tier": "PRIME",
        "tier_source": "nrfi_band",  # explicit attribution
        "audited_rate": round(hr, 1),
        "audited_n": n,
        "context": {
            "home_pitcher": g.get("home_pitcher"),
            "away_pitcher": g.get("away_pitcher"),
            "home_first_inn_era": g.get("home_first_inning_era"),
            "away_first_inn_era": g.get("away_first_inning_era"),
        },
    }


def find_yrfi_lock(games, tier_rates):
    """Find YRFI lean games.

    2026-08-12 sample-size + break-even discipline:
      * Require rate_row.total >= 50 (YRFI markets are common enough that
        the bar is higher than NRFI)
      * Require lifetime hit_rate >= 58% (typical YRFI juice is -130 to
        -150 which needs 56.5-60% break-even). Lifetime YRFI at 57% is
        essentially break-even at typical juice — don't publish as a lock.
    """
    candidates = [g for g in games if (g.get("nrfi_score") or 100) <= 40]
    if not candidates:
        return None
    candidates.sort(key=lambda g: g.get("nrfi_score") or 100)  # lowest = strongest YRFI
    g = candidates[0]
    rate_row = tier_rates.get("yrfi_lean_le40", {}) or {}
    n = rate_row.get("total") or 0
    hr = (rate_row.get("hit_rate") or 0) * 100
    if n < 50 or hr < 58:
        print(f"  🚫 YRFI lock suppressed: n={n} hit={hr:.1f}% "
              f"(need n>=50 AND hit>=58% to publish)")
        return None
    _yrfi = g.get("nrfi_score")
    return {
        "game": f"{g.get('away_team')} @ {g.get('home_team')}",
        # Phase 1 namespacing (2026-06-18): YRFI uses the same nrfi_score
        # column (low score = YRFI lean), so `yrfi_score` is added as
        # the canonical app-side key. `score` retained for back-compat.
        "score": _yrfi,           # deprecated — back-compat only
        "yrfi_score": _yrfi,       # canonical field
        "score_source": "yrfi_score",
        "tier": "YRFI",
        "tier_source": "yrfi_band",
        "audited_rate": round(hr, 1),
        "audited_n": n,
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
    """Surface top N props of a given type WITH the juice/book gate applied.

    Previously this just returned filtered[:n] — bypassed the
    HITS_OVER_05_CARD_CONV_FLOOR juice floor defined above. As a result
    the card was publishing PRIME hits_over 0.5 picks at -300 juice
    where 72-77% lifetime hit rate is break-even or worse.

    Permanent fix (2026-06-06): apply the juice floor here. Specifically:
      - hits_over 0.5 line: require conviction >= 95 (lifetime says PRIME
        is barely +EV after -300 juice; only the top-of-PRIME survives)
      - all other types: require conviction >= 70 (minimum surface bar)
      - require non-null book_line (no internal-line-only props on card)
    """
    filtered = [p for p in props if p.get("prop_type") == target_type]
    out = []
    for p in filtered:
        conv = p.get("conviction") or 0
        # Bar 1: type-specific juice floor
        if _is_high_juice_hits_over(p):
            if conv < HITS_OVER_05_CARD_CONV_FLOOR:
                continue
        else:
            if conv < 70:
                continue
        # Bar 2: must have a book line attached (rule out internal-only props)
        # Hits_over 0.5 specifically must have a book line — sportsbooks
        # always publish that line, so a missing book line means our
        # Phase 2 attach failed and we don't actually know the price.
        if target_type == "hits_over":
            bl = p.get("book_line")
            if bl is None or (isinstance(bl, (int, float)) and bl == 0):
                continue
        out.append(p)
        if len(out) >= n:
            break
    return out


def _odds_within_range(prop, lo: int = -300, hi: int = 150) -> bool:
    """Per feedback_prop_jerry_odds: only ship props priced in [-300, +150]
    on the direction we're taking. Outside that band, the juice/payout
    profile doesn't support recommending regardless of conviction.

    2026-08-11: added after 4 batter hits_over slipped onto today's card at
    -300 to -475 juice — Aranda -375 with PRIME 77% hit rate is -1.9pt EV
    even at max conviction. Odds unknown → returns True (don't block on
    missing data; other gates handle it — _is_high_juice_hits_over defaults
    conservatively for hits_over 0.5 with null odds).

    Uses over/under odds based on direction. Returns True if:
      * relevant odds field is null (defer to other gates)
      * odds ∈ [lo, hi]
    Returns False if odds fall outside the band.
    """
    direction = (prop.get('direction') or '').lower()
    odds = (prop.get('book_over_odds') if direction == 'over'
            else prop.get('book_under_odds') if direction == 'under'
            else None)
    if odds is None:
        return True  # unknown → defer to other gates
    try:
        odds = int(odds)
    except (TypeError, ValueError):
        return True
    return lo <= odds <= hi


def _is_high_juice_hits_over(prop):
    """Heuristic: hits_over 0.5 lines are nearly always priced -250 to -400
    because 70-80% of MLB starters get a hit. Only returns True (= "apply
    strict floor") when we're actually in juice-trap territory.

    2026-08-11 (v2): now odds-aware. Prior version always returned True for
    hits_over 0.5 regardless of book_odds, which meant a solid pick at
    -180 juice (well within acceptable range) got treated the same as a
    -400 juice trap. Result: 4 batter picks today with 8-signal data stacks
    (Aranda hit 7/7 L7, opp SP 4.42 xERA, opp BP 5.45 ERA) got blocked
    because they were labeled "high juice" without checking the actual
    price.

    Rules:
      * Not hits_over 0.5 → False (no restriction)
      * book_over_odds ≥ -220 (fair juice) → False (data-quality gate handles it)
      * book_over_odds <= -220 (juice territory) → True (require conv ≥ 95)
      * book_over_odds unknown → True (can't verify price, be conservative)

    Also see feedback_prop_jerry_odds: ship props only when odds are in
    [-300, +150]. Outside that range gets skipped upstream, not here.
    """
    if prop.get('prop_type') != 'hits_over':
        return False
    try:
        if float(prop.get('prop_line') or 0) > 0.5:
            return False  # 1.5+ lines are typically +money, unaffected
    except (TypeError, ValueError):
        return False
    odds = prop.get('book_over_odds')
    if odds is None:
        return True  # unknown price → treat as juice trap defensively
    try:
        odds = int(odds)
    except (TypeError, ValueError):
        return True
    # -220 threshold: at 68.75% break-even, a PRIME hit rate of 77% (from
    # 30d audit) clears with ~8pt margin. Below -220 the margin evaporates.
    return odds <= -220


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
      - 30d hit_rate >= break_even + safety_margin (n>=10) -> 'eligible'
      - 30d below but 60d >= break_even (n>=10) -> 'demote' (drop tier one level)
      - 60d also below (n>=10) -> 'suppress'
      - Insufficient sample anywhere -> 'eligible' (no data is not failure)

    SAFETY_MARGIN (2026-06-06): added 1.5pt buffer above break-even so
    cohorts hovering within ±1pt of break-even (statistically zero-EV at
    typical juice) get suppressed instead of slipping through. Was a real
    issue: hits_under STRONG at 57.2% on n=187 cleared the 56.5% break-even
    by 0.7pt, but recent 7-day was 52% — variance was reverting it back to
    break-even. Now requires 58% to clear, forcing a meaningful edge.

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

    SAFETY_MARGIN = 1.5  # pp above break-even to clear the gate
    threshold = break_even + SAFETY_MARGIN

    def _check(rates):
        r = rates.get((pt, direction, tier))
        if r is None: return None
        rate, n = r
        if rate is None or n < COHORT_MIN_N:
            return None  # insufficient sample
        return rate >= threshold

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

    # Tier calibration (2026-08-03 v2) — FADE historical losers (flip
    # direction), cap juice traps, promote goldmine SKIPs. Per user
    # directive 2026-08-03: don't suppress garbage — Jerry should FADE
    # the other side ("market has priced it in").
    try:
        from prop_tier_calibration import apply_calibration
        pre = len(props)
        calibrated = []
        cal_faded = cal_capped = cal_promoted = 0
        for p in props:
            r = apply_calibration(p, jerry_verdict='BACK',
                                  sport=(p.get('sport') or 'MLB'))
            p = dict(p)
            if r.get('flip_direction'):
                old_dir = p['direction']
                new_dir = r['new_direction']
                p['direction'] = new_dir
                p['tier'] = r['new_tier']
                p['conviction'] = r['new_conviction']
                if p.get('prop_type', '').endswith(f'_{old_dir}'):
                    family = p['prop_type'].rsplit('_', 1)[0]
                    p['prop_type'] = f'{family}_{new_dir}'
                p['_tier_calibration'] = r['reason']
                cal_faded += 1
            elif 'promoted' in r.get('reason', ''):
                p['tier'] = r['new_tier']
                p['conviction'] = r['new_conviction']
                p['_tier_calibration'] = r['reason']
                cal_promoted += 1
            elif r['new_conviction'] < (p.get('conviction') or 0):
                p['conviction'] = r['new_conviction']
                p['_tier_calibration'] = r['reason']
                cal_capped += 1
            calibrated.append(p)
        props = calibrated
        if cal_faded or cal_capped or cal_promoted:
            print(f'  tier calibration: faded {cal_faded} '
                  f'· juice-capped {cal_capped} · goldmine-promoted {cal_promoted} '
                  f'(of {pre} pre-cal)')
    except ImportError:
        pass

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
    seen_keys = set()  # dedupe by (source_table, source_key)
    seen_play_signatures = set()  # 2026-06-21 dedup: same game-side play
    # surfacing twice. Yesterday Cubs ML appeared at rank 1 (POTD path,
    # source_table=daily_best_bet_history) AND rank 4 (ML primary-play path,
    # source_table=daily_picks_ml) — different source keys but identical
    # game + bet. seen_play_signatures catches "Cubs ML" no matter which
    # path surfaces it second.
    seen_ml_games = set()  # 2026-06-21 conflict — same-game opposite-side
    # ML picks. Today's run had Cubs ML (DotD PRIME, rank 2) AND Toronto
    # ML lean (rank 4) — both on TOR @ CHC. Auto-loss on one. When ANY
    # ML pick lands on a game, no other ML on the same game can be added.
    # Game-side ML "wins" the game lock; subsequent ML candidates fall
    # through and let prop/total picks on that game still surface.
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

    # PROP-TYPE DIVERSITY CAP (added 2026-06-06). Same prop_type can appear
    # at most MAX_PROP_TYPE_IN_TOP_8 times in the curated set. Before this,
    # nights with stale prop calibration would stack 5+ hits_under STRONG
    # in top_8 — user feedback "spreading love" architecture says the card
    # should mix hits/Ks/BB/HA/ER/totals/sides instead of doubling down on
    # one cohort. POTD + DotD bypass the cap (anchors). Game-side ML/RL
    # don't count toward prop caps.
    MAX_PROP_TYPE_IN_TOP_8 = 2
    prop_type_count = {}

    # ── Correlation cluster tracking (2026-07-21) ──
    # Tag picks with cluster metadata so the UI can render "correlated w/ #X"
    # warnings. Cluster signature = (game_id, directional_side). Two picks
    # in the same cluster mean the same outcome cashes them BOTH (or neither).
    #
    # Example clusters:
    #   • WSH ML + WSH/COL O + Freeland ER O = all cash if WSH scores big
    #   • LAD ML + Sánchez HA O = both cash if LAD bats hit Sánchez
    #   • Cease K OVER + TB ML = uncorrelated (different outcomes)
    #
    # 3+ legs on same cluster = 1 bet in 3 wrappers, not real diversification.
    # We assign cluster_id here; downstream card render surfaces the warning.
    cluster_by_signature = {}      # signature -> cluster_id
    next_cluster_id = [1]

    def _cluster_signature(pick):
        """Correlated bets share game + directional side. Returns None for
        picks that don't fit the pattern (e.g. neutral prop like K prop)."""
        game = pick.get('game')
        if not game:
            return None
        label = (pick.get('label') or '').lower()
        ptype = (pick.get('type') or '').lower()
        # Extract which team's side we're betting on
        # ML picks: side = team_name
        if _is_ml_pick(pick):
            for team_marker in ('braves','yankees','dodgers','phillies','giants',
                                'royals','athletics','diamondbacks','padres','cubs',
                                'tigers','angels','cardinals','mets','brewers',
                                'orioles','red sox','mariners','reds','white sox',
                                'rangers','rays','blue jays','pirates','guardians',
                                'twins','marlins','astros','nationals','rockies'):
                if team_marker in label:
                    return (game, f'ml_{team_marker}')
            return (game, 'ml_unknown')
        # Total OVER/UNDER — cluster with any offensive prop on the game
        if 'over' in label and 'total' in ptype:
            return (game, 'game_over')
        if 'under' in label and 'total' in ptype:
            return (game, 'game_under')
        # Pitcher props — cluster with the "opposing team wins" side
        # Pitcher HA_UNDER / ER_UNDER / K_OVER = pitcher dominating = his team ML side
        # Pitcher HA_OVER / ER_OVER / K_UNDER / BB_OVER = pitcher struggling = opposing side
        if ptype.startswith('prop_'):
            prop_type = ptype.replace('prop_', '')
            if prop_type in ('ha_under', 'er_under', 'ks_over', 'outs_over', 'bb_under'):
                # Pitcher dominates → his team side
                return (game, f'pitcher_dominant_{pick.get("player","?")}')
            if prop_type in ('ha_over', 'er_over', 'ks_under', 'outs_under', 'bb_over'):
                # Pitcher struggles → opposing team side
                return (game, f'pitcher_struggles_{pick.get("player","?")}')
            if prop_type == 'hits_over':
                # Batter hits — cluster with team offense
                return (game, 'batter_hits_stack')
        return None

    def _play_signature(pick):
        """Normalize a pick to its bet-level identity. Same game + same
        bet (ML/RL/total/prop) collapses to one signature, regardless
        of which pipeline path surfaced the pick. Returns None when we
        can't infer a stable signature (and dedup falls through to the
        legacy source_key check)."""
        g = pick.get("game") or ""
        # Game-side bets: label encodes both team + market type
        # ("Chicago Cubs ML", "Pittsburgh Pirates ML lean", "Over 9.5", etc.)
        label = (pick.get("label") or "").strip().lower()
        ptype = (pick.get("type") or "").lower()
        if not label:
            return None
        # Strip trailing " lean" so PRIMARY and "lean" variants collapse.
        # 2026-08-02: also strip " (jerry X/YY)" suffix so POTD label
        # ("Milwaukee Brewers ML (Jerry 76/100)") collapses with the plain
        # ML slot label ("Milwaukee Brewers ML") — otherwise Brewers ML
        # surfaced at rank 1 (POTD) AND rank 2 (Jerry-anchored ML slot).
        import re as _re
        norm_label = label.replace(" lean", "").strip()
        norm_label = _re.sub(r"\s*\(jerry\s+\d+/\d+\)\s*$", "", norm_label).strip()
        # For props, include the player name (already in label) so two
        # different players on the same team don't collide.
        if ptype.startswith("prop_"):
            return ("prop", norm_label)
        return (g.lower(), norm_label)

    def _is_ml_pick(pick):
        """True when the pick is a moneyline bet (any path)."""
        ptype = (pick.get("type") or "").lower()
        if ptype in ("ml", "potd", "dotd"):
            label = (pick.get("label") or "").lower()
            return " ml" in label
        return False

    def add(pick):
        # Dedupe by a stable identifier
        key = f"{pick['source_table']}:{pick['source_key']}"
        if key in seen_keys:
            return False
        # Play-signature dedup — same bet should never appear twice even
        # when surfaced by different paths.
        sig = _play_signature(pick)
        if sig is not None and sig in seen_play_signatures:
            return False
        # Same-game ML conflict — once any ML pick is added on a game,
        # block opposite-side ML picks on the same game.
        gkey = _game_key(pick)
        if gkey is not None and _is_ml_pick(pick) and gkey in seen_ml_games:
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
        # Prop-type diversity cap (skip for anchors and game-side picks)
        if not is_anchor and ptype.startswith("prop_"):
            prop_type = ptype.replace("prop_", "")
            if prop_type_count.get(prop_type, 0) >= MAX_PROP_TYPE_IN_TOP_8:
                return False
            prop_type_count[prop_type] = prop_type_count.get(prop_type, 0) + 1
        seen_keys.add(key)
        if sig is not None:
            seen_play_signatures.add(sig)
        if gkey is not None and _is_ml_pick(pick):
            seen_ml_games.add(gkey)
        if gkey is not None:
            game_pick_count[gkey] = game_pick_count.get(gkey, 0) + 1
        pick["rank"] = len(picks) + 1
        pick["result"] = "Pending"
        # ── Cluster correlation tag (2026-07-21) ──
        # Assign cluster_id so the UI can render "correlated w/ #X" warnings
        # when 2+ picks share directional exposure. Downstream card renderer
        # can prune, warn, or display alongside — up to the app.
        csig = _cluster_signature(pick)
        if csig is not None:
            if csig not in cluster_by_signature:
                cluster_by_signature[csig] = next_cluster_id[0]
                next_cluster_id[0] += 1
            pick["_correlation_cluster_id"] = cluster_by_signature[csig]
            # Count how many earlier picks share this cluster
            cluster_size = sum(1 for p in picks if p.get("_correlation_cluster_id") == cluster_by_signature[csig])
            if cluster_size >= 1:
                pick["_correlation_note"] = f"cluster #{cluster_by_signature[csig]} · {cluster_size + 1} correlated legs"
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
        # POTD tier mapping (Phase 2 of engine_clarity_refactor — 2026-06-18):
        # The `confidence` field is set by play_of_day.py based on which
        # POTD selection path fired (audit pool vs value fallback). It's
        # NOT a generic data-availability flag, but it IS heterogeneous:
        #   'elite'       — audit pool path (STRONG/ELITE resolver)
        #   'secondary'   — value pool LEAN resolver
        #   'tertiary'    — value pool LIGHT resolver
        #   'value'       — value pool PASSTHROUGH (side picks)
        # Per the unified taxonomy (PRIME/STRONG/LEAN), map by
        # confidence:
        #   elite       -> PRIME (passes audit + projection gate)
        #   secondary   -> STRONG (value pool LEAN — still defensible)
        #   tertiary    -> LEAN  (LIGHT-tier — informational)
        #   value       -> STRONG (PASSTHROUGH side — typically STRONG resolver)
        # This kills the simplistic "elite → PRIME, anything else → VALUE"
        # split that was misleading users about confidence-level mapping.
        _potd_tier_map = {
            'elite': 'PRIME',
            'secondary': 'STRONG',
            'tertiary': 'LEAN',
            'value': 'STRONG',
        }
        potd_tier = _potd_tier_map.get(confidence or '', 'STRONG')
        add({
            "type": "POTD",
            "icon": "🏆",
            "label": (pick.get("label") or pd.get("leanDisplay") or "POTD"),
            "game": pd.get("matchup") or pd.get("game", {}).get("matchup"),
            "conviction": pd.get("score", {}).get("total"),
            "tier": potd_tier,
            "tier_source": "potd_confidence_map",  # explicit attribution
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

    # 3. Game-side candidates — Jerry-driven single source of truth (2026-08-06).
    #
    # Refactor rationale: 30d daily_grades showed primary_play at 55.6%,
    # v4_total_edges implicitly averaged in at ~55%, mc_high_conf at 40%.
    # Meanwhile Jerry-scored props hit 61-62%. Selection layer was mixing
    # quality bars from 8+ sources; simplifying to a single Jerry-ranked
    # source. primary_play + v4_total_edges become SIGNALS Jerry considers
    # in his synthesis, not direct card picks.
    #
    # NRFI/YRFI are folded in via sync_nrfi_to_jerry_reads.py which
    # bridges the rules-based nrfi_ensemble output into jerry_reads with
    # call_market='nrfi'/'yrfi'. From the card's perspective they're just
    # more jerry_reads entries.
    #
    # (The block that reads jerry_reads and builds game_side_candidates
    # lives below in section 4 — kept there for locality with its ML
    # juice gate + line-movement contradiction filter.)
    game_side_candidates = []

    # 4. Jerry-driven game-side selection — PRIMARY SOURCE (2026-08-06 refactor).
    #
    # jerry_reads is now the single source of truth for ML/total game picks.
    # Everything Jerry BACKs at conv >= 70 is a candidate; nothing else
    # surfaces at this layer. primary_play + v4 total edges are inputs
    # Jerry synthesizes over, not competing picks. This closed the
    # split-quality-bar problem where 8+ sources fought for card slots
    # (see 30d dip to 57.9% driven by primary_play 55.6% and mc_high_conf 40%).
    #
    # NRFI/YRFI live directly on mlb_game_context (nrfi_ensemble_tier/pick/conf).
    # No jerry_reads bridge — the unique constraint (sport, game_id, game_date)
    # blocks a second jerry_read per game. Instead, read nrfi_ensemble
    # directly and add as candidates in the block below.
    try:
        jr_url = f"{os.environ.get('SUPABASE_URL')}/rest/v1/jerry_reads"
        jr_h = {"apikey": os.environ.get('SUPABASE_KEY'),
                "Authorization": f"Bearer {os.environ.get('SUPABASE_KEY')}"}
        # Filter: exclude both 'pass' AND 'lean' from Sweat Card (2026-08-04).
        # 'lean' = Jerry's directional hedge at conv 40-64, not an active play.
        # Sweat Card only surfaces conv ≥70 (active PLAY tier) — the ceiling
        # for 'lean' is 64 so pulling call_market != pass AND != lean is
        # equivalent to conv ≥ 65 filter, but keeps it explicit + safe.
        # 2026-08-06 persona shift: pull ALL Jerry reads except structural
        # PASS (blank data). LEAN + READ get published as their own tiers.
        # Pass-through everything with a directional side; conviction band
        # decides the tier on the card. Historical filter was
        # call_market != pass AND != lean (conv >= 70 only) — that was
        # the pre-persona-shift discipline gate. New card shows the
        # analyst's take on every game.
        jr = requests.get(jr_url, headers=jr_h,
                          params={"sport": "eq.MLB", "game_date": f"eq.{today_et()}",
                                  "call_market": "not.eq.pass",
                                  "select": "game_id,call_market,call_side,call_line,"
                                            "conviction,call_text,short_read",
                                  "order": "conviction.desc"},
                          timeout=10).json()
        # Map game_id -> matchup for label building
        game_by_id = {g.get("game_id"): g for g in games if g.get("game_id")}
        for jread in (jr if isinstance(jr, list) else []):
            conv = jread.get("conviction") or 0
            # New tier map (2026-08-06 persona shift):
            #   80+  = PRIME
            #   65-79 = STRONG
            #   50-64 = LEAN
            #   30-49 = READ (analytical, thin edge — still directional)
            #   <30  = drop (data broken)
            if conv < 30:
                continue
            gid = jread.get("game_id")
            gctx = game_by_id.get(gid)
            if not gctx:
                continue
            mkt = (jread.get("call_market") or "").lower()
            side = (jread.get("call_side") or "").upper()
            call_text = jread.get("call_text") or ""
            if conv >= 80:   tier = "PRIME"
            elif conv >= 65: tier = "STRONG"
            elif conv >= 50: tier = "LEAN"
            else:            tier = "READ"
            if mkt == "ml":
                # HEAVY-FAV ML HARD FILTER (2026-08-04): Jerry's prompt says
                # never emit ML at -200+ but Jerry has violated it in practice
                # (PHI ML -298 conv 72 tonight). Defense-in-depth: reject at
                # curation layer too. Same principle as jerry_anchor_potd.
                pick_ml = gctx.get("home_ml_close") if side == "HOME" else gctx.get("away_ml_close")
                if pick_ml is not None and pick_ml <= -200:
                    print(f"  🚫 sweat-card juice gate: skipped {jread.get('call_text') or '?'} at ML {pick_ml}")
                    continue
                team = gctx["home_team"] if side == "HOME" else gctx["away_team"]
                label = f"{team} ML"
                icon = "📈"; ptype = "ml"; line = None
            elif mkt == "total":
                label = f"{side.title()} {jread.get('call_line') or gctx.get('close_total','')}".strip()
                icon = "📊"; ptype = "over" if side == "OVER" else "under"
                line = jread.get("call_line") or gctx.get("close_total")
                # LINE MOVEMENT CONTRADICTION FILTER (2026-08-05).
                # Suppress totals where the market has moved AGAINST Jerry's
                # thesis by ≥0.5 runs. LAA@BAL tonight was Jerry UNDER 8.0
                # while the total drifted 8.0 → 9.5 — edge (if any) evaporated
                # and the sharp side is now opposite ours.
                try:
                    from validate_jerry_read import validate_line_movement
                    lm = validate_line_movement(ptype, line, gctx.get("current_total"))
                    if lm['contradicts']:
                        print(f"  🚫 sweat-card line-move filter: skipped {label} — {lm['reason']}")
                        continue
                except ImportError:
                    pass
            else:
                continue  # spread + others + nrfi/yrfi: skip; NRFI/YRFI handled below
            game_side_candidates.append({
                "type": "ML" if mkt == "ml" else "Over/Under",
                "icon": icon,
                "label": label,
                "game": f"{gctx.get('away_team')} @ {gctx.get('home_team')}",
                "conviction": conv,
                "tier": tier,
                "tier_source": "jerry_synthesis",
                "source_table": "mlb_game_results",
                "source_key": gid,
                "eval": {"type": ptype, "side": label,
                         "line": line,
                         "home_team": gctx.get("home_team"),
                         "away_team": gctx.get("away_team")},
                "narrative_hint": (jread.get("short_read") or call_text)[:200],
            })
    except Exception as _e:
        print(f"  ⚠ jerry-driven game-side selection failed: {_e}")

    # 4b. NRFI/YRFI ensemble → game-side candidates (2026-08-06).
    # Bridged directly from mlb_game_context.nrfi_ensemble_* fields.
    # Ensemble tier converts to conviction: PRIME→80, STRONG→72, LEAN→55.
    # LEAN skipped for card (needs conv >= 70 gate). Not published if
    # POTD already surfaced the same NRFI/YRFI pick.
    _tier_to_conv = {"PRIME": 80, "STRONG": 72, "LEAN": 55}
    for g in games:
        pick = g.get("nrfi_ensemble_pick")
        tier = (g.get("nrfi_ensemble_tier") or "").upper()
        if not pick or tier not in ("PRIME", "STRONG"):
            continue
        conv = _tier_to_conv[tier]
        pick_upper = pick.upper()
        if pick_upper not in ("NRFI", "YRFI"):
            continue
        # Dedup with POTD (which often picks the top NRFI)
        if any(p.get("type") == "POTD" and pick_upper in str(p.get("label","")).upper()
               for p in picks):
            continue
        matchup = f'{g.get("away_team")} @ {g.get("home_team")}'
        icon = "🔒" if pick_upper == "NRFI" else "🔥"
        label = f'{pick_upper} — {matchup}'
        game_side_candidates.append({
            "type": pick_upper,
            "icon": icon,
            "label": label,
            "game": matchup,
            "conviction": conv,
            "tier": tier,
            "tier_source": "nrfi_ensemble",
            "source_table": "mlb_game_results",
            "source_key": g.get("game_id"),
            "eval": {
                "type": pick_upper.lower(),
                "side": pick_upper,
                "line": None,
                "home_team": g.get("home_team"),
                "away_team": g.get("away_team"),
            },
            "narrative_hint": (g.get("nrfi_ensemble_reason") or "")[:200],
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
            # Phase 1: prop tier comes from generate_props.tier_for() which
            # uses per-prop-type conviction thresholds. Note these are NOT
            # the same scale as side/total resolver tiers — see
            # engine_clarity_refactor.md Phase 2 for the unification plan.
            "tier_source": "prop_pipeline_conviction",
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
        # 2026-08-11: odds-range gate — skip props priced outside [-300, +150]
        if not _odds_within_range(prop):
            continue
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
        # 2026-08-11: odds-range gate — skip props priced outside [-300, +150]
        if not _odds_within_range(prop):
            continue
        if _is_high_juice_hits_over(prop) and (prop.get("conviction") or 0) < HITS_OVER_05_CARD_CONV_FLOOR:
            continue
        player = prop.get("player_name")
        if player in seen_players:
            continue
        seen_players.add(player)
        add(_build_prop_pick(prop))

    return picks[:8]


def _fetch_all_game_reads(game_date: str) -> list:
    """Pull every non-PASS Jerry game read for the slate, sorted by conviction.

    Powers the sweat card's 'game_reads' section — per-game analyst content
    surfaced regardless of whether the read made the featured top_8. Persona
    shift 2026-08-06 means every game gets a directional take; this exposes
    those takes to the app for game-list rendering.

    Tier is derived from conviction (30+ = READ, 50+ = LEAN, 65+ = STRONG,
    80+ = PRIME) so the app can style each entry appropriately.
    """
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/jerry_reads",
            headers=HEADERS,
            params={
                "sport": "eq.MLB",
                "game_date": f"eq.{game_date}",
                "call_market": "not.eq.pass",
                "select": "game_id,call_market,call_side,call_line,call_text,"
                          "conviction,short_read",
                "order": "conviction.desc",
            },
            timeout=15,
        )
        rows = r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f"  ⚠ game_reads fetch failed: {e}")
        return []

    out = []
    for row in rows:
        conv = row.get("conviction") or 0
        if conv < 30:
            continue
        if conv >= 80:   tier = "PRIME"
        elif conv >= 65: tier = "STRONG"
        elif conv >= 50: tier = "LEAN"
        else:            tier = "READ"
        row["tier"] = tier
        out.append(row)
    return out


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

    # Unified TOP PROPS with DIVERSITY CAP (2026-06-06 permanent fix).
    #
    # Before: top 8 by conviction, no type cap. Result was hits_under or
    # hits_over heavy slates (6+ of the same prop type in top_8) which
    # users called out as "not spreading the love." Same single edge bet
    # 6 times = juice multiplies + concentration risk.
    #
    # Now: max 3 picks per prop_type within top_props, ranked by conviction.
    # If a type runs out, fall through to next type. Guarantees the surface
    # mixes hits / Ks / BB / HA / ER / outs / total bases.
    #
    # Hits_over 0.5 also runs through _is_high_juice_hits_over gate (conv
    # ≥ HITS_OVER_05_CARD_CONV_FLOOR) — same juice protection as the
    # top_props_by_type helper above.
    MAX_PER_TYPE_IN_TOP_PROPS = 3
    # Re-rank by (tier × prop_type) live hit rate — added 2026-06-15 after
    # the prop_tier_x_type analysis (n=1,213 over 30d) found huge variance:
    # outs_under 96% vs PRIME bb_under 53% vs outs_over 36%. Conviction
    # score alone didn't capture this. Now: boost EDGE types, demote
    # CALIBRATED, fade COINFLIP/FADE — independent of conviction.
    # See [[project_prop_tier_x_type_615]].
    try:
        from read_live_tier_record import prop_score_multiplier, prop_edge_class
    except Exception:
        prop_score_multiplier = lambda *a, **k: 1.0
        prop_edge_class = lambda *a, **k: "INSUFFICIENT"

    def _adjusted_conviction(p):
        """Return conviction × tier×type multiplier. Drives the re-sort."""
        base = p.get("conviction") or 0
        mult = prop_score_multiplier(p.get("prop_type"), tier=p.get("tier"))
        return base * mult

    # Sort props by adjusted conviction so high-edge tier×type combos
    # surface first, fade combos drop.
    props_sorted = sorted(props, key=lambda p: -_adjusted_conviction(p))

    top_props_all = []
    type_counts = {}
    for p in props_sorted:
        if p.get("tier") not in ("PRIME", "STRONG"):
            continue
        ptype = p.get("prop_type", "?")
        # 2026-08-11: hard odds-range gate per feedback_prop_jerry_odds.
        # Props outside [-300, +150] on the direction we're taking are
        # filtered — juice too heavy at extreme -odds, payout too thin at
        # extreme +odds. Caught 4 batter hits at -300 to -475 today.
        if not _odds_within_range(p):
            continue
        # Juice gate for high-juice hits_over 0.5
        if _is_high_juice_hits_over(p):
            if (p.get("conviction") or 0) < HITS_OVER_05_CARD_CONV_FLOOR:
                continue
        # 30/60d hit-rate gate — same auto-suppression curate_top_8 uses.
        # Hits_under STRONG is at 53% lifetime (52% 8-day) and was leaking
        # onto the card via this surface because top_props_all bypassed
        # eligibility. Routed through _cohort_eligibility to fix.
        elig = _cohort_eligibility(p, gate_window="30d")
        if elig == "suppress":
            continue
        if elig == "demote" and (p.get("tier") or "").upper() == "STRONG":
            # demote STRONG to LEAN here would drop them out of the surface anyway
            continue
        # Hard filter: tier×type combos with FADE class (<45% hit rate)
        # never make the card regardless of conviction. e.g. outs_over
        # at 36% hit rate over n=14 — engine has been wrong on this combo.
        edge_class = prop_edge_class(ptype, tier=p.get("tier"))
        if edge_class == "FADE":
            continue
        if type_counts.get(ptype, 0) >= MAX_PER_TYPE_IN_TOP_PROPS:
            continue
        # Stamp the live hit-rate band so downstream consumers (Jerry
        # reads, app card) can surface "this prop type hits 96% live".
        p["_tier_type_edge_class"] = edge_class
        p["_tier_type_multiplier"] = round(prop_score_multiplier(ptype, tier=p.get("tier")), 2)
        top_props_all.append(p)
        type_counts[ptype] = type_counts.get(ptype, 0) + 1
        if len(top_props_all) >= 8:
            break

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

    # ─── DEDUP: top_props excludes anything already in top_8 ──────────────
    # (added 2026-06-18, fixed 2026-06-27)
    # Same principle as ladder-separate-from-public-5: surfaces shouldn't show
    # the same pick twice. If a prop is in top_8 (the curated card lead), it
    # shouldn't also appear in top_props (the "other props worth a look" list).
    #
    # 2026-06-27 fix: the prior label-string matching silently failed —
    # top_8 stores label="Dylan Cease Over 7.5 Ks  ·  proj 8.6" but the
    # dedup was building "Dylan Cease Over 7.5 ks over". Neither the tuple
    # match nor the "ks over" substring contains check hit, so Cease was
    # published in BOTH surfaces. Now uses canonical source_key tuple
    # (player_name, prop_type, prop_line as float) which the resolver
    # already uses for grading — single source of truth.
    top_8_prop_keys = set()  # set of (player_name, prop_type, float(prop_line))
    for pick_8 in top_8_curated:
        if not pick_8.get("type", "").startswith("prop_"):
            continue
        sk = pick_8.get("source_key") or ""
        parts = sk.split("|")
        if len(parts) < 3:
            continue
        try:
            top_8_prop_keys.add((parts[0], parts[1], float(parts[2])))
        except (ValueError, TypeError):
            continue

    def _prop_key(p):
        """Canonical (player, prop_type, line) tuple — matches top_8 source_key shape."""
        try:
            line = float(p.get("prop_line")) if p.get("prop_line") is not None else None
        except (TypeError, ValueError):
            line = p.get("prop_line")
        return (p.get("player_name"), p.get("prop_type"), line)

    if top_8_prop_keys:
        before_count = len(top_props_all)
        top_props_all = [p for p in top_props_all if _prop_key(p) not in top_8_prop_keys]
        # Backfill from props_sorted to maintain ~5-7 picks if dedup shrunk us
        # 2026-08-11: MUST re-apply the same juice gate + eligibility gate as
        # the initial pass. Prior implementation bypassed both, letting the
        # 4 batter hits_over 0.5 conv 72-87 picks (below the 95 floor) leak
        # onto the card today. User feedback: batter hits at -180+ juice is
        # fine WHEN data quality is really solid — conviction >= 95 encodes
        # that (base scorer only awards top conv on l7_hot + l14_heat +
        # lineup_spot + opp_bullpen signal stack).
        if len(top_props_all) < 5 and len(top_props_all) < before_count:
            existing_keys = {_prop_key(p) for p in top_props_all}
            for p in props_sorted:
                if (p.get("tier") not in ("PRIME", "STRONG")
                        and (p.get("tier") or "").upper() != "LEAN"):
                    continue
                k = _prop_key(p)
                if k in existing_keys or k in top_8_prop_keys:
                    continue
                # Odds-range gate — same as initial pass
                if not _odds_within_range(p):
                    continue
                # Juice gate — same as initial pass line 1613
                if _is_high_juice_hits_over(p):
                    if (p.get("conviction") or 0) < HITS_OVER_05_CARD_CONV_FLOOR:
                        continue
                # Eligibility gate — same as initial pass line 1620
                elig = _cohort_eligibility(p, gate_window="30d")
                if elig == "suppress":
                    continue
                if elig == "demote" and (p.get("tier") or "").upper() == "STRONG":
                    continue
                ptype = p.get("prop_type", "?")
                edge_class = prop_edge_class(ptype, tier=p.get("tier"))
                if edge_class == "FADE":
                    continue
                p["_tier_type_edge_class"] = edge_class
                p["_tier_type_multiplier"] = round(prop_score_multiplier(ptype, tier=p.get("tier")), 2)
                top_props_all.append(p)
                existing_keys.add(k)
                if len(top_props_all) >= 7:
                    break
        print(f"  ✓ top_props dedup vs top_8: {before_count} → {len(top_props_all)} (excluded {before_count - len(top_props_all)} overlap{'s' if before_count - len(top_props_all) != 1 else ''})")

    # 2026-06-21 dedup extended to the per-prop-type top lists
    # (top_hits_over / top_hits_under / top_ks_over / top_ks_under).
    # Without this the same Gallen Ks UNDER PRIME pick could appear on
    # the sweat card top_8 AND in the "Top Ks Under" section below it
    # — user feedback "redundant, listed twice." Each list is filtered
    # against the same canonical source_key set.
    def _filter_against_top_8(plist):
        if not top_8_prop_keys:
            return plist
        return [p for p in plist if _prop_key(p) not in top_8_prop_keys]
    top_hits = _filter_against_top_8(top_hits)
    top_ks = _filter_against_top_8(top_ks)
    top_under_hits = _filter_against_top_8(top_under_hits)
    top_under_ks = _filter_against_top_8(top_under_ks)

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

    # 2026-08-09: run correlation_check across the finalized card picks so
    # the app can render "correlation warning" badges on picks that share
    # a same-game or same-pitcher risk (R1-R7 rules — see correlation_check).
    # Doesn't remove picks, just annotates. Sport-universal.
    try:
        from correlation_check import check_correlations
        # Build one flat pick list for correlation scan (top_8 + top_props)
        corr_input = []
        for entry in (top_8_curated or []) + (top_props_all or []):
            corr_input.append({
                'game_id':   entry.get('game_id'),
                'market':    entry.get('market') or ('prop' if entry.get('prop_type') else None),
                'side':      entry.get('side') or entry.get('direction'),
                'line':      entry.get('line') or entry.get('prop_line'),
                'prop_type': entry.get('prop_type'),
                'prop_line': entry.get('prop_line'),
                'player_name': entry.get('player_name'),
                'conviction': entry.get('conviction') or entry.get('refit_conviction') or 0,
            })
        # Build pitcher_map (game_id → home/away pitcher) from games ctx
        pitcher_map = {}
        for g in games:
            gid = g.get('game_id')
            if gid:
                pitcher_map[gid] = {'home_pitcher': g.get('home_pitcher'),
                                    'away_pitcher': g.get('away_pitcher')}
        corr_warnings = check_correlations(corr_input, pitcher_map=pitcher_map)
        # Annotate each affected pick with correlation warning
        for w in corr_warnings:
            for idx in w.get('picks', []):
                if idx >= len(corr_input): continue
                src = corr_input[idx]
                # Find corresponding entry in top_8_curated or top_props_all
                targets = [top_8_curated, top_props_all]
                for tgt_list in targets:
                    if not tgt_list: continue
                    if idx < len(tgt_list):
                        item = tgt_list[idx if idx < len(tgt_list) else 0]
                        item.setdefault('correlation_warnings', []).append({
                            'rule': w.get('rule'),
                            'severity': w.get('severity'),
                            'note': w.get('note'),
                        })
        if corr_warnings:
            print(f'  ⚠ correlation_check: {len(corr_warnings)} warning(s) tagged onto card picks')
    except Exception as e:
        print(f'  ⚠ correlation_check skipped: {e}')

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
                # Phase 1 namespacing (2026-06-18): explicit attribution so
                # downstream readers don't confuse DAWG-specific conviction
                # with sweat_dim or prop_pipeline conviction.
                "tier_source": "dawg_cohort",
                "score_source": "dawg_conviction",
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
        # 2026-08-06 persona shift: every Jerry take on every game.
        # top_8 is FEATURED picks (PRIME/STRONG heavy); game_reads is the
        # per-game analyst content (READ tier and up) that the app renders
        # in the game list so users see Jerry's read even on games he'd
        # PASS as a bet. Independent pull direct from jerry_reads so the
        # data isn't gated by curate_top_8's featured-picks selection.
        "game_reads": _fetch_all_game_reads(today),
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

    # 2026-08-08 freshness metadata: stamp the card with WHEN it was built
    # + the most recent jerry_reads generation timestamp for the slate. App
    # can display "Card as of X" so users know when the picks last refreshed.
    # Prevents the 8/7 CLE-flip-mid-day silent staleness issue.
    jr_latest = None
    try:
        r_j = requests.get(
            f"{SUPABASE_URL}/rest/v1/jerry_reads",
            headers=HEADERS,
            params={"game_date": f"eq.{today}", "sport": "eq.MLB",
                    "order": "generated_at.desc", "limit": "1",
                    "select": "generated_at"},
            timeout=10,
        )
        rows = r_j.json() if r_j.status_code == 200 else []
        if rows: jr_latest = rows[0].get("generated_at")
    except Exception:
        pass
    now_utc = datetime.now(timezone.utc).isoformat()
    card["_card_built_at"] = now_utc
    card["_jerry_reads_latest_at"] = jr_latest
    card["_synced"] = (jr_latest is not None)

    # Upsert to jerry_cache
    cache_key = f"sweat_card_{today}"
    payload = {
        "cache_key": cache_key,
        "game_id": cache_key,
        "sport": "MLB",
        "narrative": f"Sweat Card for {today}",
        "data": card,
        "fetched_at": now_utc,
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
