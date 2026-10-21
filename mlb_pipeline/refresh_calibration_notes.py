#!/usr/bin/env python3
"""Write each sport's calibration banner from its LIVE record, and pull it when
the sport is calibrated.

== 2026-10-20 · WHY ==
ANDY: "For NHL note its annoying having to update that everyday becaue i
walwyas have to tell you can we make ti where it updates uatomaticlaly or at
what point is it pulled and consider callibrated."

He is right that it should never have been a manual job. The NHL banner was
hand-written and had drifted into quoting a record that exists nowhere:

    banner said   139 picks (79-60), "ahead of break-even at 56.8%"
    published     67-51 (118) on game_read

and the "ahead of break-even" half was not just stale, it was WRONG in a way
that flatters us: 98% of NHL reads are moneylines, where breakeven moves with
the price. At the prices we actually took, 57.0% returns -2.7% — break-even,
not an edge. A hand-maintained banner will always drift toward the flattering
version, because nobody re-reads it on a day the number got worse.

HOW A SPORT GRADUATES, so this has a defined end rather than living forever:
a banner is shown while a sport is still CALIBRATING, and pulled the moment it
clears both bars —

    picks_count >= CALIBRATION_MIN_N      enough graded picks to mean anything
    AND the sport has been live >= CALIBRATION_MIN_DAYS

The n floor is the standing n>=30 rule scaled to a season surface: 250 graded
reads is roughly a month of a daily sport and is where a hit rate's 95% band
tightens to about +/-6pp. Below that a tier genuinely is a lighter claim and
the banner says so; above it the sport is simply judged on its published
record like MLB and NFL, and the banner comes down on its own.

WHAT THE COPY SAYS is derived, never asserted by hand:
  * the read record and the last-7-day record, both from surface_records
  * whether the hit rate actually beats the PRICE, computed from receipts,
    because on a moneyline sport a hit rate alone cannot say that
  * the curated surfaces' records, so the banner cannot brag while the
    Steam Room slate is underwater
  * every figure is one a user can find elsewhere in the app

USAGE
    python refresh_calibration_notes.py --dry-run
    python refresh_calibration_notes.py
    python refresh_calibration_notes.py --sport NHL
"""
from __future__ import annotations
import argparse
import datetime as dt
import math
import os
import sys

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV = os.path.join(_HERE, '.env')
for _l in (open(_ENV, encoding='utf-8') if os.path.exists(_ENV) else []):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=representation'}

#: A sport stops being "calibrating" at both of these, not either.
CALIBRATION_MIN_N = 250
CALIBRATION_MIN_DAYS = 45
#: Sports whose banner this script owns. A sport absent here keeps whatever
#: copy an operator wrote by hand - this never silently takes one over.
# NBA is deliberately ABSENT: its banner is operator-owned right now (the
# "per-game predictions are in review" message, B87) and auto-generating over
# it would delete the one thing NBA users actually need to know. Add 'NBA'
# back the moment that banner is cleared with the build.
OWNED = ('NHL', 'NCAAB', 'UFC')
#: The surface a user reads as "the model's record" per sport.
READ_SURFACE = 'game_read'
CURATED = ('sharp_card', 'sweat_card')


def page(tbl, params, order=None):
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        if order:
            p['order'] = order
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, timeout=180, params=p)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{tbl} -> {r.status_code}: {(r.text or "")[:160]}')
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def dec(american):
    a = f(american)
    if a is None or a == 0:
        return None
    return 1 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def rec_for(records, sport, surface, window):
    for r in records:
        if (r.get('sport') == sport and r.get('surface') == surface
                and r.get('window_key') == window):
            return r
    return None


def wl(r):
    if not r:
        return None
    w, l = int(r.get('wins') or 0), int(r.get('losses') or 0)
    return (w, l) if (w + l) else None


def price_truth(sport):
    """(hit%, implied%, roi%) for this sport's reads, from real prices.

    A hit rate cannot say whether we are ahead on a moneyline sport, so the
    banner needs the price-aware number or it will claim an edge it does not
    have.
    """
    rows = page('public_receipts',
                {'select': 'result,pick_odds,market',
                 'surface': f'eq.{READ_SURFACE}', 'sport': f'eq.{sport}'},
                'game_date.desc')
    g = [x for x in rows
         if str(x.get('result') or '').upper() in ('WIN', 'LOSS')
         and dec(x.get('pick_odds'))]
    if len(g) < 30:
        return None
    w = sum(1 for x in g if str(x['result']).upper() == 'WIN')
    n = len(g)
    roi = sum(((dec(x['pick_odds']) - 1) if str(x['result']).upper() == 'WIN'
               else -1) for x in g) / n * 100
    implied = sum(100.0 / dec(x['pick_odds']) for x in g) / n
    return (100.0 * w / n, implied, roi, n)


