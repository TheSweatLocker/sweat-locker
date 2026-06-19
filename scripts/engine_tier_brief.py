"""Engine tier brief — produce verbatim tier language for picks.

Solves the recurring overselling problem (saved as
feedback_quote_engine_tier_verbatim.md): without this script, the
content-writing step pulls cohort signals, builds a narrative, and
writes at higher conviction than the engine actually outputs. Example:
2026-06-18 NYY ML written as "STRONG +10.8 dev loudest signal" when
the live resolver_side tier was actually LIGHT.

This script:
  1. Reads jerry_cache for today's game_read_<game_id>_<date> rows
  2. For every game, prints the AUTHORITATIVE resolver tier (side + total)
  3. Lists ALLOWED writeup language for that tier
  4. Lists FORBIDDEN writeup language (the overselling vocabulary)
  5. Surfaces cohort signals as SUPPORTING evidence only, with n

Usage:
  python scripts/engine_tier_brief.py
  python scripts/engine_tier_brief.py --date 2026-06-19
  python scripts/engine_tier_brief.py --matchup "Mets @ Reds"

Output is meant to be copy-paste quotable into a writeup. The
"ALLOWED" lines are the only conviction language safe to use.
"""
import argparse
import json
import os
import sys

import requests
from dotenv import load_dotenv

# Load .env from any candidate location. The repo has two .env files —
# repo-root holds Expo public keys, mlb_pipeline/.env holds Supabase
# credentials. Load BOTH (in order) so SUPABASE_URL/KEY end up set.
_here = os.path.dirname(os.path.abspath(__file__))
for _candidate in (os.path.join(_here, "..", "mlb_pipeline", ".env"),
                    os.path.join(_here, "..", ".env")):
    if os.path.exists(_candidate):
        load_dotenv(_candidate, override=False)  # don't clobber already-set vars
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
if not SUPABASE_URL:
    sys.stderr.write("ERROR: SUPABASE_URL not set. Set it in mlb_pipeline/.env.\n")
    sys.exit(2)
H = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


_ALLOWED_BY_TIER = {
    "ELITE": [
        "ELITE total play",
        "all-models-agree + cohort LOUD",
        "ELITE side resolver",
    ],
    "STRONG": [
        "STRONG side resolver",
        "STRONG total resolver",
        "model majority + cohort LOUD aligned",
        "loudest aligned signal of the slate (only if true vs other STRONG/ELITE picks)",
    ],
    "LEAN": [
        "LEAN total resolver",
        "model + cohort LEAN aligned",
        "modest engine lean",
    ],
    "LIGHT": [
        "LIGHT cohort lean",
        "cohort engine alone",
        "cohort signal, models neutral",
        "directional cohort without model majority",
    ],
    "SKIP": [
        "engine SKIPS this game",
        "contested read - no play",
        "model vs cohort disagreement - sit out",
    ],
}

_FORBIDDEN_GENERIC = [
    "STRONG (when resolver said LIGHT)",
    "loudest signal (when not actually loudest)",
    "highest conviction (without verification)",
    "PRIME side (when resolver tier is below STRONG)",
    "model majority (when resolver said cohort-alone)",
    "+X dev STRONG (citing cohort dev as if it were resolver tier)",
]


def _fetch_reads(date):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/jerry_cache",
        params={"cache_key": f"like.game_read_*_{date}",
                "select": "cache_key,data", "limit": "50"},
        headers=H, timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
    by_matchup = {}
    for row in rows:
        d = row.get("data")
        if isinstance(d, str):
            try: d = json.loads(d)
            except Exception: continue
        if not isinstance(d, dict): continue
        mu = d.get("matchup")
        if mu:
            by_matchup[mu] = d
    return by_matchup


def _print_brief_for_read(matchup, read):
    side = read.get("resolver_side") or read.get("side_resolver") or {}
    total = read.get("resolver") or {}
    side_tier = side.get("tier") or "-"
    side_dir = side.get("direction") or ""
    side_team = side.get("team") or ""
    side_reason = (side.get("reason") or "").strip()
    total_tier = total.get("tier") or "-"
    total_dir = total.get("direction") or ""
    total_reason = (total.get("reason") or "").strip()

    print("=" * 92)
    print(f"  {matchup}")
    print("=" * 92)

    print()
    print("SIDE / ML resolver:")
    print(f"  ENGINE TIER: {side_tier}   DIRECTION: {side_dir}   TEAM: {side_team}")
    if side_reason:
        print(f"  Engine reason: {side_reason}")
    allowed = _ALLOWED_BY_TIER.get(str(side_tier).upper(), [])
    if allowed:
        print("  ALLOWED writeup language:")
        for a in allowed:
            print(f"    - \"{a}\"")
    print("  FORBIDDEN (overselling traps - DO NOT USE):")
    for f in _FORBIDDEN_GENERIC:
        print(f"    - {f}")

    print()
    print("TOTAL resolver:")
    print(f"  ENGINE TIER: {total_tier}   DIRECTION: {total_dir}")
    if total_reason:
        print(f"  Engine reason: {total_reason}")
    allowed = _ALLOWED_BY_TIER.get(str(total_tier).upper(), [])
    if allowed:
        print("  ALLOWED writeup language:")
        for a in allowed:
            print(f"    - \"{a}\"")

    cs = read.get("cohort_signals") or {}
    matched = cs.get("matched_plays") if isinstance(cs, dict) else None
    if matched:
        print()
        print("SUPPORTING cohort signals (context only - DO NOT use as tier label):")
        for play, rules in matched.items():
            if not rules: continue
            top = rules[0]
            tier_in = top.get("tier", "?")
            pct = top.get("shrunken_pct", "?")
            raw = top.get("raw", "?")
            direction = top.get("direction", "?")
            n_ok = isinstance(raw, str) and (("(" in raw and "games)" in raw))
            n_note = "" if n_ok else "  [n not visible - verify]"
            print(f"  {play:10s}: {tier_in:12s} {direction:6s} {pct}% {raw}{n_note}")
    else:
        print()
        print("SUPPORTING cohort signals: none surfaced for this game")

    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    parser.add_argument("--matchup", help="filter to one game (substring match)")
    args = parser.parse_args()

    if not args.date:
        from datetime import datetime, timezone, timedelta
        today = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")
        args.date = today
    print(f"Engine tier brief - {args.date}")
    print(f"(Source: jerry_cache.game_read_*_{args.date})")
    print()

    reads = _fetch_reads(args.date)
    if not reads:
        print(f"  No game_read rows for {args.date}. Has the cron run yet?")
        return 1
    matchups = sorted(reads.keys())
    if args.matchup:
        matchups = [m for m in matchups if args.matchup.lower() in m.lower()]
        if not matchups:
            print(f"  No matchup matching '{args.matchup}'")
            return 1
    for mu in matchups:
        _print_brief_for_read(mu, reads[mu])
    return 0


if __name__ == "__main__":
    sys.exit(main())
