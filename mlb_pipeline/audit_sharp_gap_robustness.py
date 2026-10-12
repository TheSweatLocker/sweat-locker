"""Does the one surviving sharp-money cell hold up, or is it the 1-in-24?

CONTEXT. audit_sharp_money_edges.py scanned sharp-money divergence against
results across MLB / NCAAF / NFL and two markets. Sharp money ALONE lost
money in every single sport and market. Exactly one cell survived both the
hit-rate band and the ROI test:

    NCAAF ml · divergence >= +5 AND SP+ gap AGREES
        75-29  72.1%  vs a 44.1% side base  ·  ROI +5.7%  n=104
    NCAAF ml · divergence >= +10 AND SP+ gap AGREES
        44-21  67.7%  vs 42.3%              ·  ROI +8.0%  n=65

I expected that to be a favourite-selection artefact and it was not — the
money agreed. But it is ONE cell out of roughly 24, and ~1 in 20 clears by
luck. So before it is allowed near the engine it has to pass the tests that
kill lucky cells, which is what this file is.

THE FIVE TESTS
  1. ROI standard error. A hit-rate 2SE band says nothing about money when
     prices are mixed. Bootstrap the ROI distribution and report whether
     zero sits inside it. An ROI whose interval straddles 0 is not an edge.
  2. TEMPORAL SPLIT. Fit nothing, just cut the season in half by date. A
     real pattern appears in both halves. A lucky one lives in one.
  3. PRICE MIX. If the cell's profit comes from a handful of long-shot
     winners, the mean ROI is a tail artefact. Report the price
     distribution and the ROI with the single best bet removed.
  4. DROP-ONE / JACKKNIFE. Remove the best and worst bet; an edge that
     needs one game is not an edge.
  5. THE SAME CELL ON THE SPREAD. Reported for honesty: the ATS twin is
     -4.5%. That does not automatically refute an ML edge (favourites can
     win without covering, which is specifically an ML edge) but it does
     mean the claim is narrow: it is about who WINS, not by how much, and
     it must be stated that way or not at all.

WRITES NOTHING.

CLI
    python audit_sharp_gap_robustness.py
    python audit_sharp_gap_robustness.py --div 10
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

import market_line as ML


def page(table, params, cap=120000):
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        body = None
        for _ in range(4):
            try:
                r = requests.get(f'{ML.SB}/rest/v1/{table}', headers=ML.H,
                                 params=q, timeout=180)
                if r.status_code in (200, 206):
                    body = r.json()
                    break
            except Exception:                                  # noqa: BLE001
                pass
            time.sleep(4)
        if body is None:
            return out
        out += body
        if len(body) < 1000 or len(out) >= cap:
            return out
        off += 1000


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def profit(odds, won):
    o = f(odds)
    if o is None or o == 0:
        return None
    if won:
        return (o / 100.0) if o > 0 else (100.0 / -o)
    return -1.0


def boot_ci(vals, iters=20000, lo=2.5, hi=97.5, seed=7):
    """Percentile bootstrap CI of the mean. Mixed prices make the analytic
    SE unreliable, and the whole question is whether 0 is inside."""
    rng = random.Random(seed)
    n = len(vals)
    means = []
    for _ in range(iters):
        s = 0.0
        for _i in range(n):
            s += vals[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    return (means[int(len(means) * lo / 100)],
            means[int(len(means) * hi / 100)])


def build(sport, market, div_min):
    ctx_t = f'{sport.lower()}_game_context'
    res_t = f'{sport.lower()}_game_results'
    sign = +1 if sport == 'NFL' else -1
    ml_cols = (('home_ml_close', 'away_ml_close') if sport == 'MLB'
               else ('close_home_ml', 'close_away_ml'))

    sp = [x for x in page('public_splits_archive', {
        'select': 'game_id,market,pick_side,cz_money_pct,cz_bets_pct,'
                  'fr_handle_pct,fr_bettors_pct,ftp_money_pct,ftp_bets_pct,'
                  'oc_money_pct,oc_bets_pct,captured_at',
        'sport': f'eq.{sport}'}) if str(x.get('market')) == market]
    ctx = {str(g['game_id']): g for g in page(ctx_t, {'select': '*'})}
    res = page(res_t, {'select': 'game_id,game_date,home_team,away_team,'
                                 'home_score,away_score,close_spread,'
                                 + ','.join(ml_cols)})
    by_id = {str(r['game_id']): r for r in res if r.get('game_id')}
    idx = {(str(r['game_date'])[:10], str(r['home_team']).lower(),
            str(r['away_team']).lower()): r for r in res}

    latest = {}
    for x in sp:
        k = (str(x['game_id']), str(x.get('pick_side') or '').upper())
        c = str(x.get('captured_at') or '')
        if k not in latest or c > latest[k][0]:
            latest[k] = (c, x)

    out = []
    for (gid, side), (_c, x) in latest.items():
        if side not in ('HOME', 'AWAY'):
            continue
        g = ctx.get(gid) or {}
        r = by_id.get(gid)
        if r is None and g:
            r = idx.get((str(g.get('game_date'))[:10],
                         str(g.get('home_team')).lower(),
                         str(g.get('away_team')).lower()))
        if not r:
            continue
        hs, as_ = f(r.get('home_score')), f(r.get('away_score'))
        if hs is None or as_ is None:
            continue
        divs = []
        for m, b in (('cz_money_pct', 'cz_bets_pct'),
                     ('fr_handle_pct', 'fr_bettors_pct'),
                     ('ftp_money_pct', 'ftp_bets_pct'),
                     ('oc_money_pct', 'oc_bets_pct')):
            mv, bv = f(x.get(m)), f(x.get(b))
            if mv is not None and bv is not None:
                divs.append(mv - bv)
        if not divs:
            continue
        margin = hs - as_
        if market == 'ml':
            won = (margin > 0) if side == 'HOME' else (margin < 0)
            odds = r.get(ml_cols[0] if side == 'HOME' else ml_cols[1])
        else:
            cs = f(r.get('close_spread')) or f(g.get('close_spread'))
            if cs is None:
                continue
            cover = margin - (cs * sign)
            if abs(cover) < 1e-9:
                continue
            won = (cover > 0) if side == 'HOME' else (cover < 0)
            odds = -110
        hsp, asp = f(g.get('home_sp_plus')), f(g.get('away_sp_plus'))
        if hsp is None or asp is None:
            continue
        sp_gap = hsp - asp
        signed = sp_gap if side == 'HOME' else -sp_gap
        d = statistics.fmean(divs)
        if d < div_min or signed <= 0:
            continue
        p = profit(odds, won)
        if p is None:
            continue
        out.append({'date': str(r.get('game_date'))[:10], 'side': side,
                    'won': 1 if won else 0, 'div': d, 'gap': signed,
                    'odds': f(odds), 'pf': p,
                    'team': (r.get('home_team') if side == 'HOME'
                             else r.get('away_team'))})
    return out


def report(rows, label):
    n = len(rows)
    if n < 20:
        print(f'  {label}: n={n} too thin')
        return
    w = sum(r['won'] for r in rows)
    pf = [r['pf'] for r in rows]
    roi = statistics.fmean(pf) * 100
    lo, hi = boot_ci(pf)
    inside = (lo <= 0 <= hi)
    verdict = 'ZERO INSIDE CI — not an edge' if inside else 'CI excludes zero'
    print(f'  {label:<34} {w}-{n - w}  {w / n * 100:5.1f}%  '
          f'ROI {roi:+6.2f}%  95% CI [{lo * 100:+6.2f}%, {hi * 100:+6.2f}%]  '
          f'n={n:<4} {verdict}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF')
    ap.add_argument('--div', type=float, default=5.0)
    a = ap.parse_args()

    print('=== robustness of the one surviving sharp x SP+ cell ===')
    print(f'    {a.sport} · sharp divergence >= +{a.div:.0f} AND SP+ gap '
          f'agrees with the side\n')

    ml = build(a.sport, 'ml', a.div)
    rl = build(a.sport, 'rl', a.div)
    if not ml:
        print('  nothing built')
        return 0

    print('── TEST 1 · ROI with a bootstrap CI (mixed prices make the '
          'analytic SE useless)')
    report(ml, 'ML  (the claimed edge)')
    report(rl, 'ATS (the same cell, spread)')

    print('\n── TEST 2 · TEMPORAL SPLIT — a real pattern lives in both halves')
    ds = sorted(r['date'] for r in ml)
    mid = ds[len(ds) // 2]
    report([r for r in ml if r['date'] < mid], f'ML first half (< {mid})')
    report([r for r in ml if r['date'] >= mid], f'ML second half (>= {mid})')

    print('\n── TEST 3 · PRICE MIX — is the profit a long-shot tail?')
    od = sorted(r['odds'] for r in ml)
    favs = [r for r in ml if r['odds'] < 0]
    dogs = [r for r in ml if r['odds'] > 0]
    print(f'  price range {od[0]:+.0f} to {od[-1]:+.0f} · median '
          f'{statistics.median(od):+.0f} · {len(favs)} favourites, '
          f'{len(dogs)} underdogs')
    if favs:
        report(favs, 'favourites only')
    if dogs:
        report(dogs, 'underdogs only')

    print('\n── TEST 4 · DROP-ONE — an edge that needs one game is not one')
    srt = sorted(ml, key=lambda r: r['pf'])
    best = srt[-1]
    print(f'  best single bet: {best["team"]} {best["odds"]:+.0f} '
          f'on {best["date"]} (+{best["pf"]:.2f}u)')
    report(srt[:-1], 'ML minus the best bet')
    report(srt[1:], 'ML minus the worst bet')
    wins = sorted((r for r in ml if r['won']), key=lambda r: -r['pf'])[:3]
    top3 = sum(r['pf'] for r in wins)
    tot = sum(r['pf'] for r in ml)
    print(f'  top 3 winners contribute +{top3:.2f}u of {tot:+.2f}u total '
          f'({top3 / tot * 100 if tot else 0:.0f}%)')

    print('\n── TEST 5 · WHAT THE CLAIM WOULD HAVE TO SAY')
    print('  The ATS twin above is the limit of the claim. If ML is positive')
    print('  and ATS is not, the pattern is about WHO WINS, not by how much,')
    print('  and it can only ever be published as a moneyline read.')
    print('\n  A cell passes only if: CI excludes zero, BOTH halves are')
    print('  positive, and it survives dropping its best bet. Anything less')
    print('  is one of ~24 cells doing what randomness does.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
