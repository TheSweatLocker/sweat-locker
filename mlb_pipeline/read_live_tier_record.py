"""Read jerry_cache.live_tier_records and format for pick recommendations.

Phase 2 of the live-track-record framework. Phase 1 (track_live_tier_record.py)
writes the data; this reads it back in a usable shape so any pick recommendation
can cite the actual hit rate alongside the tier name.

Two surfaces:
  1. CLI:   `python read_live_tier_record.py` → human-readable table
  2. JSON:  `python read_live_tier_record.py --json` → structured payload
            usable by downstream Python (game_reads, recommendations, etc.)
  3. Helper functions for inline import:
       get_record(category, tier, window='30d') → dict or None
       format_inline(category, tier) → "STRONG side, live 1-5 (16.7% over n=6 / 7d)"
"""
import argparse
import json
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}

CACHE_KEY = "live_tier_records"
_PAYLOAD_CACHE = {}  # in-process memoization


def _fetch():
    if "payload" in _PAYLOAD_CACHE:
        return _PAYLOAD_CACHE["payload"]
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/jerry_cache",
        params={"cache_key": f"eq.{CACHE_KEY}", "select": "data,fetched_at"},
        headers=HEADERS, timeout=10,
    )
    if r.status_code != 200 or not r.json():
        return None
    row = r.json()[0]
    raw = row.get("data")
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
    else:
        data = raw
    if isinstance(data, dict):
        data["_fetched_at"] = row.get("fetched_at")
    _PAYLOAD_CACHE["payload"] = data
    return data


def get_record(category, tier, window="30d"):
    """Return {n, actionable, w, l, p, pct} or None. window in '7d','30d','lifetime'."""
    payload = _fetch()
    if not payload:
        return None
    cat = payload.get("categories", {}).get(category.upper())
    if not cat:
        return None
    tier_data = cat.get(tier.upper())
    if not tier_data:
        return None
    return tier_data.get(window)


def format_inline(category, tier, window="30d"):
    """Return one-line annotation for use in pick writeups.
    Returns '' if no data yet so callers can append unconditionally."""
    rec = get_record(category, tier, window)
    if not rec or rec.get("actionable", 0) == 0:
        return f"(live: no actionable picks yet)"
    return (f"(live {window}: {rec['w']}-{rec['l']} "
            f"{rec['pct']}% over n={rec['actionable']})")


def calibration_flag(category, tier, window="30d"):
    """Return a short calibration signal vs the resolver's tier prior.
    OVER_PERF / UNDER_PERF / CALIBRATED / INSUFFICIENT — for visual flags."""
    rec = get_record(category, tier, window)
    if not rec or (rec.get("actionable") or 0) < 5:
        return "INSUFFICIENT"
    pct = rec.get("pct") or 0
    expected = {
        ("TOTAL", "ELITE"): 75, ("TOTAL", "STRONG"): 64,
        ("TOTAL", "LEAN"): 62,  ("TOTAL", "LIGHT"): 50,
        ("SIDE",  "ELITE"): 75, ("SIDE",  "STRONG"): 65,
        ("SIDE",  "LEAN"): 62,  ("SIDE",  "LIGHT"): 55,
        ("PROP",  "PRIME"): 65, ("PROP",  "STRONG"): 58,
        ("PROP",  "LEAN"): 55,
    }.get((category.upper(), tier.upper()))
    if expected is None:
        return "INSUFFICIENT"
    if pct >= expected + 8:
        return "OVER_PERF"
    if pct <= expected - 8:
        return "UNDER_PERF"
    return "CALIBRATED"


def render_table():
    payload = _fetch()
    if not payload:
        return "(no live_tier_records row yet — run track_live_tier_record.py first)"
    lines = []
    lines.append("=" * 96)
    lines.append("  LIVE (tier × category) TRACK RECORD")
    lines.append(f"  Computed: {payload.get('computed_at','?')[:19]} | Resolver live since: {payload.get('resolver_live_since','?')}")
    lines.append("=" * 96)
    for category in ("TOTAL", "SIDE", "PROP"):
        cat = payload.get("categories", {}).get(category) or {}
        if not cat:
            continue
        lines.append(f"  {category}:")
        for tier in ("ELITE", "STRONG", "LEAN", "LIGHT", "PRIME"):
            stat = cat.get(tier)
            if not stat:
                continue
            life = stat["lifetime"]
            d30 = stat["30d"]
            d7 = stat["7d"]
            flag = calibration_flag(category, tier, "30d")
            flag_marker = {"OVER_PERF": " 🔥", "UNDER_PERF": " ⚠️ ",
                           "CALIBRATED": " ✓", "INSUFFICIENT": ""}.get(flag, "")
            lines.append(
                f"    {tier:<8} life {life['w']:>2}-{life['l']:<2} ({life['pct']!s:>5}%, n={life['actionable']:<3}) | "
                f"30d {d30['w']:>2}-{d30['l']:<2} ({d30['pct']!s:>5}%) | "
                f"7d {d7['w']:>2}-{d7['l']:<2} ({d7['pct']!s:>5}%){flag_marker}"
            )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit raw JSON")
    parser.add_argument("--category", help="filter to a category (TOTAL/SIDE/PROP)")
    parser.add_argument("--tier", help="filter to a tier")
    parser.add_argument("--window", default="30d", help="7d|30d|lifetime")
    args = parser.parse_args()

    if args.category and args.tier:
        rec = get_record(args.category, args.tier, args.window)
        if args.json:
            print(json.dumps(rec, indent=2))
        else:
            print(format_inline(args.category, args.tier, args.window))
        return

    if args.json:
        print(json.dumps(_fetch(), indent=2))
    else:
        print(render_table())


if __name__ == "__main__":
    main()
