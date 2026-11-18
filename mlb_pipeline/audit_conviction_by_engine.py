#!/usr/bin/env python3
"""audit_conviction_by_engine - is conviction a bad RANKER, or a MIXTURE of
four rankers on four different scales?

WHY THIS EXISTS (2026-11-17)
============================
Measured on n=1234 graded picks, conviction is non-monotonic: the 0-44 band
beat the market implied by +3.1pp and the 70-100 band LOST by 3.8pp. The
standing read of that was "the engine cannot rank". This script tests a
different explanation first, because it is cheaper to fix and has to be
ruled out before any engine rework:

    `conviction` is ONE column written by FOUR different formulas, and
    three of the four are not the same measure.

      ensemble_scorer.py:3208    50 + win_score*10 + margin*20   (agreement)
      lr_v1 override             p_home_win * 100                (PROBABILITY)
      conviction_scale           rank_from_signals/2026-10-30    (PERCENTILE)
      ncaab_conviction           _cal_p_model                    (CALIBRATED p)

Counts since 2026-10-01: ensemble_v2 1247, lr_v1 458, rank_from_signals 562
(layered), ncaab_conviction 153, and 223 NCAAB rows with no `_engine` at all.
The mixture ratio differs by SPORT -- NFL is 42% lr_v1, NCAAB is 0% -- so
"conviction 70" is a different claim in different sports, and every gate
(POTD threshold, Sweat Card tier floor, The Sharp) reads it with ONE number.

A mixture of four distributions is non-monotonic even when each component
ranks perfectly. So the decisive question is:

    WITHIN a single engine, does conviction rank?

  If YES -> the defect is UNITS. Normalise to one measure and the gates work.
  If NO  -> the defect is the ENGINE, and normalising changes nothing.

Nobody had split the curve this way, so both fixes were guesses.

METHOD
======
Outcomes come from each sport's `*_game_results` GRADED columns
(`home_win`, `spread_result`, `total_result`) rather than from scores and a
derived sign, because `close_spread` does not use one sign convention across
sports ([[feedback_one_column_two_side_conventions]],
[[feedback_football_and_the_rest_name_columns_differently]]). `push` is
excluded, not counted as a loss.

Prices: moneyline is the only market with a stored two-way price in all five
sports, so it carries the priced comparison. Spread/total prices exist only
on NBA and NHL. An unpriced pick is counted in the HIT RATE and excluded from
the implied-probability comparison -- an unpriced rate is not a record
([[feedback_an_unpriced_moneyline_rate_is_not_a_record]]).

Every percentage prints its n, and no bucket under MIN_N is read as a result
([[feedback_sample_size_with_pct]]).
"""
from __future__ import annotations
import argparse, os, sys, json
from collections import defaultdict
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

import requests

SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ.get('SUPABASE_KEY')
H = {'apikey': KEY or '', 'Authorization': 'Bearer ' + (KEY or '')}

MIN_N = 30

SPORTS = {
    'NFL':   ('nfl_game_context',   'nfl_game_results'),
    'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results'),
    'NBA':   ('nba_game_context',   'nba_game_results'),
    'NCAAB': ('ncaab_game_context', 'ncaab_game_results'),
    'NHL':   ('nhl_game_context',   'nhl_game_results'),
}

# Spread / total price columns, per sport. Absent => the pick is unpriced and
# contributes a hit rate only.
SPREAD_PRICE = {
    'NBA': ('close_spread_home_price', 'close_spread_away_price'),
    'NHL': ('close_puckline_home_price', 'close_puckline_away_price'),
}
TOTAL_PRICE = {
    'NBA': ('close_total_over_price', 'close_total_under_price'),
    'NHL': ('close_total_over_price', 'close_total_under_price'),
}


def paged(table, params, page=1000):
    out, off = [], 0
    while True:
        qs = '&'.join(f'{k}={v}' for k, v in params.items())
        url = f'{SB}/rest/v1/{table}?{qs}&limit={page}&offset={off}'
        r = requests.get(url, headers=H, timeout=120)
        r.raise_for_status()
        b = r.json()
        out += b
        if len(b) < page:
            return out
        off += page


