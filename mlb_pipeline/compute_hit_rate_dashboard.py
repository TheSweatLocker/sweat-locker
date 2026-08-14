"""Rolling hit-rate dashboard writer (2026-08-14).

Session A of the pre-launch monitoring infrastructure.

Computes rolling W/L hit rates per (sport, surface, tier, window_days),
upserts to hit_rate_snapshots. Runs nightly; every subsequent read
(alert engine, CLI reporter, app-embedded dashboard) queries this
pre-computed table rather than re-aggregating raw picks.

Universal architecture — adding a new sport = ONE entry in SPORT_CONFIG.
Everything else (surfaces, tiers, windows, alerting, reporting) works
automatically.

CONCEPTS

  sport    : MLB / NFL / NCAAF / NCAAB / NBA / NHL / UFC
  surface  : where the pick lives —
             'jerry_game'    : game-level LLM verdict (jerry_reads)
             'jerry_prop'    : prop-level LLM verdict (prop_jerry_reads)
             'pipeline_prop' : rule-based prop pick (mlb_pipeline_props etc.)
             'primary_play'  : game_context primary_play field
             'dawg'          : daily_dawg (MLB only for now)
  tier     : PRIME / STRONG / LEAN / READ / COVERAGE / SKIP, or NULL for
             surface-wide aggregate (which we always also compute)
  window   : lookback in days — 7, 30, 90

For each (sport × surface × tier × window) combo we count W/L/P/NO_ACTION
across the window, compute hit_rate = 100 * W / (W+L), upsert one row.

Idempotent — snapshot_date is today; unique key means re-runs update.

CLI:
    python compute_hit_rate_dashboard.py [--date YYYY-MM-DD] [--sport MLB|ALL]
                                          [--windows 7,30,90] [--dry-run]
"""
from __future__ import annotations
import argparse, os, sys
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Iterable

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# ─── PER-SPORT CONFIG ────────────────────────────────────────────────
# Each sport declares which surfaces are available + which tables to
# read from. Add a new sport = add one entry. Everything below
# (compute, alert, report) works uniformly across sports.
#
# For each surface: (table_name, tier_column, result_column, extra_filter)
# extra_filter is a PostgREST param dict merged into the base query.

TIER_LEVELS = ['PRIME', 'STRONG', 'LEAN', 'READ', 'COVERAGE', 'SKIP']
DEFAULT_WINDOWS = [7, 30, 90]

SPORT_CONFIG = {
    'MLB': {
        'jerry_game':     {'table': 'jerry_reads',        'tier_col': None,   'result_col': 'result', 'sport_filter': 'eq.MLB'},
        'jerry_prop':     {'table': 'prop_jerry_reads',   'tier_col': None,   'result_col': 'result', 'sport_filter': 'eq.MLB'},
        'pipeline_prop':  {'table': 'mlb_pipeline_props', 'tier_col': 'tier', 'result_col': 'result', 'sport_filter': None},
        'dawg':           {'table': 'daily_dawg',         'tier_col': 'tier', 'result_col': 'result', 'sport_filter': None},
    },
    'NFL': {
        'jerry_game':     {'table': 'jerry_reads',        'tier_col': None,   'result_col': 'result', 'sport_filter': 'eq.NFL'},
        'jerry_prop':     {'table': 'prop_jerry_reads',   'tier_col': None,   'result_col': 'result', 'sport_filter': 'eq.NFL'},
        'pipeline_prop':  {'table': 'nfl_pipeline_props', 'tier_col': 'tier', 'result_col': 'result', 'sport_filter': None},
    },
    'NCAAF': {
        'jerry_game':     {'table': 'jerry_reads',        'tier_col': None,   'result_col': 'result', 'sport_filter': 'eq.NCAAF'},
        'pipeline_prop':  {'table': 'ncaaf_pipeline_props', 'tier_col': 'tier', 'result_col': 'result', 'sport_filter': None},
    },
    'NCAAB': {
        'jerry_game':     {'table': 'jerry_reads',        'tier_col': None,   'result_col': 'result', 'sport_filter': 'eq.NCAAB'},
    },
    'NBA': {
        'jerry_game':     {'table': 'jerry_reads',        'tier_col': None,   'result_col': 'result', 'sport_filter': 'eq.NBA'},
    },
    'NHL': {
        'jerry_game':     {'table': 'jerry_reads',        'tier_col': None,   'result_col': 'result', 'sport_filter': 'eq.NHL'},
    },
    'UFC': {
        'jerry_game':     {'table': 'jerry_reads',        'tier_col': None,   'result_col': 'result', 'sport_filter': 'eq.UFC'},
    },
}


def _et_today() -> date:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def _paginate(url: str, params: dict, page: int = 1000) -> list:
    """PostgREST pagination via offset. Bounded 5k rows / call."""
    out = []; offset = 0
    while offset < 5000:
        p = {**params, 'limit': str(page), 'offset': str(offset)}
        r = requests.get(url, headers=H_READ, params=p, timeout=30)
        if r.status_code != 200:
            return out
        rows = r.json()
        if not isinstance(rows, list) or not rows: break
        out.extend(rows)
        if len(rows) < page: break
        offset += page
    return out


