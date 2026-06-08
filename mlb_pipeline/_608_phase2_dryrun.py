"""Dry-run: show what Phase 2 cohort wire would produce for tonight's slate
WITHOUT writing to any public surface. Compare to currently-published POTD
and DAWG so user can decide whether to push live refresh."""
import os, sys, requests, json
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

URL = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H = {'apikey':KEY,'Authorization':f'Bearer {KEY}'}

# Force fresh imports
for m in list(sys.modules):
    if 'play_of_day' in m or 'cohort' in m or 'generate_dawg' in m:
        del sys.modules[m]

from play_of_day import score_mlb_game
from generate_dawg_of_day import score_dawg

games = requests.get(f'{URL}/rest/v1/mlb_game_context?game_date=eq.2026-06-08&select=*&order=sweat_score.desc', headers=H).json()
props = requests.get(f'{URL}/rest/v1/mlb_pipeline_props?game_date=eq.2026-06-08&select=*', headers=H).json()
props_by_game = {}
for p in props:
    gid = p.get('game_id')
    if gid: props_by_game.setdefault(gid, []).append(p)

live_potd = requests.get(f'{URL}/rest/v1/jerry_cache?game_id=eq.best_bet_2026-06-08&select=data', headers=H).json()
live_dawg = requests.get(f'{URL}/rest/v1/daily_dawg?game_date=eq.2026-06-08&select=team,matchup,conviction,tier', headers=H).json()

print('=== CURRENT LIVE POTD (published) ===')
if live_potd:
    d = live_potd[0]['data']
    print(f"  Pick:  {d.get('lean') or (d.get('pick') or {}).get('label')}")
    print(f"  Score: {(d.get('score') or {}).get('total')}")
    print(f"  Sport: {d.get('sport')}")

print()
print('=== CURRENT LIVE DAWG ===')
for d in live_dawg:
    print(f"  {d.get('team')} | conviction {d.get('conviction')} | {d.get('tier')}")

print()
print('=' * 100)
print('=== DRY RUN — Phase 2 cohort-wired scoring on tonight slate ===')
print('=' * 100)

scored = []
for g in games:
    gid = g.get('game_id')
    score, dims = score_mlb_game(g, game_props=props_by_game.get(gid, []), track={'contributions':[]})
    play = dims.get('model_play') or {}
    a = g.get('away_team','')[:18]; h = g.get('home_team','')[:18]
    side = dims['side']; tot = dims['total']
    cs = [d for d in (side.get('drivers') or []) if 'ohort' in (d.get('label') or '')]
    ct = [d for d in (tot.get('drivers') or []) if 'ohort' in (d.get('label') or '')]
    scored.append((score, dims, play, a, h, cs, ct, g))

scored.sort(key=lambda x: -x[0])

print()
print('TOP CANDIDATES BY HEADLINE SCORE (Phase 2 wire):')
for i, (score, dims, play, a, h, cs, ct, g) in enumerate(scored[:6], 1):
    side_s = dims['side']['score']; side_t = dims['side']['tier']
    tot_s = dims['total']['score']; tot_t = dims['total']['tier']
    print(f'  #{i} {a:<18} @ {h:<18} | headline={score} | side={side_s}/{side_t} total={tot_s}/{tot_t} | winning={dims["winning_dimension"]}')
    print(f'      pick: {(play.get("label") if play else None) or "—"}')
    for d in cs:
        sign = '+' if d['points']>0 else ''
        print(f'      SIDE cohort {sign}{d["points"]}: {d["detail"][:95]}')
    for d in ct:
        sign = '+' if d['points']>0 else ''
        print(f'      TOT  cohort {sign}{d["points"]}: {d["detail"][:95]}')

top = scored[0]
print()
print('=== IF WE REFRESHED NOW, POTD WOULD BE: ===')
print(f'  {top[3]} @ {top[4]} — {(top[2] or {}).get("label")} (score {top[0]}, winning {top[1]["winning_dimension"]})')

print()
print('=== DAWG candidates (Phase 2 wire) ===')
ml_map = {}
for g in games:
    hml = g.get('home_ml_close') or g.get('home_ml_open')
    aml = g.get('away_ml_close') or g.get('away_ml_open')
    if hml is not None and aml is not None:
        ml_map[(g.get('home_team'), g.get('away_team'))] = {'home_ml': hml, 'away_ml': aml}

dawg_scored = []
for g in games:
    d = score_dawg(g, ml_map=ml_map)
    if d: dawg_scored.append(d)
dawg_scored.sort(key=lambda x: -x['conviction'])
for d in dawg_scored[:5]:
    print(f'  {d["conviction"]:>3} | {d["tier"]:<6} | {d["team"]:<24} ML {d.get("team_ml"):+d}')
    cohort = d.get('signals',{}).get('cohort_confirms') or d.get('signals',{}).get('cohort_fades')
    if cohort: print(f'      cohort: {cohort[:110]}')
