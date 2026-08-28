"""Comprehensive ML strategy backtest.

Tests 12+ different ML lean signals across all 2807 resolved games,
evaluating each as if we'd placed every qualifying bet. Reports W-L,
hit rate, and units profit (using closing ML odds for EV math).

Strategies tested (each fires on a game when its condition is met,
returning the side to bet):

  S01  xERA gap ≥1.5     — bet team with better xERA when |gap| ≥ 1.5
  S02  xERA gap ≥2.0
  S03  xERA gap ≥2.5
  S04  xERA gap ≥3.0     — extreme arms, high conviction
  S05  Bullpen ERA gap ≥1.5 + opposing pen workload-loaded (≥9 relievers in 3d)
  S06  L10 hot vs cold   — bet team L10 ≥+0.8 R/G when opp L10 ≤-0.4
  S07  v2 model edge ≥4pt no-vig
  S08  v2 model edge ≥5pt
  S09  v2 model edge ≥7pt — PRIME conviction
  S10  v2 dog-only edge  — model_p_home > market_p_home AND model favors away (or vice versa)
  S11  Plus-money + xERA edge — bet underdog when their starter is meaningfully better
  S12  Framing edge      — bet team with much better catcher framing (≥3 framing run gap)
  S13  First-inn ERA fade — bet team whose opp starter has 1st-inn ERA ≥6 (when their own is ≤4)
  S14  Combined: xERA ≥1.5 AND v2 edge ≥4pt
  S15  Combined: xERA ≥1.5 AND home favorite (home advantage)
  S16  Recency + xERA combined: own team L10 hot AND own pitcher xERA <opp by 1.0+

Each strategy reports:
  - n picks fired
  - W-L record + hit rate
  - Average closing ML price taken (units profit math)
  - EV per 100 units bet (positive = profitable)
  - Break-even threshold given avg odds
"""
import os
import sys
import json
import urllib.parse
import urllib.request
from collections import defaultdict
from dotenv import load_dotenv

