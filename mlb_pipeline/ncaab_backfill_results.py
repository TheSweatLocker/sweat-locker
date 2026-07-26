"""NCAAB historical backfill via ESPN scoreboard.

Iterates a date range, pulls ESPN /scoreboard?dates=YYYYMMDD, and
upserts all games (with scores + opening/closing odds where ESPN provides
them) into ncaab_game_results.

Rationale:
  CBB has no CFBD-equivalent free API. ESPN's public scoreboard endpoint
  returns per-game state including final scores and (for a subset of
  games) odds from participating books. Not every game gets odds
  attached — early-season Sun Belt games often lack them — but scores
  are near-complete for the modern era.

Sign convention (matches ncaab_game_context + ncaab_odds_pull):
  close_spread NEGATIVE = home favored. ESPN's `odds[0].details` typically
  encodes as "TEAM ±X" — we parse and store home-perspective spread.

USAGE:
    python ncaab_backfill_results.py --season 2024-25       # Nov 2024 → Apr 2025
    python ncaab_backfill_results.py --season 2024-25 --dry-run
    python ncaab_backfill_results.py --start 20250201 --end 20250228
"""
import argparse
import json
import os
import re
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
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

ESPN_BASE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball'
GAP_LOG_PATH = Path(__file__).parent / 'ncaab_alias_gaps.log'

# CBB regular season is roughly Nov 1 → Mar 15, NCAA Tournament Mar 18 → early Apr.
SEASON_WINDOWS = {
    '2020-21': ('20201125', '20210405'),
    '2021-22': ('20211109', '20220404'),
    '2022-23': ('20221107', '20230403'),
    '2023-24': ('20231106', '20240408'),
    '2024-25': ('20241104', '20250407'),
    '2025-26': ('20251104', '20260406'),
}


def _f(v):
    try: return float(v) if v not in (None, '') else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(float(v)) if v not in (None, '') else None
    except (TypeError, ValueError): return None


# ─── alias loading ────────────────────────────────────────────────

def load_alias_lookups() -> tuple[dict, dict]:
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


def resolve_team(t: dict, by_espn: dict, by_name: dict) -> Optional[str]:
    eid = str(t.get('id')) if t.get('id') else None
    if eid and eid in by_espn:
        return by_espn[eid]
    for k in ('location', 'displayName', 'shortDisplayName', 'name'):
        v = t.get(k)
        if v and v.lower() in by_name:
            return by_name[v.lower()]
    return None


def log_gap(entry: dict) -> None:
    try:
        with GAP_LOG_PATH.open('a', encoding='utf-8') as f:
            f.write(f'{datetime.now(timezone.utc).isoformat()} BACKFILL_GAP '
                    f'{json.dumps(entry)}\n')
    except Exception:
        pass


# ─── ESPN parsing ─────────────────────────────────────────────────

def fetch_scoreboard(yyyymmdd: str) -> list:
    # groups=50 unlocks full D1 coverage (2026-07-25 Phase 1b fix).
    # Without groups param, ESPN returns only ~15-30 "featured" games/day.
    # With groups=50: ~130-200 games/day = full D1 slate. Test on 2025-03-01
    # showed 133 events with groups=50 vs 16 without — 8x coverage jump.
    url = f'{ESPN_BASE}/scoreboard?dates={yyyymmdd}&groups=50&limit=500'
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
        r.raise_for_status()
        return r.json().get('events', []) or []
    except Exception as e:
        print(f'  ⚠ ESPN {yyyymmdd}: {e}')
        return []


