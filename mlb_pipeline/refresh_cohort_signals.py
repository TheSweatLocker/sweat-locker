"""
Refresh cohort_signals from latest graded game data.

Re-runs the attribution analysis on every graded mlb_game_results row,
applies Bayesian shrinkage to the raw hit-rates, filters out cohorts
that are decaying or reversed in recent data, and writes the surviving
rules to jerry_cache (cache_key='cohort_signals').

NIGHTLY USAGE: runs after resolve_game_results.py in the morning cron.
MANUAL USAGE: python refresh_cohort_signals.py [--dryrun]

KEY DESIGN CHOICES (locked 2026-06-08 with user):
  - Bayesian shrinkage with prior_n=30 (conservative regression to mean
    so 17-0 cohorts surface as ~80%, not 100%)
  - Tier bands: LOCK >=75%, STRONG >=65%, LEAN >=60%, NEUTRAL 45-60,
                SOFT_FADE >=35%, FADE >=28%, HARD_FADE <28%
  - Conviction deltas: +18 / +10 / +4 / 0 / -5 / -12 / -25
  - Recency veto: 30d hit_pct must be within 15pp of lifetime AND
    same sign vs baseline. Decaying/reversed rules are dropped.
  - Min raw_n = 10 floor (don't ship rules on thin samples).
"""
import os
import sys
import json
import math
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
WRITE_HEADERS = {**HEADERS, "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"}

CACHE_KEY = "cohort_signals"

# ── Design constants (locked 2026-06-08) ──
PRIOR_N = 30
MIN_RAW_N = 10
TIER_THRESHOLDS = [
    ("LOCK",        75.0, +18, "PROMOTE_TO_PRIME"),
    ("STRONG_EDGE", 65.0, +10, "CARD_OK"),
    ("LEAN",        60.0,  +4, "CARD_OK"),
    ("NEUTRAL",     45.0,   0, "CARD_OK"),
    ("SOFT_FADE",   35.0,  -5, "CARD_OK"),
    ("FADE",        28.0, -12, "BLOCK_FROM_CARD"),
    ("HARD_FADE",    0.0, -25, "BLOCK_FROM_CARD"),
]
RECENCY_VETO_DROP_PP = 15.0

# Phase 1 cohort rotation (see docs/cohort_rotation_policy.md).
# Probationary rules are capped at LEAN (or SOFT_FADE on the fade side)
# regardless of shrunken_pct, until they accumulate enough sample to
# graduate. age_days check defers to Phase 2 once last_fired_date lands.
PROBATION_N_THRESHOLD = 25
PROBATION_TIER_CAP_LOUD = ("LEAN", +4)            # caps LOCK/STRONG_EDGE
PROBATION_TIER_CAP_FADE = ("SOFT_FADE", -5)       # caps FADE/HARD_FADE


def classify_state(raw_n):
    """Phase 1: state derived purely from sample size. Phase 2 will add
    last_fired_date staleness + age_days check."""
    if raw_n < PROBATION_N_THRESHOLD:
        return "PROBATIONARY"
    return "ACTIVE"


def apply_state_cap(tier_name, delta, eligibility, state):
    """Cap loud tiers for PROBATIONARY rules. Returns the (possibly
    modified) tier triple. Caller stores the pre-cap values as
    natural_tier / natural_delta for audit."""
    if state != "PROBATIONARY":
        return tier_name, delta, eligibility
    if tier_name in ("LOCK", "STRONG_EDGE"):
        capped_tier, capped_delta = PROBATION_TIER_CAP_LOUD
        return capped_tier, capped_delta, "CARD_OK"
    if tier_name in ("FADE", "HARD_FADE"):
        capped_tier, capped_delta = PROBATION_TIER_CAP_FADE
        return capped_tier, capped_delta, "CARD_OK"
    return tier_name, delta, eligibility


def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(v)
    except (TypeError, ValueError): return None


# ── Fetch ──
def fetch_rows(since_date=None):
    rows = []; page = 0
    while True:
        params = {
            "home_score": "not.is.null", "signal_confluence_net": "not.is.null",
            "select": "*", "order": "game_date.desc",
            "offset": str(page * 1000), "limit": "1000",
        }
        if since_date:
            params["game_date"] = f"gte.{since_date}"
        r = requests.get(f"{SUPABASE_URL}/rest/v1/mlb_game_results",
                         params=params, headers=HEADERS, timeout=30)
        if r.status_code != 200: break
        batch = r.json()
        if not batch: break
        rows.extend(batch)
        if len(batch) < 1000: break
        page += 1
    return rows


