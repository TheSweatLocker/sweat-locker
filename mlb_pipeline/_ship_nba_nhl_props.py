"""Ship NBA + NHL prop signal batches — cross-sport expansion continuation 8/21."""
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

SIGNALS = []

# ═══════════════════════════════════════════════════════════════════════
# NBA PROPS — 12 signals (was 9)
# ═══════════════════════════════════════════════════════════════════════
NBA_PROPS = [
    dict(signal_key='nba_prop_l10_extreme', sport='NBA',
         **{'class':'prop_form'}, subject_scope='prop', market_scope='*',
         condition_expr="p.get('player_l10_hit_count') is not None and (int(p['player_l10_hit_count']) >= 8 or int(p['player_l10_hit_count']) <= 2)",
         side_expr="'BACK' if (int(p['player_l10_hit_count']) >= 8 and p['direction']=='over') or (int(p['player_l10_hit_count']) <= 2 and p['direction']=='under') else 'FADE'",
         strength_expr="abs(int(p['player_l10_hit_count']) - 5) / 5.0",
         display_prose_template='L10 hit count extreme',
         description='NBA L10 extreme 8/21.', enabled=True, origin='nba_props_2026_08_21'),
    dict(signal_key='nba_prop_l5_hot', sport='NBA',
         **{'class':'prop_form'}, subject_scope='prop', market_scope='*',
         condition_expr="p.get('player_l5_hit_count') is not None and int(p['player_l5_hit_count']) >= 4",
         side_expr="'BACK' if p['direction'] == 'over' else 'FADE'",
         strength_expr='0.55',
         display_prose_template='L5 hit rate >= 4/5',
         description='NBA L5 hot 8/21.', enabled=True, origin='nba_props_2026_08_21'),
    dict(signal_key='nba_prop_l5_cold', sport='NBA',
         **{'class':'prop_form'}, subject_scope='prop', market_scope='*',
         condition_expr="p.get('player_l5_hit_count') is not None and int(p['player_l5_hit_count']) <= 1",
         side_expr="'BACK' if p['direction'] == 'under' else 'FADE'",
         strength_expr='0.55',
         display_prose_template='L5 hit rate <= 1/5',
         description='NBA L5 cold 8/21.', enabled=True, origin='nba_props_2026_08_21'),
    dict(signal_key='nba_prop_projection_supports', sport='NBA',
         **{'class':'prop_model'}, subject_scope='prop', market_scope='*',
         condition_expr="p.get('projection') is not None and p.get('prop_line') is not None and ((p['direction'] == 'over' and float(p['projection']) - float(p['prop_line']) >= 0.10 * float(p['prop_line'])) or (p['direction'] == 'under' and float(p['prop_line']) - float(p['projection']) >= 0.10 * float(p['prop_line'])))",
         side_expr="'BACK'", strength_expr='0.55',
         display_prose_template='projection supports prop direction by 10%+',
         description='NBA projection supports 8/21.', enabled=True, origin='nba_props_2026_08_21'),
    dict(signal_key='nba_prop_projection_opposes', sport='NBA',
         **{'class':'prop_model'}, subject_scope='prop', market_scope='*',
         condition_expr="p.get('projection') is not None and p.get('prop_line') is not None and ((p['direction'] == 'over' and float(p['prop_line']) - float(p['projection']) >= 0.10 * float(p['prop_line'])) or (p['direction'] == 'under' and float(p['projection']) - float(p['prop_line']) >= 0.10 * float(p['prop_line'])))",
         side_expr="'FADE'", strength_expr='0.60',
         display_prose_template='projection opposes by 10%+ — FADE',
         description='NBA projection opposes 8/21.', enabled=True, origin='nba_props_2026_08_21'),
    dict(signal_key='nba_prop_projection_strong', sport='NBA',
         **{'class':'prop_model'}, subject_scope='prop', market_scope='*',
         condition_expr="p.get('projection') is not None and p.get('prop_line') is not None and ((p['direction'] == 'over' and float(p['projection']) - float(p['prop_line']) >= 0.25 * float(p['prop_line'])) or (p['direction'] == 'under' and float(p['prop_line']) - float(p['projection']) >= 0.25 * float(p['prop_line'])))",
         side_expr="'BACK'", strength_expr='0.75',
         display_prose_template='projection STRONGLY supports (25%+)',
         description='NBA projection strong 8/21.', enabled=True, origin='nba_props_2026_08_21'),
    dict(signal_key='nba_prop_season_hit_pct_high', sport='NBA',
         **{'class':'prop_form'}, subject_scope='prop', market_scope='*',
         condition_expr="p.get('player_season_hit_pct') is not None and float(p['player_season_hit_pct']) >= 0.65",
         side_expr="'BACK' if p['direction'] == 'over' else 'FADE'",
         strength_expr='0.50',
         display_prose_template='season hit% >= 65 — line-hitting consistency',
         description='NBA season hit rate 8/21.', enabled=True, origin='nba_props_2026_08_21'),
    # B2B fatigue impacts props (fewer minutes, worse shooting)
    dict(signal_key='nba_prop_b2b_fade', sport='NBA',
         **{'class':'situational'}, subject_scope='prop', market_scope='*',
         condition_expr="((ctx.home_is_b2b is not None and (ctx.home_is_b2b == True or str(ctx.home_is_b2b).lower() == 'true') and p.get('player_team') == ctx.home_team) or (ctx.away_is_b2b is not None and (ctx.away_is_b2b == True or str(ctx.away_is_b2b).lower() == 'true') and p.get('player_team') == ctx.away_team))",
         side_expr="'BACK' if p['direction'] == 'under' else 'FADE'",
         strength_expr='0.45',
         display_prose_template='player on back-to-back — fatigue',
         description='NBA B2B fade prop 8/21.', enabled=True, origin='nba_props_2026_08_21'),
    # Pace impact
    dict(signal_key='nba_prop_high_pace_over', sport='NBA',
         **{'class':'situational'}, subject_scope='prop', market_scope='*',
         condition_expr="ctx.home_pace is not None and ctx.away_pace is not None and (float(ctx.home_pace) + float(ctx.away_pace)) / 2 >= 100 and p.get('prop_type') is not None and any(w in str(p['prop_type']).lower() for w in ('pts','points','reb','ast','pra','pr','pa'))",
         side_expr="'BACK' if p['direction'] == 'over' else 'FADE'",
         strength_expr='0.45',
         display_prose_template='avg pace >= 100 — counting stats over',
         description='NBA pace boost prop 8/21.', enabled=True, origin='nba_props_2026_08_21'),
    dict(signal_key='nba_prop_low_pace_under', sport='NBA',
         **{'class':'situational'}, subject_scope='prop', market_scope='*',
         condition_expr="ctx.home_pace is not None and ctx.away_pace is not None and (float(ctx.home_pace) + float(ctx.away_pace)) / 2 <= 96 and p.get('prop_type') is not None and any(w in str(p['prop_type']).lower() for w in ('pts','points','reb','ast','pra','pr','pa'))",
         side_expr="'BACK' if p['direction'] == 'under' else 'FADE'",
         strength_expr='0.45',
         display_prose_template='avg pace <= 96 — counting stats under',
         description='NBA slow pace prop 8/21.', enabled=True, origin='nba_props_2026_08_21'),
    # Star out boosts secondary players
    dict(signal_key='nba_prop_star_out_teammate_boost', sport='NBA',
         **{'class':'situational'}, subject_scope='prop', market_scope='*',
         condition_expr="((ctx.home_star_out is not None and (ctx.home_star_out == True or str(ctx.home_star_out).lower() == 'true') and p.get('player_team') == ctx.home_team) or (ctx.away_star_out is not None and (ctx.away_star_out == True or str(ctx.away_star_out).lower() == 'true') and p.get('player_team') == ctx.away_team))",
         side_expr="'BACK' if p['direction'] == 'over' else 'FADE'",
         strength_expr='0.55',
         display_prose_template='team star OUT — teammate usage boost',
         description='NBA star-out teammate boost 8/21.', enabled=True, origin='nba_props_2026_08_21'),
    # Minutes projection sanity
    dict(signal_key='nba_prop_minutes_low_fade', sport='NBA',
         **{'class':'situational'}, subject_scope='prop', market_scope='*',
         condition_expr="p.get('projected_minutes') is not None and float(p['projected_minutes']) <= 22 and p.get('prop_type') is not None and any(w in str(p['prop_type']).lower() for w in ('pts','points','reb','ast'))",
         side_expr="'BACK' if p['direction'] == 'under' else 'FADE'",
         strength_expr='0.55',
         display_prose_template='projected minutes <= 22 — hard to hit counting-stat overs',
         description='NBA low minutes fade 8/21.', enabled=True, origin='nba_props_2026_08_21'),
]
SIGNALS.extend(NBA_PROPS)