def _parse_odds(comp: dict, home_abbr: str, away_abbr: str) -> dict:
    """Extract close_spread + close_total from ESPN's odds block if present.
    ESPN encodes spread as 'details' like 'DUKE -3.5' or 'PK' for pick.
    Returns {close_spread: float or None, close_total: float or None}.
    close_spread convention: NEGATIVE = home favored."""
    out = {'close_spread': None, 'close_total': None,
           'home_ml_close': None, 'away_ml_close': None}
    odds_list = comp.get('odds') or []
    if not odds_list: return out
    o = odds_list[0]  # ESPN provider ranking — first is typically primary

    ou = _f(o.get('overUnder'))
    if ou is not None:
        out['close_total'] = ou

    # Spread — ESPN uses `spread` field (home-perspective) or `details`
    sp_raw = o.get('spread')
    if sp_raw is not None:
        # ESPN convention: positive spread = HOME favored by X (per docs).
        # We store negative = home favored, so flip.
        out['close_spread'] = -1 * _f(sp_raw)
    else:
        # Fallback: parse 'details' if present, e.g. "DUKE -3.5"
        details = o.get('details') or ''
        m = re.match(r'^([A-Z&]+)\s+([+-]?\d+(?:\.\d+)?|PK)$', details.strip())
        if m:
            team_abbr, pt_raw = m.group(1), m.group(2)
            pt = 0.0 if pt_raw == 'PK' else _f(pt_raw)
            if pt is not None:
                if team_abbr == home_abbr: out['close_spread'] = -pt if pt > 0 else abs(pt)
                elif team_abbr == away_abbr: out['close_spread'] = abs(pt) if pt > 0 else -pt

    # ML — ESPN puts homeMoneyLine / awayMoneyLine on the odds block when present
    if o.get('homeTeamOdds') is not None:
        out['home_ml_close'] = _i((o.get('homeTeamOdds') or {}).get('moneyLine'))
    if o.get('awayTeamOdds') is not None:
        out['away_ml_close'] = _i((o.get('awayTeamOdds') or {}).get('moneyLine'))

    return out


def event_to_row(event: dict, by_espn: dict, by_name: dict, season: str) -> Optional[dict]:
    comp = (event.get('competitions') or [{}])[0]
    competitors = comp.get('competitors') or []
    if len(competitors) != 2:
        return None

    home_c = next((c for c in competitors if c.get('homeAway') == 'home'), None)
    away_c = next((c for c in competitors if c.get('homeAway') == 'away'), None)
    if not (home_c and away_c):
        return None

    home_team = resolve_team(home_c.get('team', {}), by_espn, by_name)
    away_team = resolve_team(away_c.get('team', {}), by_espn, by_name)
    if not home_team or not away_team:
        log_gap({
            'season': season,
            'espn_event_id': event.get('id'),
            'date': event.get('date'),
            'home_disp': home_c.get('team', {}).get('displayName'),
            'home_id':   home_c.get('team', {}).get('id'),
            'home_resolved': home_team,
            'away_disp': away_c.get('team', {}).get('displayName'),
            'away_id':   away_c.get('team', {}).get('id'),
            'away_resolved': away_team,
        })
        return None

    commence = event.get('date', '')
    try:
        dt = datetime.fromisoformat(commence.replace('Z', '+00:00'))
        game_date = dt.date().isoformat()
        game_id = f'ncaab_{dt.strftime("%Y%m%d")}_{away_team}_{home_team}'.replace(' ', '_')
    except Exception:
        return None

    status = ((event.get('status') or {}).get('type') or {}).get('state')
    hs = _i(home_c.get('score')) if status == 'post' else None
    as_ = _i(away_c.get('score')) if status == 'post' else None

    row = {
        'game_id': game_id,
        'game_date': game_date,
        'season': season,
        'home_team': home_team,
        'away_team': away_team,
        'home_score': hs,
        'away_score': as_,
    }
    if hs is not None and as_ is not None:
        row.update({
            'total_points': hs + as_,
            'home_win': hs > as_,
        })

    home_abbr = (home_c.get('team') or {}).get('abbreviation') or ''
    away_abbr = (away_c.get('team') or {}).get('abbreviation') or ''
    odds_fields = _parse_odds(comp, home_abbr, away_abbr)
    row.update(odds_fields)
    # Mirror close → open for backfill (no observed movement history)
    row['open_spread'] = row.get('close_spread')
    row['open_total']  = row.get('close_total')
    row['home_ml_open'] = row.get('home_ml_close')
    row['away_ml_open'] = row.get('away_ml_close')

    # Compute spread_result + total_result if we have both scores + line
    cs = _f(row.get('close_spread'))
    if hs is not None and as_ is not None and cs is not None:
        margin = hs - as_
        if margin > -cs:   row['spread_result'] = 'home_covered'
        elif margin < -cs: row['spread_result'] = 'away_covered'
        else:              row['spread_result'] = 'push'
    ct = _f(row.get('close_total'))
    if hs is not None and as_ is not None and ct is not None:
        tot = hs + as_
        if tot > ct:   row['total_result'] = 'over'
        elif tot < ct: row['total_result'] = 'under'
        else:          row['total_result'] = 'push'

    return row


