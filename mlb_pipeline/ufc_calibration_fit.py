"""UFC model calibration fitter (2026-08-17).

Trains an isotonic regression calibrator on top of the XGBoost model's
raw p_winner_a output. Maps model probability → historically-observed
probability using graded fight results.

Diagnosis (as of 2026-08-17):
  Model 85-95% → actual 40% (n=5)
  Model 65-75% → actual 0%  (n=4)
  Model overconfident across the board

Output: models/ufc_calibrator_v1.pkl — an IsotonicRegression pickle that
ufc_predict.py loads and applies to raw XGBoost predictions.

This is a one-shot fit script. Rerun weekly during UFC seasons as new
graded fights come in. Requires:
  * scikit-learn
  * At least 30 graded fights for a meaningful fit (target: 100+)

USAGE:
  python ufc_calibration_fit.py                        # last 180d
  python ufc_calibration_fit.py --days 365 --dry-run   # inspect only
"""
from __future__ import annotations
import argparse, os, pickle, sys
from datetime import date, timedelta
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def fetch_graded_fights(days: int) -> list[dict]:
    """Pull graded UFC picks with raw model prob + actual outcome.

    Reads from ufc_picks (the model's own output), not jerry_reads. Every
    graded fight contributes one (p_winner_a, actual_a_won) pair — isotonic
    on prob-of-A is monotone in prob-of-B so a single fit covers both sides.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    r = requests.get(f'{SB}/rest/v1/ufc_picks',
                     headers=H,
                     params={'event_date': f'gte.{cutoff}',
                             'winner_actual': 'not.is.null',
                             'select': 'event_date,p_winner_a,winner_actual',
                             'limit': '2000'},
                     timeout=30)
    rows = r.json() if r.status_code == 200 else []

    pairs = []
    for row in rows:
        p_a = row.get('p_winner_a')
        wa = (row.get('winner_actual') or '').lower()
        if p_a is None or wa not in ('a', 'b'): continue
        try: p_a = float(p_a)
        except (TypeError, ValueError): continue
        outcome = 1 if wa == 'a' else 0
        pairs.append((p_a, outcome))
    return pairs


def fit_calibrator(pairs: list[tuple[float, int]]):
    """Train IsotonicRegression on (raw_prob, outcome) pairs."""
    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        print('  ⚠ scikit-learn not installed. Run: pip install scikit-learn')
        return None
    if len(pairs) < 20:
        print(f'  ⚠ only {len(pairs)} graded fights — need 20+ for meaningful fit')
        print(f'     (target: 100+ before flipping into production)')
        return None
    X = [p for p, _ in pairs]
    y = [o for _, o in pairs]
    iso = IsotonicRegression(y_min=0.05, y_max=0.95, out_of_bounds='clip')
    iso.fit(X, y)
    return iso


def summarize_fit(pairs: list[tuple[float, int]], iso) -> None:
    """Report how the fitted calibrator remaps each model band."""
    bands = [(0.85, 0.95), (0.75, 0.85), (0.65, 0.75), (0.55, 0.65), (0.45, 0.55)]
    print(f'\n  Band-level comparison (n={len(pairs)}):')
    print(f'  {"model band":<15} {"n":<5} {"actual":<9} {"calibrated":<12}')
    for lo, hi in bands:
        band = [(p, o) for p, o in pairs if lo <= p < hi]
        if not band: continue
        n = len(band)
        actual = sum(o for _, o in band) / n
        avg_p = sum(p for p, _ in band) / n
        calibrated = float(iso.predict([avg_p])[0])
        print(f'  {lo*100:.0f}-{hi*100:.0f}%          n={n:<3} {actual*100:>5.1f}%   {calibrated*100:>5.1f}%')


def run(days: int = 180, dry_run: bool = False):
    print(f'=== ufc_calibration_fit · last {days} days ===')
    pairs = fetch_graded_fights(days)
    print(f'  {len(pairs)} graded (model_prob, outcome) pairs')
    iso = fit_calibrator(pairs)
    if iso is None: return
    summarize_fit(pairs, iso)
    if dry_run:
        print(f'\n  [DRY] would save models/ufc_calibrator_v1.pkl')
        return
    models_dir = Path(__file__).parent / 'models'
    models_dir.mkdir(exist_ok=True)
    out_path = models_dir / 'ufc_calibrator_v1.pkl'
    with open(out_path, 'wb') as f:
        pickle.dump({'calibrator': iso, 'n_samples': len(pairs),
                     'trained_at': date.today().isoformat()}, f)
    print(f'\n  ✓ saved {out_path}')
    print(f'\n  Next: update ufc_predict.py to load + apply this calibrator')
    print(f'  (see project_ufc_model_broken_817 memo for wiring plan)')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=180)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(days=args.days, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
