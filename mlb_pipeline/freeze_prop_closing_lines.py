#!/usr/bin/env python3
"""freeze_prop_closing_lines — snapshot true closing lines on props at T-Xmin.

Existing book_line / book_over_odds / book_under_odds on
mlb_pipeline_props (+ nfl_pipeline_props) get overwritten by every sweep,
so we can't measure "did we beat the close?" without a separate
snapshot. This script fetches Odds API once inside the freeze window and
writes close_prop_line / close_over_odds / close_under_odds /
close_locked_at / close_source on rows that haven't been frozen yet.

Idempotent: skips rows where close_locked_at is already set. Cheap
outside game windows: filters on today's PRIME/STRONG only.

Cron: fast cron every 5-10 min (same cadence as freeze_closing_lines.py
for sides). At T-5min for MLB / T-15min for NFL the freeze fires.

Sport-universal via SPORT_CONFIG.

CLI
  python freeze_prop_closing_lines.py                # today, all sports
  python freeze_prop_closing_lines.py --sport MLB
  python freeze_prop_closing_lines.py --dry-run
"""

from __future__ import annotations
import argparse, os, sys, datetime as dt
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

SB  = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


# Freeze window: minutes before commence when we snap.
# `odds_sport` is the Odds API sport key used to look up commence_time
# for each game (no persistent commence column exists on mlb_game_context).
SPORT_CONFIG = {
    'MLB': {
        'table':       'mlb_pipeline_props',
        'odds_sport':  'baseball_mlb',
        'freeze_min':  5,
        'gen_module':  'generate_props',
    },
    'NFL': {
        'table':       'nfl_pipeline_props',
        'odds_sport':  'americanfootball_nfl',
        'freeze_min':  15,
        'gen_module':  'nfl_generate_props',
    },
}


def _norm_name(s: str) -> str:
    """Match the accent-folding used by generate_props book fetcher."""
    import unicodedata
    return unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower().strip()