# ─── DB layer ─────────────────────────────────────────────────────

def _normalize_batch_keys(rows: list) -> list:
    """PGRST102 fix — union keys + backfill None across rows.
    Per feedback_postgrest_batch_normalize_keys memory."""
    if not rows: return rows
    keys = set()
    for r in rows: keys.update(r.keys())
    for r in rows:
        for k in keys: r.setdefault(k, None)
    return rows


def upsert_batch(rows: list, dry_run: bool = False) -> int:
    if not rows: return 0
    rows = _normalize_batch_keys(rows)
    if dry_run:
        print(f'  [DRY] would upsert {len(rows)} rows (sample: {rows[0].get("game_id")})')
        return len(rows)
    r = requests.post(
        f'{SB}/rest/v1/ncaab_game_results?on_conflict=game_id',
        headers=H_WRITE, json=rows, timeout=60,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(rows)


# ─── main ─────────────────────────────────────────────────────────

def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def run(start_yyyymmdd: str, end_yyyymmdd: str, season: str,
        dry_run: bool = False, batch_size: int = 200) -> None:
    print(f'=== NCAAB backfill · {start_yyyymmdd}→{end_yyyymmdd} · season {season} ===')
    by_espn, by_name = load_alias_lookups()
    if not by_espn:
        print('  ✗ alias table not enriched — run ncaab_enrich_aliases.py first'); return
    print(f'  aliases: {len(by_espn)} by ESPN id, {len(by_name)} by name variant')

    start = datetime.strptime(start_yyyymmdd, '%Y%m%d').date()
    end   = datetime.strptime(end_yyyymmdd, '%Y%m%d').date()

    total_events = 0; total_rows = []; total_written = 0
    dates_scanned = 0
    for d in daterange(start, end):
        events = fetch_scoreboard(d.strftime('%Y%m%d'))
        dates_scanned += 1
        total_events += len(events)
        day_rows = []
        for e in events:
            r = event_to_row(e, by_espn, by_name, season)
            if r: day_rows.append(r)
        total_rows.extend(day_rows)

        # Flush in batches so a mid-run crash doesn't lose weeks of work
        if len(total_rows) >= batch_size:
            written = upsert_batch(total_rows, dry_run=dry_run)
            total_written += written
            print(f'  [flush] date={d} cumulative events={total_events} '
                  f'flushed={written} total_written={total_written}')
            total_rows = []

        time.sleep(0.35)  # polite pacing

    # Final flush
    if total_rows:
        written = upsert_batch(total_rows, dry_run=dry_run)
        total_written += written

    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}scanned {dates_scanned} dates · '
          f'{total_events} events · wrote {total_written} rows')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', help='Season key (e.g., 2024-25)')
    ap.add_argument('--start', help='Override start date YYYYMMDD')
    ap.add_argument('--end', help='Override end date YYYYMMDD')
    ap.add_argument('--batch-size', type=int, default=200)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if args.start and args.end:
        season = args.season or f'{args.start[:4]}'
        run(args.start, args.end, season, args.dry_run, args.batch_size)
    elif args.season:
        if args.season not in SEASON_WINDOWS:
            print(f'  ✗ unknown season {args.season}. Known: {list(SEASON_WINDOWS)}')
            return
        s, e = SEASON_WINDOWS[args.season]
        run(s, e, args.season, args.dry_run, args.batch_size)
    else:
        print('  ✗ specify --season or --start + --end')


if __name__ == '__main__':
    main()