# ── Play definitions ──
def _ml_call(s):
    if s is None or abs(s) < 0.3: return None
    return "home" if s > 0 else "away"

def _rl_call(s):
    if s is None: return None
    if s > 1.5: return "home"
    if s < -1.5: return "away"
    return "away" if s < 0 else "home"

def _conf_side(net):
    n = _i(net)
    if n is None: return None
    if n > 1: return "home"
    if n < -1: return "away"
    return None

def _total_call(mt, line):
    if mt is None or line is None: return None
    if mt >= line + 0.7: return "over"
    if mt <= line - 0.7: return "under"
    return None

def _actual_ml(g):
    if g.get("home_win") is True: return "home"
    if g.get("home_win") is False: return "away"
    return None

def _actual_rl(g):
    hs = g.get("home_score"); as_ = g.get("away_score")
    if hs is None or as_ is None: return None
    m = abs(hs - as_)
    if m <= 1: return "push"
    return "home" if hs > as_ else "away"

def _actual_total(g):
    line = g.get("close_total") or g.get("open_total")
    hs = g.get("home_score"); as_ = g.get("away_score")
    if line is None or hs is None: return None
    t = hs + as_
    if t > line: return "over"
    if t < line: return "under"
    return "push"


PLAY_TYPES = [
    ("v3_ml",    lambda g: _ml_call(_f(g.get("projected_spread"))),  _actual_ml),
    ("v4_ml",    lambda g: _ml_call(_f(g.get("model_pred_spread"))), _actual_ml),
    ("jerry_ml", lambda g: _ml_call(_f(g.get("jerry_pred_spread"))), _actual_ml),
    ("conf_ml",  lambda g: _conf_side(g.get("signal_confluence_net")), _actual_ml),
    ("v3_rl",    lambda g: _rl_call(_f(g.get("projected_spread"))),  _actual_rl),
    ("v4_rl",    lambda g: _rl_call(_f(g.get("model_pred_spread"))), _actual_rl),
    ("jerry_rl", lambda g: _rl_call(_f(g.get("jerry_pred_spread"))), _actual_rl),
    ("conf_rl",  lambda g: _conf_side(g.get("signal_confluence_net")), _actual_rl),
    ("v3_tot",   lambda g: _total_call(_f(g.get("projected_total")),
                                       _f(g.get("close_total")) or _f(g.get("open_total"))), _actual_total),
    ("v4_tot",   lambda g: _total_call(_f(g.get("model_pred_total")),
                                       _f(g.get("close_total")) or _f(g.get("open_total"))), _actual_total),
    ("jerry_tot",lambda g: _total_call(_f(g.get("jerry_pred_total")),
                                       _f(g.get("close_total")) or _f(g.get("open_total"))), _actual_total),
]


def _score(call, actual):
    if call is None or actual is None: return None
    if actual == "push": return "P"
    return "W" if call == actual else "L"


# ── Bayesian shrinkage + tier assignment ──

def shrink(raw_pct, n, baseline_pct):
    """Posterior estimate: weights raw wins toward baseline by prior_n trials."""
    raw_wins = (raw_pct / 100.0) * n
    prior_wins = (baseline_pct / 100.0) * PRIOR_N
    return round(100.0 * (raw_wins + prior_wins) / (n + PRIOR_N), 1)


def tier_for(shrunken_pct):
    for name, threshold, delta, eligibility in TIER_THRESHOLDS:
        if shrunken_pct >= threshold:
            return name, delta, eligibility
    return "HARD_FADE", -25, "BLOCK_FROM_CARD"


# ── Tally pass ──

