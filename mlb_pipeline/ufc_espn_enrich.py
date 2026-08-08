"""ESPN-based UFC fighter stats enrichment (2026-08-08).

The ufcstats.com scraper (ufc_scraper.py) has been failing silently
because the site now serves a JS anti-scraper challenge. 940 of 1000
fighters in ufc_fighter_stats have total_wins > 0 but wins_by_ko / sub /
dec all = 0. This causes hallucinated UFC content ("Gamrot has no
finishes in 25 fights" was cited from the corrupted 0s).

ESPN's public core API (no auth needed) has cleaner data with W-L-D
plus finish-method breakdown. This module rebuilds ufc_fighter_stats
using ESPN as the source.

Endpoints:
  http://sports.core.api.espn.com/v2/sports/mma/leagues/ufc/athletes
    → paginated list of all UFC athletes (~1830, ~5 pages of 500)
  http://sports.core.api.espn.com/v2/sports/mma/athletes/{id}/records
    → per-fighter W-L-D + TKO/submission/decision breakdown

Usage:
    python ufc_espn_enrich.py --today          # only today's ufc_picks fighters
    python ufc_espn_enrich.py --backfill-all   # rebuild entire table (~10 min)
    python ufc_espn_enrich.py --fighter "Mateusz Gamrot"
"""
from __future__ import annotations
import argparse, json, os, sys, time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

ESPN_HEADERS = {'User-Agent': 'Mozilla/5.0'}


def _get(url: str) -> dict | None:
    """Fetch JSON via urllib. Returns None on any error."""
    try:
        req = urllib.request.Request(url, headers=ESPN_HEADERS)
        return json.loads(urllib.request.urlopen(req, timeout=15).read())
    except Exception:
        return None


def build_espn_athlete_index() -> dict:
    """Paginate through all UFC athletes on ESPN. Build name → athlete_id
    map for lookup. Returns {lowered_name: espn_id_str}."""
    out = {}
    page = 1
    while True:
        u = (f'http://sports.core.api.espn.com/v2/sports/mma/leagues/ufc/'
             f'athletes?limit=500&page={page}')
        data = _get(u)
        if not data or not data.get('items'):
            break
        for item in data['items']:
            ref = item.get('$ref')
            if not ref: continue
            athlete = _get(ref)
            if not athlete: continue
            name = (athlete.get('fullName') or '').strip()
            aid = str(athlete.get('id') or '')
            if name and aid:
                out[name.lower()] = aid
        total_pages = data.get('pageCount') or 0
        print(f'  ESPN page {page}/{total_pages} → {len(out)} athletes indexed')
        if page >= total_pages: break
        page += 1
        time.sleep(0.5)  # polite pacing
    return out


def fetch_espn_record(espn_id: str) -> dict | None:
    """Pull overall record + finish breakdown for one athlete.
    Returns dict with wins/losses/draws + wins_by_ko/sub/dec.
    Returns None if lookup fails."""
    url = (f'http://sports.core.api.espn.com/v2/sports/mma/athletes/'
           f'{espn_id}/records?lang=en&region=us')
    data = _get(url)
    if not data or not data.get('items'):
        return None
    # Follow first record ref (overall)
    first_ref = data['items'][0].get('$ref')
    if not first_ref: return None
    rec = _get(first_ref)
    if not rec or not rec.get('stats'):
        return None
    stat_map = {s.get('type'): s.get('value') for s in rec.get('stats', [])}
    wins = int(stat_map.get('wins') or 0)
    losses = int(stat_map.get('losses') or 0)
    draws = int(stat_map.get('draws') or 0)
    tkos = int(stat_map.get('tkos') or 0)
    subs = int(stat_map.get('submissions') or 0)
    total_finishes = tkos + subs
    wins_by_dec = max(0, wins - total_finishes)
    finishing_rate = round(total_finishes / wins * 100, 1) if wins > 0 else 0.0
    return {
        'total_wins': wins,
        'total_losses': losses,
        'total_draws': draws,
        'wins_by_ko': tkos,
        'wins_by_sub': subs,
        'wins_by_dec': wins_by_dec,
        'finishing_rate': finishing_rate,
        'record': f'{wins}-{losses}-{draws}',
    }


def fetch_espn_athlete_meta(espn_id: str) -> dict | None:
    """Pull athlete metadata (height, weight, reach, stance)."""
    url = (f'http://sports.core.api.espn.com/v2/sports/mma/athletes/'
           f'{espn_id}?lang=en&region=us')
    data = _get(url)
    if not data: return None
    out = {}
    if data.get('height') is not None: out['height'] = data['height']
    if data.get('weight') is not None: out['weight'] = data['weight']
    if data.get('reach') is not None: out['reach'] = data['reach']
    stance = data.get('stance')
    if isinstance(stance, dict): out['stance'] = stance.get('text')
    return out


