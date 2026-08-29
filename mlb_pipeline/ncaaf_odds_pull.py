"""NCAAF live-lines pull from Odds API — Phase 1.

Pulls spread, total, and moneyline for upcoming CFB games. Upserts to
ncaaf_game_results on game_id. Runs on the NCAAF workflow cron
(Wed/Fri/Sat) once season starts (2026-08-22).

Sign convention (verified against CFBD schedule):
  Odds API: home team spread (-3.5 = home favored by 3.5)
  We store the OPPOSITE — nflverse-style — positive close_spread = home favored.
  Flips at write time so downstream cohort/context logic is uniform
  across NFL + NCAAF.

USAGE:
    python ncaaf_odds_pull.py                  # today + next 7 days
    python ncaaf_odds_pull.py --dry-run
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


def _et_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _f(v) -> Optional[float]:
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _i(v) -> Optional[int]:
    try: return int(v) if v is not None else None
    except (TypeError, ValueError): return None


def load_alias_map() -> dict:
    """Map Odds API full team name → canonical short name via ncaaf_team_aliases."""
    r = requests.get(
        f'{SB}/rest/v1/ncaaf_team_aliases?select=canonical_name,full_name,alt_names',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200:
        print(f'  ⚠ alias fetch failed: {r.status_code}')
        return {}
    aliases = {}
    for row in r.json():
        canon = row.get('canonical_name')
        for n in ([row.get('full_name')] + (row.get('alt_names') or [])):
            if n and canon: aliases[n] = canon
    return aliases


def fetch_odds_api(markets: str = 'spreads,totals,h2h') -> tuple[list, int]:
    if not ODDS_KEY: return [], 401
    url = (f'{ODDS_API_BASE}/americanfootball_ncaaf/odds/'
           f'?apiKey={ODDS_KEY}&regions=us&markets={markets}&oddsFormat=american')
    r = requests.get(url, timeout=20)
    if r.status_code != 200:
        print(f'  ⚠ Odds API NCAAF: {r.status_code} {r.text[:120]}')
        return [], r.status_code
    return r.json(), 200


def _pick_book(event: dict, market_key: str) -> dict:
    for book in event.get('bookmakers', []):
        for m in book.get('markets', []):
            if m['key'] == market_key:
                return {'book': book['title'], 'outcomes': m['outcomes']}
    return {}


def event_to_row(event: dict, aliases: dict) -> Optional[dict]:
    home_raw = event.get('home_team')
    away_raw = event.get('away_team')
    home = aliases.get(home_raw)
    away = aliases.get(away_raw)
    # 2026-08-29 fix: don't drop the whole game when one team's alias
    # is missing (common on FBS-vs-FCS Week 1 games like Bethune-Cookman
    # @ UCF or Grambling @ LSU). Prior behavior: game got filtered
    # entirely → app renders with raw Odds API data → mangled labels +
    # "market-based analysis" fallback. Fix: fall back to a normalized
    # raw name so the ctx row lands and the app has SOMETHING to render.
    # Downstream signals that need alias-matched stats will still be
    # null for the FCS side (they don't have CFBD stats anyway) — but
    # the FBS side gets its full ctx.
    if not (home_raw and away_raw):
        return None
    if not home:
        home = (home_raw or '').strip()
    if not away:
        away = (away_raw or '').strip()

    commence = event.get('commence_time', '')
    try:
        dt = datetime.fromisoformat(commence.replace('Z', '+00:00'))
        game_date = dt.date().isoformat()
    except Exception:
        dt = _et_now(); game_date = dt.date().isoformat()
    game_id = f'ncaaf_{dt.strftime("%Y%m%d")}_{away}_{home}'

    row = {
        'game_id': game_id,
        'game_date': game_date,
        'season': dt.year,
        'season_type': 'regular',   # postseason handled separately (bowls)
        'home_team': home,
        'away_team': away,
        'kickoff_utc': commence,
    }

    # Spreads — keep CFBD convention (positive = home dog / home is underdog).
    # 2026-08-09 fix: previous code flipped to nflverse convention (positive =
    # home fav) but resolve_ncaaf_results.py + ncaaf_cohort_backfill.py both
    # assume CFBD convention. Historical `cfbd_*` rows use CFBD too. Flipping
    # here meant 2026 season results would grade INVERTED and cohorts would
    # mis-tag heavy dogs as favorites. Removing the flip aligns all systems.
    sp = _pick_book(event, 'spreads')
    for o in sp.get('outcomes') or []:
        if o['name'] == home_raw:
            pt = _f(o.get('point'))
            row['close_spread'] = pt if pt is not None else None
        elif o['name'] == away_raw:
            pass  # away spread implied

    tot = _pick_book(event, 'totals')
    for o in tot.get('outcomes') or []:
        if o['name'] == 'Over':
            row['close_total'] = _f(o.get('point'))

    ml = _pick_book(event, 'h2h')
    for o in ml.get('outcomes') or []:
        price = _i(o.get('price'))
        if o['name'] == home_raw:
            row['close_home_ml'] = price
            row['open_home_ml'] = price
        elif o['name'] == away_raw:
            row['close_away_ml'] = price
            row['open_away_ml'] = price

    # Mirror close → open on first pull
    row.setdefault('open_spread', row.get('close_spread'))
    row.setdefault('open_total', row.get('close_total'))
    return row


def _normalize_batch_keys(rows: list) -> list:
    """PostgREST batch upsert requires all rows share the same keys
    (PGRST102). event_to_row conditionally adds fields when Odds API
    returns partial market data, so batches mix schemas. Fix: union keys
    + backfill None. Per feedback_postgrest_batch_normalize_keys memory.
    """
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
        for r in rows:
            print(f"  [DRY] {r['game_id']}  sp={r.get('close_spread')} "
                  f"tot={r.get('close_total')} ml={r.get('close_home_ml')}/{r.get('close_away_ml')}")
        return len(rows)
    r = requests.post(
        f'{SB}/rest/v1/ncaaf_game_results?on_conflict=game_id',
        headers=H_WRITE, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(rows)


def run(dry_run: bool = False) -> None:
    print(f'=== NCAAF odds pull · {_et_now().date()} ===')
    if not ODDS_KEY:
        print('  ✗ ODDS_API_KEY missing — abort')
        return
    aliases = load_alias_map()
    if not aliases:
        print('  ✗ ncaaf_team_aliases empty — run ncaaf_seed_aliases.py first')
        return
    print(f'  alias map: {len(aliases)} name variants')

    events, status = fetch_odds_api()
    if status != 200:
        return
    print(f'  Odds API events: {len(events)}')
    if not events:
        return

    rows = []; skipped = 0
    for event in events:
        row = event_to_row(event, aliases)
        if row is None:
            skipped += 1
            continue
        rows.append(row)
    if skipped:
        print(f'  ⚠ skipped {skipped} events with unmapped teams (seed_aliases may need more)')

    written = upsert_games(rows, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}wrote {written} rows to ncaaf_game_results')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
