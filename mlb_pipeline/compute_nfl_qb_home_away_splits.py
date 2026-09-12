"""Compute per-QB career + last-10 home/road W-L splits from
nfl_game_results and materialize to nfl_qb_home_away_splits.

Signal thesis
-------------
Home/away QB performance is a well-documented edge that most public
models under-weight. Some QBs are dramatically different on the road
(crowd noise, travel, hostile environments); some are elite at home.
Dome QBs in outdoor cold road games, west-coast QBs in 1pm ET road
games, and rookies making a first road divisional start are all
patterns the market prices off season averages rather than split.

Data source
-----------
nfl_game_results has home_qb_id / away_qb_id / home_win going back to
2020 (~1000 rows total across seasons). No external data pull needed.

Refresh cadence
---------------
Runs weekly on Tuesday morning after MNF. That way the split table
reflects the previous week's outcomes before the current week's
nfl_game_context builds pull it into ctx.

CLI
---
    python compute_nfl_qb_home_away_splits.py                 # live
    python compute_nfl_qb_home_away_splits.py --dry-run       # print plan
    python compute_nfl_qb_home_away_splits.py --qb Mahomes    # single QB
"""
from __future__ import annotations
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

import requests

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

RECENT_N = 10  # last-N starts per side (home / road)


def fetch_results() -> list[dict]:
    """Pull every graded nfl_game_result that has both QB ids + a
    non-null home_win. Paginate — table is ~1000 rows today but will
    grow with each week."""
    out: list[dict] = []
    offset = 0
    page = 1000
    while True:
        r = requests.get(
            f'{SB}/rest/v1/nfl_game_results',
            headers={**H_READ, 'Range-Unit': 'items',
                     'Range': f'{offset}-{offset + page - 1}'},
            params={
                'home_qb_id': 'not.is.null',
                'away_qb_id': 'not.is.null',
                'home_win':   'not.is.null',
                'select': 'game_date,home_qb_id,home_qb_name,'
                          'away_qb_id,away_qb_name,home_win',
                'order': 'game_date.desc',
            },
            timeout=30,
        )
        if r.status_code not in (200, 206):
            print(f'  ⚠ results fetch {r.status_code}: {r.text[:200]}')
            break
        rows = r.json() or []
        if not rows: break
        out.extend(rows)
        if len(rows) < page: break
        offset += page
    return out


