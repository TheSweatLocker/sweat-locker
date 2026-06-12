"""Lock immutable game-time receipts for published picks.

WHY: When STL @ NYM Over 9.0 goes 8-9 final, we want a frozen record
of what the system claimed BEFORE the result was known: which models
agreed, what the closing line was, which cohort signals fired, which
props were promoted. Without this, post-game audits and social-media
receipts can be retroactively challenged ("you must've updated it
after the game started").

WHAT: On every imminent-games watchdog cycle, capture a receipt for any
game whose first pitch is in the next 15 min OR started in the last 15
min, IF no receipt already exists for that game today. Receipts are
write-once — once stamped they never update.

STORAGE: jerry_cache row keyed `receipts_{date}` containing an array of
per-game receipt dicts. No schema migration needed.

USAGE: Imported and called from refresh_imminent_games.run() after the
SP fill-in step. Standalone CLI also available for manual capture.
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
WRITE_HEADERS = {
    **HEADERS,
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
}

# Capture window around first pitch — wide enough that a watchdog cycle
# always catches each game once, narrow enough that we don't waste calls
# on games hours away.
CAPTURE_PRE_MIN = 15   # capture up to 15 min before first pitch
CAPTURE_POST_MIN = 15  # capture up to 15 min after first pitch (catch
                       # games that started while watchdog was idle)


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _et_today():
    return _et_now().strftime("%Y-%m-%d")


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def fetch_today_contexts(date_str):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context",
        params={"game_date": f"eq.{date_str}", "select": "*"},
        headers=HEADERS,
        timeout=15,
    )
    return r.json() if r.status_code == 200 else []


def fetch_existing_receipts(date_str):
    """Return the existing receipts payload for today, or empty container."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/jerry_cache",
        params={"cache_key": f"eq.receipts_{date_str}", "select": "data"},
        headers=HEADERS,
        timeout=10,
    )
    if r.status_code != 200:
        return {"date": date_str, "receipts": []}
    rows = r.json()
    if not rows:
        return {"date": date_str, "receipts": []}
    raw = rows[0].get("data")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"date": date_str, "receipts": []}
    return raw or {"date": date_str, "receipts": []}


def fetch_potd(date_str):
    """Return the published POTD payload, or None."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/jerry_cache",
        params={"cache_key": f"eq.best_bet_{date_str}", "select": "data"},
        headers=HEADERS,
        timeout=10,
    )
    if r.status_code != 200:
        return None
    rows = r.json()
    if not rows:
        return None
    return rows[0].get("data")


def fetch_prime_props(date_str):
    """Return list of PRIME-tier props grouped by matchup."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props",
        params={
            "game_date": f"eq.{date_str}",
            "tier": "in.(PRIME,STRONG)",
            "select": "matchup,player_name,prop_type,prop_line,direction,tier,book_over_odds,book_under_odds",
        },
        headers=HEADERS,
        timeout=10,
    )
    if r.status_code != 200:
        return {}
    grouped = {}
    for p in r.json():
        m = p.get("matchup")
        if not m:
            continue
        grouped.setdefault(m, []).append({
            "player": p.get("player_name"),
            "prop": f"{p.get('prop_type')} {p.get('prop_line')}",
            "direction": p.get("direction"),
            "tier": p.get("tier"),
            "odds": (p.get("book_over_odds") if (p.get("direction") or "").lower() == "over"
                     else p.get("book_under_odds")),
        })
    return grouped


def fetch_mlb_schedule(date_str):
    """Return MLB API schedule data with commence_times."""
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={"sportId": 1, "date": date_str},
            timeout=10,
        )
        return r.json().get("dates", [])
    except Exception:
        return []


def _commence_time_for(ctx, schedule_dates):
    away = (ctx.get("away_team") or "").lower()
    home = (ctx.get("home_team") or "").lower()
    for d in schedule_dates:
        for g in d.get("games", []):
            ma = g.get("teams", {}).get("away", {}).get("team", {}).get("name", "").lower()
            mh = g.get("teams", {}).get("home", {}).get("team", {}).get("name", "").lower()
            if (away in ma or ma in away) and (home in mh or mh in home):
                return g.get("gameDate")
    return None


