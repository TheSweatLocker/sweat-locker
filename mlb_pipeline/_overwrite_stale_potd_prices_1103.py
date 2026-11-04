"""Replace stored POTD prices that disagree with the recoverable real close.

== ANDY APPROVED THIS, 2026-11-03: "Overwrtight frozen picks." ===========
Replaying price recovery over all 175 POTD receipts found 36 whose stored
`pick_odds` disagrees with the price recoverable from the authoritative
source, 17 of them a flat -110:

    2026-08-02  LOSS  stored -110  real -232   Milwaukee Brewers ML
    2026-10-03  WIN   stored -110  real +124   Chicago White Sox ML
    2026-08-26  WIN   stored -110  real +100   Under 8.5
    2026-09-29  WIN   stored +102  real -145   Michael King Over 14.5 Outs

On those 36 graded rows:

    as stored    21-15  58.33% vs BE 55.42%  ROI +8.70%
    real prices  21-15  58.33% vs BE 58.10%  ROI +4.10%
                 ROI overstated 4.60pp, breakeven understated 2.68pp

A flat -110 on a game that closed -232 is the exact shape of
feedback_flat_110_inflates_every_ml_figure.

== pick_odds IS SET-ONCE, SO THIS MAY BE REFUSED ========================
The receipt freeze accepted the 106 null->value writes earlier tonight
because null counts as unset. Overwriting a NON-null value is a different
operation and the trigger may reject it with a 204 and no change -- the same
silent refusal that dropped the `audit` stamp
(feedback_receipt_audit_column_is_frozen_too).

So this reads back EVERY row it touches and reports refusals as refusals. It
does not report success off a status code.

Provenance for each change is appended to
docs/potd_price_provenance_1103.json by the manifest script, which should be
re-run with --write afterwards.

    python _overwrite_stale_potd_prices_1103.py            # dry run
    python _overwrite_stale_potd_prices_1103.py --apply
"""
import json
import os
import statistics
import sys
from pathlib import Path

import requests

for _ln in (Path(__file__).parent / '.env').read_text(
        encoding='utf-8', errors='replace').split('\n'):
    if '=' in _ln and not _ln.lstrip().startswith('#'):
        _k, _v = _ln.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

APPLY = '--apply' in sys.argv
MANIFEST = (Path(__file__).parent.parent / 'docs'
            / 'potd_price_provenance_1103.json')


def payout(o):
    o = float(o)
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def roi(pairs):
    w = l = 0
    u = 0.0
    for res, o in pairs:
        if res == 'WIN':
            w += 1
            u += payout(o)
        elif res == 'LOSS':
            l += 1
            u -= 1.0
    n = w + l
    be = statistics.mean([100 * abs(float(o)) / (abs(float(o)) + 100)
                          for _r, o in pairs]) if pairs else 0.0
    return w, l, (100.0 * w / n if n else 0.0), be, (100.0 * u / n if n else 0.0)


