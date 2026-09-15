"""NCAAF TOTAL logistic regression trainer.

2026-09-15 v1.05 rebuild (was market-only, 51.8% test acc = ~market-noise).
Adds leak-safe features derivable from ncaaf_game_results itself:
  * Weather: temp, wind (known at kickoff)
  * Context: neutral_site, conference_game, week
  * Rolling team form (computed from prior games, chronological pass):
      - home_l4_ppg, home_l4_pa      (last 4 games points scored / against)
      - away_l4_ppg, away_l4_pa
      - home_l4_total_avg, away_l4_total_avg  (avg game total the team played in)
      - home_l4_over_rate, away_l4_over_rate  (rate team went O of the closing total)
Historical ncaaf_game_context has 0 rows pre-2026 so team EPA / SP+ / pace
are not available for the training set — those wait for v1.1 backfill or
switch to a live-only inference model.
"""
import argparse, json, os, sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())
SB = os.environ['SUPABASE_URL']; K = os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': f'Bearer {K}'}
MODELS_DIR = Path(__file__).parent / 'models'; MODELS_DIR.mkdir(exist_ok=True)

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

# Feature order matters for model I/O — keep stable across versions.
MARKET_FEATURES = [
    'close_total', 'open_total', 'close_spread', 'open_spread',
    'close_home_ml', 'close_away_ml',
]
CTX_FEATURES = ['temp', 'wind', 'neutral_site', 'conference_game', 'week']
ROLLING_FEATURES = [
    'home_l4_ppg', 'home_l4_pa', 'away_l4_ppg', 'away_l4_pa',
    'home_l4_total_avg', 'away_l4_total_avg',
    'home_l4_over_rate', 'away_l4_over_rate',
]
# 2026-09-15 ncaaf_total v1.06: rolling L4 team form re-enabled after
# ncaaf_game_context started persisting these fields (see migration
# 20260915b_ncaaf_ctx_l4_rolling_form.sql + load_team_rolling_form in
# ncaaf_game_context.py). Trainer computes from ncaaf_game_results;
# inference now reads matching columns from ncaaf_game_context. Prior
# v1.05 shipped without rolling features to avoid dead-signal masking.
FEATURES = MARKET_FEATURES + CTX_FEATURES + ROLLING_FEATURES

_ROLLING_WINDOW = 4  # last-4 games per team


def _to_float(v):
    if v is None: return None
    if isinstance(v, bool): return 1.0 if v else 0.0
    try: return float(v)
    except (TypeError, ValueError): return None


def _compute_rolling(rows):
    """Chronological pass. For each row, attach home_/away_ rolling
    features computed from the team's PRIOR games only. First 4 games
    per team get NaN so imputer can fill them at train time."""
    # sort ascending by (game_date, game_id) so ties are deterministic
    rows.sort(key=lambda r: (r.get('game_date') or '', r.get('game_id') or ''))
    hist = defaultdict(lambda: deque(maxlen=_ROLLING_WINDOW))
    out = []
    for r in rows:
        home, away = r.get('home_team'), r.get('away_team')
        hs = _to_float(r.get('home_score')); as_ = _to_float(r.get('away_score'))
        ct = _to_float(r.get('close_total'))
        if home is None or away is None or hs is None or as_ is None:
            out.append(None); continue
        # Snapshot rolling BEFORE appending this game
        def _summarize(team, is_home_side):
            h = hist[team]
            if not h: return (None, None, None, None)
            ppg = np.mean([g['pf'] for g in h])
            pa  = np.mean([g['pa'] for g in h])
            tot_avg = np.mean([g['tot_line'] for g in h if g['tot_line'] is not None]) if any(g['tot_line'] is not None for g in h) else None
            over_hits = [g['over_hit'] for g in h if g['over_hit'] is not None]
            over_rate = float(np.mean(over_hits)) if over_hits else None
            return (float(ppg), float(pa), float(tot_avg) if tot_avg is not None else None, over_rate)
        h_ppg, h_pa, h_tot, h_over = _summarize(home, True)
        a_ppg, a_pa, a_tot, a_over = _summarize(away, False)
        r['_rolling'] = {
            'home_l4_ppg': h_ppg, 'home_l4_pa': h_pa,
            'away_l4_ppg': a_ppg, 'away_l4_pa': a_pa,
            'home_l4_total_avg': h_tot, 'away_l4_total_avg': a_tot,
            'home_l4_over_rate': h_over, 'away_l4_over_rate': a_over,
        }
        # Update history AFTER snapshot (this game's outcome becomes prior for next game)
        actual_tot = hs + as_
        over_hit = None
        if ct is not None: over_hit = 1.0 if actual_tot > ct else 0.0
        hist[home].append({'pf': hs, 'pa': as_, 'tot_line': ct, 'over_hit': over_hit})
        hist[away].append({'pf': as_, 'pa': hs, 'tot_line': ct, 'over_hit': over_hit})
        out.append(r)
    return [r for r in out if r is not None]


