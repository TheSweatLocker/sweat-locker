"""Keep the in-calibration disclaimer TRUE by computing it, not typing it.

WHY (2026-10-08)
----------------
Andy, looking at the NHL banner: "note that says model is still claibrqtin
our sample is 21 gaems is that accurate, does thta number update, is that
note dhncgabela via bakcend".

Three questions, and the answers were: no, no, and yes.

The banner lives in `sport_registry.state_message`, which IS backend-editable
(no Apple build). But the "21 games" in it was typed into a string literal in
`_nhl_calibration_note.py` — a gitignored scratch script, in no workflow, run
once by hand. Nothing recomputed it, so it froze on the day it was written.

Measured on 2026-10-08 against `jerry_reads` (sport NHL, regular season from
the registry's own `season_start` of 2026-09-29):

    the banner said   21 graded games
    actually graded   55  (28-27, 50.9%) across 9 dates 09-29..10-07

So the number users read was understating our real sample by 2.6x, and the
claim "too small to separate variance from signal" was being argued from a
figure that was wrong in the direction that made us look less tested than we
are. A disclaimer that is itself inaccurate is not caution, it is just a
second wrong number on the screen.

This is the same shape as every other defect we chased this week: a value
computed once at write time, copied into a surface, and never re-derived when
its inputs changed. The fix is the same shape too — derive it on a schedule.

WHAT IT DOES
Recomputes the sentence from the measured graded sample every run and PATCHes
`sport_registry.state_message`. Sport-generic, because NBA and NCAAB will each
need exactly this banner as they open.

WHAT IT WILL NOT DO
  * It will not overwrite copy a human wrote. The script only owns a message
    that matches the shape it generates (see `_is_machine_owned`); anything
    else it reports and leaves alone, so hand-edited marketing copy is safe.
  * It will not retire the disclaimer on its own. Once the sample clears
    CALIBRATION_FLOOR it prints a loud READY TO RETIRE notice and changes
    nothing — removing a caution from a paying user's screen is Andy's call,
    not a cron job's.
  * It will not claim a direction the sample cannot support. The "early
    returns" clause is chosen from the measured rate against the -110
    breakeven of 52.38%, not frozen prose.
  * It will not count preseason. The window starts at the registry's
    `season_start` (NHL preseason was 25-18 and inflated the record — see
    project_nhl_preseason_record_930).
  * A PATCH that matches zero rows is treated as a failure, not a success
    (a 204 is not a write).

CLI
    python refresh_calibration_notice.py --sport NHL
    python refresh_calibration_notice.py --sport NHL --apply
    python refresh_calibration_notice.py --apply          # every active sport
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

#: Graded picks below which we tell users the sport is still calibrating.
#: Not a statistical threshold so much as an honesty one: at n<150 a 5-point
#: swing in hit rate is one week of variance.
CALIBRATION_FLOOR = 150

#: Breakeven at -110.
BREAKEVEN = 52.38

#: Phrases that identify a message this script generated. If the live text
#: contains none of them, a human wrote it and we do not touch it.
_OWNED_MARKERS = ('graded sample is', 'still calibrating')


def _page(table: str, params: dict, cap: int = 60000) -> list:
    out, off = [], 0
    while off < cap:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=q,
                         timeout=180)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{table} {r.status_code} {r.text[:160]}')
        chunk = r.json()
        if not isinstance(chunk, list):
            raise RuntimeError(f'{table} returned {type(chunk).__name__}')
        out += chunk
        if len(chunk) < 1000:
            return out
        off += 1000
    return out


def _is_machine_owned(msg: str | None) -> bool:
    """True only for a message this script generated.

    An EMPTY message is not an invitation. NFL carries no state_message at
    all, and treating blank as "ours to fill" would have this script invent a
    user-facing calibration disclaimer on a flagship sport running 58.2% —
    a banner nobody asked for, on the one surface where a stray caution costs
    the most. Blank means "no banner by choice"; use --create to opt in.
    """
    if not str(msg or '').strip():
        return False
    low = str(msg).lower()
    return any(m in low for m in _OWNED_MARKERS)


def measure(sport: str, season_start: str) -> dict:
    """Graded W/L for a sport's game reads, regular season only.

    `jerry_reads` is the authority here rather than `public_receipts`: the
    receipts are a per-surface COPY and are demonstrably incomplete (12 NHL
    receipts for 10-06..10-07 carry a NULL pick_label while their source read
    is fully graded). Counting the copy would under-report the sample, which
    is the exact failure this script exists to end.
    """
    rows = _page('jerry_reads',
                 {'select': 'game_date,result',
                  'sport': f'eq.{sport}',
                  'game_date': f'gte.{season_start}'})
    wins = losses = pushes = ungraded = 0
    dates = set()
    for x in rows:
        res = str(x.get('result') or '').strip().upper()
        if res in ('WIN', 'W'):
            wins += 1
        elif res in ('LOSS', 'L'):
            losses += 1
        elif res in ('PUSH', 'P', 'VOID'):
            pushes += 1
        else:
            ungraded += 1
            continue
        dates.add(str(x.get('game_date'))[:10])
    graded = wins + losses
    return {'wins': wins, 'losses': losses, 'pushes': pushes,
            'ungraded': ungraded, 'graded': graded, 'reads': len(rows),
            'dates': len(dates),
            'hit_pct': (wins / graded * 100) if graded else None}


def compose(sport: str, m: dict, opened: str) -> str:
    """The disclaimer, with every number measured rather than asserted."""
    when = dt.date.fromisoformat(opened).strftime('%b %-d') \
        if sys.platform != 'win32' else \
        dt.date.fromisoformat(opened).strftime('%b %d').replace(' 0', ' ')
    hp = m['hit_pct']

    # The directional clause is CHOSEN from the measurement. The old frozen
    # text asserted "early returns are below our own expectations" forever.
    if hp is None:
        tone = ('Nothing has graded yet, so there is no record to read into '
                'either direction.')
    elif hp < BREAKEVEN - 4:
        tone = (f'Early returns are below our own expectations at '
                f'{hp:.1f}%, and the sample is still too small to separate '
                f'variance from signal.')
    elif hp < BREAKEVEN:
        tone = (f'Early returns are right around break-even at {hp:.1f}% '
                f'(a -110 bet needs {BREAKEVEN:.2f}%), which at this sample '
                f'size is indistinguishable from noise in either direction.')
    else:
        tone = (f'Early returns are ahead of break-even at {hp:.1f}%, but at '
                f'this sample size that is not yet evidence of an edge.')

    return (
        f'{sport} opened {when} and the model is still calibrating on '
        f'live-season data. Our graded sample is {m["graded"]} picks '
        f'({m["wins"]}-{m["losses"]}) across {m["dates"]} slates — well below '
        f'the threshold we hold MLB and NFL to — so a {sport} tier carries '
        f'less weight than the same tier in another sport. {tone} Treat these '
        f'as early-stage reads and stake accordingly; tiers refine as grading '
        f'rolls in.'
    )


def run(sport: str, apply: bool, create: bool = False) -> int:
    r = requests.get(f'{SB}/rest/v1/sport_registry', headers=H,
                     params={'select': 'sport,state,state_message,'
                                       'season_start,active',
                             'sport': f'eq.{sport}'}, timeout=60)
    rows = r.json() if r.status_code in (200, 206) else []
    if not rows:
        print(f'  {sport}: no sport_registry row — skipped')
        return 0
    reg = rows[0]
    opened = str(reg.get('season_start') or '')[:10]
    if not opened:
        print(f'  {sport}: no season_start — refusing to guess a window')
        return 0

    m = measure(sport, opened)
    print(f'\n  {sport}  season from {opened}')
    print(f'    reads {m["reads"]}  ·  graded {m["graded"]} '
          f'({m["wins"]}-{m["losses"]}, {m["pushes"]} push)  ·  '
          f'ungraded {m["ungraded"]}  ·  slates {m["dates"]}')
    if m['hit_pct'] is not None:
        print(f'    hit rate {m["hit_pct"]:.1f}%  (breakeven {BREAKEVEN}%)')

    live = reg.get('state_message')
    if not str(live or '').strip():
        if not create:
            print('    no banner today — leaving it that way '
                  '(pass --create to add one)')
            return 0
    elif not _is_machine_owned(live):
        print('    ! live message was hand-written — NOT touching it')
        print(f'      {str(live)[:160]}')
        return 0

    if m['graded'] >= CALIBRATION_FLOOR:
        print(f'    ** READY TO RETIRE: {m["graded"]} graded >= floor '
              f'{CALIBRATION_FLOOR}. Leaving the banner as-is; removing a '
              f'caution from the app is a human decision.')
        return 0

    new = compose(sport, m, opened)
    if str(live or '').strip() == new.strip():
        print('    already current — no write')
        return 0

    print(f'    OLD: {str(live)[:150]}')
    print(f'    NEW: {new[:150]}')
    if not apply:
        return 1

    pr = requests.patch(f'{SB}/rest/v1/sport_registry', headers=H_W,
                        params={'sport': f'eq.{sport}'},
                        data=json.dumps({'state_message': new}), timeout=60)
    back = pr.json() if pr.content else []
    if pr.status_code not in (200, 204) or not back:
        print(f'    ! PATCH {pr.status_code} matched no row — NOT written')
        return 0
    if back[0].get('state_message') != new:
        print('    ! read-back disagrees with what we sent — NOT confirmed')
        return 0
    print('    written and verified')
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None,
                    help='NHL/NBA/NCAAB; default every active sport')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--create', action='store_true',
                    help='also ADD a banner to a sport that has '
                         'none today (off by default)')
    args = ap.parse_args()

    if args.sport:
        sports = [args.sport.upper()]
    else:
        r = requests.get(f'{SB}/rest/v1/sport_registry', headers=H,
                         params={'select': 'sport', 'active': 'is.true',
                                 'limit': '50'}, timeout=60)
        sports = sorted(str(x['sport']).upper()
                        for x in (r.json() if r.status_code in (200, 206)
                                  else []))

    print(f'=== refresh_calibration_notice · {len(sports)} sport(s) · '
          f'{"APPLY" if args.apply else "DRY"} ===')
    changed = 0
    for s in sports:
        try:
            changed += run(s, args.apply, args.create)
        except Exception as exc:                           # noqa: BLE001
            print(f'  ! {s}: {type(exc).__name__}: {exc}')
    print(f'\n  {"updated" if args.apply else "would update"} {changed} '
          f'banner(s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
