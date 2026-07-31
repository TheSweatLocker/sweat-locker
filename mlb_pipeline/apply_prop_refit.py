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

WEIGHTS_PATH = Path(__file__).parent / "models" / "prop_refit_weights_v1.json"


def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def load_weights() -> dict:
    if not WEIGHTS_PATH.exists():
        print(f"  ⚠ no refit weights at {WEIGHTS_PATH}")
        return {}
    return json.loads(WEIGHTS_PATH.read_text())


def compute_refit(prop_type: str, direction: str, signals: dict,
                  weights: dict) -> tuple[float, str] | None:
    """Returns (refit_conviction 0-100, refit_version) or None if skipped."""
    key = f"{prop_type}/{direction}"
    pw = (weights.get("prop_types") or {}).get(key)
    if not pw:
        return None
    coefs = pw["coefficients"]
    score_min = pw["score_min"]; score_max = pw["score_max"]
    # Sum of fired-signal coefficients (positive coefs help, negative hurt)
    fired = [k for k in (signals or {}).keys() if k in coefs]
    raw = sum(coefs[k] for k in fired)
    # Rescale to 0-100 using the training-time observed range
    if score_max <= score_min:
        conv = 50.0
    else:
        conv = 100.0 * (raw - score_min) / (score_max - score_min)
    conv = max(0.0, min(100.0, conv))
    return round(conv, 1), weights.get("trained_at", "v1")


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
                "select": "id,prop_type,direction,conviction,signals",
                "limit": 500},
        timeout=30,
    )
    props = r.json() if r.status_code == 200 else []
    print(f"  {len(props)} props to consider")

    updated = skipped = 0
    for p in props:
        result = compute_refit(p["prop_type"], p["direction"],
                                p.get("signals") or {}, weights)
        if result is None:
            skipped += 1
            continue
        conv, version = result
        if dry_run:
            print(f"  [DRY] id={p['id']} {p['prop_type']}/{p['direction']}: "
                  f"conv={p.get('conviction')} → refit={conv}")
            updated += 1
            continue
        pr = requests.patch(
            f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props?id=eq.{p['id']}",
            headers=H_WRITE,
            json={"refit_conviction": conv, "refit_version": version[:10]},
            timeout=10,
        )
        if pr.status_code in (200, 204):
            updated += 1
        else:
            print(f"  ⚠ patch id={p['id']}: {pr.status_code} {pr.text[:120]}")

    print(f"\n  ✅ {updated} refit_conviction rows written")
    print(f"  ⏭  {skipped} skipped (prop type not in refit registry)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--date")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(game_date=args.date, dry_run=args.dry_run)
