"""NCAAB post-game resolver — pull ESPN scoreboard, refresh scores,
compute outcomes, then kick the sport-agnostic external picks grader.

Runs nightly during CBB season on the NCAAB workflow cron. Uses ESPN's
free public scoreboard endpoint (no auth) as the score source of truth
— CFB has CFBD, MLB has stats.mlb, CBB has no equivalent free feed so
ESPN it is.

Flow:
  1. Iterate a window of dates (default: yesterday + today, catches
     west-coast late finals + any pending scores).
  2. For each date, pull ESPN /scoreboard?dates=YYYYMMDD.
  3. Match each game's teams to canonical names via ncaab_team_aliases
     (espn_id primary, location fallback).
  4. Compute game_id = ncaab_YYYYMMDD_AWAY_HOME to match odds_pull.
  5. PATCH ncaab_game_results with home_score/away_score/spread_result/
     total_result/home_win.
  6. Kick resolve_externals.py --sport NCAAB to grade any external picks.

Idempotent — only writes when scores changed.

USAGE:
    python resolve_ncaab_results.py                 # yesterday + today
    python resolve_ncaab_results.py --days-back 7   # last 7 days
    python resolve_ncaab_results.py --date 20250301 # specific date
    python resolve_ncaab_results.py --dry-run
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
SB_KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

ESPN_BASE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball'
GAP_LOG_PATH = Path(__file__).parent / 'ncaab_alias_gaps.log'


def _f(v):
    try: return float(v) if v not in (None, '') else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(float(v)) if v not in (None, '') else None
    except (TypeError, ValueError): return None


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


# ─── alias loading ────────────────────────────────────────────────

def load_alias_lookups() -> tuple[dict, dict]:
    """Returns (by_espn_id, by_name) lookup dicts.
      by_espn_id: {espn_id_str: canonical_name}  — primary matcher (bulletproof)
      by_name:    {name.lower(): canonical_name} — fallback for teams
                  not yet enriched with espn_id (e.g., recent D1 arrivals
                  that get added via scoreboard pull first)
    """
    r = requests.get(
        f'{SB}/rest/v1/ncaab_team_aliases'
        '?select=canonical_name,odds_api_name,alt_names,espn_id',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200:
        print(f'  ⚠ alias fetch failed: {r.status_code}')
        return {}, {}
    by_espn, by_name = {}, {}
    for row in r.json():
        canon = row['canonical_name']
        if row.get('espn_id'):
            by_espn[str(row['espn_id'])] = canon
        by_name[canon.lower()] = canon
        if row.get('odds_api_name'):
            by_name[row['odds_api_name'].lower()] = canon
        for alt in (row.get('alt_names') or []):
            if alt: by_name[alt.lower()] = canon
    return by_espn, by_name


def resolve_team(espn_team: dict, by_espn: dict, by_name: dict) -> Optional[str]:
    """espn_id primary, then location/displayName/shortDisplayName fallbacks."""
    eid = str(espn_team.get('id')) if espn_team.get('id') else None
    if eid and eid in by_espn:
        return by_espn[eid]
    for k in ('location', 'displayName', 'shortDisplayName', 'name'):
        v = espn_team.get(k)
        if v and v.lower() in by_name:
            return by_name[v.lower()]
    return None


def _log_gap(entry: dict) -> None:
    """Append an unresolved-team record to the gap audit for manual review."""
    try:
        with GAP_LOG_PATH.open('a', encoding='utf-8') as f:
            f.write(f'{datetime.now(timezone.utc).isoformat()} RESOLVE_GAP '
                    f'{json.dumps(entry)}\n')
    except Exception:
        pass


# ─── ESPN scoreboard ──────────────────────────────────────────────

def fetch_espn_scoreboard(date_str: str) -> list:
    """Returns list of ESPN 'events' for a given YYYYMMDD."""
    url = f'{ESPN_BASE}/scoreboard?dates={date_str}&limit=500'
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
        r.raise_for_status()
        return r.json().get('events', []) or []
    except Exception as e:
        print(f'  ⚠ ESPN scoreboard {date_str}: {e}')
        return []


def event_to_outcome(event: dict, by_espn: dict, by_name: dict) -> Optional[dict]:
    """Extract (game_id, home_team, away_team, scores, status) from an ESPN event.
    Returns None if either team can't map or game is not yet final."""
    comp = (event.get('competitions') or [{}])[0]
    competitors = comp.get('competitors') or []
    if len(competitors) != 2:
        return None

    # ESPN: homeAway="home" | "away"
    home_c = next((c for c in competitors if c.get('homeAway') == 'home'), None)
    away_c = next((c for c in competitors if c.get('homeAway') == 'away'), None)
    if not (home_c and away_c):
        return None

    home_team = resolve_team(home_c.get('team', {}), by_espn, by_name)
    away_team = resolve_team(away_c.get('team', {}), by_espn, by_name)
    if not home_team or not away_team:
        # Log which side(s) failed so gaps are actionable
        _log_gap({
            'espn_event_id': event.get('id'),
            'date': event.get('date'),
            'home': home_c.get('team', {}).get('displayName'),
            'home_id': home_c.get('team', {}).get('id'),
            'home_resolved': home_team,
            'away': away_c.get('team', {}).get('displayName'),
            'away_id': away_c.get('team', {}).get('id'),
            'away_resolved': away_team,
        })
        return None

    home_score = _i(home_c.get('score'))
    away_score = _i(away_c.get('score'))
    status = ((event.get('status') or {}).get('type') or {}).get('state')
    completed = status == 'post'   # ESPN: pre | in | post

    commence = event.get('date', '')
    try:
        dt = datetime.fromisoformat(commence.replace('Z', '+00:00'))
        game_date = dt.date().isoformat()
        game_id = f'ncaab_{dt.strftime("%Y%m%d")}_{away_team}_{home_team}'.replace(' ', '_')
    except Exception:
        return None

    return {
        'game_id': game_id,
        'game_date': game_date,
        'home_team': home_team,
        'away_team': away_team,
        'home_score': home_score if completed else None,
        'away_score': away_score if completed else None,
        'completed': completed,
    }


