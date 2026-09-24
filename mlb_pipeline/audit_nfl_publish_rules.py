"""Compare candidate NFL prop publication rules on graded results.

WHY
---
What reaches users today is `v_nfl_props_publishable`, which gates on
`tier IN ('PRIME','STRONG')`. Measured on 1,331 graded props to 2026-09-21
that rule went 202-189, 51.7%, against a 54.6% break-even at the prices we
paid — ROI -5.21%, -20.4u. Meanwhile LEAN, the largest tier at 627 rows and
the most profitable, is never published at all, and PRIME stopped occurring
after 2026-09-13.

The candidate (R3) is: publish PRIME + STRONG + LEAN, restricted to counting
stats, inside the -150..+150 price band. On the same history it went 198-162,
55.0% against a 52.8% break-even, ROI +4.22%, +15.2u — at n=360 against
today's n=391, so roughly the same exposure with the sign flipped.

Andy concurred with running it in shadow and revisiting after the 2026-09-27
weekend rather than gating on it now. This script is the Monday command.

EVERY NUMBER HERE IS IN-SAMPLE UNTIL THE WEEKEND LANDS. The "counting stats"
and "-150..+150" filters were both chosen by looking at the same rows they are
scored on, so the historical figures flatter the rule. The only honest test is
games played after the rule was written, which is what --since is for.

ON MUTATION. These rules read `tier`, `prop_type` and the published side's
odds off nfl_pipeline_props, which is a mutable table. It is safe to measure
after the fact because nfl_generate_props only builds rows for games between
-1h and +14 days out, so a row stops changing at kickoff. public_receipts is
NOT used as the source precisely because NFL's rows there are 998
reconstructed against 2 live, so it would inherit any drift rather than
protect against it.

USAGE
    python audit_nfl_publish_rules.py                       # all graded
    python audit_nfl_publish_rules.py --since 2026-09-27    # the weekend
    python audit_nfl_publish_rules.py --since 2026-09-27 --by-tier
"""
import os
import sys
import argparse
from collections import defaultdict

import requests

sys.stdout.reconfigure(encoding='utf-8')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

MIN_N = 30
BAND_LO, BAND_HI = -150, 150

# Counting stats: driven by role and usage, which the market re-prices slowly.
# Yardage is decided by explosive plays, which nothing forecasts — measured
# 47.2% (n=599) against 53.8% (n=732) for these.
COUNTING = {'receptions', 'pass_attempts', 'pass_completions',
            'rush_attempts', 'pass_tds', 'rush_tds',
            'pass_interceptions', 'anytime_td'}


def page(select: str, extra: dict | None = None) -> list:
    out, offset = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/nfl_pipeline_props', headers=H,
                         timeout=200,
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


def won(r) -> bool:
    return str(r.get('result') or '').strip().upper() in ('WIN', 'W')


def graded(r) -> bool:
    return str(r.get('result') or '').strip().upper() in ('WIN', 'W',
                                                          'LOSS', 'L')


def taken_odds(r):
    """Price on the side we published."""
    return _f(r.get('book_over_odds') if r.get('direction') == 'over'
              else r.get('book_under_odds'))


def profit(o: float) -> float:
    return (o / 100.0) if o > 0 else (100.0 / abs(o))


def implied(o: float) -> float:
    return (100.0 / (o + 100.0)) if o > 0 else (abs(o) / (abs(o) + 100.0))


def family(r) -> str:
    return (r.get('prop_type') or '').rsplit('_', 1)[0]


def tier(r) -> str:
    return str(r.get('tier') or '?').upper()


def in_band(r) -> bool:
    o = taken_odds(r)
    return o is not None and BAND_LO <= o <= BAND_HI


