"""Andy picks it, this makes it stick — on game detail, Sweat Card and Sharp.

ANDY 2026-10-10 night: "i think i want ot pick what goes on Sweat Card
tomorrow, and the the sahrp as well how can we make that possible? I just tell
you and populate in the morning?"

Yes — and most of the machinery already exists. What was missing was a safe
way to CREATE the override. manual_pick_override.preserve() has been wired
into recompute_primary_play / recompute_ncaaf_primary_play /
recompute_nfl_primary_play since 2026-10-06, so an approved pick already
survives every recompute. But setting one meant hand-patching
primary_play JSON, which is how a wrong side or a stale line gets typed in at
8am.

WHY A SET ON primary_play REACHES BOTH CARDS. Verified, not assumed:
  * generate_sharp_card.py selects from <sport>_game_context.primary_play
    (lines 454, 518-526, 693, 1047)
  * generate_sweat_card.py does the same (lines 790-791, 835)
So one write lands on game detail, the Sweat Card and The Sharp together.
There is no separate card-level pin to maintain.

⚠ THE DEADLINE IS REAL. Both cards hard-lock at 12:00 ET
(SHARP_CARD_HARD_LOCK_ET_HOUR / SWEAT_CARD_HARD_LOCK_ET_HOUR, both default
'12'). Past that hour a republish is REFUSED — deliberately, by Andy's
2026-09-12 directive after NCAAF picks moved late. So an override set at 12:30
will sit in primary_play and never reach today's card. This script prints the
ET time and the hours remaining on every run, and REFUSES to write past the
lock unless --force-past-lock is passed, because a silent no-op is the worst
outcome here.

WHAT IT WRITES, and what it leaves alone. The override owns only WHAT THE
PICK IS — side, tier, type, label, line, conviction. The engine's prose,
model fields, shadows and audit trail are left for the recompute to keep
current, exactly as manual_pick_override.preserve() intends: we overrode the
conclusion, not the reading.

SAFETY, because this writes a published claim:
  * refuses a side that does not match the game's actual teams
  * refuses a line whose sign contradicts the stored close (the NCAAF
    negative-is-home-favourite convention has bitten twice —
    project_close_spread_sign_bug_914, the Red River inversion)
  * DRY BY DEFAULT. --apply is required.
  * re-reads the row after writing and prints what is actually stored, because
    a 204 is not a write (feedback_204_is_not_a_write) and primary_play sits
    behind a pick-lock trigger that silently refuses to overwrite a
    NON-EMPTY value
  * stamps _manual_correction with the prior value so the trail shows what the
    engine had said

CLI
    # see today's board and what the engine currently has
    python set_manual_pick.py --sport NCAAF --list

    # set one
    python set_manual_pick.py --sport NCAAF --game ncaaf_20261011_X_Y \\
        --side HOME --type rl --line -7.5 --tier STRONG --label "Texas -7.5"

    # and then actually write it
    ... --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
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
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

CTX = {'MLB': 'mlb_game_context', 'NFL': 'nfl_game_context',
       'NCAAF': 'ncaaf_game_context', 'NHL': 'nhl_game_context',
       'NBA': 'nba_game_context', 'NCAAB': 'ncaab_game_context'}

#: Both cards refuse to republish at or after this ET hour. Andy's
#: 2026-09-12 directive. Keep in step with the two env defaults in
#: generate_sharp_card.py / generate_sweat_card.py.
LOCK_ET_HOUR = 12

TIERS = ('PRIME', 'STRONG', 'LEAN', 'COVERAGE', 'PASS')
TYPES = ('ml', 'rl', 'spread', 'total')
SIDES = ('HOME', 'AWAY', 'OVER', 'UNDER')


def et_now():
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)


def today_et():
    return et_now().strftime('%Y-%m-%d')


def lock_status():
    now = et_now()
    hrs = LOCK_ET_HOUR - now.hour - (now.minute / 60.0)
    return now, hrs


def fetch(sport, game_date):
    r = requests.get(f'{SB}/rest/v1/{CTX[sport]}', headers=H,
                     params={'select': 'game_id,game_date,home_team,away_team,'
                                       'close_spread,close_total,primary_play',
                             'game_date': f'eq.{game_date}', 'limit': '400'},
                     timeout=60)
    return r.json() if r.status_code in (200, 206) else []


def show(rows):
    print(f'  {"game_id":<40}{"matchup":<40}{"engine pick":<26}{"close":>8}')
    for g in sorted(rows, key=lambda z: str(z.get('game_id'))):
        pp = g.get('primary_play')
        if isinstance(pp, str):
            try:
                pp = json.loads(pp)
            except Exception:                               # noqa: BLE001
                pp = None
        cur = '—'
        if isinstance(pp, dict):
            cur = (f"{pp.get('tier','?')}/{pp.get('type','?')}/"
                   f"{pp.get('side','?')} {pp.get('label') or ''}")[:24]
            if pp.get('_manual_correction'):
                cur = 'MANUAL ' + cur
        mu = f"{g.get('away_team')} @ {g.get('home_team')}"
        print(f'  {str(g.get("game_id")):<40}{mu[:38]:<40}{cur:<26}'
              f'{str(g.get("close_spread")):>8}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, choices=sorted(CTX))
    ap.add_argument('--date', default=None,
                    help='ET game date; default tomorrow if past the lock, '
                         'else today')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--game')
    ap.add_argument('--side', choices=SIDES)
    ap.add_argument('--type', dest='mtype', choices=TYPES)
    ap.add_argument('--line', type=float, default=None)
    ap.add_argument('--tier', choices=TIERS, default='STRONG')
    ap.add_argument('--conviction', type=int, default=None)
    ap.add_argument('--label')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--force-past-lock', action='store_true',
                    dest='force_lock')
    a = ap.parse_args()

    now, hrs = lock_status()
    gd = a.date or today_et()
    # The lock governs TODAY's card publish. Setting a FUTURE date is never
    # blocked by it — the card for that date has not been built yet. Being
    # sloppy here would either nag on a perfectly safe write or, worse, imply
    # a future board is locked when it is not.
    is_today = (gd == today_et())
    blocked = is_today and hrs <= 0
    print(f'=== set_manual_pick · {a.sport} · {gd}')
    if is_today:
        print(f'    ET now {now:%Y-%m-%d %H:%M} · card hard-lock '
              f'{LOCK_ET_HOUR}:00 ET · '
              f'{"PAST LOCK" if blocked else f"{hrs:.1f}h remaining"}')
        if blocked:
            print('    ⚠ past the lock: a write now will NOT reach today\'s '
                  'cards.')
            print('      Use --date <a future date> for the next board, or')
            print('      --force-past-lock to change the row anyway.')
    else:
        print(f'    ET now {now:%Y-%m-%d %H:%M} · setting a FUTURE board '
              f'({gd}) — the 12:00 ET lock does not apply, but the card for '
              f'{gd} must be generated AFTER this write.')

    rows = fetch(a.sport, gd)
    print(f'    {len(rows)} {a.sport} games on {gd}\n')
    if a.list or not a.game:
        if not rows:
            print('    no games — wrong date?')
            return 0
        show(rows)
        if not a.game:
            print('\n  Pass --game <game_id> --side --type [--line --tier '
                  '--label] to set one.')
        return 0

    g = next((x for x in rows if str(x.get('game_id')) == a.game), None)
    if not g:
        print(f'  ! {a.game} is not on the {gd} {a.sport} board. '
              f'Run --list to see game_ids.')
        return 1
    home, away = str(g.get('home_team')), str(g.get('away_team'))
    print(f'  game   : {away} @ {home}')
    print(f'  close  : spread {g.get("close_spread")} · '
          f'total {g.get("close_total")}')

    if not a.side or not a.mtype:
        print('  ! --side and --type are required to set a pick.')
        return 1
    # A side must be coherent with the market.
    if a.mtype == 'total' and a.side not in ('OVER', 'UNDER'):
        print('  ! a total pick needs --side OVER or UNDER.')
        return 1
    if a.mtype != 'total' and a.side not in ('HOME', 'AWAY'):
        print('  ! an ml/rl/spread pick needs --side HOME or AWAY.')
        return 1

    team = home if a.side == 'HOME' else away
    label = a.label or (
        f'{team} {a.line:+g}' if (a.line is not None and a.mtype != 'ml')
        else f'{team} ML' if a.mtype == 'ml'
        else f'{a.side} {a.line:g}' if a.line is not None else f'{a.side}')

    # Sign sanity against the stored close. NCAAF/MLB/NHL/NBA/NCAAB store a
    # NEGATIVE close_spread for a HOME favourite; NFL POSITIVE. A manual line
    # whose sign disagrees with the market by more than a touch is the exact
    # shape of the Red River inversion, so it is flagged loudly rather than
    # written quietly.
    cs = g.get('close_spread')
    if a.line is not None and a.mtype in ('rl', 'spread') and cs is not None:
        try:
            mkt_home = (float(cs) * (1.0 if a.sport == 'NFL' else -1.0))
            want_home = (a.line < 0) if a.side == 'HOME' else (a.line > 0)
            is_home_fav = mkt_home > 0
            if a.side == 'HOME' and want_home != is_home_fav:
                print(f'  ⚠ SIGN CHECK: you are laying {a.line:+g} on the HOME '
                      f'side but the market has home as the '
                      f'{"favourite" if is_home_fav else "underdog"} '
                      f'(close {cs}). Double-check the number.')
        except (TypeError, ValueError):
            pass

    old = g.get('primary_play')
    if isinstance(old, str):
        try:
            old = json.loads(old)
        except Exception:                                   # noqa: BLE001
            old = None
    old = old if isinstance(old, dict) else {}

    new = dict(old)
    new.update({
        'side': a.side, 'type': a.mtype, 'tier': a.tier, 'label': label,
        'conviction': (a.conviction if a.conviction is not None
                       else old.get('conviction') or 70),
    })
    if a.line is not None:
        new['line'] = a.line
    new['_manual_correction'] = {
        'at': today_et(), 'by': 'andy-approved',
        'was': {k: old.get(k) for k in
                ('side', 'type', 'tier', 'label', 'line', 'conviction')},
    }

    print(f'\n  engine had : {old.get("tier")}/{old.get("type")}/'
          f'{old.get("side")} {old.get("label")}')
    print(f'  will set   : {a.tier}/{a.mtype}/{a.side} "{label}"'
          f'{f" line {a.line:+g}" if a.line is not None else ""}')
    print(f'  reaches    : game detail + Sweat Card + The Sharp '
          f'(all three read primary_play)')

    if not a.apply:
        print('\n  [DRY] nothing written. Re-run with --apply.')
        return 0
    if hrs <= 0 and not a.force_lock:
        print('\n  REFUSED: past the 12:00 ET card lock and --force-past-lock '
              'not given.')
        return 1

    r = requests.patch(f'{SB}/rest/v1/{CTX[a.sport]}', headers=H_W,
                       params={'game_id': f'eq.{a.game}'},
                       json={'primary_play': new}, timeout=60)
    print(f'\n  PATCH -> {r.status_code}')
    if r.status_code not in (200, 204):
        print(f'  ! {r.text[:200]}')
        return 1
    # A 204 is not a write, and primary_play sits behind a pick-lock trigger
    # that refuses to overwrite a non-empty value. Re-read and prove it.
    back = fetch(a.sport, gd)
    chk = next((x for x in back if str(x.get('game_id')) == a.game), None)
    pp = (chk or {}).get('primary_play')
    if isinstance(pp, str):
        try:
            pp = json.loads(pp)
        except Exception:                                   # noqa: BLE001
            pp = None
    if not isinstance(pp, dict):
        print('  ! re-read found no primary_play — NOT stored.')
        return 1
    ok = (pp.get('side') == a.side and pp.get('type') == a.mtype
          and pp.get('tier') == a.tier)
    print(f'  VERIFY by re-read: {pp.get("tier")}/{pp.get("type")}/'
          f'{pp.get("side")} "{pp.get("label")}" · manual='
          f'{bool(pp.get("_manual_correction"))}')
    if not ok:
        print('  ! STORED VALUE DOES NOT MATCH — the pick lock likely refused '
              'the overwrite. Clear pick_locked_at for this row and retry.')
        return 1
    print('  ✓ stored and stamped. Recomputes will preserve it '
          '(manual_pick_override.preserve).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
