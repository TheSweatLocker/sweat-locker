"""What did the overrides actually change, and were they right?

Andy 2026-09-25: "should we eliminate the refit override, does it even work or
does it just constantly cause confusion."

That question was unanswerable. prop_jerry_reads.call_verdict is the SHIPPED
value and eight scripts mutate it in place, so the model's own call was gone by
the time anyone looked. The refit override could be compared to results but
never to the decision it replaced.

Migration 20260925a adds generator_verdict / generator_conviction, an immutable
snapshot written once at insert. This reads both and answers three things:

  1. HOW OFTEN an override moves a call, and in which direction.
  2. WHETHER the move was right — the shipped call's record vs the record the
     generator's call would have produced on the same rows.
  3. PER RULE, so a rule that earns its keep is separable from one that does
     not, instead of judging "the override" as one thing.

That second one is the point. A cap that demotes a 45% population is doing its
job; the same cap on a 60% population is destroying edge. Same rule, opposite
verdict, and until now neither was visible.

NOTE ON WHAT CAN BE ANSWERED. Rows created before 2026-09-25 that were already
overridden carry a NULL snapshot — the original is genuinely unrecoverable and
the migration does not guess. Those rows are reported as "no snapshot" and
excluded from the comparison rather than silently folded in. The picture fills
in from here.

BREAKEVEN is 54.2%, not 52.4% — average published price is -118.6.

    python audit_override_impact.py                  # last 30d, MLB
    python audit_override_impact.py --sport NFL --days 14
    python audit_override_impact.py --since 2026-09-22   # post L5-leak-fix only
"""
import argparse
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

# Real breakeven at the prices we actually publish, not the -110 ideal.
BREAKEVEN = 54.2

# The L5 lookback leak means anything before this date had inflated form
# signals. Records spanning it are not comparable.
LEAK_FIX_DATE = '2026-09-22'

_RULE_RE = re.compile(r'\[Auto-[a-z-]+ \d{4}-\d{2}-\d{2} ([A-Z_0-9]+)')


def page(path: str, params: dict) -> list:
    """Paginate — PostgREST caps at 1000 and says so only in a header."""
    out, off = [], 0
    while True:
        q = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, params=q, timeout=90)
        if r.status_code not in (200, 206):
            body = (r.text or '')[:200]
            if 'generator_verdict' in body:
                print('\n  Migration 20260925a has not been applied yet.\n'
                      '  Apply supabase/migrations/20260925a_prop_jerry_'
                      'generator_snapshot.sql, then re-run.\n')
                sys.exit(3)
            print(f'  ERROR {r.status_code}: {body}')
            sys.exit(2)
        b = r.json()
        if not isinstance(b, list):
            print(f'  ERROR: {str(b)[:200]}')
            sys.exit(2)
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def rule_of(row: dict) -> str:
    hits = _RULE_RE.findall(row.get('audit_notes') or '')
    return hits[-1] if hits else 'NO_OVERRIDE'


def rec(rows: list) -> tuple:
    """(wins, losses, hit%) over decided rows only; pushes excluded."""
    w = sum(1 for x in rows if str(x.get('result') or '').upper().startswith('W'))
    l = sum(1 for x in rows if str(x.get('result') or '').upper().startswith('L'))
    return w, l, (w / (w + l) * 100 if (w + l) else None)


