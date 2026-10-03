"""Refresh *_qb_name / *_qb_id on FORWARD nfl_game_context rows.

══ 2026-10-02 · THE QB ON THE CARD WAS A JULY SNAPSHOT ══
Andy: "do you think all nfl picks are sound and consistent with data and
accounts for injured QB1s?"

They did not. Measured tonight:

    nfl_starters wk4 (correct)      nfl_game_context (what picks use)
    WAS  Marcus Mariota             Jayden Daniels      <- wrong
    NYG  Jameis Winston             Jaxson Dart         <- wrong

nfl_game_context does not read nfl_starters at all; it re-derives the
starter from nfl_player_stats. That derivation is CORRECT today — run by
hand it returns Mariota (wk3, 31 att) and Winston (wk3, 22 att). The defect
is that it never re-runs: IND @ WAS carries

    updated_at = computed_at = 2026-07-22      <- QB written in July
    team_form_enriched_at    = 2026-10-02      <- refreshed tonight

so the row looks fresh while the QB is ten weeks stale, and only 16 of 223
forward games carry a QB name at all. Daniels was the presumed Washington
starter in July; he is not the starter now, and IND @ WAS was priced with
him at quarterback.

WHY READ nfl_starters RATHER THAN RE-RUN THE CONTEXT DERIVATION. Two
resolvers for one question is the actual problem. nfl_starters is the one
that got the work: ESPN's roster endpoint is ALPHABETICAL BY SURNAME, not
depth-ordered, which had nfl_weekly_starters.py wrong on 72% of teams until
it was rebuilt on the box-score rule (26% -> 95%). That table is also
refreshed by a scheduled workflow step. Pointing the context at it makes
the card, the QB Matchup panel and the Madden enrichment agree by
construction instead of by coincidence.

player_id is NULL on nfl_starters rows, and the id feeds nfl_qb_vs_team and
the home/away splits join, so it is resolved by (team, name) against
nfl_player_stats. A name that cannot be resolved still gets written — the
name is what users read — with the id left alone rather than blanked.

FORWARD ROWS ONLY. A past game's QB is a historical record of who actually
played; rewriting it would corrupt the audit trail.

Dry run unless --apply.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
load_dotenv('.env')
SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
RH = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
WH = {**RH, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def paged(tbl: str, params: dict) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=RH,
                         params=dict(params, limit='1000', offset=str(off)),
                         timeout=90)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{tbl} -> {r.status_code}: {(r.text or "")[:200]}')
        body = r.json()
        if not isinstance(body, list):
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def _norm(n: str) -> str:
    return ''.join(ch for ch in (n or '').lower() if ch.isalnum())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int,
                    default=datetime.now(timezone.utc).year)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    today = str(datetime.now(timezone.utc).date())

    # ── 1. latest starter per team (carry-forward: highest week wins) ──
    st = paged('nfl_starters', {
        'select': 'team,week,player_name,player_id,position,is_starter,source',
        'season': f'eq.{args.season}', 'position': 'eq.QB',
        'season_type': 'eq.REG'})
    best: dict = {}
    for r in st:
        if r.get('is_starter') is False:
            continue
        t, w = r.get('team'), r.get('week')
        if not t or w is None:
            continue
        if t not in best or w > best[t]['week']:
            best[t] = r
    print(f'nfl_starters: {len(st)} QB rows -> {len(best)} teams '
          f'(latest week each)')
    if not best:
        print('  ✖ no starters — refusing to write')
        return 1

    # ── 2. name -> player_id, from the per-week stat rows ──
    ps = paged('nfl_player_stats', {
        'select': 'team,player_name,player_id,position,week',
        'season': f'eq.{args.season}', 'position': 'eq.QB',
        'season_type': 'eq.REG'})
    ids: dict = {}
    for r in ps:
        if r.get('player_id') and r.get('player_name'):
            ids[(r.get('team'), _norm(r['player_name']))] = r['player_id']
            ids.setdefault(_norm(r['player_name']), r['player_id'])

    # ── 3. forward context rows ──
    ctx = paged('nfl_game_context', {
        'select': 'game_id,game_date,week,home_team,away_team,'
                  'home_qb_name,away_qb_name,home_qb_id,away_qb_id',
        'game_date': f'gte.{today}'})
    print(f'forward nfl_game_context rows: {len(ctx)}')

    patches, changed, unresolved = [], [], defaultdict(int)
    for g in ctx:
        body = {}
        for sidepfx, teamkey in (('home', 'home_team'), ('away', 'away_team')):
            team = g.get(teamkey)
            s = best.get(team)
            if not s:
                unresolved[f'no starter row for {team}'] += 1
                continue
            name = s.get('player_name')
            if not name:
                continue
            cur = g.get(f'{sidepfx}_qb_name')
            if cur == name:
                continue
            body[f'{sidepfx}_qb_name'] = name
            pid = (ids.get((team, _norm(name))) or ids.get(_norm(name)))
            if pid:
                body[f'{sidepfx}_qb_id'] = pid
            changed.append((g.get('game_date'), g.get('week'),
                            g.get('away_team'), g.get('home_team'),
                            sidepfx, cur, name, s.get('source'),
                            'id' if pid else 'NO-ID'))
        if body:
            patches.append((g['game_id'], body))

    print(f'\nrows to patch: {len(patches)}  ·  field changes: {len(changed)}')
    for c in changed[:20]:
        print(f'  {c[0]} wk{c[1]} {c[3]} vs {c[2]}  {c[4]}_qb: '
              f'{str(c[5])!r} -> {c[6]!r}  (src={c[7]}, {c[8]})')
    if len(changed) > 20:
        print(f'  … and {len(changed) - 20} more')
    if unresolved:
        print('\nteams with no starter row:')
        for k, n in sorted(unresolved.items(), key=lambda kv: -kv[1])[:8]:
            print(f'  {n:4d}  {k}')

    if not args.apply:
        print('\n[dry] nothing written — pass --apply')
        return 0

    ok = 0
    for gid, body in patches:
        import urllib.parse
        q = urllib.parse.quote(str(gid), safe='')
        r = requests.patch(f'{SB}/rest/v1/nfl_game_context?game_id=eq.{q}',
                           headers=WH, json=body, timeout=40)
        if r.status_code in (200, 204):
            ok += 1
        else:
            print(f'  ! {gid}: {r.status_code} {(r.text or "")[:140]}')
    print(f'\npatched {ok}/{len(patches)}')

    back = paged('nfl_game_context', {
        'select': 'home_qb_name,away_qb_name', 'game_date': f'gte.{today}'})
    filled = sum(1 for r in back if r.get('home_qb_name'))
    print(f'forward rows with a home QB name: {filled}/{len(back)}')
    return 0 if ok == len(patches) else 1


if __name__ == '__main__':
    raise SystemExit(main())
