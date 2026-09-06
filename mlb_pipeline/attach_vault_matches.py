"""Vault Match attach — evaluates PATTERN_CATALOG against today's
game_context rows and persists matched_patterns[] to each row.

Runs AFTER game_context builder + AFTER compute_sport_patterns.py so:
  1. Today's ctx rows exist with current tier/splits/etc.
  2. sport_pattern_registry has current hit rates per pattern.

For each upcoming game (game_date >= today - 1d, <= today + 8d):
  - Load ctx row
  - Iterate PATTERN_CATALOG (matching this row's sport)
  - Call matches_fn(ctx) — did this pattern fire on THIS game?
  - For each fired pattern, look up its current metric in
    sport_pattern_registry
  - Filter to patterns where n_total >= 15 AND hit_pct >= 65
  - Write matched_patterns = [{key, label, hit_pct, n, description}...]

Silent-hide contract: if no patterns clear threshold, matched_patterns
stays [] and the app renders no Vault Match chip.

USAGE:
    python attach_vault_matches.py                # all sports
    python attach_vault_matches.py --sport MLB
    python attach_vault_matches.py --dry-run
"""
import argparse
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

from compute_sport_patterns import PATTERN_CATALOG

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


# Badge threshold — patterns below this don't fire the chip.
# 2026-09-06: relaxed 65 → 60 for launch. 60% at -110 is ~+9% ROI —
# still a real edge and enough to surface a Vault chip. Sparse-coverage
# risk (some days no matches) is accepted per user directive; we'd
# rather show nothing than surface below-break-even patterns.
# n=15 unchanged — small-sample noise dominates below that regardless
# of point-estimate hit rate.
MIN_N = 15
MIN_HIT_PCT = 60.0

# 2026-09-01 GUARDRAILS:
# - Skip a pattern if its stats are older than this — prevents rendering
#   stale hit rates if recompute step failed.
STALE_HOURS = 36
# - Wilson lower-bound floor. A pattern's TRUE hit rate could be as low
#   as this given sample noise. Below 55% (still positive edge over 50%)
#   we don't render the chip even if point estimate >= 65%.
MIN_WILSON_LOWER = 0.52  # 2026-09-06: relaxed 0.55→0.52 alongside MIN_HIT_PCT drop.
# 0.52 = break-even at -110 juice. Anything below actively loses on -110 stakes
# so it's still a real floor; just doesn't require the extra +3pp cushion the
# 0.55 floor did.
# - Reject if EV is < break-even at -110 juice. Prevents shipping edges
#   that look positive but don't cover book vig. -110 = 52.38% break-even.
MIN_HIT_PCT_ABOVE_JUICE = 52.4


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple:
    """Wilson score confidence interval for a binomial proportion.
    Returns (lower, upper) bounds in [0, 1]. Uses z=1.96 for 95% CI.
    Handles edge cases (n=0) safely."""
    if n <= 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z*z/n
    center = (p + z*z/(2*n)) / denom
    margin = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _hours_since(iso_ts: Optional[str]) -> Optional[float]:
    if not iso_ts:
        return None
    try:
        # Registry writes iso8601 without tz; treat as ET (matches _et_now)
        ts = datetime.fromisoformat(str(iso_ts).replace('Z', '+00:00'))
        if ts.tzinfo is None:
            # ET-naive → localize
            ts = ts.replace(tzinfo=timezone.utc) - timedelta(hours=4)
        delta = _et_now() - ts.replace(tzinfo=None)
        return delta.total_seconds() / 3600.0
    except Exception:
        return None


def _fetch_pattern_metrics(sport: str) -> dict:
    """Load current sport_pattern_registry rows keyed by pattern_key."""
    r = requests.get(
        f'{SB}/rest/v1/sport_pattern_registry?'
        f'select=*&sport=eq.{sport}',
        headers=H_READ, timeout=30,
    )
    if r.status_code != 200:
        return {}
    return {row['pattern_key']: row for row in r.json()}


def _fetch_upcoming_games(sport: str) -> list:
    """Load upcoming ctx rows for a sport (today-1d to today+8d)."""
    today = _et_now().date()
    lo = (today - timedelta(days=1)).isoformat()
    hi = (today + timedelta(days=8)).isoformat()
    ctx_table = f'{sport.lower()}_game_context'
    r = requests.get(
        f'{SB}/rest/v1/{ctx_table}?'
        f'select=*&game_date=gte.{lo}&game_date=lte.{hi}',
        headers=H_READ, timeout=30,
    )
    return r.json() if r.status_code == 200 else []


