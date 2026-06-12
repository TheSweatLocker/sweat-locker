"""Track cohort rule lifecycle events day-over-day.

WHY: refresh_cohort_signals silently promotes/demotes/vetoes rules every
night. A LOCK rule going 0-5 over a month gets recency-vetoed without
any audit trail; we lose the rule AND the evidence of its death. This
script keeps the audit trail.

WHAT: After every refresh_cohort_signals run, diff the new rules dict
against the previous snapshot and emit lifecycle events. Events stored
in `jerry_cache.cohort_lifecycle_{date}` (no schema migration needed).
Previous snapshot stored at `jerry_cache.cohort_signals_previous`.

EVENT TYPES:
  added       — rule didn't exist yesterday, exists today
  promoted    — rule moved up the tier ladder (LEAN → STRONG_EDGE, etc.)
  demoted     — rule moved down (STRONG_EDGE → LEAN)
  vetoed      — rule existed yesterday but recency veto dropped it today
  removed     — rule fell below MIN_RAW_N or hit NEUTRAL band
  pct_drift   — same tier but shrunken_pct moved ≥3 percentage points

USAGE:
  Auto-runs at end of refresh_cohort_signals.run() (added 2026-06-11).
  Manual: python track_cohort_lifecycle.py [--dryrun]
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

CURRENT_KEY = "cohort_signals"
PREVIOUS_KEY = "cohort_signals_previous"

# Tier order from refresh_cohort_signals.TIER_THRESHOLDS (loud → quiet).
# Used to classify promotions vs demotions.
TIER_ORDER = {
    "LOCK": 6,
    "STRONG_EDGE": 5,
    "LEAN": 4,
    "NEUTRAL": 3,
    "SOFT_FADE": 2,
    "FADE": 1,
    "HARD_FADE": 0,
}

# Material pct drift threshold — small movements aren't worth logging.
PCT_DRIFT_THRESHOLD_PP = 3.0


def _fetch_jerry_cache(key):
    """Return the parsed `data` field from a jerry_cache row, or None."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/jerry_cache",
        params={"cache_key": f"eq.{key}", "select": "data"},
        headers=HEADERS,
        timeout=10,
    )
    if r.status_code != 200:
        return None
    rows = r.json()
    if not rows:
        return None
    raw = rows[0].get("data")
    # refresh_cohort_signals stores payload as a JSON STRING (json.dumps).
    # Other callers may store dicts directly. Handle both.
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw


def _rules_to_snapshot(rules):
    """Reduce a rules list to the minimal dict we diff on. Keeps payload
    small — we only care about tier + state + pct movement, not the full
    feature breakdown which lives in the live cohort_signals row."""
    snap = {}
    for r in rules or []:
        rid = r.get("id")
        if not rid:
            continue
        snap[rid] = {
            "tier": r.get("tier"),
            "shrunken_pct": r.get("shrunken_pct"),
            "raw_n": r.get("raw_n"),
            "play": r.get("play"),
            "direction": r.get("direction"),
            "recency_status": r.get("recency_status"),
            "state": r.get("state"),
            "natural_tier": r.get("natural_tier"),
        }
    return snap


