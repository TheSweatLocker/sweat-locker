"""UFC fight resolver — ESPN-backed (v2, 2026-07-29).

Runs after each UFC card (Sunday cron post-Saturday). Pulls the completed
event from ESPN's MMA API, parses each fight's result, upserts to
ufc_fight_results keyed by (event_name, fighter_a, fighter_b).

Background — why ESPN not UFCStats:
  ufcstats.com/statistics/events since ~2026-05-21 returns a JS bot-check
  page. The old resolver hit that URL and got 0 fights back. Same fix as
  ufc_card_scraper_v3.py — swap to ESPN's site.api.espn.com/sports/mma/ufc.

ESPN endpoints used:
  Scoreboard with date range:
    https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard?dates=YYYYMMDD-YYYYMMDD
  Each event has competitions[] (fights) with:
    - competitors[]: displayName + winner:bool + order:1|2
    - status.type.completed: bool
    - status.period: 1-5 (round the fight ended)
    - status.displayClock: "mm:ss"
    - details[]: array of {type.text} entries; look for "Unofficial Winner {Decision|Submission|KO/TKO|DQ}"

Schema: see supabase/migrations/20260506_ufc_fight_results.sql
Fighter URL lookup: ufc_fighter_stats via name normalization (accent-strip)

Usage:
  python ufc_resolve_fights.py                          # latest completed event (last 7 days)
  python ufc_resolve_fights.py --date 2026-08-01        # specific date
  python ufc_resolve_fights.py --lookback 14            # last N days scanned
"""
import argparse
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

ESPN_SCOREBOARD = 'https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard'

H_READ = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}


def _normalize_name(name: str) -> str:
    """Accent-strip + lowercase for fighter-name matching."""
    if not name:
        return ''
    n = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'\s+', ' ', n).strip().lower()


def find_fighter_url(name: str) -> str | None:
    """Look up ufc_fighter_stats.fighter_url via 3-tier match (exact → ilike → normalized)."""
    if not name:
        return None
    # Exact
    r = requests.get(
        f'{SUPABASE_URL}/rest/v1/ufc_fighter_stats',
        params={'fighter_name': f'eq.{name}', 'select': 'fighter_url,fighter_name', 'limit': '1'},
        headers=H_READ, timeout=10,
    )
    d = r.json() if r.status_code == 200 else []
    if d:
        return d[0].get('fighter_url')
    # ILIKE with normalized target
    target = _normalize_name(name)
    for candidate in (name, name.split()[-1] if ' ' in name else name):
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/ufc_fighter_stats',
            params={'fighter_name': f'ilike.%{candidate}%', 'select': 'fighter_url,fighter_name', 'limit': '10'},
            headers=H_READ, timeout=10,
        )
        d = r.json() if r.status_code == 200 else []
        for row in d:
            if _normalize_name(row.get('fighter_name', '')) == target:
                return row.get('fighter_url')
        if d:
            return d[0].get('fighter_url')
    return None


# Map ESPN's "Unofficial Winner ..." detail text → our method taxonomy.
# Ordering matters: check specific before generic (KO/TKO before DEC etc).
def parse_method_from_details(details: list) -> tuple[str | None, str | None]:
    """Return (method, method_detail_text). method taxonomy matches training data:
       KO/TKO | SUB | U-DEC | S-DEC | M-DEC | DQ | Draw | NC"""
    if not details:
        return None, None
    for d in details:
        text = ((d.get('type') or {}).get('text') or '').strip()
        low = text.lower()
        # Winner headline usually contains method
        if 'unofficial winner' not in low and 'result' not in low:
            continue
        if 'submission' in low or 'sub' in low:
            return 'SUB', text
        if 'ko/tko' in low or 'ko' in low or 'tko' in low:
            return 'KO/TKO', text
        if 'unanimous' in low:
            return 'U-DEC', text
        if 'split' in low:
            return 'S-DEC', text
        if 'majority' in low:
            return 'M-DEC', text
        if 'decision' in low:
            return 'U-DEC', text  # default assumption if unspecified
        if 'disqualification' in low or ' dq' in low:
            return 'DQ', text
        if 'no contest' in low or 'nc' in low:
            return 'NC', text
        if 'draw' in low:
            return 'Draw', text
    return None, None


