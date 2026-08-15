"""MLB advanced metrics enrichment (2026-08-15).

5-part refinement pack per project_yday_814 discussion:
  1. Third-Time-Through-The-Order (TTTO) penalty on totals
  2. Catcher framing → K prop bonus
  3. BABIP regression flag (L14 team BABIP > 0.320 or < 0.280)
  4. Bullpen availability score (leverage relievers rest-adjusted)
  5. SIERA (Skill-Interactive ERA) alongside xERA

Runs AFTER game_context.py + bullpen_stats.py so the base fields
(catcher_framing, bp_relievers_3d, home_pitcher_last_3_k_pct, etc.)
are populated. Reads mlb_game_context rows, computes the derived
metrics, patches back to same rows.

Designed as ADDITIVE — nothing here changes existing projection or
scoring behavior. Downstream consumers (play_of_day, generate_props,
compute_primary_play) can optionally read these fields to refine
projections; leaving them off = no behavior change.

CLI
  python mlb_advanced_metrics.py                    # today's slate
  python mlb_advanced_metrics.py --date 2026-08-15
  python mlb_advanced_metrics.py --dry-run          # print, don't write
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}


# ─── #1 · TTTO PENALTY ────────────────────────────────────────────
#
# Third-Time-Through-The-Order effect (well-documented in modern MLB):
#   1st TTO: wOBA against ~.290 (baseline)
#   2nd TTO: wOBA against ~.310 (+20pp)
#   3rd TTO: wOBA against ~.340 (+50pp)
#
# A batter faced in TTTO produces ~0.05 more runs than a 1st-time PA.
# If a starter throws 6 IP (~24 batters), that's ~5-6 batters in 3rd TTO,
# adding ~0.3 runs to game total on top of the "base" run rate.
#
# We model: expected_starter_ip → expected TTTO exposure → penalty runs.

def compute_expected_starter_ip(l3_ip: Optional[float], season_avg_ip: Optional[float]) -> Optional[float]:
    """Blend recent form (L3) with season avg. Fallback to whichever is available."""
    if l3_ip is not None and season_avg_ip is not None:
        # 60/40 recent/season weight — recent form dominates for role changes
        return round(0.6 * float(l3_ip) + 0.4 * float(season_avg_ip), 2)
    return float(l3_ip) if l3_ip is not None else (float(season_avg_ip) if season_avg_ip is not None else None)


def compute_ttto_penalty(expected_ip_home: Optional[float],
                          expected_ip_away: Optional[float]) -> Optional[float]:
    """Total runs added to projection due to expected TTTO exposure."""
    def _pen(ip):
        if ip is None: return 0.0
        # Batters faced ≈ IP × 4.2 (accounts for baserunners)
        bf = ip * 4.2
        # 3rd TTO starts around batter 19
        ttto_bf = max(0, bf - 18)
        # Each TTTO batter ~0.05 runs added (from wOBA .050 lift × ~1 R per wOBA point cluster)
        return ttto_bf * 0.05
    return round(_pen(expected_ip_home) + _pen(expected_ip_away), 2)


# ─── #2 · CATCHER FRAMING → K PROP BONUS ──────────────────────────
#
# Elite framers add ~5-8 called strikes per game vs replacement-level.
# Each called strike ~ 0.15 K conversion (2-strike counts convert more).
# Framing_runs is on a season scale — we normalize to per-game bonus.

def compute_framing_k_bonus(framing_runs: Optional[float]) -> Optional[float]:
    """Return expected additional Ks per starter behind a framer.

    Rough scaling: framing_runs of +15 (elite, top-5 catcher) → +0.8 K/game
    for the starter. Below-avg framer -5 → -0.3 K/game."""
    if framing_runs is None: return None
    try:
        fr = float(framing_runs)
    except (TypeError, ValueError):
        return None
    # Linear scaling: 1 framing run per season ≈ 0.05 K per game
    # (assumes ~150 games caught, framing runs distributed evenly)
    return round(fr * 0.05, 2)


# ─── #3 · BABIP REGRESSION FLAG ───────────────────────────────────
#
# Teams with L14 BABIP > 0.320 are due to regress DOWN (offense cools).
# Teams with L14 BABIP < 0.280 are due to regress UP (offense heats up).
# League average BABIP is stable ~0.297.
#
# Cohort tag: 'hot' (regression risk, fade the offense) or 'cold'
# (rebound risk, fade the pitcher on those teams).

BABIP_HOT_THRESHOLD = 0.320
BABIP_COLD_THRESHOLD = 0.280


def compute_team_babip_l14(team_name: str) -> Optional[float]:
    """Compute L14 team BABIP from mlb_team_offense (or gameLog if avail).

    BABIP = (H - HR) / (AB - K - HR + SF)
    """
    # Try mlb_team_offense first — has season aggregates but not L14 typically
    # Fallback: pull team gameLog last 14 days
    try:
        # Get team_id via mlb API
        r = requests.get('https://statsapi.mlb.com/api/v1/teams',
                         params={'sportId': 1}, timeout=10)
        teams = r.json().get('teams', [])
        team_last = team_name.split()[-1].lower()
        mlb_team = None
        for t in teams:
            n = t.get('name', '').lower()
            if n.endswith(team_last) or team_last in n:
                mlb_team = t; break
        if not mlb_team: return None
        tid = mlb_team['id']
        # Team hitting gameLog current season
        r2 = requests.get(f'https://statsapi.mlb.com/api/v1/teams/{tid}/stats',
                          params={'stats': 'gameLog', 'group': 'hitting',
                                  'season': datetime.now().year},
                          timeout=10)
        splits = r2.json().get('stats', [])
        if not splits or not splits[0].get('splits'): return None
        games = splits[0]['splits']
        games.sort(key=lambda g: g.get('date', ''), reverse=True)
        recent = games[:14]
        if len(recent) < 5: return None  # need meaningful sample
        totals = {'h': 0, 'ab': 0, 'k': 0, 'hr': 0, 'sf': 0}
        for g in recent:
            s = g.get('stat', {})
            totals['h']  += int(s.get('hits', 0) or 0)
            totals['ab'] += int(s.get('atBats', 0) or 0)
            totals['k']  += int(s.get('strikeOuts', 0) or 0)
            totals['hr'] += int(s.get('homeRuns', 0) or 0)
            totals['sf'] += int(s.get('sacFlies', 0) or 0)
        denom = totals['ab'] - totals['k'] - totals['hr'] + totals['sf']
        if denom <= 0: return None
        babip = (totals['h'] - totals['hr']) / denom
        return round(babip, 3)
    except Exception:
        return None


def classify_babip_flag(home_babip: Optional[float], away_babip: Optional[float]) -> str:
    """Return 'hot', 'cold', 'mixed', or 'neutral' per team L14 BABIP."""
    flags = []
    for b in (home_babip, away_babip):
        if b is None: continue
        if b > BABIP_HOT_THRESHOLD: flags.append('hot')
        elif b < BABIP_COLD_THRESHOLD: flags.append('cold')
    if not flags: return 'neutral'
    if 'hot' in flags and 'cold' in flags: return 'mixed'
    return flags[0]


# ─── #4 · BULLPEN AVAILABILITY ────────────────────────────────────
#
# Season BP ERA is a lagging aggregate. What matters TONIGHT is
# which leverage relievers are RESTED.
#
# We have `home_bp_relievers_3d` / `away_bp_relievers_3d` fields —
# these count relievers who have thrown in last 3 days.
#
# Higher count = MORE taxed bullpen = LESS quality expected.
# Availability score: 100 - (relievers_3d × 10), floored at 0.
#
# Effective ERA: shift toward league-avg BP ERA (4.20) as availability
# drops (taxed bullpen = mop-up guys pitching = closer to league avg).

LEAGUE_AVG_BP_ERA = 4.20


def compute_bullpen_availability(relievers_3d: Optional[int]) -> Optional[float]:
    """0-100 score. Fewer taxed relievers = higher availability."""
    if relievers_3d is None: return None
    try:
        n = int(relievers_3d)
    except (TypeError, ValueError):
        return None
    # 0 relievers used = 100 avail; 5+ = 50 avail; 8+ = 20 avail
    score = max(0, min(100, 100 - n * 10))
    return float(score)


def compute_effective_bp_era(bp_era: Optional[float],
                               availability: Optional[float]) -> Optional[float]:
    """Weighted blend of team BP ERA and league average based on availability.
    availability=100 → team ERA (full trust)
    availability=0 → league avg (all leverage guys unavailable)
    """
    if bp_era is None or availability is None: return None
    try:
        era = float(bp_era); w = float(availability) / 100.0
    except (TypeError, ValueError):
        return None
    return round(w * era + (1 - w) * LEAGUE_AVG_BP_ERA, 2)


# ─── #5 · SIERA ────────────────────────────────────────────────────
#
# Skill-Interactive ERA formula (from FanGraphs). Uses K%, BB%, GB%.
# Generally 3-5% more accurate at predicting future ERA than xERA.
#
# Formula:
#   SIERA = 6.145
#         - 16.986 * (K/PA)
#         + 11.434 * (BB/PA)
#         - 1.858  * (GB%)
#         + 7.653  * (K/PA)^2
#         - if (GB% - FB% - PU%) < 0: 6.664 * (GB% - FB% - PU%)^2
#           else: -6.664 * (GB% - FB% - PU%)^2
#         + 10.130 * (K/PA) * (GB% - FB% - PU%)
#
# For simplicity + given we may not have full pitch-type breakdown,
# we compute a simplified SIERA using just K%, BB%, GB% where
# GB% is estimated from pitcher's ground_out ratio.

def compute_siera(k_pct: Optional[float], bb_pct: Optional[float],
                   gb_pct: Optional[float] = None) -> Optional[float]:
    """Simplified SIERA. Percentages should be as decimals (0.25 not 25.0).

    If GB% is unavailable, uses league-avg 0.43 as fallback."""
    if k_pct is None or bb_pct is None: return None
    try:
        k = float(k_pct); bb = float(bb_pct)
        gb = float(gb_pct) if gb_pct is not None else 0.43  # league avg
    except (TypeError, ValueError):
        return None
    # Normalize: if given as percentage (25.5), convert to decimal
    if k > 1.0: k /= 100.0
    if bb > 1.0: bb /= 100.0
    if gb > 1.0: gb /= 100.0
    # Simplified formula (drop the GB-FB-PU interaction terms for now)
    siera = (6.145
             - 16.986 * k
             + 11.434 * bb
             - 1.858 * gb
             + 7.653 * (k ** 2))
    # Floor at 0.50 — negative SIERA is nonsensical (can't have neg ERA).
    # Happens when simplified formula over-extrapolates on very high K%
    # without the GB-FB-PU interaction terms. Cap at reasonable elite floor.
    siera = max(0.50, siera)
    return round(siera, 2)


# ─── ORCHESTRATOR ──────────────────────────────────────────────────

def enrich_row(row: dict) -> dict:
    """Compute all 5 metrics for one game_context row. Returns patch payload."""
    patch = {}

    # #1 TTTO
    # Use L3 IP if available (home_pitcher_last_ip / away_pitcher_last_ip
    # are single-outing; need to average L3). For MVP, use last_ip as proxy.
    home_l3_ip = row.get('home_pitcher_last_3_era')  # if we track L3 IP explicitly
    away_l3_ip = row.get('away_pitcher_last_3_era')
    # Fallback: use `last_ip` from most recent start (single game)
    home_last_ip = row.get('home_last_ip')
    away_last_ip = row.get('away_last_ip')
    # Approximate expected IP: L5 avg or single last outing
    # Since we don't have direct L3 IP field, use last_ip as proxy for now
    exp_ip_home = float(home_last_ip) if home_last_ip is not None else None
    exp_ip_away = float(away_last_ip) if away_last_ip is not None else None
    if exp_ip_home is not None:
        patch['expected_starter_ip_home'] = round(exp_ip_home, 1)
    if exp_ip_away is not None:
        patch['expected_starter_ip_away'] = round(exp_ip_away, 1)
    ttto_pen = compute_ttto_penalty(exp_ip_home, exp_ip_away)
    if ttto_pen is not None:
        patch['ttto_penalty_runs'] = ttto_pen

    # #2 Catcher framing → K bonus
    h_frame = row.get('home_catcher_framing')
    a_frame = row.get('away_catcher_framing')
    if h_frame is not None:
        b = compute_framing_k_bonus(h_frame)
        if b is not None: patch['home_framing_k_bonus'] = b
    if a_frame is not None:
        b = compute_framing_k_bonus(a_frame)
        if b is not None: patch['away_framing_k_bonus'] = b

    # #3 BABIP regression flag
    home_team = row.get('home_team')
    away_team = row.get('away_team')
    home_babip = compute_team_babip_l14(home_team) if home_team else None
    away_babip = compute_team_babip_l14(away_team) if away_team else None
    if home_babip is not None:
        patch['home_team_babip_l14'] = home_babip
    if away_babip is not None:
        patch['away_team_babip_l14'] = away_babip
    flag = classify_babip_flag(home_babip, away_babip)
    if flag != 'neutral' or (home_babip is not None or away_babip is not None):
        patch['babip_regression_flag'] = flag

    # #4 Bullpen availability
    h_bp3d = row.get('home_bp_relievers_3d')
    a_bp3d = row.get('away_bp_relievers_3d')
    h_bp_era = row.get('home_bullpen_era')
    a_bp_era = row.get('away_bullpen_era')
    h_avail = compute_bullpen_availability(h_bp3d)
    a_avail = compute_bullpen_availability(a_bp3d)
    if h_avail is not None:
        patch['home_bullpen_availability'] = h_avail
        eff = compute_effective_bp_era(h_bp_era, h_avail)
        if eff is not None: patch['home_bullpen_effective_era'] = eff
    if a_avail is not None:
        patch['away_bullpen_availability'] = a_avail
        eff = compute_effective_bp_era(a_bp_era, a_avail)
        if eff is not None: patch['away_bullpen_effective_era'] = eff

    # #5 SIERA
    # Uses last_3 K% + BB% (proxy for talent). Some fields may be null.
    h_k = row.get('home_pitcher_last_3_k_pct')
    a_k = row.get('away_pitcher_last_3_k_pct')
    # BB% not directly in schema — approximate from projected_bb / projected_outs
    h_bb_proj = row.get('home_pitcher_projected_bb')
    h_out_proj = row.get('home_pitcher_projected_outs')
    a_bb_proj = row.get('away_pitcher_projected_bb')
    a_out_proj = row.get('away_pitcher_projected_outs')
    def _bb_pct(bb, outs):
        if bb is None or outs is None or outs <= 0: return None
        # BB per BF ≈ BB / (outs/3 × 4.2 BF/IP)
        bf = (outs / 3) * 4.2
        return round(bb / bf, 3) if bf > 0 else None
    h_bb_pct = _bb_pct(h_bb_proj, h_out_proj)
    a_bb_pct = _bb_pct(a_bb_proj, a_out_proj)
    if h_k is not None and h_bb_pct is not None:
        s = compute_siera(h_k, h_bb_pct)
        if s is not None: patch['home_sp_siera'] = s
    if a_k is not None and a_bb_pct is not None:
        s = compute_siera(a_k, a_bb_pct)
        if s is not None: patch['away_sp_siera'] = s

    return patch


def run(game_date: Optional[str] = None, dry_run: bool = False) -> int:
    if game_date is None:
        game_date = (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()
    print(f'=== mlb_advanced_metrics · {game_date} ===')

    r = requests.get(
        f'{SB}/rest/v1/mlb_game_context'
        f'?select=game_id,home_team,away_team,'
        f'home_catcher_framing,away_catcher_framing,'
        f'home_bp_relievers_3d,away_bp_relievers_3d,'
        f'home_bullpen_era,away_bullpen_era,'
        f'home_pitcher_last_3_k_pct,away_pitcher_last_3_k_pct,'
        f'home_pitcher_last_3_era,away_pitcher_last_3_era,'
        f'home_last_ip,away_last_ip,'
        f'home_pitcher_projected_bb,away_pitcher_projected_bb,'
        f'home_pitcher_projected_outs,away_pitcher_projected_outs'
        f'&game_date=eq.{game_date}',
        headers=H_READ, timeout=30)
    if r.status_code != 200:
        print(f'  ✗ fetch failed: {r.status_code} {r.text[:200]}')
        return 0
    rows = r.json() or []
    print(f'  games: {len(rows)}')

    written = 0
    for row in rows:
        if not isinstance(row, dict): continue
        gid = row.get('game_id')
        patch = enrich_row(row)
        if not patch:
            print(f'  skip {row.get("away_team","?")}@{row.get("home_team","?")}: no fields to compute')
            continue

        if dry_run:
            print(f'  [DRY] {row.get("away_team","?")[:14]}@{row.get("home_team","?")[:14]}: {patch}')
            written += 1
            continue

        pr = requests.patch(
            f'{SB}/rest/v1/mlb_game_context?game_id=eq.{gid}',
            headers=H_WRITE, json=patch, timeout=15)
        if pr.status_code in (200, 204):
            written += 1
        else:
            print(f'  ✗ patch {gid}: {pr.status_code} {pr.text[:150]}')

    print(f'  ✓ {written}/{len(rows)} rows enriched')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD; defaults to today ET')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(args.date, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