# ─── DB layer ─────────────────────────────────────────────────────

def fetch_existing(game_ids: list) -> dict:
    if not game_ids: return {}
    out = {}
    for i in range(0, len(game_ids), 100):
        chunk = game_ids[i:i+100]
        ids = ','.join(f'"{gid}"' for gid in chunk)
        r = requests.get(
            f'{SB}/rest/v1/ncaab_game_results?game_id=in.({ids})'
            f'&select=game_id,home_score,away_score,close_spread,close_total',
            headers=H_READ, timeout=30,
        )
        if r.status_code != 200: continue
        for row in r.json():
            out[row['game_id']] = row
    return out


def compute_outcome_patch(new: dict, existing: dict) -> Optional[dict]:
    """Build PATCH payload. Returns None if unchanged / not final."""
    hs = new.get('home_score')
    as_ = new.get('away_score')
    if hs is None or as_ is None:
        return None
    if _i(existing.get('home_score')) == hs and _i(existing.get('away_score')) == as_:
        return None

    payload = {
        'home_score': hs,
        'away_score': as_,
        'total_points': hs + as_,
        'home_win': hs > as_,
        'result_logged_at': datetime.now(timezone.utc).isoformat(),
    }

    # NCAAB convention: close_spread negative = home favored.
    # spread_result semantics: home_covered if home_margin > -close_spread.
    cs = _f(existing.get('close_spread'))
    if cs is not None:
        margin = hs - as_
        if margin > -cs:   payload['spread_result'] = 'home_covered'
        elif margin < -cs: payload['spread_result'] = 'away_covered'
        else:              payload['spread_result'] = 'push'

    ct = _f(existing.get('close_total'))
    if ct is not None:
        tot = hs + as_
        if tot > ct:   payload['total_result'] = 'over'
        elif tot < ct: payload['total_result'] = 'under'
        else:          payload['total_result'] = 'push'

    return payload


