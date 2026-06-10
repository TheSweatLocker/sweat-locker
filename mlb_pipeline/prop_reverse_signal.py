"""
prop_reverse_signal.py — Reverse-aggregate player prop tiers into game-level votes.

The prop pipeline already does lineup-level matchup analysis (batter L7 form vs
opposing SP K rate, etc.) and surfaces PRIME/STRONG/LEAN/LIGHT_LEAN tiers per
player-prop. That's exactly the lineup-level granularity the cohort engine
misses at game level.

This module reverse-aggregates those signals into game-level votes:
  - Total OVER / UNDER lean
  - Side (home / away) lean
  - Confidence (LOW / MEDIUM / HIGH based on signal concentration)

Tier weights:
  PRIME       = 3 points
  STRONG      = 2 points
  LEAN        = 1 point
  LIGHT_LEAN  = 0.5 points
  (SKIP / PASS / no tier → 0)

Prop type → game-level vote mapping:
  Hitting (batter is on a team):
    hits_over / total_bases_over / runs_over / rbi_over → OVER + that team's offense
    hits_under                                          → UNDER + fade that team's offense
    hr_over                                             → OVER (HR-heavy implies game total up)

  Pitching (pitcher is on a team — counts as opposing team's offensive signal):
    ks_over    → UNDER (pitcher dominant) + favor that team's ML/RL
    ks_under   → OVER  (pitcher gets hit) + opposing team's offense + ML/RL
    er_over    → OVER  (pitcher allows runs) + opposing team's offense
    er_under   → UNDER (pitcher suppresses)  + favor that team's ML/RL
    bb_over    → OVER  (control issues, more baserunners)
    bb_under   → UNDER (clean SP)
    ha_over    → OVER  (hits allowed up = traffic)
    ha_under   → UNDER (limited contact)
    outs_over  → UNDER (going deep = bullpen protected)
    outs_under → OVER  (early exit = bullpen exposed)

Voting:
  total_vote = (over_points - under_points) / max(over_points + under_points, 1)
  side_vote  = (home_points - away_points) / max(home_points + away_points, 1)

  Normalized to [-1, +1]. POTD selector / Jerry reads / sweat_dim drivers
  consume this signal as an INDEPENDENT vote (no overlap with cohort or model
  votes, since the underlying inputs are lineup-level not game-level).
"""
import os
import requests
from collections import defaultdict
from typing import Dict, List, Optional


TIER_WEIGHT = {
    'PRIME': 3.0,
    'STRONG': 2.0,
    'LEAN': 1.0,
    'LIGHT_LEAN': 0.5,
}

# Map each (prop_type, direction) to (total_vote_direction, side_vote_direction)
# total_vote_direction: 'OVER', 'UNDER', or None
# side_vote_direction: 'BATTER_TEAM_OFFENSE', 'PITCHER_TEAM_ML', or None
#   BATTER_TEAM_OFFENSE = the team the prop's player is on, voting they score
#   PITCHER_TEAM_ML    = the team the pitcher is on, voting they cover/win
PROP_TYPE_MAP = {
    # Batter props
    ('hits_over', 'over'):        ('OVER', 'BATTER_TEAM_OFFENSE'),
    ('hits_under', 'under'):      ('UNDER', None),
    ('total_bases_over', 'over'): ('OVER', 'BATTER_TEAM_OFFENSE'),
    ('runs_over', 'over'):        ('OVER', 'BATTER_TEAM_OFFENSE'),
    ('rbi_over', 'over'):         ('OVER', 'BATTER_TEAM_OFFENSE'),
    ('hr_over', 'over'):          ('OVER', 'BATTER_TEAM_OFFENSE'),

    # Pitcher props — pitcher_team is who PITCHES; the "vote for ML"
    # implies that pitcher's team is favored to cover/win.
    ('ks_over', 'over'):          ('UNDER', 'PITCHER_TEAM_ML'),
    ('ks_under', 'under'):        ('OVER',  None),
    ('er_over', 'over'):          ('OVER',  None),
    ('er_under', 'under'):        ('UNDER', 'PITCHER_TEAM_ML'),
    ('bb_over', 'over'):          ('OVER',  None),
    ('bb_under', 'under'):        ('UNDER', 'PITCHER_TEAM_ML'),
    ('ha_over', 'over'):          ('OVER',  None),
    ('ha_under', 'under'):        ('UNDER', 'PITCHER_TEAM_ML'),
    ('outs_over', 'over'):        ('UNDER', 'PITCHER_TEAM_ML'),
    ('outs_under', 'under'):      ('OVER',  None),
}


def _tier_weight(prop) -> float:
    """Return tier weight, or 0 if SKIP/PASS/unknown."""
    tier = (prop.get('tier') or '').upper()
    return TIER_WEIGHT.get(tier, 0.0)


def _player_is_pitcher(prop) -> bool:
    """Pitcher props use SP types (ks/er/bb/ha/outs); batter props use batting stats."""
    pt = (prop.get('prop_type') or '').lower()
    return any(pt.startswith(p) for p in ('ks_', 'er_', 'bb_', 'ha_', 'outs_'))


