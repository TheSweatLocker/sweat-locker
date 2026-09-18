"""Phase 1 shadow scorer — NFL prop usage_v2.

Writes `nfl_pipeline_props.playbook_snapshot_shadow_v2` with candidate
tier + conviction derived from usage signals the live scorer barely uses:

    WOPR              — best single WR opportunity metric (0-1 scale)
    air_yards_share   — separates deep threats from possession catchers
    targets (raw)     — pure volume; strongest single predictor of catches
    yac_per_reception — playmaker vs possession (rec_yds implication)

Signal composite → conviction delta → tier bump/demote if strong enough.
Tagged `variant: "usage_v2"` for the backtest harness.

**Reads live prop; writes shadow ONLY.** Zero impact on user-visible
picks until we promote via shadow_v2_backtest.py gate:
    picks_count >= 50 AND edge_pp >= 3.0 sustained ≥ 4 weeks.

Usage:
    # Score all upcoming NFL props (default: 14d window)
    python nfl_prop_usage_v2_shadow.py

    # Backfill historical props for a specific week
    python nfl_prop_usage_v2_shadow.py --start 2026-09-10 --end 2026-09-15

    # Dry-run — show scoring without DB writes
    python nfl_prop_usage_v2_shadow.py --dry-run

2026-09-17: Phase 1 of NFL SOTA rebuild. See
memory/project_nfl_sota_rebuild_917.md for full context + phase list.
"""
from __future__ import annotations

import argparse
import functools
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

_ENV = Path(__file__).parent / '.env'
if _ENV.exists():
    for line in _ENV.read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
if not SB or not KEY:
    print('SUPABASE_URL / SUPABASE_KEY not set — refusing to run.')
    sys.exit(1)

H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

VARIANT = 'usage_v2'

# Signal thresholds — see project_nfl_sota_rebuild_917 for rationale.
# Values calibrated from Andy's 9/17 SOTA proposal; will re-tune based
# on backtest hit-rate data after 4+ weeks of shadow.
WOPR_ELITE      = 0.55   # tier promotion candidate on OVER
WOPR_HIGH       = 0.45
WOPR_LOW        = 0.20   # tier demotion / UNDER lean
AIR_SHARE_DEEP  = 0.35   # deep-threat marker
TARGET_VOL_HIGH = 8.0    # avg targets/game
TARGET_VOL_LOW  = 3.0
YAC_PLAYMAKER   = 5.0    # avg YAC/reception
YAC_POSSESSION  = 2.0

# Signal bonuses (added to live conviction to produce shadow_conviction)
BONUS = {
    'wopr_elite_over':      +10,
    'wopr_high_over':       +6,
    'wopr_low_over':        -8,
    'wopr_low_under':       +6,
    'wopr_elite_under':     -6,
    'air_share_deep_over':  +5,       # applies primarily to rec_yds
    'air_share_deep_under_receptions': +3,  # deep guys catch fewer but bigger
    'target_vol_high_over': +6,
    'target_vol_low_over':  -7,
    'target_vol_low_under': +5,
    'yac_playmaker_rec_yds_over': +5,
    'yac_possession_rec_yds_over': -3,
    'yac_possession_receptions_over': +3,
}

# Tier bumps when composite score crosses thresholds
COMPOSITE_PROMOTE = 12   # composite bonus this high → bump tier up one step
COMPOSITE_DEMOTE  = -8   # composite bonus this low → bump tier down one step
TIER_ORDER = ['SKIP', 'LIGHT', 'COVERAGE', 'LEAN', 'STRONG', 'PRIME']

# Prop families we score (pass-catcher + rushing; anything volume-driven)
FAMILIES = {
    'receptions',
    'reception_yds',
    'rush_yds',
    'rush_attempts',
    'anytime_td',
    'longest_reception',
}


