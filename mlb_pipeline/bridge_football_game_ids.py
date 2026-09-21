"""bridge_football_game_ids — connect football context ids to results ids.

THE PROBLEM. NFL and NCAAF use different game_id schemes in their context
and results tables, and nothing joins them:

    nfl_game_context    000fc688beb4fc004ecdad115d9adb1c   (hash)
    nfl_game_results    2020_01_ARI_SF                     (season_week_away_home)
    ncaaf_game_context  ncaaf_20260829_Hawaii_Stanford     (slug)
    ncaaf_game_results  cfbd_401403853 AND ncaaf_2026...   (mixed)

Measured 2026-09-21: 0 of 316 nfl_game_context ids exist in
nfl_game_results. So anything keyed on a context id can never be graded.
That is why the sharp-money classification had NFL 0/51 and NCAAF 25/441
flags graded, leaving the whole money-flow gate unvalidated on football
(project_fade_gate_performance_921).

THE FIX. Write nfl/ncaaf_game_results.context_game_id (migration
20260921d) by matching on (game_date, away_team, home_team). Flags keep
storing the context id — which is correct, since at write time the game
has not been played and no result row exists — and the back-reference
lands on the row that appears later.

MATCHING DISCIPLINE. Requires a UNIQUE (date, away, home) hit. Team names
are normalised through the curated alias tables (nfl_team_aliases,
ncaaf_team_aliases) rather than fuzzy-matched, because college football is
exactly where fuzzy matching fails — 'Iowa' and 'Northern Iowa' are
different programs and a substring matcher will weld one game's grade onto
another. Anything that does not resolve uniquely is left NULL and counted,
never guessed.

CLI
  python bridge_football_game_ids.py --dry-run
  python bridge_football_game_ids.py --sport NFL
  python bridge_football_game_ids.py --days 400      # limit the backfill window
"""
from __future__ import annotations
import argparse, os, sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

SPORTS = {
    'NFL':   ('nfl_game_context',   'nfl_game_results',   'nfl_team_aliases'),
    'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results', 'ncaaf_team_aliases'),
}

_alias_cache: dict = {}


def paged(table: str, params: dict) -> list:
    out = []
    for off in range(0, 80000, 1000):
        p = dict(params); p['limit'] = 1000; p['offset'] = off
        p.setdefault('order', 'game_id.asc')
        r = requests.get(f'{SB}/rest/v1/{table}', params=p, headers=H_READ, timeout=40)
        if r.status_code != 200:
            print(f'  ⚠ {table} read failed {r.status_code}: {r.text[:120]}')
            break
        chunk = r.json()
        if not isinstance(chunk, list):
            break
        out += chunk
        if len(chunk) < 1000:
            break
    return out


def alias_map(sport: str) -> dict:
    """{name variant (lower) -> canonical_name}. Exact lookups only."""
    if sport in _alias_cache:
        return _alias_cache[sport]
    _, _, atbl = SPORTS[sport]
    out: dict = {}
    for row in paged(atbl, {'select': '*', 'order': 'canonical_name.asc'}):
        canon = row.get('canonical_name')
        if not canon:
            continue
        variants = [canon, row.get('full_name'), row.get('mascot'), row.get('nickname'),
                    row.get('city'), row.get('location'), row.get('abbrev'),
                    row.get('odds_api_name'), row.get('espn_name')]
        alts = row.get('alt_names')
        if isinstance(alts, list):
            variants += alts
        for v in variants:
            if not v:
                continue
            # First writer wins — a bare mascot ('Wildcats') or bare state
            # name belongs to several programs, and letting a later row
            # overwrite would silently reassign it.
            out.setdefault(' '.join(str(v).split()).lower(), canon)
    _alias_cache[sport] = out
    return out


def canon(sport: str, name) -> str:
    return alias_map(sport).get(' '.join(str(name or '').split()).lower(),
                                ' '.join(str(name or '').split()).lower())


def _result_index(sport: str, days: Optional[int]) -> dict:
    """{(date, canon_away, canon_home) -> [results_game_id, ...]}"""
    _, res_tbl, _ = SPORTS[sport]
    params = {'select': 'game_id,game_date,away_team,home_team'}
    if days:
        since = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
        params['game_date'] = f'gte.{since}'
    idx = defaultdict(list)
    rows = paged(res_tbl, params)
    for row in rows:
        idx[(row.get('game_date'), canon(sport, row.get('away_team')),
             canon(sport, row.get('home_team')))].append(row.get('game_id'))
    return idx, len(rows)


def _from_context(sport: str, days: Optional[int]) -> list:
    """Aliases sourced from <sport>_game_context (the live id scheme)."""
    ctx_tbl, _, _ = SPORTS[sport]
    params = {'select': 'game_id,game_date,away_team,home_team'}
    if days:
        since = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
        params['game_date'] = f'gte.{since}'
    return [{'alias': r.get('game_id'), 'date': r.get('game_date'),
             'away': r.get('away_team'), 'home': r.get('home_team'),
             'source': 'context'} for r in paged(ctx_tbl, params)]


def _orphan_flag_ids(sport: str, known: set) -> set:
    """Flag game_ids that resolve to no context row — the ones needing rescue."""
    out = set()
    for row in paged('line_movement_flags',
                     {'select': 'game_id', 'sport': f'eq.{sport}'}):
        gid = row.get('game_id')
        if gid and gid not in known:
            out.add(gid)
    return out


