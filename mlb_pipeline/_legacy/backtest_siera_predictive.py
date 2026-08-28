"""SIERA backtest — is SIERA gap a real predictive signal for game outcomes?

SIERA was shipped 8/15 with zero backtest. Since SIERA is season-stable
(pitcher's skill doesn't wildly fluctuate week-to-week), we can retro-fit
by using CURRENT-season SIERA on historical games where those pitchers
pitched.

Test:
  1. Pull mlb_game_results (graded, ~1000-4000 games).
  2. Pull savant_pitcher_stats (234 pitchers with current SIERA + xERA).
  3. For each game, look up home_starter + away_starter SIERA + xERA.
  4. Compute gap: home_siera - away_siera.
  5. Test: does gap predict which side wins ML? (fav-arm side hit rate)
  6. Test: does gap predict total over/under? (bigger gap = more variance?)
  7. Compare to xERA gap on same games.

Interpretation:
  - If SIERA gap predicts ML at higher rate than xERA gap → SIERA is real edge
  - If similar → SIERA is redundant with xERA
  - If worse → skip SIERA
"""
import os, requests
from pathlib import Path
from collections import defaultdict

env = Path(__file__).parent / '.env'
for line in env.read_text().split('\n'):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())
SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

# Pull savant_pitcher_stats (SIERA source)
savant = requests.get(f'{SB}/rest/v1/savant_pitcher_stats?select=pitcher_name,siera_full&season=eq.2026', headers=H, timeout=30).json()
siera_by_name = {}
for s in savant:
    name = (s.get('pitcher_name') or '').strip().lower()
    if name and s.get('siera_full') is not None:
        siera_by_name[name] = float(s['siera_full'])
print(f'SIERA pitchers indexed: {len(siera_by_name)}')

# Pull mlb_pitcher_stats (xERA source)
xera_rows = []
for off in range(0, 5000, 1000):
    r = requests.get(f'{SB}/rest/v1/mlb_pitcher_stats?select=player_name,xera&limit=1000&offset={off}', headers=H, timeout=30)
    d = r.json() if isinstance(r.json(), list) else []
    xera_rows += d
    if len(d) < 1000: break
xera_by_name = {(r.get('player_name') or '').strip().lower(): r.get('xera')
                for r in xera_rows if r.get('xera') is not None}
print(f'xERA pitchers indexed: {len(xera_by_name)}')

# Pull graded games (last N days worth — up to 5000)
games = []
for off in range(0, 5000, 1000):
    r = requests.get(f'{SB}/rest/v1/mlb_game_results?home_score=not.is.null&select=game_id,game_date,home_score,away_score,close_total,total_result&limit=1000&offset={off}&order=game_date.desc', headers=H, timeout=30)
    d = r.json() if isinstance(r.json(), list) else []
    games += d
    if len(d) < 1000: break
print(f'graded games: {len(games)}')

# Need home_pitcher + away_pitcher — those come from mlb_game_context (or archived history)
# Pull mlb_game_context (retention may not cover all history but try)
gc = []
for off in range(0, 5000, 1000):
    r = requests.get(f'{SB}/rest/v1/mlb_game_context?select=game_id,home_pitcher,away_pitcher&limit=1000&offset={off}', headers=H, timeout=30)
    d = r.json() if isinstance(r.json(), list) else []
    gc += d
    if len(d) < 1000: break
pitchers_by_gid = {row['game_id']: row for row in gc}
print(f'game_context rows: {len(pitchers_by_gid)}')

# Join
joined = []
for g in games:
    ctx = pitchers_by_gid.get(g['game_id'])
    if not ctx: continue
    hp = (ctx.get('home_pitcher') or '').strip().lower()
    ap = (ctx.get('away_pitcher') or '').strip().lower()
    if not hp or not ap: continue
    h_siera = siera_by_name.get(hp)
    a_siera = siera_by_name.get(ap)
    h_xera = xera_by_name.get(hp)
    a_xera = xera_by_name.get(ap)
    hs = g.get('home_score'); as_ = g.get('away_score')
    if hs is None or as_ is None or hs == as_: continue
    joined.append({
        'game_id': g['game_id'],
        'home_score': hs, 'away_score': as_,
        'total': hs + as_,
        'close_total': g.get('close_total'),
        'total_result': (g.get('total_result') or '').lower(),
        'h_siera': h_siera, 'a_siera': a_siera,
        'h_xera': h_xera, 'a_xera': a_xera,
        'actual_ml_winner': 'HOME' if hs > as_ else 'AWAY',
    })
print(f'joined games (both metrics available): {len(joined)}')

# ============================================================
# TEST 1: SIERA gap ML prediction — does fav-arm side win more?
# ============================================================
def test_gap_predicts_ml(rows, gap_key):
    """For each row where gap_key populated, compute: does side with better arm win?"""
    buckets = defaultdict(lambda: {'wins':0,'losses':0})
    for r in rows:
        h_val = r.get(f'h_{gap_key}')
        a_val = r.get(f'a_{gap_key}')
        if h_val is None or a_val is None: continue
        gap = h_val - a_val
        # Fav-arm side = one with LOWER SIERA/xERA
        fav_side = 'HOME' if gap < 0 else 'AWAY'
        won = fav_side == r['actual_ml_winner']
        # Bucket by abs gap
        ag = abs(gap)
        if ag < 0.25: b = '0.00-0.24'
        elif ag < 0.50: b = '0.25-0.49'
        elif ag < 0.75: b = '0.50-0.74'
        elif ag < 1.00: b = '0.75-0.99'
        elif ag < 1.50: b = '1.00-1.49'
        else: b = '1.50+'
        if won: buckets[b]['wins'] += 1
        else: buckets[b]['losses'] += 1
    return buckets