def tally(rows, get_features_fn):
    """Returns:
        play_baseline_pct[play] -> overall hit rate per play
        dir_baseline_pct[(play, dir)] -> per-direction hit rate
        cohort_tally[(play, ck, dir)] -> {w, l, p, n}
    """
    overall = defaultdict(lambda: {"w": 0, "l": 0, "p": 0})
    splits = defaultdict(lambda: {"w": 0, "l": 0, "p": 0})
    cohort_tally = defaultdict(lambda: {"w": 0, "l": 0, "p": 0})

    for g in rows:
        features = get_features_fn(g)
        for play, predict, actual_fn in PLAY_TYPES:
            call = predict(g)
            res = _score(call, actual_fn(g))
            if res is None: continue
            t = overall[play]
            if res == "W": t["w"] += 1
            elif res == "L": t["l"] += 1
            elif res == "P": t["p"] += 1
            if call:
                t2 = splits[(play, call)]
                if res == "W": t2["w"] += 1
                elif res == "L": t2["l"] += 1
                elif res == "P": t2["p"] += 1
                for ck in features:
                    t3 = cohort_tally[(play, ck, "any")]
                    if res == "W": t3["w"] += 1
                    elif res == "L": t3["l"] += 1
                    elif res == "P": t3["p"] += 1
                    t4 = cohort_tally[(play, ck, call)]
                    if res == "W": t4["w"] += 1
                    elif res == "L": t4["l"] += 1
                    elif res == "P": t4["p"] += 1

    play_base = {}
    for play, t in overall.items():
        n = t["w"] + t["l"]
        play_base[play] = round(100*t["w"]/n, 1) if n else None
    dir_base = {}
    for (play, d), t in splits.items():
        n = t["w"] + t["l"]
        dir_base[(play, d)] = round(100*t["w"]/n, 1) if n else None
    return play_base, dir_base, cohort_tally


