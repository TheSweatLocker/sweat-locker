"""Do the published prop tiers actually rank by outcome?

PRIME / STRONG / LIGHT / LEAN are shown to paying users as a confidence
ladder. This checks whether the ladder holds, per sport, on data that is
allowed to be quoted.

THE LEAK WINDOW IS NOT OPTIONAL. project_prop_l5_leak_922: MLB's L5/L10
lookback included the game being predicted, and those columns feed the LR
model that overrides tier. No valid MLB prop tier record exists before
2026-09-22. Quoting PRIME 74.5% from before that date is quoting the leak.
So MLB is reported in two windows, clearly separated, and the pre-fix one
is labelled as unusable rather than omitted — omitting it invites someone
to recompute it later and believe it.

NFL WAS CHECKED, NOT ASSUMED. The leak lived in
backfill_prop_lookback.fetch_mlb_player_recent, an MLB-specific function.
NFL derives its hit counts from signals._stat_last10, which
nfl_generate_props writes at generation time from completed games. To
confirm the predicted game is not inside its own window, --leak-check runs
the control-group test the leak writeup recommends: for each position in
the array, how often does that entry's value equal the prop's own
final_value? A leak makes position 0 spike against positions 1-4. Measured
2026-09-24: 10.9 / 11.4 / 10.5 / 10.9 / 9.6 percent — flat, so position 0
is a genuinely prior game and the 10.9% is just how often a player repeats
a number. NFL history is clean and quotable.

Every rate prints its n, and anything under n=30 is marked unreadable.
"""
import os
import sys
import argparse
import requests
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

MLB_LEAK_FIX = '2026-09-22'
BREAKEVEN = 52.4          # -110
MIN_N = 30
TIER_ORDER = ['PRIME', 'STRONG', 'LIGHT', 'LEAN', 'COVERAGE', 'SKIP']

SPORTS = {
    'NFL': 'nfl_pipeline_props',
    'MLB': 'mlb_pipeline_props',
    'NBA': 'nba_pipeline_props',
    'NHL': 'nhl_pipeline_props',
}


def page(table: str, select: str, extra: dict | None = None) -> list:
    out, offset = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=200,
                         params={**(extra or {}), 'select': select,
                                 'order': 'id.asc',
                                 'limit': 1000, 'offset': offset})
        r.raise_for_status()
        b = r.json()
        out.extend(b)
        if len(b) < 1000:
            return out
        offset += 1000


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def graded(rows: list) -> list:
    return [r for r in rows
            if str(r.get('result') or '').strip().upper()
            in ('WIN', 'W', 'LOSS', 'L')]


def _won(r) -> bool:
    return str(r.get('result') or '').strip().upper() in ('WIN', 'W')


def report(label: str, rows: list, usable: bool = True) -> None:
    g = graded(rows)
    tally = defaultdict(lambda: [0, 0])
    for r in g:
        t = str(r.get('tier') or '?').upper()
        tally[t][1] += 1
        if _won(r):
            tally[t][0] += 1
    head = f'{label}   graded n={len(g)}'
    print(f'\n{head}')
    if not usable:
        print('  ' + '!' * (len(head) - 2))
        print('  NOT QUOTABLE — inside the pre-2026-09-22 leak window.')
    if not g:
        print('  no graded rows')
        return
    seen = [t for t in TIER_ORDER if t in tally] + \
           [t for t in tally if t not in TIER_ORDER]
    for t in seen:
        w, n = tally[t]
        rate = 100.0 * w / n
        mark = '' if n >= MIN_N else '   n<30, not readable'
        vig = '' if n < MIN_N else ('  above -110' if rate >= BREAKEVEN
                                    else '  below -110')
        print(f'  {t:9s} {w:>5d}-{n - w:<5d} {rate:5.1f}%  n={n:<6d}{vig}{mark}')

    # Does the ladder actually descend? Only judge on readable tiers.
    ladder = [(t, 100.0 * tally[t][0] / tally[t][1], tally[t][1])
              for t in TIER_ORDER
              if t in tally and t != 'SKIP' and tally[t][1] >= MIN_N]
    if len(ladder) < 2:
        print('  ordering: not enough readable tiers to judge')
        return
    bad = [(a[0], a[1], b[0], b[1])
           for a, b in zip(ladder, ladder[1:]) if a[1] < b[1]]
    if bad:
        print('  ordering: BROKEN — a lower tier outperforms a higher one')
        for hi, hr, lo, lr in bad:
            print(f'      {hi} {hr:.1f}%  <  {lo} {lr:.1f}%')
    else:
        print('  ordering: holds across readable tiers')


def leak_check(table: str) -> None:
    """Is the predicted game sitting inside its own lookback window?"""
    rows = page(table, 'id,opp_team,result,final_value,signals')
    g = graded(rows)
    hit = [0] * 5
    tot = [0] * 5
    oppm = [0] * 5
    for x in g:
        s = x.get('signals')
        arr = s.get('_stat_last10') if isinstance(s, dict) else None
        fv = _f(x.get('final_value'))
        if not isinstance(arr, list) or fv is None:
            continue
        opp = (x.get('opp_team') or '').upper()
        for i in range(min(5, len(arr))):
            e = arr[i]
            if not isinstance(e, dict):
                continue
            v = _f(e.get('value'))
            if v is None:
                continue
            tot[i] += 1
            if abs(v - fv) < 1e-9:
                hit[i] += 1
                if str(e.get('opp') or '').upper() == opp:
                    oppm[i] += 1
    print('\nLEAK CONTROL TEST — value equal to this prop\'s own final_value,')
    print('by position in _stat_last10 (0 = newest). A leak spikes position 0.')
    for i in range(5):
        if not tot[i]:
            continue
        print(f'  pos {i}  {hit[i]:>5d}/{tot[i]:<6d} {100.0*hit[i]/tot[i]:5.1f}%'
              f'   (also same opponent: {oppm[i]})')
    if tot[0] and tot[1]:
        p0 = 100.0 * hit[0] / tot[0]
        rest = [100.0 * hit[i] / tot[i] for i in range(1, 5) if tot[i]]
        avg = sum(rest) / len(rest)
        verdict = 'LEAK SUSPECTED' if p0 > avg + 5 else 'flat — no leak'
        print(f'  position 0 {p0:.1f}% vs positions 1-4 avg {avg:.1f}%  ->  {verdict}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=list(SPORTS))
    ap.add_argument('--leak-check', action='store_true',
                    help='run the control test on the lookback window')
    args = ap.parse_args()

    sports = [args.sport] if args.sport else list(SPORTS)
    for sport in sports:
        table = SPORTS[sport]
        print('\n' + '=' * 72)
        print(f'  {sport}  ({table})')
        print('=' * 72)
        try:
            rows = page(table, 'id,game_date,tier,result')
        except Exception as e:
            print(f'  fetch failed: {e}')
            continue
        if not rows:
            print('  table is empty')
            continue
        if sport == 'MLB':
            post = [r for r in rows if str(r.get('game_date'))[:10] >= MLB_LEAK_FIX]
            pre = [r for r in rows if str(r.get('game_date'))[:10] < MLB_LEAK_FIX]
            report(f'MLB, on or after {MLB_LEAK_FIX} (clean)', post)
            report(f'MLB, before {MLB_LEAK_FIX}', pre, usable=False)
        else:
            report(f'{sport}, all graded history', rows)
        if args.leak_check and sport in ('NFL',):
            leak_check(table)


if __name__ == '__main__':
    main()