def parse_espn_event(event: dict) -> tuple[str, str, list]:
    """Convert an ESPN event dict → (event_name, event_date_iso, fights[])"""
    event_name = event.get('name') or event.get('shortName') or 'UFC Event'
    date_raw = event.get('date') or ''  # ISO like "2026-07-25T13:00Z"
    event_date_iso = date_raw[:10] if date_raw else None

    fights = []
    for i, comp in enumerate(event.get('competitions') or []):
        status = comp.get('status') or {}
        stype = status.get('type') or {}
        if not stype.get('completed'):
            continue

        competitors = comp.get('competitors') or []
        if len(competitors) < 2:
            continue

        # Preserve booking-order via `order` field (1=top listed = fighter_a in our schema).
        competitors_sorted = sorted(competitors, key=lambda x: x.get('order') or 99)
        c_a, c_b = competitors_sorted[0], competitors_sorted[1]
        name_a = (c_a.get('athlete') or {}).get('displayName') or ''
        name_b = (c_b.get('athlete') or {}).get('displayName') or ''
        if not name_a or not name_b:
            continue

        # Winner
        if c_a.get('winner') is True:
            winner = 'a'
        elif c_b.get('winner') is True:
            winner = 'b'
        else:
            winner = None  # draw or NC — set by method parse below

        # Round + time
        round_num = status.get('period')
        time_str = status.get('displayClock')

        # Method
        method, method_detail = parse_method_from_details(comp.get('details') or [])
        if method in ('Draw',):
            winner = 'draw'
        elif method in ('NC',):
            winner = 'no_contest'

        # Weight class — ESPN sometimes lists via competition.type.text or notes
        weight_class = None
        ctype = (comp.get('type') or {}).get('text') or ''
        # Not always populated; leave None if not present (backward-compatible)
        for wc in ('Heavyweight', 'Light Heavyweight', 'Middleweight', 'Welterweight',
                   'Lightweight', 'Featherweight', 'Bantamweight', 'Flyweight',
                   "Women's Strawweight", "Women's Flyweight", "Women's Bantamweight",
                   "Women's Featherweight", 'Catch Weight', 'Open Weight'):
            if wc.lower() in ctype.lower() or wc.lower() in event_name.lower():
                weight_class = wc
                break

        went_distance = method in ('U-DEC', 'S-DEC', 'M-DEC')

        fights.append({
            'event_name': event_name,
            'event_url': f'espn:mma/ufc/event/{event.get("id","?")}',
            'fight_order': i + 1,
            'fighter_a': name_a,
            'fighter_b': name_b,
            'fighter_a_url': find_fighter_url(name_a),
            'fighter_b_url': find_fighter_url(name_b),
            'weight_class': weight_class,
            'winner': winner,
            'method': method,
            'method_detail': method_detail,
            'round': round_num,
            'time': time_str,
            'went_distance': went_distance,
        })

    return event_name, event_date_iso, fights


def get_events_in_range(date_from: str, date_to: str) -> list:
    """Return list of ESPN event dicts in the YYYYMMDD-YYYYMMDD range."""
    url = f'{ESPN_SCOREBOARD}?dates={date_from}-{date_to}'
    r = requests.get(url, timeout=20)
    if r.status_code != 200:
        print(f'  ⚠ ESPN scoreboard {r.status_code}: {r.text[:200]}')
        return []
    return r.json().get('events') or []


def upload_fight(event_date_iso: str, fight: dict) -> bool:
    payload = dict(fight)
    payload['event_date'] = event_date_iso
    r = requests.post(
        f'{SUPABASE_URL}/rest/v1/ufc_fight_results?on_conflict=event_name,fighter_a,fighter_b',
        headers=H_WRITE,
        json=payload,
        timeout=15,
    )
    if r.status_code not in (200, 201, 204):
        print(f'    ⚠ upload {r.status_code}: {r.text[:200]}')
        return False
    return True


def run(target_date: str | None = None, lookback_days: int = 7):
    """Resolve the latest completed event.

    - target_date: 'YYYY-MM-DD' — resolve that specific date only
    - lookback_days: how many days to scan backward from today
    """
    if target_date:
        d = datetime.strptime(target_date, '%Y-%m-%d')
        date_from = d.strftime('%Y%m%d')
        date_to = date_from
        print(f'Fetching UFC events on {target_date}...')
    else:
        today = datetime.now(timezone.utc)
        start = today - timedelta(days=lookback_days)
        date_from = start.strftime('%Y%m%d')
        date_to = today.strftime('%Y%m%d')
        print(f'Scanning ESPN UFC scoreboard {date_from}-{date_to} for completed events...')

    events = get_events_in_range(date_from, date_to)
    completed_events = []
    for e in events:
        # Consider event completed if ANY fight is completed
        for c in (e.get('competitions') or []):
            if ((c.get('status') or {}).get('type') or {}).get('completed'):
                completed_events.append(e)
                break

    if not completed_events:
        print('No completed events found in range')
        return

    print(f'Found {len(completed_events)} completed event(s) in range')

    total_success = 0
    total_errors = 0
    for event in completed_events:
        name, date_iso, fights = parse_espn_event(event)
        print(f'\n=== {name} ({date_iso}) — {len(fights)} completed fights ===')
        success = 0
        errors = 0
        for f in fights:
            if upload_fight(date_iso, f):
                success += 1
                mstr = f.get('method') or '?'
                rstr = f'R{f["round"]}' if f.get('round') else '—'
                tstr = f.get('time') or '—'
                winner_name = (f.get('fighter_a') if f.get('winner') == 'a'
                               else f.get('fighter_b') if f.get('winner') == 'b'
                               else str(f.get('winner') or '?'))
                print(f'  ✅ {f["fighter_a"]} vs {f["fighter_b"]} — {winner_name} via {mstr} ({rstr} {tstr})')
            else:
                errors += 1
        total_success += success
        total_errors += errors
        print(f'  Event summary: ✅ {success}  ❌ {errors}')

    print(f'\nDone! ✅ {total_success} fights stored, ❌ {total_errors} errors across {len(completed_events)} events')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='Specific YYYY-MM-DD event date to resolve')
    p.add_argument('--lookback', type=int, default=7,
                   help='Days to scan backward from today (default: 7)')
    args = p.parse_args()
    run(target_date=args.date, lookback_days=args.lookback)
