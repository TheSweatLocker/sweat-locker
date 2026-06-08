"""
Read-side helper for L5 pitcher actuals. Call get_l5(game_date, game_id)
at render time to access the last-5-starts data fetched nightly by
compute_l5_pitcher_actuals.py.

DATA SOURCE: jerry_cache row 'pitcher_l5_{game_date}_{game_id}'.

USAGE:
    from pitcher_l5_lookup import get_l5

    l5 = get_l5(game_date, game_id)
    if l5:
        away_avg_outs = (l5.get('away') or {}).get('avg', {}).get('outs')
        # Compare to projection: if L5 avg + projection both lean same
        # direction, strengthen the play. If they disagree, fade.

Or higher-level helpers:
    confirms_over(side_pitcher_l5, metric, line) -> bool/None
    confirms_under(side_pitcher_l5, metric, line) -> bool/None
"""
import json
import os
from datetime import datetime, timezone, timedelta

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

_CACHE = {}  # {(game_date, game_id): data}
_FRESHNESS_HOURS = 36


def _fetch_row(cache_key):
    if not (SUPABASE_URL and SUPABASE_KEY):
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/jerry_cache",
            params={"cache_key": f"eq.{cache_key}",
                    "select": "data,created_at"},
            headers={"apikey": SUPABASE_KEY,
                     "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=10,
        )
        if r.status_code != 200 or not r.json():
            return None
        row = r.json()[0]
        # Freshness check
        try:
            created = datetime.fromisoformat(row.get("created_at", ""))
            if datetime.now(timezone.utc) - created > timedelta(hours=_FRESHNESS_HOURS):
                return None
        except (ValueError, TypeError):
            pass
        data = row.get("data")
        if isinstance(data, str):
            data = json.loads(data)
        return data if isinstance(data, dict) else None
    except (requests.RequestException, json.JSONDecodeError, ValueError):
        return None


def get_l5(game_date, game_id):
    """Returns {'home': {...}, 'away': {...}} or None.

    Each side dict (when present): {name, mlb_id, starts: [...], avg: {outs,
    ks, bb, hits, er}, n_starts}.
    """
    if not (game_date and game_id):
        return None
    key = (game_date, game_id)
    if key in _CACHE:
        return _CACHE[key]
    cache_key = f"pitcher_l5_{game_date}_{game_id}"
    data = _fetch_row(cache_key)
    _CACHE[key] = data
    return data


def confirms_direction(side_l5, metric, line, direction):
    """True iff the pitcher's L5 average for `metric` (outs|ks|bb|hits|er)
    is on the SAME side of `line` as `direction` ('over'|'under').

    Returns None when L5 data is missing — caller should treat None as
    "no signal" (not a fade).

    Example:
        l5 = get_l5(...)
        away_l5 = l5.get('away')
        # Bet: Flaherty hits allowed OVER 4.5
        if confirms_direction(home_l5, 'hits', 4.5, 'over'):
            # L5 avg 6.0 vs 4.5 line → same direction as the bet → BOOST
            ...
    """
    if not side_l5:
        return None
    avg = (side_l5.get("avg") or {}).get(metric)
    if avg is None or line is None:
        return None
    if direction == "over":
        return avg > line
    if direction == "under":
        return avg < line
    return None


def streak_count(side_l5, metric, line, direction):
    """How many of the L5 starts hit on the same side as the bet.
    Returns (hits, total) or (None, 0) when missing.

    Useful for: 'Baz outs over 17.5: hit 4 of last 5 starts' framing.
    """
    if not side_l5:
        return (None, 0)
    starts = side_l5.get("starts") or []
    total = 0
    hits = 0
    for s in starts:
        v = s.get(metric)
        if v is None:
            continue
        total += 1
        if direction == "over" and v > line:
            hits += 1
        elif direction == "under" and v < line:
            hits += 1
    return (hits, total) if total else (None, 0)
