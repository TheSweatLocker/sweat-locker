"""Ship NFL game-level signals to close framework gaps."""
import os, requests
from pathlib import Path
_env = Path(__file__).parent / '.env'
for line in _env.read_text().split('\n'):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())
SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = {**H, 'Content-Type':'application/json',
      'Prefer':'resolution=merge-duplicates,return=minimal'}

SIGNALS = [
    # A. DIRECT ODDSCROWD SIGNALS (mirror MLB pattern — currently zero direct signals reading oddscrowd)
    dict(signal_key='oddscrowd_ml_fade_boost_nfl', sport='NFL',
         **{'class':'oddscrowd_direct'}, subject_scope='game', market_scope='*',
         condition_expr="isinstance(ctx.oddscrowd_snapshot, dict) and isinstance(ctx.oddscrowd_snapshot.get('ml'), dict) and ctx.oddscrowd_snapshot['ml'].get('fade') == 'boost' and ctx.oddscrowd_snapshot['ml'].get('pick') in ('HOME','AWAY')",
         side_expr="(ctx.oddscrowd_snapshot['ml']['pick'] + '_ML')",
         strength_expr='0.30',
         display_prose_template='OC ml fade=boost on {sample_n}',
         description='NFL DIRECT OC signal 8/21 mirror of MLB. Verifier weight.',
         enabled=True, origin='nfl_expansion_2026_08_21'),
    dict(signal_key='oddscrowd_rl_fade_boost_nfl', sport='NFL',
         **{'class':'oddscrowd_direct'}, subject_scope='game', market_scope='*',
         condition_expr="isinstance(ctx.oddscrowd_snapshot, dict) and isinstance(ctx.oddscrowd_snapshot.get('rl'), dict) and ctx.oddscrowd_snapshot['rl'].get('fade') == 'boost' and ctx.oddscrowd_snapshot['rl'].get('pick') in ('HOME','AWAY')",
         side_expr="(ctx.oddscrowd_snapshot['rl']['pick'] + '_RL')",
         strength_expr='0.30',
         display_prose_template='OC spread fade=boost on {sample_n}',
         description='NFL OC RL signal 8/21.', enabled=True, origin='nfl_expansion_2026_08_21'),
    dict(signal_key='oddscrowd_total_fade_boost_nfl', sport='NFL',
         **{'class':'oddscrowd_direct'}, subject_scope='game', market_scope='*',
         condition_expr="isinstance(ctx.oddscrowd_snapshot, dict) and isinstance(ctx.oddscrowd_snapshot.get('total'), dict) and ctx.oddscrowd_snapshot['total'].get('fade') == 'boost' and ctx.oddscrowd_snapshot['total'].get('pick') in ('OVER','UNDER')",
         side_expr="ctx.oddscrowd_snapshot['total']['pick']",
         strength_expr='0.35',
         display_prose_template='OC total fade=boost, sharp side',
         description='NFL OC total signal 8/21.', enabled=True, origin='nfl_expansion_2026_08_21'),

    # B. WEATHER - upgrade beyond existing basic ones
    dict(signal_key='nfl_extreme_wind_under', sport='NFL',
         **{'class':'weather'}, subject_scope='game', market_scope='*',
         condition_expr='ctx.wind is not None and float(ctx.wind) >= 20',
         side_expr='"UNDER"', strength_expr='0.65',
         display_prose_template='wind >= 20mph — passing suppressed, total under',
         description='Extreme wind 8/21 upgrade from nfl_high_wind_under threshold.',
         enabled=True, origin='nfl_expansion_2026_08_21'),
    dict(signal_key='nfl_freezing_cold_under', sport='NFL',
         **{'class':'weather'}, subject_scope='game', market_scope='*',
         condition_expr='ctx.temp is not None and float(ctx.temp) <= 25',
         side_expr='"UNDER"', strength_expr='0.55',
         display_prose_template='temp <= 25F — freezing game, total under',
         description='Freezing weather 8/21.', enabled=True, origin='nfl_expansion_2026_08_21'),

    # C. REST ADVANTAGE — upgrade beyond binary; check gaps
    dict(signal_key='nfl_home_rest_edge_2plus', sport='NFL',
         **{'class':'situational'}, subject_scope='game', market_scope='*',
         condition_expr='ctx.home_rest is not None and ctx.away_rest is not None and int(ctx.home_rest) - int(ctx.away_rest) >= 2',
         side_expr='"HOME_ML"', strength_expr='0.50',
         display_prose_template='home has 2+ days rest advantage',
         description='NFL rest edge home 8/21.', enabled=True, origin='nfl_expansion_2026_08_21'),
    dict(signal_key='nfl_away_rest_edge_2plus', sport='NFL',
         **{'class':'situational'}, subject_scope='game', market_scope='*',
         condition_expr='ctx.home_rest is not None and ctx.away_rest is not None and int(ctx.away_rest) - int(ctx.home_rest) >= 2',
         side_expr='"AWAY_ML"', strength_expr='0.50',
         display_prose_template='away has 2+ days rest advantage',
         description='NFL rest edge away 8/21.', enabled=True, origin='nfl_expansion_2026_08_21'),
    dict(signal_key='nfl_short_week_thursday_fade', sport='NFL',
         **{'class':'situational'}, subject_scope='game', market_scope='*',
         condition_expr='ctx.home_rest is not None and int(ctx.home_rest) <= 4 and ctx.away_rest is not None and int(ctx.away_rest) <= 4',
         side_expr='"UNDER"', strength_expr='0.40',
         display_prose_template='both teams on short week (Thursday) — total under (fatigue)',
         description='NFL Thursday night under 8/21.', enabled=True, origin='nfl_expansion_2026_08_21'),

    # D. OFFENSE RATING MISMATCH
    dict(signal_key='nfl_offense_mismatch_home_edge', sport='NFL',
         **{'class':'offense'}, subject_scope='game', market_scope='*',
         condition_expr='ctx.home_off_rating is not None and ctx.away_off_rating is not None and float(ctx.home_off_rating) - float(ctx.away_off_rating) >= 5',
         side_expr='"HOME_ML"', strength_expr='0.55',
         display_prose_template='home offense rating +5 above opponent',
         description='NFL offense mismatch 8/21.', enabled=True, origin='nfl_expansion_2026_08_21'),
    dict(signal_key='nfl_offense_mismatch_away_edge', sport='NFL',
         **{'class':'offense'}, subject_scope='game', market_scope='*',
         condition_expr='ctx.home_off_rating is not None and ctx.away_off_rating is not None and float(ctx.away_off_rating) - float(ctx.home_off_rating) >= 5',
         side_expr='"AWAY_ML"', strength_expr='0.55',
         display_prose_template='away offense rating +5 above home',
         description='NFL offense mismatch away 8/21.', enabled=True, origin='nfl_expansion_2026_08_21'),
    dict(signal_key='nfl_both_offenses_hot_over', sport='NFL',
         **{'class':'offense'}, subject_scope='game', market_scope='*',
         condition_expr='ctx.home_off_rating is not None and ctx.away_off_rating is not None and float(ctx.home_off_rating) >= 5 and float(ctx.away_off_rating) >= 5',
         side_expr='"OVER"', strength_expr='0.55',
         display_prose_template='both offenses rated 5+ — total over',
         description='NFL both hot 8/21.', enabled=True, origin='nfl_expansion_2026_08_21'),

    # E. MODEL CONSENSUS — when all models agree, extra weight
    # 2026-08-22 FIX (silent-bug audit finding #5): ctx.panel_pred_spread was
    # never populated — nfl_game_context.py only writes panel_pred_home_pts,
    # panel_pred_away_pts, panel_pred_total. Derive spread inline as
    # (home_pts - away_pts) — negative means home favored (matches close_spread
    # convention).
    dict(signal_key='nfl_model_consensus_home_spread', sport='NFL',
         **{'class':'model'}, subject_scope='game', market_scope='*',
         condition_expr='ctx.v4_spread is not None and ctx.panel_pred_home_pts is not None and ctx.panel_pred_away_pts is not None and float(ctx.v4_spread) < 0 and (float(ctx.panel_pred_away_pts) - float(ctx.panel_pred_home_pts)) < 0',
         side_expr='"HOME_RL"', strength_expr='0.55',
         display_prose_template='V4 + Panel models both project home cover',
         description='NFL model consensus home 8/21.', enabled=True, origin='nfl_expansion_2026_08_21'),
    dict(signal_key='nfl_model_consensus_away_spread', sport='NFL',
         **{'class':'model'}, subject_scope='game', market_scope='*',
         condition_expr='ctx.v4_spread is not None and ctx.panel_pred_home_pts is not None and ctx.panel_pred_away_pts is not None and float(ctx.v4_spread) > 0 and (float(ctx.panel_pred_away_pts) - float(ctx.panel_pred_home_pts)) > 0',
         side_expr='"AWAY_RL"', strength_expr='0.55',
         display_prose_template='V4 + Panel models both project away cover',
         description='NFL model consensus away 8/21.', enabled=True, origin='nfl_expansion_2026_08_21'),

    # F. PROJECTION SANITY GUARD (mirror MLB pattern)
    dict(signal_key='nfl_projection_contradicts_total_over', sport='NFL',
         **{'class':'model'}, subject_scope='game', market_scope='*',
         condition_expr='ctx.projected_total is not None and ctx.close_total is not None and float(ctx.close_total) - float(ctx.projected_total) >= 3.5',
         side_expr='"UNDER"', strength_expr='0.75',
         display_prose_template='book total 3.5+ above projected — fade the over',
         description='NFL projection sanity guard 8/21.', enabled=True, origin='nfl_expansion_2026_08_21'),
    dict(signal_key='nfl_projection_contradicts_total_under', sport='NFL',
         **{'class':'model'}, subject_scope='game', market_scope='*',
         condition_expr='ctx.projected_total is not None and ctx.close_total is not None and float(ctx.projected_total) - float(ctx.close_total) >= 3.5',
         side_expr='"OVER"', strength_expr='0.75',
         display_prose_template='book total 3.5+ below projected — fade the under',
         description='NFL projection sanity total under guard 8/21.', enabled=True, origin='nfl_expansion_2026_08_21'),

    # G. DOME + INDOOR OVER LEAN
    # already exists (nfl_dome_over) — skip

    # H. DIVISION UNDERDOG (division dogs cover ~53% historically)
    dict(signal_key='nfl_division_underdog_cover', sport='NFL',
         **{'class':'situational'}, subject_scope='game', market_scope='*',
         condition_expr='ctx.div_game is not None and (ctx.div_game == True or ctx.div_game == "true" or str(ctx.div_game).lower() == "true") and ctx.close_spread is not None and float(ctx.close_spread) < 0',
         side_expr='"AWAY_RL"', strength_expr='0.45',
         display_prose_template='division game + home fav — dog +points cover trend',
         description='NFL division dog cover 8/21. Historical ~53% cover.',
         enabled=True, origin='nfl_expansion_2026_08_21'),
]

for sig in SIGNALS:
    r = requests.post(f'{SB}/rest/v1/signal_sources', headers=HW, json=sig, timeout=10)
    marker = '+' if r.status_code == 201 else '.' if r.status_code == 204 else '!'
    print(f'  {marker} {sig["signal_key"]:38s}: {r.status_code}')

print(f'\nTotal shipped: {len(SIGNALS)}')