def abstention_clause(sport):
    """If the next card/slate has reads but NO play, say so.

    ══ 2026-10-20 · B83 · "THE UFC ENGINE HAS STOPPED MAKING PICKS" ══
    The premise turned out to be wrong, and the measurement is worth keeping
    because it reverses the obvious fix.

    WHAT IS ACTUALLY HAPPENING. On the 10-24 card, 7 of 8 fights are genuine
    coin flips to the model (p_winner_a 0.47-0.55, ev_tier SKIP), so passing on
    them is correct. The eighth is not: Volkanovski/Evloev came back
    p_a=0.3192, conviction 36, tier_winner STRONG, ev_tier PRIME - and the
    synthesis layer turned it into a PASS. Across every card, 6 of 19 PRIME
    model picks produced no published play at all.

    THAT LOOKS EXACTLY LIKE project_jerry_override_costs_the_edge_1007, AND IT
    IS NOT. Grading all 95 gradeable picks at the real median decimal price:

        ALL                51-44   53.7%   ROI  -4.7%
        ev_tier PRIME       7-10   41.2%   ROI -37.5%   <- the override SAVED
        ev_tier STRONG      8-2    80.0%   ROI  +8.6%
        tier_winner PRIME  19-10   65.5%   ROI -13.5%   <- wins often, loses
                                                           money: favourite
                                                           picking at bad prices

    So forcing PRIME picks through would have LOST money, and the abstention is
    the engine behaving correctly on a tier that does not beat its price. n=17
    on PRIME is far too small to call it proven - the honest statement is that
    there is no evidence PRIME wins and the point estimate is -37.5%.

    THE REAL DEFECT IS USER-FACING, and it is this: a fight-week card shows
    ZERO plays while the banner above it reports 61.1%. That reads as broken.
    So when the next card has reads and no play, the note says so. Stating a
    measured fact, not a product decision - whether UFC should publish at all
    is Andy's call and is recorded in B83.

    THREE WIRING ERRORS WERE HIT GETTING THAT TABLE, all caught by the
    0-rows-is-a-bug rule rather than shipped as findings:
      * ufc_fight_results was capped at 1000 rows, so picks and results shared
        ZERO dates (the documented PostgREST truncation trap)
      * `winner` holds a SIDE CODE 'a'/'b', not a name, so a name comparison
        matched nothing and reported 0-95 at 0.0%
      * the results row's fighter_a is not necessarily the picks row's
        fighter_a, so the side code has to be resolved to a name on its OWN
        row first
    """
    try:
        today = dt.date.today().isoformat()
        reads = page('jerry_reads',
                     {'select': 'game_date,call_market',
                      'sport': f'eq.{sport}',
                      'game_date': f'gte.{today}'}, 'game_date.asc')
        if not reads:
            return ''
        nxt = min(str(r['game_date'])[:10] for r in reads)
        card = [r for r in reads if str(r['game_date'])[:10] == nxt]
        plays = [r for r in card
                 if str(r.get('call_market') or '').lower() not in ('pass', '')]
        if plays or not card:
            return ''
        unit = 'fights' if sport == 'UFC' else 'games'
        return (f' No play on the {nxt} card: the model rates all '
                f'{len(card)} {unit} too close to price, and its most '
                f'confident tier has not beaten the prices we would have '
                f'paid. A pass is a position.')
    except Exception:                                          # noqa: BLE001
        return ''


