"""NFL play-of-day generator — reads nfl_game_context, tier-gates picks,
writes to nfl_game_picks, flags lock_of_week.

Runs AFTER nfl_game_context.py in the cron. Because game_context already
computes projections + confluence + primary_play, this file is thin:
   1. Read upcoming-week rows from nfl_game_context
   2. For each row with primary_play, write a nfl_game_picks row
   3. Select ONE pick per week as lock_of_week (highest conviction, tie-break
      prefers PRIME → STRONG → LIGHT; within tier prefers cohort_tags with
      audit-validated hit rates)
   4. Also write "skip" rows for high-visibility chalk traps (helps app
      surface Skip Alerts consistently)

Weekly-cadence mental model:
  Tue 11am ET cron → context refresh + picks write
  Thu 2pm ET cron  → lock_of_week finalized after TNF landscape known
  Sun 8am ET cron  → last refresh before Sunday slate goes live

Result column filled post-game by resolve_nfl_results.py (Phase 3.2).

Usage:
    python nfl_play_of_day.py             # process upcoming games
    python nfl_play_of_day.py --dry-run
"""
import argparse
import os
import sys
from datetime import datetime, date, timedelta, timezone
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


# Cohort priority order for lock-of-week tie-breaking. Audit-validated
# cohorts first, then confluence-derived, then generic.
COHORT_PRIORITY = [
    'nfl_heavy_home_dog',    # 63.1% audit lifetime (n=65)
    'nfl_outdoor_under',     # 55.6% audit lifetime (n=196)
    'nfl_dome_over',
    'nfl_div_home_cover',
    'nfl_home_fav',
]

# Skip-alert cohorts (chalk traps — write as pick_type='skip')
SKIP_COHORTS = {
    # Mid-range div home fav (-3.5 to -6.5): 48.6% audit — coinflip trap
    'div_home_fav_mid': lambda ctx: (
        ctx.get('close_spread') is not None
        and 3.5 <= float(ctx['close_spread']) <= 6.5
        and ctx.get('div_game') is True
    ),
    # Dome + total >= 50 (juiced OVER)
    'dome_over_juiced': lambda ctx: (
        (ctx.get('roof') or '').lower() in ('dome', 'closed')
        and ctx.get('close_total') is not None
        and float(ctx['close_total']) >= 50.0
    ),
}


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(v) if v is not None else None
    except (TypeError, ValueError): return None


def load_upcoming(days_ahead: int = 10) -> list:
    """Pull nfl_game_context rows whose kickoff is today or within N days."""
    today = _et_now().date()
    horizon = today + timedelta(days=days_ahead)
    r = requests.get(
        f'{SB}/rest/v1/nfl_game_context'
        f'?game_date=gte.{today.isoformat()}'
        f'&game_date=lte.{horizon.isoformat()}'
        f'&select=*&order=game_date.asc',
        headers=H_READ, timeout=15,
    )
    return r.json() if r.status_code == 200 else []


def _side_from_play(play: dict, ctx: dict) -> Optional[str]:
    """Convert primary_play.label → structured pick_side.

    primary_play is generated in nfl_game_context.compute_primary_play with
    labels like "Panthers +7", "Over 44.5", "PHI spread lean". We reconstruct
    'home' | 'away' | 'over' | 'under'.
    """
    if not play: return None
    label = (play.get('label') or '').lower()
    if 'over' in label: return 'over'
    if 'under' in label: return 'under'
    # Team-based: match against home/away name
    home = (ctx.get('home_team') or '').lower()
    away = (ctx.get('away_team') or '').lower()
    if home and home in label: return 'home'
    if away and away in label: return 'away'
    # Fallback: infer from primary_play sub text or projected_spread sign
    ps = ctx.get('projected_spread')
    if ps is not None:
        return 'home' if float(ps) > 0 else 'away'
    return None


def _pick_line(play: dict, ctx: dict) -> Optional[float]:
    """Extract numeric line from primary_play. For spreads, use close_spread
    magnitude; for totals, use close_total."""
    if not play: return None
    ptype = (play.get('type') or '').lower()
    if ptype == 'total':
        return _f(ctx.get('close_total'))
    if ptype == 'spread':
        cs = _f(ctx.get('close_spread'))
        return abs(cs) if cs is not None else None
    if ptype == 'ml':
        return None
    return None


