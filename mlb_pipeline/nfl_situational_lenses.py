"""Rest, weather and venue as cover lenses — NFL, on 7,000+ games.

WHY (2026-10-09)
----------------
The NCAAF lens panel failed because all six of its lenses were the same
question: five correlated at r=+0.76..+0.97 ("how good is this offence") and
only explosiveness was independent. A panel needs inputs that differ in KIND.

Rest, weather and venue differ in kind. They are not team quality at all — a
well-rested team in a dome is not a better team, it is a team in better
circumstances. That is exactly the sort of thing a quality rating cannot
express.

AND THIS IS THE FIRST WELL-POWERED TEST IN THE PROJECT. Every negative result
today came off thin data — 247 graded picks, 277 predicted games, 57 combo
games. Here:

    nfl_game_results   7,141 games with a cover result
    home_rest/away_rest  7,081    roof, div_game  7,081
    temp/wind            5,055

At n=7,000 a median split has a two-standard-error band near 2.4pp, so an
effect of any practical size becomes visible instead of being swamped. A null
here is a real null, and a hit here is worth acting on.

TWO THINGS THAT KEEP THIS HONEST
  * NO LEAK IS POSSIBLE for the raw measurement. Rest, roof and division are
    known days ahead; temp and wind are forecast pre-game. Nothing here is
    computed from the result. No walk-forward split is needed because nothing
    is fitted — these are measurements, not models.
  * MULTIPLE COMPARISONS ARE COUNTED. This runs roughly a dozen tests. At a
    two-standard-error bar you expect about one false positive per twenty
    tests, so anything landing between 2SE and 3SE is reported as suggestive
    and NOT as a finding. The 3SE column is the one to believe.

THE QUESTION BEHIND THE QUESTION
The market knows the rest days and the forecast too, so the honest bar is not
"does rest relate to winning" — it is "does rest relate to COVERING", i.e. is
the effect left unpriced. Covering is therefore the only outcome measured.

WRITES NOTHING.

CLI
    python nfl_situational_lenses.py
    python nfl_situational_lenses.py --since 2020-01-01
"""
from __future__ import annotations

import argparse
import collections
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


