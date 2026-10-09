"""What did the CARDS actually return? Plays only, not game reads.

THE DISTINCTION THIS EXISTS TO ENFORCE (2026-10-09)
---------------------------------------------------
I spent a day measuring engine accuracy off `jerry_reads` by tier and treating
the result as exposure. It is not. NCAAF receipts show the cards only ever
carry STRONG and PRIME:

    sharp_card   STRONG 26, PRIME 16
    sweat_card   STRONG 24, PRIME  6
    game_read    259 (no tier -- per-game analysis, never a play)

So LEAN / COVERAGE / PASS performance is DISPLAY QUALITY, not money, and a
-8.66u "loss" attributed to NCAAF LEAN was exposure nobody ever had. A gate
built on that would have removed picks no one could bet while leaving the ones
they could.

`public_receipts` is the right source: it IS the published-play ledger, it is
identity-frozen, and it carries the price the play was published at. Anything
measured anywhere else is a proxy.

A NOTE ON PRICE COVERAGE, printed first on purpose. A missing price defaults
to -110 and that default has twice produced a confident wrong answer in this
codebase ("NCAAF ML is the winner", "NFL ML is the winner" -- both vanished at
real prices). Read the coverage line before trusting any ROI below it.


Correcting my own analysis. I measured jerry_reads by tier and treated the
result as exposure. But NCAAF receipts show the cards only ever carry STRONG
and PRIME:

    sharp_card   STRONG 26, PRIME 16
    sweat_card   STRONG 24, PRIME  6
    game_read    259 (no tier — per-game analysis, never a play)

So LEAN / COVERAGE / PASS performance is display quality, not money. The
money-relevant population is the card surfaces. public_receipts is the right
source because it IS the published-play ledger and it carries the price.
"""
import argparse
import collections
import sys

import requests

sys.path.insert(0, 'C:/Users/gomez/SweatShop/mlb_pipeline')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import market_line as ML

SB, H = ML.SB, ML.H
BREAKEVEN = 52.38
PLAY_SURFACES = ('sharp_card', 'sweat_card', 'potd', 'dawg', 'ladder')


def page(t, p, cap=60000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def units(won, odds):
    o = odds if odds is not None else -110
    if not won:
        return -1.0
    return (o / 100.0) if o > 0 else (100.0 / abs(o))


_ap = argparse.ArgumentParser()
_ap.add_argument('--since', default='2026-09-19',
                 help='default is when price capture came online; earlier '
                      'windows have no prices and fake the units')
_ap.add_argument('--sport', default=None)
_args = _ap.parse_args()

rc = page('public_receipts',
          {'select': 'sport,surface,tier,result,pick_odds,game_date,'
                     'pick_label,market',
           'game_date': f'gte.{_args.since}'})
rc = [x for x in rc
      if str(x.get('result') or '').upper() in ('WIN', 'LOSS', 'W', 'L')]
if _args.sport:
    rc = [x for x in rc if str(x['sport']).upper() == _args.sport.upper()]
print(f'{len(rc)} graded receipts since {_args.since}')

print()
print('PRICE COVERAGE on the play surfaces (a missing price fakes the units):')
for sp in ('NFL', 'NCAAF', 'MLB', 'NHL'):
    sub = [x for x in rc if str(x['sport']) == sp
           and str(x['surface']) in PLAY_SURFACES]
    if not sub:
        continue
    hp = sum(1 for x in sub if x.get('pick_odds') is not None)
    print(f'   {sp:<6}{len(sub):>4} plays · priced {hp:>4} '
          f'({hp / len(sub) * 100:3.0f}%)')


def tally(rows, label):
    w = l = 0
    u = 0.0
    for x in rows:
        won = str(x['result']).upper() in ('WIN', 'W')
        w += won
        l += not won
        u += units(won, x.get('pick_odds'))
    n = w + l
    if not n:
        return
    pct = w / n * 100
    mark = '  >' if pct >= BREAKEVEN else '  <'
    print(f'    {label:<34}{w:>4}-{l:<4}n={n:<4}{pct:5.1f}%{mark}'
          f'{u:>+8.2f}u  ROI {u / n * 100:+6.1f}%')


for sp in sorted({str(x['sport']) for x in rc}):
    plays = [x for x in rc if str(x['sport']) == sp
             and str(x['surface']) in PLAY_SURFACES]
    if not plays:
        continue
    print()
    print(f'=== {sp} CARD PLAYS — {len(plays)} graded')
    tally(plays, 'ALL play surfaces')
    print('    by surface:')
    for s in sorted({str(x['surface']) for x in plays}):
        tally([x for x in plays if str(x['surface']) == s], f'  {s}')
    print('    by tier:')
    for t in sorted({str(x.get('tier') or '-') for x in plays}):
        tally([x for x in plays if str(x.get('tier') or '-') == t], f'  {t}')
