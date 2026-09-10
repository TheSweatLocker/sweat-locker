"""One-shot backfill: force every current jerry_reads row to align with
ensemble primary_play.

Runs the same rule as enforce_primary_play_alignment() from
jerry_reads_dual_write.py, applied AFTER-THE-FACT to rows already written.
Fixes the launch-weekend inventory: BAL@IND, BUF@HOU, and every other
soft-signal game where the badge and the prose disagreed.

CLI:
    python backfill_jerry_pick_alignment.py                # all sports, next 7 days
    python backfill_jerry_pick_alignment.py --sport NFL    # single sport
    python backfill_jerry_pick_alignment.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import datetime, timedelta, timezone
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
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

_CTX_TABLE = {
    'MLB': 'mlb_game_context',
    'NFL': 'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NBA': 'nba_game_context',
    'NCAAB': 'ncaab_game_context',
    'NHL': 'nhl_game_context',
}
_VALID_MARKETS_BY_SPORT = {
    'MLB':   {'ml', 'rl', 'total', 'nrfi', 'yrfi'},
    'NFL':   {'ml', 'rl', 'spread', 'total'},
    'NCAAF': {'ml', 'rl', 'spread', 'total'},
    'NBA':   {'ml', 'rl', 'spread', 'total'},
    'NCAAB': {'ml', 'rl', 'spread', 'total'},
    'NHL':   {'ml', 'rl', 'puckline', 'total'},
    'UFC':   {'ml', 'fight'},
}


def load_ctx(sport: str, date_from: str, date_to: str) -> dict:
    tbl = _CTX_TABLE.get(sport)
    if not tbl: return {}
    r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_READ,
        params={'game_date': f'gte.{date_from}',
                'and': f'(game_date.lte.{date_to})',
                'select': 'game_id,home_team,away_team,primary_play',
                'limit': '600'}, timeout=25)
    out = {}
    for row in (r.json() if isinstance(r.json(), list) else []):
        if row.get('game_id'):
            out[row['game_id']] = row
    return out


def load_reads(sport: str, date_from: str, date_to: str) -> list:
    r = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H_READ,
        params={'sport': f'eq.{sport}',
                'game_date': f'gte.{date_from}',
                'and': f'(game_date.lte.{date_to})',
                'select': 'id,game_id,game_date,call_market,call_side,call_line,call_text,conviction,short_read',
                'limit': '600'}, timeout=25)
    return r.json() if isinstance(r.json(), list) else []


def align_row(sport: str, read_row: dict, pp: dict, ctx_home: str, ctx_away: str) -> dict | None:
    """Return the PATCH payload or None if no change needed."""
    if not isinstance(pp, dict) or not pp: return None
    market = str(pp.get('type') or '').lower()
    side = pp.get('side')
    label = pp.get('label')
    conviction = pp.get('conviction')
    line = pp.get('line')
    tier = str(pp.get('tier') or '').upper()
    valid_markets = _VALID_MARKETS_BY_SPORT.get(sport.upper(), set())
    # Soft-tier / no-pick → force PASS badge, preserve prose
    if tier in ('COVERAGE', 'PASS', 'SKIP') or market not in valid_markets or not side or not label:
        target = {
            'call_market': 'pass', 'call_side': None, 'call_line': None,
            'call_text': 'Pass', 'conviction': 0,
        }
    else:
        target = {
            'call_market': market,
            'call_side': str(side).upper(),
            'call_line': line,
            'call_text': label,
            'conviction': max(0, min(100, int(conviction))) if isinstance(conviction, (int, float)) else read_row.get('conviction') or 0,
        }
    # Compare to existing — build diff-only patch
    patch = {}
    for k, v in target.items():
        cur = read_row.get(k)
        if cur != v:
            patch[k] = v
    return patch or None


def run(sport_filter: str | None, days_ahead: int, dry_run: bool) -> None:
    from datetime import date as _date
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    date_from = today.isoformat()
    date_to = (today + timedelta(days=days_ahead)).isoformat()
    print(f'=== jerry_reads alignment backfill · {date_from} → {date_to}{" · DRY" if dry_run else ""} ===')
    sports = [sport_filter] if sport_filter else ['MLB', 'NFL', 'NCAAF', 'NBA', 'NCAAB', 'NHL']
    total_updated = 0
    for sport in sports:
        ctx_by_gid = load_ctx(sport, date_from, date_to)
        reads = load_reads(sport, date_from, date_to)
        if not reads:
            print(f'  {sport}: no reads in window'); continue
        aligned = 0; unchanged = 0; skipped = 0
        for r in reads:
            gid = r.get('game_id')
            ctx = ctx_by_gid.get(gid) or {}
            pp = ctx.get('primary_play')
            if isinstance(pp, str):
                try: pp = json.loads(pp)
                except (TypeError, ValueError): pp = None
            if not isinstance(pp, dict):
                skipped += 1; continue
            patch = align_row(sport, r, pp, ctx.get('home_team',''), ctx.get('away_team',''))
            if not patch:
                unchanged += 1; continue
            if dry_run:
                aligned += 1
                print(f"  [DRY] {sport} {ctx.get('away_team','?')}@{ctx.get('home_team','?')}: "
                      f"{r.get('call_market')}/{r.get('call_side')}={r.get('call_text')!r} → "
                      f"{patch.get('call_market', r.get('call_market'))}/"
                      f"{patch.get('call_side', r.get('call_side'))}={patch.get('call_text', r.get('call_text'))!r}")
                continue
            pr = requests.patch(f'{SB}/rest/v1/jerry_reads', headers=H_WRITE,
                params={'id': f'eq.{r["id"]}'}, json=patch, timeout=15)
            if pr.status_code in (200, 204):
                aligned += 1
                print(f"  ✓ {sport} {ctx.get('away_team','?')}@{ctx.get('home_team','?')}: "
                      f"{r.get('call_text','?')} → {patch.get('call_text', r.get('call_text'))}"
                      f" ({r.get('call_market')} → {patch.get('call_market', r.get('call_market'))})")
            else:
                skipped += 1
                print(f"  ✗ {sport} gid={gid[:12]}: {pr.status_code} {pr.text[:150]}")
        print(f'  {sport}: aligned={aligned} unchanged={unchanged} skipped(no pp)={skipped}')
        total_updated += aligned
    print(f'\n=== total aligned: {total_updated} ===')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=['MLB','NFL','NCAAF','NBA','NCAAB','NHL'])
    p.add_argument('--days', type=int, default=14)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(args.sport, args.days, args.dry_run)


if __name__ == '__main__':
    main()