print('\n' + '='*80)
print('TEST 1: SIERA gap → fav-arm ML win rate (fav-arm = LOWER SIERA)')
print('='*80)
s_buckets = test_gap_predicts_ml(joined, 'siera')
for b in sorted(s_buckets.keys()):
    w = s_buckets[b]['wins']; l = s_buckets[b]['losses']
    total = w + l
    rate = 100 * w / total if total else 0
    print(f'  gap {b:<12}  fav-arm won: {w:>3}-{l:<3}  ({rate:5.1f}%)  n={total}')

print('\nTEST 1b: xERA gap → fav-arm ML win rate (comparison)')
x_buckets = test_gap_predicts_ml(joined, 'xera')
for b in sorted(x_buckets.keys()):
    w = x_buckets[b]['wins']; l = x_buckets[b]['losses']
    total = w + l
    rate = 100 * w / total if total else 0
    print(f'  gap {b:<12}  fav-arm won: {w:>3}-{l:<3}  ({rate:5.1f}%)  n={total}')

# ============================================================
# TEST 2: Total prediction — does SIERA/xERA gap correlate with OVER?
# Big gap = mismatch = LOW-scoring team gets shut out → total UNDER
# ============================================================
def test_gap_predicts_total(rows, gap_key):
    """Big gap → UNDER hypothesis (one team scoreless)."""
    buckets = defaultdict(lambda: {'over':0,'under':0,'push':0})
    for r in rows:
        h_val = r.get(f'h_{gap_key}')
        a_val = r.get(f'a_{gap_key}')
        if h_val is None or a_val is None: continue
        gap = abs(h_val - a_val)
        if gap < 0.25: b = '0.00-0.24'
        elif gap < 0.50: b = '0.25-0.49'
        elif gap < 0.75: b = '0.50-0.74'
        elif gap < 1.00: b = '0.75-0.99'
        elif gap < 1.50: b = '1.00-1.49'
        else: b = '1.50+'
        tr = r.get('total_result')
        if tr == 'over': buckets[b]['over'] += 1
        elif tr == 'under': buckets[b]['under'] += 1
        elif tr == 'push': buckets[b]['push'] += 1
    return buckets

print('\n' + '='*80)
print('TEST 2: SIERA gap → total OVER/UNDER (bigger gap = more UNDER?)')
print('='*80)
s_totals = test_gap_predicts_total(joined, 'siera')
for b in sorted(s_totals.keys()):
    o = s_totals[b]['over']; u = s_totals[b]['under']; p = s_totals[b]['push']
    total = o + u
    over_rate = 100 * o / total if total else 0
    print(f'  gap {b:<12}  O:{o:>3}  U:{u:<3}  (OVER {over_rate:5.1f}%)  n={total}')

print('\nTEST 2b: xERA gap → total OVER/UNDER (comparison)')
x_totals = test_gap_predicts_total(joined, 'xera')
for b in sorted(x_totals.keys()):
    o = x_totals[b]['over']; u = x_totals[b]['under']; p = x_totals[b]['push']
    total = o + u
    over_rate = 100 * o / total if total else 0
    print(f'  gap {b:<12}  O:{o:>3}  U:{u:<3}  (OVER {over_rate:5.1f}%)  n={total}')

# ============================================================
# TEST 3: Ace duel — both starters SIERA ≤ 3.00 → UNDER?
# ============================================================
print('\n' + '='*80)
print('TEST 3: ACE DUEL (both SIERA ≤ 3.00) → UNDER?')
print('='*80)
ace_games = [r for r in joined if r.get('h_siera') is not None and r.get('a_siera') is not None
             and r['h_siera'] <= 3.0 and r['a_siera'] <= 3.0]
o = sum(1 for r in ace_games if r.get('total_result') == 'over')
u = sum(1 for r in ace_games if r.get('total_result') == 'under')
p = sum(1 for r in ace_games if r.get('total_result') == 'push')
total = o + u
print(f'  Both SIERA≤3.00: O:{o} U:{u} P:{p}  UNDER rate: {100*u/total:.1f}%  n={total}')

# ACE DUEL via xERA
print('\nTEST 3b: xERA both ≤ 3.00 → UNDER? (comparison)')
ace_x = [r for r in joined if r.get('h_xera') is not None and r.get('a_xera') is not None
         and r['h_xera'] <= 3.0 and r['a_xera'] <= 3.0]
o = sum(1 for r in ace_x if r.get('total_result') == 'over')
u = sum(1 for r in ace_x if r.get('total_result') == 'under')
total = o + u
print(f'  Both xERA≤3.00: O:{o} U:{u}  UNDER rate: {100*u/total:.1f}%  n={total}')

# Overall correlation between SIERA gap and xERA gap
print('\n' + '='*80)
print('SIERA vs xERA CORRELATION check')
print('='*80)
both = [r for r in joined if r.get('h_siera') is not None and r.get('a_siera') is not None
        and r.get('h_xera') is not None and r.get('a_xera') is not None]
same_dir = sum(1 for r in both if (r['h_siera']-r['a_siera'])*(r['h_xera']-r['a_xera']) > 0)
opposite = sum(1 for r in both if (r['h_siera']-r['a_siera'])*(r['h_xera']-r['a_xera']) < 0)
print(f'  Games where SIERA and xERA agree on which side has better arm: {same_dir}/{len(both)} ({100*same_dir/len(both):.1f}%)')
print(f'  Games where SIERA and xERA disagree: {opposite}/{len(both)} ({100*opposite/len(both):.1f}%)')
