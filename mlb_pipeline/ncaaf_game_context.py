"""NCAAF game context pipeline — server-side analog of nfl_game_context.

Reads live NCAAF games from ncaaf_game_results (populated by
ncaaf_odds_pull), joins ncaaf_team_stats, computes SP+ and EPA-based
projections + confluence + sweat + primary_play, upserts to
ncaaf_game_context.

Model (v1 — Week 1 baseline, calibrates after Week 4):
  power_diff = (home_sp_overall - away_sp_overall)   OR fallback:
               (home_off_epa - home_def_epa) - (away_off_epa - away_def_epa)
  projected_spread = power_diff * K_PTS + HFA
  projected_total  = 52.0 baseline + roof/temp adjustments (CFB scores higher than NFL)

USAGE:
    python ncaaf_game_context.py           # today + next 7d
    python ncaaf_game_context.py --dry-run
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


# Calibration constants — CFB has larger score variance than NFL.
K_PTS_SP = 0.85           # SP+ rating diff → spread points scaling
K_PTS_EPA = 5.5           # EPA diff → spread points (fallback)
HOME_FIELD_PTS = 2.8      # CFB HFA slightly higher than NFL
BASE_TOTAL = 52.0         # CFB avg total higher than NFL


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(v) if v is not None else None
    except (TypeError, ValueError): return None


def load_upcoming(days_ahead: int = 10) -> list:
    """Pull upcoming ncaaf_game_results within horizon."""
    today = _et_now().date().isoformat()
    horizon = (_et_now() + timedelta(days=days_ahead)).date().isoformat()
    r = requests.get(
        f'{SB}/rest/v1/ncaaf_game_results?'
        f'game_date=gte.{today}&game_date=lte.{horizon}'
        f'&select=*&order=game_date.asc&limit=200',
        headers=H_READ, timeout=15,
    )
    return r.json() if r.status_code == 200 else []


def load_team_stats(season: int) -> dict:
    """Return {team: stats_row} for the season."""
    r = requests.get(
        f'{SB}/rest/v1/ncaaf_team_stats?season=eq.{season}&season_type=eq.regular&select=*',
        headers=H_READ, timeout=15,
    )
    return {row['team']: row for row in r.json()} if r.status_code == 200 else {}


# Weeks 1-3 discipline (mirrors NFL Sept-4 fallback).
# CFB regular season = ~12 games per team; 3 games/team avg ≈ Week 4.
# Below this we fall back to prior season with regression-to-mean.
# Shrink=0.5 (heavier than NFL's 0.4) because CFB has higher year-over-year
# roster turnover — portal transfers + coaching changes shift team quality
# more than NFL free agency does. Cutting prior-year edge in half is honest.
MIN_GAMES_PER_TEAM_AVG = 3.0
SHRINK = 0.5


def _avg_games(stats_dict: dict) -> float:
    if not stats_dict: return 0.0
    games = [(row.get('games') or 0) for row in stats_dict.values()]
    return sum(games) / max(1, len(games))


def _league_mean_stats(stats_dict: dict) -> dict:
    """Compute league-mean SP+/EPA rates for regression-to-mean blending."""
    if not stats_dict: return {}
    keys = ('sp_overall', 'off_epa_per_play', 'def_epa_per_play',
            'off_success_rate', 'off_explosiveness')
    totals = {k: 0.0 for k in keys}
    counts = {k: 0 for k in keys}
    for row in stats_dict.values():
        for k in keys:
            v = row.get(k)
            if v is not None:
                totals[k] += float(v)
                counts[k] += 1
    return {k: (totals[k] / counts[k]) if counts[k] > 0 else 0.0 for k in keys}


def _regress_to_mean(stats_dict: dict, shrink: float = SHRINK) -> dict:
    """Blend prior-season stats toward league mean.
    shrink=0.5 → 50% prior signal + 50% league mean. Handles portal
    turnover + coaching changes without discarding prior-year signal.
    """
    if not stats_dict: return {}
    mean = _league_mean_stats(stats_dict)
    keys = ('sp_overall', 'off_epa_per_play', 'def_epa_per_play',
            'off_success_rate', 'off_explosiveness')
    out = {}
    for team, row in stats_dict.items():
        new = dict(row)
        for k in keys:
            v = row.get(k)
            if v is not None:
                new[k] = (1 - shrink) * float(v) + shrink * mean.get(k, 0.0)
        out[team] = new
    return out


def load_team_stats_with_fallback(current_season: int) -> tuple:
    """Return (stats_dict, source_label). Falls back to prior-season
    regressed-to-mean when current-year sample is too thin (Aug-Sep).
    """
    current = load_team_stats(current_season)
    if _avg_games(current) >= MIN_GAMES_PER_TEAM_AVG:
        return current, 'current'
    prior = load_team_stats(current_season - 1)
    if _avg_games(prior) >= MIN_GAMES_PER_TEAM_AVG:
        return _regress_to_mean(prior, shrink=SHRINK), 'prior_season_regressed'
    return current or prior, 'none'


def compute_projections(home_stats: dict, away_stats: dict,
                        neutral_site: bool = False) -> dict:
    """EPA/SP+ based projected spread + total."""
    out = {
        'home_off_epa_pp': None, 'away_off_epa_pp': None,
        'home_def_epa_pp': None, 'away_def_epa_pp': None,
        'home_sp_overall': None, 'away_sp_overall': None,
        'sp_gap': None,
        'projected_spread': None, 'projected_total': None,
        'model_pred_home_points': None, 'model_pred_away_points': None,
    }
    h_sp = _f(home_stats.get('sp_overall'))
    a_sp = _f(away_stats.get('sp_overall'))
    h_off_epa = _f(home_stats.get('off_epa_per_play'))
    a_off_epa = _f(away_stats.get('off_epa_per_play'))
    h_def_epa = _f(home_stats.get('def_epa_per_play'))
    a_def_epa = _f(away_stats.get('def_epa_per_play'))
    out['home_off_epa_pp'] = h_off_epa
    out['away_off_epa_pp'] = a_off_epa
    out['home_def_epa_pp'] = h_def_epa
    out['away_def_epa_pp'] = a_def_epa
    out['home_sp_overall'] = h_sp
    out['away_sp_overall'] = a_sp

    hfa = 0 if neutral_site else HOME_FIELD_PTS
    # Prefer SP+ when both teams have it
    if h_sp is not None and a_sp is not None:
        sp_gap = h_sp - a_sp
        out['sp_gap'] = round(sp_gap, 2)
        projected_spread = round(sp_gap * K_PTS_SP + hfa, 2)
    elif h_off_epa is not None and a_off_epa is not None:
        # EPA fallback: net_epa = off_epa - def_epa (higher = better team)
        h_net = h_off_epa - (h_def_epa or 0)
        a_net = a_off_epa - (a_def_epa or 0)
        projected_spread = round((h_net - a_net) * K_PTS_EPA + hfa, 2)
    else:
        return out
    out['projected_spread'] = projected_spread

    # Total = base + team pace/scoring adjustments (v1: static base)
    total = BASE_TOTAL
    # Split via power_diff (favorite gets ~54/46 in CFB)
    if projected_spread >= 0:
        home_share = 0.50 + min(0.10, projected_spread * 0.008)
    else:
        home_share = 0.50 + max(-0.10, projected_spread * 0.008)
    out['projected_total'] = round(total, 2)
    out['model_pred_home_points'] = round(total * home_share, 1)
    out['model_pred_away_points'] = round(total * (1 - home_share), 1)
    return out


def compute_confluence(home: dict, away: dict) -> tuple:
    breakdown = {}
    # SP+ overall
    h_sp = _f(home.get('sp_overall')); a_sp = _f(away.get('sp_overall'))
    if h_sp is not None and a_sp is not None:
        if h_sp - a_sp >= 3: breakdown['sp_plus'] = 'home'
        elif a_sp - h_sp >= 3: breakdown['sp_plus'] = 'away'
    # Off EPA per play
    h_off = _f(home.get('off_epa_per_play')); a_off = _f(away.get('off_epa_per_play'))
    if h_off is not None and a_off is not None:
        if h_off - a_off >= 0.10: breakdown['off_epa'] = 'home'
        elif a_off - h_off >= 0.10: breakdown['off_epa'] = 'away'
    # Def EPA (lower = better defense)
    h_def = _f(home.get('def_epa_per_play')); a_def = _f(away.get('def_epa_per_play'))
    if h_def is not None and a_def is not None:
        if a_def - h_def >= 0.10: breakdown['def_epa'] = 'home'  # home has better def
        elif h_def - a_def >= 0.10: breakdown['def_epa'] = 'away'
    # Success rate
    h_sr = _f(home.get('off_success_rate')); a_sr = _f(away.get('off_success_rate'))
    if h_sr is not None and a_sr is not None:
        if h_sr - a_sr >= 0.03: breakdown['success_rate'] = 'home'
        elif a_sr - h_sr >= 0.03: breakdown['success_rate'] = 'away'
    # Explosiveness
    h_ex = _f(home.get('off_explosiveness')); a_ex = _f(away.get('off_explosiveness'))
    if h_ex is not None and a_ex is not None:
        if h_ex - a_ex >= 0.10: breakdown['explosiveness'] = 'home'
        elif a_ex - h_ex >= 0.10: breakdown['explosiveness'] = 'away'
    # HFA default
    breakdown['hfa'] = 'home'
    h = sum(1 for v in breakdown.values() if v == 'home')
    a = sum(1 for v in breakdown.values() if v == 'away')
    return h - a, breakdown


def sweat_tier(score):
    if score >= 80: return 'PRIME'
    if score >= 65: return 'STRONG'
    if score >= 50: return 'LIGHT_LEAN'
    return 'PASS'


def compute_sweat_score(proj_spread, close_spread, conf_net, proj_total, close_total):
    score = 45
    if proj_spread is not None and close_spread is not None:
        edge = abs(proj_spread - close_spread)
        if edge >= 7:   score += 25
        elif edge >= 5: score += 18
        elif edge >= 3: score += 12
        elif edge >= 1.5: score += 6
    if conf_net is not None:
        a = abs(conf_net)
        if a >= 4: score += 15
        elif a >= 3: score += 10
        elif a >= 2: score += 5
    if proj_total is not None and close_total is not None:
        te = abs(proj_total - close_total)
        if te >= 7: score += 8
        elif te >= 4: score += 5
        elif te >= 2: score += 3
    return min(100, max(0, score))


def compute_primary_play(ctx):
    """NCAAF primary_play. Early-season discipline (Aug-Sep):
    when stats_source='prior_season_regressed', all tiers cap at LEAN.
    Cohort-based plays exempt (none audit-validated yet for NCAAF —
    see project_ncaaf_phase1_audit_baselines for future additions).
    """
    stats_source = ctx.get('stats_source') or 'current'
    stats_stale = stats_source != 'current'

    conf = ctx.get('signal_confluence_net') or 0
    proj_spread = ctx.get('projected_spread')
    close_spread = ctx.get('close_spread')
    home_team = ctx.get('home_team') or 'Home'
    away_team = ctx.get('away_team') or 'Away'
    proj_total = ctx.get('projected_total')
    close_total = ctx.get('close_total')

    spread_edge = None
    if proj_spread is not None and close_spread is not None:
        spread_edge = round(float(proj_spread) - float(close_spread), 2)
    abs_edge = abs(spread_edge) if spread_edge is not None else 0.0
    fav = home_team if (proj_spread is not None and float(proj_spread) > 0) else away_team

    total_edge = None
    if proj_total is not None and close_total is not None:
        total_edge = round(float(proj_total) - float(close_total), 2)

    stale_note = ' · prior-season regressed, LEAN cap' if stats_stale else ''

    # PRIME spread — big edge + confluence agreement
    if abs_edge >= 6.0 and abs(conf) >= 3:
        tier = 'LEAN' if stats_stale else 'PRIME'
        floor = 60 if stats_stale else 85
        return {'type': 'spread', 'tier': tier,
                'label': f'{fav} spread {"lean" if stats_stale else "cover"}',
                'sub': f'Model {proj_spread:+.1f} vs market {close_spread:+.1f} (edge {abs_edge:.1f}, conf {conf:+d}){stale_note}',
                'signal_floor': floor}
    # STRONG spread — meaningful edge
    if abs_edge >= 4.0 and abs(conf) >= 2:
        tier = 'LEAN' if stats_stale else 'STRONG'
        floor = 58 if stats_stale else 72
        return {'type': 'spread', 'tier': tier,
                'label': f'{fav} spread {"lean" if stats_stale else "cover"}',
                'sub': f'Model {proj_spread:+.1f} vs market {close_spread:+.1f} (edge {abs_edge:.1f}){stale_note}',
                'signal_floor': floor}
    # STRONG total
    if total_edge is not None and abs(total_edge) >= 5.0:
        side = 'Over' if total_edge > 0 else 'Under'
        tier = 'LEAN' if stats_stale else 'STRONG'
        floor = 58 if stats_stale else 70
        return {'type': 'total', 'tier': tier,
                'label': f'{side} {close_total}',
                'sub': f'Model projects {proj_total:.1f} vs market {close_total} ({total_edge:+.1f}){stale_note}',
                'signal_floor': floor}
    # LIGHT spread — cap at LEAN when stale (same rationale as NFL:
    # a weaker signal shouldn't sneak into lock_of_week when the
    # stronger STRONG-tier signal on the same data got capped out)
    if abs_edge >= 3.0:
        tier = 'LEAN' if stats_stale else 'LIGHT'
        return {'type': 'spread', 'tier': tier,
                'label': f'{fav} spread lean',
                'sub': f'Edge {abs_edge:.1f}{stale_note}',
                'signal_floor': 60}
    return None


def build_context_row(g: dict, team_stats: dict, stats_source: str = 'current') -> Optional[dict]:
    home = g.get('home_team'); away = g.get('away_team')
    if not home or not away:
        return None
    home_stats = team_stats.get(home) or {}
    away_stats = team_stats.get(away) or {}

    proj = compute_projections(home_stats, away_stats,
                               neutral_site=bool(g.get('neutral_site')))
    conf_net, breakdown = compute_confluence(home_stats, away_stats)

    row = {
        'game_id': g['game_id'],
        'game_date': g['game_date'],
        'season': g.get('season'),
        'season_type': g.get('season_type') or 'regular',
        'week': g.get('week'),
        'home_team': home,
        'away_team': away,
        'kickoff_utc': g.get('kickoff_utc'),
        'close_spread': g.get('close_spread'),
        'open_spread': g.get('open_spread'),
        'close_total': g.get('close_total'),
        'open_total': g.get('open_total'),
        'close_home_ml': g.get('close_home_ml'),
        'close_away_ml': g.get('close_away_ml'),
        'neutral_site': g.get('neutral_site'),
        'conference_game': g.get('conference_game'),
        'stats_source': stats_source,
        **proj,
        'signal_confluence_net': conf_net,
        'signal_confluence_breakdown': breakdown,
    }
    score = compute_sweat_score(
        row.get('projected_spread'), row.get('close_spread'), conf_net,
        row.get('projected_total'), row.get('close_total'),
    )
    row['sweat_score'] = score
    row['sweat_tier'] = sweat_tier(score)
    row['primary_play'] = compute_primary_play(row)
    return row


def upsert(rows: list, dry_run: bool = False) -> int:
    if not rows: return 0
    if dry_run:
        for r in rows:
            pp = r.get('primary_play') or {}
            print(f"  [DRY] {r['game_id']}  {r['away_team']} @ {r['home_team']}  "
                  f"sp={r.get('close_spread')} proj={r.get('projected_spread')}  "
                  f"conf={r.get('signal_confluence_net'):+d}  ss={r['sweat_score']} {r['sweat_tier']}"
                  + (f"  → {pp.get('tier')} {pp.get('label')}" if pp else ''))
        return len(rows)
    r = requests.post(
        f'{SB}/rest/v1/ncaaf_game_context?on_conflict=game_id',
        headers=H_WRITE, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(rows)


def run(dry_run: bool = False) -> None:
    print(f'=== NCAAF game context · {_et_now().date()} ===')
    games = load_upcoming()
    print(f'  upcoming games (10d): {len(games)}')
    if not games:
        return
    season = games[0].get('season') or 2026
    team_stats, stats_source = load_team_stats_with_fallback(season)
    if stats_source == 'prior_season_regressed':
        print(f'  ⚠ current season {season} thin — falling back to {season-1} regressed to mean (LEAN cap on non-cohort plays)')
    elif stats_source == 'none':
        print(f'  ⚠ neither {season} nor {season-1} has usable team stats — cohort/market signal only')
    print(f'  team_stats: {len(team_stats)} teams  source={stats_source}')

    rows = [build_context_row(g, team_stats, stats_source=stats_source) for g in games]
    rows = [r for r in rows if r]
    written = upsert(rows, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}wrote {written} rows to ncaaf_game_context')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