def diff_snapshots(prev, current):
    """Produce a list of lifecycle event dicts. prev/current are dicts
    keyed by rule_id → snapshot row."""
    events = []
    prev = prev or {}
    current = current or {}

    prev_ids = set(prev)
    curr_ids = set(current)

    # 1. ADDED — appeared today, didn't exist yesterday
    for rid in sorted(curr_ids - prev_ids):
        c = current[rid]
        events.append({
            "event_type": "added",
            "rule_id": rid,
            "play": c.get("play"),
            "direction": c.get("direction"),
            "from_tier": None,
            "to_tier": c.get("tier"),
            "from_pct": None,
            "to_pct": c.get("shrunken_pct"),
            "raw_n": c.get("raw_n"),
            "reason": (
                f"new rule emitted at {c.get('tier')} "
                f"({c.get('shrunken_pct')}%, n={c.get('raw_n')})"
            ),
        })

    # 2. REMOVED / VETOED — existed yesterday, gone today
    # We can't distinguish "vetoed by recency" from "fell to NEUTRAL" from
    # "raw_n dropped below MIN" without re-running. Mark as `removed` and
    # include yesterday's recency_status to hint at the cause.
    for rid in sorted(prev_ids - curr_ids):
        p = prev[rid]
        prior_recency = p.get("recency_status")
        # If yesterday's recency_status was already "drifting_kept" or
        # the prior tier was borderline LEAN/SOFT_FADE, recency veto is
        # the likely cause. Otherwise it could be sample shrinkage.
        if prior_recency in ("drifting_kept", "decaying_dropped", "reversed_dropped"):
            etype = "vetoed"
            reason = f"recency veto (prior status: {prior_recency})"
        else:
            etype = "removed"
            reason = f"fell out of rule set (prior tier: {p.get('tier')})"
        events.append({
            "event_type": etype,
            "rule_id": rid,
            "play": p.get("play"),
            "direction": p.get("direction"),
            "from_tier": p.get("tier"),
            "to_tier": None,
            "from_pct": p.get("shrunken_pct"),
            "to_pct": None,
            "raw_n": p.get("raw_n"),
            "reason": reason,
        })

    # 3. PROMOTED / DEMOTED / PCT_DRIFT / GRADUATED — present in both
    for rid in sorted(curr_ids & prev_ids):
        p = prev[rid]; c = current[rid]
        p_tier = p.get("tier"); c_tier = c.get("tier")
        p_pct = p.get("shrunken_pct") or 0; c_pct = c.get("shrunken_pct") or 0
        p_rank = TIER_ORDER.get(p_tier, -1)
        c_rank = TIER_ORDER.get(c_tier, -1)
        p_state = p.get("state"); c_state = c.get("state")
        c_natural = c.get("natural_tier")

        # Cap-migration suppression. When yesterday emitted the rule at
        # its natural tier (no state field, or state was ACTIVE) but
        # today it's PROBATIONARY-capped to a different tier, the
        # apparent tier change is purely the cap kicking in — not a
        # genuine pct or sample shift. Suppress to avoid polluting the
        # diff. Tracked elsewhere via the `probationary_capped` count.
        # Both rank directions matter: LOCK → LEAN cap looks demoted;
        # FADE → SOFT_FADE cap looks promoted.
        if (c_state == "PROBATIONARY"
                and c_natural and c_natural == p_tier
                and c_natural != c_tier):
            continue

        # State transition takes priority. Graduations from PROBATIONARY
        # to ACTIVE often coincide with tier change (LEAN cap lifts to
        # natural LOCK/STRONG_EDGE) — emit a single graduated event
        # capturing both the state shift and the tier consequence.
        if p_state == "PROBATIONARY" and c_state == "ACTIVE":
            events.append({
                "event_type": "graduated",
                "rule_id": rid,
                "play": c.get("play"),
                "direction": c.get("direction"),
                "from_tier": p_tier,
                "to_tier": c_tier,
                "from_pct": p_pct,
                "to_pct": c_pct,
                "raw_n": c.get("raw_n"),
                "reason": (
                    f"PROBATIONARY → ACTIVE (n {p.get('raw_n')} → "
                    f"{c.get('raw_n')}); cap lifted "
                    f"{p_tier} → {c_tier}"
                ),
            })
            continue
        if p_state == "ACTIVE" and c_state == "PROBATIONARY":
            events.append({
                "event_type": "regressed_to_probation",
                "rule_id": rid,
                "play": c.get("play"),
                "direction": c.get("direction"),
                "from_tier": p_tier,
                "to_tier": c_tier,
                "from_pct": p_pct,
                "to_pct": c_pct,
                "raw_n": c.get("raw_n"),
                "reason": (
                    f"ACTIVE → PROBATIONARY (n dropped {p.get('raw_n')} → "
                    f"{c.get('raw_n')}); rare — investigate result grading"
                ),
            })
            continue

        if c_rank > p_rank:
            events.append({
                "event_type": "promoted",
                "rule_id": rid,
                "play": c.get("play"),
                "direction": c.get("direction"),
                "from_tier": p_tier,
                "to_tier": c_tier,
                "from_pct": p_pct,
                "to_pct": c_pct,
                "raw_n": c.get("raw_n"),
                "reason": f"{p_tier} → {c_tier} (pct {p_pct} → {c_pct})",
            })
        elif c_rank < p_rank:
            events.append({
                "event_type": "demoted",
                "rule_id": rid,
                "play": c.get("play"),
                "direction": c.get("direction"),
                "from_tier": p_tier,
                "to_tier": c_tier,
                "from_pct": p_pct,
                "to_pct": c_pct,
                "raw_n": c.get("raw_n"),
                "reason": f"{p_tier} → {c_tier} (pct {p_pct} → {c_pct})",
            })
        elif abs(c_pct - p_pct) >= PCT_DRIFT_THRESHOLD_PP:
            events.append({
                "event_type": "pct_drift",
                "rule_id": rid,
                "play": c.get("play"),
                "direction": c.get("direction"),
                "from_tier": p_tier,
                "to_tier": c_tier,
                "from_pct": p_pct,
                "to_pct": c_pct,
                "raw_n": c.get("raw_n"),
                "reason": (
                    f"same tier ({c_tier}), pct moved {p_pct} → {c_pct} "
                    f"({c_pct - p_pct:+.1f}pp)"
                ),
            })
    return events


