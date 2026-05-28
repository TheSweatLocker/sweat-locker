"""5/24 full-slate audit — every game's model output vs Action Network sharp data."""
import os, json, urllib.request, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
sb = os.environ["SUPABASE_URL"]
h  = {"apikey": os.environ["SUPABASE_KEY"], "Authorization": f'Bearer {os.environ["SUPABASE_KEY"]}'}

def g(p):
    return json.loads(urllib.request.urlopen(urllib.request.Request(sb + p, headers=h), timeout=30).read())

# Standardize team abbrev for AN lookup
ABBR = {
    'Washington Nationals':'WSH','Atlanta Braves':'ATL','Texas Rangers':'TEX','Los Angeles Angels':'LAA',
    'Los Angeles Dodgers':'LAD','Milwaukee Brewers':'MIL','Cleveland Guardians':'CLE','Philadelphia Phillies':'PHI',
    'Colorado Rockies':'COL','Arizona Diamondbacks':'ARI','Detroit Tigers':'DET','Baltimore Orioles':'BAL',
    'Chicago White Sox':'CWS','San Francisco Giants':'SF','Tampa Bay Rays':'TB','New York Yankees':'NYY',
    'Minnesota Twins':'MIN','Boston Red Sox':'BOS','New York Mets':'NYM','Miami Marlins':'MIA',
    'Seattle Mariners':'SEA','Kansas City Royals':'KC','St. Louis Cardinals':'STL','Cincinnati Reds':'CIN',
    'Houston Astros':'HOU','Chicago Cubs':'CHC','Athletics':'ATH','San Diego Padres':'SD',
    'Toronto Blue Jays':'TOR','Pittsburgh Pirates':'PIT',
}

# AN data (5/24, user-pasted 5/25)
AN = {
    ('WSH','ATL'): {'sharp_ml': 'ATL', 'diff': 6,  'note': 'consensus heavy fav'},
    ('TEX','LAA'): {'sharp_ml': 'TEX', 'diff': 24, 'note': 'HUGE sharp on TEX dog'},
    ('LAD','MIL'): {'sharp_ml': 'LAD', 'diff': 1,  'note': 'consensus LAD'},
    ('CLE','PHI'): {'sharp_ml': 'CLE', 'diff': 11, 'note': 'sharp leans CLE harder'},
    ('COL','ARI'): {'sharp_ml': 'COL', 'diff': 2,  'note': 'slight sharp on dog COL'},
    ('DET','BAL'): {'sharp_ml': 'BAL', 'diff': 6,  'note': 'sharp on BAL (or DET in earlier game)'},
    ('CWS','SF'):  {'sharp_ml': 'SF',  'diff': 17, 'note': 'sharp money on SF'},
    ('TB','NYY'):  {'sharp_ml': 'TB',  'diff': 29, 'note': 'HUGE sharp fading NYY'},
    ('MIN','BOS'): {'sharp_ml': 'BOS', 'diff': 1,  'note': 'basically even, slight BOS'},
    ('NYM','MIA'): {'sharp_ml': 'MIA', 'diff': 2,  'note': 'slight sharp MIA'},
    ('SEA','KC'):  {'sharp_ml': 'KC',  'diff': 19, 'note': 'HEAVY sharp on dog KC'},
    ('STL','CIN'): {'sharp_ml': 'STL', 'diff': 24, 'note': 'HUGE sharp on STL dog'},
    ('HOU','CHC'): {'sharp_ml': 'CHC', 'diff': 1,  'note': 'consensus CHC'},
    ('ATH','SD'):  {'sharp_ml': 'ATH', 'diff': 8,  'note': 'sharp on dog ATH'},
}

games = g('/rest/v1/mlb_game_results?game_date=eq.2026-05-24&select=away_team,home_team,away_score,home_score,away_ml_close,home_ml_close,open_total,close_total,open_spread,close_spread,projected_total,projected_spread,model_pred_away_runs,model_pred_home_runs,over_lean,spread_lean,total_result,home_win,home_spread_covered,nrfi_result,nrfi_score,sweat_score,spread_delta&order=away_team.asc&limit=50')

print('=' * 110)
print(f'5/24 FULL SLATE — {len(games)} games — MODEL vs ACTION NETWORK SHARP MONEY')
print('=' * 110)
print()
print(f'{"Matchup":<12} {"Score":<8} {"Line":<11} {"Total":<11} {"Model Proj":<11} {"Lean":<8} {"Total Res":<9} {"Sharp ML":<13} {"Sharp aligned?":<14}')
print('-' * 110)

ml_align = {'agree': 0, 'fade': 0, 'no_lean': 0, 'no_an': 0}
ml_align_winners = {'agree_w': 0, 'agree_l': 0, 'fade_w': 0, 'fade_l': 0}

