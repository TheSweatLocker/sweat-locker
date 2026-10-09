"""The wind/total finding, put through everything that could kill it.

WHAT CAME OUT OF nfl_situational_lenses.py (2026-10-09, 2015+, n~2,900)
    wind 0-5    OVER 53.3%   n=610
    wind 6-10   OVER 49.5%   n=778
    wind 11-15  OVER 40.5%   n=380      <- i.e. the UNDER at 59.5%
    wind 16+    OVER 45.2%   n=157
    gap 12.75pp vs a 3SE bar of 9.80  ->  the only lens of ten to clear 3SE

Ten tests were run, so at a 3SE bar the expected number of false positives is
about 0.03. Multiple comparisons do not explain this one.

ALREADY RULED OUT
  * Dome contamination. Indoor games carry wind NULL on 1,814 of 1,815 rows,
    so they never enter a wind bucket. The calm bucket is genuinely outdoor
    games in light air, not roofed games scored as zero.

WHAT THIS SCRIPT TESTS, each of which could still sink it
  1. FULL HISTORY. The first pass used 2015+ out of habit; the table goes back
     to 1999-09-12. Roughly doubling n either tightens the effect or exposes
     it as an eleven-season fluke.
  2. ERA SPLIT. The single most informative robustness check available, and
     the closest thing to walk-forward for a measurement: if the effect is
     real physics it appears in BOTH halves. If it lives in one era only, it
     is either a rules artefact or noise that happened to clump.
  3. IS IT ALREADY PRICED? The decisive question. Bookmakers see the same
     forecast. If closing totals are already lower in wind, the market is
     adjusting -- and the only thing worth betting is the RESIDUAL, i.e.
     whether it adjusts ENOUGH. So this reports mean close_total per bucket
     alongside the hit rate. A market that moves the number and still goes
     under 59% of the time is under-adjusting; one that does not move the
     number at all would be leaving something far too obvious on the table,
     which is itself a reason to doubt the data.
  4. THE HONEST CAVEAT, measured not hand-waved. nflverse `wind` is the
     conditions AT the game, not the forecast two days out that the market
     actually priced. So part of any edge here is "games that turned out
     windier than expected go under" -- true, and NOT bettable, because we
     would need to have known. This separates what is capturable (forecastable
     wind, which a pre-game weather call can see) from what is not (the
     surprise). It cannot be fully resolved without storing our own forecasts,
     so the script says so plainly rather than implying the whole gap is ours.

WRITES NOTHING.

CLI
    python nfl_wind_total_lens.py
"""
from __future__ import annotations

import argparse
import collections
import math
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
#: -110 both ways. A totals play needs this to break even.
BREAKEVEN = 52.38


def _page(t, p, cap=400000):
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


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def bucket(w):
    return ('0-5' if w <= 5 else '6-10' if w <= 10
            else '11-15' if w <= 15 else '16+')


ORDER = ('0-5', '6-10', '11-15', '16+')
#: 2026-10-09 — the ACTIONABLE filter is 11+ as ONE bucket, not 11-15 sliced
#: off from 16+. Splitting them produced a non-monotonic late-era result
#: (11-15 under 59.3%, 16+ under 52.8%) which has no physical reading: more
#: wind cannot suppress scoring less. Pooled across all seasons the two agree
#: closely (55.64% and 55.56%), so the split was slicing noise. Choosing the
#: better-looking of two adjacent slices is how a measurement becomes a
#: specification search.
WINDY_MIN = 11.0


def season_of(d):
    """NFL seasons span the new year — Jan/Feb games belong to the PRIOR one.

    Bucketing those games by calendar year would scatter each season's
    playoffs into the next season's row and make a per-season consistency
    check meaningless.
    """
    y, m = int(d[:4]), int(d[5:7])
    return y - 1 if m <= 2 else y