def upsert_new_games(new_rows: list, dry_run: bool = False) -> int:
    """For games from ESPN not yet in ncaab_game_results (no odds row),
    insert a minimal row so the resolver has something to update.
    This catches unlined games (scrimmages, exhibition, MTE tournaments)."""
    if not new_rows: return 0
    payload = [{
        'game_id':   n['game_id'],
        'game_date': n['game_date'],
        'home_team': n['home_team'],
        'away_team': n['away_team'],
        'home_score': n.get('home_score'),
        'away_score': n.get('away_score'),
        'total_points': (n.get('home_score') or 0) + (n.get('away_score') or 0)
                        if n.get('home_score') is not None and n.get('away_score') is not None
                        else None,
        'home_win': n.get('home_score') > n.get('away_score')
                    if n.get('home_score') is not None and n.get('away_score') is not None
                    else None,
        'result_logged_at': datetime.now(timezone.utc).isoformat(),
    } for n in new_rows]
    if dry_run:
        print(f'  [DRY] would INSERT {len(payload)} unlined games')
        return len(payload)
    r = requests.post(
        f'{SB}/rest/v1/ncaab_game_results?on_conflict=game_id',
        headers={**H_WRITE, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
        json=payload, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ new-game insert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(payload)


def apply_patches(patches: list, dry_run: bool = False) -> int:
    if not patches: return 0
    if dry_run:
        for gid, payload in patches[:10]:
            print(f'  [DRY] {gid}: {payload.get("home_score")}-{payload.get("away_score")} '
                  f'sp={payload.get("spread_result")} tot={payload.get("total_result")}')
        if len(patches) > 10:
            print(f'  [DRY] ... {len(patches)-10} more')
        return len(patches)
    updated = 0
    for gid, payload in patches:
        r = requests.patch(
            f'{SB}/rest/v1/ncaab_game_results?game_id=eq.{gid}',
            headers=H_WRITE, json=payload, timeout=15,
        )
        if r.status_code in (200, 201, 204):
            updated += 1
        else:
            print(f'  ⚠ patch {gid} failed {r.status_code}: {r.text[:120]}')
    return updated


def kick_external_resolver() -> None:
    """Fire sport-agnostic external picks resolver for NCAAB."""
    print(f'\n=== Kicking resolve_externals --sport NCAAB ===')
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resolve_externals.py')
    if not os.path.exists(script):
        print(f'  (resolve_externals.py not found — skip)'); return
    try:
        result = subprocess.run(
            [sys.executable, script, '--sport', 'NCAAB', '--days', '14'],
            capture_output=True, text=True, timeout=120,
        )
        if result.stdout:
            print('\n'.join(result.stdout.splitlines()[-8:]))
    except Exception as e:
        print(f'  ⚠ external resolver kickoff failed: {e}')


# ─── main ─────────────────────────────────────────────────────────

def run(dates: list, dry_run: bool = False, skip_external: bool = False) -> None:
    print(f'=== NCAAB resolver · dates={dates} ===')
    by_espn, by_name = load_alias_lookups()
    if not by_espn and not by_name:
        print('  ✗ alias table empty — run ncaab_seed_aliases.py then ncaab_enrich_aliases.py')
        return
    print(f'  aliases: {len(by_espn)} by ESPN id, {len(by_name)} by name variant')

    all_outcomes = []
    total_events = 0
    for date_str in dates:
        events = fetch_espn_scoreboard(date_str)
        total_events += len(events)
        for e in events:
            o = event_to_outcome(e, by_espn, by_name)
            if o: all_outcomes.append(o)
        time.sleep(0.4)  # polite pacing on ESPN
    print(f'  ESPN events pulled: {total_events} (resolved {len(all_outcomes)})')

    finals = [o for o in all_outcomes if o['completed']]
    print(f'  finals in window: {len(finals)}')
    if not finals:
        print('  (no completed games)'); return

    existing = fetch_existing([o['game_id'] for o in finals])
    print(f'  existing rows matched: {len(existing)}/{len(finals)}')

    patches = []
    new_games = []
    for o in finals:
        ex = existing.get(o['game_id'])
        if not ex:
            new_games.append(o)  # unlined game, insert minimal row
            continue
        payload = compute_outcome_patch(o, ex)
        if payload:
            patches.append((o['game_id'], payload))

    updated = apply_patches(patches, dry_run=dry_run)
    inserted = upsert_new_games(new_games, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'{prefix}patched {updated}, inserted {inserted} unlined')

    if not skip_external and not dry_run:
        kick_external_resolver()


def _date_range(days_back: int) -> list:
    today = _et_now().date()
    return [(today - timedelta(days=d)).strftime('%Y%m%d') for d in range(days_back + 1)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='Single YYYYMMDD (e.g., 20250301)')
    ap.add_argument('--days-back', type=int, default=1,
                    help='How many days back from today to resolve (default: 1 = yesterday + today)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--skip-external', action='store_true')
    args = ap.parse_args()

    if args.date:
        dates = [args.date]
    else:
        dates = _date_range(args.days_back)

    run(dates, dry_run=args.dry_run, skip_external=args.skip_external)


if __name__ == '__main__':
    main()
