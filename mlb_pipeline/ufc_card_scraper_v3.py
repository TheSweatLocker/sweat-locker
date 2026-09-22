"""UFC card scraper v3 — ESPN-backed (UFCStats.com now bot-blocked).

Background:
  ufc_card_scraper.py (v1) and _v2 both hit ufcstats.com/statistics/events
  which since ~2026-05-21 returns a JS bot-check page ("Checking your
  browser..."). Requests-based scraping gets 2.9KB of JS, no data.
  Confirmed 2026-07-27.

Fix:
  Switch upcoming-event + fight-card source to ESPN's public MMA API
  (site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard). Returns
  structured JSON, no auth, no bot check. Reliably lists the next
  upcoming event with all fights + athlete names.

Fighter model URLs still come from ufc_fighter_stats (4466 rows pre-
loaded — the scorer's predict_fight() reads from that table). We match
ESPN athlete display name → fighter_name via ILIKE.

Writes to ufc_upcoming_event same as v1 so downstream ufc_score_card.py
continues to work unchanged.
"""
import argparse
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

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

ESPN_SCOREBOARD = 'https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard'


def _normalize_name(name: str) -> str:
    """Strip accents + lower + collapse whitespace for name matching."""
    if not name: return ''
    import unicodedata
    n = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'\s+', ' ', n).strip().lower()


def fetch_forward_espn_events(days: int = 28) -> list:
    """Every UFC event in the next `days`, soonest first.

    2026-09-22: was fetch_next_espn_event(), which called the scoreboard
    with no date range and took events[0] — one event, the closest one.

    Enough for scoring the next card, but it leaves the table unable to
    answer "is this fight a UFC fight", which is what the Games tab needs.
    The Odds API exposes a single `mma_mixed_martial_arts` key and stamps
    every promotion sport_title="MMA" — Bare Knuckle, PFL and UFC arrive
    in one undifferentiated feed with no field to filter on. This endpoint
    is UFC-specific (/mma/ufc/) and is the only authoritative roster we
    have; one event covers ~4 days of a feed that runs two weeks out.

    Holding the forward slate makes that filter possible and costs one
    request. on_conflict=event_name keeps re-runs idempotent, and past
    events simply fall outside the caller's date filter.
    """
    rng = (f"{date.today().strftime('%Y%m%d')}-"
           f"{(date.today() + timedelta(days=days)).strftime('%Y%m%d')}")
    try:
        r = requests.get(ESPN_SCOREBOARD, params={'dates': rng}, timeout=20)
    except requests.exceptions.RequestException as e:
        print(f'  ⚠ ESPN scoreboard unreachable: {e}')
        return []
    if r.status_code != 200:
        print(f'  ⚠ ESPN scoreboard: {r.status_code}')
        return []
    events = (r.json().get('events') or [])
    if not events:
        print('  no upcoming events on ESPN')
        return []
    return sorted(events, key=lambda e: str(e.get('date') or ''))


def find_fighter_url(name: str) -> str | None:
    """Look up ufc_fighter_stats.fighter_url via ILIKE on normalized name."""
    if not name: return None
    # Try exact first (most performant)
    r = requests.get(
        f'{SB}/rest/v1/ufc_fighter_stats',
        params={'fighter_name': f'eq.{name}', 'select': 'fighter_url,fighter_name', 'limit': '1'},
        headers=H_READ, timeout=10,
    )
    d = r.json() if r.status_code == 200 else []
    if d: return d[0].get('fighter_url')
    # Fallback ilike
    r = requests.get(
        f'{SB}/rest/v1/ufc_fighter_stats',
        params={'fighter_name': f'ilike.{name}', 'select': 'fighter_url,fighter_name', 'limit': '5'},
        headers=H_READ, timeout=10,
    )
    d = r.json() if r.status_code == 200 else []
    if not d: return None
    # Prefer exact normalized match
    target = _normalize_name(name)
    for row in d:
        if _normalize_name(row.get('fighter_name', '')) == target:
            return row.get('fighter_url')
    return d[0].get('fighter_url')


