"""Validate a draft pick writeup against the live engine reads.

Companion to scripts/engine_tier_brief.py. That script TELLS you what
language is allowed. This script CHECKS a draft writeup and flags
overselling — saying "STRONG" when the engine read LIGHT, claiming
"loudest signal" without verification, etc.

Usage:
  echo "Take NYY ML on a STRONG resolver signal" | python scripts/validate_writeup.py --matchup "Yankees"
  python scripts/validate_writeup.py --matchup "Yankees" --file draft.md
  python scripts/validate_writeup.py --matchup "Yankees" --text "..."

The validator flags:
  - Tier words inconsistent with the engine's actual resolver tier
  - "loudest" / "highest" / "strongest" claims when engine tier is LIGHT/LEAN
  - "+X dev" citations that conflate cohort dev with resolver tier
  - "model majority" claims when resolver said cohort-alone

Exit non-zero on violations so it can wedge into CI / pre-publish hooks.

Spec: feedback_quote_engine_tier_verbatim.md
"""
import argparse
import json
import os
import re
import sys

import requests
from dotenv import load_dotenv

_here = os.path.dirname(os.path.abspath(__file__))
for _candidate in (os.path.join(_here, "..", "mlb_pipeline", ".env"),
                    os.path.join(_here, "..", ".env")):
    if os.path.exists(_candidate):
        load_dotenv(_candidate, override=False)
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
if not SUPABASE_URL:
    sys.stderr.write("ERROR: SUPABASE_URL not set.\n")
    sys.exit(2)
H = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


# Tier words used as LABELS (capitalized or all-caps). Lowercase usage
# treated as descriptive language ("cohort lean" = OK; "STRONG signal" or
# "Strong play" = tier label). This keeps the validator from flagging
# allowed phrases like "LIGHT cohort lean" where 'lean' is descriptive.
_TIER_PATTERNS = {
    "ELITE": re.compile(r"\b(?:ELITE|Elite)\b"),
    "STRONG": re.compile(r"\b(?:STRONG|Strong)\b"),
    "LEAN": re.compile(r"\b(?:LEAN|Lean)\b"),
    "LIGHT": re.compile(r"\b(?:LIGHT|Light)\b"),
    "PRIME": re.compile(r"\b(?:PRIME|Prime)\b"),
}

# Hyperbolic conviction claims that REQUIRE the engine to have said STRONG+.
# Any of these in the writeup without an engine STRONG or ELITE tier is a
# violation.
_HYPERBOLE_PATTERNS = [
    (re.compile(r"\bloudest\b", re.IGNORECASE), "'loudest'"),
    (re.compile(r"\bhighest conviction\b", re.IGNORECASE), "'highest conviction'"),
    (re.compile(r"\bstrongest (?:signal|read|pick)\b", re.IGNORECASE), "'strongest signal/read/pick'"),
    (re.compile(r"\btop of the slate\b", re.IGNORECASE), "'top of the slate'"),
    (re.compile(r"\bmodel majority\b", re.IGNORECASE), "'model majority'"),
    (re.compile(r"\bbest play\b", re.IGNORECASE), "'best play'"),
]

# Cohort dev citation. Flag if the writeup cites "+X dev" as a tier
# justification — dev is a cohort input, not a resolver tier.
_DEV_PATTERN = re.compile(r"\+\d+(?:\.\d+)?\s*dev\b", re.IGNORECASE)

# Allowed tier per pick context. ELITE > STRONG > LEAN > LIGHT > SKIP.
_TIER_RANK = {"ELITE": 4, "STRONG": 3, "LEAN": 2, "LIGHT": 1, "SKIP": 0, "-": 0}


def _fetch_game_read(date, matchup_substring):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/jerry_cache",
        params={"cache_key": f"like.game_read_*_{date}",
                "select": "data", "limit": "50"},
        headers=H, timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
    for row in rows:
        d = row.get("data")
        if isinstance(d, str):
            try: d = json.loads(d)
            except Exception: continue
        if not isinstance(d, dict): continue
        mu = d.get("matchup") or ""
        if matchup_substring.lower() in mu.lower():
            return mu, d
    return None, None


