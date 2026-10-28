#!/usr/bin/env python3
"""THE WEEKLY CADENCE: what drives picks vs what drives WINS, and the gap.

══ WHY (2026-10-27) ════════════════════════════════════════════════════════
Andy's standard, recorded as feedback_engine_selection_standard:

    "all of that should be recorded and assessed week to week to see what is
     driving picks the most and resulting in favorable predicted outcome"

Those are TWO questions, and the whole value is in the difference between
them:

    INFLUENCE  how much a signal drives the pick   = firings x weight
    OUTCOME    how often it was right              = hit rate vs breakeven

A signal with high influence and poor outcome is OVER-WEIGHTED and is
costing money every week. A signal with low influence and strong outcome is
UNDER-WEIGHTED and is being wasted. That gap is the reweighting
instruction, and it is the thing that was impossible to compute before
today -- `season_week` was NULL on 58% of rows, NHL/NBA/NCAAB had no
attribution at all, and only 19 signals were tracked across all sports
against the 215 that actually fire.

DISCIPLINE THIS REPORT KEEPS
  * breakeven is PER MARKET, measured from stored prices, never a flat
    -110: ml 58.33%, prop 52.83%, rl/total 52.38%. A moneyline signal at
    57% is LOSING, and a report that calls it a winner is worse than no
    report (see feedback_flat_110_inflates_every_ml_figure).
  * every percentage carries its n, and nothing is called over- or
    under-weighted below MIN_N firings.
  * `kind` is not used as the headline. It is ~97% 'ok' by construction --
    the ensemble picks the side its own signals favour -- so ranking by it
    would be meaningless. Outcomes come from the graded result.
  * weeks are the sport's own league week where one exists, else the ISO
    week of the date (baseball and basketball have no league weeks).

    python weekly_signal_attribution_report.py
    python weekly_signal_attribution_report.py --sport NCAAF --weeks 4
    python weekly_signal_attribution_report.py --sport NFL --all-weeks
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# measured from stored prices on graded receipts since 2026-09-19,
# median payout -> 1/(1+payout). See refit_signal_registry.BREAKEVEN_BY_MARKET
BREAKEVEN = {'ml': 58.33, 'moneyline': 58.33, 'prop': 52.83,
             'rl': 52.38, 'spread': 52.38, 'total': 52.38}
BREAKEVEN_DEFAULT = 52.38
MIN_N = 15          # below this a signal is listed but never judged


def _load_env(path):
    out = {}
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


_HERE = os.path.dirname(os.path.abspath(__file__))
_E = _load_env(os.path.join(_HERE, '.env'))
URL = _E.get('SUPABASE_URL') or os.environ.get('SUPABASE_URL')
KEY = (_E.get('SUPABASE_SERVICE_ROLE_KEY') or _E.get('SUPABASE_KEY')
       or os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
if not URL or not KEY:
    sys.exit('missing SUPABASE_URL / SUPABASE_KEY')
BASE = URL.rstrip('/') + '/rest/v1/'
HDR = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY}


def fetch_all(table, select='*', **filt):
    out, off = [], 0
    while True:
        p = {'select': select, 'limit': '1000', 'offset': str(off)}
        p.update(filt)
        u = BASE + table + '?' + urllib.parse.urlencode(p, safe='.,()*:')
        r = urllib.request.Request(u, headers=HDR)
        try:
            with urllib.request.urlopen(r, timeout=90) as resp:
                rows = json.loads(resp.read().decode('utf-8', 'replace'))
        except urllib.error.HTTPError as e:
            print('    HTTP %s on %s :: %s'
                  % (e.code, table, e.read().decode('utf-8', 'replace')[:200]))
            return out
        if not isinstance(rows, list):
            return out
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return out


def f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def wilson_lo(w, n):
    if not n:
        return 0.0
    p = w / n
    z = 1.96
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return 100.0 * (c - m) / d


def be_for(markets):
    """Breakeven for a signal, weighted by the markets it fires on."""
    tot = sum(markets.values()) or 1
    return sum(BREAKEVEN.get(m, BREAKEVEN_DEFAULT) * c
               for m, c in markets.items()) / tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport')
    ap.add_argument('--weeks', type=int, default=3,
                    help='how many most-recent weeks to detail')
    ap.add_argument('--all-weeks', action='store_true')
    ap.add_argument('--min-n', type=int, default=MIN_N)
    a = ap.parse_args()

    rows = fetch_all('signal_attribution')
    reg = fetch_all('signal_registry',
                    select='signal_name,sport,recommended_weight,tier,'
                           'hit_rate,sample_n')
    wmap, tmap = {}, {}
    for r in reg:
        k = (str(r.get('sport')), str(r.get('signal_name')))
        w = f(r.get('recommended_weight'))
        if w is not None and (k not in wmap or w > wmap[k]):
            wmap[k] = w
            tmap[k] = str(r.get('tier') or '')

    sports = ([a.sport.upper()] if a.sport
              else sorted({str(r.get('sport')) for r in rows}))

    print('=' * 108)
    print('WEEKLY SIGNAL ATTRIBUTION — what drives picks vs what drives WINS')
    print('  breakeven is PER MARKET: ml 58.33%, prop 52.83%, rl/total 52.38%')
    print('  a signal is only judged at n >= %d graded firings' % a.min_n)
    print('=' * 108)

    for sp in sports:
        srows = [r for r in rows if str(r.get('sport')) == sp]
        graded = [r for r in srows if str(r.get('result')) in ('W', 'L')]
        if not graded:
            print('')
            print('  %s — no graded attribution rows' % sp)
            continue
        weeks = sorted({int(r['season_week']) for r in graded
                        if r.get('season_week') is not None})
        print('')
        print('─' * 108)
        print('%s   graded firings %d   weeks %s'
              % (sp, len(graded), weeks))
        print('─' * 108)

        show = weeks if a.all_weeks else weeks[-a.weeks:]
        for wk in show:
            wr = [r for r in graded
                  if r.get('season_week') is not None
                  and int(r['season_week']) == wk]
            if not wr:
                continue
            agg = collections.defaultdict(
                lambda: {'w': 0, 'l': 0, 'mkt': collections.Counter()})
            for r in wr:
                k = str(r.get('signal_key'))
                if str(r.get('result')) == 'W':
                    agg[k]['w'] += 1
                else:
                    agg[k]['l'] += 1
                m = str(r.get('pick_market') or '').lower()
                if m:
                    agg[k]['mkt'][m] += 1
            tw = sum(v['w'] for v in agg.values())
            tl = sum(v['l'] for v in agg.values())
            print('')
            print('  WEEK %-3s  firings %-5d  signal-level %d-%d (%.1f%%)'
                  % (wk, tw + tl, tw, tl,
                     100.0 * tw / (tw + tl) if tw + tl else 0))
            rank = []
            for k, v in agg.items():
                n = v['w'] + v['l']
                if n < a.min_n:
                    continue
                hit = 100.0 * v['w'] / n
                bk = be_for(v['mkt'])
                wt = wmap.get((sp, k), 0.0)
                rank.append({'k': k, 'n': n, 'hit': hit, 'be': bk,
                             'edge': hit - bk, 'wt': wt,
                             'infl': n * max(wt, 0.0),
                             'tier': tmap.get((sp, k), '')})
            if not rank:
                print('     no signal reached n>=%d this week' % a.min_n)
                continue
            rank.sort(key=lambda z: -z['infl'])
            print('     %-40s %5s %7s %8s %7s %8s %-12s'
                  % ('signal', 'n', 'hit%', 'vs BE', 'weight', 'influence',
                     'tier'))
            for d in rank[:10]:
                print('     %-40s %5d %6.1f%% %+7.1f %7.3f %8.1f %-12s'
                      % (d['k'][:40], d['n'], d['hit'], d['edge'], d['wt'],
                         d['infl'], d['tier'][:12]))

        # ── the gap: the tuning instruction ──
        agg = collections.defaultdict(
            lambda: {'w': 0, 'l': 0, 'mkt': collections.Counter()})
        for r in graded:
            k = str(r.get('signal_key'))
            if str(r.get('result')) == 'W':
                agg[k]['w'] += 1
            else:
                agg[k]['l'] += 1
            m = str(r.get('pick_market') or '').lower()
            if m:
                agg[k]['mkt'][m] += 1
        cand = []
        for k, v in agg.items():
            n = v['w'] + v['l']
            if n < a.min_n:
                continue
            hit = 100.0 * v['w'] / n
            bk = be_for(v['mkt'])
            wt = wmap.get((sp, k), 0.0)
            cand.append({'k': k, 'n': n, 'hit': hit, 'be': bk,
                         'edge': hit - bk, 'wt': wt, 'infl': n * max(wt, 0.0),
                         'lo': wilson_lo(v['w'], n),
                         'tier': tmap.get((sp, k), '')})
        # ══ NEVER PRESENT "none" AS A CLEAN RESULT ══════════════════════
        # An empty list here has two completely different meanings: "no
        # signal is mis-weighted" and "no signal has enough firings to
        # judge". Printing the same word for both is the silent-no-op class
        # this whole audit keeps running into, so the pool size is always
        # stated.
        all_sig = len(agg)
        pool = len(cand)
        thin = all_sig - pool
        print('')
        print('  ══ THE GAP (all weeks pooled) — the reweighting instruction ══')
        print('     signals firing: %d   judgeable at n>=%d: %d   '
              'TOO THIN to judge: %d'
              % (all_sig, a.min_n, pool, thin))
        if pool:
            wts = [c['wt'] for c in cand]
            print('     judgeable weights: min %.3f  median %.3f  max %.3f'
                  % (min(wts), sorted(wts)[len(wts) // 2], max(wts)))
        if not cand:
            print('     NOT A CLEAN BILL OF HEALTH — no signal in %s has %d+'
                  ' graded firings yet. %d signals are firing unjudged.'
                  % (sp, a.min_n, all_sig))
            continue
        over = [c for c in cand if c['edge'] < -2.0 and c['wt'] >= 0.30]
        over.sort(key=lambda z: z['edge'])
        print('')
        print('  OVER-WEIGHTED — drives picks, loses against its own breakeven:')
        if not over:
            _wtok = sum(1 for c in cand if c['wt'] >= 0.30)
            print('     none of the %d judgeable signals qualifies '
                  '(needs edge < -2.0 AND weight >= 0.30; %d carry weight '
                  '>= 0.30)' % (pool, _wtok))
        for c in over[:8]:
            print('     %-40s n=%-4d hit %5.1f%% vs BE %5.1f%%  edge %+6.1f  '
                  'weight %.3f  %s'
                  % (c['k'][:40], c['n'], c['hit'], c['be'], c['edge'],
                     c['wt'], c['tier'][:12]))
        under = [c for c in cand
                 if c['lo'] > c['be'] and c['wt'] < 0.50]
        under.sort(key=lambda z: -(z['lo'] - z['be']))
        print('')
        print('  UNDER-WEIGHTED — beats breakeven with the CI clear, low weight:')
        if not under:
            _ciok = sum(1 for c in cand if c['lo'] > c['be'])
            print('     none of the %d judgeable signals qualifies '
                  '(needs CI lower bound above breakeven AND weight < 0.50; '
                  '%d clear their CI)' % (pool, _ciok))
        for c in under[:8]:
            print('     %-40s n=%-4d hit %5.1f%% (CI lo %5.1f%%) vs BE %5.1f%%'
                  '  weight %.3f  %s'
                  % (c['k'][:40], c['n'], c['hit'], c['lo'], c['be'],
                     c['wt'], c['tier'][:12]))
    print('')
    print('=' * 108)
    print('HOW TO ACT ON THIS')
    print('  OVER-WEIGHTED  -> cut the weight or re-tier. It is driving picks')
    print('                    and losing against the price it is taken at.')
    print('  UNDER-WEIGHTED -> raise the weight. Its CI lower bound already')
    print('                    clears its own breakeven and it barely counts.')
    print('  Neither list is an instruction to flip a side. A signal that')
    print('  loses is not evidence its opposite wins -- that needs its own')
    print('  measurement (see project_lr_v1_override_erases_attribution_1027).')
    print('=' * 108)
    return 0


if __name__ == '__main__':
    sys.exit(main())
