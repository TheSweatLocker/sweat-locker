"""
Read-side helper for cohort stats — call get_cohort(name) at render time
to surface a fresh win-rate label instead of a hardcoded number.

DATA SOURCE: jerry_cache row with cache_key='cohort_stats', refreshed
nightly by cohort_stats.py. In-process cached so the next call within
the same render hits zero network. Falls back gracefully to None when:
  - the row is missing
  - the data is older than _FRESHNESS_HOURS
  - the cohort's sample is below min_n
  - the Supabase fetch fails for any reason

Callers MUST handle None and surface a generic label instead of a number.

USAGE:
    from cohort_lookup import get_cohort, format_label

    c = get_cohort("conf4_dog_rl")
    if c:
        label = f"{c['win_pct']}% DOG RL (n={c['n']})"
    else:
        label = "DOG RL cohort"   # silent graceful fallback
"""
import json
import os
from datetime import datetime, timezone, timedelta

import requests

# Defensive load — works whether the caller has already invoked load_dotenv
# or not. No-op if env is already populated.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
CACHE_KEY = "cohort_stats"

_CACHE = {"loaded_at": None, "data": None}
_FRESHNESS_HOURS = 36  # 36h cutoff — nightly cron runs ~24h apart, so a
                       # single skipped run still surfaces real numbers


def _load():
    """Fetch the cohort_stats row from jerry_cache once per process.
    Returns the parsed dict, or None on any failure."""
    if _CACHE["loaded_at"] is not None:
        return _CACHE["data"]
    _CACHE["loaded_at"] = datetime.now(timezone.utc)
    if not (SUPABASE_URL and SUPABASE_KEY):
        _CACHE["data"] = None
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/jerry_cache",
            params={"cache_key": f"eq.{CACHE_KEY}",
                    "select": "data,created_at"},
            headers={"apikey": SUPABASE_KEY,
                     "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=10,
        )
        if r.status_code != 200:
            _CACHE["data"] = None
            return None
        rows = r.json()
        if not rows:
            _CACHE["data"] = None
            return None
        data = rows[0].get("data")
        if isinstance(data, str):
            data = json.loads(data)
        _CACHE["data"] = data if isinstance(data, dict) else None
    except (requests.RequestException, json.JSONDecodeError, ValueError, KeyError):
        _CACHE["data"] = None
    return _CACHE["data"]


def _is_fresh(entry):
    """True iff the entry was computed within _FRESHNESS_HOURS."""
    if not entry:
        return False
    try:
        computed_at = datetime.fromisoformat(entry["computed_at"])
        age = datetime.now(timezone.utc) - computed_at
        return age <= timedelta(hours=_FRESHNESS_HOURS)
    except (KeyError, ValueError, TypeError):
        return False


def get_cohort(name, min_n=10):
    """Return cohort entry { win_pct, n, computed_at, ... } when fresh AND
    has at least `min_n` samples. Otherwise None.

    Callers should use:
        c = get_cohort('conf4_dog_rl')
        if c:
            label = f"{c['win_pct']}% (n={c['n']})"
        else:
            label = "lifetime cohort"   # generic fallback, never a stale %
    """
    data = _load()
    if not data:
        return None
    entry = data.get(name)
    if not entry:
        return None
    if not _is_fresh(entry):
        return None
    if (entry.get("n") or 0) < min_n:
        return None
    if entry.get("win_pct") is None:
        return None
    return entry


def format_label(name, min_n=30, fallback=""):
    """Convenience: return a `{pct}% (n={n})` string when fresh AND n>=min_n.
    Else `fallback`. For inline use in driver strings.

    2026-06-18: default min_n raised from 10 -> 30 to match the
    quotability threshold in feedback_sample_size_with_pct. Below 30,
    the % is omitted entirely so users never see false-precision
    percentages backed by thin samples.

    Example:
        f"PEAK confluence ({format_label('conf4_dog_rl', fallback='lifetime cohort')})"
    """
    c = get_cohort(name, min_n=min_n)
    if not c:
        return fallback
    return f"{c['win_pct']}% (n={c['n']})"