def main() -> int:
    man = json.loads(MANIFEST.read_text(encoding='utf-8'))
    plan = [e for e in man['entries']
            if e.get('provenance') == 'measured'
            and e.get('stored_odds') not in (None, '')
            and int(e['stored_odds']) != int(e['recovered_odds'])]
    print(f'POTD rows whose stored price != recoverable real price: {len(plan)}')

    # == TWO OF THE 36 ARE THE WRONG MARKET, AND I ALMOST WROTE THEM ======
    # Caught by reading the dry-run labels, not by the counts:
    #
    #   'San Diego Padres ML Away 1.5'   shape() matched \bML\b FIRST, so a
    #     RUN LINE was priced off away_ml_close -- the moneyline price for a
    #     spread bet.
    #   'Texas Rangers RL +1.5'          priced off spread_home_odds, but
    #     nothing here establishes that Texas was the HOME side. That is a
    #     coin flip on which side's price, and a coin flip is not a recovery.
    #
    # Both keep their stored price. A real price from the wrong market is
    # worse than a stale one, because it looks authoritative.
    EXCLUDE_SHAPES = {'SPREAD'}
    import re as _re
    _RL = _re.compile(r'\bML\b.*?(Away|Home)?\s*[+-]?\d+\.\d', _re.I)
    dropped = [e for e in plan
               if e['shape'] in EXCLUDE_SHAPES
               or (e['shape'] == 'ML' and _RL.search(str(e['pick_label'])))]
    if dropped:
        print(f'  EXCLUDED {len(dropped)} (wrong market / side not '
              f'establishable):')
        for e in dropped:
            print(f"     {e['game_date']}  {str(e['pick_label'])[:52]!r}  "
                  f"keeps {int(e['stored_odds']):+}")
    plan = [e for e in plan if e not in dropped]
    print(f'  overwriting: {len(plan)}')
    if not plan:
        print('nothing to do')
        return 0

    live = {}
    for e in plan:
        r = requests.get(f'{SB}/rest/v1/public_receipts', headers=H, timeout=30,
                         params={'select': 'id,result,pick_odds,game_date,'
                                           'pick_label',
                                 'id': f"eq.{e['id']}"})
        rows = r.json() or []
        if rows:
            live[e['id']] = rows[0]

    graded = [(e, live[e['id']]) for e in plan
              if e['id'] in live
              and str(live[e['id']].get('result')) in ('WIN', 'LOSS')]
    a = roi([(str(r['result']), e['stored_odds']) for e, r in graded])
    b = roi([(str(r['result']), e['recovered_odds']) for e, r in graded])
    print(f'  graded among them: {len(graded)}')
    print(f'    as stored   {a[0]}-{a[1]}  {a[2]:.2f}% vs BE {a[3]:.2f}%  '
          f'ROI {a[4]:+.2f}%')
    print(f'    real prices {b[0]}-{b[1]}  {b[2]:.2f}% vs BE {b[3]:.2f}%  '
          f'ROI {b[4]:+.2f}%')
    print()
    for e in sorted(plan, key=lambda x: str(x['game_date'])):
        cur = live.get(e['id'], {})
        drift = int(e['recovered_odds']) - int(e['stored_odds'])
        print(f"  {e['game_date']}  {str(cur.get('result') or '-'):5} "
              f"{int(e['stored_odds']):+5} -> {int(e['recovered_odds']):+5} "
              f"({drift:+5})  {e['source']}")
        print(f"      {str(e['pick_label'])[:62]}")
    if not APPLY:
        print('\n(dry run -- pass --apply to write)')
        return 0

    print()
    ok = refused = failed = 0
    for e in plan:
        want = int(e['recovered_odds'])
        pr = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                            timeout=30, params={'id': f"eq.{e['id']}"},
                            data=json.dumps({'pick_odds': want}))
        if pr.status_code not in (200, 204):
            print(f"  !! {e['id']} PATCH {pr.status_code}: {pr.text[:140]}")
            failed += 1
            continue
        rb = requests.get(f'{SB}/rest/v1/public_receipts', headers=H, timeout=30,
                          params={'select': 'pick_odds', 'id': f"eq.{e['id']}"})
        got = (rb.json() or [{}])[0].get('pick_odds')
        if got in (None, '') or int(got) != want:
            print(f"  REFUSED {e['id']} ({e['game_date']}): still "
                  f"{got!r}, wanted {want:+}. pick_odds is set-once and this "
                  f"row already had a value.")
            refused += 1
        else:
            ok += 1
    print(f'\n  overwritten and confirmed: {ok}')
    print(f'  silently refused by the freeze: {refused}')
    print(f'  hard failures: {failed}')
    if ok:
        after = []
        for e, r in graded:
            rb = requests.get(f'{SB}/rest/v1/public_receipts', headers=H,
                              timeout=30,
                              params={'select': 'pick_odds',
                                      'id': f"eq.{e['id']}"})
            v = (rb.json() or [{}])[0].get('pick_odds')
            after.append((str(r['result']), v))
        c = roi([x for x in after if x[1] not in (None, '')])
        print(f'\n  those rows now read {c[0]}-{c[1]}  {c[2]:.2f}% vs BE '
              f'{c[3]:.2f}%  ROI {c[4]:+.2f}%')
    print('\nRe-run _potd_price_manifest_1103.py --write, then '
          'compute_surface_records.py.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
