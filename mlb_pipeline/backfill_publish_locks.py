"""Backfill publish_lock from today's live surface caches.

Andy 9/17: shipping the publish-lock system mid-day means TODAY's
already-published picks have no lock rows yet. This script walks the
current cache surfaces (jerry_cache sweat_card + sharp_card entries,
plus prop_jerry_reads for individual prop cards) and locks each pick
at the tier it CURRENTLY shows on the surface.

Not perfect — the surface cache was written earlier today at whatever
tier the row had then. If the LIVE tier has since flipped and the
surface hasn't been regenerated, the surface + DB disagree; we lock
what the SURFACE shows (the honest "what user saw" reading).

CLI:
  python backfill_publish_locks.py                     # write live
  python backfill_publish_locks.py --dry-run           # print only
  python backfill_publish_locks.py --date YYYY-MM-DD
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone, timedelta
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

from prop_publish_lock import lock_publish  # noqa: E402


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _lookup_prop_id(sport: str, game_date: str, player_name: str,
                    prop_type: str, direction: str) -> int | None:
    """Resolve prop_line row id from (sport, game_date, player, type, dir)."""
    tbl = 'mlb_pipeline_props' if sport == 'MLB' else 'nfl_pipeline_props'
    r = requests.get(
        f'{SB}/rest/v1/{tbl}',
        headers=H_READ,
        params={
            'game_date': f'eq.{game_date}',
            'player_name': f'eq.{player_name}',
            'prop_type': f'eq.{prop_type}',
            'direction': f'eq.{direction}',
            'select': 'id',
            'limit': '1',
        },
        timeout=10,
    )
    if r.status_code != 200: return None
    rows = r.json()
    return rows[0]['id'] if rows else None


def backfill_sweat_card(game_date: str, dry_run: bool = False) -> int:
    """Lock every top_8 pick from the Sweat Card cache for `game_date`."""
    r = requests.get(
        f'{SB}/rest/v1/jerry_cache',
        headers=H_READ,
        params={'cache_key': f'eq.sweat_card_{game_date}',
                'select': 'sport,data'},
        timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
    n = 0
    for row in rows:
        sport = row.get('sport') or 'ALL'
        d = row.get('data') or {}
        picks = d.get('top_8') or d.get('picks') or []
        for p in picks:
            if not isinstance(p, dict): continue
            tier = (p.get('tier') or '').upper()
            conv = p.get('conviction')
            source = p.get('source_key') or p.get('source_id') or p.get('id')
            market = p.get('market') or p.get('type')
            pick_sport = (p.get('sport') or sport or '').upper()
            if not source or not market: continue
            if lock_publish(pick_sport, market, source, tier, conv,
                            'sweat_card', dry_run=dry_run):
                n += 1
    return n


def backfill_sharp_card(game_date: str, dry_run: bool = False) -> int:
    """Lock every item from the Sharp Card (Steam Room) cache."""
    r = requests.get(
        f'{SB}/rest/v1/jerry_cache',
        headers=H_READ,
        params={'cache_key': f'eq.sharp_card_{game_date}',
                'select': 'sport,data'},
        timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
    n = 0
    for row in rows:
        sport = row.get('sport') or 'ALL'
        d = row.get('data') or {}
        items = d.get('items') or d.get('picks') or []
        for p in items:
            if not isinstance(p, dict): continue
            tier = (p.get('tier') or '').upper()
            conv = p.get('conviction')
            market = p.get('prop_type') and 'prop' or p.get('market') or p.get('type')
            pick_sport = (p.get('sport') or sport or '').upper()
            if market == 'prop':
                # Sharp Card item for a prop — resolve to mlb_pipeline_props.id
                sid = _lookup_prop_id(
                    pick_sport, game_date,
                    p.get('player_name'), p.get('prop_type'), p.get('direction'),
                )
                if not sid: continue
                if lock_publish(pick_sport, 'prop', sid, tier, conv,
                                'sharp_card', dry_run=dry_run):
                    n += 1
            else:
                # Side pick — source_id = game_id
                gid = p.get('game_id')
                if not gid or not market: continue
                if lock_publish(pick_sport, market, gid, tier, conv,
                                'sharp_card', dry_run=dry_run):
                    n += 1
    return n


def backfill_prop_jerry_reads(game_date: str, dry_run: bool = False) -> int:
    """Lock every prop_jerry_reads row (every prop card that a user
    could have opened on the Prop Jerry tab)."""
    r = requests.get(
        f'{SB}/rest/v1/prop_jerry_reads',
        headers={**H_READ, 'Range-Unit': 'items', 'Range': '0-9999'},
        params={
            'game_date': f'eq.{game_date}',
            'select': 'sport,player_name,prop_type,direction,conviction',
        },
        timeout=20,
    )
    rows = r.json() if r.status_code == 200 else []
    n = 0
    for row in rows:
        sport = (row.get('sport') or '').upper()
        if sport not in ('MLB', 'NFL'): continue
        sid = _lookup_prop_id(
            sport, game_date,
            row.get('player_name'), row.get('prop_type'), row.get('direction'),
        )
        if not sid: continue
        # Prop Jerry stores its own conviction; tier we don't have on
        # the jerry_reads row — fetch it from the source prop instead
        # so we lock the SOURCE tier the composer decided.
        tbl = 'mlb_pipeline_props' if sport == 'MLB' else 'nfl_pipeline_props'
        pr = requests.get(
            f'{SB}/rest/v1/{tbl}',
            headers=H_READ,
            params={'id': f'eq.{sid}', 'select': 'tier,conviction'},
            timeout=10,
        )
        prop = pr.json()[0] if pr.status_code == 200 and pr.json() else {}
        tier = (prop.get('tier') or '').upper()
        conv = prop.get('conviction')
        if lock_publish(sport, 'prop', sid, tier, conv,
                        'prop_jerry', dry_run=dry_run):
            n += 1
    return n


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    gd = args.date or _today_et()

    print(f'=== backfill_publish_locks · {gd}{" [DRY]" if args.dry_run else ""} ===')
    print()

    s = backfill_sweat_card(gd, dry_run=args.dry_run)
    print(f'  sweat_card:        {s} locks')
    p_ = backfill_sharp_card(gd, dry_run=args.dry_run)
    print(f'  sharp_card:        {p_} locks')
    j = backfill_prop_jerry_reads(gd, dry_run=args.dry_run)
    print(f'  prop_jerry_reads:  {j} locks')
    print()
    print(f'  total: {s + p_ + j} locks written')


if __name__ == '__main__':
    main()
