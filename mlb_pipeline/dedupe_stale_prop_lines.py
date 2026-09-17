"""Dedup stale prop-line duplicates in mlb_pipeline_props.

2026-09-17 · post-Lugo-outs-dup fix. `generate_props.upsert_props`
uses (game_date, player_name, prop_type, direction, prop_line) as the
uniqueness key. When a re-attach lands a NEW prop_line (Fliff 15.5 →
FanDuel 16.5 for Seth Lugo outs_under 2026-09-17), the upsert creates
a SECOND row instead of updating the first — because prop_line differs.
Result: two live rows for the same (player, prop_type, direction),
both publishable, both showing in the app.

Structural fix path (queued): drop prop_line from the conflict key +
migrate to (game_date, player_name, prop_type, direction) uniqueness.
Requires purging existing dups + a schema constraint change. Not
shipped today.

Surgical fix (this script): scan today's props, group by (game_date,
player_name, prop_type, direction), keep the row with MAX
last_attached_at (most recent book scrape), mark all OTHERS as SKIP
so they drop out of v_mlb_props_publishable.

Idempotent: running twice on a clean slate is a no-op.

Runs in the daily workflow after generate_props + attach_book_lines +
prop_ensemble_scorer, before generate_prop_jerry_synthesis. Add a
step to the MLB pipeline workflow to call this.

CLI:
  python dedupe_stale_prop_lines.py                 # today ET
  python dedupe_stale_prop_lines.py --date YYYY-MM-DD
  python dedupe_stale_prop_lines.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from collections import defaultdict
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
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _fetch_props(game_date: str) -> list[dict]:
    """Paginated pull — dodges the 1000-row PostgREST cap. Only fields
    needed for dedup decisions."""
    all_rows: list[dict] = []
    select = 'id,player_name,prop_type,direction,prop_line,book_source,tier,last_attached_at'
    for page in range(20):
        r = requests.get(
            f'{SB}/rest/v1/mlb_pipeline_props',
            headers={**H_READ, 'Range-Unit': 'items',
                     'Range': f'{page*1000}-{(page+1)*1000-1}'},
            params={'game_date': f'eq.{game_date}', 'select': select},
            timeout=30,
        )
        if r.status_code not in (200, 206): break
        page_rows = r.json() or []
        if not isinstance(page_rows, list): break
        all_rows.extend(page_rows)
        if len(page_rows) < 1000: break
    return all_rows


def _skip_row(row_id: int, note: str, dry_run: bool) -> bool:
    """Mark a row as SKIP with conviction 0."""
    if dry_run: return True
    r = requests.patch(
        f'{SB}/rest/v1/mlb_pipeline_props?id=eq.{row_id}',
        headers=H_WRITE,
        json={'tier': 'SKIP', 'conviction': 0},
        timeout=15,
    )
    ok = r.status_code in (200, 204)
    if not ok:
        print(f'    ✗ skip patch failed for id={row_id}: {r.status_code} {r.text[:120]}')
    return ok


def dedupe(game_date: str, dry_run: bool = False) -> dict:
    print(f'=== dedupe_stale_prop_lines · {game_date}{" [DRY]" if dry_run else ""} ===')
    rows = _fetch_props(game_date)
    print(f'  scanned {len(rows)} props')

    # Group by (player, prop_type, direction) — ignore prop_line
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        key = (r.get('player_name'), r.get('prop_type'), r.get('direction'))
        groups[key].append(r)

    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    print(f'  found {len(dup_groups)} (player, prop_type, direction) groups with >1 row')
    if not dup_groups:
        return {'scanned': len(rows), 'dup_groups': 0, 'skipped': 0}

    skipped = 0
    for key, dup_rows in dup_groups.items():
        # Sort: latest last_attached_at first. Rows with a null
        # last_attached_at sort last (older/unattached).
        def _sort_key(r):
            ts = r.get('last_attached_at') or ''
            return ts
        dup_rows.sort(key=_sort_key, reverse=True)
        winner = dup_rows[0]
        losers = dup_rows[1:]
        pn, pt, dr = key
        print(f'  {pn} · {pt} · {dr}:')
        print(f'    KEEP id={winner["id"]} line={winner.get("prop_line")} '
              f'src={winner.get("book_source")!r} attached={winner.get("last_attached_at")}')
        for l in losers:
            already_skip = (l.get('tier') or '').upper() == 'SKIP'
            note = ' (already SKIP)' if already_skip else ''
            print(f'    SKIP id={l["id"]} line={l.get("prop_line")} '
                  f'src={l.get("book_source")!r} attached={l.get("last_attached_at")}{note}')
            if already_skip: continue
            if _skip_row(l['id'], 'stale line dup', dry_run):
                skipped += 1

    print(f'\n  {"[DRY] " if dry_run else ""}marked {skipped} stale row(s) SKIP across {len(dup_groups)} dup groups')
    return {'scanned': len(rows), 'dup_groups': len(dup_groups), 'skipped': skipped}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    gd = args.date or _today_et()
    dedupe(gd, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