def load_surface_rows(sport: str, surface: str, cfg: dict,
                      window_start: date, window_end: date) -> list:
    """Pull graded rows for one surface within [window_start, window_end]."""
    date_col = 'game_date'
    select_cols = ['id', date_col, cfg['result_col']]
    if cfg.get('tier_col'):
        select_cols.append(cfg['tier_col'])
    # jerry_reads / prop_jerry_reads also carry conviction; useful for aggregate
    params = {
        f'{date_col}': f'gte.{window_start.isoformat()}',
        'select': ','.join(select_cols),
    }
    # game_date <= end done via extra filter in URL (PostgREST doesn't dedup dict keys)
    # Workaround: pass as `and=(...)`
    params['and'] = f'({date_col}.gte.{window_start.isoformat()},' \
                    f'{date_col}.lte.{window_end.isoformat()})'
    params.pop(date_col, None)
    if cfg.get('sport_filter'):
        params['sport'] = cfg['sport_filter']
    return _paginate(f'{SB}/rest/v1/{cfg["table"]}', params)


def bucketize(rows: list, cfg: dict) -> dict:
    """Group rows by tier → counts of W/L/P/NO_ACTION.
    Returns dict: {tier_or_None: (w, l, p, na)}. None key = surface-wide aggregate."""
    tier_col = cfg.get('tier_col')
    buckets = {None: [0, 0, 0, 0]}  # aggregate

    for row in rows:
        res = (row.get(cfg['result_col']) or '').strip()
        # Normalize result variants
        rl = res.lower()
        idx = None
        if rl == 'win':                     idx = 0
        elif rl == 'loss':                  idx = 1
        elif rl == 'push':                  idx = 2
        elif rl in ('no_action', 'na', ''): idx = 3
        elif rl in ('void',):               continue  # skip voided
        elif rl == 'ungradeable':           continue  # skip ungradeable from counts
        else: continue

        # Aggregate always counted
        buckets[None][idx] += 1

        if tier_col:
            tier = row.get(tier_col)
            if tier:
                buckets.setdefault(tier, [0, 0, 0, 0])
                buckets[tier][idx] += 1

    return buckets


def upsert_snapshot(snapshot_date: date, sport: str, surface: str,
                    tier: Optional[str], window_days: int,
                    counts: list, dry_run: bool = False) -> bool:
    w, l, p, na = counts
    sample = w + l
    hit_rate = round(100 * w / sample, 2) if sample > 0 else None
    payload = {
        'snapshot_date': snapshot_date.isoformat(),
        'sport': sport,
        'surface': surface,
        'tier': tier,  # None → NULL row (surface-wide)
        'window_days': window_days,
        'wins': w, 'losses': l, 'pushes': p, 'no_action': na,
        'hit_rate': hit_rate,
        'sample_n': sample,
        'computed_at': datetime.now(timezone.utc).isoformat(),
    }
    if dry_run:
        tier_str = tier or 'ALL'
        print(f'  [DRY] {sport:6} {surface:15} tier={tier_str:9} w{window_days:2d}: '
              f'{w:3}-{l:3}  hit={hit_rate}  n={sample}')
        return True
    # PostgREST NULL in URL for tier means we need to use conflict_target params
    # Workaround: use POST with Prefer merge-duplicates. Since the unique
    # index includes tier column, matching NULL works via Postgres semantics
    # when we omit tier from payload — but here we're setting explicit NULL.
    # Cleanest: DELETE then INSERT on exact key match. Simpler: use the
    # on_conflict param.
    conflict_key = 'snapshot_date,sport,surface,tier,window_days'
    pr = requests.post(
        f'{SB}/rest/v1/hit_rate_snapshots?on_conflict={conflict_key}',
        headers=H_WRITE, json=payload, timeout=15)
    if pr.status_code in (200, 201, 204): return True
    print(f'  ✗ upsert failed: {pr.status_code} {pr.text[:200]}')
    return False


def run(snapshot_date: date, sports: Iterable[str], windows: list,
        dry_run: bool = False) -> int:
    print(f'=== hit_rate_dashboard · date={snapshot_date} · sports={list(sports)} · '
          f'windows={windows} ===')
    written = 0
    for sport in sports:
        cfg_map = SPORT_CONFIG.get(sport)
        if not cfg_map:
            print(f'  {sport}: not registered — skip'); continue
        for surface, cfg in cfg_map.items():
            for w_days in windows:
                w_start = snapshot_date - timedelta(days=w_days)
                # window is INCLUSIVE of yesterday; today's games not yet graded
                w_end = snapshot_date - timedelta(days=1)
                rows = load_surface_rows(sport, surface, cfg, w_start, w_end)
                buckets = bucketize(rows, cfg)
                for tier, counts in buckets.items():
                    ok = upsert_snapshot(snapshot_date, sport, surface, tier,
                                          w_days, counts, dry_run=dry_run)
                    if ok: written += 1
    print(f'\n{"[DRY] would write" if dry_run else "wrote"} {written} snapshots')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='snapshot date (YYYY-MM-DD); defaults today ET')
    p.add_argument('--sport', default='ALL', help='MLB/NFL/... or ALL')
    p.add_argument('--windows', default='7,30,90')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    sd = date.fromisoformat(args.date) if args.date else _et_today()
    sports = list(SPORT_CONFIG.keys()) if args.sport == 'ALL' else [args.sport]
    windows = [int(x) for x in args.windows.split(',')]
    run(sd, sports, windows, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