TESTS = []            # (label, bucket_fn) — bucket_fn returns a key or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2015-01-01')
    ap.add_argument('--min-n', type=int, default=150, dest='min_n')
    a = ap.parse_args()

    rows = [x for x in _page('nfl_game_results',
                             {'select': 'game_date,home_team,away_team,'
                                        'home_rest,away_rest,temp,wind,roof,'
                                        'div_game,close_spread,spread_result,'
                                        'total_result,close_total',
                              'game_date': f'gte.{a.since}'})
            if str(x.get('spread_result') or '').lower()
            in ('home_covered', 'away_covered')]
    for r in rows:
        r['hc'] = 1 if str(r['spread_result']).lower() == 'home_covered' else 0
        # The totals outcome is a SEPARATE column with its own vocabulary and
        # its own missingness — a game can have a graded spread and an
        # ungraded total. Derive it explicitly and leave it None otherwise
        # rather than letting .get('over') read as a silent NULL, which is the
        # exact failure mode that produced four phantom defects earlier today.
        tr = str(r.get('total_result') or '').lower()
        r['over'] = 1 if tr == 'over' else 0 if tr == 'under' else None
    print(f'=== NFL situational lenses · {len(rows)} graded games '
          f'since {a.since}')
    base = statistics.fmean([r['hc'] for r in rows]) * 100
    print(f'    base home-cover rate {base:.2f}%  ·  breakeven 52.38%')
    print(f'    a null here is a REAL null: 2SE at n={len(rows)} is '
          f'{2 * (0.5 / len(rows) ** 0.5) * 100:.2f}pp')

    results = []

    def test(label, fn, outcome='hc'):
        buckets = collections.defaultdict(list)
        for r in rows:
            k = fn(r)
            if k is None:
                continue
            v = r.get(outcome)
            if v is None:
                continue
            buckets[k].append(v)
        usable = {k: v for k, v in buckets.items() if len(v) >= a.min_n}
        if len(usable) < 2:
            print(f'\n  {label}: fewer than two buckets reach n={a.min_n}')
            return
        print(f'\n  {label}')
        stats = []
        for k in sorted(usable, key=lambda z: str(z)):
            v = usable[k]
            p = statistics.fmean(v) * 100
            se = (0.5 / len(v) ** 0.5) * 100
            stats.append((k, p, len(v), se))
            print(f'      {str(k):<26}{p:6.2f}%  n={len(v):<6} '
                  f'+/-{2 * se:4.2f}pp')
        hi = max(stats, key=lambda s: s[1])
        lo = min(stats, key=lambda s: s[1])
        gap = hi[1] - lo[1]
        se = (hi[3] ** 2 + lo[3] ** 2) ** 0.5
        verdict = ('FINDING (>3SE)' if gap > 3 * se
                   else 'suggestive (2-3SE)' if gap > 2 * se
                   else 'noise')
        print(f'      spread {hi[0]} vs {lo[0]}: {gap:+.2f}pp · '
              f'2SE {2 * se:.2f} · 3SE {3 * se:.2f} -> {verdict}')
        results.append((label, gap, 2 * se, 3 * se, verdict))

    # ── REST ─────────────────────────────────────────────────────────────
    def rest_diff(r):
        h, aw = _f(r.get('home_rest')), _f(r.get('away_rest'))
        if h is None or aw is None:
            return None
        d = h - aw
        if d <= -7:
            return 'away rested 7+ more'
        if d <= -3:
            return 'away rested 3-6 more'
        if d < 3:
            return 'similar rest (<3d)'
        if d < 7:
            return 'home rested 3-6 more'
        return 'home rested 7+ more'
    test('REST DIFFERENTIAL -> home cover', rest_diff)

    def short_week(r):
        h, aw = _f(r.get('home_rest')), _f(r.get('away_rest'))
        if h is None or aw is None:
            return None
        if h <= 4 and aw > 4:
            return 'home on short week only'
        if aw <= 4 and h > 4:
            return 'away on short week only'
        if h <= 4 and aw <= 4:
            return 'both short'
        return 'neither short'
    test('SHORT WEEK (<=4 days) -> home cover', short_week)

    # ── WEATHER ──────────────────────────────────────────────────────────
    def wind_b(r):
        w = _f(r.get('wind'))
        if w is None:
            return None
        return ('wind 0-5' if w <= 5 else 'wind 6-10' if w <= 10
                else 'wind 11-15' if w <= 15 else 'wind 16+')
    test('WIND -> home cover', wind_b)
    test('WIND -> OVER hit', wind_b, outcome='over')

    def temp_b(r):
        t = _f(r.get('temp'))
        if t is None:
            return None
        return ('temp <=32' if t <= 32 else 'temp 33-50' if t <= 50
                else 'temp 51-70' if t <= 70 else 'temp 71+')
    test('TEMPERATURE -> home cover', temp_b)
    test('TEMPERATURE -> OVER hit', temp_b, outcome='over')

    # ── VENUE / CONTEXT ──────────────────────────────────────────────────
    # Four raw roof values, but only two physical conditions. A retractable
    # roof that is CLOSED is indoors — no wind, no rain, controlled temp — and
    # 'open' is outdoors. Grouping on the condition rather than the stadium
    # type is both the right question and what keeps 'open' (n=2 in the latest
    # 1000) from being silently dropped by the sample floor.
    _INDOOR = {'dome', 'closed'}
    _OUTDOOR = {'outdoors', 'open'}

    def roof_cond(r):
        v = str(r.get('roof') or '').lower()
        return ('indoors' if v in _INDOOR else
                'outdoors' if v in _OUTDOOR else None)
    test('ROOF CONDITION -> home cover', roof_cond)
    test('ROOF CONDITION -> OVER hit', roof_cond, outcome='over')
    test('DIVISION GAME -> home cover',
         lambda r: ('divisional' if r.get('div_game') in (True, 'true', 1)
                    else 'non-divisional') if r.get('div_game') is not None
         else None)

    def fav_size(r):
        cs = _f(r.get('close_spread'))
        if cs is None:
            return None
        # NFL/nflverse: POSITIVE close_spread = HOME favourite (verified
        # empirically 2026-10-09 against who actually won).
        return ('home fav 7+' if cs >= 7 else 'home fav 3-7' if cs >= 3
                else 'pick-em' if cs > -3 else 'away fav 3-7' if cs > -7
                else 'away fav 7+')
    test('FAVOURITE SIZE -> home cover', fav_size)

    print('\n' + '=' * 70)
    print(f'  {len(results)} tests run. At a 2SE bar you expect roughly one')
    print('  false positive per twenty tests, so believe the 3SE column.')
    findings = [r for r in results if r[4].startswith('FINDING')]
    sugg = [r for r in results if r[4].startswith('suggestive')]
    print(f'\n  FINDINGS (>3SE): {len(findings)}')
    for lbl, gap, s2, s3, _v in findings:
        print(f'    {lbl:<38}{gap:+.2f}pp  (3SE {s3:.2f})')
    print(f'  suggestive (2-3SE): {len(sugg)}')
    for lbl, gap, s2, s3, _v in sugg:
        print(f'    {lbl:<38}{gap:+.2f}pp  (2SE {s2:.2f}, 3SE {s3:.2f})')
    if not findings:
        print('\n  No lens clears 3SE. On THIS much data that is a real null,')
        print('  not a sample-size excuse — which is what makes it useful.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
