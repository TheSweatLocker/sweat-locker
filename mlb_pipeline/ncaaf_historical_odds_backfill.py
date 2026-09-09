"""ncaaf_historical_odds_backfill — populate close_home_ml / close_away_ml /
close_spread / close_total on ncaaf_game_results for 2022-2025.

Audit 2026-09-08 found NCAAF close-line coverage at 17-21% ML and 37-41%
spread across 15,342 historical games. This blocks:
  - LR training on the full corpus (trainer refuses without features)
  - Pattern discovery buckets (n<30 in most fade buckets)
  - Retrospective backtests of the ensemble against real market lines

Source: CFBD /lines endpoint (already have api key, already used for
returning production, rankings, SoR, etc). One request per year returns
every game with every provider's lines.

Provider preference (matches modern US market at closing):
  1. DraftKings (largest US sportsbook; closes tight)
  2. Bovada (offshore; historically wide coverage on smaller games)
  3. ESPN Bet
  4. Consensus / first available

Idempotent: only patches rows where the target column is NULL. Existing
close_* values are preserved (don't overwrite live-captured lines with
retro pulls).

USAGE:
    python ncaaf_historical_odds_backfill.py                    # 2022-2025
    python ncaaf_historical_odds_backfill.py --year 2024        # one season
    python ncaaf_historical_odds_backfill.py --start 2022 --end 2025
    python ncaaf_historical_odds_backfill.py --dry-run          # no writes
"""
from __future__ import annotations
import argparse, os, sys, time
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
CFBD_KEY = os.environ['CFBD_API_KEY']
CFBD_BASE = 'https://api.collegefootballdata.com'

H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json',
       'Prefer': 'return=minimal'}
H_CFBD = {'Authorization': f'Bearer {CFBD_KEY}', 'Accept': 'application/json'}

PROVIDER_PRIORITY = ('DraftKings', 'Bovada', 'ESPN Bet', 'consensus')


def _pick_line(lines: list) -> dict | None:
    """From a game's lines array, return the best-provider consolidated
    line as {spread, over_under, home_ml, away_ml}. Missing fields are
    None. Returns None if no provider has any usable field."""
    if not lines: return None
    # Bucket by provider for lookup
    by_prov = {ln.get('provider'): ln for ln in lines if isinstance(ln, dict)}
    picked = {}
    # For each target field, take the first provider that has it
    for field, key in [('spread', 'spread'),
                        ('over_under', 'overUnder'),
                        ('home_ml', 'homeMoneyline'),
                        ('away_ml', 'awayMoneyline')]:
        for prov in PROVIDER_PRIORITY:
            ln = by_prov.get(prov)
            if ln and ln.get(key) is not None:
                picked[field] = ln[key]
                break
        else:
            # nothing in priority list — take any provider that has it
            for ln in lines:
                if isinstance(ln, dict) and ln.get(key) is not None:
                    picked[field] = ln[key]
                    break
            else:
                picked[field] = None
    if all(v is None for v in picked.values()): return None
    return picked


def _fetch_cfbd_year(year: int) -> list[dict]:
    """One call returns every game with lines for the year (regular +
    postseason). Retries once on network hiccup."""
    all_rows = []
    for st in ('regular', 'postseason'):
        for attempt in range(2):
            try:
                r = requests.get(f'{CFBD_BASE}/lines', headers=H_CFBD,
                                 params={'year': year, 'seasonType': st},
                                 timeout=60)
                if r.status_code == 200:
                    all_rows.extend(r.json() or [])
                    break
                print(f'  ⚠ CFBD {year} {st} status={r.status_code} attempt={attempt+1}')
                time.sleep(2)
            except Exception as e:
                print(f'  ⚠ CFBD {year} {st} err={e} attempt={attempt+1}')
                time.sleep(2)
    return all_rows