def train(dry_run: bool = False):
    print('== NCAAF TOTAL logreg train (v1.05 with rolling+ctx) ==')
    # Pull everything (paginate). Include ctx columns needed for training.
    select_str = ('game_id,game_date,home_team,away_team,home_score,away_score,total_points,'
                  + ','.join(MARKET_FEATURES)
                  + ',temp,wind,neutral_site,conference_game,week')
    rows = []
    for off in range(0, 25000, 1000):
        r = requests.get(f'{SB}/rest/v1/ncaaf_game_results',
            params={'select': select_str, 'home_score': 'not.is.null',
                    'away_score': 'not.is.null', 'close_total': 'not.is.null',
                    'limit': 1000, 'offset': off, 'order': 'game_date.asc'},
            headers=H, timeout=45)
        chunk = r.json() if isinstance(r.json(), list) else []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
    print(f'  pulled {len(rows)} resolved NCAAF games w/ scores + close_total')
    if len(rows) < 500:
        print('  ⚠ insufficient data'); return

    rows = _compute_rolling(rows)
    print(f'  after rolling attach: {len(rows)} rows')

    X_rows, y_rows = [], []
    for r in rows:
        try:
            hs = float(r.get('home_score')); as_ = float(r.get('away_score'))
            line = float(r.get('close_total'))
        except (TypeError, ValueError): continue
        tot = r.get('total_points')
        if tot is None: tot = hs + as_
        else:
            try: tot = float(tot)
            except (TypeError, ValueError): tot = hs + as_
        y = 1 if tot > line else 0
        roll = r.get('_rolling') or {}
        row_feats = []
        for f in FEATURES:
            if f in ROLLING_FEATURES:
                v = roll.get(f)
            else:
                v = r.get(f)
            fv = _to_float(v)
            row_feats.append(float('nan') if fv is None else fv)
        X_rows.append(row_feats)
        y_rows.append(y)
    X = np.array(X_rows); y = np.array(y_rows)
    print(f'  X shape: {X.shape}, over hit: {y.mean()*100:.1f}%')

    valid_col_mask = ~np.all(np.isnan(X), axis=0)
    dropped = [f for f, keep in zip(FEATURES, valid_col_mask) if not keep]
    if dropped: print(f'  dropped (all-nan): {dropped}')
    features = [f for f, keep in zip(FEATURES, valid_col_mask) if keep]
    X = X[:, valid_col_mask]

    imputer = SimpleImputer(strategy='median')
    X_imp = imputer.fit_transform(X)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_imp)

    # Chronological split — train on the past, test on the most recent
    # 30% — so the test accuracy reflects out-of-sample performance the
    # way live inference will experience it (was random-shuffle prior).
    cut = int(len(y) * 0.7)
    lr = LogisticRegression(max_iter=1000, C=1.0)
    lr.fit(X_scaled[:cut], y[:cut])
    train_acc = lr.score(X_scaled[:cut], y[:cut])
    test_acc  = lr.score(X_scaled[cut:], y[cut:])
    baseline  = y[cut:].mean()
    print(f'\n  Train {train_acc*100:.1f}%   Test {test_acc*100:.1f}%   '
          f'Baseline {baseline*100:.1f}%   Lift {(test_acc-baseline)*100:+.1f}pp')

    probs = lr.predict_proba(X_scaled[cut:])[:, 1]
    print(f'\n  Probability spread (test set):')
    print(f'    min={probs.min():.4f}  max={probs.max():.4f}  '
          f'p10={np.quantile(probs,0.1):.4f}  p90={np.quantile(probs,0.9):.4f}')

    print(f'\n  Bucket hit-rates (test set):')
    for lo, hi, tier, side in [(0.65, 1.01, 'PRIME',  'OVER'),
                                (0.55, 0.65, 'STRONG', 'OVER'),
                                (0.45, 0.55, 'COIN',  'NONE'),
                                (0.35, 0.45, 'STRONG', 'UNDER'),
                                (0.00, 0.35, 'PRIME', 'UNDER')]:
        mask = (probs >= lo) & (probs < hi)
        n = int(mask.sum())
        if n == 0: continue
        if side == 'OVER':  wins = int(((y[cut:] == 1) & mask).sum())
        elif side == 'UNDER': wins = int(((y[cut:] == 0) & mask).sum())
        else: wins = 0
        print(f'    {tier}_{side:5s}: {wins}/{n} = {100*wins/n if n else 0:.1f}%')

    print(f'\n  Coefs (sorted by |weight|):')
    for f, c in sorted(zip(features, lr.coef_[0]), key=lambda x: -abs(x[1])):
        print(f'    {f:30s} {c:>+7.3f}')

    lr_full = LogisticRegression(max_iter=1000, C=1.0); lr_full.fit(X_scaled, y)
    out = {
        'version': datetime.now(timezone.utc).isoformat(),
        'features': features,
        'coefficients': lr_full.coef_[0].tolist(),
        'intercept': float(lr_full.intercept_[0]),
        'imputer_medians': imputer.statistics_.tolist(),
        'scaler_mean': scaler.mean_.tolist(),
        'scaler_scale': scaler.scale_.tolist(),
        'meta': {'test_accuracy': float(test_acc),
                 'baseline_accuracy': float(baseline),
                 'lift_pp': float((test_acc - baseline) * 100),
                 'n_train': int(cut), 'n_test': int(len(y) - cut),
                 'features_market': [f for f in features if f in MARKET_FEATURES],
                 'features_ctx':    [f for f in features if f in CTX_FEATURES],
                 'features_rolling':[f for f in features if f in ROLLING_FEATURES],
                 'trainer_version': 'v1.05_rolling_ctx_20260915'},
    }
    out_path = MODELS_DIR / 'ncaaf_total_logreg.json'
    if dry_run: print(f'  [DRY] would write {out_path}')
    else: out_path.write_text(json.dumps(out, indent=2)); print(f'  ✓ saved {out_path}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    train(dry_run=ap.parse_args().dry_run)