for row in games:
    a = ABBR.get(row['away_team'], row['away_team'][:3].upper())
    hm = ABBR.get(row['home_team'], row['home_team'][:3].upper())
    match = f'{a}@{hm}'

    aw_sc = row.get('away_score'); h_sc = row.get('home_score')
    score = f'{aw_sc}-{h_sc}' if aw_sc is not None and h_sc is not None else 'PPD/—'

    aw_ml = row.get('away_ml_close'); h_ml = row.get('home_ml_close')
    line  = f'{aw_ml:+d}/{h_ml:+d}' if aw_ml is not None and h_ml is not None else '—'

    o_tot = row.get('open_total'); c_tot = row.get('close_total')
    tot = f'{c_tot}' if c_tot else (f'{o_tot}' if o_tot else '—')

    proj = row.get('projected_total')
    proj_str = f'{proj:.1f}' if proj is not None else '—'

    over_lean = row.get('over_lean') or ''
    sp_lean = row.get('spread_lean') or ''
    sp_delta = row.get('spread_delta')
    over_lean_s = str(over_lean) if over_lean and not isinstance(over_lean, bool) else ''
    sp_lean_s = str(sp_lean) if sp_lean and not isinstance(sp_lean, bool) else ''
    lean_parts = []
    if over_lean_s: lean_parts.append(over_lean_s[:4])
    if sp_lean_s: lean_parts.append(f'{sp_lean_s[:4]}{f"({sp_delta:+.1f})" if sp_delta is not None else ""}')
    lean = ' / '.join(lean_parts) if lean_parts else '—'

    tot_res = row.get('total_result') or '—'

    # Sharp lookup
    an = AN.get((a, hm))
    sharp_ml = f"{an['sharp_ml']} +{an['diff']}%" if an else '—'

    # Model ML lean: derive from projected_spread (negative = home, positive = away)
    model_ml = '—'
    ps = row.get('projected_spread')
    if ps is not None:
        model_ml = hm if ps < 0 else a

    aligned = '—'
    if an and model_ml != '—':
        if model_ml == an['sharp_ml']:
            aligned = '[OK] AGREE'
            ml_align['agree'] += 1
            if row.get('home_win') is not None:
                actual_winner = hm if row['home_win'] else a
                if actual_winner == model_ml: ml_align_winners['agree_w'] += 1
                else: ml_align_winners['agree_l'] += 1
        else:
            aligned = '[X] FADE'
            ml_align['fade'] += 1
            if row.get('home_win') is not None:
                actual_winner = hm if row['home_win'] else a
                if actual_winner == model_ml: ml_align_winners['fade_w'] += 1
                else: ml_align_winners['fade_l'] += 1
    elif not an:
        ml_align['no_an'] += 1
    else:
        ml_align['no_lean'] += 1

    print(f'{match:<12} {score:<8} {line:<11} {tot:<11} {proj_str:<11} {lean:<8} {tot_res:<9} {sharp_ml:<13} {aligned:<14}')

print('-' * 110)
print(f'\nSHARP ALIGNMENT SUMMARY:')
print(f'  Model AGREES with sharps: {ml_align["agree"]}  (won {ml_align_winners["agree_w"]} / lost {ml_align_winners["agree_l"]})')
print(f'  Model FADES sharps:       {ml_align["fade"]}   (won {ml_align_winners["fade_w"]} / lost {ml_align_winners["fade_l"]})')
print(f'  No AN coverage:           {ml_align["no_an"]}')
print(f'  No model lean:            {ml_align["no_lean"]}')

# Total alignment summary
tot_correct = sum(1 for r in games if r.get('total_result') == 'OVER' and (r.get('over_lean') or '').lower().startswith('over')
                                   or r.get('total_result') == 'UNDER' and (r.get('over_lean') or '').lower().startswith('under'))
tot_wrong = sum(1 for r in games if r.get('total_result') in ('OVER','UNDER') and r.get('over_lean') and r.get('total_result').lower() not in (r.get('over_lean') or '').lower())
tot_no_lean = sum(1 for r in games if not r.get('over_lean'))
print(f'\nTOTAL LEAN PERFORMANCE:')
print(f'  Correct: {tot_correct}   Wrong: {tot_wrong}   No lean: {tot_no_lean}')

# NRFI
nrfi_hits = sum(1 for r in games if r.get('nrfi_result') == 'NRFI')
nrfi_total = sum(1 for r in games if r.get('nrfi_result') in ('NRFI','YRFI'))
print(f'\nNRFI: {nrfi_hits}/{nrfi_total} games went NRFI')
