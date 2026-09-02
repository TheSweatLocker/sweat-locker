"""Signal pattern registry — nightly recompute.

Reads line_movement_flags × {sport}_game_results, grades each flag's
implied side against outcome, computes rolling hit rate per
(sport, classification, market). Upserts to signal_pattern_registry.

Powers The Split's "does this pattern hit?" inline chip context.

USAGE:
    python compute_signal_patterns.py                 # all sports
    python compute_signal_patterns.py --sport MLB
    python compute_signal_patterns.py --dry-run
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import date, timedelta, datetime, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


LOOKBACK_DAYS = 30

SPORT_RESULTS_TABLE = {
    'MLB':   'mlb_game_results',
    'NFL':   'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'NBA':   'nba_game_results',
    'NCAAB': 'ncaab_game_results',
    'NHL':   'nhl_game_results',
}

# Classifications we track (from line_movement_flags.classification).
# PATTERN_ONLY + NEUTRAL excluded — they lack sharp/public directional info.
TRACKED_CLASSIFICATIONS = {
    'SHARP_MOVE_TRIPLE_CONFIRMED': ('Sharp Triple', 'All 3 sources show sharp money on this side + line moved to confirm'),
    'SHARP_MOVE_CONFIRMED':        ('Sharp Confirmed', '2 sources show sharp money on this side + line moved to confirm'),
    'SHARP_MOVE_LEAN':             ('Sharp Lean', '1 source shows sharp money on this side (unconfirmed, single-source)'),
    'CONSENSUS_TRIPLE_CONFIRMED':  ('Consensus Triple', 'All 3 sources agree on directional split + line moved with it'),
    'CONSENSUS_CONFIRMED':         ('Consensus', '2 sources agree on directional split + line moved with it'),
    'CONSENSUS_LEAN':              ('Consensus Lean', '1 source shows directional split (unconfirmed)'),
    'PUBLIC_MOVE_CONFIRMED':       ('Public Move', 'Line moved WITH public money — historically underperforms (fade candidate)'),
    'PUBLIC_MOVE_LEAN':            ('Public Lean', '1 source shows public-heavy on side line moved to'),
}

# Markets we track separately. 'ALL' also computed as cross-market roll.
TRACKED_MARKETS = ['ml', 'rl', 'total']


def _et_today() -> date:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def _fetch_paged(url: str, chunk: int = 1000) -> list:
    out = []
    off = 0
    while True:
        r = requests.get(f'{url}&limit={chunk}&offset={off}', headers=H_READ, timeout=30)
        if r.status_code != 200:
            break
        b = r.json() if isinstance(r.json(), list) else []
        if not b:
            break
        out.extend(b)
        if len(b) < chunk:
            break
        off += chunk
    return out


def fetch_flags(sport: str, days: int) -> list:
    cutoff = (_et_today() - timedelta(days=days)).isoformat()
    tracked_list = ','.join(sorted(TRACKED_CLASSIFICATIONS.keys()))
    url = (f'{SB}/rest/v1/line_movement_flags?'
           f'select=game_id,market,side,classification,first_seen_at'
           f'&sport=eq.{sport}'
           f'&classification=in.({tracked_list})'
           f'&first_seen_at=gte.{cutoff}T00:00:00Z')
    return _fetch_paged(url)


def fetch_results(sport: str, game_ids: list) -> dict:
    """Return {game_id: result_row} for the games we need."""
    tbl = SPORT_RESULTS_TABLE.get(sport)
    if not tbl or not game_ids:
        return {}
    out = {}
    # Chunk in batches of 200 for PostgREST IN clause safety
    ids = list({str(g) for g in game_ids if g})
    for i in range(0, len(ids), 200):
        batch = ids[i:i+200]
        r = requests.get(
            f'{SB}/rest/v1/{tbl}?'
            f'select=game_id,home_win,spread_result,total_result'
            f'&game_id=in.({",".join(batch)})',
            headers=H_READ, timeout=30,
        )
        if r.status_code == 200:
            for row in r.json():
                out[str(row.get('game_id'))] = row
    return out


def grade_flag(flag: dict, result: dict) -> Optional[str]:
    """Return 'W' | 'L' | 'P' | None (ungradeable) for whether the
    flagged side won its market.

    Grading follows canonical fields:
      market='ml':    home_win bool → HOME=W if hw else L; AWAY inverse
      market='rl':    spread_result 'home_covered'|'away_covered'|'push'
      market='total': total_result 'over'|'under'|'push' (case-insensitive)

    For PUBLIC_MOVE_CONFIRMED (fade signal), we return the outcome of
    BACKING the flag side (raw). App/compute layer can invert if it
    wants fade-graded values — this stays raw for consistency.
    """
    market = str(flag.get('market') or '').lower()
    side = str(flag.get('side') or '').upper()
    if market == 'ml':
        hw = result.get('home_win')
        if hw is None: return None
        if side == 'HOME': return 'W' if hw else 'L'
        if side == 'AWAY': return 'L' if hw else 'W'
        return None
    if market in ('rl', 'spread', 'puckline', 'runline'):
        sr = str(result.get('spread_result') or '').lower()
        if sr == 'push': return 'P'
        if sr == 'home_covered': return 'W' if side == 'HOME' else 'L'
        if sr == 'away_covered': return 'W' if side == 'AWAY' else 'L'
        return None
    if market == 'total':
        tr = str(result.get('total_result') or '').lower()
        if tr == 'push': return 'P'
        if tr == 'over':  return 'W' if side == 'OVER' else 'L'
        if tr == 'under': return 'W' if side == 'UNDER' else 'L'
        return None
    return None


def compute_sport(sport: str, days: int = LOOKBACK_DAYS) -> list:
    """Return list of upsert rows for sport_pattern_registry."""
    flags = fetch_flags(sport, days)
    if not flags:
        return []

    # Fetch results for all unique game_ids in one shot
    gids = list({f.get('game_id') for f in flags if f.get('game_id')})
    results = fetch_results(sport, gids)

    # Bucket flags by (classification, market)
    buckets = defaultdict(lambda: {'W': 0, 'L': 0, 'P': 0})
    for f in flags:
        cls = f.get('classification')
        mkt = str(f.get('market') or '').lower()
        if cls not in TRACKED_CLASSIFICATIONS:
            continue
        result_row = results.get(str(f.get('game_id')))
        if not result_row:
            continue
        verdict = grade_flag(f, result_row)
        if verdict is None:
            continue
        # Per-market bucket
        if mkt in TRACKED_MARKETS:
            buckets[(cls, mkt)][verdict] += 1
        # Cross-market ALL bucket
        buckets[(cls, 'ALL')][verdict] += 1

    now_iso = datetime.now(timezone.utc).isoformat()
    rows = []
    for (cls, mkt), b in buckets.items():
        w, l, p = b['W'], b['L'], b['P']
        n = w + l
        if n + p == 0:
            continue
        label, desc = TRACKED_CLASSIFICATIONS[cls]
        hit_pct = round(100.0 * w / n, 2) if n > 0 else None
        rows.append({
            'sport': sport,
            'pattern_key': cls,
            'market': mkt,
            'pattern_label': f'{label}' if mkt == 'ALL' else f'{label} · {mkt.upper()}',
            'description': desc,
            'lookback_days': days,
            'n_wins': w, 'n_losses': l, 'n_pushes': p, 'n_total': n + p,
            'hit_pct': hit_pct,
            'last_computed_at': now_iso,
        })
    return rows


def upsert(rows: list, dry_run: bool = False) -> int:
    if not rows:
        return 0
    if dry_run:
        for r in rows:
            print(f"  [DRY] {r['sport']:5} {r['pattern_key']:32} {r['market']:5} "
                  f"{r['n_wins']:>3}-{r['n_losses']:>3}-{r['n_pushes']:>2} "
                  f"= {r['hit_pct']}% (n={r['n_total']})")
        return len(rows)
    r = requests.post(
        f'{SB}/rest/v1/signal_pattern_registry?on_conflict=sport,pattern_key,market',
        headers=H_WRITE, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(rows)


def run(sport_filter: Optional[str] = None, dry_run: bool = False):
    print('=== signal pattern registry recompute ===')
    sports = [sport_filter] if sport_filter else list(SPORT_RESULTS_TABLE.keys())
    total = 0
    for sport in sports:
        print(f'\n  {sport}: fetching flags...')
        rows = compute_sport(sport, LOOKBACK_DAYS)
        print(f'    → {len(rows)} pattern rows')
        n = upsert(rows, dry_run)
        total += n
    verb = '[DRY]' if dry_run else 'wrote'
    print(f'\n  {verb} {total} rows total')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=list(SPORT_RESULTS_TABLE.keys()),
                    help='Restrict to a single sport (default: all)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(sport_filter=args.sport, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
