"""Snapshot every shipped prop pick to prop_pick_snapshots at card-lock time.

Runs LAST in the pipeline after all scorers + refit + reconciler have
settled. Captures the IMMUTABLE snapshot of odds/tier/conviction the user
will see when the Sharp Card renders. Grader references this table so
historical PnL uses the odds you were actually facing when you locked in,
not whatever the odds refresh happened to leave on the source row.

Fixes the 8/17 accountability gap where 3 of 15 shipped props had NULL
book_odds and my 30d PnL was ~46% overstated due to flat -110 fallback.

Idempotent: on_conflict=(prop_id, snapshot_source, game_date) DO NOTHING,
so a same-day rerun is a no-op. Different snapshot_source values (morning,
afternoon, card_lock) coexist as separate rows for intra-day analysis.

CLI:
  python snapshot_pick_lock.py                           # today MLB, card_lock
  python snapshot_pick_lock.py --source afternoon
  python snapshot_pick_lock.py --date 2026-08-18
  python snapshot_pick_lock.py --dry-run
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
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=ignore-duplicates,return=minimal'}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


PROP_TABLES = {
    'MLB': 'mlb_pipeline_props',
    'NFL': 'nfl_pipeline_props',
    'NHL': 'nhl_pipeline_props',
    'NBA': 'nba_pipeline_props',
    'NCAAF': 'ncaaf_pipeline_props',
}


def fetch_props(sport: str, game_date: str) -> list[dict]:
    """Only snapshot picks that are actually shipping (PRIME/STRONG/LEAN)."""
    table = PROP_TABLES[sport]
    r = requests.get(
        f'{SB}/rest/v1/{table}',
        headers=H_READ,
        params={
            'game_date': f'eq.{game_date}',
            'tier': 'in.(PRIME,STRONG,LEAN)',
            'select': 'id,game_date,player_name,prop_type,direction,prop_line,'
                      'matchup,book_line,book_over_odds,book_under_odds,'
                      'tier,conviction,refit_conviction',
        },
        timeout=30,
    )
    return r.json() if r.status_code == 200 else []


def fetch_playbook_decisions(sport: str, game_date: str) -> dict:
    """{(player_name, prop_type, direction): {playbook_tier, playbook_conviction, playbook_side}}"""
    r = requests.get(
        f'{SB}/rest/v1/prop_playbook_decisions',
        headers=H_READ,
        params={
            'game_date': f'eq.{game_date}',
            'sport': f'eq.{sport}',
            'select': 'player_name,prop_type,direction,playbook_tier,'
                      'playbook_conviction,playbook_side',
        },
        timeout=20,
    )
    if r.status_code != 200:
        return {}
    return {(d['player_name'], d['prop_type'], d['direction']): d
            for d in (r.json() or [])}


def run(sport: str = 'MLB', game_date: str | None = None,
         source: str = 'card_lock', dry_run: bool = False):
    gd = game_date or _et_today()
    print(f'=== snapshot_pick_lock · {sport} · {gd} · source={source}'
          f'{" [DRY]" if dry_run else ""} ===')

    props = fetch_props(sport, gd)
    if not props:
        print(f'  no shipping picks for {gd}'); return
    print(f'  {len(props)} shipping picks (PRIME/STRONG/LEAN)')

    pb_lookup = fetch_playbook_decisions(sport, gd)
    print(f'  {len(pb_lookup)} playbook decisions available for cross-stamp')

    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = []
    null_odds = 0
    for p in props:
        pdir = p.get('direction')
        odds = p.get('book_over_odds') if pdir == 'over' else p.get('book_under_odds')
        if odds is None: null_odds += 1

        pb = pb_lookup.get((p.get('player_name'), p.get('prop_type'), pdir), {})

        payloads.append({
            'prop_id': p['id'],
            'sport': sport,
            'game_date': p['game_date'],
            'snapshotted_at': now_iso,
            'snapshot_source': source,
            'player_name': p.get('player_name'),
            'prop_type': p.get('prop_type'),
            'direction': pdir,
            'prop_line': p.get('prop_line'),
            'matchup': p.get('matchup'),
            'book_line': p.get('book_line'),
            'book_over_odds': p.get('book_over_odds'),
            'book_under_odds': p.get('book_under_odds'),
            'legacy_tier': p.get('tier'),
            'legacy_conviction': p.get('conviction'),
            'refit_conviction': p.get('refit_conviction'),
            'playbook_tier': pb.get('playbook_tier'),
            'playbook_conviction': pb.get('playbook_conviction'),
            'playbook_side': pb.get('playbook_side'),
        })

    if null_odds:
        print(f'  WARNING: {null_odds}/{len(props)} picks have NULL book_odds '
              f'— grading will fall back to -110 (accountability gap)')

    if dry_run:
        print(f'  [DRY] would write {len(payloads)} snapshot rows')
        for p in payloads[:5]:
            odds = p['book_over_odds'] if p['direction']=='over' else p['book_under_odds']
            print(f"    [{p['legacy_tier']:<6}] {p['player_name'][:22]:<22} "
                  f"{p['prop_type']:<10} {p['direction']:<5} odds={odds}  "
                  f"pb_tier={p.get('playbook_tier')}")
        return

    written = 0
    for i in range(0, len(payloads), 100):
        chunk = payloads[i:i+100]
        pr = requests.post(
            f'{SB}/rest/v1/prop_pick_snapshots'
            f'?on_conflict=prop_id,snapshot_source,game_date',
            headers=H_WRITE, json=chunk, timeout=30,
        )
        if pr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  x write chunk {i}: {pr.status_code} {pr.text[:200]}')

    print(f'  ✓ upserted {written} snapshot rows (idempotent per prop+source+date)')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB', choices=list(PROP_TABLES.keys()))
    p.add_argument('--date')
    p.add_argument('--source', default='card_lock',
                   choices=['card_lock', 'morning', 'afternoon', 'manual'])
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(sport=args.sport, game_date=args.date,
        source=args.source, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