def build_note(sport, records, season_start):
    read_life = wl(rec_for(records, sport, READ_SURFACE, 'lifetime'))
    read_d7 = wl(rec_for(records, sport, READ_SURFACE, 'd7'))
    life_row = rec_for(records, sport, READ_SURFACE, 'lifetime')
    n = int((life_row or {}).get('picks_count') or 0)

    days_live = None
    if season_start:
        try:
            days_live = (dt.date.today() - dt.date.fromisoformat(str(season_start)[:10])).days
        except Exception:                                      # noqa: BLE001
            days_live = None

    # ── graduation ──────────────────────────────────────────────────────
    if n >= CALIBRATION_MIN_N and (days_live or 0) >= CALIBRATION_MIN_DAYS:
        return None, (f'calibrated — {n} graded reads over {days_live} days '
                      f'clears {CALIBRATION_MIN_N}/{CALIBRATION_MIN_DAYS}')

    if not read_life:
        return (f'{sport} is live and the model is still calibrating. No graded '
                f'reads yet, so treat anything here as an early-stage read.'), \
               'no graded reads yet'

    w, l = read_life
    hit = 100.0 * w / (w + l)
    bits = [f'{sport} reads are {w}-{l} ({hit:.1f}%) across {w + l} graded picks']
    if read_d7:
        bits.append(f'and {read_d7[0]}-{read_d7[1]} over the last week')
    head = ', '.join(bits) + '.'

    # the price-aware sentence - the one a hit rate cannot replace
    pt = price_truth(sport)
    mid = ''
    if pt:
        hr, imp, roi, pn = pt
        if roi > 2:
            mid = (f' At the prices we took, that is {roi:+.1f}% on the '
                   f'{pn} priced picks — genuinely ahead, not just above 50%.')
        elif roi < -2:
            mid = (f' Read that carefully: at the prices we took it returns '
                   f'{roi:+.1f}% over {pn} priced picks, because a '
                   f'{hr:.0f}% hit rate needs about {imp:.0f}% to break even '
                   f'at those odds.')
        else:
            mid = (f' At the prices we took that is {roi:+.1f}% over {pn} '
                   f'priced picks — roughly break-even rather than an edge.')

    # never brag while a curated surface is underwater
    cur = []
    for s in CURATED:
        c = wl(rec_for(records, sport, s, 'lifetime'))
        if c and (c[0] + c[1]) >= 10:
            label = 'the Steam Room slate' if s == 'sharp_card' else 'the Sweat Card'
            cur.append(f'{label} at {c[0]}-{c[1]}')
    tail = (' The curated surfaces sit behind that, with '
            + ' and '.join(cur) + '.') if cur else ''

    close = (f' {sport} is still inside its calibration window ({n} of '
             f'{CALIBRATION_MIN_N} graded reads), so treat a {sport} tier as a '
             f'lighter claim than the same tier in MLB or NFL.')
    # B83: a card with reads and no play must say so, or the tab reads broken.
    abst = abstention_clause(sport)
    return (head + mid + tail + close + abst), \
        f'calibrating ({n}/{CALIBRATION_MIN_N})'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', action='append')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    records = page('surface_records', {'select': '*'})
    reg = page('sport_registry', {'select': '*'})
    targets = [s for s in (a.sport or OWNED)]
    for sport in targets:
        row = next((r for r in reg if r.get('sport') == sport), None)
        if not row:
            print(f'{sport}: no sport_registry row — skipping')
            continue
        if sport not in OWNED and not a.sport:
            continue
        # A calibration banner is a statement about LIVE play. On a preseason
        # or off-season sport it would replace a correct "season starts Nov 3"
        # note with "NCAAB is live", which is simply false - caught in the
        # first dry run.
        state = str(row.get('state') or '').lower()
        if state != 'in_season':
            print(f'{sport}: state={state!r} — not in season, leaving its '
                  f'banner alone')
            continue
        note, why = build_note(sport, records, row.get('season_start'))
        cur = row.get('state_message')
        print(f'\n=== {sport} === ({why})')
        print(f'  current: {str(cur)[:120] or "(none)"}')
        print(f'  new    : {str(note)[:300] or "(PULL — clearing the banner)"}')
        if note == cur:
            print('  unchanged')
            continue
        if a.dry_run:
            print('  (dry run)')
            continue
        r = requests.patch(f'{SB}/rest/v1/sport_registry', headers=HW,
                           params={'sport': f'eq.{sport}'},
                           json={'state_message': note}, timeout=30)
        ok = r.status_code in (200, 201)
        print(f'  patch {r.status_code}' + ('' if ok else f' {r.text[:120]}'))
        if ok:
            back = r.json()[0].get('state_message')
            print('  verified: ' + ('cleared' if back is None
                                    else f'{str(back)[:90]}...'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
