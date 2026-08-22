"""Ship prop signals that read pitcher-vs-team history (2026-08-22).

Silent-bug audit finding: mlb_game_context populates
  home_pitcher_vs_team_era, _avg, _k_per_9, _ip
  away_pitcher_vs_team_era, _avg, _k_per_9, _ip
  home_pitcher_vs_team_recent_era, _baa, _n_starts (rolling window)

But NO prop signal reads them. Painter tonight had 5.3 IP / .111 BAA
against the Cards (his one prior start dominated), and the prop
ensemble ignored it entirely when grading er_over 2.5.

These signals close that blind spot for pitcher-outcome props (ks / ha /
bb / outs / er). Minimum 3 IP sample to avoid one-appearance flukes.

Run once: python _ship_pitcher_vs_team_prop_signals.py
Then verify: python verify_signal_wiring.py --sport MLB
"""
import os, requests
from pathlib import Path

_env = Path(__file__).parent / '.env'
for line in _env.read_text().split('\n'):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())
SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = {**H, 'Content-Type': 'application/json',
      'Prefer': 'resolution=merge-duplicates,return=minimal'}

# Helper: extract the pitcher-team ERA for whichever pitcher owns the prop.
# The prop's player_name is either the home or away starter. Match on that
# then pull the appropriate vs-team column.
_PITCHER_SIDE = (
    "(('home' if p.get('player_name') == ctx.home_pitcher "
    " else 'away' if p.get('player_name') == ctx.away_pitcher else None))"
)

# Only fire on pitcher props (prop_type family in ks/ha/bb/outs/er)
_IS_PITCHER_PROP = (
    "(p.get('prop_type','').split('_')[0] in ('ks','ha','bb','outs','er'))"
)

# ─── ERA-based signals ──────────────────────────────────────────────
# Pitcher dominated this team historically (ERA ≤ 2.75, IP ≥ 3):
#   For er_over/ha_over/outs_under → FADE (pitcher likely under lines)
#   For er_under/ha_under/outs_over/ks_over → BACK
_DOMINANT_COND = (
    f"{_IS_PITCHER_PROP} and "
    "((p.get('player_name') == ctx.home_pitcher "
    "  and ctx.home_pitcher_vs_team_era is not None "
    "  and ctx.home_pitcher_vs_team_ip is not None "
    "  and float(ctx.home_pitcher_vs_team_ip) >= 3 "
    "  and float(ctx.home_pitcher_vs_team_era) <= 2.75) "
    "or (p.get('player_name') == ctx.away_pitcher "
    "    and ctx.away_pitcher_vs_team_era is not None "
    "    and ctx.away_pitcher_vs_team_ip is not None "
    "    and float(ctx.away_pitcher_vs_team_ip) >= 3 "
    "    and float(ctx.away_pitcher_vs_team_era) <= 2.75))"
)
_DOMINANT_SIDE = (
    "'FADE' if p.get('prop_type','').endswith('over') and "
    "  p.get('prop_type','').split('_')[0] in ('er','ha') "
    "else 'BACK' if p.get('prop_type','').endswith('under') and "
    "  p.get('prop_type','').split('_')[0] in ('er','ha') "
    "else 'BACK' if p.get('prop_type','').endswith('over') and "
    "  p.get('prop_type','').split('_')[0] == 'ks' "
    "else 'BACK' if p.get('prop_type','').endswith('over') and "
    "  p.get('prop_type','').split('_')[0] == 'outs' "
    "else ''"
)

# Pitcher got tagged historically (ERA ≥ 5.50, IP ≥ 3):
#   For er_over/ha_over → BACK
#   For er_under/ha_under → FADE
_TAGGED_COND = (
    f"{_IS_PITCHER_PROP} and "
    "((p.get('player_name') == ctx.home_pitcher "
    "  and ctx.home_pitcher_vs_team_era is not None "
    "  and ctx.home_pitcher_vs_team_ip is not None "
    "  and float(ctx.home_pitcher_vs_team_ip) >= 3 "
    "  and float(ctx.home_pitcher_vs_team_era) >= 5.50) "
    "or (p.get('player_name') == ctx.away_pitcher "
    "    and ctx.away_pitcher_vs_team_era is not None "
    "    and ctx.away_pitcher_vs_team_ip is not None "
    "    and float(ctx.away_pitcher_vs_team_ip) >= 3 "
    "    and float(ctx.away_pitcher_vs_team_era) >= 5.50))"
)
_TAGGED_SIDE = (
    "'BACK' if p.get('prop_type','').endswith('over') and "
    "  p.get('prop_type','').split('_')[0] in ('er','ha') "
    "else 'FADE' if p.get('prop_type','').endswith('under') and "
    "  p.get('prop_type','').split('_')[0] in ('er','ha') "
    "else ''"
)

SIGNALS = [
    dict(signal_key='pitcher_vs_team_dominant_history', sport='MLB',
         **{'class': 'prop_matchup'}, subject_scope='prop', market_scope='*',
         condition_expr=_DOMINANT_COND, side_expr=_DOMINANT_SIDE,
         strength_expr='0.35',
         display_prose_template='pitcher has owned this lineup (vs-team ERA <=2.75, IP >=3)',
         description='Pitcher-vs-team history dominant. Prior audit finding — data existed on ctx but no reader. Small-sample threshold IP>=3.',
         enabled=True, origin='vs_team_prop_2026_08_22'),

    dict(signal_key='pitcher_vs_team_tagged_history', sport='MLB',
         **{'class': 'prop_matchup'}, subject_scope='prop', market_scope='*',
         condition_expr=_TAGGED_COND, side_expr=_TAGGED_SIDE,
         strength_expr='0.35',
         display_prose_template='pitcher has been tagged by this lineup (vs-team ERA >=5.50, IP >=3)',
         description='Pitcher-vs-team history rough. IP>=3.',
         enabled=True, origin='vs_team_prop_2026_08_22'),
]

for sig in SIGNALS:
    r = requests.post(f'{SB}/rest/v1/signal_sources?on_conflict=signal_key,sport,market_scope',
                      headers=HW, json=sig, timeout=10)
    marker = '+' if r.status_code == 201 else '.' if r.status_code == 204 else '!'
    print(f'  {marker} {sig["signal_key"]:38s}: {r.status_code}')

print(f'\nTotal shipped: {len(SIGNALS)}')
