"""Rule-fire stats computer (2026-08-14).

Session A part 2. Walks jerry_reads + prop_jerry_reads for the last
{window_days}, extracts every "[Auto-<rule>...]" tag from short_read /
audit_notes, cross-references with the underlying prop outcome to
compute per-rule hit rate.

The output feeds the alert engine: any rule at n>=20 with hit rate < 45%
gets a warn/critical alert. This is how we catch the NEXT FORCE_FADE_TRAP
before it silently loses money for months.

Universal architecture: iterates SPORT_CONFIG from compute_hit_rate_dashboard,
so every sport with a jerry synth or repair pipeline is covered.

RULE_CLASS mapping: parsed from the tag prefix. Standard classes:
    Auto-refit-override      → refit_override
    Auto-trend-repair        → pipeline_repair
    Auto-sim-repair          → pipeline_repair
    Auto-flipped             → pipeline_repair
    Auto-prop-discipline     → pipeline_discipline
    Auto-fade-discipline     → pipeline_discipline
    Auto-refit-override:BAND → refit_calibration
    (unknown)                → 'other'

Idempotent — snapshot_date + unique key means re-runs update.

CLI:
    python compute_rule_fire_stats.py [--date YYYY-MM-DD]
                                       [--sport MLB|ALL] [--windows 7,30,90]
                                       [--dry-run]
"""
from __future__ import annotations
import argparse, os, re, sys
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict
from typing import Iterable, Optional

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

DEFAULT_WINDOWS = [7, 30, 90]
SPORTS_WITH_JERRY = ['MLB', 'NFL', 'NCAAF', 'NCAAB', 'NBA', 'NHL', 'UFC']

RULE_CLASS_MAP = {
    'Auto-refit-override':    'refit_override',
    'Auto-trend-repair':      'pipeline_repair',
    'Auto-sim-repair':        'pipeline_repair',
    'Auto-flipped':           'pipeline_repair',
    'Auto-prop-discipline':   'pipeline_discipline',
    'Auto-fade-discipline':   'pipeline_discipline',
    'Auto-layer-d':           'pipeline_repair',
    'Auto-stat-verify':       'pipeline_repair',
}

# Matches "[Auto-<class>] <YYYY-MM-DD> <ACTION_NAME>:" or
#         "[Auto-<class>] <YYYY-MM-DD> <ACTION_NAME>" (unclosed).
# Captures class and action name for granular tracking.
TAG_PATTERN = re.compile(
    r'\[(Auto-[a-z\-]+)\s+\d{4}-\d{2}-\d{2}\s+([A-Z_]+)',
    re.IGNORECASE
)


def _et_today() -> date:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def _paginate(url: str, params: dict, page: int = 1000) -> list:
    out = []; offset = 0
    while offset < 8000:
        p = {**params, 'limit': str(page), 'offset': str(offset)}
        r = requests.get(url, headers=H_READ, params=p, timeout=30)
        if r.status_code != 200: return out
        rows = r.json()
        if not isinstance(rows, list) or not rows: break
        out.extend(rows)
        if len(rows) < page: break
        offset += page
    return out


def extract_rule_fires(text: str) -> list:
    """Extract every '[Auto-<class> YYYY-MM-DD ACTION_NAME' from a text blob.
    Returns list of (class, action) tuples (may be empty)."""
    if not text: return []
    return TAG_PATTERN.findall(text)


def rule_class_of(auto_prefix: str) -> str:
    return RULE_CLASS_MAP.get(auto_prefix, 'other')


def scan_surface(sport: str, table: str, date_col: str, sport_filter: Optional[str],
                 window_start: date, window_end: date) -> list:
    """Pull rows in window with their audit_notes + short_read + result."""
    params = {
        'select': f'id,{date_col},call_verdict,result,short_read,audit_notes',
        'and': f'({date_col}.gte.{window_start.isoformat()},'
               f'{date_col}.lte.{window_end.isoformat()})',
        'or': '(audit_notes.ilike.*Auto-*,short_read.ilike.*Auto-*)',
    }
    if sport_filter: params['sport'] = sport_filter
    return _paginate(f'{SB}/rest/v1/{table}', params)


