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
    """Pull live audited tier rates from mlb_tier_calibration (30d window)."""
    rows = sb_get("mlb_tier_calibration", {
        "window_label": "eq.30d",
        "sport": "eq.mlb",
        "select": "tier,hits,total,hit_rate",
    })
    return {r["tier"]: r for r in rows}


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
    """Yesterday's POTD + Dawg results for thin/empty-day track-record content."""
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

    return recap


def fetch_upcoming_events():
    """Next 7 days of high-signal events the app can preview on quiet days.

    Currently UFC card + tomorrow's MLB pitcher matchups. Extends naturally
    as NFL / NCAAB / NHL pipelines come online — each gets its own probe."""
    today = today_et()
    horizon = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d")
    events = []

    # Upcoming UFC card
    ufc_rows = sb_get("ufc_picks", {
        "event_date": f"gte.{today}",
        "select": "event_name,event_date,fight_order,fighter_a,fighter_b,tier_winner,recommended_side",
        "order": "event_date.asc,fight_order.asc",
        "limit": "12",
    })
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
            "select": "player_name,prop_type,prop_line,direction,tier,conviction,signals,matchup",
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


def find_total_edges(games, min_delta=1.5):
    """Find games where the model projects a total meaningfully different
    from the market line. |projected_total - close_total| >= min_delta.
    Returns top 2 by absolute delta. No calibrated cohort yet — these go
    in the Sweat Card with a neutral 60% prior.

    Direction follows model: model > market => OVER lean, < market => UNDER."""
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
        candidates.append({
            "game": f"{g.get('away_team')} @ {g.get('home_team')}",
            "projected_total": round(float(pt), 1),
            "close_total": round(float(ct), 1),
            "delta": round(delta, 2),
            "direction": "OVER" if delta > 0 else "UNDER",
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

    # Stack alert detection — find games where 4+ hits picks are PRIME
    stack_games = {}
    for p in props:
        if p.get("prop_type") in ("hits_over", "hits_under") and p.get("conviction", 0) >= 82:
            mu = p.get("matchup", "")
            stack_games[mu] = stack_games.get(mu, 0) + 1
    stack_alerts = [{"matchup": mu, "prime_count": n} for mu, n in stack_games.items() if n >= 4]

    # Padding for thin / empty days — keeps the card useful when slate is
    # MLB-only-July-Tuesday or post-season-only-MLB. Standard days skip these
    # fetches to avoid wasted Supabase reads.
    audit_roll_up = None
    yesterday_recap = None
    upcoming_events = None
    if density["mode"] in ("thin", "empty"):
        audit_roll_up = fetch_audit_roll_up()
        yesterday_recap = fetch_yesterday_recap()
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
        "top_hits_over": top_hits,
        "top_ks_over": top_ks,
        "top_hits_under": top_under_hits,
        "top_ks_under": top_under_ks,
        "bucket_angle": bucket,
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