def evaluate(rows) -> dict | None:
    if not rows:
        return None
    w = sum(1 for r in rows if won(r))
    staked = net = 0.0
    probs = []
    for r in rows:
        o = taken_odds(r)
        if o is None:
            continue
        staked += 1.0
        probs.append(implied(o))
        net += profit(o) if won(r) else -1.0
    return {
        'n': len(rows), 'w': w, 'l': len(rows) - w,
        'hit': 100.0 * w / len(rows),
        'needs': 100.0 * sum(probs) / len(probs) if probs else float('nan'),
        'roi': net / staked * 100.0 if staked else float('nan'),
        'units': net,
    }


def line(label: str, m: dict | None) -> None:
    if not m:
        print(f'  {label:46s} nothing qualifies')
        return
    flag = '   n<30' if m['n'] < MIN_N else ''
    print(f"  {label:46s} {m['w']:>4d}-{m['l']:<4d} {m['hit']:6.1f}% "
          f"needs {m['needs']:4.1f}%  ROI {m['roi']:+6.2f}%  "
          f"{m['units']:+7.1f}u  n={m['n']}{flag}")


RULES = {
    'R1 live today: PRIME+STRONG, any price, any stat':
        lambda r: tier(r) in ('PRIME', 'STRONG'),
    'R2: PRIME+STRONG, in band, counting only':
        lambda r: tier(r) in ('PRIME', 'STRONG') and in_band(r)
        and family(r) in COUNTING,
    'R3 CANDIDATE: +LEAN, in band, counting only':
        lambda r: tier(r) in ('PRIME', 'STRONG', 'LEAN') and in_band(r)
        and family(r) in COUNTING,
    'R4: PRIME+LEAN (no STRONG), in band, counting':
        lambda r: tier(r) in ('PRIME', 'LEAN') and in_band(r)
        and family(r) in COUNTING,
    'R6 price gate alone (any stat)':
        lambda r: tier(r) in ('PRIME', 'STRONG') and in_band(r),
    'R7 counting alone (any price)':
        lambda r: tier(r) in ('PRIME', 'STRONG', 'LEAN')
        and family(r) in COUNTING,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', help='YYYY-MM-DD — the out-of-sample window')
    ap.add_argument('--by-tier', action='store_true',
                    help='break the candidate rule down by tier')
    args = ap.parse_args()

    extra = {'game_date': f'gte.{args.since}'} if args.since else None
    rows = [r for r in page(
        'game_date,player_name,prop_type,prop_line,direction,tier,'
        'conviction,result,book_over_odds,book_under_odds', extra)
        if graded(r)]
    if not rows:
        print('no graded rows in range — nothing to measure yet')
        return
    span = (min(str(r['game_date'])[:10] for r in rows),
            max(str(r['game_date'])[:10] for r in rows))
    print(f'graded NFL props: {len(rows)}   dates {span[0]} .. {span[1]}')
    if args.since:
        print('OUT-OF-SAMPLE WINDOW — the rule was written 2026-09-24 on data '
              'up to 09-21.')
    else:
        print('IN-SAMPLE — filters were chosen by looking at these same rows.')
    print()
    for label, rule in RULES.items():
        line(label, evaluate([r for r in rows if rule(r)]))

    r1 = evaluate([r for r in rows if RULES[
        'R1 live today: PRIME+STRONG, any price, any stat'](r)])
    r3 = evaluate([r for r in rows if RULES[
        'R3 CANDIDATE: +LEAN, in band, counting only'](r)])
    if r1 and r3:
        print(f"\n  R3 vs R1:  ROI {r3['roi'] - r1['roi']:+.2f}pp   "
              f"units {r3['units'] - r1['units']:+.1f}u   "
              f"exposure {r3['n'] - r1['n']:+d} props")
        if r3['n'] < MIN_N:
            print('  R3 sample is under 30 — not readable yet, wait for more '
                  'games.')

    if args.by_tier:
        print('\nR3 broken down by tier (is the ladder ordered inside it?)')
        sub = [r for r in rows if RULES[
            'R3 CANDIDATE: +LEAN, in band, counting only'](r)]
        for t in ('PRIME', 'STRONG', 'LEAN'):
            line(f'  {t}', evaluate([r for r in sub if tier(r) == t]))


if __name__ == '__main__':
    main()
