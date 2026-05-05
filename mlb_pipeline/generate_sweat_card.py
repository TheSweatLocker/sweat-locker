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

    games = fetch_game_context()
    print(f"  {len(games)} games on slate")

    tier_rates = fetch_tier_rates()
    potd = fetch_potd()
    dawg = fetch_dawg()
    props = fetch_top_props()

    nrfi_lock = find_nrfi_lock(games, tier_rates)
    yrfi_lock = find_yrfi_lock(games, tier_rates)
    bucket = find_bucket_angle(games)
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

    card = {
        "slate_date": today,
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
        "stack_alerts": stack_alerts,
        "skip_alerts": skip_alerts,
        "tier_rates_30d": {
            "nrfi_prime_90_94": tier_rates.get("nrfi_prime_90_94"),
            "yrfi_lean_le40": tier_rates.get("yrfi_lean_le40"),
            "nrfi_volatile_95plus": tier_rates.get("nrfi_volatile_95plus"),
            "spread_delta_ge2": tier_rates.get("spread_delta_ge2"),
        },
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
              f"stacks={len(stack_alerts)}, skips={len(skip_alerts)}")
    else:
        print(f"❌ Sweat Card upsert failed {r.status_code}: {r.text[:200]}")


if __name__ == "__main__":
    build_card()