def report(label, rows, min_n=60):
    """Hit rate, closing total and ROI per wind bucket."""
    by = collections.defaultdict(list)
    for r in rows:
        by[r['wb']].append(r)
    print(f'\n  {label}  (n={len(rows)})')
    print(f'      {"wind":<8}{"OVER%":>8}{"UNDER%":>8}{"n":>7}'
          f'{"2SE":>7}   {"mean close_total":>17}')
    out = {}
    for b in ORDER:
        s = by.get(b) or []
        if len(s) < min_n:
            if s:
                print(f'      {b:<8}{"":>8}{"":>8}{len(s):>7}   '
                      f'below n={min_n} floor, not reported')
            continue
        ov = statistics.fmean([x['over'] for x in s]) * 100
        se = (0.5 / len(s) ** 0.5) * 100
        tot = [x['ct'] for x in s if x['ct'] is not None]
        mt = f'{statistics.fmean(tot):.2f} (n={len(tot)})' if tot else 'n/a'
        print(f'      {b:<8}{ov:7.2f}%{100 - ov:7.2f}%{len(s):>7}'
              f'{2 * se:6.2f}   {mt:>17}')
        out[b] = (ov, len(s), se)
    # The row that matters: 11+ as one bucket, with the UNDER's ROI at -110.
    windy = [x for x in rows if x['wind'] >= WINDY_MIN]
    if len(windy) >= min_n:
        und = (1 - statistics.fmean([x['over'] for x in windy])) * 100
        se = (0.5 / len(windy) ** 0.5) * 100
        roi = (und / 100 * (100 / 110) - (1 - und / 100)) * 100
        edge = und - BREAKEVEN
        print(f'      {"11+ (one bucket)":<8}  UNDER {und:.2f}%  '
              f'n={len(windy)}  2SE +/-{2 * se:.2f}pp  ROI {roi:+.2f}%')
        print(f'        clears breakeven by {edge:+.2f}pp = '
              f'{edge / se:.1f} standard errors')
        out['11+'] = (100 - und, len(windy), se)
    if '0-5' in out and '11+' in out:
        a, b = out['0-5'], out['11+']
        gap = a[0] - b[0]
        se = (a[2] ** 2 + b[2] ** 2) ** 0.5
        tag = ('HOLDS (>3SE)' if gap > 3 * se else
               'weak (2-3SE)' if gap > 2 * se else 'GONE (<2SE)')
        print(f'      calm vs 11+: {gap:+.2f}pp · 2SE {2 * se:.2f} · '
              f'3SE {3 * se:.2f} -> {tag}')
    return out


