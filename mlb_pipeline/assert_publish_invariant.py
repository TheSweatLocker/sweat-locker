#!/usr/bin/env python3
"""ONE INVARIANT: the side we DISPLAY, the side we READ, the side the CARD
shows and the side we GRADE must all be the same bet.

══ WHY THIS EXISTS (2026-10-25) ════════════════════════════════════════════
Andy: "why cant we get on same page with records and resolvers it always
something every day". He is right, and every incident on 2026-10-25 was the
same shape -- two surfaces disagreed and nothing checked:

  * the flip wrote ctx while the receipt kept the pre-flip side, so 17
    receipts graded the opposite of what shipped (B171/B188). The Sweat Card
    booked 1-4 on a card that went 5-1
  * the recap preferred one card key while the writer filled another, so five
    graded NCAAF plays vanished and it reported 1-1
  * the POTD lock tested the ENGINE's anchor instead of "is there a decision
    here", so a scheduled run deleted a published Play of the Day
  * preserve() compared against a snapshot read hours earlier, so three picks
    reverted and the lock then froze the reverted value
  * a badge said DEN -4.5 while the POTD and the read both said ARI +5

Each of those was found by hand, after the fact, usually by Andy. Each fix was
correct and none of them stopped the next one, because the defects are not
related by cause -- they are related by the ABSENCE OF A CHECK.

So this asserts the one thing that must be true, on every sport, every day:

    ctx.primary_play  ==  jerry_reads.call_*  ==  card leg  ==  public_receipts

on SIDE and MARKET. It is read-only. It never repairs anything, because a
repair that runs automatically is how a wrong side becomes permanent -- it
reports, loudly, and exits non-zero so a workflow step goes red.

WHAT IS AND IS NOT A FAILURE
  SIDE or MARKET disagreement  -> CRITICAL. These are different bets. A user
                                  sees a contradiction and the record grades
                                  something we did not publish.
  LINE differs, side matches   -> WARN. The market moves and the pick lock
                                  deliberately allows a number to change on
                                  the same side (20261007a). Reported so drift
                                  is visible, not failed.
  a surface has no row         -> INFO. Not every game reaches every surface.
  receipt result = 'void'      -> SKIPPED. A void is a retired receipt; it is
                                  the remedy, not the defect.
  side present on one side
    of a comparison only       -> UNVERIFIABLE, counted separately. Calling
                                  that a mismatch is how an earlier audit of
                                  mine produced 22 false positives off rows
                                  whose pick_side was simply NULL.

Usage:
    python assert_publish_invariant.py                      # all sports, today
    python assert_publish_invariant.py --sport NFL --date 2026-10-25
    python assert_publish_invariant.py --warn-only          # never exit non-zero
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV = os.path.join(_HERE, '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV, encoding='utf-8'):
        if '=' in _ln and not _ln.strip().startswith('#'):
            _k, _v = _ln.strip().split('=', 1)
            os.environ.setdefault(_k, _v)

SB = os.environ['SUPABASE_URL']
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
       'NBA': 'nba_game_context', 'NHL': 'nhl_game_context',
       'MLB': 'mlb_game_context', 'NCAAB': 'ncaab_game_context'}
CARD_KEY = 'sweat_card_%s'
# Surfaces that are supposed to state THE SAME BET as the badge.
#
# `fade` is deliberately EXCLUDED. The Fade's whole thesis is to take the side
# against public money, so it is an independent opinion and is *expected* to
# disagree with the badge -- on the first run of this check it produced 5 of
# the 16 "critical" rows purely by doing its job (badge 'Minnesota Wild ML'
# vs fade 'Under 6.5'). A watchdog that cries wolf gets muted, and a muted
# watchdog is worse than none.
#
# `ledger` is excluded for the same reason: it is a composite of legs, not a
# restatement of one game's pick.
GAME_SURFACES = ('game_read', 'sweat_card', 'sharp_card')
VOIDISH = {'void', 'Void', 'VOID'}


def get(table: str, params: dict) -> list:
    """Paged read. A non-200 is NAMED, never swallowed (B153)."""
    out, off = [], 0
    while off < 10000:
        p = dict(params)
        p['limit'] = '1000'
        p['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=60,
                         params=p)
        if r.status_code != 200:
            print(f'  ! HTTP {r.status_code} {r.url} :: {r.text[:200]}')
            return out
        rows = r.json() or []
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return out


def jload(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_LBL = re.compile(r'^(.*?)\s*([+-]\d+(?:\.\d+)?)\s*$')


def parse_label(label: str):
    """(team, line, market) by SHAPE, never by vocabulary.

    feedback_parse_a_pick_by_shape_not_vocabulary: a POTD reading "Toronto
    Raptors -8.5" matched no market WORD and was stored as `no-pick`.
    """
    s = str(label or '').strip()
    if not s or s.lower() in ('none', 'no-pick', 'pass', 'no play'):
        return None, None, None
    up = s.upper()
    if up.endswith(' ML'):
        return s[:-3].strip(), None, 'ml'
    if up.startswith('OVER ') or up.startswith('UNDER '):
        parts = s.split()
        return parts[0].upper(), f(parts[-1]), 'total'
    m = _LBL.match(s)
    if m:
        return m.group(1).strip(), f(m.group(2)), 'rl'
    return s, None, None


def norm_side(v):
    v = str(v or '').strip().upper()
    return v if v in ('HOME', 'AWAY', 'OVER', 'UNDER') else None


def norm_mkt(v):
    v = str(v or '').strip().lower()
    if v in ('spread', 'rl', 'runline', 'puckline'):
        return 'rl'
    if v in ('ml', 'moneyline', 'h2h'):
        return 'ml'
    if v in ('total', 'totals', 'ou'):
        return 'total'
    return v or None


def side_from_team(team, home, away):
    t = str(team or '').strip().lower()
    if not t:
        return None
    if t == str(home).strip().lower():
        return 'HOME'
    if t == str(away).strip().lower():
        return 'AWAY'
    # abbreviation vs full name, either direction
    for cand, side in ((home, 'HOME'), (away, 'AWAY')):
        c = str(cand).strip().lower()
        if c and (c in t or t in c):
            return side
    return None


class Claim:
    """One surface's statement about which bet this is."""

    def __init__(self, surface, side, market, line, label, extra=''):
        self.surface = surface
        self.side = norm_side(side)
        self.market = norm_mkt(market)
        self.line = f(line)
        self.label = label
        self.extra = extra

    def __repr__(self):
        return (f'{self.surface}={self.label!r} '
                f'({self.side}/{self.market}/{self.line})')


