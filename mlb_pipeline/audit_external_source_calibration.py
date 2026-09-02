"""External-source rolling calibration audit.

Runs nightly after resolve_externals.py. Reads graded external_picks
and writes per (source, sport, surface, window_label) hit rates to
external_source_calibration. Also breaks out by fade_flag tag so we
can audit whether our BOOST/FADE labels actually predict outcomes.

Feeds:
  - Dynamic fade_flag refresh (next-day pull uses updated tag)
  - Consensus_fade_alert detector (needs current per-source rates)
  - App-side per-source track record display (Tier 2 UX)

Windows: 7d (recent form), 30d (calibration standard), lifetime
(baseline reference).

USAGE:
    python audit_external_source_calibration.py                # all sports, all windows
    python audit_external_source_calibration.py --sport MLB
    python audit_external_source_calibration.py --dry-run
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date
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


WINDOWS = [
    ('7d', 7),
    ('30d', 30),
    ('lifetime', None),
]


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def fetch_graded(sport: Optional[str], since_days: Optional[int]) -> list:
    filt = '&result=in.(W,L,P)'
    if sport:
        filt += f'&sport=eq.{sport}'
    if since_days:
        cutoff = (_et_now().date() - timedelta(days=since_days)).isoformat()
        filt += f'&game_date=gte.{cutoff}'

    rows = []
    off = 0
    while True:
        r = requests.get(
            f'{SB}/rest/v1/external_picks?'
            f'select=source,sport,surface,result,fade_flag,game_date'
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


def compute_calibration(rows: list, window_label: str) -> list:
    """Aggregate rows into calibration records keyed by (source, sport, surface)."""
    buckets = defaultdict(lambda: {
        'wins': 0, 'losses': 0, 'pushes': 0,
        'boost_wins': 0, 'boost_losses': 0,
        'fade_wins': 0, 'fade_losses': 0,
        'trust_wins': 0, 'trust_losses': 0,
        'neutral_wins': 0, 'neutral_losses': 0,
    })
    for r in rows:
        key = (r['source'], r['sport'], r['surface'])
        b = buckets[key]
        res = r.get('result')
        flag = r.get('fade_flag') or 'neutral'
        if res == 'W':
            b['wins'] += 1
            b[f'{flag}_wins'] = b.get(f'{flag}_wins', 0) + 1
        elif res == 'L':
            b['losses'] += 1
            b[f'{flag}_losses'] = b.get(f'{flag}_losses', 0) + 1
        elif res == 'P':
            b['pushes'] += 1

    today = _et_now().date().isoformat()
    out = []
    for (src, sport, surface), b in buckets.items():
        n = b['wins'] + b['losses']
        if n == 0:
            continue
        hit_pct = round(100 * b['wins'] / n, 2)
        out.append({
            'source': src, 'sport': sport, 'surface': surface,
            'window_label': window_label,
            'wins': b['wins'], 'losses': b['losses'], 'pushes': b['pushes'],
            'hit_pct': hit_pct, 'sample_n': n,
            'boost_wins': b['boost_wins'], 'boost_losses': b['boost_losses'],
            'fade_wins': b['fade_wins'], 'fade_losses': b['fade_losses'],
            'trust_wins': b['trust_wins'], 'trust_losses': b['trust_losses'],
            'neutral_wins': b['neutral_wins'], 'neutral_losses': b['neutral_losses'],
            'computed_date': today,
        })
    return out


def upsert_calibration(records: list, dry_run: bool = False) -> int:
    if not records:
        return 0
    if dry_run:
        for r in records:
            tag_detail = []
            for tag in ('boost', 'fade', 'trust', 'neutral'):
                tn = r[f'{tag}_wins'] + r[f'{tag}_losses']
                if tn:
                    tp = round(100 * r[f'{tag}_wins'] / tn, 1)
                    tag_detail.append(f'{tag}={tp}%/{tn}')
            detail = '  ' + ' | '.join(tag_detail) if tag_detail else ''
            print(f"  [DRY] {r['sport']:5} {r['source']:14} {r['surface']:6} {r['window_label']:8} "
                  f"{r['wins']}-{r['losses']}-{r['pushes']} = {r['hit_pct']:5.1f}% (n={r['sample_n']:>3})"
                  f"{detail}")
        return len(records)
    r = requests.post(
        f'{SB}/rest/v1/external_source_calibration?'
        f'on_conflict=source,sport,surface,window_label,computed_date',
        headers=H_WRITE, json=records, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(records)


def run(sport: Optional[str] = None, dry_run: bool = False) -> None:
    print(f'=== external source calibration audit ===')
    total_written = 0
    for window_label, days in WINDOWS:
        rows = fetch_graded(sport, days)
        print(f'\n  window {window_label}: {len(rows)} graded picks')
        if not rows:
            continue
        records = compute_calibration(rows, window_label)
        print(f'  → {len(records)} (source × sport × surface) buckets')
        written = upsert_calibration(records, dry_run=dry_run)
        total_written += written

    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}Total rows written: {total_written}')


def main():
    ap = argparse.ArgumentParser()
    # 2026-09-02: expanded choices for cross-sport Split calibration.
    # Sport is passed through to fetch_graded as `sport=eq.{sport}` filter
    # on external_picks — any sport that has externals graded populates.
    ap.add_argument('--sport', default=None,
                    choices=[None, 'MLB', 'NFL', 'NCAAF', 'NBA', 'NCAAB', 'NHL', 'UFC'],
                    help='Restrict to a single sport (default: all)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(sport=args.sport, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
