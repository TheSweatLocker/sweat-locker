"""Bridge NHL prop rows to their game's real id — the hash/numeric split.

THE PROBLEM (project_nhl_props_cannot_join_their_game_1003, still open)
NHL props and NHL games are keyed in two different ID SPACES:

    nhl_pipeline_props.game_id   841087bae12b56c16406af177d78ea01   MD5 hash
    nhl_game_context.game_id     2026020078                         NHL numeric

So any query that filters props by the game's id returns ZERO rows. That is
why game detail cannot show NHL props: the panel is handed the numeric id and
the props carry a hash. The panel being MLB-only has been masking it.

WHY A (DATE, TEAM-PAIR) MATCH IS SAFE HERE, and how that was checked
Matching on names is normally the fuzzy-substring anti-pattern this codebase
has already removed once. It is defensible here only because of a structural
check: the number of distinct prop games per date EXACTLY equals the number
of context games per date, on every date props exist —

    09-29  5/5    10-01  8/8    10-03 13/13   10-05  4/4    10-07  3/3
    09-30  3/3    10-02  5/5    10-04  5/5    10-06  9/9    10-08 10/10

With equal counts and an UNORDERED team pair as the key, the mapping is
1:1 and a wrong match would have to collide two real games on one date — which
the count equality rules out. The match is on ABBREVIATIONS mapped through the
canonical table in nhl_team_stats_pull.py, not on substrings of team names.

AND IT REFUSES TO GUESS. Any prop game that does not resolve to exactly one
context game is reported and SKIPPED, never approximated. A bridge that
silently mis-attributes a prop to the wrong game would attach a player to
opponents he never faced, which is worse than showing no props at all.

WHAT IT WRITES
`nhl_pipeline_props.ctx_game_id` — the numeric id, added by migration
20261009a. The hash `game_id` is left ALONE: receipts and existing joins key
on it, and rewriting a published identity is the set-once trap
(feedback_204_is_not_a_write). A second column is additive and reversible.

RUN ORDER: apply migrations/20261009a_nhl_prop_ctx_game_id.sql first. This
script checks for the column and bails with instructions if it is missing.

CLI
    python bridge_nhl_prop_game_ids.py                  # dry run
    python bridge_nhl_prop_game_ids.py --apply
    python bridge_nhl_prop_game_ids.py --since 2026-09-29
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML
# Reuse the canonical abbrev -> full-name table rather than copying it. Two
# copies of a team-name map drift, and a drifted map here silently attaches
# props to the wrong game. The module is import-safe (its work sits behind
# an `if __name__ == '__main__'` guard).
from nhl_team_stats_pull import TEAM_NAMES as TEAM_BY_ABBREV

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def _page(t, p, cap=400000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:160]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def _norm(s):
    """Fold the accent and punctuation differences between sources.

    'Montréal Canadiens' vs 'Montreal Canadiens' and 'St. Louis' vs
    'St Louis' are the two that actually bite in this dataset.
    """
    s = (s or '').lower().strip()
    for a, b in (('é', 'e'), ('è', 'e'), ('ö', 'o'), ('.', ''), ('-', ' ')):
        s = s.replace(a, b)
    return ' '.join(s.split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-09-01')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    # Column presence check — the migration is applied by hand in the
    # Supabase SQL editor, so this must fail loudly rather than PATCH into
    # a column that does not exist (PostgREST would 400 per row).
    probe = requests.get(f'{SB}/rest/v1/nhl_pipeline_props', headers=H,
                         params={'select': 'ctx_game_id', 'limit': '1'},
                         timeout=60)
    if probe.status_code != 200:
        print('! nhl_pipeline_props.ctx_game_id does not exist yet.')
        print('  Apply supabase/migrations/20261009a_nhl_prop_ctx_game_id.sql')
        print('  in the Supabase SQL editor first, then re-run.')
        return 1

    ctx = _page('nhl_game_context',
                {'select': 'game_id,game_date,home_team,away_team',
                 'game_date': f'gte.{a.since}'})
    # key: (date, frozenset of two normalised team names) -> [game_id]
    by_key = collections.defaultdict(list)
    for g in ctx:
        d = str(g.get('game_date') or '')[:10]
        h, aw = _norm(g.get('home_team')), _norm(g.get('away_team'))
        if d and h and aw:
            by_key[(d, frozenset((h, aw)))].append(str(g['game_id']))

    props = _page('nhl_pipeline_props',
                  {'select': 'id,game_id,game_date,team_abbrev,opp_abbrev,'
                             'ctx_game_id',
                   'game_date': f'gte.{a.since}'})
    print(f'=== NHL prop game bridge · {len(props)} prop rows · '
          f'{len(ctx)} context games since {a.since}')

    resolved = {}          # prop hash game_id -> ctx numeric game_id
    unresolved = collections.Counter()
    no_abbrev = ambiguous = 0
    for p in props:
        d = str(p.get('game_date') or '')[:10]
        ta, oa = p.get('team_abbrev'), p.get('opp_abbrev')
        if not ta or not oa:
            no_abbrev += 1
            continue
        tn, on = _norm(TEAM_BY_ABBREV.get(ta)), _norm(TEAM_BY_ABBREV.get(oa))
        if not tn or not on:
            unresolved[f'unknown abbrev {ta}/{oa}'] += 1
            continue
        hits = by_key.get((d, frozenset((tn, on)))) or []
        if len(hits) == 1:
            resolved[str(p['game_id'])] = hits[0]
        elif not hits:
            unresolved[f'no ctx game {d} {ta}@{oa}'] += 1
        else:
            # Two context games for one date+pair would break the 1:1
            # guarantee the count check established. Refuse, loudly.
            ambiguous += 1
            unresolved[f'AMBIGUOUS {d} {ta}@{oa} -> {hits}'] += 1

    prop_games = {str(p['game_id']) for p in props if p.get('game_id')}
    print(f'    distinct prop games {len(prop_games)} · '
          f'resolved {len(resolved)} '
          f'({100 * len(resolved) / max(1, len(prop_games)):.1f}%)')
    print(f'    rows with no abbrev pair: {no_abbrev} · ambiguous: {ambiguous}')
    if unresolved:
        print(f'\n    UNRESOLVED ({sum(unresolved.values())} rows):')
        for k, v in unresolved.most_common(12):
            print(f'      {k}  x{v}')
    if ambiguous:
        print('\n    ! Ambiguity breaks the 1:1 assumption this bridge rests')
        print('      on. Do NOT --apply until it is understood.')
        return 1

    todo = [p for p in props
            if str(p.get('game_id')) in resolved
            and str(p.get('ctx_game_id') or '') != resolved[str(p['game_id'])]]
    print(f'\n    prop rows needing a ctx_game_id write: {len(todo)}')
    for p in props[:4]:
        gid = str(p.get('game_id'))
        if gid in resolved:
            print(f'      {p["game_date"]} {p["team_abbrev"]}@'
                  f'{p["opp_abbrev"]}  {gid[:12]}... -> {resolved[gid]}')

    if not a.apply:
        print('\n[DRY] nothing written. Re-run with --apply')
        return 0

    # Batch by target id so one PATCH covers a whole game.
    by_target = collections.defaultdict(list)
    for p in todo:
        by_target[resolved[str(p['game_id'])]].append(str(p['game_id']))
    ok = failed = 0
    for ctx_id, hashes in by_target.items():
        for hsh in set(hashes):
            r = requests.patch(
                f'{SB}/rest/v1/nhl_pipeline_props', headers=H_W,
                params={'game_id': f'eq.{hsh}'},
                data=json.dumps({'ctx_game_id': ctx_id}), timeout=120)
            if r.status_code not in (200, 204):
                print(f'    ! {hsh[:12]} {r.status_code} {r.text[:120]}')
                failed += 1
                continue
            # A 204 is not a write — verify by reading one row back.
            v = requests.get(f'{SB}/rest/v1/nhl_pipeline_props', headers=H,
                             params={'select': 'ctx_game_id',
                                     'game_id': f'eq.{hsh}', 'limit': '1'},
                             timeout=60)
            got = (v.json() or [{}])[0].get('ctx_game_id') \
                if v.status_code == 200 else None
            if str(got) == str(ctx_id):
                ok += 1
            else:
                print(f'    ! {hsh[:12]} read-back {got!r} != {ctx_id!r}')
                failed += 1
    print(f'\n  bridged {ok} prop games · failed {failed}')
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
