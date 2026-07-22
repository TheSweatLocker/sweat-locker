"""Consensus-bucket calibration audit.

For every graded game with external picks, reconstructs:
  - the aggregate consensus % on each surface
  - which side had consensus
  - whether our model AGREED or CONTRA'd that consensus
  - the actual result (did consensus win?)

Then buckets by (sport, surface, pct_band, model_alignment) and writes
30d / 60d / lifetime W/L hit rates to consensus_bucket_calibration.

This SUBSTANTIATES the consensus_fade detector — without this the
detector was firing on a hypothesis (7/21 aggregate 53% n=13). Now it
can look up the specific bucket a live game falls into and only fire
FADE when the bucket historically hits <48% at n>=20.

Confidence tiers:
  high   — n >= 50 AND stable
  medium — n >= 20
  low    — n <  20 (monitoring, not actionable)

USAGE:
    python audit_consensus_bucket_calibration.py               # all sports
    python audit_consensus_bucket_calibration.py --sport MLB
    python audit_consensus_bucket_calibration.py --dry-run
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
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

WINDOWS = [('30d', 30), ('60d', 60), ('lifetime', None)]

SPORT_CONTEXT_TABLE = {
    'MLB': 'mlb_game_context',
    'NFL': 'nfl_game_context',
    'NCAAB': 'ncaab_game_context',
}

# Only bucket at consensus >= 75% (below that, aggregate hit rate is
# meaningless — mixed markets don't have "consensus")
PCT_BANDS = [
    ('75-84',  0.75, 0.849),
    ('85-94',  0.85, 0.949),
    ('95-100', 0.95, 1.001),
]


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _pct_band(pct: float) -> Optional[str]:
    for label, lo, hi in PCT_BANDS:
        if lo <= pct <= hi:
            return label
    return None


def fetch_picks(sport: str, since_days: Optional[int]) -> list:
    filt = '&result=in.(W,L)'  # only graded picks (exclude push/pending)
    filt += f'&sport=eq.{sport}'
    if since_days:
        cutoff = (_et_now().date() - timedelta(days=since_days)).isoformat()
        filt += f'&game_date=gte.{cutoff}'
    rows = []
    off = 0
    while True:
        r = requests.get(
            f'{SB}/rest/v1/external_picks?'
            f'select=game_id,sport,surface,pick_side,result'
            f'{filt}&order=game_date.desc&limit=1000&offset={off}',
            headers=H_READ, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        off += 1000
    return rows


def fetch_model_directions(sport: str, game_ids: list) -> dict:
    """Return {game_id: {'ml_side': 'HOME'|'AWAY', 'total_side': 'OVER'|'UNDER'}}
    based on our composite projections."""
    tbl = SPORT_CONTEXT_TABLE.get(sport)
    if not tbl or not game_ids:
        return {}
    out = {}
    for i in range(0, len(game_ids), 100):
        chunk = game_ids[i:i + 100]
        ids_str = ','.join(f'"{g}"' for g in chunk)
        r = requests.get(
            f'{SB}/rest/v1/{tbl}?game_id=in.({ids_str})'
            f'&select=game_id,projected_spread,projected_total,close_total',
            headers=H_READ, timeout=15,
        )
        if r.status_code != 200:
            continue
        for row in r.json():
            ps = row.get('projected_spread')
            pt = row.get('projected_total')
            ct = row.get('close_total')
            ml_side = None
            total_side = None
            if ps is not None:
                try: ml_side = 'HOME' if float(ps) > 0 else 'AWAY' if float(ps) < 0 else None
                except: pass
            if pt is not None and ct is not None:
                try: total_side = 'OVER' if float(pt) > float(ct) else 'UNDER' if float(pt) < float(ct) else None
                except: pass
            out[row['game_id']] = {'ml_side': ml_side, 'total_side': total_side}
    return out


def compute_buckets(picks: list, model_dir: dict) -> list:
    """Group into buckets and compute hit rates."""
    # Per (game_id, surface): list of picks with W/L
    by_game_surf = defaultdict(list)
    for p in picks:
        by_game_surf[(p['game_id'], p['sport'], p['surface'])].append(p)

    # For each game+surface, compute consensus side + pct + model_alignment
    # + whether consensus WON (all picks on that side share the same W/L)
    buckets = defaultdict(lambda: {'W': 0, 'L': 0})
    for (gid, sport, surface), pl in by_game_surf.items():
        if surface not in ('ml', 'spread', 'total'):
            continue
        by_side = defaultdict(list)
        for p in pl:
            side = (p.get('pick_side') or '').upper()
            if side: by_side[side].append(p)
        total = sum(len(v) for v in by_side.values())
        if total < 3:  # need at least 3 books for meaningful consensus
            continue
        dominant_side, side_picks = max(by_side.items(), key=lambda kv: len(kv[1]))
        pct = len(side_picks) / total
        band = _pct_band(pct)
        if band is None:  # below 75% not a consensus bucket
            continue
        # Model alignment
        md = model_dir.get(gid, {})
        if surface == 'total':
            our_side = md.get('total_side')
        else:
            our_side = md.get('ml_side')
        if our_side is None:
            alignment = 'unknown'
        elif our_side == dominant_side:
            alignment = 'aligned'
        else:
            alignment = 'contra'

        # Did the consensus win? All picks on dominant_side share the game
        # outcome by definition (same side of same game). Take the first.
        outcome = side_picks[0].get('result')
        if outcome not in ('W', 'L'):
            continue
        key = (sport, surface, band, alignment)
        buckets[key][outcome] += 1

    # Convert to records
    today = _et_now().date().isoformat()
    records = []
    for (sport, surface, band, align), wl in buckets.items():
        n = wl['W'] + wl['L']
        pct = round(100 * wl['W'] / n, 2) if n else None
        if n >= 50: conf = 'high'
        elif n >= 20: conf = 'medium'
        else: conf = 'low'
        records.append({
            'sport': sport, 'surface': surface, 'pct_band': band,
            'model_alignment': align,
            'wins': wl['W'], 'losses': wl['L'], 'pushes': 0,
            'hit_pct': pct, 'sample_n': n, 'confidence': conf,
            'computed_date': today,
        })
    return records


def upsert(records: list, window_label: str, dry_run: bool = False) -> int:
    if not records: return 0
    for r in records:
        r['window_label'] = window_label
    if dry_run:
        for r in sorted(records, key=lambda x: (x['sport'], x['surface'],
                                                x['pct_band'], x['model_alignment'])):
            marker = ''
            if r['hit_pct'] is not None:
                if r['hit_pct'] < 48 and r['sample_n'] >= 20:
                    marker = ' 🚨 FADE-worthy'
                elif r['hit_pct'] >= 55 and r['sample_n'] >= 20:
                    marker = ' ⭐ signal (don\'t fade)'
            print(f"  [DRY] {r['sport']:5} {r['surface']:6} {r['pct_band']:8} "
                  f"{r['model_alignment']:8} {window_label:8}  "
                  f"{r['wins']}-{r['losses']} = {r['hit_pct'] if r['hit_pct'] else '-'}% "
                  f"(n={r['sample_n']}, {r['confidence']}){marker}")
        return len(records)
    r = requests.post(
        f'{SB}/rest/v1/consensus_bucket_calibration'
        f'?on_conflict=sport,surface,pct_band,model_alignment,window_label,computed_date',
        headers=H_WRITE, json=records, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(records)


def run(sport: Optional[str] = None, dry_run: bool = False) -> None:
    print(f'=== consensus bucket calibration audit ===')
    sports = [sport] if sport else ['MLB', 'NFL', 'NCAAB']
    total = 0
    for s in sports:
        for window_label, days in WINDOWS:
            picks = fetch_picks(s, days)
            if not picks:
                continue
            game_ids = list({p['game_id'] for p in picks})
            model_dir = fetch_model_directions(s, game_ids)
            records = compute_buckets(picks, model_dir)
            print(f'\n  {s} · {window_label}: {len(picks)} graded picks '
                  f'-> {len(records)} buckets')
            written = upsert(records, window_label, dry_run=dry_run)
            total += written
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}Total rows written: {total}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None,
                    choices=[None, 'MLB', 'NFL', 'NCAAB'])
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(sport=args.sport, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