def season_consistency(rows):
    """Per-season hit rate, and whether the scatter is more than sampling.

    THIS IS THE TEST THAT DECIDED THE FINDING. A pooled 55.6% on n=1,444 can
    arise two ways: a stable effect present every year, or a few huge seasons
    dragging up a mass of break-even ones. Those are the same pooled number
    and completely different products — the second is unbettable because you
    cannot know which kind of season you are in.

    The discriminator is dispersion. If the true rate is a constant p, each
    season's rate should scatter around it with SD = sqrt(p(1-p)/n_season).
    Observed SD materially ABOVE that means the rate itself moves year to
    year; observed SD AT that level means one stable rate and the yearly
    variation is nothing but small samples.
    """
    by = collections.defaultdict(list)
    for r in rows:
        if r['wind'] >= WINDY_MIN:
            by[season_of(r['date'])].append(r['over'])
    seasons = {s: v for s, v in by.items() if len(v) >= 15}
    if len(seasons) < 8:
        print('    too few seasons to judge consistency')
        return
    rates = [(1 - statistics.fmean(v)) * 100 for v in seasons.values()]
    n_tot = sum(len(v) for v in seasons.values())
    pooled = (1 - statistics.fmean(
        [x for v in seasons.values() for x in v])) * 100
    print(f'\n      UNDER at {WINDY_MIN:.0f}+ mph, by season '
          f'(breakeven {BREAKEVEN}%)')
    for s in sorted(seasons):
        p = (1 - statistics.fmean(seasons[s])) * 100
        print(f'        {s}  {p:5.1f}%  n={len(seasons[s]):<4}'
              f'{"#" * int(max(0, p - 30) / 2)}')
    print(f'\n      pooled          {pooled:.2f}%  n={n_tot}')
    print(f'      mean of seasons {statistics.fmean(rates):.2f}%  '
          f'(equal weight — guards against one huge season)')
    print(f'      MEDIAN season   {statistics.median(rates):.2f}%  '
          f'<- what a typical year actually delivers')
    print(f'      worst / best    {min(rates):.1f}% / {max(rates):.1f}%')
    obs = statistics.stdev(rates)
    exp = statistics.fmean([math.sqrt(pooled * (100 - pooled) / len(v))
                            for v in seasons.values()])
    print(f'\n      DISPERSION: observed SD {obs:.2f}pp vs {exp:.2f}pp '
          f'expected at a constant {pooled:.1f}%')
    if obs > exp * 1.15:
        print('      -> MORE dispersed than a constant rate. The edge comes')
        print('         and goes, so the pooled number OVERSTATES a typical')
        print('         season. Do not size off it.')
    else:
        print('      -> consistent with ONE stable underlying rate: the')
        print('         year-to-year scatter is small samples, not a signal')
        print('         that switches on and off. The pooled number is the')
        print('         honest estimate.')
    clear = sum(1 for r in rates if r > BREAKEVEN)
    expect = sum(0.5 * (1 + math.erf(
        (pooled - BREAKEVEN) /
        (math.sqrt(pooled * (100 - pooled) / len(v)) * math.sqrt(2))))
        for v in seasons.values())
    print(f'\n      seasons clearing breakeven: {clear}/{len(rates)} · '
          f'{expect:.1f} expected at a constant {pooled:.1f}%')
    print(f'      firing rate: {n_tot / len(rates):.1f} games/season = '
          f'~{100 * n_tot / len(rates) / 272:.0f}% of a 272-game slate')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='1999-01-01')
    a = ap.parse_args()

    raw = _page('nfl_game_results',
                {'select': 'game_date,season,home_team,away_team,wind,temp,'
                           'roof,total_result,close_total,home_score,'
                           'away_score',
                 'game_date': f'gte.{a.since}'})
    rows = []
    for x in raw:
        tr = str(x.get('total_result') or '').lower()
        w = _f(x.get('wind'))
        gd = str(x.get('game_date') or '')[:10]
        if tr not in ('over', 'under') or w is None or not gd:
            continue
        rows.append({'over': 1 if tr == 'over' else 0, 'wind': w,
                     'wb': bucket(w), 'date': gd, 'yr': int(gd[:4]),
                     'ct': _f(x.get('close_total')),
                     'roof': str(x.get('roof') or '').lower()})
    print(f'=== NFL wind -> totals · {len(rows)} graded outdoor-measured '
          f'games, {min(r["date"] for r in rows)}..'
          f'{max(r["date"] for r in rows)}')
    print(f'    breakeven at -110 both ways: {BREAKEVEN}%')

    # ── 1. FULL HISTORY ──────────────────────────────────────────────────
    print('\n' + '-' * 68)
    print('  1. FULL HISTORY — does doubling the sample tighten or dissolve it')
    report('all seasons', rows)

    # ── 2. ERA SPLIT ─────────────────────────────────────────────────────
    print('\n' + '-' * 68)
    print('  2. ERA SPLIT — real physics shows up in BOTH halves.')
    print('     This is the closest thing to walk-forward for a measurement:')
    print('     an effect that lives in one era only is a rules artefact or')
    print('     noise that clumped, and must not be published either way.')
    yrs = sorted({r['yr'] for r in rows})
    mid = yrs[len(yrs) // 2]
    early = report(f'{yrs[0]}-{mid - 1}', [r for r in rows if r['yr'] < mid])
    late = report(f'{mid}-{yrs[-1]}', [r for r in rows if r['yr'] >= mid])
    if '11+' in early and '11+' in late:
        print(f'\n      UNDER at {WINDY_MIN:.0f}+ wind: early '
              f'{100 - early["11+"][0]:.2f}% (n={early["11+"][1]}) · '
              f'late {100 - late["11+"][0]:.2f}% (n={late["11+"][1]})')
        both = all(100 - d['11+'][0] > BREAKEVEN for d in (early, late))
        print(f'      above breakeven in BOTH eras: '
              f'{"YES" if both else "NO — do not publish"}')

    # ── 2b. SEASON-BY-SEASON ─────────────────────────────────────────────
    print('\n' + '-' * 68)
    print('  2b. SEASON CONSISTENCY — two eras can hide a lumpy edge.')
    season_consistency(rows)

    # ── 3. ALREADY PRICED? ───────────────────────────────────────────────
    print('\n' + '-' * 68)
    print('  3. IS IT PRICED? The mean close_total column above is the test.')
    print('     If the market already drops the number in wind AND the under')
    print('     still clears breakeven, it is under-adjusting — that residual')
    print('     is the only thing worth betting.')
    calm = [r['ct'] for r in rows if r['wb'] == '0-5' and r['ct'] is not None]
    windy = [r['ct'] for r in rows
             if r['wb'] in ('11-15', '16+') and r['ct'] is not None]
    if calm and windy:
        d = statistics.fmean(calm) - statistics.fmean(windy)
        print(f'\n      mean close_total calm {statistics.fmean(calm):.2f} '
              f'(n={len(calm)}) vs 11+ wind {statistics.fmean(windy):.2f} '
              f'(n={len(windy)})')
        print(f'      the market already takes {d:+.2f} points off the total '
              f'in wind.')
        verdict = ('It moves the number — so any remaining edge is the '
                   'residual.' if d > 0.25 else
                   'It barely moves the number at all.')
        print(f'      {verdict}')

    # ── 4. THE CAVEAT, MEASURED ──────────────────────────────────────────
    print('\n' + '-' * 68)
    print('  4. WHAT IS ACTUALLY CAPTURABLE')
    print('     nflverse `wind` is the condition AT the game, not the forecast')
    print('     the market priced days earlier. A forecast is a good but')
    print('     imperfect predictor of game-time wind, so only the')
    print('     FORECASTABLE part of this gap is ours. The surprise part —')
    print('     games windier than anyone expected — is real and unbettable.')
    print('     Settling the split requires storing our own pre-game')
    print('     forecasts going forward; nothing in the DB can answer it')
    print('     retroactively. Treat the numbers above as an UPPER BOUND.')
    hi = [r for r in rows if r['wind'] >= 11]
    print(f'\n      games at 11+ wind: {len(hi)} of {len(rows)} '
          f'({100 * len(hi) / len(rows):.1f}%) — roughly '
          f'{len(hi) / max(1, len(set(r["yr"] for r in rows))):.1f} per '
          f'season, so this is a THIN filter, not a slate-filler.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