def fnum(v):
    try:
        if v is None or v == '':
            return None
        return float(v)
    except Exception:
        return None


def implied(odds):
    """American odds -> implied probability."""
    o = fnum(odds)
    if o is None or o == 0:
        return None
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def wilson(w, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    p = w / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return ((c - m) / d, (c + m) / d)


def collect(sport, since):
    ctxt, rest = SPORTS[sport]
    ctx = paged(ctxt, {'select': '*', 'game_date': f'gte.{since}',
                       'primary_play': 'not.is.null'})
    res = paged(rest, {'select': '*', 'game_date': f'gte.{since}'})
    by_id = {str(r.get('game_id')): r for r in res}
    by_key = {(str(r.get('game_date'))[:10], str(r.get('home_team')),
               str(r.get('away_team'))): r for r in res}

    picks, drops = [], defaultdict(int)
    for c in ctx:
        if c.get('stats_source') == 'preseason':
            drops['preseason'] += 1
            continue
        pp = c.get('primary_play')
        if isinstance(pp, str):
            try: pp = json.loads(pp)
            except Exception: pp = None
        if not isinstance(pp, dict):
            drops['no primary_play'] += 1
            continue

        mtype = str(pp.get('type') or '').lower()
        side = str(pp.get('side') or '').upper()
        if mtype in ('', 'pass', 'none') or not side:
            drops['no call (pass/withdrawn)'] += 1
            continue

        conv = pp.get('conviction')
        if conv is None:
            drops['NULL conviction'] += 1
            continue

        g = (by_id.get(str(c.get('game_id')))
             or by_key.get((str(c.get('game_date'))[:10],
                            str(c.get('home_team')), str(c.get('away_team')))))
        if not g:
            drops['no results row'] += 1
            continue

        # ---- outcome, from the DB's own graded columns ----
        win = None
        if mtype == 'ml':
            hw = g.get('home_win')
            if hw is None:
                drops['ungraded ml'] += 1
                continue
            win = bool(hw) == (side == 'HOME')
        elif mtype in ('spread', 'rl', 'puckline'):
            sr = g.get('spread_result')
            if sr in (None, '', 'push'):
                drops['ungraded/push spread'] += 1
                continue
            win = sr == ('home_covered' if side == 'HOME' else 'away_covered')
        elif mtype == 'total':
            tr = g.get('total_result')
            if tr in (None, '', 'push'):
                drops['ungraded/push total'] += 1
                continue
            if side not in ('OVER', 'UNDER'):
                drops['total with no over/under side'] += 1
                continue
            win = tr == side.lower()
        else:
            drops[f'unknown market {mtype}'] += 1
            continue

        # ---- price for the side WE published ----
        odds = None
        if mtype == 'ml':
            if side == 'HOME':
                odds = (c.get('close_home_ml') or c.get('home_ml_close')
                        or c.get('home_ml_odds'))
            else:
                odds = (c.get('close_away_ml') or c.get('away_ml_close')
                        or c.get('away_ml_odds'))
        elif mtype in ('spread', 'rl', 'puckline'):
            cols = SPREAD_PRICE.get(sport)
            if cols:
                odds = c.get(cols[0] if side == 'HOME' else cols[1])
        elif mtype == 'total':
            cols = TOTAL_PRICE.get(sport)
            if cols:
                odds = c.get(cols[0] if side == 'OVER' else cols[1])

        # ---- the model's own probability for the side WE published ----
        # Only lr_v1 and the NCAAB calibrated model emit one. Oriented to the
        # published side, never left home-relative -- orientation is not a
        # fact ([[feedback_orientation_is_not_a_fact_at_a_neutral_site]]).
        p_side = None
        _plr = fnum(pp.get('_lr_p_home_win'))
        if _plr is None:
            _sh = pp.get('_lr_ml_shadow')
            if isinstance(_sh, dict):
                _plr = fnum(_sh.get('p_home_win'))
        if _plr is not None and mtype == 'ml':
            p_side = _plr if side == 'HOME' else 1.0 - _plr
        if p_side is None:
            _pc = fnum(pp.get('_cal_p_win'))
            if _pc is not None:
                p_side = _pc

        ci = pp.get('_conv_inputs') or {}
        picks.append({
            'p_side': p_side,
            'margin': (fnum(ci.get('margin')) if isinstance(ci, dict)
                       else None),
            'sport': sport,
            'date': str(c.get('game_date'))[:10],
            'market': 'spread' if mtype in ('rl', 'puckline') else mtype,
            'engine': str(pp.get('_engine') or 'none'),
            'formula': (ci.get('formula') if isinstance(ci, dict) else None)
                       or pp.get('_cal_model') or '-',
            'conv': float(conv),
            'tier': str(pp.get('tier') or '-'),
            'win': bool(win),
            'imp': implied(odds),
            'win_score': (fnum(ci.get('win_score'))
                          if isinstance(ci, dict) else None),
        })
    return picks, drops


def block(label, picks, indent='    '):
    """One row: n, hit rate, and -- on the priced subset -- the edge."""
    n = len(picks)
    if not n:
        print(f'{indent}{label:<34} n=0')
        return
    w = sum(1 for p in picks if p['win'])
    hr = 100.0 * w / n
    lo, hi = wilson(w, n)
    pr = [p for p in picks if p['imp'] is not None]
    tag = '' if n >= MIN_N else '  <- under n=%d, not a result' % MIN_N
    if pr:
        ih = 100.0 * sum(1 for p in pr if p['win']) / len(pr)
        im = 100.0 * sum(p['imp'] for p in pr) / len(pr)
        print(f'{indent}{label:<34} n={n:<5} hit {hr:5.1f}%  '
              f'[{100*lo:4.1f},{100*hi:5.1f}]   priced n={len(pr):<5} '
              f'hit {ih:5.1f}% vs imp {im:5.1f}%  edge {ih-im:+5.1f}pp{tag}')
    else:
        print(f'{indent}{label:<34} n={n:<5} hit {hr:5.1f}%  '
              f'[{100*lo:4.1f},{100*hi:5.1f}]   UNPRICED - no edge figure{tag}')


BANDS = [(0, 45), (45, 55), (55, 65), (65, 75), (75, 101)]


def bands(picks, indent='      '):
    for lo, hi in BANDS:
        sel = [p for p in picks if lo <= p['conv'] < hi]
        if sel:
            block(f'conviction {lo}-{hi-1}', sel, indent)


def spearman(picks):
    """Rank correlation between conviction and winning. The ONE number that
    answers 'does this rank', and it needs no bucketing choice."""
    pts = [(p['conv'], 1.0 if p['win'] else 0.0) for p in picks]
    n = len(pts)
    if n < MIN_N:
        return None

    def ranks(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx = ranks([p[0] for p in pts])
    ry = ranks([p[1] for p in pts])
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    rho = num / (dx * dy)
    se = 1.0 / ((n - 1) ** 0.5)
    return rho, rho / se, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-09-01')
    ap.add_argument('--sport', choices=sorted(SPORTS))
    ap.add_argument('--market', choices=['ml', 'spread', 'total'])
    a = ap.parse_args()

    if not SB or not KEY:
        print('!! SUPABASE_URL / key not set - cannot measure', file=sys.stderr)
        return 2

    allp, alldrops = [], defaultdict(int)
    for s in ([a.sport] if a.sport else sorted(SPORTS)):
        p, d = collect(s, a.since)
        allp += p
        for k, v in d.items():
            alldrops[k] += v
    if a.market:
        allp = [p for p in allp if p['market'] == a.market]

    print('=' * 96)
    print(f'CONVICTION BY ENGINE   since {a.since}   '
          f'{len(allp)} graded picks carrying a conviction')
    print('=' * 96)
    print('  excluded: ' + ', '.join(
        f'{k} {v}' for k, v in sorted(alldrops.items(), key=lambda x: -x[1])))

    print('\n' + '-' * 96)
    print('1. THE WHOLE POOL - the non-monotonicity as currently seen')
    print('-' * 96)
    block('ALL published picks', allp)
    bands(allp, '    ')
    sp = spearman(allp)
    if sp:
        print(f'    spearman(conviction, win) = {sp[0]:+.4f}  '
              f'z={sp[1]:+.2f}  n={sp[2]}')

    print('\n' + '-' * 96)
    print('2. SPLIT BY ENGINE - does conviction rank WITHIN one formula?')
    print('-' * 96)
    byeng = defaultdict(list)
    for p in allp:
        byeng[p['engine']].append(p)
    for eng in sorted(byeng, key=lambda e: -len(byeng[e])):
        sel = byeng[eng]
        print(f'\n  == _engine={eng}   n={len(sel)} ==')
        fs = defaultdict(int)
        for p in sel:
            fs[p['formula']] += 1
        print('     formulas present: ' + ', '.join(
            f'{k} ({v})' for k, v in sorted(fs.items(), key=lambda x: -x[1])))
        block('all', sel, '     ')
        bands(sel, '       ')
        sp = spearman(sel)
        if sp:
            verdict = ('RANKS' if sp[1] > 1.96 else
                       'INVERSE' if sp[1] < -1.96 else 'NO RANK (noise)')
            print(f'       spearman = {sp[0]:+.4f}  z={sp[1]:+.2f}  '
                  f'n={sp[2]}   -> {verdict}')

    print('\n' + '-' * 96)
    print('3. SAME CONVICTION, DIFFERENT SPORT - is 70 one claim or five?')
    print('-' * 96)
    for s in sorted(set(p['sport'] for p in allp)):
        sel = [p for p in allp if p['sport'] == s]
        mix = defaultdict(int)
        for p in sel:
            mix[p['engine']] += 1
        share = ', '.join(f'{k} {100*v/len(sel):.0f}%'
                          for k, v in sorted(mix.items(), key=lambda x: -x[1]))
        print(f'\n  == {s}  n={len(sel)}   engine mix: {share} ==')
        block('all', sel, '     ')
        bands(sel, '       ')

    print('\n' + '-' * 96)
    print('4. win_score - the term ensemble_scorer says was never studied')
    print('-' * 96)
    ws = [p for p in allp if p['win_score'] is not None]
    print(f'  n={len(ws)} picks carry _conv_inputs.win_score')
    for lo, hi in [(-99, 0.3), (0.3, 0.6), (0.6, 1.0), (1.0, 1.5), (1.5, 99)]:
        sel = [p for p in ws if lo <= p['win_score'] < hi]
        if sel:
            block(f'win_score {lo if lo > -99 else 0}-'
                  f'{hi if hi < 99 else "max"}', sel, '    ')

    print('\n' + '-' * 96)
    print('5. margin - the only OTHER term in the ensemble formula')
    print('-' * 96)
    mg = [p for p in allp if p['margin'] is not None]
    print(f'  n={len(mg)} picks carry _conv_inputs.margin')
    for lo, hi in [(-99, 0.1), (0.1, 0.3), (0.3, 0.6), (0.6, 99)]:
        sel = [p for p in mg if lo <= p['margin'] < hi]
        if sel:
            block(f'margin {lo if lo > -99 else 0}-'
                  f'{hi if hi < 99 else "max"}', sel, '    ')

    print('\n' + '-' * 96)
    print('6. THE CONSTRUCTIVE TEST - does EDGE vs the price rank?')
    print('-' * 96)
    print('  edge = our model probability for the published side MINUS the')
    print('  implied probability at the price taken. This is what conviction')
    print('  is READ as. Only lr_v1 and the NCAAB calibrated model emit a')
    print('  probability at all, so this is the whole measurable universe.')
    ed = [p for p in allp if p['p_side'] is not None and p['imp'] is not None]
    print(f'\n  n={len(ed)} picks carry BOTH a model probability and a price')
    if ed:
        for p in ed:
            p['edge'] = p['p_side'] - p['imp']
        for lo, hi in [(-9, -0.05), (-0.05, 0.0), (0.0, 0.05),
                       (0.05, 0.10), (0.10, 9)]:
            sel = [p for p in ed if lo <= p['edge'] < hi]
            if sel:
                block(f'model edge {100*lo if lo > -9 else -999:+.0f}..'
                      f'{100*hi if hi < 9 else 999:+.0f}pp', sel, '    ')
        pts = [(p['edge'], p['win']) for p in ed]
        n = len(pts)
        if n >= MIN_N:
            fake = [{'conv': e, 'win': w} for e, w in pts]
            sp = spearman(fake)
            if sp:
                verdict = ('RANKS' if sp[1] > 1.96 else
                           'INVERSE' if sp[1] < -1.96 else 'NO RANK (noise)')
                print(f'    spearman(model edge, win) = {sp[0]:+.4f}  '
                      f'z={sp[1]:+.2f}  n={sp[2]}   -> {verdict}')
        pos = [p for p in ed if p['edge'] > 0]
        neg = [p for p in ed if p['edge'] <= 0]
        print()
        block('model says WE have the edge', pos, '    ')
        block('model says the PRICE is right', neg, '    ')

        print('\n' + '-' * 96)
        print('7. PRICE CONTROL on section 6 - edge, or a dog proxy?')
        print('-' * 96)
        print('  A model that claims edge mostly on DOGS will look inverse')
        print('  purely because dogs lose more often, and this repo has')
        print('  already been fooled that way twice -- the favourite bias')
        print('  WAS the market chooser, and model vote is a dog proxy.')
        print('  So: hold the PRICE roughly fixed, then ask whether claimed')
        print('  edge still inverts INSIDE the band. A signal needs a')
        print('  direction-only control before it is a finding.')
        print()
        print('  mean implied prob where we claim edge vs where we do not:')
        if pos and neg:
            print(f'    claim edge   n={len(pos):<4} mean implied '
                  f'{100*sum(p["imp"] for p in pos)/len(pos):5.1f}%')
            print(f'    concede      n={len(neg):<4} mean implied '
                  f'{100*sum(p["imp"] for p in neg)/len(neg):5.1f}%')
        PB = [(0.0, 0.45), (0.45, 0.55), (0.55, 0.65), (0.65, 0.80),
              (0.80, 1.01)]
        for plo, phi in PB:
            band = [p for p in ed if plo <= p['imp'] < phi]
            if len(band) < MIN_N:
                continue
            print(f'\n  == implied {100*plo:.0f}-{100*phi:.0f}%   '
                  f'n={len(band)} ==')
            bp = [p for p in band if p['edge'] > 0]
            bn = [p for p in band if p['edge'] <= 0]
            block('claim edge', bp, '      ')
            block('concede price', bn, '      ')
            if len(bp) >= MIN_N and len(bn) >= MIN_N:
                d = ((100.0 * sum(1 for p in bp if p['win']) / len(bp))
                     - (100.0 * sum(1 for p in bn if p['win']) / len(bn)))
                print(f'      within-band gap (claim - concede) '
                      f'{d:+.1f}pp')

        # Pooled over the two pick-dense bands. This is the headline the
        # control leaves standing: the dog proxy is real (claim-edge picks
        # average 10pp longer prices), and the inversion survives it anyway
        # in the heart of the board.
        core = [p for p in ed if 0.45 <= p['imp'] < 0.65]
        cp = [p for p in core if p['edge'] > 0]
        cn = [p for p in core if p['edge'] <= 0]
        if len(cp) >= MIN_N and len(cn) >= MIN_N:
            print('\n  == POOLED 45-65% implied - the price-controlled '
                  'headline ==')
            block('claim edge', cp, '      ')
            block('concede price', cn, '      ')
            ip = 100.0 * sum(p['imp'] for p in cp) / len(cp)
            iN = 100.0 * sum(p['imp'] for p in cn) / len(cn)
            print(f'      mean implied  claim {ip:.1f}%  vs  '
                  f'concede {iN:.1f}%   (price now held close)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
