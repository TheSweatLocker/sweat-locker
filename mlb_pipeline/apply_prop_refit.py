"""Post-hoc prop refit conviction (2026-07-31 · Option C step 1).

Runs AFTER generate_props.py in the cron chain. Reads today's props,
computes refit_conviction from the logistic-fit signal weights, updates
the DB in place. Legacy conviction column untouched — refit lives
alongside for A/B comparison and backwards compatibility.

Also handles the "sign-flip" fix implicitly: signals with negative
coefficients (data-driven L-predictors) naturally reduce refit_conviction
when they fire. No manual sign-flip of hand-tuned code needed.

Refit approach:
  score_raw = sum(coef[k] for k in signals if k in coefs)   # sum of
                                                             # fired
                                                             # signal
                                                             # coefficients
  normalized = 0..100 rescale from (score_min, score_max) fit-time bounds
  Prop types NOT in the refit registry: skip (refit_conviction stays NULL,
  app falls back to legacy conviction).

Usage:
    python apply_prop_refit.py [--date YYYY-MM-DD] [--dry-run]
"""
import argparse, json, os, sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass

H_READ = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
H_WRITE = {**H_READ, "Content-Type": "application/json",
           "Prefer": "resolution=merge-duplicates,return=minimal"}

WEIGHTS_V1 = Path(__file__).parent / "models" / "prop_refit_weights_v1.json"
WEIGHTS_V2 = Path(__file__).parent / "models" / "prop_refit_weights_v2.json"


def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def load_weights() -> dict:
    """Load v2 as primary + v1 as fallback for prop types v2 didn't cover.

    v2 was retrained 2026-08-01 with fixes for sign-flipped bb_under coefficients:
      - class_weight=None (was 'balanced' → sign flips at small n)
      - L1 penalty + tighter regularization (kills correlated features)
      - Higher min-sample gate (n≥60)

    Result: v2 covers 4 high-sample types (hits_over/under n=300+, ks_over/under n=80+)
    with zero sign flips. Low-sample types (bb_*, er_*, ha_*) still use v1 weights;
    Jerry synth conviction caps prevent overexposure on those uncalibrated flows.
    """
    merged = {}
    if WEIGHTS_V1.exists():
        merged = json.loads(WEIGHTS_V1.read_text())
    if WEIGHTS_V2.exists():
        v2 = json.loads(WEIGHTS_V2.read_text())
        # v2 prop_types override v1 for anything v2 retrained
        pt_merged = dict(merged.get("prop_types") or {})
        pt_merged.update(v2.get("prop_types") or {})
        merged["prop_types"] = pt_merged
        merged["version"] = "v2+v1_fallback"
        merged["v2_trained_at"] = v2.get("trained_at")
    if not merged:
        print(f"  ⚠ no refit weights available")
        return {}
    return merged


# 2026-08-23 REFIT BLACKLIST — prop families with known bad trained
# weights. Sign-flipped coefficients produce nonsense refit values that
# undermine trust in the model. Return None → app falls back to legacy
# conviction until weights are retrained properly.
#
# bb_under/under: BOTH v1 AND v2 weights have sign-flipped coefs.
#   v1: aggressive_opp=-0.30, clean_start=-0.25, book_recal=-0.13
#       (all should be POSITIVE — fewer walks vs aggressive opps,
#       clean prior starts, book giving line above proj all support
#       UNDER hitting).
#   v2: last7_control=-0.3538 sign-flipped (elite recent control
#       should boost UNDER, not crush it). Was supposed to fix v1
#       but introduced a different sign flip.
#   Observed: elite BB command pitchers (Martinez 0.9 BB/9 last 7)
#   land refit=22.4 instead of correctly ~85-95. Legacy conviction
#   is directionally right (PRIME 97) so users get legacy while
#   refit retrains.
REFIT_BLACKLIST = frozenset({
    ('bb_under', 'under'),
    # ks_under/under: pos_sum=0.000, all 5 coefs are negative in merged
    # v1/v2 weights. book_recalibration=-0.49, l3_k=-0.29, etc. NO
    # signal can produce a positive refit → every fired-signal ks_under
    # lands crushed to floor. Structurally broken until retrain.
    ('ks_under', 'under'),
    # ha_over/over: pos_sum=0.000, only negative coefs (l3_hard=-0.081,
    # park=-0.077). Same structural issue as ks_under. Rare prop family
    # so lower priority but same blacklist treatment.
    ('ha_over', 'over'),
})


