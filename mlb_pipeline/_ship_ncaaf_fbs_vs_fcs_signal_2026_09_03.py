"""Ship the FBS-vs-FCS early-season chalk signal (docs/FCS_COVERAGE_PLAN_2026-09-03.md).

Fires when we detect an FBS home team paired with an unknown-data
opponent (proxy for FCS/D-II) at a heavy home spread in the first 3
weeks of the season. Historical: FBS covers 80% ATS in this spot.

Plug-in via signal_sources — no ensemble_scorer code change.

Gate logic (baked into condition_expr):
    home_sp_overall is not None         # FBS home has SP+ data
    AND away_sp_overall is None         # FCS/D-II opponent (data absent)
    AND close_spread <= -20             # heavy home fav (CFBD sign convention)
    AND '2026-08-25' <= game_date <= '2026-09-22'   # Weeks 1-3 of the 2026 season

Cast (per plan):
    market  : rl
    side    : HOME_RL
    strength: 0.50
    hit_rate: 80% (n=200) — external prior, will retune on live grading
    tier    : DISCOVERY (has historical prior, no first-party sample yet)
"""
import os, requests
from pathlib import Path

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())
SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = {**H, 'Content-Type': 'application/json',
      'Prefer': 'resolution=merge-duplicates,return=minimal'}

SIGNAL = dict(
    signal_key='ncaaf_fbs_vs_fcs_early_season',
    sport='NCAAF',
    subject_scope='game',
    market_scope='rl',
    condition_expr=(
        "ctx.home_sp_overall is not None "
        "and ctx.away_sp_overall is None "
        "and ctx.close_spread is not None "
        "and float(ctx.close_spread) <= -20 "
        "and ctx.game_date is not None "
        "and '2026-08-25' <= str(ctx.game_date) <= '2026-09-22'"
    ),
    side_expr='"HOME_RL"',
    strength_expr='0.50',
    hit_rate_pct=80.0,
    sample_n=200,
    display_prose_template=(
        'FBS home team over unknown-data opponent — early-season chalk pattern (~80% ATS)'
    ),
    description=(
        'FBS home fav vs FCS/D-II opponent in Weeks 1-3. Away team missing '
        'SP+/EPA/returning production => proxy for unrated opponent. '
        'Historical ATS 80% on spread <= -20 body-bags per Team Ranking research.'
    ),
    enabled=True,
    origin='fcs_coverage_plan_2026_09_03',
)
# Set class via kwarg after (reserved keyword).
SIGNAL['class'] = 'situational'

r = requests.post(f'{SB}/rest/v1/signal_sources', headers=HW, json=SIGNAL, timeout=10)
marker = '+' if r.status_code == 201 else '.' if r.status_code == 204 else '!'
print(f'{marker} {SIGNAL["signal_key"]}: {r.status_code}')
if r.status_code >= 300:
    print(r.text)

# Also seed the signal_registry row so it's immediately visible to the
# ensemble scorer's tier lookup (else the inline hit_rate_pct/sample_n
# path is used — both should point to DISCOVERY tier).
REG = dict(
    signal_name='ncaaf_fbs_vs_fcs_early_season',
    sport='NCAAF',
    category='situational',
    market_scope='rl',
    tier='DISCOVERY',
    hit_rate=0.80,
    sample_n=200,
    recommended_weight=0.50,
    origin='fcs_coverage_plan_2026_09_03',
    notes='External prior — 80% ATS on FBS home fav vs FCS/D-II Weeks 1-3 (Team Ranking).',
)
r = requests.post(f'{SB}/rest/v1/signal_registry', headers=HW, json=REG, timeout=10)
marker = '+' if r.status_code == 201 else '.' if r.status_code == 204 else '!'
print(f'{marker} signal_registry ncaaf_fbs_vs_fcs_early_season: {r.status_code}')
if r.status_code >= 300:
    print(r.text)

print('\nDone. Next NCAAF game_context run picks up the signal automatically.')