def _write_matched(sport: str, game_id: str, patterns: list, dry_run: bool) -> bool:
    """Upsert matched_patterns[] to the sport's game_context row."""
    ctx_table = f'{sport.lower()}_game_context'
    if dry_run:
        labels = ', '.join(p['label'] for p in patterns) if patterns else '—'
        print(f"  [DRY] {sport} {game_id}: {len(patterns)} matched → {labels}")
        return True

    r = requests.patch(
        f'{SB}/rest/v1/{ctx_table}?game_id=eq.{game_id}',
        headers=H_WRITE,
        json={'matched_patterns': patterns},
        timeout=30,
    )
    if r.status_code not in (200, 204):
        print(f'  ⚠ patch failed {sport} {game_id}: {r.status_code} {r.text[:120]}')
        return False
    return True


def run(sport_filter: Optional[str] = None, dry_run: bool = False) -> None:
    print('=== Vault Match attach ===')
    sports = sorted({p['sport'] for p in PATTERN_CATALOG
                     if not sport_filter or p['sport'] == sport_filter.upper()})
    if not sports:
        print(f'  no sports in catalog (filter={sport_filter!r})')
        return

    total_games = 0
    total_matches = 0
    for sport in sports:
        metrics = _fetch_pattern_metrics(sport)
        if not metrics:
            print(f'\n  {sport}: no pattern metrics yet (run compute_sport_patterns.py first)')
            continue
        games = _fetch_upcoming_games(sport)
        print(f'\n  {sport}: {len(games)} upcoming games, {len(metrics)} tracked patterns')
        sport_patterns = [p for p in PATTERN_CATALOG if p['sport'] == sport]

        for game in games:
            total_games += 1
            matched = []
            for pattern in sport_patterns:
                try:
                    if not pattern['matches'](game):
                        continue
                except Exception:
                    continue
                metric = metrics.get(pattern['key'])
                if not metric:
                    continue

                # ── Guardrail: freshness ──────────────────────────────
                age_hrs = _hours_since(metric.get('last_computed_at'))
                if age_hrs is not None and age_hrs > STALE_HOURS:
                    continue

                n_total = int(metric.get('n_total') or 0)
                n_wins = int(metric.get('n_wins') or 0)
                hit_pct = float(metric.get('hit_pct') or 0)

                # ── Guardrail: sample floor ───────────────────────────
                if n_total < MIN_N:
                    continue

                # ── Guardrail: point estimate threshold ───────────────
                if hit_pct < MIN_HIT_PCT:
                    continue

                # ── Guardrail: EV over vig ────────────────────────────
                if hit_pct < MIN_HIT_PCT_ABOVE_JUICE:
                    continue

                # ── Guardrail: Wilson lower bound ─────────────────────
                # Even if point estimate is 72%, if the lower CI bound is
                # 48% we're not confident enough to render a "moat" chip.
                wilson_lo, wilson_hi = _wilson_ci(n_wins, n_wins + int(metric.get('n_losses') or 0))
                if wilson_lo < MIN_WILSON_LOWER:
                    continue

                matched.append({
                    'key': pattern['key'],
                    'label': pattern['label'],
                    'direction': pattern.get('direction') or metric.get('direction') or 'BACK',
                    'description': pattern.get('description') or metric.get('pattern_description') or '',
                    'hit_pct': hit_pct,
                    'n': n_total,
                    # 2026-09-01 guardrail payload — app drawer surfaces
                    # so users see the uncertainty, not just the point
                    # estimate. wilson bounds are 0-1 (multiply by 100
                    # for display).
                    'wilson_low':  round(wilson_lo * 100, 1),
                    'wilson_high': round(wilson_hi * 100, 1),
                    'lookback_days': int(metric.get('lookback_days') or 30),
                    'computed_hours_ago': round(age_hrs, 1) if age_hrs is not None else None,
                })
            # Sort matched by strength (hit_pct DESC) so app can render best first
            matched.sort(key=lambda m: (-m['hit_pct'], -m['n']))
            _write_matched(sport, str(game.get('game_id')), matched, dry_run)
            total_matches += len(matched)

    verb = '[DRY]' if dry_run else 'attached'
    print(f'\n  {verb}: {total_matches} matches across {total_games} games')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', help='Only attach this sport')
    ap.add_argument('--dry-run', action='store_true', help='Print without writing')
    args = ap.parse_args()
    run(sport_filter=args.sport, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