def compute_splits(results: list[dict], qb_filter: str | None = None) -> list[dict]:
    """Build per-QB career + last-10 home/road records.

    Each result row contributes ONE home-start (for home_qb) and ONE
    road-start (for away_qb). The QB's split record aggregates across
    all rows they appear in as either side.

    Recent N = the last N chronological starts on that side (home
    or road independently). Dates are already sorted desc from the
    fetch, so we consume in order and count while under the cap.
    """
    home: dict[str, dict] = {}   # qb_id → aggregate
    road: dict[str, dict] = {}
    # For the recent-N slicing we need per-QB per-side chronological
    # lists. Results already come sorted date-desc, so appending in
    # order gives us "most-recent first" per (qb, side).
    home_recent: dict[str, list[int]] = defaultdict(list)
    road_recent: dict[str, list[int]] = defaultdict(list)

    for r in results:
        hqb, hname = r.get('home_qb_id'), r.get('home_qb_name')
        aqb, aname = r.get('away_qb_id'), r.get('away_qb_name')
        won = bool(r.get('home_win'))
        # Home QB start
        if hqb:
            rec = home.setdefault(hqb, {'qb_name': hname, 'starts': 0, 'wins': 0})
            rec['starts'] += 1
            if won: rec['wins'] += 1
            if hname and not rec.get('qb_name'):
                rec['qb_name'] = hname
            home_recent[hqb].append(1 if won else 0)
        # Away QB start (won = NOT home_win)
        if aqb:
            rec = road.setdefault(aqb, {'qb_name': aname, 'starts': 0, 'wins': 0})
            rec['starts'] += 1
            if not won: rec['wins'] += 1
            if aname and not rec.get('qb_name'):
                rec['qb_name'] = aname
            road_recent[aqb].append(1 if not won else 0)

    # Union of QBs seen on either side
    all_qbs = set(home.keys()) | set(road.keys())

    out: list[dict] = []
    for qb in all_qbs:
        h = home.get(qb, {'qb_name': None, 'starts': 0, 'wins': 0})
        r = road.get(qb, {'qb_name': None, 'starts': 0, 'wins': 0})
        name = h.get('qb_name') or r.get('qb_name') or qb
        if qb_filter and qb_filter.lower() not in name.lower():
            continue

        # Recent-N (dates were desc, so first N entries are most recent)
        h_recent = home_recent.get(qb, [])[:RECENT_N]
        r_recent = road_recent.get(qb, [])[:RECENT_N]

        def _pct(w: int, n: int):
            return round(w / n, 3) if n > 0 else None

        h_pct = _pct(h['wins'], h['starts'])
        r_pct = _pct(r['wins'], r['starts'])
        rh_pct = _pct(sum(h_recent), len(h_recent))
        rr_pct = _pct(sum(r_recent), len(r_recent))

        def _delta_pp(a, b):
            if a is None or b is None: return None
            return round((a - b) * 100.0, 2)

        out.append({
            'qb_id':                qb,
            'qb_name':              name,
            'home_starts':          h['starts'],
            'home_wins':            h['wins'],
            'road_starts':          r['starts'],
            'road_wins':            r['wins'],
            'home_win_pct':         h_pct,
            'road_win_pct':         r_pct,
            'career_h_r_delta_pp':  _delta_pp(h_pct, r_pct),
            'recent_home_starts':   len(h_recent),
            'recent_home_wins':     sum(h_recent),
            'recent_road_starts':   len(r_recent),
            'recent_road_wins':     sum(r_recent),
            'recent_home_win_pct':  rh_pct,
            'recent_road_win_pct':  rr_pct,
            'recent_h_r_delta_pp':  _delta_pp(rh_pct, rr_pct),
            'computed_at':          datetime.now(timezone.utc).isoformat(),
            'source_row_count':     h['starts'] + r['starts'],
        })
    return out


def upsert_splits(rows: list[dict], dry_run: bool = False) -> int:
    if not rows: return 0
    if dry_run:
        # Print a sample of high-delta cases so we can eyeball the signal
        interesting = sorted(
            [r for r in rows if r['home_starts'] >= 10 and r['road_starts'] >= 10],
            key=lambda x: -(abs(x['career_h_r_delta_pp'] or 0)),
        )[:15]
        print(f'\n  [DRY] would upsert {len(rows)} QBs')
        print(f'  Top 15 career home-vs-road deltas (min 10 starts each side):')
        print(f'  {"QB":<22}  {"home":>6}  {"road":>6}  {"delta_pp":>10}')
        for r in interesting:
            print(f'  {r["qb_name"][:22]:<22}  '
                  f'{(str(r["home_wins"]) + "-" + str(r["home_starts"] - r["home_wins"])):>6}  '
                  f'{(str(r["road_wins"]) + "-" + str(r["road_starts"] - r["road_wins"])):>6}  '
                  f'{r["career_h_r_delta_pp"]:>+10.2f}')
        return len(rows)

    # Chunked upsert
    ok = 0
    CHUNK = 100
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        r = requests.post(
            f'{SB}/rest/v1/nfl_qb_home_away_splits?on_conflict=qb_id',
            headers=H_WRITE,
            json=chunk,
            timeout=30,
        )
        if r.status_code in (200, 201, 204):
            ok += len(chunk)
        else:
            print(f'  ⚠ chunk {i}: {r.status_code} {r.text[:200]}')
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--qb', help='Filter to QBs whose name contains this string')
    args = ap.parse_args()

    print(f'=== compute_nfl_qb_home_away_splits · {datetime.now().isoformat()[:19]} ===')
    results = fetch_results()
    print(f'  fetched {len(results)} graded nfl_game_results rows')
    if not results:
        print('  no rows to compute — abort')
        return

    splits = compute_splits(results, qb_filter=args.qb)
    print(f'  computed splits for {len(splits)} QBs')

    written = upsert_splits(splits, dry_run=args.dry_run)
    prefix = '[DRY] ' if args.dry_run else '✓ '
    print(f'\n{prefix}wrote {written} rows to nfl_qb_home_away_splits')


if __name__ == '__main__':
    main()