import projection_v2 as v2

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def sb_get(table, params):
    qs = urllib.parse.urlencode(params, safe=",.()")
    url = f"{SUPABASE_URL}/rest/v1/{table}?{qs}"
    req = urllib.request.Request(
        url, headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fetch_resolved():
    all_rows = []
    offset = 0
    select_fields = (
        "game_date,home_team,away_team,"
        "home_sp_xera,away_sp_xera,home_sp_name,away_sp_name,"
        "home_sp_k_pct,away_sp_k_pct,"
        "home_runs_per_game,away_runs_per_game,"
        "home_wrc_plus,away_wrc_plus,"
        "home_wrc_vs_opp_hand,away_wrc_vs_opp_hand,"
        "home_first_inning_era,away_first_inning_era,"
        "home_bullpen_era,away_bullpen_era,"
        "home_bp_relievers_3d,away_bp_relievers_3d,"
        "home_team_oaa,away_team_oaa,"
        "home_catcher_framing,away_catcher_framing,"
        "home_pitcher_last_3_era,away_pitcher_last_3_era,"
        "park_run_factor,temperature,"
        "projected_total,projected_spread,"
        "close_total,close_spread,"
        "home_ml_close,away_ml_close,"
        "home_score,away_score,home_win,total_runs,"
        "nrfi_score,nrfi_result,total_result,"
        "home_last5_run_diff,away_last5_run_diff,"
        "signal_confluence_net"
    )
    while True:
        rows = sb_get("mlb_game_results", {
            "home_win": "not.is.null",
            "select": select_fields,
            "order": "game_date.asc",
            "limit": "1000",
            "offset": str(offset),
        })
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000
    return all_rows


def fetch_pitcher_buckets():
    out = {}
    for offset in range(0, 5000, 1000):
        rows = sb_get("mlb_pitcher_stats", {
            "select": "player_name,innings_1_3_era,innings_1_3_ip,innings_4_6_era,innings_7_9_era",
            "limit": "1000",
            "offset": str(offset),
        })
        if not rows:
            break
        for r in rows:
            out[r["player_name"]] = r
        if len(rows) < 1000:
            break
    return out


def fetch_team_buckets():
    out = {}
    rows = sb_get("mlb_team_offense", {
        "select": "team,innings_1_3_runs_per_game,innings_4_6_runs_per_game,innings_7_9_runs_per_game,last10_runs_per_game",
    })
    for r in rows:
        out[r["team"]] = r
    return out


def fetch_bullpen_buckets():
    out = {}
    rows = sb_get("mlb_bullpen_stats", {
        "select": "team,pitching_1_3_era,pitching_4_6_era,pitching_7_9_era",
    })
    for r in rows:
        out[r["team"]] = r
    return out


def _f(v, d=None):
    if v is None:
        return d
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _ml_to_prob(ml):
    if ml is None:
        return None
    if ml < 0:
        return abs(ml) / (abs(ml) + 100)
    return 100 / (ml + 100)


def _ml_payout(ml, stake=100):
    """Return profit if pick wins (excluding stake)."""
    if ml is None:
        return 0
    if ml < 0:
        return stake * 100 / abs(ml)
    return stake * ml / 100


# ============================================================
# STRATEGY DEFINITIONS
# Each takes a game row (and optional v2 projection) and returns:
#   ('home', stake_units) | ('away', stake_units) | None
# ============================================================

def s_xera_gap(threshold):
    def fn(g, proj=None):
        hx = _f(g.get('home_sp_xera')); ax = _f(g.get('away_sp_xera'))
        if hx is None or ax is None: return None
        gap = abs(hx - ax)
        if gap < threshold: return None
        return ('home' if hx < ax else 'away', 1.0)
    return fn


def s_bullpen_workload(threshold):
    def fn(g, proj=None):
        hbp = _f(g.get('home_bullpen_era')); abp = _f(g.get('away_bullpen_era'))
        h_rel = g.get('home_bp_relievers_3d') or 0
        a_rel = g.get('away_bp_relievers_3d') or 0
        if hbp is None or abp is None: return None
        gap = abs(hbp - abp)
        if gap < threshold: return None
        # Bet team with better pen + opp pen gassed (≥9 relievers in 3d)
        if hbp < abp and a_rel >= 9:
            return ('home', 1.0)
        if abp < hbp and h_rel >= 9:
            return ('away', 1.0)
        return None
    return fn


def s_recency_hot_cold():
    def fn(g, proj=None):
        h_rd = _f(g.get('home_last5_run_diff'))
        a_rd = _f(g.get('away_last5_run_diff'))
        if h_rd is None or a_rd is None: return None
        # Bet team with much better recent run diff
        diff = h_rd - a_rd
        if diff >= 8:
            return ('home', 1.0)
        if diff <= -8:
            return ('away', 1.0)
        return None
    return fn


def s_v2_edge(min_edge_pct):
    def fn(g, proj=None):
        if proj is None: return None
        h_ml = _f(g.get('home_ml_close')); a_ml = _f(g.get('away_ml_close'))
        if h_ml is None or a_ml is None: return None
        p_h_raw = _ml_to_prob(h_ml); p_a_raw = _ml_to_prob(a_ml)
        if not p_h_raw or not p_a_raw: return None
        norm = p_h_raw + p_a_raw
        p_h_market = p_h_raw / norm
        edge_h = (proj.p_home_win - p_h_market) * 100
        edge_a = ((1 - proj.p_home_win) - (1 - p_h_market)) * 100
        if edge_h >= min_edge_pct and edge_h >= edge_a:
            return ('home', 1.0)
        if edge_a >= min_edge_pct:
            return ('away', 1.0)
        return None
    return fn


def s_v2_dog_only(min_edge_pct):
    """Only fires when the model's pick is the underdog (positive ML)."""
    def fn(g, proj=None):
        if proj is None: return None
        h_ml = _f(g.get('home_ml_close')); a_ml = _f(g.get('away_ml_close'))
        if h_ml is None or a_ml is None: return None
        p_h_raw = _ml_to_prob(h_ml); p_a_raw = _ml_to_prob(a_ml)
        if not p_h_raw or not p_a_raw: return None
        norm = p_h_raw + p_a_raw
        p_h_market = p_h_raw / norm
        edge_h = (proj.p_home_win - p_h_market) * 100
        edge_a = ((1 - proj.p_home_win) - (1 - p_h_market)) * 100
        # Home dog (home_ml positive) with edge
        if edge_h >= min_edge_pct and h_ml > 0:
            return ('home', 1.0)
        if edge_a >= min_edge_pct and a_ml > 0:
            return ('away', 1.0)
        return None
    return fn


def s_plus_money_xera(min_xera_gap):
    """Bet underdog when underdog's starter is meaningfully better."""
    def fn(g, proj=None):
        hx = _f(g.get('home_sp_xera')); ax = _f(g.get('away_sp_xera'))
        h_ml = _f(g.get('home_ml_close')); a_ml = _f(g.get('away_ml_close'))
        if any(v is None for v in (hx, ax, h_ml, a_ml)): return None
        # Home dog with better starter
        if h_ml > 0 and hx < ax and (ax - hx) >= min_xera_gap:
            return ('home', 1.0)
        # Away dog with better starter
        if a_ml > 0 and ax < hx and (hx - ax) >= min_xera_gap:
            return ('away', 1.0)
        return None
    return fn


def s_framing_edge():
    def fn(g, proj=None):
        hf = _f(g.get('home_catcher_framing'))
        af = _f(g.get('away_catcher_framing'))
        if hf is None or af is None: return None
        gap = hf - af
        # Better framing catcher = pitcher gets more called strikes
        if gap >= 3:
            return ('home', 1.0)
        if gap <= -3:
            return ('away', 1.0)
        return None
    return fn


def s_first_inn_fade():
    """Bet team whose opposing starter folds in 1st (≥6 ERA) when own SP holds."""
    def fn(g, proj=None):
        h_fi = _f(g.get('home_first_inning_era'))
        a_fi = _f(g.get('away_first_inning_era'))
        if h_fi is None or a_fi is None: return None
        # Home team: bet them if away starter has bad 1st-inn AND home starter doesn't
        if a_fi >= 6 and h_fi <= 4:
            return ('home', 1.0)
        if h_fi >= 6 and a_fi <= 4:
            return ('away', 1.0)
        return None
    return fn


def s_combined_xera_v2(xera_thr, edge_thr):
    """xERA gap AND v2 edge — both required."""
    def fn(g, proj=None):
        if proj is None: return None
        # xERA gap check
        hx = _f(g.get('home_sp_xera')); ax = _f(g.get('away_sp_xera'))
        if hx is None or ax is None: return None
        gap = abs(hx - ax)
        if gap < xera_thr: return None
        xera_side = 'home' if hx < ax else 'away'
        # v2 edge check
        h_ml = _f(g.get('home_ml_close')); a_ml = _f(g.get('away_ml_close'))
        if h_ml is None or a_ml is None: return None
        p_h_raw = _ml_to_prob(h_ml); p_a_raw = _ml_to_prob(a_ml)
        if not p_h_raw or not p_a_raw: return None
        norm = p_h_raw + p_a_raw
        p_h_market = p_h_raw / norm
        edge_h = (proj.p_home_win - p_h_market) * 100
        edge_a = ((1 - proj.p_home_win) - (1 - p_h_market)) * 100
        v2_side = 'home' if edge_h >= edge_a else 'away'
        v2_edge = max(edge_h, edge_a)
        if v2_edge < edge_thr: return None
        # BOTH must agree
        if v2_side != xera_side: return None
        return (xera_side, 1.0)
    return fn


def s_xera_home_fav():
    """Bet home favorite when they have meaningfully better starter."""
    def fn(g, proj=None):
        hx = _f(g.get('home_sp_xera')); ax = _f(g.get('away_sp_xera'))
        h_ml = _f(g.get('home_ml_close'))
        if any(v is None for v in (hx, ax, h_ml)): return None
        if h_ml >= 0: return None  # home must be favored (negative ML)
        if hx >= ax: return None    # home starter must be better
        if (ax - hx) < 1.0: return None
        return ('home', 1.0)
    return fn


def s_chalk_stack_fade(min_indicators):
    """Count indicators that agree on one side. If ≥min agree, FADE that side
    (bet the opposite). Tests the 'obvious chalk is overpriced' hypothesis.

    Indicators tracked:
      1. Better xERA (gap ≥0.75)
      2. Better L3 ERA (gap ≥1.0)
      3. Better bullpen ERA (gap ≥0.5)
      4. Better catcher framing (gap ≥2.0 framing runs)
      5. Hot recency (own L5 run diff ≥3 better than opp)
      6. Better 1st-inn ERA (gap ≥2.0)
    """
    def fn(g, proj=None):
        votes_home = 0; votes_away = 0
        # 1. xERA
        hx = _f(g.get('home_sp_xera')); ax = _f(g.get('away_sp_xera'))
        if hx is not None and ax is not None and abs(hx - ax) >= 0.75:
            if hx < ax: votes_home += 1
            else: votes_away += 1
        # 2. L3 ERA
        h_l3 = _f(g.get('home_pitcher_last_3_era')); a_l3 = _f(g.get('away_pitcher_last_3_era'))
        if h_l3 is not None and a_l3 is not None and abs(h_l3 - a_l3) >= 1.0:
            if h_l3 < a_l3: votes_home += 1
            else: votes_away += 1
        # 3. Bullpen
        hbp = _f(g.get('home_bullpen_era')); abp = _f(g.get('away_bullpen_era'))
        if hbp is not None and abp is not None and abs(hbp - abp) >= 0.5:
            if hbp < abp: votes_home += 1
            else: votes_away += 1
        # 4. Catcher framing
        hf = _f(g.get('home_catcher_framing')); af = _f(g.get('away_catcher_framing'))
        if hf is not None and af is not None and abs(hf - af) >= 2.0:
            if hf > af: votes_home += 1
            else: votes_away += 1
        # 5. Recency run diff
        h_rd = _f(g.get('home_last5_run_diff')); a_rd = _f(g.get('away_last5_run_diff'))
        if h_rd is not None and a_rd is not None and abs(h_rd - a_rd) >= 3:
            if h_rd > a_rd: votes_home += 1
            else: votes_away += 1
        # 6. First-inn ERA
        h_fi = _f(g.get('home_first_inning_era')); a_fi = _f(g.get('away_first_inning_era'))
        if h_fi is not None and a_fi is not None and abs(h_fi - a_fi) >= 2.0:
            if h_fi < a_fi: votes_home += 1
            else: votes_away += 1
        # Decide chalk side
        if votes_home >= min_indicators and votes_home > votes_away:
            return ('away', 1.0)  # FADE — bet opposite
        if votes_away >= min_indicators and votes_away > votes_home:
            return ('home', 1.0)  # FADE — bet opposite
        return None
    return fn


def s_chalk_stack_dog_only(min_indicators):
    """Like chalk_stack_fade but only fires when the chalk side is ALSO the
    market favorite. The proper 'fade the chalk' setup."""
    def fn(g, proj=None):
        h_ml = _f(g.get('home_ml_close')); a_ml = _f(g.get('away_ml_close'))
        if h_ml is None or a_ml is None: return None
        votes_home = 0; votes_away = 0
        hx = _f(g.get('home_sp_xera')); ax = _f(g.get('away_sp_xera'))
        if hx is not None and ax is not None and abs(hx - ax) >= 0.75:
            if hx < ax: votes_home += 1
            else: votes_away += 1
        h_l3 = _f(g.get('home_pitcher_last_3_era')); a_l3 = _f(g.get('away_pitcher_last_3_era'))
        if h_l3 is not None and a_l3 is not None and abs(h_l3 - a_l3) >= 1.0:
            if h_l3 < a_l3: votes_home += 1
            else: votes_away += 1
        hbp = _f(g.get('home_bullpen_era')); abp = _f(g.get('away_bullpen_era'))
        if hbp is not None and abp is not None and abs(hbp - abp) >= 0.5:
            if hbp < abp: votes_home += 1
            else: votes_away += 1
        hf = _f(g.get('home_catcher_framing')); af = _f(g.get('away_catcher_framing'))
        if hf is not None and af is not None and abs(hf - af) >= 2.0:
            if hf > af: votes_home += 1
            else: votes_away += 1
        h_rd = _f(g.get('home_last5_run_diff')); a_rd = _f(g.get('away_last5_run_diff'))
        if h_rd is not None and a_rd is not None and abs(h_rd - a_rd) >= 3:
            if h_rd > a_rd: votes_home += 1
            else: votes_away += 1
        h_fi = _f(g.get('home_first_inning_era')); a_fi = _f(g.get('away_first_inning_era'))
        if h_fi is not None and a_fi is not None and abs(h_fi - a_fi) >= 2.0:
            if h_fi < a_fi: votes_home += 1
            else: votes_away += 1
        # Home is chalk (votes side) AND also market favorite
        if votes_home >= min_indicators and votes_home > votes_away and h_ml < 0:
            return ('away', 1.0)  # bet the dog
        if votes_away >= min_indicators and votes_away > votes_home and a_ml < 0:
            return ('home', 1.0)  # bet the dog
        return None
    return fn


def s_v2_vs_chalk_stack(min_edge_pct, min_indicators):
    """v2 model edge AND chalk-stack DISAGREES — the 'two independent sources
    agree on contrarian side' hypothesis. Only fires when:
      - v2 model picks side X with edge ≥ min_edge_pct vs market no-vig
      - Chalk-stack (xERA, L3, BP, framing, recency, 1st-inn) has ≥ min_indicators
        votes on side Y (opposite of X)
    Bets side X at the +odds price the market gave it because chalk anchored Y.
    """
    def fn(g, proj=None):
        if proj is None:
            return None
        h_ml = _f(g.get('home_ml_close')); a_ml = _f(g.get('away_ml_close'))
        if h_ml is None or a_ml is None:
            return None
        p_h_raw = _ml_to_prob(h_ml); p_a_raw = _ml_to_prob(a_ml)
        if not p_h_raw or not p_a_raw:
            return None
        norm = p_h_raw + p_a_raw
        p_h_market = p_h_raw / norm
        edge_h = (proj.p_home_win - p_h_market) * 100
        edge_a = ((1 - proj.p_home_win) - (1 - p_h_market)) * 100
        # v2 pick = whichever side has the bigger edge
        if edge_h >= edge_a:
            v2_side = 'home'
            v2_edge = edge_h
        else:
            v2_side = 'away'
            v2_edge = edge_a
        if v2_edge < min_edge_pct:
            return None
        # Count chalk-stack votes
        votes_home = 0; votes_away = 0
        hx = _f(g.get('home_sp_xera')); ax = _f(g.get('away_sp_xera'))
        if hx is not None and ax is not None and abs(hx - ax) >= 0.75:
            if hx < ax: votes_home += 1
            else: votes_away += 1
        h_l3 = _f(g.get('home_pitcher_last_3_era')); a_l3 = _f(g.get('away_pitcher_last_3_era'))
        if h_l3 is not None and a_l3 is not None and abs(h_l3 - a_l3) >= 1.0:
            if h_l3 < a_l3: votes_home += 1
            else: votes_away += 1
        hbp = _f(g.get('home_bullpen_era')); abp = _f(g.get('away_bullpen_era'))
        if hbp is not None and abp is not None and abs(hbp - abp) >= 0.5:
            if hbp < abp: votes_home += 1
            else: votes_away += 1
        hf = _f(g.get('home_catcher_framing')); af = _f(g.get('away_catcher_framing'))
        if hf is not None and af is not None and abs(hf - af) >= 2.0:
            if hf > af: votes_home += 1
            else: votes_away += 1
        h_rd = _f(g.get('home_last5_run_diff')); a_rd = _f(g.get('away_last5_run_diff'))
        if h_rd is not None and a_rd is not None and abs(h_rd - a_rd) >= 3:
            if h_rd > a_rd: votes_home += 1
            else: votes_away += 1
        h_fi = _f(g.get('home_first_inning_era')); a_fi = _f(g.get('away_first_inning_era'))
        if h_fi is not None and a_fi is not None and abs(h_fi - a_fi) >= 2.0:
            if h_fi < a_fi: votes_home += 1
            else: votes_away += 1
        # Chalk side = whichever has the votes
        if v2_side == 'home':
            chalk_side = 'away' if votes_away >= min_indicators and votes_away > votes_home else None
        else:
            chalk_side = 'home' if votes_home >= min_indicators and votes_home > votes_away else None
        if chalk_side is None:
            return None  # Chalk doesn't disagree with v2 — skip
        # Both signals confirm contrarian play: bet v2's side (which is the dog
        # by chalk consensus)
        return (v2_side, 1.0)
    return fn


def s_recency_xera_combo():
    """Own team hot recency AND own starter better than opp."""
    def fn(g, proj=None):
        h_rd = _f(g.get('home_last5_run_diff'))
        a_rd = _f(g.get('away_last5_run_diff'))
        hx = _f(g.get('home_sp_xera')); ax = _f(g.get('away_sp_xera'))
        if any(v is None for v in (h_rd, a_rd, hx, ax)): return None
        # Home: positive run diff AND better xERA
        if h_rd >= 4 and hx < ax and (ax - hx) >= 1.0:
            return ('home', 1.0)
        # Away
        if a_rd >= 4 and ax < hx and (hx - ax) >= 1.0:
            return ('away', 1.0)
        return None
    return fn


# ============================================================
# RUNNER
# ============================================================

def evaluate_strategy(label, fn, games, projections):
    n = 0; wins = 0; losses = 0
    units_bet = 0.0; units_profit = 0.0
    avg_ml_taken = []
    for g, proj in zip(games, projections):
        result = fn(g, proj)
        if result is None: continue
        side, stake = result
        ml = _f(g.get(f'{side}_ml_close'))
        if ml is None: continue
        won = bool(g.get('home_win')) if side == 'home' else not bool(g.get('home_win'))
        n += 1
        units_bet += stake
        avg_ml_taken.append(ml)
        if won:
            wins += 1
            units_profit += _ml_payout(ml, stake=stake * 100) / 100
        else:
            losses += 1
            units_profit -= stake
    rate = (wins / n * 100) if n else 0
    avg_ml = sum(avg_ml_taken) / len(avg_ml_taken) if avg_ml_taken else 0
    ev_per_100 = (units_profit / units_bet * 100) if units_bet else 0
    return {
        'label': label,
        'n': n,
        'wins': wins,
        'losses': losses,
        'rate': rate,
        'avg_ml': avg_ml,
        'units_profit': units_profit,
        'ev_per_100': ev_per_100,
    }


def main():
    print("Fetching resolved games + lookups...")
    games = fetch_resolved()
    pitcher_buckets = fetch_pitcher_buckets()
    team_buckets = fetch_team_buckets()
    bullpen_buckets = fetch_bullpen_buckets()
    print(f"  {len(games)} games, {len(pitcher_buckets)} pitchers, {len(team_buckets)} teams\n")

    # Pre-compute v2 projections once for all games
    print("Computing v2 projections for all games...")
    projections = []
    for g in games:
        try:
            ctx = dict(g)
            home_p = g.get('home_sp_name'); away_p = g.get('away_sp_name')
            if home_p in pitcher_buckets:
                pb = pitcher_buckets[home_p]
                ctx['home_innings_1_3_era'] = pb.get('innings_1_3_era')
                ctx['home_innings_4_6_era'] = pb.get('innings_4_6_era')
                ctx['home_innings_7_9_era'] = pb.get('innings_7_9_era')
                ctx['home_sp_ip'] = pb.get('innings_1_3_ip', 0) or 0
            if away_p in pitcher_buckets:
                pb = pitcher_buckets[away_p]
                ctx['away_innings_1_3_era'] = pb.get('innings_1_3_era')
                ctx['away_innings_4_6_era'] = pb.get('innings_4_6_era')
                ctx['away_innings_7_9_era'] = pb.get('innings_7_9_era')
                ctx['away_sp_ip'] = pb.get('innings_1_3_ip', 0) or 0
            for team_field, prefix in (('home_team', 'home'), ('away_team', 'away')):
                tn = g.get(team_field)
                if tn in team_buckets:
                    tb = team_buckets[tn]
                    ctx[f'{prefix}_innings_1_3_runs_per_game'] = tb.get('innings_1_3_runs_per_game')
                    ctx[f'{prefix}_innings_4_6_runs_per_game'] = tb.get('innings_4_6_runs_per_game')
                    ctx[f'{prefix}_innings_7_9_runs_per_game'] = tb.get('innings_7_9_runs_per_game')
                    ctx[f'{prefix}_last10_runs_per_game'] = tb.get('last10_runs_per_game')
                if tn in bullpen_buckets:
                    bb = bullpen_buckets[tn]
                    ctx[f'{prefix}_pitching_1_3_era'] = bb.get('pitching_1_3_era')
                    ctx[f'{prefix}_pitching_4_6_era'] = bb.get('pitching_4_6_era')
                    ctx[f'{prefix}_pitching_7_9_era'] = bb.get('pitching_7_9_era')
            projections.append(v2.project_game(ctx))
        except Exception:
            projections.append(None)
    print(f"  {sum(1 for p in projections if p)} projections computed\n")

    strategies = [
        ('S01 xERA gap ≥1.5',           s_xera_gap(1.5)),
        ('S02 xERA gap ≥2.0',           s_xera_gap(2.0)),
        ('S03 xERA gap ≥2.5',           s_xera_gap(2.5)),
        ('S04 xERA gap ≥3.0',           s_xera_gap(3.0)),
        ('S05 BP gap ≥1.5 + workload',  s_bullpen_workload(1.5)),
        ('S06 Recency hot vs cold',     s_recency_hot_cold()),
        ('S07 v2 edge ≥4pt',            s_v2_edge(4)),
        ('S08 v2 edge ≥5pt',            s_v2_edge(5)),
        ('S09 v2 edge ≥7pt',            s_v2_edge(7)),
        ('S10 v2 dog-only edge ≥4pt',   s_v2_dog_only(4)),
        ('S11 Plus-money + xERA ≥1.5',  s_plus_money_xera(1.5)),
        ('S12 Framing edge ≥3 runs',    s_framing_edge()),
        ('S13 First-inn ERA fade',      s_first_inn_fade()),
        ('S14 xERA ≥1.5 AND v2 ≥4pt',   s_combined_xera_v2(1.5, 4)),
        ('S15 xERA ≥2.0 AND v2 ≥4pt',   s_combined_xera_v2(2.0, 4)),
        ('S16 Home fav + xERA ≥1.0',    s_xera_home_fav()),
        ('S17 Recency + xERA combo',    s_recency_xera_combo()),
        ('S18 Chalk-stack fade ≥3 indicators', s_chalk_stack_fade(3)),
        ('S19 Chalk-stack fade ≥4 indicators', s_chalk_stack_fade(4)),
        ('S20 Chalk-stack fade ≥5 indicators', s_chalk_stack_fade(5)),
        ('S21 Dog when chalk stacked ≥3', s_chalk_stack_dog_only(3)),
        ('S22 Dog when chalk stacked ≥4', s_chalk_stack_dog_only(4)),
        ('S23 v2 ≥4pt AND chalk-stack ≥3 disagrees', s_v2_vs_chalk_stack(4, 3)),
        ('S24 v2 ≥4pt AND chalk-stack ≥4 disagrees', s_v2_vs_chalk_stack(4, 4)),
        ('S25 v2 ≥5pt AND chalk-stack ≥3 disagrees', s_v2_vs_chalk_stack(5, 3)),
        ('S26 v2 ≥3pt AND chalk-stack ≥3 disagrees', s_v2_vs_chalk_stack(3, 3)),
    ]

    def invert(fn):
        """Wrap a strategy to bet the OPPOSITE side."""
        def fn2(g, proj=None):
            res = fn(g, proj)
            if res is None: return None
            side, stake = res
            return ('away' if side == 'home' else 'home', stake)
        return fn2

    # Run each strategy in BOTH directions — forward bets the model's pick,
    # reverse bets the opposite side. If a strategy fires 30% W, reverse = 70% W.
    all_results = []
    for label, fn in strategies:
        all_results.append(evaluate_strategy(label + ' [FORWARD]', fn, games, projections))
        all_results.append(evaluate_strategy(label + ' [REVERSE]', invert(fn), games, projections))

    # Sort by EV per 100u
    all_results.sort(key=lambda r: -r['ev_per_100'])

    print(f"{'STRATEGY':50s} {'N':>5s} {'W-L':>10s} {'RATE':>7s} {'AVG ML':>8s} {'PROFIT':>9s} {'EV/100':>8s}")
    print("-" * 110)
    for r in all_results:
        if r['n'] == 0:
            print(f"{r['label']:50s} {'0':>5s}  no fires")
            continue
        record = f"{r['wins']}-{r['losses']}"
        ml = f"{int(r['avg_ml']):+d}" if r['avg_ml'] else '—'
        flag = ' 🔥' if r['ev_per_100'] >= 5 and r['n'] >= 30 else (' ⚠️' if r['n'] < 30 else '')
        print(f"{r['label']:50s} {r['n']:>5d} {record:>10s} {r['rate']:>6.1f}% {ml:>8s} {r['units_profit']:>+8.1f}u {r['ev_per_100']:>+7.1f}%{flag}")

    print()
    print("=== SHIPPABLE STRATEGIES (EV ≥ +3% AND n ≥ 30) ===")
    shippable = [r for r in all_results if r['ev_per_100'] >= 3 and r['n'] >= 30]
    if shippable:
        for r in shippable:
            print(f"  ✅ {r['label']}: {r['wins']}-{r['losses']} ({r['rate']:.1f}%) @ avg ML {int(r['avg_ml']):+d} → +{r['ev_per_100']:.1f}u/100 over {r['n']} picks")
    else:
        print("  ❌ Nothing clears +3% EV at n≥30 in either direction.")


if __name__ == "__main__":
    main()