def fmt(w, l, pct, n_floor=30) -> str:
    n = w + l
    if not n:
        return f'{"—":>16s}'
    flag = '' if n >= n_floor else ' (n<30)'
    return f'{w:3d}-{l:<3d} n={n:4d} {pct:5.1f}%{flag}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='MLB')
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--since', default=None,
                    help='explicit start date; overrides --days')
    args = ap.parse_args()

    since = args.since or (
        (datetime.now(timezone.utc) - timedelta(hours=4)).date()
        - timedelta(days=args.days)).isoformat()

    rows = page('prop_jerry_reads', {
        'select': 'id,game_date,player_name,prop_type,direction,call_verdict,'
                  'conviction,generator_verdict,generator_conviction,'
                  'audit_notes,result,book_odds',
        'sport': f'eq.{args.sport}', 'game_date': f'gte.{since}',
        'order': 'game_date.asc'})

    print(f'=== override impact · {args.sport} · since {since} ===')
    print(f'    {len(rows)} reads')
    if since < LEAK_FIX_DATE:
        print(f'    ⚠ window starts before the L5 lookback leak fix '
              f'({LEAK_FIX_DATE}). Rows before that date carry inflated form '
              f'signals — measured at +6.7pp on untouched props — so records '
              f'spanning it are not comparable. Use --since {LEAK_FIX_DATE}.')

    snap = [x for x in rows if x.get('generator_verdict')]
    print(f'    {len(snap)} with a generator snapshot, '
          f'{len(rows) - len(snap)} without (pre-migration overridden rows)')
    if not snap:
        print('\n  No snapshots yet. Reads created from now on will carry one; '
              'this fills in as the pipeline runs.')
        return

    moved = [x for x in snap
             if (x.get('generator_verdict') or '') != (x.get('call_verdict') or '')
             or (x.get('generator_conviction') is not None
                 and x.get('generator_conviction') != x.get('conviction'))]
    verdict_moved = [x for x in moved
                     if (x.get('generator_verdict') or '') != (x.get('call_verdict') or '')]
    print(f'    {len(moved)} touched by a mutator '
          f'({len(verdict_moved)} had the VERDICT changed, '
          f'{len(moved) - len(verdict_moved)} conviction only)')

    print('\n--- 1. WHERE CALLS MOVED (generator -> shipped) ---')
    trans = Counter((x.get('generator_verdict'), x.get('call_verdict'))
                    for x in verdict_moved)
    if not trans:
        print('    no verdict changes in window')
    for (g, c), n in trans.most_common(12):
        sel = [x for x in verdict_moved
               if x.get('generator_verdict') == g and x.get('call_verdict') == c]
        w, l, p = rec(sel)
        print(f'    {str(g):8s} -> {str(c):8s} {n:4d}   shipped went {fmt(w, l, p)}')

    print('\n--- 2. WAS THE OVERRIDE RIGHT? ---')
    print('    On rows a mutator touched, the record the SHIPPED call produced.')
    print('    A demotion is correct when that record is below breakeven —')
    print('    it kept a losing population off the cards.')
    w, l, p = rec(moved)
    print(f'\n    touched by a mutator : {fmt(w, l, p)}')
    # Membership by id — `x not in moved` compares dicts field by field across
    # the whole list, which is O(n²) on a 12k-row table.
    moved_ids = {x['id'] for x in moved}
    un = [x for x in snap if x['id'] not in moved_ids]
    w2, l2, p2 = rec(un)
    print(f'    untouched            : {fmt(w2, l2, p2)}')
    print(f'    breakeven            : {BREAKEVEN}%')
    if p is not None and p2 is not None:
        if p < BREAKEVEN <= p2:
            print('\n    -> mutators are demoting a losing population while '
                  'leaving a profitable one alone. Working as intended.')
        elif p >= BREAKEVEN > p2:
            print('\n    -> mutators are touching the PROFITABLE population and '
                  'leaving the losing one. Inverted — investigate before trusting.')
        else:
            print('\n    -> both sides land on the same side of breakeven; '
                  'this window does not separate them.')

    print('\n--- 3. PER RULE ---')
    by_rule = defaultdict(list)
    for x in snap:
        by_rule[rule_of(x)].append(x)
    print(f'    {"rule":30s} {"shipped record":>22s}   {"avg conv move":>13s}')
    for r, sel in sorted(by_rule.items(), key=lambda z: -len(z[1])):
        w, l, p = rec(sel)
        deltas = [x['conviction'] - x['generator_conviction'] for x in sel
                  if x.get('conviction') is not None
                  and x.get('generator_conviction') is not None]
        dv = f'{sum(deltas)/len(deltas):+6.1f}' if deltas else '     —'
        verdict = ''
        if p is not None and (w + l) >= 30:
            verdict = '  ✓ earns it' if (
                (r != 'NO_OVERRIDE' and p < BREAKEVEN) or
                (r == 'NO_OVERRIDE' and p >= BREAKEVEN)
            ) else '  ⚠ check'
        print(f'    {r:30s} {fmt(w, l, p):>22s}   {dv:>13s}{verdict}')

    print('\n    "earns it" on a mutator rule means the population it touched '
          'lost money,\n    so demoting it was correct. It is not a claim the '
          'rule made money.')


if __name__ == '__main__':
    main()