def _within_capture_window(commence_iso):
    if not commence_iso:
        return False
    try:
        ct = datetime.fromisoformat(commence_iso.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    now = datetime.now(timezone.utc)
    minutes_until = (ct - now).total_seconds() / 60
    # Capture window: from 15 min before first pitch through 15 min after.
    return -CAPTURE_POST_MIN <= minutes_until <= CAPTURE_PRE_MIN


def build_receipt(ctx, prime_props_for_matchup, potd_data, commence_iso):
    """Produce one immutable receipt dict from a game context + sidecars."""
    matchup = f"{ctx.get('away_team')} @ {ctx.get('home_team')}"
    sb = ctx.get("sweat_breakdown")
    if isinstance(sb, str):
        try:
            sb = json.loads(sb)
        except json.JSONDecodeError:
            sb = {}
    sb = sb or {}
    resolver_total = (sb.get("dimensions") or {}).get("resolver_total") or {}
    resolver_side = (sb.get("dimensions") or {}).get("resolver_side") or {}

    is_potd = False
    potd_block = None
    if potd_data and isinstance(potd_data, dict):
        potd_game = potd_data.get("game") or {}
        if (potd_game.get("home_team") == ctx.get("home_team")
                and potd_game.get("away_team") == ctx.get("away_team")):
            is_potd = True
            potd_block = {
                "lean_display": potd_data.get("leanDisplay"),
                "confidence": potd_data.get("confidence"),
                "sweat_score": (potd_data.get("score") or {}).get("total"),
            }

    return {
        "game_id": ctx.get("game_id"),
        "matchup": matchup,
        "home_team": ctx.get("home_team"),
        "away_team": ctx.get("away_team"),
        "first_pitch_iso": commence_iso,
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "is_potd": is_potd,
        "potd": potd_block,
        "lines": {
            "open_total": ctx.get("open_total"),
            "close_total": ctx.get("close_total"),
            "open_spread": ctx.get("open_spread"),
            "close_spread": ctx.get("close_spread"),
            "home_ml_open": ctx.get("home_ml_open"),
            "home_ml_close": ctx.get("home_ml_close"),
            "away_ml_open": ctx.get("away_ml_open"),
            "away_ml_close": ctx.get("away_ml_close"),
        },
        "models": {
            "v3_total": ctx.get("projected_total"),
            "v4_total": ctx.get("model_pred_total"),
            "jerry_total": ctx.get("jerry_pred_total"),
            "v3_spread": ctx.get("projected_spread"),
            "v4_spread": ctx.get("model_pred_spread"),
            "jerry_spread": ctx.get("jerry_pred_spread"),
        },
        "resolver": {
            "total": {
                "tier": resolver_total.get("tier"),
                "direction": resolver_total.get("direction"),
                "reason": resolver_total.get("reason"),
            } if resolver_total else None,
            "side": {
                "tier": resolver_side.get("tier"),
                "direction": resolver_side.get("direction"),
                "reason": resolver_side.get("reason"),
            } if resolver_side else None,
        },
        "sweat": {
            "score": ctx.get("sweat_score"),
            "tier": ctx.get("sweat_tier"),
            "tier_max": ctx.get("sweat_tier_max"),
        },
        "signal_confluence_net": ctx.get("signal_confluence_net"),
        "nrfi_score": ctx.get("nrfi_score"),
        "pitchers": {
            "home": ctx.get("home_pitcher"),
            "away": ctx.get("away_pitcher"),
            "home_xera": ctx.get("home_sp_xera"),
            "away_xera": ctx.get("away_sp_xera"),
        },
        "prime_props": prime_props_for_matchup or [],
        "umpire": ctx.get("umpire"),
        "weather": {
            "temp": ctx.get("temperature"),
            "wind_speed": ctx.get("wind_speed"),
            "wind_dir": ctx.get("wind_direction"),
        },
    }


def persist_receipts(date_str, receipts_payload):
    body = {
        "cache_key": f"receipts_{date_str}",
        "game_id": f"receipts_{date_str}",
        "sport": "mlb",
        "narrative": "",
        "data": json.dumps(receipts_payload),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=cache_key",
        headers=WRITE_HEADERS, json=body, timeout=15,
    )
    return r.status_code in (200, 201, 204)


def run(dryrun=False, force_matchups=None):
    """force_matchups: list of "Away @ Home" strings to capture regardless
    of the window. Use only for manual backfill — bypasses the immutability
    intent of in-window captures."""
    date_str = _et_today()
    print(f"[lock_game_receipts] capture pass — {date_str} {_et_now().strftime('%H:%M ET')}")

    contexts = fetch_today_contexts(date_str)
    if not contexts:
        print("  no mlb_game_context rows — nothing to capture")
        return

    existing = fetch_existing_receipts(date_str)
    existing_ids = {r.get("game_id") for r in existing.get("receipts", []) if r.get("game_id")}
    print(f"  {len(contexts)} games on slate, {len(existing_ids)} already locked")

    schedule_dates = fetch_mlb_schedule(date_str)
    potd_data = fetch_potd(date_str)
    prime_props = fetch_prime_props(date_str)

    force_set = set(force_matchups or [])
    new_receipts = []
    for ctx in contexts:
        gid = ctx.get("game_id")
        if not gid or gid in existing_ids:
            continue
        commence_iso = _commence_time_for(ctx, schedule_dates)
        matchup = f"{ctx.get('away_team')} @ {ctx.get('home_team')}"
        if matchup not in force_set and not _within_capture_window(commence_iso):
            continue
        matchup = f"{ctx.get('away_team')} @ {ctx.get('home_team')}"
        receipt = build_receipt(
            ctx, prime_props.get(matchup, []), potd_data, commence_iso,
        )
        new_receipts.append(receipt)
        flag = " 🔒 POTD" if receipt["is_potd"] else ""
        forced = " (forced)" if matchup in force_set else ""
        print(f"  📸 locking {matchup}{flag}{forced} — first pitch {(commence_iso or '?')[:16]}")

    if not new_receipts:
        print("  no games in capture window — done")
        return

    if dryrun:
        print(f"[lock_game_receipts] DRYRUN — would write {len(new_receipts)} receipt(s)")
        return

    # Append new receipts to existing payload, preserving immutability of
    # already-locked entries. Receipts already in existing_ids are never
    # rewritten — that's the whole point.
    merged = {
        "date": date_str,
        "receipts": existing.get("receipts", []) + new_receipts,
        "last_capture": datetime.now(timezone.utc).isoformat(),
    }
    if persist_receipts(date_str, merged):
        print(f"  ✅ persisted {len(new_receipts)} new receipt(s) (total {len(merged['receipts'])} today)")
    else:
        print(f"  ⚠ persist FAILED")


if __name__ == "__main__":
    # CLI: --dryrun to skip writes. --force="Away @ Home,Other @ Foo" to
    # backfill specific matchups outside the time window.
    forced = []
    for arg in sys.argv[1:]:
        if arg.startswith("--force="):
            forced = [m.strip() for m in arg.split("=", 1)[1].split(",") if m.strip()]
    run(dryrun="--dryrun" in sys.argv, force_matchups=forced)