def check_sport(sport: str, date: str) -> dict:
    tbl = CTX.get(sport)
    if not tbl:
        return {}
    ctx = get(tbl, {'select': '*', 'game_date': f'eq.{date}'})
    if not ctx:
        print(f'  {sport}: no context rows for {date}')
        return {'games': 0, 'critical': [], 'warn': [], 'unverifiable': []}

    reads = {}
    for r in get('jerry_reads', {'select': '*', 'sport': f'eq.{sport}',
                                 'game_date': f'eq.{date}'}):
        reads[str(r.get('game_id'))] = r

    receipts = {}
    for r in get('public_receipts', {'select': '*', 'sport': f'eq.{sport}',
                                     'game_date': f'eq.{date}'}):
        if str(r.get('result') or '') in VOIDISH:
            continue
        if str(r.get('surface')) not in GAME_SURFACES:
            continue
        receipts.setdefault(str(r.get('game_id')), []).append(r)

    card_legs = {}
    crow = get('jerry_cache', {'select': 'data',
                               'cache_key': f'eq.{CARD_KEY % date}'})
    cdata = jload((crow or [{}])[0].get('data')) if crow else {}
    for src in ('unified_top_picks', 'football_picks', 'top_8'):
        for e in (cdata.get(src) or []):
            if isinstance(e, dict) and e.get('game_id'):
                card_legs.setdefault(str(e['game_id']), (src, e))

    critical, warn, unver = [], [], []
    for g in ctx:
        gid = str(g.get('game_id'))
        home, away = g.get('home_team'), g.get('away_team')
        mu = f'{away}@{home}'
        pp = jload(g.get('primary_play'))
        if not pp or not pp.get('label'):
            continue

        claims = [Claim('badge', pp.get('side'), pp.get('type'),
                        pp.get('line'), pp.get('label'))]

        rd = reads.get(gid)
        if rd and rd.get('call_text'):
            s = norm_side(rd.get('call_side'))
            if s is None:
                team, ln, mk = parse_label(rd.get('call_text'))
                s = side_from_team(team, home, away) or norm_side(team)
                claims.append(Claim('read', s, rd.get('call_market') or mk,
                                    rd.get('call_line') or ln,
                                    rd.get('call_text')))
            else:
                claims.append(Claim('read', s, rd.get('call_market'),
                                    rd.get('call_line'), rd.get('call_text')))

        if gid in card_legs:
            src, leg = card_legs[gid]
            s = norm_side(leg.get('side'))
            if s is None:
                team, ln, mk = parse_label(leg.get('label'))
                s = side_from_team(team, home, away) or norm_side(team)
            claims.append(Claim(f'card:{src}', s,
                                leg.get('type') or leg.get('market'),
                                leg.get('line'), leg.get('label')))

        for rc in receipts.get(gid, []):
            s = norm_side(rc.get('pick_side'))
            if s is None:
                team, ln, mk = parse_label(rc.get('pick_label'))
                s = side_from_team(team, home, away) or norm_side(team)
            claims.append(Claim(f"receipt:{rc.get('surface')}", s,
                                rc.get('market'), rc.get('pick_line'),
                                rc.get('pick_label'),
                                extra=f"id={rc.get('id')}"))

        known = [c for c in claims if c.side and c.market]
        if len(known) < 2:
            if len(claims) > 1:
                unver.append((sport, mu, claims))
            continue

        # ══ MAJORITY, not "whatever the badge says" ══
        # The first pass always named the badge as the authority and reported
        # "badge says X but Y says Z". On IND@MIN the badge was the ONE wrong
        # surface -- read, card and both receipts all said IND +7 -- so it
        # printed four rows blaming the three correct surfaces. Pick the
        # modal (side, market) and name the DISSENTERS, which is also what
        # tells you where to go and fix it.
        from collections import Counter
        votes = Counter((c.side, c.market) for c in known)
        winner, nwin = votes.most_common(1)[0]
        tie = len([1 for _k, v in votes.items() if v == nwin]) > 1
        agree = [c for c in known if (c.side, c.market) == winner]
        dissent = [c for c in known if (c.side, c.market) != winner]

        for c in dissent:
            # A SIDE disagreement is a different team. A MARKET-only
            # disagreement is the same team on a different bet -- still a
            # record that grades something we did not display, and the known
            # population of B144, but a materially different defect.
            kind = 'SIDE' if c.side != winner[0] else 'MARKET'
            critical.append({'sport': sport, 'mu': mu, 'kind': kind,
                             'winner': winner, 'agree': agree, 'odd': c,
                             'tie': tie})
        for c in agree:
            if (c is not agree[0] and c.line is not None
                    and agree[0].line is not None
                    and abs(agree[0].line - c.line) > 0.001):
                warn.append((sport, mu, agree[0], c))

    return {'games': len(ctx), 'critical': critical, 'warn': warn,
            'unverifiable': unver}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(CTX))
    ap.add_argument('--date')
    ap.add_argument('--warn-only', action='store_true')
    # Default to failing on SIDE only, deliberately.
    #
    # MARKET-only mismatches are the KNOWN, pre-existing B144 population (237
    # of 1,754 game_read receipts publish a different market than the read).
    # Failing on those would put every pipeline permanently red on day one,
    # and a permanently red step is a muted step -- which is the exact
    # mechanism that let `continue-on-error: true` hide a dead depth-chart
    # scrape for 39 days. So SIDE fails the build, MARKET shouts, and the
    # bar tightens to `--fail-on any` once B144 is cleared.
    ap.add_argument('--fail-on', choices=['side', 'any'], default='side')
    a = ap.parse_args()
    date = a.date or (dt.datetime.now(dt.timezone.utc)
                      - dt.timedelta(hours=4)).date().isoformat()
    sports = [a.sport] if a.sport else sorted(CTX)

    print(f'=== publish invariant · {date} · '
          f'{"/".join(sports)} ===')
    print('    badge == read == card == receipt, on SIDE and MARKET')
    print('')
    tot_c = tot_w = tot_u = tot_g = 0
    _crit_by_sport = {}
    for sp in sports:
        res = check_sport(sp, date)
        if not res:
            continue
        c, w, u = res['critical'], res['warn'], res['unverifiable']
        _crit_by_sport[sp] = c
        tot_c += len(c)
        tot_w += len(w)
        tot_u += len(u)
        tot_g += res['games']
        nside = len([x for x in c if x['kind'] == 'SIDE'])
        nmkt = len(c) - nside
        status = 'OK' if not c else (f'{nside} SIDE'
                                     + (f' / {nmkt} MARKET' if nmkt else ''))
        print(f'  {sp:<6} games={res["games"]:<4} {status}'
              f'{f"  line-drift={len(w)}" if w else ""}'
              f'{f"  unverifiable={len(u)}" if u else ""}')
        for x in sorted(c, key=lambda z: (z['kind'] != 'SIDE', z['mu'])):
            od, ag = x['odd'], x['agree']
            mark = '✖' if x['kind'] == 'SIDE' else '⚠'
            agreed = ', '.join(sorted(s.surface for s in ag))
            print(f"     {mark} {x['mu']:<12} [{x['kind']}] "
                  f"{len(ag)} surface(s) say {ag[0].label!r} "
                  f"({x['winner'][0]}/{x['winner'][1]}) [{agreed}]  "
                  f"BUT {od.surface} says {od.label!r} "
                  f"({od.side}/{od.market}) {od.extra}"
                  + ('   TIE - no majority' if x['tie'] else ''))
        for sport, mu, base, other in w:
            print(f'     ~ {mu:<12} same bet, line drift: '
                  f'{base.surface} {base.line:+g} vs '
                  f'{other.surface} {other.line:+g} ({other.label!r})')

    print('')
    print(f'  games checked {tot_g}   CRITICAL {tot_c}   '
          f'line-drift {tot_w}   unverifiable {tot_u}')
    tot_side = sum(1 for sp in sports for x in (_crit_by_sport.get(sp) or [])
                   if x['kind'] == 'SIDE')
    if tot_c:
        print('')
        print('  A CRITICAL row means a user can see one side while the record')
        print('  grades another. Fix the SOURCE (B171) -- do not patch a')
        print('  receipt: pick_side/pick_line are immutable, so a wrong-side')
        print('  receipt can only ever be voided (B181).')
        if a.warn_only:
            print('  --warn-only: exiting 0 anyway.')
            return 0
        if a.fail_on == 'side' and not tot_side:
            print(f'  MARKET-only mismatches ({tot_c}); these are the known '
                  f'B144 population, so exiting 0 under --fail-on side. '
                  f'Tighten to --fail-on any once B144 is cleared.')
            return 0
        return 1
    print('  invariant holds.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