# ═══════════════════════════════════════════════════════════════════════
# NHL PROPS — 10 signals (was 5)
# ═══════════════════════════════════════════════════════════════════════
NHL_PROPS = [
    dict(signal_key='nhl_prop_l10_extreme', sport='NHL',
         **{'class':'prop_form'}, subject_scope='prop', market_scope='*',
         condition_expr="p.get('player_l10_hit_count') is not None and (int(p['player_l10_hit_count']) >= 8 or int(p['player_l10_hit_count']) <= 2)",
         side_expr="'BACK' if (int(p['player_l10_hit_count']) >= 8 and p['direction']=='over') or (int(p['player_l10_hit_count']) <= 2 and p['direction']=='under') else 'FADE'",
         strength_expr="abs(int(p['player_l10_hit_count']) - 5) / 5.0",
         display_prose_template='L10 hit count extreme',
         description='NHL L10 extreme 8/21.', enabled=True, origin='nhl_props_2026_08_21'),
    dict(signal_key='nhl_prop_l5_hot', sport='NHL',
         **{'class':'prop_form'}, subject_scope='prop', market_scope='*',
         condition_expr="p.get('player_l5_hit_count') is not None and int(p['player_l5_hit_count']) >= 4",
         side_expr="'BACK' if p['direction'] == 'over' else 'FADE'",
         strength_expr='0.55',
         display_prose_template='L5 hit rate >= 4/5',
         description='NHL L5 hot 8/21.', enabled=True, origin='nhl_props_2026_08_21'),
    dict(signal_key='nhl_prop_l5_cold', sport='NHL',
         **{'class':'prop_form'}, subject_scope='prop', market_scope='*',
         condition_expr="p.get('player_l5_hit_count') is not None and int(p['player_l5_hit_count']) <= 1",
         side_expr="'BACK' if p['direction'] == 'under' else 'FADE'",
         strength_expr='0.55',
         display_prose_template='L5 hit rate <= 1/5',
         description='NHL L5 cold 8/21.', enabled=True, origin='nhl_props_2026_08_21'),
    dict(signal_key='nhl_prop_projection_supports', sport='NHL',
         **{'class':'prop_model'}, subject_scope='prop', market_scope='*',
         condition_expr="p.get('projection') is not None and p.get('prop_line') is not None and ((p['direction'] == 'over' and float(p['projection']) - float(p['prop_line']) >= 0.15 * float(p['prop_line'])) or (p['direction'] == 'under' and float(p['prop_line']) - float(p['projection']) >= 0.15 * float(p['prop_line'])))",
         side_expr="'BACK'", strength_expr='0.55',
         display_prose_template='projection supports by 15%+',
         description='NHL projection supports 8/21.', enabled=True, origin='nhl_props_2026_08_21'),
    dict(signal_key='nhl_prop_projection_opposes', sport='NHL',
         **{'class':'prop_model'}, subject_scope='prop', market_scope='*',
         condition_expr="p.get('projection') is not None and p.get('prop_line') is not None and ((p['direction'] == 'over' and float(p['prop_line']) - float(p['projection']) >= 0.15 * float(p['prop_line'])) or (p['direction'] == 'under' and float(p['projection']) - float(p['prop_line']) >= 0.15 * float(p['prop_line'])))",
         side_expr="'FADE'", strength_expr='0.60',
         display_prose_template='projection opposes by 15%+ — FADE',
         description='NHL projection opposes 8/21.', enabled=True, origin='nhl_props_2026_08_21'),
    # Goalie strength impact on shooter props
    dict(signal_key='nhl_prop_facing_elite_goalie', sport='NHL',
         **{'class':'prop_matchup'}, subject_scope='prop', market_scope='*',
         condition_expr="((p.get('player_team') == ctx.home_team and ctx.away_goalie_sv_pct is not None and float(ctx.away_goalie_sv_pct) >= 0.920) or (p.get('player_team') == ctx.away_team and ctx.home_goalie_sv_pct is not None and float(ctx.home_goalie_sv_pct) >= 0.920)) and p.get('prop_type') is not None and any(w in str(p['prop_type']).lower() for w in ('goal','pts','shots'))",
         side_expr="'BACK' if p['direction'] == 'under' else 'FADE'",
         strength_expr='0.50',
         display_prose_template='shooter facing elite goalie (SV% >= .920)',
         description='NHL shooter vs elite goalie 8/21.', enabled=True, origin='nhl_props_2026_08_21'),
    # Weak goalie boost
    dict(signal_key='nhl_prop_facing_weak_goalie', sport='NHL',
         **{'class':'prop_matchup'}, subject_scope='prop', market_scope='*',
         condition_expr="((p.get('player_team') == ctx.home_team and ctx.away_goalie_sv_pct is not None and float(ctx.away_goalie_sv_pct) <= 0.895) or (p.get('player_team') == ctx.away_team and ctx.home_goalie_sv_pct is not None and float(ctx.home_goalie_sv_pct) <= 0.895)) and p.get('prop_type') is not None and any(w in str(p['prop_type']).lower() for w in ('goal','pts','shots'))",
         side_expr="'BACK' if p['direction'] == 'over' else 'FADE'",
         strength_expr='0.50',
         display_prose_template='shooter facing weak goalie (SV% <= .895)',
         description='NHL shooter vs weak goalie 8/21.', enabled=True, origin='nhl_props_2026_08_21'),
    # B2B fatigue for skaters
    dict(signal_key='nhl_prop_b2b_fade', sport='NHL',
         **{'class':'situational'}, subject_scope='prop', market_scope='*',
         condition_expr="((ctx.home_is_b2b is not None and (ctx.home_is_b2b == True or str(ctx.home_is_b2b).lower() == 'true') and p.get('player_team') == ctx.home_team) or (ctx.away_is_b2b is not None and (ctx.away_is_b2b == True or str(ctx.away_is_b2b).lower() == 'true') and p.get('player_team') == ctx.away_team))",
         side_expr="'BACK' if p['direction'] == 'under' else 'FADE'",
         strength_expr='0.40',
         display_prose_template='player on B2B — fatigue',
         description='NHL B2B prop fade 8/21.', enabled=True, origin='nhl_props_2026_08_21'),
    # Power play unit prop boost
    dict(signal_key='nhl_prop_pp_specialist_hot_pk', sport='NHL',
         **{'class':'prop_matchup'}, subject_scope='prop', market_scope='*',
         condition_expr="((p.get('player_team') == ctx.home_team and ctx.away_pk_pct is not None and float(ctx.away_pk_pct) <= 0.78) or (p.get('player_team') == ctx.away_team and ctx.home_pk_pct is not None and float(ctx.home_pk_pct) <= 0.78)) and p.get('prop_type') is not None and any(w in str(p['prop_type']).lower() for w in ('goal','pts','pp'))",
         side_expr="'BACK' if p['direction'] == 'over' else 'FADE'",
         strength_expr='0.45',
         display_prose_template='PP opportunity vs weak PK (opp PK <= 78%)',
         description='NHL PP opportunity 8/21.', enabled=True, origin='nhl_props_2026_08_21'),
    # Season hit consistency
    dict(signal_key='nhl_prop_season_hit_pct_high', sport='NHL',
         **{'class':'prop_form'}, subject_scope='prop', market_scope='*',
         condition_expr="p.get('player_season_hit_pct') is not None and float(p['player_season_hit_pct']) >= 0.60",
         side_expr="'BACK' if p['direction'] == 'over' else 'FADE'",
         strength_expr='0.50',
         display_prose_template='season hit% >= 60',
         description='NHL season hit rate 8/21.', enabled=True, origin='nhl_props_2026_08_21'),
]
SIGNALS.extend(NHL_PROPS)

print(f'Shipping {len(SIGNALS)} signals ({len(NBA_PROPS)} NBA props + {len(NHL_PROPS)} NHL props)')
for sig in SIGNALS:
    r = requests.post(f'{SB}/rest/v1/signal_sources', headers=HW, json=sig, timeout=10)
    marker = '+' if r.status_code == 201 else '.' if r.status_code == 204 else '!'
    print(f'  {marker} {sig["signal_key"]:42s}: {r.status_code}')