def upsert_fighter_stats(fighter_name: str, updates: dict) -> bool:
    """Update existing ufc_fighter_stats row OR insert new one."""
    updates = dict(updates)  # copy
    updates['fighter_name'] = fighter_name
    updates['updated_at'] = datetime.now(timezone.utc).isoformat()
    # PostgREST upsert via merge-duplicates. But there's no unique
    # constraint on fighter_name (need to look up id first).
    r = requests.get(
        f'{SB}/rest/v1/ufc_fighter_stats',
        headers=H_READ,
        params={'fighter_name': f'eq.{fighter_name}', 'select': 'id', 'limit': 1},
        timeout=10,
    )
    existing = r.json() if r.status_code == 200 else []
    if existing:
        # PATCH existing row
        row_id = existing[0]['id']
        r2 = requests.patch(
            f'{SB}/rest/v1/ufc_fighter_stats?id=eq.{row_id}',
            headers=H_WRITE, data=json.dumps(updates), timeout=10,
        )
        return r2.status_code in (200, 204)
    else:
        # INSERT new
        r2 = requests.post(
            f'{SB}/rest/v1/ufc_fighter_stats',
            headers=H_WRITE, data=json.dumps(updates), timeout=10,
        )
        return r2.status_code in (200, 201, 204)


def enrich_fighter(fighter_name: str, name_to_id: dict) -> bool:
    """One-fighter enrichment. Returns True if any data written."""
    espn_id = name_to_id.get(fighter_name.lower())
    if not espn_id:
        print(f'  ⚠ {fighter_name} not found in ESPN index')
        return False
    record = fetch_espn_record(espn_id)
    meta = fetch_espn_athlete_meta(espn_id) or {}
    payload = {**meta}
    if record: payload.update(record)
    if not payload:
        print(f'  ⚠ {fighter_name} — no data from ESPN')
        return False
    ok = upsert_fighter_stats(fighter_name, payload)
    if ok:
        rec = record.get('record') if record else '?'
        finishes = (record.get('wins_by_ko',0) + record.get('wins_by_sub',0)) if record else 0
        print(f'  ✓ {fighter_name}: {rec} ({finishes} finishes)')
    else:
        print(f'  ⚠ {fighter_name} — DB write failed')
    return ok


def enrich_todays_fighters():
    """Enrich only fighters in today's ufc_picks card."""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    r = requests.get(
        f'{SB}/rest/v1/ufc_picks',
        headers=H_READ,
        params={'event_date': f'eq.{today}',
                'select': 'fighter_a,fighter_b'},
        timeout=15,
    )
    if r.status_code != 200:
        print(f'  ⚠ failed to load ufc_picks for {today}'); return
    picks = r.json()
    names = set()
    for p in picks:
        if p.get('fighter_a'): names.add(p['fighter_a'])
        if p.get('fighter_b'): names.add(p['fighter_b'])
    print(f'  today ({today}) — {len(names)} unique fighters to enrich')
    print(f'  building ESPN athlete index...')
    name_to_id = build_espn_athlete_index()
    print(f'  indexed {len(name_to_id)} ESPN athletes')
    print()
    updated = 0
    for name in sorted(names):
        if enrich_fighter(name, name_to_id):
            updated += 1
        time.sleep(0.3)
    print(f'\n✓ enriched {updated}/{len(names)} fighters')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--today', action='store_true',
                    help='Enrich only today\'s ufc_picks fighters')
    ap.add_argument('--fighter', help='Enrich one specific fighter by name')
    ap.add_argument('--backfill-all', action='store_true',
                    help='Rebuild entire ufc_fighter_stats table (~10 min)')
    args = ap.parse_args()

    if args.fighter:
        name_to_id = build_espn_athlete_index()
        enrich_fighter(args.fighter, name_to_id)
    elif args.today:
        enrich_todays_fighters()
    elif args.backfill_all:
        # Load all fighter names from ufc_picks
        r = requests.get(
            f'{SB}/rest/v1/ufc_picks',
            headers=H_READ,
            params={'select': 'fighter_a,fighter_b', 'limit': '5000'},
            timeout=30,
        ).json()
        names = set()
        for p in r:
            if p.get('fighter_a'): names.add(p['fighter_a'])
            if p.get('fighter_b'): names.add(p['fighter_b'])
        print(f'  backfill target: {len(names)} unique fighters from ufc_picks')
        name_to_id = build_espn_athlete_index()
        for i, name in enumerate(sorted(names)):
            enrich_fighter(name, name_to_id)
            if (i+1) % 20 == 0: print(f'  progress {i+1}/{len(names)}')
            time.sleep(0.3)
    else:
        print('Specify --today, --fighter NAME, or --backfill-all')


if __name__ == '__main__':
    main()