def build_fights_array(espn_event: dict) -> list:
    """Convert ESPN competitions into the fight_card array the scorer expects.

    Schema per existing ufc_upcoming_event.fight_card entries:
      {'fighter1': str, 'fighter2': str, 'rounds_sched': int}

    Fighter URLs get resolved at scoring time by ufc_score_card.fetch_fighter_url —
    no need to enrich here.
    """
    # 2026-08-14 FIX: previously `rounds = 5 if i == 0 else 3` marked the
    # FIRST enumerated fight as main event (5rd). But ESPN returns fights
    # in prelim→main event order, so i==0 is the OPENER not the main event.
    # UFC 330 case: Wells/Orolbai (opener, fight #1) was marked 5rd, and
    # Makhachev/Garry (actual main event) was marked 3rd — model was
    # scoring the wrong round-count. Fix: main event is the LAST fight
    # in the list, so i == last_index gets 5rd.
    competitions = espn_event.get('competitions') or []
    valid_indices = [
        i for i, comp in enumerate(competitions)
        if len(comp.get('competitors') or []) >= 2
        and (comp['competitors'][0].get('athlete') or {}).get('displayName')
        and (comp['competitors'][1].get('athlete') or {}).get('displayName')
    ]
    main_event_idx = valid_indices[-1] if valid_indices else -1
    fights = []
    for i, comp in enumerate(competitions):
        competitors = comp.get('competitors') or []
        if len(competitors) < 2:
            continue
        a_ath = (competitors[0].get('athlete') or {})
        b_ath = (competitors[1].get('athlete') or {})
        a_name = a_ath.get('displayName') or a_ath.get('fullName') or ''
        b_name = b_ath.get('displayName') or b_ath.get('fullName') or ''
        if not a_name or not b_name:
            continue
        # Main event (last fight) = 5rd, others = 3rd.
        # PPV title bouts + co-main are also often 5rd, but ESPN doesn't
        # flag distinctly. Ship the main-event-only rule as the safe
        # default; co-main override can come as a per-event exception.
        rounds = 5 if i == main_event_idx else 3
        fights.append({
            'fighter1': a_name,
            'fighter2': b_name,
            'rounds_sched': rounds,
            'fight_order': i + 1,
        })
    return fights


def upsert_upcoming_event(event_name: str, event_date_iso: str, fights: list, dry_run: bool = False) -> bool:
    # ufc_upcoming_event uses fight_card (JSONB), event_date is TEXT (readable).
    # Convert ISO date to readable "August 01, 2026" format.
    date_display = event_date_iso
    try:
        dt = datetime.fromisoformat(event_date_iso.replace('Z', '+00:00'))
        date_display = dt.strftime('%B %d, %Y')
    except Exception:
        pass
    payload = {
        'event_name': event_name,
        'event_date': date_display,
        'fight_card': fights,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }
    if dry_run:
        print(f'  [DRY] would upsert {event_name} · {event_date_iso} · {len(fights)} fights')
        return True
    r = requests.post(
        f'{SB}/rest/v1/ufc_upcoming_event?on_conflict=event_name',
        headers=H_WRITE, json=payload, timeout=20,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return False
    return True


def run(dry_run: bool = False) -> None:
    print(f'=== UFC card scraper v3 (ESPN) · {datetime.now(timezone.utc).date()} ===')
    events = fetch_forward_espn_events()
    if not events:
        return
    print(f'  forward UFC events: {len(events)}')

    # 2026-09-22 WRITE ORDER IS A CONTRACT. Four consumers
    # (pull_externals_ufc, ufc_predict, ufc_score_card, and the app)
    # all resolve "the upcoming card" with
    #     order=updated_at.desc & limit=1
    # That was correct while this script wrote exactly ONE event.
    # Storing the forward slate broke it silently: writing soonest
    # first meant the FURTHEST-OUT card was written last and became
    # "latest". pull_externals_ufc immediately started pulling for
    # Oct 17 instead of the Sept 26 card.
    #
    # event_date is stored as TEXT ("October 17, 2026") so consumers
    # cannot simply order by it — "September" sorts after "October"
    # alphabetically. Until they select on a parsed date (BACKLOG),
    # writing furthest-first keeps the soonest card newest and every
    # existing consumer correct without changing four call sites
    # that cannot all be tested from here.
    events = list(reversed(events))

    wrote = skipped = 0
    for ev in events:
        event_name = ev.get('name', 'Unknown event')
        event_date = ev.get('date', '')
        fights = build_fights_array(ev)
        if not fights:
            # Cards are announced before the bouts are listed. Not an
            # error — just nothing to store for this one yet.
            print(f'  · {event_name} ({event_date[:10]}) — no fights listed yet')
            skipped += 1
            continue
        print(f'  → {event_name} ({event_date[:10]}) · {len(fights)} fights')
        for f in fights[:3]:
            print(f'      [{f["fight_order"]}] {f["fighter1"]} vs {f["fighter2"]}')
        if upsert_upcoming_event(event_name, event_date, fights, dry_run=dry_run):
            wrote += 1

    print(f'\n{"[DRY] " if dry_run else "✓ "}{wrote} event(s) written'
          + (f', {skipped} awaiting fight listings' if skipped else ''))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