def compute_refit(prop_type: str, direction: str, signals: dict,
                  weights: dict) -> tuple[float, str] | None:
    """Returns (refit_conviction 0-100, refit_version) or None if skipped."""
    # Blacklist check first — skip prop families with known bad weights
    if (prop_type, direction) in REFIT_BLACKLIST:
        return None
    key = f"{prop_type}/{direction}"
    pw = (weights.get("prop_types") or {}).get(key)
    if not pw:
        return None
    coefs = pw["coefficients"]
    score_min = pw["score_min"]; score_max = pw["score_max"]
    # Sum of fired-signal coefficients (positive coefs help, negative hurt)
    fired = [k for k in (signals or {}).keys() if k in coefs]
    # 2026-08-23 FIRED-EMPTY GUARD: when zero signals fire, refit has
    # nothing to compute from — return None so downstream sees NULL and
    # falls back to legacy conviction, rather than shipping the formula's
    # baseline (which produces uniform fake numbers per family: all
    # hits_over land 48.5, all ks_over 37.7, all er_over 0.0). Bryce
    # Miller er_over 2.5 on 8/23 was a canonical case — legacy said LEAN
    # 68 while refit showed 0.0 because upstream signals wipe (fixed in
    # backfill_prop_lookback.py this same commit) left fired empty.
    if not fired:
        return None
    raw = sum(coefs[k] for k in fired)
    # Rescale to 0-100 using the training-time observed range
    if score_max <= score_min:
        conv = 50.0
    else:
        conv = 100.0 * (raw - score_min) / (score_max - score_min)
    # 2026-08-22: cap at 95 (never 100). User flagged: seeing 100s across
    # the app is a credibility hit — nothing in real prop betting hits at
    # 100%. Also today: 30 pitcher props saturated to 100 because their
    # default 6-signal pattern (l3_k, park, xera, l3_era, opp_wrc, opp_k_rate)
    # tops the training-time observed score_max. This is model overfitting,
    # not real 100% confidence. Cap at 95 preserves ordering + relative
    # ranking but ends the "100" display bug. Retrain with broader feature
    # diversity is a bigger task queued in the tier-calibration merge.
    conv = max(0.0, min(95.0, conv))
    # 2026-08-10: prefer v2 trained_at (fresh) over v1's stale stamp.
    # If v2 was merged in, use its trained_at; else fall back to v1.
    stamp = weights.get("v2_trained_at") or weights.get("trained_at", "v1")
    return round(conv, 1), stamp


def run(game_date: str | None = None, dry_run: bool = False) -> None:
    gd = game_date or today_et()
    print(f"=== apply_prop_refit · {gd} ===")

    weights = load_weights()
    if not weights or not weights.get("prop_types"):
        print("  ⛔ no weights loaded — abort")
        return
    covered = list(weights["prop_types"].keys())
    print(f"  refit weights v={weights.get('trained_at','?')[:10]}, "
          f"{len(covered)} prop types covered")

    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props",
        headers=H_READ,
        params={"game_date": f"eq.{gd}",
                # 2026-08-23: also fetch `tier` so we can gate PRIME
                # hits_over promotions on refit disagreement (see below).
                "select": "id,prop_type,direction,conviction,tier,signals",
                "limit": 500},
        timeout=30,
    )
    props = r.json() if r.status_code == 200 else []
    print(f"  {len(props)} props to consider")

    updated = skipped = hits_capped = 0
    for p in props:
        result = compute_refit(p["prop_type"], p["direction"],
                                p.get("signals") or {}, weights)
        if result is None:
            skipped += 1
            continue
        conv, version = result

        # 2026-08-23 HITS_OVER REFIT-GATE (from 8/22 audit).
        # All 3 hits_over PRIME cards on 8/22 had legacy conviction 78-84
        # BUT refit_conviction only 48.5 — refit was telling us these were
        # coin flips. Result: 1-2 (33%). Meanwhile STRONG hits_over at the
        # same refit 48.5 went 8-1 by short-line luck. The rule: when the
        # refit model disagrees strongly with the legacy tier (refit < 60
        # on a PRIME), cap the tier at STRONG. Same architecture as the
        # MC-dissent gate for games — treat internal model disagreement as
        # a hygiene downgrade, not a W/L booster.
        patch: dict = {"refit_conviction": conv, "refit_version": version[:10]}
        prop_type = (p.get("prop_type") or "").lower()
        current_tier = (p.get("tier") or "").upper()
        if (prop_type == "hits_over"
                and current_tier == "PRIME"
                and conv < 60):
            patch["tier"] = "STRONG"
            # Also cap conviction at STRONG ceiling so display doesn't show
            # PRIME-tier conviction number on a STRONG-tier bucket.
            legacy_conv = p.get("conviction")
            if isinstance(legacy_conv, (int, float)) and legacy_conv > 65:
                patch["conviction"] = 65
            hits_capped += 1
            print(f"  🔽 hits_over PRIME cap: id={p['id']} "
                  f"legacy={p.get('conviction')} refit={conv} → tier=STRONG")

        if dry_run:
            print(f"  [DRY] id={p['id']} {p['prop_type']}/{p['direction']}: "
                  f"conv={p.get('conviction')} → refit={conv}")
            updated += 1
            continue
        pr = requests.patch(
            f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props?id=eq.{p['id']}",
            headers=H_WRITE,
            json=patch,
            timeout=10,
        )
        if pr.status_code in (200, 204):
            updated += 1
        else:
            print(f"  ⚠ patch id={p['id']}: {pr.status_code} {pr.text[:120]}")

    print(f"\n  ✅ {updated} refit_conviction rows written")
    print(f"  ⏭  {skipped} skipped (prop type not in refit registry)")
    if hits_capped:
        print(f"  🔽 {hits_capped} hits_over PRIME rows capped to STRONG (refit < 60)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--date")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(game_date=args.date, dry_run=args.dry_run)
