"""Cohort tag record rollup — NFL + NCAAF (2026-09-10).

Re-derives cohort_tags for every historical *_game_results row using the SAME
rules as nfl_game_context.compute_cohort_tags / ncaaf_game_context, then tallies
wins-losses-pushes against spread_result + total_result. Writes to cohort_tag_records
so the app surfaces "Home Favorite · 45-40-2 ATS (52.9%)" under each chip.

Runs nightly (add to workflow after NFL/NCAAF result grading). Idempotent —
truncates + reinserts on each run so mid-season definition tweaks stay in sync.

Convention (both NFL + NCAAF results tables):
  close_spread > 0  →  home team favored by that many
  close_spread < 0  →  home team is that big of a dog
  spread_result IN ('home_covered', 'away_covered', 'push')
  total_result   IN ('over', 'under', 'push')

Feedback anchors:
  project_cohort_signal_ux_909, project_badge_record_ledger_909
"""
from __future__ import annotations
import os, sys
from datetime import datetime, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _line in _env.read_text().split('\n'):
        if '=' in _line and not _line.startswith('#'):
            _k, _v = _line.split('=', 1); os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# NFL division map — mirror nfl_game_context.NFL_DIVISION (32 teams).
NFL_DIVISION = {
    'BUF':'AFC East','MIA':'AFC East','NE':'AFC East','NYJ':'AFC East',
    'BAL':'AFC North','CIN':'AFC North','CLE':'AFC North','PIT':'AFC North',
    'HOU':'AFC South','IND':'AFC South','JAX':'AFC South','TEN':'AFC South',
    'DEN':'AFC West','KC':'AFC West','LV':'AFC West','LAC':'AFC West',
    'DAL':'NFC East','NYG':'NFC East','PHI':'NFC East','WAS':'NFC East',
    'CHI':'NFC North','DET':'NFC North','GB':'NFC North','MIN':'NFC North',
    'ATL':'NFC South','CAR':'NFC South','NO':'NFC South','TB':'NFC South',
    'ARI':'NFC West','LA':'NFC West','SEA':'NFC West','SF':'NFC West',
}


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(v) if v is not None else None
    except (TypeError, ValueError): return None


def nfl_tags(row: dict) -> list[str]:
    """Mirror nfl_game_context.compute_cohort_tags."""
    tags = []
    spread = _f(row.get('close_spread'))
    total = _f(row.get('close_total'))
    roof = (row.get('roof') or '').lower()
    temp = _i(row.get('temp'))
    wind = _i(row.get('wind'))
    home = row.get('home_team'); away = row.get('away_team')
    if spread is not None and spread <= -7.0:
        tags.append('nfl_heavy_home_dog')
    if roof in ('outdoors', 'open') and (
        (temp is not None and temp <= 40) or (wind is not None and wind >= 12)
    ):
        tags.append('nfl_outdoor_under')
    if home and away and NFL_DIVISION.get(home) == NFL_DIVISION.get(away) and NFL_DIVISION.get(home):
        tags.append('nfl_div_home_cover')
    if roof in ('dome', 'closed') and total is not None and total >= 47:
        tags.append('nfl_dome_over')
    if spread is not None and spread > 0:
        tags.append('nfl_home_fav')
    return tags


def ncaaf_tags(row: dict) -> list[str]:
    """Mirror ncaaf_game_context tag block (ncaaf_game_context.py:903-930)."""
    tags = []
    sp = _f(row.get('close_spread')); tot = _f(row.get('close_total'))
    if sp is not None and sp >= 7.0: tags.append('ncaaf_heavy_home_dog')
    if sp is not None and sp <= -20.0: tags.append('ncaaf_heavy_home_fav')
    if sp is not None and sp < 0: tags.append('ncaaf_home_fav')
    if tot is not None and tot >= 60: tags.append('ncaaf_shootout')
    if tot is not None and tot <= 42: tags.append('ncaaf_grinder')
    return tags


def _tally_ats(tag: str, tags_here: list, sp_res: str, acc: dict):
    """Some tags are HOME-side ATS bets (nfl_home_fav, nfl_heavy_home_dog,
    ncaaf_home_fav, ncaaf_heavy_home_fav, nfl_div_home_cover); some are just
    situational markers (nfl_dome_over → total-side; others). Track the
    ATS wager side that the tag NAME implies so the record answers 'if I
    followed this tag as an ATS play, what would my record be?'.
    """
    if tag not in tags_here: return
    # Which side of ATS does this tag correspond to?
    HOME_ATS = {'nfl_home_fav','nfl_heavy_home_dog','nfl_div_home_cover',
                'ncaaf_home_fav','ncaaf_heavy_home_fav','ncaaf_heavy_home_dog'}
    # None of our current tags are AWAY-ATS by name.
    if tag in HOME_ATS:
        if sp_res == 'home_covered':  acc['w'] += 1
        elif sp_res == 'away_covered': acc['l'] += 1
        elif sp_res == 'push':         acc['p'] += 1