def aggregate_rule_fires(sport: str, window_start: date, window_end: date) -> dict:
    """Return {(rule_class, action_name): [wins, losses, fires]} for sport in window."""
    stats = defaultdict(lambda: [0, 0, 0])  # [wins, losses, fires]

    # Both jerry_reads (game) + prop_jerry_reads (prop) carry Auto- tags
    for table, date_col in (('jerry_reads', 'game_date'),
                             ('prop_jerry_reads', 'game_date')):
        sport_filter = f'eq.{sport}'
        rows = scan_surface(sport, table, date_col, sport_filter,
                             window_start, window_end)
        for row in rows:
            merged_text = ((row.get('audit_notes') or '') + '\n' +
                           (row.get('short_read') or ''))
            fires = extract_rule_fires(merged_text)
            if not fires: continue
            # Dedup within a single row — one row can carry multiple rules
            unique_fires = set(fires)
            result = (row.get('result') or '').lower()
            for cls, action in unique_fires:
                key = (rule_class_of(cls), action.upper())
                stats[key][2] += 1  # fires
                if result == 'win':  stats[key][0] += 1
                elif result == 'loss': stats[key][1] += 1
    return stats


def upsert_rule_stats(snapshot_date: date, sport: str, window_days: int,
                      stats: dict, dry_run: bool = False) -> int:
    written = 0
    for (rule_class, action), (w, l, fires) in stats.items():
        sample = w + l
        hit_rate = round(100 * w / sample, 2) if sample > 0 else None
        payload = {
            'snapshot_date': snapshot_date.isoformat(),
            'sport': sport,
            'rule_name': action,
            'rule_class': rule_class,
            'window_days': window_days,
            'fires': fires,
            'wins_when_fired': w,
            'losses_when_fired': l,
            'hit_rate': hit_rate,
            'sample_n': sample,
            'computed_at': datetime.now(timezone.utc).isoformat(),
        }
        if dry_run:
            hr_str = f'{hit_rate}%' if hit_rate is not None else '(no grades)'
            print(f'  [DRY] {sport:6} {rule_class:22} {action:35} w{window_days:2d}: '
                  f'{fires} fires · {w}-{l} {hr_str}')
            written += 1
            continue
        conflict = 'snapshot_date,sport,rule_name,window_days'
        pr = requests.post(f'{SB}/rest/v1/rule_fire_stats?on_conflict={conflict}',
            headers=H_WRITE, json=payload, timeout=15)
        if pr.status_code in (200, 201, 204): written += 1
        else: print(f'  ✗ {sport} {action}: {pr.status_code} {pr.text[:150]}')
    return written


def run(snapshot_date: date, sports: Iterable[str], windows: list,
        dry_run: bool = False) -> int:
    print(f'=== rule_fire_stats · date={snapshot_date} · sports={list(sports)} · '
          f'windows={windows} ===')
    total = 0
    for sport in sports:
        for w_days in windows:
            w_start = snapshot_date - timedelta(days=w_days)
            w_end = snapshot_date - timedelta(days=1)
            stats = aggregate_rule_fires(sport, w_start, w_end)
            n = upsert_rule_stats(snapshot_date, sport, w_days, stats, dry_run=dry_run)
            total += n
    print(f'\n{"[DRY] would write" if dry_run else "wrote"} {total} rule-fire rows')
    return total


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='snapshot date; defaults today ET')
    p.add_argument('--sport', default='ALL')
    p.add_argument('--windows', default='7,30,90')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    sd = date.fromisoformat(args.date) if args.date else _et_today()
    sports = SPORTS_WITH_JERRY if args.sport == 'ALL' else [args.sport]
    windows = [int(x) for x in args.windows.split(',')]
    run(sd, sports, windows, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
