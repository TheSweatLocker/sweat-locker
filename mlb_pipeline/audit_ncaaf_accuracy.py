"""Where does NCAAF accuracy actually live? Baselines first, then us.

WHY (2026-10-08)
----------------
Andy: "Yes i want the most accurate engine we cna have for ncaaf".

NCAAF published reads run 126-122 (50.8%, n=248) against a 52.38% breakeven,
so the engine is currently a losing product for the sport. Before changing
anything, this establishes WHICH part is losing, because the documented
pathologies point in different directions and the fixes are incompatible:

    project_ncaaf_ml_path_is_the_leak_930      ML -23.1% vs spread +7.8%
    project_ncaaf_dog_bias_926                 dogs 24.3% vs favs 57.9%
    project_sp_plus_compression_927            margin 1.7x too small
    project_ncaaf_totals_model_worse_930       30.8% n=39, -16.09u
    project_ncaaf_conviction_is_scale_error    tier and conviction disagree
    B66 (today)                                weights r=+0.20 vs reality

THE BASELINE COMES FIRST, ALWAYS. A signal that "hits 57%" means nothing if
flat-home hit 57% on the same games. That mistake has already been made here
once — the model vote looked additive until it was measured against a flat
take-the-dog rule and turned out to BE that rule
(feedback_model_vote_may_be_a_dog_proxy). So every one of our numbers below is
printed next to what a no-model rule did on the identical game set.

WHAT IT MEASURES, on graded NCAAF games only
  1. flat baselines   home / away / favourite / dog ATS, over / under
  2. our picks        by market, by conviction band, by tier, by side
  3. ROI, not just hit rate, using the stored price where there is one
  4. conviction monotonicity — does a higher number actually win more often?
     If it does not, reweighting signals is the wrong repair and the scoring
     scale itself is the defect.

WRITES NOTHING.

CLI
    python audit_ncaaf_accuracy.py
    python audit_ncaaf_accuracy.py --since 2026-09-01
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
BREAKEVEN = 52.38


def _page(t, p, cap=60000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:140]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def _jl(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct(w, l):
    n = w + l
    return (w / n * 100) if n else 0.0


def _line(label, w, l, extra=''):
    n = w + l
    mark = ''
    if n >= 30:
        mark = '  <' if _pct(w, l) < BREAKEVEN else '  >'
    print(f'    {label:<30}{w:>4}-{l:<4}n={n:<5}{_pct(w, l):5.1f}%{mark}{extra}')


def _units(result, odds):
    if result == 'PUSH':
        return 0.0
    o = odds if odds is not None else -110
    if result == 'LOSS':
        return -1.0
    return (o / 100.0) if o > 0 else (100.0 / abs(o))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-08-20')
    a = ap.parse_args()

    res = {str(x['game_id']): x for x in _page(
        'ncaaf_game_results',
        {'select': 'game_id,game_date,home_score,away_score,spread_result,'
                   'total_result,home_win,close_spread,close_total',
         'game_date': f'gte.{a.since}'})}
    ctx = _page('ncaaf_game_context',
                {'select': 'game_id,game_date,away_team,home_team,'
                           'close_spread,primary_play',
                 'game_date': f'gte.{a.since}'})
    reads = {str(x['game_id']): x for x in _page(
        'jerry_reads', {'select': 'game_id,result,conviction,call_side,'
                                  'call_market,call_text,price_american',
                        'sport': 'eq.NCAAF'})}
    print(f'=== NCAAF accuracy · since {a.since} · {len(res)} graded results, '
          f'{len(ctx)} ctx rows')

    # ── 1. FLAT BASELINES on games with a graded spread result ───────────
    print('\n  1. FLAT BASELINES — no model, same games')
    fav_w = fav_l = dog_w = dog_l = 0
    hw = hl = 0
    ow = ol = 0
    for g in ctx:
        r = res.get(str(g['game_id']))
        if not r:
            continue
        sr = str(r.get('spread_result') or '').lower()
        cs = _f(g.get('close_spread'))
        if sr in ('home_covered', 'away_covered'):
            if sr == 'home_covered':
                hw += 1
            else:
                hl += 1
            if cs is not None and cs != 0:
                # NCAAF ctx convention: negative close_spread = home favourite
                home_is_fav = cs < 0
                home_cov = sr == 'home_covered'
                if home_is_fav:
                    fav_w += home_cov
                    fav_l += not home_cov
                    dog_w += not home_cov
                    dog_l += home_cov
                else:
                    dog_w += home_cov
                    dog_l += not home_cov
                    fav_w += not home_cov
                    fav_l += home_cov
        tr = str(r.get('total_result') or '').lower()
        if tr == 'over':
            ow += 1
        elif tr == 'under':
            ol += 1
    _line('always HOME ATS', hw, hl)
    _line('always AWAY ATS', hl, hw)
    _line('always FAVOURITE ATS', fav_w, fav_l)
    _line('always DOG ATS', dog_w, dog_l)
    _line('always OVER', ow, ol)
    _line('always UNDER', ol, ow)

    # ── 2. OUR PICKS ─────────────────────────────────────────────────────
    graded = [(g, reads[str(g['game_id'])]) for g in ctx
              if str(g['game_id']) in reads
              and str(reads[str(g['game_id'])].get('result') or '').upper()
              in ('WIN', 'W', 'LOSS', 'L', 'PUSH')]
    print(f'\n  2. OUR PICKS — {len(graded)} graded reads')

    def tally(keyfn, title):
        d = collections.defaultdict(lambda: [0, 0, 0, 0.0])
        for g, rd in graded:
            k = keyfn(g, rd)
            if k is None:
                continue
            rr = str(rd.get('result') or '').upper()
            rr = {'W': 'WIN', 'L': 'LOSS'}.get(rr, rr)
            if rr == 'WIN':
                d[k][0] += 1
            elif rr == 'LOSS':
                d[k][1] += 1
            else:
                d[k][2] += 1
            d[k][3] += _units(rr, _f(rd.get('price_american')))
        print(f'\n    by {title}:')
        for k in sorted(d, key=lambda z: str(z)):
            w, l, p, u = d[k]
            _line(str(k), w, l, f'  {u:+7.2f}u')

    tally(lambda g, rd: str(rd.get('call_market') or '?'), 'MARKET')
    tally(lambda g, rd: str(rd.get('call_side') or '?'), 'SIDE')

    def cband(g, rd):
        c = _f(rd.get('conviction'))
        if c is None:
            return None
        return ('0-49' if c < 50 else '50-57' if c < 58 else
                '58-64' if c < 65 else '65-74' if c < 75 else
                '75-84' if c < 85 else '85+')
    tally(cband, 'CONVICTION BAND')

    def tier(g, rd):
        pp = _jl(g.get('primary_play'))
        return str(pp.get('tier') or '?')
    tally(tier, 'STORED TIER')

    # ── 3. CONVICTION MONOTONICITY ───────────────────────────────────────
    print('\n  3. IS CONVICTION INFORMATIVE?')
    pts = []
    for g, rd in graded:
        c = _f(rd.get('conviction'))
        rr = str(rd.get('result') or '').upper()
        rr = {'W': 'WIN', 'L': 'LOSS'}.get(rr, rr)
        if c is None or rr not in ('WIN', 'LOSS'):
            continue
        pts.append((c, 1 if rr == 'WIN' else 0))
    if len(pts) >= 20:
        try:
            r_ = statistics.correlation([p[0] for p in pts],
                                        [p[1] for p in pts])
        except Exception:                                  # noqa: BLE001
            r_ = float('nan')
        print(f'    correlation(conviction, win) = {r_:+.3f}  over n={len(pts)}')
        wins = [c for c, y in pts if y]
        loss = [c for c, y in pts if not y]
        print(f'    mean conviction on WINS   {statistics.fmean(wins):5.1f}'
              f'  (n={len(wins)})')
        print(f'    mean conviction on LOSSES {statistics.fmean(loss):5.1f}'
              f'  (n={len(loss)})')
        gap = statistics.fmean(wins) - statistics.fmean(loss)
        print(f'    separation {gap:+.2f} points')
        if abs(r_) < 0.10:
            print('    => conviction carries essentially NO information about')
            print('       the outcome. Reweighting the inputs cannot fix a')
            print('       scale that does not rank. That is the defect.')
    else:
        print(f'    only n={len(pts)} — too few to judge')
    return 0


if __name__ == '__main__':
    sys.exit(main())
