"""How much of each LR prediction is built on real data vs training medians?

mlb_ml_logreg_predict.predict_home_win does:

    v = _to_float(ctx.get(f))
    if v is None: v = medians[i]        # training-set median
    scaled = (v - means[i]) / scales[i]
    z_sum += coefs[i] * scaled

so a feature the context does not carry is silently replaced by the
training set's median and still contributes `coefs[i] * scaled` to the
log-odds. Nothing records that it happened. The predict file's own comment
says it is expected -- "Features missing from ctx are imputed at predict
time using training-set medians" -- which makes it structural rather than
occasional.

This measures it per sport against TODAY'S live context rows: of the
features each model needs, how many are actually present, and how much of
|z| the imputed ones account for.

A pick whose features are mostly medians is a prediction about the average
training team, not about these two teams, and its p_home_win should not be
read as if it were.

USAGE
  python _measure_lr_imputation_1031.py
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

HERE = Path(__file__).parent
_env = HERE / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ['SUPABASE_KEY'])
H = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY}
MODELS = {'MLB': 'mlb_ml_logreg.json', 'NFL': 'nfl_ml_logreg.json',
          'NCAAF': 'ncaaf_ml_logreg.json', 'NBA': 'nba_ml_logreg.json',
          'NHL': 'nhl_ml_logreg.json'}
CTX = {'MLB': 'mlb_game_context', 'NFL': 'nfl_game_context',
       'NCAAF': 'ncaaf_game_context', 'NBA': 'nba_game_context',
       'NHL': 'nhl_game_context'}
#: forward-looking dates, so this reflects picks the engine would make now
SINCE = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).date()


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    print('=== LR feature availability, live contexts since %s ===' % SINCE)
    print('  %-6s %-9s %-11s %-11s %s'
          % ('sport', 'features', 'in ctx', 'imputed', 'share of |z| imputed'))
    for sport, fn in MODELS.items():
        p = HERE / 'models' / fn
        if not p.exists():
            print('  %-6s model file missing (%s)' % (sport, fn))
            continue
        try:
            model = json.loads(p.read_text())
        except Exception as e:                                 # noqa: BLE001
            print('  %-6s model unreadable: %s' % (sport, e))
            continue
        feats = model.get('features') or []
        coefs = model.get('coefficients') or []
        means = model.get('scaler_mean') or []
        scales = model.get('scaler_scale') or []
        meds = model.get('imputer_medians') or []
        if not (len(feats) == len(coefs) == len(means) == len(scales)
                == len(meds)):
            print('  %-6s model arrays disagree in length (%d/%d/%d/%d/%d) '
                  '-- NOT measured' % (sport, len(feats), len(coefs),
                                       len(means), len(scales), len(meds)))
            continue

        # which of those columns does the context table actually have?
        r = requests.get(SB + '/rest/v1/' + CTX[sport], headers=H, timeout=90,
                         params={'select': '*', 'limit': 1})
        if r.status_code != 200 or not r.json():
            print('  %-6s context unreadable -- NOT measured' % sport)
            continue
        cols = set(r.json()[0].keys())
        present = [x for x in feats if x in cols]

        rows = requests.get(SB + '/rest/v1/' + CTX[sport], headers=H,
                            timeout=120,
                            params={'select': '*',
                                    'game_date': 'gte.%s' % SINCE,
                                    'limit': 400})
        rows = rows.json() if rows.status_code == 200 else []
        if not rows:
            print('  %-6s %-9d %-11d no forward games to measure'
                  % (sport, len(feats), len(present)))
            continue

        # ══ ONLY ROWS THE MODEL ACTUALLY SCORED ══════════════════
        # The first cut of this sampled every row from the last 3 days and
        # reported NHL at 4-of-4 features imputed, 100% of |z| from
        # training medians -- which would mean a constant p_home_win. The
        # STORED values are distinct per game (NHL 10-29: 7 distinct of 7,
        # 0.4415..0.6653), so the figure was an artifact: the window was
        # mostly FUTURE games whose feature columns are not filled yet.
        # Measure on the rows that carry a prediction, which is the only
        # population where "was this feature available" is a real question.
        def _scored(g):
            pp = g.get('primary_play')
            if isinstance(pp, str):
                try:
                    pp = json.loads(pp)
                except Exception:
                    return False
            return isinstance(pp, dict) and pp.get('_lr_p_home_win') is not None
        rows = [g for g in rows if _scored(g)]
        if not rows:
            print('  %-6s %-9d %-11d no SCORED rows in the window'
                  % (sport, len(feats), len(present)))
            continue
        per_game_imp, per_game_zshare = [], []
        for g in rows:
            imp = 0
            z_imp = z_all = 0.0
            for i, name in enumerate(feats):
                v = f(g.get(name))
                was_imp = v is None
                if was_imp:
                    v = meds[i]
                    imp += 1
                sc = ((v - means[i]) / scales[i]) if scales[i] else 0.0
                c = abs(coefs[i] * sc)
                z_all += c
                if was_imp:
                    z_imp += c
            per_game_imp.append(imp)
            per_game_zshare.append((z_imp / z_all) if z_all else 0.0)
        per_game_imp.sort()
        per_game_zshare.sort()
        med_imp = per_game_imp[len(per_game_imp) // 2]
        med_z = per_game_zshare[len(per_game_zshare) // 2]
        print('  %-6s %-9d %-11d %-11s %.1f%%  (median, n=%d games)'
              % (sport, len(feats), len(present),
                 '%d of %d' % (med_imp, len(feats)), 100.0 * med_z,
                 len(rows)))
        missing = [x for x in feats if x not in cols]
        if missing:
            print('         columns the context does not have: %s%s'
                  % (', '.join(missing[:6]),
                     ' ... +%d' % (len(missing) - 6) if len(missing) > 6
                     else ''))
    print('\n  "imputed" = the context row has no value, so the model used')
    print('  the TRAINING median. Share of |z| is how much of the absolute')
    print('  log-odds movement those substituted features account for.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
