"""Line snapshot writer (2026-08-10).

Reads the current oddscrowd_snapshot values from mlb_game_context and
appends a time-series row to line_snapshot for each active game/market.

Runs AFTER any oddscrowd (or other line-source) puller updates
mlb_game_context. Each run creates one new snapshot per (game_id,
market, source, snapshot_ts). Over time this builds a full trajectory
of sharp/public/line-movement per game.

Enables downstream analytics:
  * Reverse-line-move detection — compare consecutive snapshots
  * Sharp-entry timing — when did sharp $ show up?
  * Public-drift patterns — how does retail sentiment shift near tip-off?

Sport-universal: takes --sport arg (defaults MLB). Extends to NFL/NCAAF
once their game_context tables have oddscrowd_snapshot columns.

## Idempotency

Unique constraint on (sport, game_id, market, snapshot_ts, source)
so re-running the same second is a no-op. But we truncate snapshot_ts
to the minute — two runs within the same minute would collide;
easy to reason about + saves storage.

CLI:
    python write_line_snapshot.py [--sport MLB] [--dry-run]
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import datetime, timedelta, timezone

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

CTX_TABLE = {
    'MLB':   'mlb_game_context',
    # 2026-08-22: NFL + NCAAF added once compute_align_status_{nfl,ncaaf}.py
    # was wired into their pipelines. Both ctx tables already carry the
    # oddscrowd_snapshot column (schema shipped earlier); pipeline just
    # wasn't populating it because compute_align_status was orphaned.
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
}


def _parse_snap(s):
    if not s: return {}
    if isinstance(s, str):
        try: return json.loads(s)
        except: return {}
    return s


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def run(sport: str = 'MLB', game_date: str | None = None,
        dry_run: bool = False) -> int:
    ctx_table = CTX_TABLE.get(sport)
    if not ctx_table:
        print(f'  [{sport}] no ctx table registered — skip'); return 0
    gd = game_date or _et_today()
    print(f'=== write_line_snapshot · {sport} · {gd} ===')

    r = requests.get(f'{SB}/rest/v1/{ctx_table}', headers=H_READ,
        params={'game_date': f'eq.{gd}',
                'select': 'game_id,close_spread,close_total,home_ml_close,'
                          'away_ml_close,oddscrowd_snapshot'},
        timeout=15).json()
    if not isinstance(r, list):
        print(f'  fetch failed: {r}'); return 0

    # Truncate snapshot_ts to minute — dedupe collisions on same-minute reruns
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    now_iso = now.isoformat()

    rows_to_write = []
    for ctx in r:
        gid = ctx.get('game_id')
        if not gid: continue
        snap = _parse_snap(ctx.get('oddscrowd_snapshot'))
        if not snap: continue
        for market in ('ml', 'spread', 'rl', 'total'):
            seg = snap.get(market) or snap.get('runline' if market == 'rl' else market)
            if not seg or not isinstance(seg, dict): continue
            pick = (seg.get('pick') or '').upper()
            if not pick: continue
            # Line value for spread/total
            line = None
            if market == 'total': line = ctx.get('close_total')
            elif market in ('spread', 'rl'): line = ctx.get('close_spread')
            # Odds
            odds = None
            if market == 'ml':
                odds = ctx.get('home_ml_close') if pick == 'HOME' else ctx.get('away_ml_close')
                # American to decimal
                if odds is not None:
                    try:
                        o = int(odds)
                        odds = round(1 + (100 / -o), 3) if o < 0 else round(1 + (o / 100), 3)
                    except Exception:
                        odds = None
            rows_to_write.append({
                'sport': sport, 'game_id': gid,
                'snapshot_ts': now_iso, 'market': market,
                'pick_side': pick,
                'money_pct': seg.get('money'),
                'bets_pct': seg.get('bets'),
                'divergence': seg.get('div'),
                'line': line,
                'odds_pick': odds,
                'source': 'oddscrowd',
                'raw': seg,
            })

    if not rows_to_write:
        print('  no snapshot data to write'); return 0

    if dry_run:
        print(f'  [DRY] would write {len(rows_to_write)} rows (sample):')
        for row in rows_to_write[:3]:
            print(f'    {row["game_id"][:8]} {row["market"]:6} pick={row["pick_side"]} '
                  f'money={row["money_pct"]}%')
        return len(rows_to_write)

    written = 0
    # Batch upsert
    for i in range(0, len(rows_to_write), 100):
        chunk = rows_to_write[i:i+100]
        rr = requests.post(
            f'{SB}/rest/v1/line_snapshot?on_conflict=sport,game_id,market,snapshot_ts,source',
            headers=H_WRITE, json=chunk, timeout=20)
        if rr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  chunk {i}: {rr.status_code} {rr.text[:200]}')
    print(f'  wrote {written} line_snapshot rows @ {now_iso}')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB')
    p.add_argument('--date')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(sport=args.sport, game_date=args.date, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
