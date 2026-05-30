"""Quick Jerry test — run against tonight's full slate and compare vs v3/v4/market."""
import os, sys, io, json, urllib.request
from urllib.parse import quote
from dotenv import load_dotenv

load_dotenv()
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jerry_model import compute_jerry_projection, enrich_ctx_for_jerry

URL = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def get(p):
    with urllib.request.urlopen(urllib.request.Request(URL + p, headers=H), timeout=20) as r:
        return json.loads(r.read())


games = get('/rest/v1/mlb_game_context?game_date=eq.2026-05-30&select=*&order=sweat_score.desc')

# Pre-load lookup caches
pitcher_names = set()
team_names = set()
for g in games:
    if g.get('away_pitcher'): pitcher_names.add(g['away_pitcher'])
    if g.get('home_pitcher'): pitcher_names.add(g['home_pitcher'])
    if g.get('away_team'): team_names.add(g['away_team'])
    if g.get('home_team'): team_names.add(g['home_team'])

# Pitcher stats — use IN filter via or-clauses
def in_filter(field, vals):
    return ','.join(f'{field}.eq.{quote(v)}' for v in vals)

pname_filter = in_filter('player_name', pitcher_names)
ps = get(f'/rest/v1/mlb_pitcher_stats?or=({pname_filter})&select=player_name,innings_1_3_era,innings_4_6_era,innings_7_9_era')
pitcher_stats = {p['player_name']: p for p in ps}

team_filter = in_filter('team', team_names)
to = get(f'/rest/v1/mlb_team_offense?or=({team_filter})&select=*')
team_offense = {t['team']: t for t in to}

bp = get(f'/rest/v1/mlb_bullpen_stats?or=({team_filter})&select=team,pitching_1_3_era,pitching_4_6_era,pitching_7_9_era')
bullpen_stats = {b['team']: b for b in bp}

print(f'Cached: {len(pitcher_stats)} pitchers, {len(team_offense)} teams, {len(bullpen_stats)} bullpens')
print()
print(f'{"GAME":54s}  {"JERRY":>5s} {"V3":>5s} {"V4":>5s} {"MKT":>5s}  {"J-MKT":>6s}  JerrySpread')
print('-' * 110)

def _parse_l10(s):
    if not s: return (None, None)
    try:
        parts = str(s).split('-')
        return (int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return (None, None)

for g in games:
    # Inject L10 from mlb_game_context's home_last10 / away_last10 strings
    h_w, h_l = _parse_l10(g.get('home_last10'))
    a_w, a_l = _parse_l10(g.get('away_last10'))
    g['home_l10_wins'] = h_w; g['home_l10_losses'] = h_l
    g['away_l10_wins'] = a_w; g['away_l10_losses'] = a_l
    # Inject known recent mastery for tonight's POTD + Braves ML games (verified via MLB API)
    if g.get('home_pitcher') == 'Roki Sasaki' and g.get('away_pitcher') == 'Jesús Luzardo':
        # Luzardo recent vs LAD: 2.57 ERA on 14 IP (2 starts)
        g['away_pitcher_vs_team_recent_era'] = 2.57
        g['away_pitcher_vs_team_recent_ip'] = 14.0
    if g.get('home_pitcher') == 'Brady Singer' and g.get('away_pitcher') == 'Martín Pérez':
        # Singer recent vs ATL: 3.00 ERA on 18 IP (3 starts) — much better than 5.48 career
        g['home_pitcher_vs_team_recent_era'] = 3.0
        g['home_pitcher_vs_team_recent_ip'] = 18.0
    enriched = enrich_ctx_for_jerry(g, pitcher_stats, team_offense, bullpen_stats)
    r = compute_jerry_projection(enriched)
    market = g.get('close_total') or g.get('open_total')
    v3 = g.get('projected_total')
    v4 = g.get('model_pred_total')
    j_total = r['jerry_total']
    j_spread = r['jerry_spread']
    matchup = f"{(g.get('away_team') or '?')[:22]:22s} @ {(g.get('home_team') or '?')[:22]:22s}"
    j_mkt = (j_total - market) if market else None
    j_mkt_str = f"{j_mkt:+.2f}" if j_mkt is not None else '?'
    print(f'{matchup:54s}  {j_total:>5.2f} {v3 or "?":>5} {v4 or "?":>5} {market or "?":>5}  {j_mkt_str:>6s}  {j_spread:+.2f}')
