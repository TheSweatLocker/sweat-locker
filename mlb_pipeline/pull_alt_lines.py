"""Nightly alt-line puller (2026-08-18) — cheap Odds API fetch of
alternate totals + alternate spreads for today's slate, cached to
alt_line_snapshots for the Ledger to consume real prices instead of
teaser_price() estimates.

Cost control:
  - Runs ONCE per day (not per-cron) via workflow schedule
  - Fetches only games that have a game_context row today
  - Uses `bookmakers=draftkings` to restrict to a single book (1 credit
    per market per fetch, not per book)

Odds API markets:
  alternate_totals   — full ladder of totals (Over 5.5, 6.5, 7.5 ...)
                       with real prices per side
  alternate_spreads  — same for run lines / point spreads

CLI:
    python pull_alt_lines.py                # today, all sports w/ context
    python pull_alt_lines.py --sport MLB
    python pull_alt_lines.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

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
ODDS_KEY = os.environ.get('ODDS_API_KEY')
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

SPORT_CFG = {
    'MLB':   ('mlb_game_context',   'baseball_mlb'),
    'NFL':   ('nfl_game_context',   'americanfootball_nfl'),
    'NCAAF': ('ncaaf_game_context', 'americanfootball_ncaaf'),
    'NCAAB': ('ncaab_game_context', 'basketball_ncaab'),
    'NHL':   ('nhl_game_context',   'icehockey_nhl'),
    'NBA':   ('nba_game_context',   'basketball_nba'),
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def fetch_alt_markets_for_sport(sport_key: str, target_game_ids: set[str]) -> list[dict]:
    """One Odds API call for alt totals + alt spreads across today's games
    at DraftKings. Returns list of (game_id, market, side, line, price) rows."""
    if not ODDS_KEY:
        print(f'  ⚠ ODDS_API_KEY missing — skipping {sport_key}')
        return []
    now_et = datetime.now(timezone.utc) - timedelta(hours=4)
    time_from = f"{now_et.strftime('%Y-%m-%d')}T04:00:00Z"
    time_to = f"{(now_et + timedelta(days=1)).strftime('%Y-%m-%d')}T03:59:59Z"

    rows = []
    for market_name in ('alternate_totals', 'alternate_spreads'):
        try:
            r = requests.get(
                f'https://api.the-odds-api.com/v4/sports/{sport_key}/odds',
                params={
                    'apiKey': ODDS_KEY,
                    'regions': 'us',
                    'markets': market_name,
                    'oddsFormat': 'american',
                    'bookmakers': 'draftkings',
                    'commenceTimeFrom': time_from,
                    'commenceTimeTo': time_to,
                }, timeout=20)
            if r.status_code != 200:
                print(f'  ⚠ {market_name} {sport_key}: HTTP {r.status_code} {r.text[:150]}')
                continue
            games = r.json() if isinstance(r.json(), list) else []
        except Exception as e:
            print(f'  ⚠ {market_name} {sport_key}: {e}')
            continue

        for g in games:
            gid = g.get('id')
            if gid not in target_game_ids: continue
            for bm in g.get('bookmakers', []):
                for m in bm.get('markets', []):
                    if m.get('key') != market_name: continue
                    for outcome in m.get('outcomes', []):
                        side_raw = outcome.get('name', '')
                        line = outcome.get('point')
                        price = outcome.get('price')
                        if line is None or price is None: continue
                        # Normalize side
                        if market_name == 'alternate_totals':
                            side = 'OVER' if 'over' in side_raw.lower() else 'UNDER'
                        else:  # alternate_spreads
                            side = 'HOME' if side_raw.strip().lower() == g.get('home_team','').lower() else 'AWAY'
                        rows.append({
                            'game_id': gid,
                            'market': market_name,
                            'side': side,
                            'line': float(line),
                            'price': int(price),
                        })
    return rows


def run(sport: str = None, game_date: str = None, dry_run: bool = False):
    gd = game_date or _et_today()
    sports = [sport] if sport else list(SPORT_CFG.keys())
    print(f'=== pull_alt_lines · {gd} · {"/".join(sports)}{" [DRY]" if dry_run else ""} ===')

    total_written = 0
    for s in sports:
        ctx_t, sport_key = SPORT_CFG[s]
        # Get today's game_ids from game_context
        try:
            r = requests.get(f'{SB}/rest/v1/{ctx_t}',
                             headers=H_READ,
                             params={'game_date': f'eq.{gd}', 'select': 'game_id', 'limit': '200'},
                             timeout=15)
            target_ids = {row['game_id'] for row in (r.json() or [])}
        except Exception:
            target_ids = set()
        if not target_ids:
            print(f'  {s}: no games on {gd} — skipping')
            continue

        rows = fetch_alt_markets_for_sport(sport_key, target_ids)
        # Add snapshot_date / sport / book fields
        now_iso = datetime.now(timezone.utc).isoformat()
        payloads = [{
            **r,
            'snapshot_date': gd,
            'sport': s,
            'book': 'draftkings',
            'fetched_at': now_iso,
        } for r in rows]

        if not payloads:
            print(f'  {s}: 0 alt lines returned')
            continue

        if dry_run:
            print(f'  [DRY] {s}: {len(payloads)} alt lines (sample: {payloads[0]})')
            continue

        # Batch upsert (chunks of 500)
        written = 0
        for i in range(0, len(payloads), 500):
            chunk = payloads[i:i+500]
            pr = requests.post(
                f'{SB}/rest/v1/alt_line_snapshots'
                f'?on_conflict=snapshot_date,sport,game_id,book,market,side,line',
                headers=H_WRITE, json=chunk, timeout=30,
            )
            if pr.status_code in (200, 201, 204):
                written += len(chunk)
            else:
                print(f'  x {s} chunk {i}: {pr.status_code} {pr.text[:200]}')
        print(f'  {s}: {written} alt lines cached')
        total_written += written

    print(f'\n  total alt lines cached: {total_written}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=list(SPORT_CFG.keys()))
    p.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(sport=args.sport, game_date=args.date, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