def _et_today():
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    return et_now.strftime("%Y-%m-%d")


def write_lifecycle_events(events, date_str):
    """Persist events to a date-keyed jerry_cache row."""
    summary = {
        "added": sum(1 for e in events if e["event_type"] == "added"),
        "promoted": sum(1 for e in events if e["event_type"] == "promoted"),
        "demoted": sum(1 for e in events if e["event_type"] == "demoted"),
        "vetoed": sum(1 for e in events if e["event_type"] == "vetoed"),
        "removed": sum(1 for e in events if e["event_type"] == "removed"),
        "pct_drift": sum(1 for e in events if e["event_type"] == "pct_drift"),
        "graduated": sum(1 for e in events if e["event_type"] == "graduated"),
        "regressed_to_probation": sum(1 for e in events if e["event_type"] == "regressed_to_probation"),
    }
    payload = {
        "date": date_str,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "events": events,
    }
    body = {
        "cache_key": f"cohort_lifecycle_{date_str}",
        "game_id": f"cohort_lifecycle_{date_str}",
        "sport": "mlb",
        "narrative": "",
        "data": json.dumps(payload),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=cache_key",
        headers=WRITE_HEADERS, json=body, timeout=15,
    )
    return r.status_code in (200, 201, 204), summary


def update_previous_snapshot(rules_snapshot):
    """Checkpoint today's snapshot as previous for tomorrow's diff."""
    body = {
        "cache_key": PREVIOUS_KEY,
        "game_id": PREVIOUS_KEY,
        "sport": "mlb",
        "narrative": "",
        "data": json.dumps({
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "rules": rules_snapshot,
        }),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=cache_key",
        headers=WRITE_HEADERS, json=body, timeout=15,
    )
    return r.status_code in (200, 201, 204)


def run(dryrun=False):
    print("[track_cohort_lifecycle] fetching current + previous snapshots...")
    current_payload = _fetch_jerry_cache(CURRENT_KEY)
    if not current_payload:
        print("  no current cohort_signals row — skipping (refresh hasn't run yet)")
        return
    current_rules = _rules_to_snapshot(current_payload.get("rules"))

    previous_payload = _fetch_jerry_cache(PREVIOUS_KEY)
    previous_rules = (previous_payload or {}).get("rules") if previous_payload else None

    if previous_rules is None:
        print("  no previous snapshot — first run, capturing baseline only.")
        if not dryrun:
            update_previous_snapshot(current_rules)
            print(f"  ✅ baseline captured ({len(current_rules)} rules)")
        return

    events = diff_snapshots(previous_rules, current_rules)
    print(f"  {len(events)} lifecycle event(s) detected")

    date_str = _et_today()
    if events:
        for e in events[:10]:
            print(f"    {e['event_type']:<10} {e['rule_id']} — {e['reason']}")
        if len(events) > 10:
            print(f"    ... and {len(events) - 10} more")

    if dryrun:
        print("[track_cohort_lifecycle] DRYRUN — not writing.")
        return

    if events:
        ok, summary = write_lifecycle_events(events, date_str)
        if ok:
            print(f"  ✅ wrote cohort_lifecycle_{date_str}: {summary}")
        else:
            print(f"  ⚠ lifecycle write FAILED")

    if update_previous_snapshot(current_rules):
        print(f"  ✅ checkpoint updated ({len(current_rules)} rules)")
    else:
        print(f"  ⚠ snapshot checkpoint FAILED")


if __name__ == "__main__":
    run(dryrun="--dryrun" in sys.argv)
