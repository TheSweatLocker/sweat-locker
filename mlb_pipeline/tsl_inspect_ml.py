import os, sys, requests, json
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv('.env')
SB=os.environ['SUPABASE_URL']; K=os.environ['SUPABASE_KEY']
H={'apikey':K,'Authorization':f'Bearer {K}'}
gd='2026-08-23'

targets = ['Miami Marlins ML','Philadelphia Phillies ML','Los Angeles Dodgers ML','Boston Red Sox ML']

r=requests.get(f'{SB}/rest/v1/mlb_game_context',
  headers=H, params={'game_date':f'eq.{gd}','select':'game_id,home_team,away_team,primary_play'},timeout=25)
games = r.json() or []
for g in games:
    pp = g.get('primary_play') or {}
    if pp.get('label') not in targets: continue
    print('='*82)
    print(f'{pp.get("label")}  [PRIME {pp.get("conviction")}]  {g["away_team"]} @ {g["home_team"]}')
    print(f'  score={pp.get("score")}  audit_note={pp.get("audit_note")}')
    srcs = pp.get('_ensemble_sources') or []
    print(f'  --- {len(srcs)} contributing sources (sorted by |contribution|) ---')
    srcs_sorted = sorted(srcs, key=lambda s: -abs(s.get('contribution',0) or 0))
    for s in srcs_sorted:
        sign = '+' if (s.get('contribution',0) or 0)>=0 else '-'
        print(f'    {sign}{abs(s.get("contribution",0) or 0):.2f}  {(s.get("class") or "?"):>12}  {(s.get("signal_key") or "?"):<32} {(s.get("side") or "?"):<8} w={s.get("weight","?"):<5} n={s.get("n","?")}')
        print(f'         > "{(s.get("prose") or "")[:110]}"')
    am = pp.get('_ensemble_all_markets') or {}
    if am:
        print(f'  Other markets: RL={(am.get("rl") or {}).get("label")} {(am.get("rl") or {}).get("tier")} / TOTAL={(am.get("total") or {}).get("label")} {(am.get("total") or {}).get("tier")}')
    print()