def _freeze_ready_via_odds_api(sport: str) -> tuple[dict, dt.datetime, dt.datetime]:
    """Fetch commence_time per event from Odds API.

    Returns (event_id_by_matchup, now_utc, cutoff_utc). We match props to
    events via the game_id our pipeline stores (Odds API event ID for
    Odds-API-sourced games). If the ID scheme doesn't line up, downstream
    caller falls back to matching by home_team/away_team from the event.
    """
    cfg = SPORT_CONFIG[sport]
    api_key = os.environ.get('ODDS_API_KEY')
    if not api_key:
        raise RuntimeError('ODDS_API_KEY missing — cannot resolve commence times')
    now_utc = dt.datetime.now(dt.timezone.utc)
    cutoff  = now_utc + dt.timedelta(minutes=cfg['freeze_min'])
    r = requests.get(
        f'https://api.the-odds-api.com/v4/sports/{cfg["odds_sport"]}/events',
        params={'apiKey': api_key,
                'commenceTimeFrom': now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')},
        timeout=15,
    )
    r.raise_for_status()
    events = r.json() or []
    # Two lookups: by odds-api id, and by matchup string (home @ away).
    by_id, by_matchup = {}, {}
    for ev in events:
        t = ev.get('commence_time')
        if not t: continue
        try:
            gt = dt.datetime.fromisoformat(t.replace('Z', '+00:00'))
        except Exception:
            continue
        by_id[ev.get('id')] = gt
        matchup = f'{ev.get("away_team","")} @ {ev.get("home_team","")}'
        by_matchup[matchup] = gt
    return {'by_id': by_id, 'by_matchup': by_matchup}, now_utc, cutoff


def _freeze_ready_games(sport: str, today: str) -> set[str]:
    """Return game_ids in mlb_pipeline_props whose commence <= cutoff."""
    lookups, now_utc, cutoff = _freeze_ready_via_odds_api(sport)
    cfg = SPORT_CONFIG[sport]
    r = requests.get(
        f'{SB}/rest/v1/{cfg["table"]}',
        params={'select': 'game_id,matchup', 'game_date': f'eq.{today}'},
        headers=H_READ, timeout=30,
    )
    r.raise_for_status()
    ready = set()
    for row in r.json():
        gid = row.get('game_id')
        gt = lookups['by_id'].get(gid) or lookups['by_matchup'].get(row.get('matchup', ''))
        if gt is None:
            continue
        if gt <= cutoff and gt >= now_utc - dt.timedelta(minutes=30):
            ready.add(gid)
    return ready


def _pending_props(sport: str, today: str, game_ids: set[str]) -> list[dict]:
    """Rows that are PRIME/STRONG, in a ready game, and not yet frozen."""
    if not game_ids:
        return []
    cfg = SPORT_CONFIG[sport]
    # Chunk game_ids to avoid overly long URL — 50 per batch
    out = []
    game_ids_l = sorted(game_ids)
    for i in range(0, len(game_ids_l), 50):
        chunk = game_ids_l[i:i+50]
        ids = ','.join(f'"{g}"' for g in chunk)
        r = requests.get(
            f'{SB}/rest/v1/{cfg["table"]}',
            params={
                'select': 'id,game_id,player_name,prop_type,prop_line,direction,tier',
                'game_date': f'eq.{today}',
                'tier': 'in.(PRIME,STRONG)',
                'close_locked_at': 'is.null',
                'game_id': f'in.({ids})',
            },
            headers=H_READ, timeout=30,
        )
        r.raise_for_status()
        out.extend(r.json())
    return out


def _fetch_market_lines(sport: str, market: str) -> dict:
    """Delegate to generate_props' book-line fetcher for the given market."""
    cfg = SPORT_CONFIG[sport]
    import importlib
    mod = importlib.import_module(cfg['gen_module'])
    fn = getattr(mod, 'fetch_book_lines_for_market', None)
    if fn is None:
        print(f'  {sport}: {cfg["gen_module"]}.fetch_book_lines_for_market missing — skip')
        return {}
    today = dt.date.today().isoformat()
    return fn(today, market)


def _write_freeze(table: str, row_id: int, line: float, over: int | None,
                  under: int | None, source: str | None, dry: bool) -> None:
    payload = {
        'close_prop_line':  line,
        'close_over_odds':  over,
        'close_under_odds': under,
        'close_source':     source,
        'close_locked_at':  dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if dry:
        print(f'  DRY  id={row_id}  {payload}')
        return
    r = requests.patch(
        f'{SB}/rest/v1/{table}?id=eq.{row_id}',
        headers=H_WRITE, json=payload, timeout=15,
    )
    if not r.ok:
        print(f'  ✗ id={row_id} freeze failed: {r.status_code} {r.text[:200]}')


def _resolve_prop_market(sport: str, prop_type: str) -> str | None:
    """Map internal prop_type → Odds API market key (same table as generate_props)."""
    cfg = SPORT_CONFIG[sport]
    import importlib
    mod = importlib.import_module(cfg['gen_module'])
    mp = getattr(mod, 'PROP_MARKET_MAP', {})
    return mp.get(prop_type)


def freeze_sport(sport: str, dry: bool = False) -> int:
    today = dt.date.today().isoformat()
    cfg = SPORT_CONFIG[sport]
    ready = _freeze_ready_games(sport, today)
    if not ready:
        print(f'{sport}: no games in freeze window ({cfg["freeze_min"]}min)')
        return 0
    pending = _pending_props(sport, today, ready)
    if not pending:
        print(f'{sport}: no pending props to freeze ({len(ready)} games ready)')
        return 0

    # Group pending rows by (market, player_norm) for one API call per market
    by_market: dict[str, list[dict]] = {}
    for p in pending:
        mkt = _resolve_prop_market(sport, p.get('prop_type'))
        if not mkt: continue
        by_market.setdefault(mkt, []).append(p)

    frozen_ct = 0
    for market, rows in by_market.items():
        book_map = _fetch_market_lines(sport, market)
        if not book_map:
            print(f'  {sport}/{market}: no book data returned — skip {len(rows)} rows')
            continue
        for p in rows:
            key = _norm_name(p.get('player_name', ''))
            entry = book_map.get(key)
            if not entry:
                continue
            _write_freeze(
                cfg['table'], p['id'],
                entry.get('line'), entry.get('over'), entry.get('under'),
                entry.get('source'), dry,
            )
            frozen_ct += 1
        print(f'  {sport}/{market}: froze {frozen_ct} rows this market (running total)')
    print(f'{sport}: total frozen this run = {frozen_ct}')
    return frozen_ct


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=['ALL'] + list(SPORT_CONFIG.keys()),
                    default='ALL')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    print(f'freeze_prop_closing_lines @ {dt.datetime.now(dt.timezone.utc).isoformat()}',
          file=sys.stderr)
    targets = list(SPORT_CONFIG) if args.sport == 'ALL' else [args.sport]
    total = 0
    for sp in targets:
        try:
            total += freeze_sport(sp, args.dry_run)
        except Exception as e:
            print(f'{sp} freeze failed: {type(e).__name__}: {e}', file=sys.stderr)
    print(f'DONE — {total} rows frozen this run', file=sys.stderr)


if __name__ == '__main__':
    main()