def _tally_total(tag: str, tags_here: list, tot_res: str, acc: dict):
    if tag not in tags_here: return
    OVER = {'nfl_dome_over','ncaaf_shootout'}
    UNDER = {'nfl_outdoor_under','ncaaf_grinder'}
    if tag in OVER:
        if tot_res == 'over':  acc['w'] += 1
        elif tot_res == 'under': acc['l'] += 1
        elif tot_res == 'push':  acc['p'] += 1
    elif tag in UNDER:
        if tot_res == 'under': acc['w'] += 1
        elif tot_res == 'over':  acc['l'] += 1
        elif tot_res == 'push':  acc['p'] += 1


def fetch_all(table: str, select: str) -> list[dict]:
    """Page through PostgREST. Requires the query to be indexable."""
    out = []
    step = 1000; off = 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H_READ, params={
            'select': select,
            'spread_result': 'not.is.null',
            'order': 'game_date.asc',
            'offset': str(off), 'limit': str(step),
        }, timeout=30)
        if r.status_code != 200:
            print(f'  fetch {table} off={off} → {r.status_code} {r.text[:200]}'); break
        rows = r.json()
        if not rows: break
        out.extend(rows)
        if len(rows) < step: break
        off += step
    return out


# NCAAF results schema has no roof/temp/wind/div_game — slim its select.
_SELECT_BY_TABLE = {
    'nfl_game_results':   'season,game_date,home_team,away_team,close_spread,close_total,roof,temp,wind,div_game,spread_result,total_result',
    'ncaaf_game_results': 'season,game_date,home_team,away_team,close_spread,close_total,spread_result,total_result',
}


def rollup(sport: str, table: str, tag_fn) -> list[dict]:
    print(f'{sport}: fetching {table} …')
    rows = fetch_all(table, _SELECT_BY_TABLE[table])
    print(f'  {len(rows)} graded rows')
    ats_acc: dict[str, dict[str, int]] = {}
    tot_acc: dict[str, dict[str, int]] = {}
    for row in rows:
        tgs = tag_fn(row)
        for t in tgs:
            ats_acc.setdefault(t, {'w':0,'l':0,'p':0})
            tot_acc.setdefault(t, {'w':0,'l':0,'p':0})
            _tally_ats(t, tgs, row.get('spread_result'), ats_acc[t])
            _tally_total(t, tgs, row.get('total_result'), tot_acc[t])
    out = []
    for tag, acc in ats_acc.items():
        n = acc['w'] + acc['l']
        if n == 0: continue
        out.append({
            'sport': sport, 'tag': tag, 'market': 'ats', 'side': 'primary',
            'wins': acc['w'], 'losses': acc['l'], 'pushes': acc['p'],
            'hit_rate': round(acc['w'] * 100.0 / n, 2), 'sample_n': n,
            'season_scope': 'lifetime',
            'updated_at': datetime.now(timezone.utc).isoformat(),
        })
    for tag, acc in tot_acc.items():
        n = acc['w'] + acc['l']
        if n == 0: continue
        out.append({
            'sport': sport, 'tag': tag, 'market': 'total', 'side': 'primary',
            'wins': acc['w'], 'losses': acc['l'], 'pushes': acc['p'],
            'hit_rate': round(acc['w'] * 100.0 / n, 2), 'sample_n': n,
            'season_scope': 'lifetime',
            'updated_at': datetime.now(timezone.utc).isoformat(),
        })
    return out


def upsert_records(rows: list[dict]) -> None:
    if not rows:
        print('  nothing to write'); return
    # Upsert via on_conflict=sport,tag,market,side,season_scope
    r = requests.post(f'{SB}/rest/v1/cohort_tag_records',
        headers={**H_WRITE, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
        params={'on_conflict': 'sport,tag,market,side,season_scope'},
        json=rows, timeout=30)
    if r.status_code in (200, 201, 204):
        print(f'  ✓ upserted {len(rows)} rows')
    else:
        print(f'  ✗ upsert failed: {r.status_code} {r.text[:300]}')


def main():
    print('=== cohort_tag_records rollup ===')
    all_rows = []
    all_rows += rollup('NFL',   'nfl_game_results',   nfl_tags)
    all_rows += rollup('NCAAF', 'ncaaf_game_results', ncaaf_tags)
    print(f'\nwriting {len(all_rows)} total records …')
    upsert_records(all_rows)
    # Print human summary
    print('\n--- SUMMARY ---')
    for r in sorted(all_rows, key=lambda x: (x['sport'], -x['hit_rate'])):
        print(f"  {r['sport']:5s} {r['tag']:28s} {r['market']:5s} "
              f"{r['wins']}-{r['losses']}-{r['pushes']}  "
              f"{r['hit_rate']}% (n={r['sample_n']})")


if __name__ == '__main__':
    main()
