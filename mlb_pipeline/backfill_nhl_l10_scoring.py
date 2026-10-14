#!/usr/bin/env python3
"""Populate nhl_game_context.home/away_l10_goals_(against_)per_game.

== 2026-10-14 - WHY ==
Both columns were NULL on 202 of 202 rows. ANDY: "you cant say that nhl has
missing data and move on."

ROOT CAUSE, and it was a typo. _load_l10_gpg_per_team() in nhl_game_context.py
passed `headers=SB_READ`, and SB_READ IS NOT DEFINED ANYWHERE in that module --
the canonical name is H_READ, used five times elsewhere; SB_READ appeared
exactly once, in that one function. Every call raised NameError and the bare
`except: return {}` swallowed it. Dead since it was written on 2026-08-22, with
a docstring claiming it revived four signals.

Three more defects behind the typo, all fixed in that module:
  * the select asked for home_team_abbrev / away_team_abbrev, columns
    nhl_game_results DOES NOT HAVE (it carries full names). PostgREST answers
    an explicit select on a missing column with HTTP 400, which the same
    `except` would also have swallowed.
  * GOALS AGAINST was never computed, only goals-for -- so _tendency_bias's
    l10_goal_diff vote could not have fired even with goals-for populated,
    because it needs all four values.
  * the join used RAW names. nhl_game_results spells it 'St Louis Blues';
    nhl_game_context spells it 'St. Louis Blues'. Same defect that cost St
    Louis all its odds and tendencies on 10-13. Now keyed on nkey(), which was
    hoisted to module level for exactly this reason.

THIS CHANGES A LIVE NUMBER, deliberately. _tendency_bias() counts home-vs-away
lean votes, and `l10_goal_diff` is one of them; it has never been able to fire.
Populating these columns lets it vote, which shifts the NHL tendency net. No
signal_sources rule reads l10_goals (verified: zero matches), so no signal
fires or stops firing -- the change is confined to the tendency lean count.

DATA NOTE: nhl_game_results holds INTERNATIONAL games alongside NHL ones --
Canada, USA, Finland, Sweden, Czechia, Denmark, Slovakia, Switzerland, France,
Germany, Italy, Latvia all appear as teams (Olympic year). They produce 12 extra
keys which no NHL context row ever looks up, so they cannot contaminate a club's
L10. All 32 NHL clubs resolve with a full 10 completed games. Flagged because
anything else computing NHL team history by name over this table should know.

USAGE
    python backfill_nhl_l10_scoring.py --dry-run
    python backfill_nhl_l10_scoring.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys

import requests

sys.path.insert(0, '.')
import nhl_game_context as N

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H_READ, H_WRITE = N.SB, N.H_READ, N.H_WRITE
COLS = ('home_l10_goals_per_game', 'home_l10_goals_against_per_game',
        'away_l10_goals_per_game', 'away_l10_goals_against_per_game')


def _per_team_history():
    """{nkey(team): [(game_date, gf, ga), ...]} over COMPLETED games, ascending.

    Needed because _load_l10_gpg_per_team() returns ONE number per team -- the
    L10 as of RIGHT NOW. Writing that onto a game played in September is
    anachronistic: it hands that row knowledge of games played after it, which
    would both misstate the tendency vote and poison any later backtest of it.
    So historical rows get a POINT-IN-TIME L10 computed from each team's last
    10 completed games strictly BEFORE that game's date.
    """
    from collections import defaultdict
    rows, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/nhl_game_results', headers=H_READ,
                         params={'select': 'game_date,home_team,away_team,'
                                           'home_score,away_score',
                                 'order': 'game_date.asc',
                                 'limit': '1000', 'offset': str(off)},
                         timeout=60)
        if r.status_code not in (200, 206):
            print(f'  history read failed {r.status_code}')
            return {}
        b = r.json()
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    hist = defaultdict(list)
    for g in rows:
        hs, as_ = g.get('home_score'), g.get('away_score')
        if hs is None or as_ is None:
            continue
        d = str(g.get('game_date'))[:10]
        h, a = N.nkey(g.get('home_team')), N.nkey(g.get('away_team'))
        if h:
            hist[h].append((d, float(hs), float(as_)))
        if a:
            hist[a].append((d, float(as_), float(hs)))
    return hist


def _l10_as_of(hist, team, date):
    """That team's last 10 completed games STRICTLY BEFORE `date`."""
    g = hist.get(N.nkey(team))
    if not g:
        return None
    prior = [x for x in g if x[0] < date][-10:]
    if len(prior) < 3:          # too little history to call it form
        return None
    n = len(prior)
    return {'gf': round(sum(x[1] for x in prior) / n, 2),
            'ga': round(sum(x[2] for x in prior) / n, 2), 'n': n}


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    cache = N._load_l10_gpg_per_team()
    if not cache:
        print('L10 cache empty — aborting rather than writing NULLs over '
              'anything')
        return 1

    rows, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/nhl_game_context', headers=H_READ,
                         params={'select': 'game_id,game_date,home_team,'
                                           'away_team,' + ','.join(COLS),
                                 'limit': '1000', 'offset': str(off)},
                         timeout=60)
        if r.status_code not in (200, 206):
            print(f'read failed {r.status_code}: {r.text[:200]}')
            return 1
        b = r.json()
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    print(f'{len(rows)} nhl_game_context rows')

    before = sum(1 for x in rows
                 if x.get('home_l10_goals_per_game') is not None)
    print(f'home_l10_goals_per_game populated BEFORE: {before}/{len(rows)}')

    hist = _per_team_history()
    print(f'point-in-time history: {len(hist)} teams')

    updates, unresolved, thin = [], [], 0
    for x in rows:
        d = str(x.get('game_date'))[:10]
        h = _l10_as_of(hist, x.get('home_team'), d)
        aw = _l10_as_of(hist, x.get('away_team'), d)
        if not h or not aw:
            # fall back to the CURRENT L10 only for games not yet played,
            # where "as of now" IS point-in-time.
            if h is None and aw is None:
                thin += 1
            unresolved.append((x.get('home_team'), x.get('away_team')))
            continue
        updates.append({
            'game_id': x['game_id'],
            'home_l10_goals_per_game': h['gf'],
            'home_l10_goals_against_per_game': h['ga'],
            'away_l10_goals_per_game': aw['gf'],
            'away_l10_goals_against_per_game': aw['ga'],
        })
    print(f'resolved {len(updates)}, unresolved {len(unresolved)} '
          f'({thin} with under 3 prior games — genuinely no form yet)')
    if unresolved:
        seen = sorted({t for pair in unresolved for t in pair
                       if N.nkey(t) not in cache})
        print(f'  UNRESOLVED TEAM NAMES ({len(seen)}): {seen[:12]}')

    if a.dry_run:
        for u in updates[:4]:
            print(f"  would set {u['game_id'][:12]}... "
                  f"home {u['home_l10_goals_per_game']}/"
                  f"{u['home_l10_goals_against_per_game']}  "
                  f"away {u['away_l10_goals_per_game']}/"
                  f"{u['away_l10_goals_against_per_game']}")
        print(f'DRY RUN — would patch {len(updates)} rows')
        return 0

    ok = fail = 0
    for u in updates:
        gid = u.pop('game_id')
        w = requests.patch(f'{SB}/rest/v1/nhl_game_context', headers=H_WRITE,
                           params={'game_id': f'eq.{gid}'},
                           data=json.dumps(u), timeout=60)
        if w.status_code in (200, 204):
            ok += 1
        else:
            fail += 1
            if fail <= 3:
                print(f'  PATCH {w.status_code}: {w.text[:200]}')
    print(f'patched {ok}, failed {fail}')

    # A 204 is not proof of a write — read it back.
    r = requests.get(f'{SB}/rest/v1/nhl_game_context', headers=H_READ,
                     params={'select': 'game_id,' + ','.join(COLS),
                             'limit': '1000'}, timeout=60)
    back = r.json() if r.status_code in (200, 206) else []
    for c in COLS:
        n = sum(1 for x in back if x.get(c) is not None)
        print(f'  AFTER  {c:<38} {n}/{len(back)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