def _from_line_history(sport: str, orphan_ids: set) -> list:
    """Aliases recoverable from line_history for ids that predate the current
    scheme.

    NCAAF switched id schemes around 2026-09-19, leaving 411 flags across 96
    games whose ids exist in NEITHER context nor results. They survive only
    here, where `matchup` ('Marshall Thundering Herd @ Penn State ...') and
    `commence_time` are enough to rebuild the fixture. Without this path those
    flags are permanently ungradeable — a silent hole in the football record
    exactly where we have least evidence.
    """
    # line_history is the largest table in the database (55.8% of it per the
    # supabase_capacity watchdog). Scanning it — even filtered by sport or
    # commence_time — returns 57014 "canceling statement due to statement
    # timeout", and the recovery path then silently yields almost nothing
    # (it matched 3 rows that way, which reads like "no legacy ids exist"
    # rather than "the query died").
    #
    # So don't scan. We already know exactly which ids need recovering —
    # the flag ids that resolve nowhere — so ask for those by key, in
    # batches. A point lookup on the primary access path instead of a walk
    # over millions of rows.
    # One row per id is all we need (matchup + commence_time are constant for
    # a fixture), so ask for exactly that. Batching with in.() looked cheaper
    # but silently lost 95 of 98 ids: line_history holds dozens of rows per
    # game — book x market x capture time — so a 40-id batch blew past any
    # sane limit and returned only the first few games' worth. A truncated
    # batch is indistinguishable from "these ids don't exist", which is how
    # it read as "no legacy ids recoverable".
    if not orphan_ids:
        return []
    rows = []
    for gid in orphan_ids:
        r = requests.get(f'{SB}/rest/v1/line_history', headers=H_READ, timeout=25,
                         params={'select': 'game_id,matchup,commence_time',
                                 'game_id': f'eq.{gid}', 'limit': 1})
        if r.status_code != 200:
            continue
        chunk = r.json()
        if isinstance(chunk, list) and chunk:
            rows += chunk
    seen = {}
    for r in rows:
        gid = r.get('game_id')
        m = str(r.get('matchup') or '')
        ct = str(r.get('commence_time') or '')
        if not gid or '@' not in m or not ct or gid in seen:
            continue
        away, home = [s.strip() for s in m.split('@', 1)]
        try:
            dt = datetime.fromisoformat(ct.replace('Z', '+00:00'))
            gdate = (dt.astimezone(timezone.utc) - timedelta(hours=4)).date().isoformat()
        except ValueError:
            continue
        seen[gid] = {'alias': gid, 'date': gdate, 'away': away, 'home': home,
                     'source': 'line_history'}
    return list(seen.values())


def run_sport(sport: str, days: Optional[int], dry_run: bool) -> int:
    ridx, n_res = _result_index(sport, days)
    ctx_rows = _from_context(sport, days)
    orphans = _orphan_flag_ids(sport, {c['alias'] for c in ctx_rows})
    cands = ctx_rows + _from_line_history(sport, orphans)
    if orphans:
        print(f'  {len(orphans)} flag id(s) resolve to no context row — '
              f'attempting line_history rescue')
    # Context wins over line_history when both carry the same id.
    dedup = {}
    for c in cands:
        dedup.setdefault(c['alias'], c)
    cands = list(dedup.values())
    print(f'\n{sport}: results={n_res}  candidate aliases={len(cands)}')

    rows, stats = [], defaultdict(int)
    for c in cands:
        key = (c['date'], canon(sport, c['away']), canon(sport, c['home']))
        hits = ridx.get(key) or []
        if len(hits) == 1:
            rows.append({'sport': sport, 'alias_game_id': c['alias'],
                         'results_game_id': hits[0], 'game_date': c['date'],
                         'away_team': str(c['away'])[:80],
                         'home_team': str(c['home'])[:80], 'source': c['source']})
            stats[f'matched_{c["source"]}'] += 1
        elif len(hits) > 1:
            # Duplicate result rows for one fixture — the duplicate-ingest
            # class (project_ncaaf_ingest_duplicate_902). Picking one would
            # attach grades to an arbitrary half of the split.
            stats['ambiguous'] += 1
        else:
            stats['no_result'] += 1

    print(f'  matched: context={stats["matched_context"]} '
          f'line_history={stats["matched_line_history"]} · '
          f'ambiguous={stats["ambiguous"]} no_result_row={stats["no_result"]}')
    if dry_run or not rows:
        if dry_run:
            print(f'  [DRY] would upsert {len(rows)} aliases')
            for r in rows[:3]:
                print(f'      {r["alias_game_id"][:28]} -> {r["results_game_id"]} '
                      f'({r["source"]})')
        return len(rows)

    ok = 0
    for i in range(0, len(rows), 300):
        chunk = rows[i:i + 300]
        r = requests.post(f'{SB}/rest/v1/football_game_id_alias'
                          '?on_conflict=sport,alias_game_id',
                          headers={**H_WRITE,
                                   'Prefer': 'resolution=merge-duplicates,return=minimal'},
                          json=chunk, timeout=40)
        if r.status_code in (200, 201, 204):
            ok += len(chunk)
        else:
            print(f'  ⚠ upsert failed {r.status_code}: {r.text[:160]}')
    print(f'  upserted {ok}')
    return ok


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=list(SPORTS.keys()))
    p.add_argument('--days', type=int, default=None,
                   help='only bridge results newer than N days (default: all)')
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    print(f'=== bridge_football_game_ids {"(DRY)" if a.dry_run else ""} ===')
    total = 0
    for sp in ([a.sport] if a.sport else list(SPORTS.keys())):
        total += run_sport(sp, a.days, a.dry_run)
    print(f'\n  total bridged: {total}')


if __name__ == '__main__':
    main()
