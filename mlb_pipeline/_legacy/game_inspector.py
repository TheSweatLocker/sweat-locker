"""Game inspector - dump EVERYTHING the ensemble sees for one game.

Human-readable audit tool. For a given date + team match, prints:
  - Game info (teams, pitchers, weather, park)
  - Market lines (open + close ML/RL/Total)
  - Public splits (OddsCrowd bets%/money%/fade)
  - Pitcher context (xERA, L3 ERA, K rate, 1st-inn ERA, vs-team history)
  - Bullpen state (ERA, availability, arms used L3d)
  - Team offense (wRC+, OPS, barrel%, BABIP L14)
  - Model predictions (V4 spread, Panel margin/total, Jerry pred, MC)
  - Historical trends (ATS L10, over/under L10, H2H recent)
  - Sharp scenarios (which patterns fire)
  - External handicapper picks
  - Final ensemble decision per market + ALL contributing chips
  - Winning-side vs losing-side score breakdown

CLI:
    python game_inspector.py --team "Yankees"
    python game_inspector.py --team "Yankees" --date 2026-08-21
    python game_inspector.py --game-id abc123...
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import datetime, timezone
from pathlib import Path
import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
for line in _env.read_text().split('\n'):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

BAR = '=' * 78
LINE = '-' * 78


def find_game(team, date):
    r = requests.get(f'{SB}/rest/v1/mlb_game_context', headers=H,
                     params={'game_date': f'eq.{date}', 'select': '*'}, timeout=30)
    games = r.json() if isinstance(r.json(), list) else []
    for g in games:
        if team.lower() in (g.get('home_team', '') + ' ' + g.get('away_team', '')).lower():
            return g
    return None


def fmt(v, dec=2, na='—'):
    if v is None: return na
    try: return f'{float(v):.{dec}f}'
    except (TypeError, ValueError): return str(v)


def section(title):
    print(f'\n{BAR}\n{title}\n{BAR}')


def sub(title):
    print(f'\n{title}\n{LINE}')


def render_game(g):
    a = g.get('away_team', '?'); h = g.get('home_team', '?')
    print(f'\n{BAR}')
    print(f'{a} @ {h}   |   {g.get("game_date", "?")}   |   game_id: {g.get("game_id","?")[:16]}')
    print(BAR)

    section('BASIC INFO')
    print(f'  home starter: {g.get("home_pitcher","?")}')
    print(f'  away starter: {g.get("away_pitcher","?")}')
    print(f'  venue:        {g.get("venue","?")}  (park factor: {fmt(g.get("park_factor"), 2)})')
    print(f'  weather:      {fmt(g.get("temperature"),0)}F, wind {fmt(g.get("wind_speed"),0)}mph {g.get("wind_direction","?")}')

    section('MARKET (odds / lines)')
    print(f'  ML close:     home {g.get("home_ml_close","?")}   away {g.get("away_ml_close","?")}   (open home {g.get("home_ml_open","?")}, away {g.get("away_ml_open","?")})')
    print(f'  spread close: home {g.get("home_spread_close","?")}   |   open {g.get("open_spread","?")}')
    print(f'  total close:  {g.get("close_total","?")}   |   open {g.get("open_total","?")}')

    section('PUBLIC SPLITS (OddsCrowd snapshot)')
    oc = g.get('oddscrowd_snapshot') or {}
    if isinstance(oc, str):
        try: oc = json.loads(oc)
        except: oc = {}
    for market in ('ml', 'rl', 'total'):
        m = oc.get(market)
        if not isinstance(m, dict): continue
        div = m.get('div'); div_sign = f'+{div}' if isinstance(div, (int, float)) and div > 0 else str(div)
        print(f'  {market:6s}  bets={m.get("bets","?")}%   money={m.get("money","?")}%   div={div_sign}   fade={m.get("fade","?")}   pick={m.get("pick","?")}')
    print(f'  consensus_fade_flag: {g.get("consensus_fade_flag","?")}   side: {g.get("consensus_fade_side","—")}   pct: {g.get("consensus_fade_pct","—")}   n: {g.get("consensus_fade_n","—")}')

    section('PITCHER CONTEXT')
    for prefix, label in (('home','HOME'), ('away','AWAY')):
        sub(f'{label}: {g.get(prefix+"_pitcher","?")}')
        print(f'  season:       xERA {fmt(g.get(prefix+"_sp_xera"),2)}  |  sIERA {fmt(g.get(prefix+"_sp_siera"),2)}')
        print(f'  recent L3:    ERA {fmt(g.get(prefix+"_pitcher_last_3_era"),2)}  |  K% {fmt(g.get(prefix+"_pitcher_last_3_k_pct"),1)}')
        print(f'  1st inning:   ERA {fmt(g.get(prefix+"_first_inning_era"),2)}  |  WHIP {fmt(g.get(prefix+"_first_inning_whip"),2)}  |  BB {fmt(g.get(prefix+"_first_inning_bb"),0)}  |  K {fmt(g.get(prefix+"_first_inning_k"),0)}')
        print(f'  vs opp team:  ERA {fmt(g.get(prefix+"_pitcher_vs_team_era"),2)}  |  BAA {fmt(g.get(prefix+"_pitcher_vs_team_avg"),3)}  |  K/9 {fmt(g.get(prefix+"_pitcher_vs_team_k_per_9"),1)}  |  IP {fmt(g.get(prefix+"_pitcher_vs_team_ip"),0)}')
        print(f'  projected:    outs {fmt(g.get(prefix+"_pitcher_projected_outs"),1)}  ks {fmt(g.get(prefix+"_pitcher_projected_ks"),1)}  bb {fmt(g.get(prefix+"_pitcher_projected_bb"),1)}  er {fmt(g.get(prefix+"_pitcher_projected_er"),1)}')

    section('BULLPEN')
    for prefix, label in (('home','HOME'), ('away','AWAY')):
        sub(f'{label} bullpen')
        print(f'  season ERA: {fmt(g.get(prefix+"_bullpen_era"),2)}  |  effective ERA: {fmt(g.get(prefix+"_bullpen_effective_era"),2)}')
        print(f'  arms used L3d: {g.get(prefix+"_bp_relievers_3d","?")}  |  availability: {fmt(g.get(prefix+"_bullpen_availability"),2)}')
        print(f'  save %: {fmt(g.get(prefix+"_save_pct"),1)}')

    section('OFFENSE / LINEUP')
    for prefix, label in (('home','HOME'), ('away','AWAY')):
        sub(f'{label} offense')
        print(f'  season wRC+: {g.get(prefix+"_wrc_plus","?")}  |  L14 wRC proxy: {fmt(g.get(prefix+"_wrc_proxy_l14"),1)}  |  vs opp hand: {fmt(g.get(prefix+"_wrc_vs_opp_hand"),1)}')
        print(f'  team_k%: {fmt(g.get(prefix+"_team_k_pct"),1)}   babip_L14: {fmt(g.get(prefix+"_team_babip_l14"),3)}   barrel%: {fmt(g.get(prefix+"_team_barrel_pct"),1)}   xwoba: {fmt(g.get(prefix+"_team_xwoba"),3)}')
        print(f'  OAA (defense): {fmt(g.get(prefix+"_team_oaa"),0)}')

    section('HISTORICAL / TRENDS')
    for prefix, label in (('home','HOME'), ('away','AWAY')):
        sub(f'{label} L10 trends')
        print(f'  ATS L10: {g.get(prefix+"_ats_last10","?")}  season: {g.get(prefix+"_season_cover_pct","?")}%')
        print(f'  as fav cover%: {g.get(prefix+"_covers_as_fav_pct","?")}   as dog cover%: {g.get(prefix+"_covers_as_dog_pct","?")}')
        print(f'  season over%: {g.get(prefix+"_season_over_pct","?")}')

    section('MODEL PREDICTIONS')
    print(f'  V4 model spread:      {fmt(g.get("v4_model_spread"),2)} (home perspective)')
    print(f'  V4 model total:       {fmt(g.get("v4_model_total"),2)}')
    print(f'  panel implied margin: {fmt(g.get("panel_implied_margin"),2)}')
    print(f'  panel implied total:  {fmt(g.get("panel_implied_total"),2)}')
    print(f'  jerry pred spread:    {fmt(g.get("jerry_pred_spread"),2)}')
    print(f'  jerry pred total:     {fmt(g.get("jerry_pred_total"),2)}')
    print(f'  MC high conf:         side={g.get("mc_high_conf_side","?")} pct={fmt(g.get("mc_high_conf_pct"),2)}')

    section('SIGNAL CONFLUENCE')
    print(f'  signal_confluence_net: {g.get("signal_confluence_net","?")}  (POSITIVE = away favored per convention)')

    # Sharp scenarios
    section('SHARP SCENARIO MATCHES')
    r = requests.get(f'{SB}/rest/v1/sharp_scenario_game_matches', headers=H,
                     params={'game_id': f'eq.{g["game_id"]}', 'select': '*'}, timeout=15)
    for m in (r.json() if isinstance(r.json(), list) else []):
        if not isinstance(m, dict): continue
        arrow = 'BACK' if m.get('back_or_fade') == 'BACK' else 'FADE' if m.get('back_or_fade') == 'FADE' else '—'
        print(f'  [{m.get("market"):5s}] {m.get("scenario_key"):32s} side={m.get("side"):<6s} {arrow:<4s} hit={fmt(m.get("hit_rate"),1)}%  n={m.get("n","?")}')

    # External picks
    section('EXTERNAL HANDICAPPER PICKS')
    r = requests.get(f'{SB}/rest/v1/external_picks', headers=H,
                     params={'game_id': f'eq.{g["game_id"]}', 'select': 'source,surface,pick_side,pick_line,fade_flag'}, timeout=15)
    for p in (r.json() if isinstance(r.json(), list) else []):
        if not isinstance(p, dict): continue
        print(f'  {p.get("source"):15s}  {p.get("surface"):5s}  pick={p.get("pick_side"):5s}  line={p.get("pick_line","?")}  fade_flag={p.get("fade_flag")}')

    # ENSEMBLE decision
    section('ENSEMBLE DECISION (primary_play + all market decisions)')
    pp = g.get('primary_play') or {}
    if isinstance(pp, str):
        try: pp = json.loads(pp)
        except: pp = {}
    print(f'  TOP PICK: {pp.get("tier","?")} {pp.get("label","?")}')
    print(f'    type:       {pp.get("type","?")}')
    print(f'    conviction: {pp.get("conviction","?")}')
    print(f'    score:      {pp.get("score","?")}')
    print(f'    engine:     {pp.get("_engine","?")}')
    print(f'    audit:      {pp.get("audit_note","?")[:100]}')

    alt = pp.get('_ensemble_all_markets') or {}
    print(f'\n  ALL MARKETS:')
    for m in ('ml', 'rl', 'total'):
        d = alt.get(m) or {}
        print(f'    {m:6s} {d.get("label","?"):32s}  tier={d.get("tier","?"):6s}  conv={d.get("conviction","?")}')

    sub('WINNING SIDE — every chip that contributed (sorted by contribution)')
    chips = pp.get('_ensemble_sources') or []
    total = sum(c.get('contribution', 0) for c in chips if isinstance(c, dict))
    print(f'  total score contributed: {total:.3f}')
    for c in sorted(chips, key=lambda x: -x.get('contribution', 0)):
        if not isinstance(c, dict): continue
        share = c.get('contribution', 0) / total * 100 if total > 0 else 0
        prose = c.get('prose', '')
        print(f'    {c.get("signal_key")[:48]:48s}  weight={c.get("weight",0):.2f}  contrib={c.get("contribution",0):.3f}  ({share:4.0f}%)')
        if prose: print(f'      → {prose[:100]}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--team', required=False)
    p.add_argument('--date', default=None)
    p.add_argument('--game-id', dest='game_id')
    args = p.parse_args()

    date = args.date or datetime.now(timezone.utc).date().isoformat()

    if args.game_id:
        r = requests.get(f'{SB}/rest/v1/mlb_game_context', headers=H,
                         params={'game_id': f'eq.{args.game_id}', 'select': '*'}, timeout=15)
        rows = r.json() if isinstance(r.json(), list) else []
        if rows:
            render_game(rows[0])
            return
        print(f'no game with id {args.game_id}')
        return

    if not args.team:
        print('need --team or --game-id')
        return
    g = find_game(args.team, date)
    if not g:
        print(f'no game found for {args.team} on {date}')
        return
    render_game(g)


if __name__ == '__main__':
    main()