def _fetch_missing_rows(year: int) -> dict[str, dict]:
    """Return {game_id: existing_row} for rows in year with ANY null close_*.
    Only these will be considered for patching (skip rows already complete)."""
    out = {}
    # PostgREST OR filter on close_ fields being null
    or_clause = (
        'close_home_ml.is.null,close_away_ml.is.null,'
        'close_spread.is.null,close_total.is.null'
    )
    offset = 0
    while True:
        r = requests.get(
            f'{SB}/rest/v1/ncaaf_game_results', headers=H_R,
            params={'season': f'eq.{year}', 'or': f'({or_clause})',
                    'select': 'game_id,close_home_ml,close_away_ml,close_spread,close_total',
                    'order': 'game_id',
                    'limit': '1000', 'offset': str(offset)},
            timeout=30,
        )
        if r.status_code != 200:
            print(f'  ⚠ SB fetch year={year} offset={offset} status={r.status_code}')
            break
        rows = r.json() or []
        if not rows: break
        for row in rows: out[row['game_id']] = row
        if len(rows) < 1000: break
        offset += 1000
    return out


def backfill_year(year: int, dry_run: bool = False) -> dict:
    print(f'\n=== NCAAF {year} historical odds backfill ===')
    cfbd_games = _fetch_cfbd_year(year)
    print(f'  CFBD returned {len(cfbd_games)} games w/ lines block')
    missing = _fetch_missing_rows(year)
    print(f'  SB has {len(missing)} rows with at least one null close_*')

    stats = {'checked': 0, 'patched': 0, 'no_lines': 0, 'no_match': 0,
             'field_fills': {'spread': 0, 'total': 0, 'home_ml': 0, 'away_ml': 0}}

    for g in cfbd_games:
        cfbd_id = g.get('id')
        if cfbd_id is None: continue
        key = f'cfbd_{cfbd_id}'
        row = missing.get(key)
        if row is None: continue  # already fully populated OR not in our results table
        stats['checked'] += 1
        picked = _pick_line(g.get('lines') or [])
        if not picked:
            stats['no_lines'] += 1
            continue
        # Only fill NULL fields — never overwrite existing values
        patch = {}
        if row.get('close_spread') is None and picked.get('spread') is not None:
            patch['close_spread'] = picked['spread']
            stats['field_fills']['spread'] += 1
        if row.get('close_total') is None and picked.get('over_under') is not None:
            patch['close_total'] = picked['over_under']
            stats['field_fills']['total'] += 1
        if row.get('close_home_ml') is None and picked.get('home_ml') is not None:
            patch['close_home_ml'] = picked['home_ml']
            stats['field_fills']['home_ml'] += 1
        if row.get('close_away_ml') is None and picked.get('away_ml') is not None:
            patch['close_away_ml'] = picked['away_ml']
            stats['field_fills']['away_ml'] += 1
        if not patch: continue
        if dry_run:
            stats['patched'] += 1
            continue
        pr = requests.patch(
            f'{SB}/rest/v1/ncaaf_game_results', headers=H_W,
            params={'game_id': f'eq.{key}'},
            json=patch, timeout=15,
        )
        if pr.status_code < 300:
            stats['patched'] += 1
        else:
            print(f'  ⚠ patch fail {key}: {pr.status_code} {pr.text[:100]}')

    # Rows in SB we couldn't match to CFBD
    stats['no_match'] = len(missing) - stats['checked']
    print(f'  patched: {stats["patched"]}  no_lines: {stats["no_lines"]}  '
          f'unmatched: {stats["no_match"]}')
    print(f'  fills: spread={stats["field_fills"]["spread"]}  '
          f'total={stats["field_fills"]["total"]}  '
          f'home_ml={stats["field_fills"]["home_ml"]}  '
          f'away_ml={stats["field_fills"]["away_ml"]}')
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', type=int, help='single season')
    ap.add_argument('--start', type=int, default=2022)
    ap.add_argument('--end', type=int, default=2025)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    years = [args.year] if args.year else list(range(args.start, args.end + 1))
    print(f'== ncaaf_historical_odds_backfill == years={years} dry_run={args.dry_run}')
    total = {'patched': 0, 'checked': 0, 'no_lines': 0}
    for y in years:
        s = backfill_year(y, dry_run=args.dry_run)
        for k in ('patched', 'checked', 'no_lines'):
            total[k] += s[k]

    print(f'\n== TOTAL == checked={total["checked"]}  '
          f'patched={total["patched"]}  no_lines={total["no_lines"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