def run(dryrun=False):
    from cohort_features import get_features

    print("[refresh_cohort_signals] fetching graded rows...")
    all_rows = fetch_rows()
    print(f"  {len(all_rows)} lifetime rows")

    cutoff_30 = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    last30 = [r for r in all_rows if r.get("game_date") and r["game_date"] >= cutoff_30]
    print(f"  {len(last30)} last-30d rows")

    print("[refresh_cohort_signals] tallying lifetime...")
    life_play_base, life_dir_base, life_tally = tally(all_rows, get_features)
    print("[refresh_cohort_signals] tallying last 30d...")
    _, _, last30_tally = tally(last30, get_features)

    # ── Build signed rules ──
    rules = []
    for (play, ck, dirn), t in life_tally.items():
        n = t["w"] + t["l"]
        if n < MIN_RAW_N:
            continue
        raw_pct = round(100 * t["w"] / n, 1)
        if dirn == "any":
            baseline = life_play_base.get(play) or 50.0
        else:
            baseline = life_dir_base.get((play, dirn)) or life_play_base.get(play) or 50.0
        shrunken = shrink(raw_pct, n, baseline)
        natural_tier, natural_delta, natural_eligibility = tier_for(shrunken)
        # Only emit rules with material edge (skip NEUTRAL — they don't move conviction)
        if natural_tier == "NEUTRAL":
            continue

        # Phase 1 cohort rotation: classify state + apply tier cap
        # for PROBATIONARY rules. Natural tier preserved on the rule
        # dict for audit ("would have been LOCK, capped to LEAN
        # pending probation graduation").
        state = classify_state(n)
        tier_name, delta, eligibility = apply_state_cap(
            natural_tier, natural_delta, natural_eligibility, state,
        )

        # Recency veto: 30d hit rate must be within RECENCY_VETO_DROP_PP of lifetime
        # AND not sign-flipped vs baseline.
        last30_t = last30_tally.get((play, ck, dirn), {"w": 0, "l": 0})
        last30_n = last30_t["w"] + last30_t["l"]
        last30_pct = None
        recency_status = "insufficient_recent"
        if last30_n >= 5:
            last30_pct = round(100 * last30_t["w"] / last30_n, 1)
            drift = last30_pct - raw_pct
            # Drop if dropped too far OR sign-flipped vs baseline direction
            life_dir = "above" if raw_pct >= baseline else "below"
            recent_dir = "above" if last30_pct >= baseline else "below"
            if abs(drift) >= RECENCY_VETO_DROP_PP and last30_n >= 10:
                if life_dir != recent_dir:
                    recency_status = "reversed_dropped"
                else:
                    recency_status = "decaying_dropped"
                continue  # drop rule entirely
            recency_status = "stable" if abs(drift) < 7 else "drifting_kept"

        rule = {
            "id": f"{play}|{ck}|{dirn}",
            "play": play,
            "direction": dirn,
            "matches_if": [ck] if "+" not in ck else ck.split("+"),
            "matches_if_raw": ck,  # original key from features
            "raw_pct": raw_pct,
            "raw_n": n,
            "raw_wins": t["w"],
            "raw_losses": t["l"],
            "shrunken_pct": shrunken,
            "tier": tier_name,
            "conviction_delta": delta,
            "card_eligibility": eligibility,
            "baseline_pct": baseline,
            "last30_pct": last30_pct,
            "last30_n": last30_n,
            "recency_status": recency_status,
            # Phase 1 cohort rotation. natural_* show the un-capped values
            # so downstream audits can answer "would this rule have been
            # LOCK if it had more sample?"
            "state": state,
            "natural_tier": natural_tier,
            "natural_delta": natural_delta,
        }
        rules.append(rule)

    # Sort by absolute edge strength
    rules.sort(key=lambda r: -abs(r["shrunken_pct"] - r["baseline_pct"]) * math.log(r["raw_n"]))

    summary = {
        "lock": len([r for r in rules if r["tier"] == "LOCK"]),
        "strong_edge": len([r for r in rules if r["tier"] == "STRONG_EDGE"]),
        "lean": len([r for r in rules if r["tier"] == "LEAN"]),
        "soft_fade": len([r for r in rules if r["tier"] == "SOFT_FADE"]),
        "fade": len([r for r in rules if r["tier"] == "FADE"]),
        "hard_fade": len([r for r in rules if r["tier"] == "HARD_FADE"]),
        # Phase 1 cohort rotation visibility
        "probationary": len([r for r in rules if r.get("state") == "PROBATIONARY"]),
        "active": len([r for r in rules if r.get("state") == "ACTIVE"]),
        "probationary_capped": len([
            r for r in rules
            if r.get("state") == "PROBATIONARY"
            and r.get("natural_tier") in ("LOCK", "STRONG_EDGE", "FADE", "HARD_FADE")
        ]),
    }

    print()
    print(f"[refresh_cohort_signals] emitted {len(rules)} rules:")
    for tier, count in summary.items():
        print(f"  {tier}: {count}")

    print()
    print("=== TOP 12 LOCK RULES ===")
    for r in [r for r in rules if r["tier"] == "LOCK"][:12]:
        print(f"  {r['play']:<10} {r['direction']:<5} {r['matches_if_raw']:<46} "
              f"raw {r['raw_pct']}%/{r['raw_n']:<3} → shrunk {r['shrunken_pct']}%  Δ{r['conviction_delta']:+d}")

    print()
    print("=== TOP 8 HARD_FADE RULES ===")
    for r in [r for r in rules if r["tier"] == "HARD_FADE"][:8]:
        print(f"  {r['play']:<10} {r['direction']:<5} {r['matches_if_raw']:<46} "
              f"raw {r['raw_pct']}%/{r['raw_n']:<3} → shrunk {r['shrunken_pct']}%  Δ{r['conviction_delta']:+d}")

    payload = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "lifetime_rows": len(all_rows),
        "last30_rows": len(last30),
        "prior_n": PRIOR_N,
        "min_raw_n": MIN_RAW_N,
        "summary": summary,
        "play_baselines": life_play_base,
        "rules": rules,
    }

    if dryrun:
        print("\n[refresh_cohort_signals] DRYRUN — not writing.")
        return

    body = {
        "cache_key": CACHE_KEY,
        "game_id": CACHE_KEY,
        "sport": "mlb",
        "narrative": "",
        "data": json.dumps(payload),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=cache_key",
        headers=WRITE_HEADERS, json=body, timeout=15,
    )
    if r.status_code in (200, 201, 204):
        print(f"\n[refresh_cohort_signals] upserted jerry_cache row '{CACHE_KEY}' ({len(rules)} rules)")
    else:
        print(f"\n[refresh_cohort_signals] upsert FAILED {r.status_code}: {r.text[:300]}")
        sys.exit(1)

    # Lifecycle tracking — diff today's rules vs yesterday's snapshot and
    # log added/promoted/demoted/vetoed events so we don't lose audit
    # trail when rules silently come and go. Fails open so a tracker bug
    # never blocks the cohort refresh.
    try:
        import track_cohort_lifecycle
        track_cohort_lifecycle.run(dryrun=False)
    except Exception as e:
        print(f"[refresh_cohort_signals] ⚠ lifecycle tracker failed: "
              f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    dryrun = "--dryrun" in sys.argv
    run(dryrun=dryrun)
