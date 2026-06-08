"""
Runtime lookup for cohort_signals.

Loads the rule set produced by refresh_cohort_signals.py from jerry_cache,
caches it in-process, and provides:

    evaluate_game_for_play(g, play_label, direction=None)
        -> list of matched rules sorted by edge strength

    apply_cohort_adjustments(base_conviction, matched_rules)
        -> (adjusted_conviction, list_of_(rule_id, delta)) tuples
        Phase 2+ use. Total delta capped at ±25.

DESIGN PRINCIPLES (locked 2026-06-08):
  - Graceful degrade: missing cache row / stale / unreachable supabase
    all return [] from evaluate. Never throws.
  - Recency gate: rules older than 36h are dropped (matches cohort_lookup
    pattern from 6/7 commit 6df5690).
  - Cap on stacked deltas: per single play, sum of applied conviction
    deltas is clamped to ±25 so no one play is moved entirely by cohorts.
  - Disable switch: env COHORT_SIGNALS_DISABLE=1 short-circuits everything
    to empty list (safety mode).

PHASE 1 USAGE: read-only surface in Jerry reads struct. No conviction
adjustment yet. Phase 2 wires conviction deltas in props/POTD/DAWG.
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

from cohort_features import get_features

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
CACHE_KEY = "cohort_signals"
DISABLE = os.environ.get("COHORT_SIGNALS_DISABLE") == "1"

_FRESHNESS_HOURS = 36
_DELTA_CAP = 25  # absolute cap on summed conviction adjustments per play

_CACHE = {"loaded_at": None, "data": None}


def _load():
    """Pull cohort_signals row from jerry_cache once per process. Returns
    parsed payload dict or None on any failure."""
    if DISABLE:
        return None
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
        if r.status_code != 200 or not r.json():
            _CACHE["data"] = None
            return None
        row = r.json()[0]
        # Freshness gate
        try:
            created = datetime.fromisoformat(row.get("created_at", ""))
            if datetime.now(timezone.utc) - created > timedelta(hours=_FRESHNESS_HOURS):
                _CACHE["data"] = None
                return None
        except (ValueError, TypeError):
            pass
        data = row.get("data")
        if isinstance(data, str):
            data = json.loads(data)
        _CACHE["data"] = data if isinstance(data, dict) else None
    except (requests.RequestException, json.JSONDecodeError, ValueError):
        _CACHE["data"] = None
    return _CACHE["data"]


def evaluate_game_for_play(g, play_label, direction=None):
    """Return list of matched rules for this game + play context.

    Each match is the full rule dict from the JSON. Sorted strongest-first
    by absolute conviction_delta.

    Args:
        g: game-context dict (mlb_game_context row OR mlb_game_results row)
        play_label: one of 'v3_ml', 'v4_ml', 'jerry_ml', 'conf_ml',
                    'v3_rl', 'v4_rl', 'jerry_rl', 'conf_rl',
                    'v3_tot', 'v4_tot', 'jerry_tot'
        direction: optional play direction ('home'/'away'/'over'/'under').
                   When provided, rules with a non-matching direction are
                   skipped. 'any'-direction rules always match.
    """
    if not isinstance(g, dict):
        return []
    payload = _load()
    if not payload or "rules" not in payload:
        return []
    features = get_features(g)
    if not features:
        return []
    matched = []
    for rule in payload["rules"]:
        if rule.get("play") != play_label:
            continue
        rule_dir = rule.get("direction")
        # 'any' rules match any direction; specific rules only match same direction
        if rule_dir and rule_dir != "any" and direction and rule_dir != direction:
            continue
        # Check all matches_if keys are in the features set
        required = rule.get("matches_if") or []
        if not all(k in features for k in required):
            continue
        matched.append(rule)
    matched.sort(key=lambda r: -abs(r.get("conviction_delta") or 0))
    return matched


def apply_cohort_adjustments(base_conviction, matched_rules):
    """Apply conviction deltas from matched rules, capping the total.

    Returns (adjusted_conviction, list of (rule_id, delta_applied)).
    The cap on total summed delta is ±25 to prevent any single play from
    being moved entirely by cohorts.

    PHASE 1 IS READ-ONLY — this is for Phase 2 use. In Phase 1 the props
    scorer does not call this.
    """
    if not matched_rules:
        return base_conviction, []
    raw_total = sum(r.get("conviction_delta") or 0 for r in matched_rules)
    # Cap to ±25 proportionally if it overshoots
    if abs(raw_total) > _DELTA_CAP:
        scale = _DELTA_CAP / abs(raw_total)
        applied = [(r["id"], round((r.get("conviction_delta") or 0) * scale, 1)) for r in matched_rules]
        total = round(_DELTA_CAP * (1 if raw_total > 0 else -1), 1)
    else:
        applied = [(r["id"], r.get("conviction_delta") or 0) for r in matched_rules]
        total = raw_total
    return base_conviction + total, applied


def card_eligibility_for_play(g, play_label, direction=None):
    """Return the strictest card_eligibility across matched rules:
        BLOCK_FROM_CARD  > PROMOTE_TO_PRIME > CARD_OK
    Phase 3+ use.
    """
    matched = evaluate_game_for_play(g, play_label, direction)
    if not matched:
        return "CARD_OK"
    eligibilities = [r.get("card_eligibility", "CARD_OK") for r in matched]
    if "BLOCK_FROM_CARD" in eligibilities:
        return "BLOCK_FROM_CARD"
    if "PROMOTE_TO_PRIME" in eligibilities:
        return "PROMOTE_TO_PRIME"
    return "CARD_OK"


def summarize_for_struct(g):
    """Phase 1 surface — return a single dict the Jerry-read struct can embed.

    Aggregates matched rules across all 11 play types and formats them so
    the LLM can cite them naturally. Returns None when no rules match
    (gracefully omitted from struct).
    """
    if not isinstance(g, dict):
        return None
    payload = _load()
    if not payload:
        return None

    play_summaries = {}
    plays = ["v3_ml", "v4_ml", "jerry_ml", "conf_ml",
             "v3_rl", "v4_rl", "jerry_rl", "conf_rl",
             "v3_tot", "v4_tot", "jerry_tot"]
    for play in plays:
        matches = evaluate_game_for_play(g, play)
        if not matches:
            continue
        # Top 3 per play sorted by absolute edge
        top = matches[:3]
        play_summaries[play] = [
            {
                "cohort": r.get("matches_if_raw"),
                "tier": r.get("tier"),
                "shrunken_pct": r.get("shrunken_pct"),
                "raw": f"{r.get('raw_wins')}-{r.get('raw_losses')} lifetime ({r.get('raw_n')} games)",
                "direction": r.get("direction"),
                "delta": r.get("conviction_delta"),
            }
            for r in top
        ]
    if not play_summaries:
        return None
    return {
        "computed_at": payload.get("computed_at"),
        "total_rules_in_lookup": len(payload.get("rules", [])),
        "matched_plays": play_summaries,
    }
