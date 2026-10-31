"""Regenerate `primary_play.sub` wherever it argues for the other team.

== 2026-10-31 . THE POTD'S REASON ARGUED FOR THE TEAM WE DID NOT TAKE =====
Marshall @ Old Dominion shipped as today's Pick of the Day:

    label  'Marshall ML'   side=AWAY  tier=STRONG  conviction=81
    sub    'Old Dominion +2.5 - Our spread model lands on this side at
            conviction 56.'

Illinois @ Maryland had the same shape ('Illinois ML' / 'Maryland +2.5 -
76% of tickets sit on the favourite, so we are taking the points'). A user
reading either sees a case for the side we are not on, quoting a different
conviction from the one on the pick. Andy, on the UFC labels earlier today:
"If picks are incorrectly labeled in app causing users to act on somehting
that we dont believe in is unacceptable fix it."

ROOT CAUSE, from the rows' own audit_note:

    2026-10-29 dog floor REVERTED: promoted on a cz/fr reading that put the
    public on the FAVOURITE; the validated oddscrowd feed puts 70% of
    tickets on [the other side]

apply_dog_floor promoted the home dog off the cz/fr splits feed;
dogfloor_read_copy then wrote the subtitle FOR that promotion, which is
exactly its job. The promotion was then reverted -- correctly, because cz/fr
inverts the public side (project_cz_splits_contradict_oddscrowd_1029). But
the revert path has no copy step, so the pick went back to the moneyline and
the sentence stayed with the withdrawn spread.

WHY THIS IS A GENERAL GUARD AND NOT A DOG-FLOOR PATCH. Any writer that
replaces primary_play after the prose is composed leaves the same wreckage,
and this codebase has several: apply_dog_floor, the LR override (lr_v1 is
the _engine on the Illinois row), the chalk cap, the ML reroute, the pick
lock. Fixing one revert path would leave the rest. So the check is on the
OUTPUT -- does the subtitle name the side we published -- which catches all
of them regardless of which one did it.

HOW THE REPLACEMENT IS BUILT. From the row's own `_ensemble_sources`,
filtered to the published side, in the format _compose_ensemble_sub already
uses ('<label>: <prose> - <prose>'). Not re-derived from scratch: re-deriving
is what rebuilt a bad name join in
feedback_grade_the_leg_from_the_row_it_came_from. If the row has no usable
prose for the published side, the sub is set to the label plus the signal
count rather than invented -- the same fallback that function uses.

Team matching uses the ROW's own home_team/away_team strings, never a league
name list (project_ncaaf_five_team_name_mismatch_1021,
project_nhl_stl_name_mismatch_1013).

Confirms by READBACK. A refused PATCH returns 200 with zero rows changed,
which is indistinguishable from success (feedback_204_is_not_a_write).

USAGE
  python repair_stale_pick_prose.py --date 2026-10-31
  python repair_stale_pick_prose.py --date 2026-10-31 --apply
  python repair_stale_pick_prose.py --date 2026-10-31 --sport NCAAF --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ['SUPABASE_KEY'])
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

TABLES = {'NCAAF': 'ncaaf_game_context', 'NFL': 'nfl_game_context',
          'NBA': 'nba_game_context', 'NHL': 'nhl_game_context',
          'NCAAB': 'ncaab_game_context'}
#: Separators the various copy writers use to end the leading clause.
SEPS = ('—', '·', ' - ', ': ')


def jd(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return None
    return v if isinstance(v, (dict, list)) else None


def names_team(text: str, team: str) -> bool:
    if not text or not team:
        return False
    t, full = text.lower(), team.lower()
    if full in t:
        return True
    tail = full.split()[-1]
    return len(tail) >= 5 and tail in t


def lead_clause(sub: str) -> str:
    head = sub
    for s in SEPS:
        if s in head:
            head = head.split(s)[0]
    return head[:70]


def side_named(text: str, home: str, away: str):
    h, a = names_team(text, home), names_team(text, away)
    if h == a:
        return None
    return 'HOME' if h else 'AWAY'


def rebuild_sub(pp: dict) -> str | None:
    """The sub _compose_ensemble_sub would have produced for this side."""
    label = str(pp.get('label') or '')
    side = str(pp.get('side') or '').upper()
    srcs = pp.get('_ensemble_sources')
    if not label:
        return None
    want = []
    if isinstance(srcs, list):
        for s in srcs:
            if not isinstance(s, dict):
                continue
            s_side = str(s.get('side') or '').upper()
            if not s_side.startswith(side):
                continue
            try:
                if float(s.get('contribution') or 0) <= 0:
                    continue
            except (TypeError, ValueError):
                continue
            p = str(s.get('prose') or '').strip()
            if p and not p.startswith('_'):
                want.append((float(s.get('contribution') or 0), p))
    want.sort(key=lambda t: -t[0])
    parts = []
    for _, p in want[:3]:
        if p and p[0].isalpha() and p[0].islower():
            p = p[0].upper() + p[1:]
        parts.append(p)
    if parts:
        return f'{label}: ' + ' · '.join(parts)
    n = len([s for s in (srcs or []) if isinstance(s, dict)])
    return f'{label} — {n} signal(s) aligned' if n else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True)
    ap.add_argument('--sport', default=None)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    sports = ([a.sport.upper()] if a.sport else list(TABLES))
    total = fixed = skipped = failed = 0
    for sport in sports:
        tbl = TABLES.get(sport)
        if not tbl:
            print(f'  ! unknown sport {sport}')
            continue
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_R, timeout=90,
                         params={'select': 'game_id,home_team,away_team,'
                                           'primary_play',
                                 'game_date': f'eq.{a.date}', 'limit': 300})
        if r.status_code != 200:
            print(f'  ! {tbl} HTTP {r.status_code} {r.text[:140]} '
                  f'-- CANNOT VERIFY, skipping {sport}')
            failed += 1
            continue
        for x in r.json():
            pp = jd(x.get('primary_play')) or {}
            label, sub = str(pp.get('label') or ''), str(pp.get('sub') or '')
            home, away = str(x.get('home_team') or ''), str(x.get('away_team') or '')
            if not (label and sub and home and away):
                continue
            lab_side = side_named(label, home, away)
            if lab_side is None:          # totals etc. name no team
                continue
            sub_side = side_named(lead_clause(sub), home, away)
            if sub_side is None or sub_side == lab_side:
                continue
            total += 1
            new = rebuild_sub(pp)
            print(f'\n  [{sport}] {away} @ {home}')
            print(f'     label {label!r} ({lab_side})')
            print(f'     OLD   {sub[:120]!r} ({sub_side})')
            if not new:
                print(f'     -> no usable prose for the published side; '
                      f'LEFT ALONE rather than invented')
                skipped += 1
                continue
            print(f'     NEW   {new[:120]!r}')
            note = str(pp.get('audit_note') or '')
            if note:
                print(f'     why   {note[:150]}')
            if not a.apply:
                continue
            newpp = dict(pp)
            newpp['sub'] = new
            # Record the repair on the row so the next reader knows the prose
            # was regenerated rather than originally composed this way.
            newpp['_sub_repaired'] = {
                'at': a.date, 'was': sub[:200],
                'by': 'repair_stale_pick_prose',
                'reason': 'subtitle named the side we did not publish'}
            pr = requests.patch(f'{SB}/rest/v1/{tbl}', headers=H_W, timeout=60,
                                params={'game_id': f'eq.{x["game_id"]}'},
                                json={'primary_play': newpp})
            back = pr.json() if pr.status_code in (200, 201) else []
            ok = False
            if back:
                b = jd(back[0].get('primary_play')) or {}
                ok = str(b.get('sub') or '') == new
            if not ok:                      # readback, never the status code
                g = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_R, timeout=60,
                                 params={'game_id': f'eq.{x["game_id"]}',
                                         'select': 'primary_play'})
                gj = g.json() if g.status_code == 200 else []
                if gj:
                    b = jd(gj[0].get('primary_play')) or {}
                    ok = str(b.get('sub') or '') == new
            print(f'     HTTP {pr.status_code} '
                  f'{"CONFIRMED" if ok else "NOT CONFIRMED -- a trigger may refuse it"}')
            fixed += 1 if ok else 0
            failed += 0 if ok else 1
    print(f'\n=== {total} stale subtitle(s) found; repaired={fixed} '
          f'left_alone={skipped} failed={failed} ===')
    if total and not a.apply:
        print('Re-run with --apply to write.')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