def compute_game_signal(props: List[Dict], home_team: str, away_team: str) -> Dict:
    """Aggregate a list of props for a single game into a directional vote.

    Returns dict with:
        total_signal:  float in [-1, +1] (negative = UNDER lean, positive = OVER lean)
        side_signal:   float in [-1, +1] (negative = AWAY lean, positive = HOME lean)
        confidence:    'NONE' | 'LOW' | 'MEDIUM' | 'HIGH' (based on point mass)
        over_pts, under_pts, home_pts, away_pts
        evidence:      list of (player, prop_type, direction, tier, weight)
    """
    over_pts = 0.0
    under_pts = 0.0
    home_pts = 0.0
    away_pts = 0.0
    evidence = []

    for prop in props:
        wt = _tier_weight(prop)
        if wt == 0:
            continue
        prop_type = (prop.get('prop_type') or '').lower()
        direction = (prop.get('direction') or '').lower()
        mapping = PROP_TYPE_MAP.get((prop_type, direction))
        if not mapping:
            continue
        total_dir, side_dir = mapping

        if total_dir == 'OVER':
            over_pts += wt
        elif total_dir == 'UNDER':
            under_pts += wt

        # Side mapping — determine which team
        player_team = prop.get('player_team') or ''
        is_home = home_team and player_team.lower() in home_team.lower() or \
                  home_team and home_team.lower() in player_team.lower()
        is_away = away_team and player_team.lower() in away_team.lower() or \
                  away_team and away_team.lower() in player_team.lower()

        if side_dir == 'BATTER_TEAM_OFFENSE':
            # Batter's team scoring → side vote for batter's team
            if is_home:
                home_pts += wt
            elif is_away:
                away_pts += wt
        elif side_dir == 'PITCHER_TEAM_ML':
            # Pitcher's team is the side favored
            if is_home:
                home_pts += wt
            elif is_away:
                away_pts += wt

        evidence.append({
            'player': prop.get('player_name'),
            'prop_type': prop_type,
            'direction': direction,
            'tier': prop.get('tier'),
            'weight': wt,
        })

    total_mass = over_pts + under_pts
    side_mass = home_pts + away_pts

    total_signal = (over_pts - under_pts) / total_mass if total_mass > 0 else 0
    side_signal = (home_pts - away_pts) / side_mass if side_mass > 0 else 0

    # Confidence is based on absolute point mass + signal strength
    max_mass = max(total_mass, side_mass)
    if max_mass >= 8 and (abs(total_signal) >= 0.5 or abs(side_signal) >= 0.5):
        confidence = 'HIGH'
    elif max_mass >= 4 and (abs(total_signal) >= 0.4 or abs(side_signal) >= 0.4):
        confidence = 'MEDIUM'
    elif max_mass >= 2:
        confidence = 'LOW'
    else:
        confidence = 'NONE'

    return {
        'total_signal': round(total_signal, 3),
        'side_signal': round(side_signal, 3),
        'confidence': confidence,
        'over_pts': round(over_pts, 1),
        'under_pts': round(under_pts, 1),
        'home_pts': round(home_pts, 1),
        'away_pts': round(away_pts, 1),
        'evidence_count': len(evidence),
        'evidence': evidence[:10],  # cap detail evidence at 10 props
    }


def compute_slate_signals(game_date: str, supabase_url: str, supabase_key: str) -> Dict[str, Dict]:
    """Fetch all props for a date and compute signals per game.

    Returns {matchup_string: signal_dict}.
    """
    headers = {'apikey': supabase_key, 'Authorization': f'Bearer {supabase_key}'}
    r = requests.get(
        f'{supabase_url}/rest/v1/mlb_pipeline_props',
        headers=headers,
        params={'select': '*', 'game_date': f'eq.{game_date}'},
    )
    props = r.json() if r.status_code == 200 else []

    # Group by matchup
    by_matchup = defaultdict(list)
    for p in props:
        by_matchup[p.get('matchup') or ''].append(p)

    out = {}
    for matchup, plist in by_matchup.items():
        if not matchup or ' @ ' not in matchup:
            continue
        away, home = matchup.split(' @ ', 1)
        out[matchup] = compute_game_signal(plist, home_team=home.strip(), away_team=away.strip())
    return out


if __name__ == '__main__':
    import sys
    from dotenv import load_dotenv
    load_dotenv()
    sys.stdout.reconfigure(encoding='utf-8')
    from datetime import datetime, timezone, timedelta
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    today = et_now.strftime('%Y-%m-%d')

    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_KEY')
    signals = compute_slate_signals(today, url, key)

    print(f'Prop Reverse Signals — {today}')
    print(f'{"GAME":<42}{"CONF":<9}{"TOT":<10}{"SIDE":<10}{"OVER":<8}{"UNDER":<8}{"HOME":<8}{"AWAY":<8}{"#"}')
    print('-' * 110)
    for matchup, sig in sorted(signals.items()):
        ts = f"{sig['total_signal']:+.2f}"
        ss = f"{sig['side_signal']:+.2f}"
        ts_dir = 'OVER' if sig['total_signal'] > 0.1 else ('UNDER' if sig['total_signal'] < -0.1 else '~')
        ss_dir = 'HOME' if sig['side_signal'] > 0.1 else ('AWAY' if sig['side_signal'] < -0.1 else '~')
        print(f"{matchup[:40]:<42}{sig['confidence']:<9}{ts}({ts_dir}){'':<3}{ss}({ss_dir}){'':<3}"
              f"{sig['over_pts']:<8}{sig['under_pts']:<8}{sig['home_pts']:<8}{sig['away_pts']:<8}{sig['evidence_count']}")
