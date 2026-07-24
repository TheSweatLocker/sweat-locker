"""NCAAB live-lines pull from Odds API — Phase 1.

Pulls spread, total, and moneyline for upcoming CBB games. Upserts to
ncaab_game_results on game_id. Runs nightly during CBB season
(Nov-Mar) on the NCAAB workflow cron.

Sign convention (matches existing ncaab_game_context.py):
  close_spread: home perspective — NEGATIVE = home favored.
  projected_spread: positive = home wins by X (KenPom-driven).
  This is opposite to NCAAF (which flipped to nflverse convention).
  Kept as-is because ncaab_game_context.py + sweat scorer already
  encode this convention (per feedback_backside_dictates_app_renders
  the app assumes server writes match the schema comments).

USAGE:
    python ncaab_odds_pull.py              # today + next 2 days
    python ncaab_odds_pull.py --dry-run
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
SB_KEY = os.environ.get('SUPABASE_KEY')
ODDS_KEY = os.environ.get('ODDS_API_KEY')
H_READ = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

ODDS_API_BASE = 'https://api.the-odds-api.com/v4/sports'
ODDS_SPORT = 'basketball_ncaab'
SEASON = '2025-26'


def _et_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(v) if v is not None else None
    except (TypeError, ValueError): return None


def load_alias_map() -> dict:
    """Map Odds API name → canonical_name via ncaab_team_aliases."""
    r = requests.get(
        f'{SB}/rest/v1/ncaab_team_aliases?select=canonical_name,odds_api_name,alt_names',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200:
        print(f'  ⚠ alias fetch failed: {r.status_code}')
        return {}
    aliases = {}
    for row in r.json():
        canon = row.get('canonical_name')
        if not canon: continue
        if row.get('odds_api_name'):
            aliases[row['odds_api_name']] = canon
        for alt in (row.get('alt_names') or []):
            if alt: aliases[alt] = canon
        aliases[canon] = canon
    return aliases


def fetch_odds_api() -> tuple[list, int]:
    if not ODDS_KEY: return [], 401
    url = (f'{ODDS_API_BASE}/{ODDS_SPORT}/odds/'
           f'?apiKey={ODDS_KEY}&regions=us,us2&markets=spreads,totals,h2h'
           f'&oddsFormat=american'
           f'&bookmakers=hardrockbet,draftkings,fanduel,espnbet,betmgm,caesars')
    r = requests.get(url, timeout=20)
    if r.status_code != 200:
        print(f'  ⚠ Odds API NCAAB: {r.status_code} {r.text[:120]}')
        return [], r.status_code
    return r.json(), 200


def _consensus_spread(event: dict, home_raw: str) -> Optional[float]:
    """Average home spread across books (Odds API convention: home spread as-is)."""
    vals = []
    for book in event.get('bookmakers', []):
        for m in book.get('markets', []):
            if m['key'] != 'spreads': continue
            for o in m.get('outcomes', []):
                if o.get('name') == home_raw:
                    pt = _f(o.get('point'))
                    if pt is not None: vals.append(pt)
    return round(sum(vals) / len(vals), 2) if vals else None


def _consensus_total(event: dict) -> Optional[float]:
    vals = []
    for book in event.get('bookmakers', []):
        for m in book.get('markets', []):
            if m['key'] != 'totals': continue
            for o in m.get('outcomes', []):
                if o.get('name') == 'Over':
                    pt = _f(o.get('point'))
                    if pt is not None: vals.append(pt)
    return round(sum(vals) / len(vals), 1) if vals else None


def _consensus_ml(event: dict, team_raw: str) -> Optional[int]:
    vals = []
    for book in event.get('bookmakers', []):
        for m in book.get('markets', []):
            if m['key'] != 'h2h': continue
            for o in m.get('outcomes', []):
                if o.get('name') == team_raw and o.get('price') is not None:
                    vals.append(int(o['price']))
    return int(sum(vals) / len(vals)) if vals else None


def event_to_row(event: dict, aliases: dict) -> Optional[dict]:
    home_raw = event.get('home_team')
    away_raw = event.get('away_team')
    home = aliases.get(home_raw)
    away = aliases.get(away_raw)
    if not home or not away:
        return None

    commence = event.get('commence_time', '')
    try:
        dt = datetime.fromisoformat(commence.replace('Z', '+00:00'))
        game_date = dt.date().isoformat()
    except Exception:
        dt = _et_now(); game_date = dt.date().isoformat()

    game_id = f'ncaab_{dt.strftime("%Y%m%d")}_{away}_{home}'.replace(' ', '_')

    home_spread = _consensus_spread(event, home_raw)  # Odds API home spread
    row = {
        'game_id': game_id,
        'game_date': game_date,
        'season': SEASON,
        'home_team': home,
        'away_team': away,
        # NCAAB convention: close_spread NEGATIVE = home favored → keep Odds API sign
        'close_spread': home_spread,
        'close_total': _consensus_total(event),
        'home_ml_close': _consensus_ml(event, home_raw),
        'away_ml_close': _consensus_ml(event, away_raw),
    }
    # Mirror close → open on first pull (open_* preserved once set by prior row)
    row['open_spread'] = row['close_spread']
    row['open_total']  = row['close_total']
    row['home_ml_open'] = row['home_ml_close']
    row['away_ml_open'] = row['away_ml_close']
    return row


def _normalize_batch_keys(rows: list) -> list:
    """Union keys + backfill None across rows (PGRST102 batch upsert requirement).
    Per feedback_postgrest_batch_normalize_keys memory."""
    if not rows: return rows
    keys = set()
    for r in rows: keys.update(r.keys())
    for r in rows:
        for k in keys: r.setdefault(k, None)
    return rows


def upsert_games(rows: list, dry_run: bool = False) -> int:
    if not rows: return 0
    rows = _normalize_batch_keys(rows)
    if dry_run:
        for r in rows[:15]:
            print(f"  [DRY] {r['game_id']} sp={r.get('close_spread')} "
                  f"tot={r.get('close_total')} ml={r.get('home_ml_close')}/{r.get('away_ml_close')}")
        if len(rows) > 15:
            print(f'  [DRY] ... {len(rows)-15} more')
        return len(rows)
    r = requests.post(
        f'{SB}/rest/v1/ncaab_game_results?on_conflict=game_id',
        headers=H_WRITE, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(rows)


def run(dry_run: bool = False) -> None:
    print(f'=== NCAAB odds pull · {_et_now().date()} ===')
    if not ODDS_KEY:
        print('  ✗ ODDS_API_KEY missing — abort'); return

    aliases = load_alias_map()
    if not aliases:
        print('  ✗ ncaab_team_aliases empty — run ncaab_seed_aliases.py first'); return
    print(f'  alias map: {len(aliases)} name variants')

    events, status = fetch_odds_api()
    if status != 200: return
    print(f'  Odds API events: {len(events)}')
    if not events:
        print('  (offseason or no lines posted)'); return

    rows, skipped = [], []
    for event in events:
        row = event_to_row(event, aliases)
        if row is None:
            skipped.append((event.get('home_team'), event.get('away_team')))
            continue
        rows.append(row)
    if skipped:
        print(f'  ⚠ skipped {len(skipped)} events w/ unmapped teams:')
        for h, a in skipped[:8]:
            print(f'      {a} @ {h}')

    written = upsert_games(rows, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}wrote {written} rows to ncaab_game_results')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
