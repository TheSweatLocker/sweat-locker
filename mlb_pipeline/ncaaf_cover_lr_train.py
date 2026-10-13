"""NCAAF cover model trained on TEAM features — the LR we should have had.

ANDY 2026-10-13: "Lets get the build done."

WHY A NEW TRAINER RATHER THAN A RETRAIN. ncaaf_ml_logreg_train.py reads
ncaaf_game_results, which holds market columns and scores and nothing else.
The model it produces is therefore a function of the closing line, and
measured against that line it is strictly worse: log loss 0.5293 vs 0.5167,
Brier 0.1780 vs 0.1739, and corr(LR, market) = +0.9702 on n=3,645. The NFL
sibling proved the same point by itself — its lasso zeroed 19 of 22 features,
keeping only close_spread, open_spread and spread_x_total.

So the defect was never the method. MLB's logreg uses 107 features of which
79 are non-market (pitcher xERA, team xwOBA, wRC+) and it is the only LR the
engine consumes. This trains the NCAAF equivalent from ncaaf_game_context,
where the team features actually live.

WHAT IT PREDICTS. Whether the HOME side covers the closing spread — not who
wins. Winning is what the moneyline already prices; covering is the market we
actually bet, and its benchmark is unambiguous: 52.38% at -110.

    home_cover = (home_margin + close_spread) > 0

NCAAF stores close_spread NEGATIVE for a home favourite, so a -7 home
favourite covers when the margin exceeds 7. Verified against stored
spread_result at 441/442 earlier today.

FOUR DISCIPLINES, each one paid for earlier in this audit:

 1. FCS OPPONENTS ARE EXCLUDED, NOT IMPUTED. Away features are ~25pp thinner
    than home because 138 rows have an FCS visitor — Mercyhurst, UT Martin,
    Duquesne, Howard, Norfolk State — with a median closing line of -33.0
    against -5.5 elsewhere. The old trainer's SimpleImputer(median) would
    hand Mercyhurst an average-FBS SP+ and teach the model that a 33-point
    cupcake is a normal game. Requiring both SP+ values present drops those
    rows and lifts every other feature's coverage to 95-100%.

 2. ENGINE OUTPUTS ARE EXCLUDED. sweat_score, signal_confluence_net,
    projected_*, sp_plus_pred_*, epa_pred_*, panel_*, mc_* are all the
    engine's own conclusions. A model fed those would advise the engine using
    the engine's answer — the same circularity the NBA Monte Carlo was built
    to avoid.

 3. LEAKAGE WAS CHECKED, NOT ASSUMED. Context features could have been
    restamped with current values, which would make any backtest fiction.
    Tested: of 128 home teams appearing on 3+ dates, 0 have a static sp_plus
    and all 128 drift (UTEP -8.53 -> -19.70 -> -24.00 -> -24.60). They are
    as-of-game values.

 4. THE TEST IS OUT-OF-SAMPLE AND AGAINST A CONTROL. 316 rows against 17
    features overfits trivially, so the split is time-ordered (train on the
    earlier games, test on the later), regularisation strength is chosen on
    the training half only, and a market-only model is fitted alongside as
    the control. A team model that cannot beat the line on held-out games is
    not an edge, and this prints both so the comparison cannot be skipped.

WRITES models/ncaaf_cover_lr.json ONLY on --save, and only when the held-out
result justifies it. Never writes a pick.

CLI
    python ncaaf_cover_lr_train.py
    python ncaaf_cover_lr_train.py --save
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import requests
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

import market_line as ML

OUT = Path(__file__).parent / 'models' / 'ncaaf_cover_lr.json'

#: home-minus-away gap features, all raw team stats. Verified >=95% populated
#: once FCS visitors are excluded.
GAPS = ['sp_plus', 'sp_overall', 'off_epa_pp', 'def_epa_pp', 'def_ppg',
        'third_down_pct', 'def_success_rate_allowed', 'pass_yds_pg',
        'rush_yds_pg', 'ol_avg_wt', 'dl_avg_wt', 'def_pass_epa_allowed',
        'def_rush_epa_allowed', 'penalties_pg', 'turnovers_pg', 'top_min',
        'def_sacks_pg']

BREAKEVEN = 52.38


def page(table, params, cap=60000):
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        r = requests.get(f'{ML.SB}/rest/v1/{table}', headers=ML.H, params=q,
                         timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ✗ {table} HTTP {r.status_code}: {r.text[:140]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000 or len(out) >= cap:
            return out
        off += 1000


def f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def build():
    today = time.strftime('%Y-%m-%d')
    ctx = page('ncaaf_game_context', {'select': '*'})
    res = {}
    for x in page('ncaaf_game_results', {
            'select': 'game_date,home_team,away_team,home_score,away_score,'
                      'close_spread'}):
        if x.get('home_score') is None:
            continue
        res[(str(x['game_date'])[:10], str(x['home_team']).lower(),
             str(x['away_team']).lower())] = x
    rows = []
    skipped_fcs = skipped_feat = 0
    for g in ctx:
        d = str(g.get('game_date'))[:10]
        if d >= today:
            continue
        m = res.get((d, str(g.get('home_team')).lower(),
                     str(g.get('away_team')).lower()))
        if not m:
            continue
        # FBS filter: both SP+ present. This is the FCS exclusion.
        if g.get('home_sp_plus') is None or g.get('away_sp_plus') is None:
            skipped_fcs += 1
            continue
        cs = f(m.get('close_spread'))
        hs, as_ = f(m.get('home_score')), f(m.get('away_score'))
        if cs is None or hs is None or as_ is None:
            continue
        resid = (hs - as_) + cs          # >0 means the home side covered
        if abs(resid) < 1e-9:
            continue                      # push carries no label
        x = []
        ok = True
        for k in GAPS:
            h, a = f(g.get(f'home_{k}')), f(g.get(f'away_{k}'))
            if h is None or a is None:
                ok = False
                break
            x.append(h - a)
        if not ok:
            skipped_feat += 1
            continue
        rows.append({'date': d, 'x': x, 'y': 1 if resid > 0 else 0, 'cs': cs})
    rows.sort(key=lambda r: r['date'])
    print(f'  {len(rows)} usable games · {skipped_fcs} skipped as FCS visitor '
          f'· {skipped_feat} skipped for a missing gap feature')
    return rows


def fit_eval(Xtr, ytr, Xte, yte, C):
    sc = StandardScaler().fit(Xtr)
    lr = LogisticRegression(C=C, max_iter=2000, penalty='l2')
    lr.fit(sc.transform(Xtr), ytr)
    ptr = lr.predict_proba(sc.transform(Xtr))[:, 1]
    pte = lr.predict_proba(sc.transform(Xte))[:, 1]
    return lr, sc, ptr, pte


def cover_at(p, y, thr):
    """Bet the side the model prefers, only when it is confident enough."""
    picks = [(pi, yi) for pi, yi in zip(p, y)
             if abs(pi - 0.5) >= thr]
    if not picks:
        return 0, 0.0, 0.0
    w = sum(1 for pi, yi in picks if (pi > 0.5) == (yi == 1))
    n = len(picks)
    roi = (w * (100 / 110) - (n - w)) / n * 100
    return n, w / n * 100, roi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--save', action='store_true')
    a = ap.parse_args()
    print('=== NCAAF cover model · team features, not the market ===')
    rows = build()
    if len(rows) < 150:
        print('  too few rows to train honestly')
        return 1
    cut = int(len(rows) * 0.70)
    tr, te = rows[:cut], rows[cut:]
    print(f'  time-ordered split: train {len(tr)} '
          f'({tr[0]["date"]}..{tr[-1]["date"]}) · '
          f'test {len(te)} ({te[0]["date"]}..{te[-1]["date"]})')
    Xtr = np.array([r['x'] for r in tr], dtype=float)
    ytr = np.array([r['y'] for r in tr])
    Xte = np.array([r['x'] for r in te], dtype=float)
    yte = np.array([r['y'] for r in te])
    print(f'  base rate: train {ytr.mean()*100:.1f}% home covers · '
          f'test {yte.mean()*100:.1f}%')

    # Regularisation chosen on the TRAIN half only, by an inner time split.
    icut = int(len(tr) * 0.75)
    best = None
    print(f"\n  {'C':>8}{'inner-val acc':>16}")
    for C in (0.003, 0.01, 0.03, 0.1, 0.3, 1.0):
        _lr, _sc, _p1, p2 = fit_eval(Xtr[:icut], ytr[:icut],
                                     Xtr[icut:], ytr[icut:], C)
        acc = float(((p2 > 0.5) == (ytr[icut:] == 1)).mean())
        print(f'  {C:>8}{acc*100:>15.1f}%')
        if best is None or acc > best[1]:
            best = (C, acc)
    C = best[0]
    print(f'  chosen C={C} (inner-validation only — the test half is untouched)')

    lr, sc, ptr, pte = fit_eval(Xtr, ytr, Xte, yte, C)
    tr_acc = float(((ptr > 0.5) == (ytr == 1)).mean())
    te_acc = float(((pte > 0.5) == (yte == 1)).mean())
    print(f'\n  TEAM model   train acc {tr_acc*100:5.1f}%   '
          f'HELD-OUT acc {te_acc*100:5.1f}%')

    # Control: market only (the closing spread), same split and method.
    Mtr = np.array([[r['cs']] for r in tr], dtype=float)
    Mte = np.array([[r['cs']] for r in te], dtype=float)
    _lrm, _scm, mptr, mpte = fit_eval(Mtr, ytr, Mte, yte, 1.0)
    m_acc = float(((mpte > 0.5) == (yte == 1)).mean())
    print(f'  MARKET-ONLY  train acc '
          f'{float(((mptr>0.5)==(ytr==1)).mean())*100:5.1f}%   '
          f'HELD-OUT acc {m_acc*100:5.1f}%   <- control')

    print(f'\n  held-out betting record by confidence threshold '
          f'(breakeven {BREAKEVEN}%):')
    print(f"    {'|p-0.5| >=':>12}{'bets':>7}{'cover%':>9}{'ROI':>9}")
    for thr in (0.0, 0.02, 0.05, 0.08, 0.12):
        n, cov, roi = cover_at(pte, yte, thr)
        if n < 10:
            print(f'    {thr:>12}{n:>7}   too thin')
            continue
        print(f'    {thr:>12}{n:>7}{cov:>8.1f}%{roi:>8.1f}%')

    coefs = sorted(zip(GAPS, lr.coef_[0]), key=lambda z: -abs(z[1]))
    print('\n  strongest coefficients (standardised):')
    for k, c in coefs[:8]:
        print(f'    {k:<28}{c:+.4f}')

    # ══ THE BASE RATE IS THE REAL BAR ══
    # A first run reported the market control at 58.0% against the team
    # model's 53.4%, which reads as "the market wins". But the test window's
    # base rate IS 58.0% home covers, so always answering "home" scores
    # 58.0% there — neither model was beating a coin weighted to that
    # window. With n=88 and a train base rate of 50.7%, that skew is
    # sampling noise, and without this line it would have been mistaken for
    # market skill.
    base = max(float(yte.mean()), 1.0 - float(yte.mean()))
    print(f'  MAJORITY CLASS                       '
          f'HELD-OUT acc {base*100:5.1f}%   <- the bar BOTH must clear')

    beats = (te_acc > m_acc and te_acc > base
             and te_acc * 100 > BREAKEVEN)
    print(f'\n  VERDICT: team model {"BEATS" if beats else "does NOT beat"} '
          f'its bars out of sample — {te_acc*100:.1f}% against market '
          f'{m_acc*100:.1f}%, majority class {base*100:.1f}%, breakeven '
          f'{BREAKEVEN}%')
    print(f'  overfit gap: train {tr_acc*100:.1f}% -> held-out '
          f'{te_acc*100:.1f}% ({(tr_acc - te_acc)*100:+.1f}pp)')
    if not beats:
        print('  Not saved. A team model that cannot beat the line on')
        print('  held-out games is not an edge, and shipping it would add a')
        print('  voter with nothing behind it.')
        return 0
    if not a.save:
        print('  --save not passed; model not written.')
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        'version': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'target': 'home_covers_close_spread',
        'features': [f'gap_{k}' for k in GAPS],
        'coefficients': [float(c) for c in lr.coef_[0]],
        'intercept': float(lr.intercept_[0]),
        'scaler_mean': [float(v) for v in sc.mean_],
        'scaler_scale': [float(v) for v in sc.scale_],
        'C': C, 'n_train': len(tr), 'n_test': len(te),
        'heldout_acc': te_acc, 'market_control_acc': m_acc,
        'notes': ('Gaps are home_<stat> - away_<stat>. FCS visitors excluded '
                  '(both SP+ required). Engine outputs excluded.'),
    }, indent=2), encoding='utf-8')
    print(f'  ✓ wrote {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