def _tier_shift(current_tier: str, bump: int) -> str:
    """Shift tier up (+1) or down (-1) in TIER_ORDER, bounded."""
    if not current_tier or current_tier not in TIER_ORDER:
        return current_tier
    idx = TIER_ORDER.index(current_tier)
    new_idx = max(0, min(len(TIER_ORDER) - 1, idx + bump))
    return TIER_ORDER[new_idx]


@functools.lru_cache(maxsize=None)
def fetch_player_usage(player_name: str, player_team: str, season: int) -> dict:
    """L4 WOPR/target_share/air_yards_share/targets + season YAC/rec avg.

    Looks up by (player_name, team) — nfl_pipeline_props doesn't carry
    player_id, only player_name + player_team. Cached — same player hits
    this repeatedly across markets for one game.

    Returns dict with L4 usage + season YAC/reception. None values allowed.
    """
    # URL-encode name (may contain apostrophes / spaces)
    from urllib.parse import quote
    name_enc = quote(player_name)
    team_enc = quote(player_team)
    base_sel = ('week,targets,target_share,air_yards_share,wopr,'
                'receptions,receiving_yards_after_catch,receiving_air_yards')
    # Recent 6 weeks descending → take L4 of what's there
    r = requests.get(
        f'{SB}/rest/v1/nfl_player_stats?player_name=eq.{name_enc}'
        f'&team=eq.{team_enc}&season=eq.{season}&season_type=eq.REG'
        f'&select={base_sel}&order=week.desc&limit=6',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200:
        return {}
    rows = r.json() or []
    # Prior-season fallback if <3 rows this season
    if len(rows) < 3:
        r2 = requests.get(
            f'{SB}/rest/v1/nfl_player_stats?player_name=eq.{name_enc}'
            f'&team=eq.{team_enc}&season=eq.{season - 1}&season_type=eq.REG'
            f'&select={base_sel}&order=week.desc&limit=10',
            headers=H_READ, timeout=15,
        )
        if r2.status_code == 200:
            prior = r2.json() or []
            rows = (rows + prior)[:10]
        # 2026-09-17: also try prior season without team filter — handles
        # offseason moves (Cousins ATL→LV, etc.) where prior season team
        # differs from current player_team.
        if len(rows) < 3:
            r3 = requests.get(
                f'{SB}/rest/v1/nfl_player_stats?player_name=eq.{name_enc}'
                f'&season=eq.{season - 1}&season_type=eq.REG'
                f'&select={base_sel}&order=week.desc&limit=10',
                headers=H_READ, timeout=15,
            )
            if r3.status_code == 200:
                rows = (rows + (r3.json() or []))[:10]

    def _avg(col: str, sample: list) -> Optional[float]:
        vals = [r.get(col) for r in sample if r.get(col) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    l4 = rows[:4] if len(rows) >= 4 else rows
    # YAC/reception = sum(YAC) / sum(receptions), across up to 10 games
    yac_rows = rows[:10]
    total_yac = sum((r.get('receiving_yards_after_catch') or 0) for r in yac_rows)
    total_rec = sum((r.get('receptions') or 0) for r in yac_rows)
    yac_per_rec = round(total_yac / total_rec, 2) if total_rec >= 3 else None

    return {
        'l4_wopr':             _avg('wopr', l4),
        'l4_target_share':     _avg('target_share', l4),
        'l4_air_yards_share':  _avg('air_yards_share', l4),
        'l4_targets':          _avg('targets', l4),
        'yac_per_reception':   yac_per_rec,
        'sample_n':            len(rows),
    }


def compute_usage_v2_shadow(prop: dict) -> Optional[dict]:
    """Compute shadow tier + conviction for one prop row.

    Returns shadow blob to store in playbook_snapshot_shadow_v2, or None
    if the prop's family isn't in scope (e.g. QB pass_yds — different
    signal set; will get its own variant later)."""
    prop_type = (prop.get('prop_type') or '').lower()
    # Strip _over/_under suffix to get family
    family = prop_type.replace('_over', '').replace('_under', '')
    if family not in FAMILIES:
        return None

    direction = (prop.get('direction') or '').lower()  # 'over' | 'under'
    is_over = direction == 'over'
    player_name = prop.get('player_name')
    player_team = prop.get('player_team')
    # Season derived from game_date year — nfl_pipeline_props has no `season`
    game_date = prop.get('game_date') or ''
    try:
        season = int(game_date[:4])
    except (ValueError, TypeError):
        season = 2026
    if not player_name or not player_team:
        return None

    usage = fetch_player_usage(player_name, player_team, season)
    if usage.get('sample_n', 0) < 2:
        # Not enough player history — skip shadow (don't lie about signal)
        return None

    signals_fired: list[str] = []
    composite = 0

    wopr = usage.get('l4_wopr')
    tgts = usage.get('l4_targets')
    ays  = usage.get('l4_air_yards_share')
    yac  = usage.get('yac_per_reception')

    # WOPR gates — apply to receptions/rec_yds/anytime_td families
    if wopr is not None and family in ('receptions', 'reception_yds', 'anytime_td'):
        if wopr >= WOPR_ELITE:
            if is_over:
                composite += BONUS['wopr_elite_over']; signals_fired.append(f'wopr_elite({wopr:.2f})+{BONUS["wopr_elite_over"]}')
            else:
                composite += BONUS['wopr_elite_under']; signals_fired.append(f'wopr_elite({wopr:.2f}){BONUS["wopr_elite_under"]}')
        elif wopr >= WOPR_HIGH and is_over:
            composite += BONUS['wopr_high_over']; signals_fired.append(f'wopr_high({wopr:.2f})+{BONUS["wopr_high_over"]}')
        elif wopr <= WOPR_LOW:
            if is_over:
                composite += BONUS['wopr_low_over']; signals_fired.append(f'wopr_low({wopr:.2f}){BONUS["wopr_low_over"]}')
            else:
                composite += BONUS['wopr_low_under']; signals_fired.append(f'wopr_low({wopr:.2f})+{BONUS["wopr_low_under"]}')

    # Air-yards-share deep-threat marker
    if ays is not None and ays >= AIR_SHARE_DEEP:
        if family == 'reception_yds' and is_over:
            composite += BONUS['air_share_deep_over']; signals_fired.append(f'air_deep({ays:.2f})+{BONUS["air_share_deep_over"]}')
        elif family == 'receptions' and not is_over:
            composite += BONUS['air_share_deep_under_receptions']
            signals_fired.append(f'air_deep({ays:.2f})+{BONUS["air_share_deep_under_receptions"]}')

    # Raw target volume — receptions/rec_yds
    if tgts is not None and family in ('receptions', 'reception_yds'):
        if tgts >= TARGET_VOL_HIGH and is_over:
            composite += BONUS['target_vol_high_over']; signals_fired.append(f'tgt_vol_high({tgts:.1f})+{BONUS["target_vol_high_over"]}')
        elif tgts <= TARGET_VOL_LOW:
            if is_over:
                composite += BONUS['target_vol_low_over']; signals_fired.append(f'tgt_vol_low({tgts:.1f}){BONUS["target_vol_low_over"]}')
            else:
                composite += BONUS['target_vol_low_under']; signals_fired.append(f'tgt_vol_low({tgts:.1f})+{BONUS["target_vol_low_under"]}')

    # YAC — playmaker vs possession
    if yac is not None and family == 'reception_yds' and is_over:
        if yac >= YAC_PLAYMAKER:
            composite += BONUS['yac_playmaker_rec_yds_over']; signals_fired.append(f'yac_playmaker({yac:.1f})+{BONUS["yac_playmaker_rec_yds_over"]}')
        elif yac <= YAC_POSSESSION:
            composite += BONUS['yac_possession_rec_yds_over']; signals_fired.append(f'yac_possession({yac:.1f}){BONUS["yac_possession_rec_yds_over"]}')
    if yac is not None and family == 'receptions' and is_over and yac <= YAC_POSSESSION:
        composite += BONUS['yac_possession_receptions_over']; signals_fired.append(f'yac_possession({yac:.1f})+{BONUS["yac_possession_receptions_over"]}')

    if not signals_fired:
        return None

    live_tier = prop.get('tier') or 'SKIP'
    live_conv = prop.get('conviction') or 50
    shadow_conv = max(0, min(100, int(live_conv + composite)))

    # Tier bump/demote gate
    tier_shift = 0
    if composite >= COMPOSITE_PROMOTE:
        tier_shift = 1
    elif composite <= COMPOSITE_DEMOTE:
        tier_shift = -1
    shadow_tier = _tier_shift(live_tier, tier_shift)

    return {
        'variant': VARIANT,
        'live_tier': live_tier,
        'live_conviction': live_conv,
        'shadow_tier': shadow_tier,
        'shadow_conviction': shadow_conv,
        'composite_bonus': composite,
        'tier_shift': tier_shift,
        'signals_fired': signals_fired,
        'usage_inputs': {
            'wopr': wopr, 'target_share': usage.get('l4_target_share'),
            'air_yards_share': ays, 'targets': tgts, 'yac_per_reception': yac,
            'sample_n': usage.get('sample_n'),
        },
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }


def run(start_date: str, end_date: str, dry_run: bool = False) -> None:
    print(f'=== nfl_prop_usage_v2_shadow · {start_date} → {end_date} '
          f'{"(DRY)" if dry_run else "(APPLY)"} ===')

    # Fetch upcoming NFL props in window
    url = (f'{SB}/rest/v1/nfl_pipeline_props?game_date=gte.{start_date}'
           f'&game_date=lte.{end_date}&select=id,player_name,player_team,'
           f'game_date,prop_type,direction,prop_line,tier,conviction'
           f'&limit=5000')
    r = requests.get(url, headers=H_READ, timeout=30).json()
    if not isinstance(r, list):
        print(f'  fetch failed: {r}')
        return
    print(f'  props in window: {len(r)}')

    scored = written = skipped = 0
    stats_by_shift = {'promote': 0, 'same': 0, 'demote': 0}

    for prop in r:
        shadow = compute_usage_v2_shadow(prop)
        if not shadow:
            skipped += 1
            continue
        scored += 1
        shift = shadow.get('tier_shift', 0)
        stats_by_shift['promote' if shift > 0 else ('demote' if shift < 0 else 'same')] += 1

        if dry_run:
            continue
        pr = requests.patch(
            f'{SB}/rest/v1/nfl_pipeline_props?id=eq.{prop["id"]}',
            headers=H_WRITE,
            json={
                'playbook_snapshot_shadow_v2': shadow,
                'shadow_v2_generated_at': datetime.now(timezone.utc).isoformat(),
            },
            timeout=15,
        )
        if pr.status_code in (200, 201, 204):
            written += 1
        else:
            print(f'  ✗ id={prop["id"]}: {pr.status_code} {pr.text[:120]}')

    print(f'\n=== summary ===')
    print(f'  scored: {scored}  · skipped (out-of-scope/thin-sample): {skipped}')
    print(f'  tier shifts: promote={stats_by_shift["promote"]}  '
          f'same={stats_by_shift["same"]}  demote={stats_by_shift["demote"]}')
    if not dry_run:
        print(f'  wrote: {written}/{scored} shadow blobs')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--start', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--end', help='YYYY-MM-DD (default: start + 13d)')
    p.add_argument('--days', type=int, default=14,
                   help='Window size in days when --end omitted (default 14)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    today_et = (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()
    start = args.start or today_et
    if args.end:
        end = args.end
    else:
        end = (datetime.strptime(start, '%Y-%m-%d') + timedelta(days=args.days - 1)).strftime('%Y-%m-%d')

    run(start, end, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