def build_pick_row(ctx: dict) -> Optional[dict]:
    """Build one nfl_game_picks row from an nfl_game_context row.
    Returns None when the game has no primary_play (below LIGHT threshold)."""
    play = ctx.get('primary_play') or {}
    if not play:
        return None

    ptype = (play.get('type') or 'skip').lower()
    tier = play.get('tier')
    conv = play.get('signal_floor') or 0
    side = _side_from_play(play, ctx)
    if not side:
        return None
    line = _pick_line(play, ctx)

    ps = _f(ctx.get('projected_spread'))
    cs = _f(ctx.get('close_spread'))
    pt = _f(ctx.get('projected_total'))
    ct = _f(ctx.get('close_total'))
    spread_edge = round(ps - cs, 2) if (ps is not None and cs is not None) else None
    total_edge = round(pt - ct, 2) if (pt is not None and ct is not None) else None

    return {
        'game_id': ctx['game_id'],
        'game_date': ctx['game_date'],
        'season': ctx.get('season'),
        'week': ctx.get('week'),
        'season_type': ctx.get('season_type') or 'REG',
        'home_team': ctx['home_team'],
        'away_team': ctx['away_team'],
        'pick_type': ptype,
        'pick_side': side,
        'pick_label': play.get('label'),
        'pick_line': line,
        'odds_american': (
            ctx.get('close_home_ml') if side == 'home' and ptype == 'ml'
            else ctx.get('close_away_ml') if side == 'away' and ptype == 'ml'
            else None
        ),
        'tier': tier,
        'conviction': conv,
        'cohort_tags': ctx.get('cohort_tags') or [],
        'projected_spread': ps,
        'projected_total': pt,
        'close_spread': cs,
        'close_total': ct,
        'spread_edge': spread_edge,
        'total_edge': total_edge,
        'signal_confluence': ctx.get('signal_confluence_net'),
        'signals': {
            'primary_play_sub': play.get('sub'),
            'signal_floor': play.get('signal_floor'),
            'sweat_score': ctx.get('sweat_score'),
            'sweat_tier': ctx.get('sweat_tier'),
            'breakdown': ctx.get('signal_confluence_breakdown'),
        },
        'is_lock_of_week': False,  # set separately in select_lock_of_week
    }


def build_skip_rows(ctx: dict) -> list:
    """Emit skip-alert rows for chalk-trap cohorts. Helps app surface a
    consistent 'Skip Alerts' section."""
    rows = []
    for reason, fn in SKIP_COHORTS.items():
        try:
            if fn(ctx):
                rows.append({
                    'game_id': ctx['game_id'],
                    'game_date': ctx['game_date'],
                    'season': ctx.get('season'),
                    'week': ctx.get('week'),
                    'season_type': ctx.get('season_type') or 'REG',
                    'home_team': ctx['home_team'],
                    'away_team': ctx['away_team'],
                    'pick_type': 'skip',
                    'pick_side': 'skip',
                    'pick_label': f'SKIP: {reason}',
                    'tier': None,
                    'conviction': None,
                    'cohort_tags': ctx.get('cohort_tags') or [],
                    'close_spread': _f(ctx.get('close_spread')),
                    'close_total': _f(ctx.get('close_total')),
                    'signals': {'reason': reason},
                    'is_lock_of_week': False,
                })
        except Exception:
            continue
    return rows


def select_lock_of_week(picks: list) -> Optional[str]:
    """Pick the single row that becomes lock_of_week. Returns the game_id."""
    candidates = [p for p in picks
                  if p['pick_type'] not in ('skip',)
                  and p.get('tier') in ('PRIME', 'STRONG', 'LIGHT')]
    if not candidates:
        return None

    def rank_key(p):
        tier_rank = {'PRIME': 0, 'STRONG': 1, 'LIGHT': 2}.get(p['tier'], 9)
        # Cohort priority — lower index = more preferred
        cohort_rank = 99
        for i, c in enumerate(COHORT_PRIORITY):
            if c in (p.get('cohort_tags') or []):
                cohort_rank = i
                break
        conv = -(p.get('conviction') or 0)  # higher conviction first
        return (tier_rank, cohort_rank, conv)

    candidates.sort(key=rank_key)
    return candidates[0]['game_id']


def upsert_picks(rows: list, dry_run: bool = False) -> int:
    if not rows: return 0
    if dry_run:
        for r in rows:
            lock = ' 🔒 LOCK' if r.get('is_lock_of_week') else ''
            print(f"  [DRY] {r['game_id'][:12]}...  "
                  f"{r['pick_type']:6}:{r['pick_side']:5}  "
                  f"tier={r.get('tier') or '-':<6}  "
                  f"conv={r.get('conviction') or '-':<3}  "
                  f"{r.get('pick_label') or ''}{lock}")
        return len(rows)
    r = requests.post(
        f'{SB}/rest/v1/nfl_game_picks?on_conflict=game_id,pick_type,pick_side',
        headers=H_WRITE, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(rows)


def run(dry_run: bool = False) -> None:
    print(f'=== NFL play-of-day · {_et_now().date()} ===')
    contexts = load_upcoming()
    print(f'  upcoming game_context rows: {len(contexts)}')
    if not contexts:
        print('  no upcoming games — offseason or lines not yet loaded')
        return

    all_rows = []
    picks_only = []
    for ctx in contexts:
        pick = build_pick_row(ctx)
        if pick:
            all_rows.append(pick)
            picks_only.append(pick)
        all_rows.extend(build_skip_rows(ctx))

    # Flag lock_of_week
    lock_gid = select_lock_of_week(picks_only)
    for p in all_rows:
        if p['game_id'] == lock_gid and p['pick_type'] != 'skip':
            p['is_lock_of_week'] = True

    print(f'  picks generated: {len(picks_only)} '
          f'(+ {len(all_rows) - len(picks_only)} skip-alerts)  '
          f'lock_of_week: {lock_gid[:12] + "..." if lock_gid else "—"}')

    written = upsert_picks(all_rows, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}wrote {written} rows to nfl_game_picks')

    # Tier tally
    by_tier = {'PRIME': 0, 'STRONG': 0, 'LIGHT': 0, 'LEAN': 0, 'skip': 0}
    for p in all_rows:
        t = p.get('tier') or 'skip'
        by_tier[t] = by_tier.get(t, 0) + 1
    print(f'  by tier: {by_tier}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
