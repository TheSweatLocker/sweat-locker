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
    """Fetch alt totals + alt spreads via Odds API PER-EVENT endpoint.
    2026-08-18 fix: bulk /sports/{sport}/odds does NOT support alternate_*
    markets (HTTP 422 INVALID_MARKET). Only /events/{event_id}/odds does.

    Cost per game = 1 call × 2 markets × 1 region × 1 book = 2 credits.
    We restrict to target_game_ids so we only spend on games we care about.
    Typical MLB slate = 15 games × 2 credits = 30 credits/day for MLB alt lines.
    """
    if not ODDS_KEY:
        print(f'  ⚠ ODDS_API_KEY missing — skipping {sport_key}')
        return []

    rows = []
    calls_made = 0
    for gid in target_game_ids:
        try:
            r = requests.get(
                f'https://api.the-odds-api.com/v4/sports/{sport_key}/events/{gid}/odds',
                params={
                    'apiKey': ODDS_KEY,
                    'regions': 'us',
                    'markets': 'alternate_totals,alternate_spreads',
                    'oddsFormat': 'american',
                    'bookmakers': 'draftkings',
                }, timeout=15)
            calls_made += 1
            if r.status_code != 200:
                if r.status_code != 404:  # 404 just means no alt markets posted for this game
                    print(f'  ⚠ event {gid[:12]}: HTTP {r.status_code} {r.text[:120]}')
                continue
            g = r.json() if isinstance(r.json(), dict) else {}
        except Exception as e:
            print(f'  ⚠ event {gid[:12]}: {e}')
            continue

        home_team = (g.get('home_team') or '').lower()
        for bm in g.get('bookmakers', []):
            for m in bm.get('markets', []):
                market_name = m.get('key')
                if market_name not in ('alternate_totals', 'alternate_spreads'): continue
                for outcome in m.get('outcomes', []):
                    side_raw = outcome.get('name', '')
                    line = outcome.get('point')
                    price = outcome.get('price')
                    if line is None or price is None: continue
                    if market_name == 'alternate_totals':
                        side = 'OVER' if 'over' in side_raw.lower() else 'UNDER'
                    else:
                        side = 'HOME' if side_raw.strip().lower() == home_team else 'AWAY'
                    rows.append({
                        'game_id': gid, 'market': market_name, 'side': side,
                        'line': float(line), 'price': int(price),
                    })
    print(f'    ({calls_made} per-event calls to Odds API)')
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
