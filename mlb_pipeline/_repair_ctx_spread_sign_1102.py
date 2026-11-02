"""Repair context close_spread rows written with the wrong SIGN.

== 2026-11-02 - WHAT I BROKE AND HOW IT WAS CAUGHT ========================
fill_ctx_close_from_line_history.py stored the spread as:

    fill['close_spread'] = hl if hl < 0 else -hl        # i.e. -abs(hl)

which forces it NEGATIVE on every game. The comment beside it said
"home-perspective, negative when home is favoured" -- a description of the
CONVENTION, implemented as though the number were always negative. A home
DOG at +5.5 was therefore stored as -5.5: the right magnitude on the wrong
team (feedback_magnitude_cannot_express_a_side).

Nothing in that script's own output could show this. Every line it printed
looked like a plausible spread. What exposed it was asking a SECOND source
the same question -- the moneyline. On the 15 NBA rows for 11-02 that I had
just written, 3 said the home team was favoured by the spread and was the
DOG by the price:

    Toronto Raptors    @ LA Clippers        ctx -5.5  home ml +170
    Detroit Pistons    @ Charlotte Hornets  ctx -4.5  home ml +149
    Atlanta Hawks      @ Indiana Pacers     ctx -5.5  home ml +165

All three are home dogs. The 12 where the home side was favoured were
already correct, which is exactly why a magnitude bug survives a glance:
it is right on the majority case.

THE CONVENTION, measured rather than assumed, on games the pipeline had
already populated: ctx.close_spread == the HOME-SIDE line exactly as
line_history posts it -- NBA 45 exact of 89 against 2 for the negation (the
rest line movement), NHL 13 of 14. It agrees with the repo's own
_HOME_HANDICAP_SIGN['NBA'] = 1.0 in generate_the_fade.py and with the
independent corr(close_spread, home margin) = -0.517 on n=2,449 recorded in
the fill script's own header.

WHAT THIS REPAIRS. Only rows whose stored spread CONTRADICTS the stored
moneyline, and only where line_history still holds a home-side quote to
restore from. It rewrites the spread to the posted home-side line and
leaves every other column alone. Rows where the spread and the price agree
are not touched, because a disagreement is the only evidence of damage I
have -- rewriting agreeing rows would be editing values the pipeline wrote.

It checks the 10-28 run too: that script shipped with the same bug and was
applied to 5 stranded games, so the damage may not be limited to today.

  python _repair_ctx_spread_sign_1102.py
  python _repair_ctx_spread_sign_1102.py --apply
"""
from __future__ import annotations

import argparse
import collections
import os
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

for _l in Path(__file__).with_name('.env').read_text().split('\n'):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())
SB = os.environ['SUPABASE_URL'].rstrip('/')
K = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': 'Bearer ' + K}
H_W = dict(H, **{'Content-Type': 'application/json',
                 'Prefer': 'return=representation'})

SPORTS = {'NBA': 'nba_game_context', 'NHL': 'nhl_game_context'}
SINCE = '2026-10-21'


def fl(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def implied(o):
    """American odds -> implied probability."""
    o = fl(o)
    if o is None:
        return None
    return (100.0 / (o + 100.0)) if o > 0 else (abs(o) / (abs(o) + 100.0))


#: how clearly the price must name a favourite before its disagreement with
#: the spread counts as EVIDENCE. A -110/-110 game has no favourite, so a
#: row that "contradicts" it contradicts nothing -- repairing on that basis
#: would be guessing a side from a coin flip, which is the same error in the
#: other direction.
MIN_IMPLIED_GAP = 0.04


def ml_favourite(mh, ma):
    """'home' / 'away' / None when the price does not clearly say."""
    ih, ia = implied(mh), implied(ma)
    if ih is None or ia is None:
        return None
    if abs(ih - ia) < MIN_IMPLIED_GAP:
        return None
    return 'home' if ih > ia else 'away'


def get(table, **p):
    r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p, timeout=90)
    if r.status_code != 200:
        print(f'  ! {table} HTTP {r.status_code} {r.text[:200]}')
        return []
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    print(f'=== repair ctx close_spread sign · '
          f'{"APPLY" if a.apply else "DRY RUN"} ===\n')

    total_bad = 0
    for sport, tbl in SPORTS.items():
        rows = get(tbl, select='game_id,game_date,away_team,home_team,'
                                'close_spread,home_ml_close,away_ml_close',
                   game_date=f'gte.{SINCE}', order='game_date.asc')
        # the only evidence of damage: spread and price name different
        # favourites
        suspect, unclear = [], 0
        for c in rows:
            cs = fl(c.get('close_spread'))
            if cs is None:
                continue
            fav = ml_favourite(c.get('home_ml_close'), c.get('away_ml_close'))
            if fav is None:
                if (fl(c.get('home_ml_close')) is not None
                        and fl(c.get('away_ml_close')) is not None):
                    unclear += 1
                continue
            if (fav == 'home') != (cs < 0):
                suspect.append(c)
        print(f'{sport}: {len(rows)} rows since {SINCE}, '
              f'{len(suspect)} where the spread CONTRADICTS a CLEAR '
              f'moneyline favourite ({unclear} skipped as near-even, '
              f'no favourite to contradict)')
        if not suspect:
            continue

        plans = []
        for c in suspect:
            q = get('line_history', select='side,line,captured_at',
                    sport=f'eq.{sport}', game_id=f'eq.{c["game_id"]}',
                    market='eq.spread', order='captured_at.desc', limit='60')
            hq = [x for x in q if str(x.get('side') or '').lower() == 'home'
                  and fl(x.get('line')) is not None]
            if not hq:
                print(f'   {c["game_date"]} {c["away_team"]} @ '
                      f'{c["home_team"]}: no home-side quote in '
                      f'line_history — CANNOT restore, left alone')
                continue
            newest = max(str(x['captured_at']) for x in hq)
            batch = [fl(x['line']) for x in hq
                     if str(x['captured_at']) == newest]
            want = collections.Counter(batch).most_common(1)[0][0]
            cs = fl(c.get('close_spread'))
            mh, ma = fl(c.get('home_ml_close')), fl(c.get('away_ml_close'))
            # the restored value must itself agree with the price, or we are
            # swapping one incoherent row for another
            if (ml_favourite(mh, ma) == 'home') != (want < 0):
                print(f'   {c["game_date"]} {c["away_team"]} @ '
                      f'{c["home_team"]}: posted home line {want:+g} ALSO '
                      f'disagrees with ml {mh:g}/{ma:g} — not a sign bug, '
                      f'left for a human')
                continue
            print(f'   {c["game_date"]} {c["away_team"]:<24} @ '
                  f'{c["home_team"]:<24} {cs:+g} -> {want:+g}   '
                  f'(ml {mh:g}/{ma:g}, home '
                  f'{"fav" if ml_favourite(mh, ma) == "home" else "dog"})')
            plans.append((c['game_id'], want))
        total_bad += len(plans)

        if not a.apply:
            continue
        for gid, want in plans:
            r = requests.patch(f'{SB}/rest/v1/{tbl}', headers=H_W,
                               params={'game_id': f'eq.{gid}'},
                               json={'close_spread': want}, timeout=60)
            ok = r.status_code in (200, 204)
            back = get(tbl, select='close_spread', game_id=f'eq.{gid}')
            got = fl(back[0]['close_spread']) if back else None
            print(f'   {"✓" if ok and got == want else "✖"} {gid} '
                  f'HTTP {r.status_code}, readback {got}')

    print(f'\n  rows needing repair: {total_bad}')
    if not a.apply:
        print('  DRY RUN — nothing written. Re-run with --apply.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