def _classify_writeup_pick_type(text):
    """Heuristically determine what kind of pick the writeup is about.
    Returns one of: 'side', 'total', 'prop', or 'unknown'."""
    t = text.lower()
    # Strong side signals: 'ML', 'moneyline', 'spread', '-1.5', '+1.5', 'RL'
    side_signals = bool(re.search(r"\b(?:ml|moneyline|spread|run[- ]?line|run line|-1\.5|\+1\.5|rl)\b", t))
    # Strong total signals: 'over X.X', 'under X.X', 'total'
    total_signals = bool(re.search(r"\b(?:over|under)\s+\d+(?:\.\d+)?|\btotal\b", t))
    # Prop signals: 'props', 'strikeouts', 'hits allowed', 'walks', 'earned runs'
    prop_signals = bool(re.search(r"\b(?:strikeout|hits? allowed|walks?|earned runs?|outs|prop)\b", t))

    # If multiple signals fire, prefer the most explicit/loudest. Side wins
    # over total when both fire (ML/spread are explicit), prop wins over both
    # when player-stat language is present.
    if prop_signals and not (side_signals or total_signals):
        return "prop"
    if side_signals and not total_signals:
        return "side"
    if total_signals and not side_signals:
        return "total"
    if side_signals and total_signals:
        # Ambiguous — most writeups primarily about ML/spread; treat as side.
        return "side"
    return "unknown"


def _validate(text, matchup, side_tier, total_tier):
    violations = []

    side_rank = _TIER_RANK.get((side_tier or "-").upper(), 0)
    total_rank = _TIER_RANK.get((total_tier or "-").upper(), 0)
    max_engine_rank = max(side_rank, total_rank)

    pick_type = _classify_writeup_pick_type(text)
    if pick_type == "side":
        engine_rank = side_rank
        engine_tier = side_tier
        engine_label = "SIDE"
    elif pick_type == "total":
        engine_rank = total_rank
        engine_tier = total_tier
        engine_label = "TOTAL"
    else:
        engine_rank = max_engine_rank
        engine_tier = f"{side_tier or '-'}/{total_tier or '-'}"
        engine_label = "GAME (unclassified)"

    # 1. Tier words used in writeup that exceed the engine's actual tier
    #    for the picked dimension.
    cited_tiers = set()
    for tier, pat in _TIER_PATTERNS.items():
        if pat.search(text):
            cited_tiers.add(tier)

    for cited in cited_tiers:
        cited_rank = _TIER_RANK.get(cited, 0)
        if cited_rank > engine_rank:
            violations.append(
                f"writeup cites '{cited}' but {engine_label} engine tier is "
                f"{engine_tier or '-'}. Downgrade language to match engine."
            )

    # 2. Hyperbole claims when picked dim doesn't have STRONG+.
    if engine_rank < _TIER_RANK["STRONG"]:
        for pat, descr in _HYPERBOLE_PATTERNS:
            m = pat.search(text)
            if m:
                violations.append(
                    f"hyperbole {descr} requires engine tier >= STRONG; "
                    f"{engine_label} engine tier is {engine_tier or '-'}"
                )

    # 3. "+X dev" cited as conviction (dev is a cohort input, not a resolver tier).
    m = _DEV_PATTERN.search(text)
    if m:
        violations.append(
            f"'{m.group(0).strip()}' is a cohort dev value, not a resolver tier. "
            "Quote the resolver tier instead (engine_tier_brief.py shows allowed phrases)."
        )

    return violations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--matchup", required=True, help="substring match against jerry_cache matchup")
    parser.add_argument("--text", help="draft writeup text inline")
    parser.add_argument("--file", help="draft writeup from file path")
    parser.add_argument("--date")
    args = parser.parse_args()

    if not args.date:
        from datetime import datetime, timezone, timedelta
        args.date = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")

    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
    elif args.text:
        text = args.text
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        sys.stderr.write("ERROR: provide --text, --file, or pipe via stdin.\n")
        return 2

    mu, read = _fetch_game_read(args.date, args.matchup)
    if not read:
        sys.stderr.write(f"ERROR: no game_read for '{args.matchup}' on {args.date}\n")
        return 2

    side = read.get("resolver_side") or {}
    total = read.get("resolver") or {}
    side_tier = side.get("tier")
    total_tier = total.get("tier")

    print(f"Validating writeup for: {mu}")
    print(f"  Engine reads -- SIDE: {side_tier or '-'} {side.get('direction','')}  TOTAL: {total_tier or '-'} {total.get('direction','')}")
    print()

    violations = _validate(text, mu, side_tier, total_tier)
    if not violations:
        print("OK: writeup language consistent with engine tier.")
        return 0
    print(f"VIOLATIONS ({len(violations)}):")
    for v in violations:
        print(f"  - {v}")
    print()
    print("Fix: run scripts/engine_tier_brief.py to see ALLOWED language, then rewrite.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
