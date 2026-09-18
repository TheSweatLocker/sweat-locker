"""Shadow-vs-live backtest harness for the NFL Phase 0+ rebuild.

Every candidate model / signal change writes to `primary_play_shadow_v2`
(games) or `playbook_snapshot_shadow_v2` (props). This script replays
those shadow picks against actual game results, rolls up a hit-rate
comparison vs the live tier that was actually presented, and writes
one row per (sport, market, variant, week, tier, side) to
`shadow_v2_backtest_results`.

Promotion gate (from Andy 9/17 directive):
    picks_count >= 50 AND (shadow_hit_rate - live_hit_rate) >= 0.03
    (≥3pp edge on ≥50 sample) sustained ≥4 weeks

Usage:
    # Grade this week's shadow picks vs live for a specific variant
    python shadow_v2_backtest.py --variant usage_v2 --sport NFL

    # Backfill against historical (weeks 1..N)
    python shadow_v2_backtest.py --variant usage_v2 --sport NFL \
        --start 2026-09-04 --end 2026-09-17

    # Report only (no DB write)
    python shadow_v2_backtest.py --variant usage_v2 --sport NFL --dry-run

Design:
- The harness knows nothing about specific variants — it just reads
  shadow blobs from the DB and compares. Adding a new variant means:
    1. Write a shadow-computing script that populates primary_play_
       shadow_v2 (or playbook_snapshot_shadow_v2) with the candidate
       logic, tagged with variant name in the JSONB.
    2. Run this harness against that variant.
- Shape expected in shadow blob:
    {
        "variant": "usage_v2",        # required — identifies the run
        "type": "ml",                  # or 'rl' / 'total' / 'prop'
        "side": "HOME",                # or team / OVER / UNDER
        "tier": "STRONG",
        "conviction": 63,
        "generated_at": "..."          # optional
    }
- Results grading uses same source of truth as live grading —
  nfl_game_results.actual_home_score / actual_away_score for sides;
  nfl_pipeline_props.result for props.

2026-09-17: Phase 0 scaffolding — enables shadow validation for
every subsequent phase before it goes live.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# Load .env from repo root if present.
_ENV_PATH = Path(__file__).parent / '.env'
if _ENV_PATH.exists():
    for line in _ENV_PATH.read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
if not SB or not KEY:
    print('SUPABASE_URL / SUPABASE_KEY not set — refusing to run.')
    sys.exit(1)

H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

CTX_TABLE = {
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
}
RESULT_TABLE = {
    'NFL':   'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
}
PROP_TABLE = {
    'NFL':   'nfl_pipeline_props',
    # NCAAF has no props per feedback_college_sports_no_props
}


# ─── Grading primitives ────────────────────────────────────────────

def _grade_side(pick: dict, result: dict) -> Optional[str]:
    """Grade a game-side pick against actual score. Returns 'W'/'L'/'P'/None."""
    home_score = result.get('actual_home_score')
    away_score = result.get('actual_away_score')
    if home_score is None or away_score is None:
        return None
    ptype = (pick.get('type') or '').lower()
    side = (pick.get('side') or '').upper()
    label = (pick.get('label') or '')

    if ptype == 'ml':
        # Winner check
        if home_score == away_score:
            return 'P'  # tie / push — very rare in NFL
        home_won = home_score > away_score
        if side == 'HOME':
            return 'W' if home_won else 'L'
        if side == 'AWAY':
            return 'L' if home_won else 'W'
        return None

    if ptype in ('rl', 'spread'):
        # label format: "TEAM ±X.X" — extract line
        try:
            line = float(pick.get('line') if pick.get('line') is not None
                         else label.split()[-1])
        except (ValueError, IndexError, TypeError):
            return None
        # For HOME side line = -X (fav) or +X (dog): home covers if
        # (home_score - away_score) + line > 0  (line is positive-when-home-dog)
        margin = home_score - away_score
        if side == 'HOME':
            adj = margin - abs(line) if line < 0 else margin + abs(line)
        elif side == 'AWAY':
            adj = -margin - abs(line) if line < 0 else -margin + abs(line)
        else:
            return None
        # Actually simpler convention: HOME line is what home covers to.
        # A fav with line -7 means home needs to WIN BY MORE THAN 7.
        # Recompute cleanly:
        if side == 'HOME':
            covered_by = (home_score - away_score) - (line if line > 0 else abs(line) * -1)
            # if home is favored, line is stored as negative? depends on schema
            # simpler: home covers when (home - away) > -line for line<0 (fav)
            # or (home - away) > -line for line>0 (dog) — same formula:
            covered_by = (home_score - away_score) + line
        else:  # AWAY
            covered_by = (away_score - home_score) - line
        if abs(covered_by) < 0.001:
            return 'P'
        return 'W' if covered_by > 0 else 'L'

    if ptype == 'total':
        try:
            line = float(pick.get('line') or label.split()[-1])
        except (ValueError, IndexError, TypeError):
            return None
        total = home_score + away_score
        if abs(total - line) < 0.001:
            return 'P'
        if side == 'OVER':
            return 'W' if total > line else 'L'
        if side == 'UNDER':
            return 'W' if total < line else 'L'
        return None

    return None


def _grade_prop(prop_row: dict) -> Optional[str]:
    """Prop grading reads the already-graded `result` column set by the
    prop resolver (resolve_nfl_props). Shadow prop grading uses the SAME
    actual outcome — the only thing that varies is which TIER we would
    have assigned."""
    res = (prop_row.get('result') or '').lower()
    if res in ('win', 'w', 'over_hit', 'under_hit'):
        return 'W'
    if res in ('loss', 'l', 'over_miss', 'under_miss'):
        return 'L'
    if res in ('push', 'p', 'void'):
        return 'P'
    return None


# ─── Backtest runners ──────────────────────────────────────────────

def run_side_backtest(sport: str, variant: str,
                      start: str, end: str, dry_run: bool = False) -> int:
    """Grade shadow_v2 vs live picks for sides (ML/RL/total). Writes one
    rollup row per (variant, tier, side) into shadow_v2_backtest_results.
    Returns number of games contributing to the backtest."""
    ctx_tbl = CTX_TABLE.get(sport)
    res_tbl = RESULT_TABLE.get(sport)
    if not ctx_tbl or not res_tbl:
        print(f'  {sport}: no context/result table registered — skip')
        return 0

    # Pull games with shadow set + result resolved
    url = (f'{SB}/rest/v1/{ctx_tbl}?game_date=gte.{start}&game_date=lte.{end}'
           f'&primary_play_shadow_v2=not.is.null'
           f'&select=game_id,game_date,away_team,home_team,primary_play,'
           f'primary_play_shadow_v2')
    r = requests.get(url, headers=H_READ, timeout=30).json()
    if not isinstance(r, list):
        print(f'  fetch ctx failed: {r}')
        return 0
    if not r:
        print(f'  {sport} {variant}: no games with shadow_v2 in window '
              f'{start}..{end}')
        return 0

    # Pull results for those games
    game_ids = ','.join(f'"{g["game_id"]}"' for g in r)
    rr = requests.get(
        f'{SB}/rest/v1/{res_tbl}?game_id=in.({game_ids})'
        f'&select=game_id,actual_home_score,actual_away_score',
        headers=H_READ, timeout=30,
    ).json()
    results_by_gid = {row['game_id']: row for row in rr if isinstance(row, dict)}

    print(f'  {sport} {variant}: {len(r)} games with shadow, '
          f'{len(results_by_gid)} with resolved score')

    # Roll up per (tier, side)
    from collections import defaultdict
    buckets: dict = defaultdict(lambda: {
        'shadow_wins': 0, 'shadow_losses': 0, 'shadow_pushes': 0,
        'live_wins':   0, 'live_losses':   0, 'live_pushes':   0,
        'n': 0,
    })

    for g in r:
        result = results_by_gid.get(g['game_id'])
        if not result:
            continue
        shadow = g.get('primary_play_shadow_v2') or {}
        # Only grade variants that match
        if shadow.get('variant') != variant:
            continue
        live = g.get('primary_play') or {}
        s_grade = _grade_side(shadow, result)
        l_grade = _grade_side(live, result) if live else None
        if s_grade is None:
            continue
        key = (
            shadow.get('type', 'unknown'),
            shadow.get('tier', 'unknown'),
            shadow.get('side', 'unknown'),
        )
        b = buckets[key]
        b['n'] += 1
        b[f'shadow_{ {"W":"wins","L":"losses","P":"pushes"}[s_grade] }'] += 1
        if l_grade is not None:
            b[f'live_{ {"W":"wins","L":"losses","P":"pushes"}[l_grade] }'] += 1

    if not buckets:
        print(f'  {sport} {variant}: 0 gradeable shadow rows in window')
        return len(r)

    # Emit rollup rows
    payloads = []
    for (market, tier, side), b in sorted(buckets.items()):
        s_dec = b['shadow_wins'] + b['shadow_losses']
        l_dec = b['live_wins'] + b['live_losses']
        s_rate = round(b['shadow_wins'] / s_dec, 4) if s_dec else None
        l_rate = round(b['live_wins'] / l_dec, 4) if l_dec else None
        edge_pp = None
        if s_rate is not None and l_rate is not None:
            edge_pp = round((s_rate - l_rate) * 100, 2)
        payloads.append({
            'sport': sport,
            'market': market,
            'variant': variant,
            'game_date': end,  # rollup keyed on end-of-window
            'tier': tier,
            'side': side,
            'picks_count': b['n'],
            'wins': b['shadow_wins'],
            'losses': b['shadow_losses'],
            'pushes': b['shadow_pushes'],
            'live_hit_rate': l_rate,
            'shadow_hit_rate': s_rate,
            'edge_pp': edge_pp,
            'n_min_ok': b['n'] >= 50,
        })
        print(f"    {market}/{tier}/{side}: n={b['n']} "
              f"shadow={s_rate}({b['shadow_wins']}-{b['shadow_losses']}) "
              f"live={l_rate}({b['live_wins']}-{b['live_losses']}) "
              f"edge_pp={edge_pp}"
              + ('  ✅ N≥50' if b['n'] >= 50 else '  (n<50)'))

    if dry_run:
        print(f'  [DRY] would upsert {len(payloads)} rollup rows')
        return len(r)

    up = requests.post(
        f'{SB}/rest/v1/shadow_v2_backtest_results'
        f'?on_conflict=sport,market,variant,game_date,tier,side',
        headers=H_WRITE, json=payloads, timeout=30,
    )
    if up.status_code in (200, 201, 204):
        print(f'  ✓ upserted {len(payloads)} rollup rows')
    else:
        print(f'  ✗ upsert failed {up.status_code}: {up.text[:200]}')
    return len(r)


def run_prop_backtest(sport: str, variant: str,
                      start: str, end: str, dry_run: bool = False) -> int:
    """Grade prop shadow_v2 vs live picks. Uses already-graded
    nfl_pipeline_props.result — shadow only changes which tier we would
    have assigned, so hit rate compares same outcome under different
    tier gates."""
    prop_tbl = PROP_TABLE.get(sport)
    if not prop_tbl:
        print(f'  {sport}: no prop table registered — skip')
        return 0

    url = (f'{SB}/rest/v1/{prop_tbl}?game_date=gte.{start}&game_date=lte.{end}'
           f'&playbook_snapshot_shadow_v2=not.is.null'
           f'&result=not.is.null'
           f'&select=id,game_date,prop_type,direction,tier,result,'
           f'playbook_snapshot_shadow_v2&limit=5000')
    r = requests.get(url, headers=H_READ, timeout=30).json()
    if not isinstance(r, list):
        print(f'  fetch props failed: {r}')
        return 0

    from collections import defaultdict
    buckets: dict = defaultdict(lambda: {
        'shadow_wins': 0, 'shadow_losses': 0, 'shadow_pushes': 0,
        'live_wins':   0, 'live_losses':   0, 'live_pushes':   0, 'n': 0,
    })

    for prop in r:
        shadow = prop.get('playbook_snapshot_shadow_v2') or {}
        if shadow.get('variant') != variant:
            continue
        grade = _grade_prop(prop)
        if grade is None:
            continue
        key = (
            prop.get('prop_type', 'unknown'),
            shadow.get('tier', 'unknown'),
            prop.get('direction', 'unknown'),
        )
        b = buckets[key]
        b['n'] += 1
        b[f'shadow_{ {"W":"wins","L":"losses","P":"pushes"}[grade] }'] += 1
        live_tier_grade = grade  # same outcome — grade unchanged, tier bucket differs
        # For live comparison, count under live tier bucket separately
        # (aggregate handled downstream in rollup query)
        b[f'live_{ {"W":"wins","L":"losses","P":"pushes"}[grade] }'] += 1

    if not buckets:
        print(f'  {sport} {variant} props: 0 gradeable rows in window')
        return len(r)

    payloads = []
    for (market, tier, side), b in sorted(buckets.items()):
        s_dec = b['shadow_wins'] + b['shadow_losses']
        s_rate = round(b['shadow_wins'] / s_dec, 4) if s_dec else None
        payloads.append({
            'sport': sport, 'market': market, 'variant': variant,
            'game_date': end, 'tier': tier, 'side': side,
            'picks_count': b['n'],
            'wins': b['shadow_wins'], 'losses': b['shadow_losses'],
            'pushes': b['shadow_pushes'],
            'shadow_hit_rate': s_rate,
            'n_min_ok': b['n'] >= 50,
        })
        print(f"    {market}/{tier}/{side}: n={b['n']} shadow_rate={s_rate}")

    if dry_run:
        print(f'  [DRY] would upsert {len(payloads)} prop rollup rows')
        return len(r)

    up = requests.post(
        f'{SB}/rest/v1/shadow_v2_backtest_results'
        f'?on_conflict=sport,market,variant,game_date,tier,side',
        headers=H_WRITE, json=payloads, timeout=30,
    )
    if up.status_code in (200, 201, 204):
        print(f'  ✓ upserted {len(payloads)} prop rollup rows')
    else:
        print(f'  ✗ upsert failed {up.status_code}: {up.text[:200]}')
    return len(r)


# ─── Main ─────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', required=True, choices=['NFL', 'NCAAF'])
    p.add_argument('--variant', required=True,
                   help='Variant tag written to shadow JSONB (e.g. usage_v2)')
    p.add_argument('--surface', choices=['sides', 'props', 'both'], default='both')
    p.add_argument('--start', default=None, help='YYYY-MM-DD (defaults 30d back)')
    p.add_argument('--end', default=None, help='YYYY-MM-DD (defaults today)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    today = datetime.now(timezone.utc).date().isoformat()
    from datetime import timedelta
    start = args.start or (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
    end = args.end or today

    print(f'=== shadow_v2_backtest · {args.sport} · variant={args.variant} · '
          f'{start} → {end} · surface={args.surface} ===')

    total = 0
    if args.surface in ('sides', 'both'):
        total += run_side_backtest(args.sport, args.variant, start, end, args.dry_run)
    if args.surface in ('props', 'both') and args.sport in PROP_TABLE:
        total += run_prop_backtest(args.sport, args.variant, start, end, args.dry_run)

    print(f'\n=== done · {total} rows scanned ===')


if __name__ == '__main__':
    main()
