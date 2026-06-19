"""CLV (Closing Line Value) audit — measures whether our picks beat the market.

CLV is the gold standard for "are we sharp." When we publish a pick at an
open line and the closing line moves toward our side, we got positive CLV
(the market validates us as sharper than the price we got). Long-run +CLV
on a tier means real edge regardless of win-loss variance.

Mechanics by pick type:
  TOTAL OVER  : CLV = close_total - open_total  (line went UP → market agrees)
  TOTAL UNDER : CLV = open_total - close_total  (line went DOWN → market agrees)
  SPREAD HOME : CLV = open_spread - close_spread  (favorite shortened → with us)
  SPREAD AWAY : CLV = close_spread - open_spread
  ML HOME     : implied_prob(open_home_ml) → close; +CLV if close > open in prob
  ML AWAY     : same with away_ml_open/close

We do NOT publish CLV externally yet. This script is for internal honesty
tracking — "did our STRONG totals beat closing line over the last 60d?"
That's the question we can't answer with W-L alone.

Source tables:
  mlb_game_context  — open_total, close_total, open_spread, close_spread,
                       away_ml_open, away_ml_close, home_ml_open, home_ml_close,
                       over_lean, spread_lean, sweat_tier, primary_play
  mlb_game_results  — final score (just to confirm graded games)
"""

import io
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from urllib.parse import urlencode

from dotenv import load_dotenv

load_dotenv()

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

SU = os.environ.get('SUPABASE_URL')
SK = os.environ.get('SUPABASE_KEY')
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}


def _get(url):
    req = urllib.request.Request(url, headers=H)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def american_to_prob(odds):
    """Convert American odds (e.g. -130, +120) to implied win probability."""
    if odds is None:
        return None
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    if o > 0:
        return 100.0 / (o + 100.0)
    return abs(o) / (abs(o) + 100.0)


def dim_tier(sweat_dimensions, key):
    """Read tier from sweat_dimensions.{key}.tier — the post-5/29 dimensional
    structure. key in ('total','side','prop'). Returns normalized tier label
    (ELITE/PRIME/STRONG/LEAN/LIGHT/PASS/UNTIERED)."""
    if not isinstance(sweat_dimensions, dict):
        return 'UNTIERED'
    block = sweat_dimensions.get(key)
    if not isinstance(block, dict):
        return 'UNTIERED'
    raw = (block.get('tier') or '').upper()
    if not raw:
        return 'UNTIERED'
    # Collapse LIGHT_LEAN / LEAN_LEAN variants → LEAN
    if 'LEAN' in raw and raw != 'LEAN':
        return 'LEAN'
    for t in ('ELITE', 'PRIME', 'STRONG', 'LEAN', 'LIGHT', 'PASS'):
        if t in raw:
            return t
    return raw or 'UNTIERED'


def lean_direction(over_lean, spread_lean, sweat_dimensions=None):
    """Returns dict: side='HOME'|'AWAY'|None, total='OVER'|'UNDER'|None.

    Prefers sweat_dimensions.{side,total}.play.label when present (the new
    dimensional source of truth), falls back to over_lean (bool) /
    spread_lean ('home'/'away')."""
    out = {'total': None, 'side': None}
    if isinstance(sweat_dimensions, dict):
        sb = sweat_dimensions.get('side') or {}
        play = sb.get('play') if isinstance(sb, dict) else None
        if isinstance(play, dict):
            lbl = (play.get('label') or '').upper()
            if 'HOME' in lbl or play.get('side') == 'home':
                out['side'] = 'HOME'
            elif 'AWAY' in lbl or play.get('side') == 'away':
                out['side'] = 'AWAY'
        tb = sweat_dimensions.get('total') or {}
        play = tb.get('play') if isinstance(tb, dict) else None
        if isinstance(play, dict):
            t = (play.get('type') or play.get('label') or '').upper()
            if 'OVER' in t:
                out['total'] = 'OVER'
            elif 'UNDER' in t:
                out['total'] = 'UNDER'
    # Fall back to legacy fields
    if out['total'] is None and over_lean is not None:
        out['total'] = 'OVER' if over_lean else 'UNDER'
    if out['side'] is None and isinstance(spread_lean, str):
        if spread_lean.lower() == 'home':
            out['side'] = 'HOME'
        elif spread_lean.lower() == 'away':
            out['side'] = 'AWAY'
    return out


def clv_total(direction, open_t, close_t):
    """Returns CLV in line units (e.g. +0.5 means market moved 0.5 in our favor)."""
    if direction is None or open_t is None or close_t is None:
        return None
    try:
        d = float(close_t) - float(open_t)
    except (TypeError, ValueError):
        return None
    return d if direction == 'OVER' else -d


def clv_spread(side, open_s, close_s):
    """open_s and close_s are home_spread (e.g. -1.5 home favorite, +1.5 home dog).
    HOME pick: +CLV if home_spread got LESS negative / MORE positive (price got better).
    AWAY pick: +CLV if home_spread got MORE negative / LESS positive."""
    if side is None or open_s is None or close_s is None:
        return None
    try:
        d = float(close_s) - float(open_s)
    except (TypeError, ValueError):
        return None
    return d if side == 'HOME' else -d


def clv_ml(side, away_open, away_close, home_open, home_close):
    """Returns CLV in implied probability points (e.g. +0.04 = +4pp)."""
    if side == 'HOME':
        p_open, p_close = american_to_prob(home_open), american_to_prob(home_close)
    elif side == 'AWAY':
        p_open, p_close = american_to_prob(away_open), american_to_prob(away_close)
    else:
        return None
    if p_open is None or p_close is None:
        return None
    # If close prob > open prob, market moved toward our side after we picked it
    return p_close - p_open


def pull_window(days=60):
    """Pull from mlb_game_results — opening + closing lines + leans + final scores
    all live here permanently (mlb_game_context is wiped daily so it can't
    backstop a historical audit)."""
    today = date.today()
    since = (today - timedelta(days=days)).isoformat()
    qs = urlencode({
        'game_date': f'gte.{since}',
        'select': ('game_id,game_date,home_score,away_score,'
                   'open_total,close_total,open_spread,close_spread,'
                   'away_ml_open,away_ml_close,home_ml_open,home_ml_close,'
                   'over_lean,spread_lean,sweat_dimensions,sweat_score,primary_play'),
        'order': 'game_date.asc',
    })
    rows = _get(f'{SU}/rest/v1/mlb_game_results?{qs}')
    out = []
    for r in rows:
        if r.get('home_score') is None:
            continue
        # Synthesize the "_result" sub-dict the grader expects
        r['_result'] = {'home_score': r['home_score'], 'away_score': r['away_score']}
        out.append(r)
    return out


def grade_pick(game, pick_type):
    """For one game and one pick type ('total'/'side'/'ml'), return:
       {'tier','direction','clv','won','line_open','line_close'}  or None"""
    sd = game.get('sweat_dimensions')
    leans = lean_direction(game.get('over_lean'), game.get('spread_lean'), sd)
    # Tier comes from the dimension matching the pick type
    dim_key = 'total' if pick_type == 'total' else 'side'
    tier = dim_tier(sd, dim_key)
    result = game['_result']
    actual = result['home_score'] + result['away_score']
    home_won = result['home_score'] > result['away_score']

    if pick_type == 'total':
        direction = leans['total']
        if direction is None:
            return None
        clv = clv_total(direction, game.get('open_total'), game.get('close_total'))
        line_close = game.get('close_total')
        if line_close is None or clv is None:
            return None
        if actual == float(line_close):
            won = None  # push
        else:
            actual_dir = 'OVER' if actual > float(line_close) else 'UNDER'
            won = (actual_dir == direction)
        return {
            'tier': tier,
            'direction': direction,
            'clv': clv,
            'won': won,
            'line_open': game.get('open_total'),
            'line_close': line_close,
        }

    if pick_type == 'side':
        side = leans['side']
        if side is None:
            return None
        clv = clv_spread(side, game.get('open_spread'), game.get('close_spread'))
        if clv is None:
            return None
        # Grade against actual margin (home_score - away_score) vs close_spread
        try:
            margin = result['home_score'] - result['away_score']
            close = float(game['close_spread'])
        except (TypeError, ValueError):
            return None
        # home_spread negative = home favorite. HOME covers if margin > -close (i.e. margin + close > 0)
        if side == 'HOME':
            covered = margin + close > 0
            pushed = margin + close == 0
        else:
            covered = margin + close < 0
            pushed = margin + close == 0
        return {
            'tier': tier,
            'direction': side,
            'clv': clv,
            'won': None if pushed else covered,
            'line_open': game.get('open_spread'),
            'line_close': game.get('close_spread'),
        }

    if pick_type == 'ml':
        side = leans['side']
        if side is None:
            return None
        clv = clv_ml(side, game.get('away_ml_open'), game.get('away_ml_close'),
                     game.get('home_ml_open'), game.get('home_ml_close'))
        if clv is None:
            return None
        won = (side == 'HOME' and home_won) or (side == 'AWAY' and not home_won)
        return {
            'tier': tier,
            'direction': side,
            'clv': clv,
            'won': won,
            'line_open': (game.get('home_ml_open') if side == 'HOME'
                          else game.get('away_ml_open')),
            'line_close': (game.get('home_ml_close') if side == 'HOME'
                           else game.get('away_ml_close')),
        }
    return None


def report(games, pick_type, label, unit):
    print()
    print('=' * 88)
    print(f'CLV — {label}')
    print('=' * 88)
    by_tier = defaultdict(list)
    for g in games:
        rec = grade_pick(g, pick_type)
        if rec is not None:
            by_tier[rec['tier']].append(rec)
    if not by_tier:
        print('  (no graded picks in window)')
        return
    tier_order = ['ELITE', 'PRIME', 'STRONG', 'LEAN', 'LIGHT', 'UNTIERED']
    print(f'  {"tier":>10s}  {"n":>4s}  {"W-L":>9s}  {"win%":>5s}  {"mean CLV":>10s}  {"+CLV %":>7s}  {"breakeven?":>12s}')
    for tier in tier_order:
        recs = by_tier.get(tier, [])
        if not recs:
            continue
        n = len(recs)
        wins = sum(1 for r in recs if r['won'] is True)
        losses = sum(1 for r in recs if r['won'] is False)
        denom = wins + losses
        winp = f'{100 * wins / denom:.0f}%' if denom else '-'
        clvs = [r['clv'] for r in recs if r['clv'] is not None]
        mean_clv = sum(clvs) / len(clvs) if clvs else 0
        pos_clv = sum(1 for c in clvs if c > 0)
        pos_pct = f'{100 * pos_clv / len(clvs):.0f}%' if clvs else '-'
        # For totals/spreads breakeven CLV is roughly 0 (vs -110/-110 you need
        # the line to not move against you). For ML, +1pp implied prob is
        # roughly worth +20 cents of price.
        unit_str = f'{mean_clv:+.3f} {unit}'
        breakeven = '✓ sharp' if mean_clv > 0 else ('flat' if mean_clv == 0 else '✗ negative')
        print(f'  {tier:>10s}  {n:>4d}  {wins:>3d}-{losses:<3d}  {winp:>5s}  {unit_str:>10s}  {pos_pct:>7s}  {breakeven:>12s}')


def main():
    WINDOWS = [('30d', 30), ('60d', 60), ('90d', 90)]
    for wlabel, wdays in WINDOWS:
        games = pull_window(days=wdays)
        print('\n' + '#' * 88)
        print(f'# WINDOW: {wlabel} (joined games: {len(games)})')
        print('#' * 88)
        report(games, 'total', f'TOTALS ({wlabel})', 'pts')
        report(games, 'side', f'SPREADS ({wlabel})', 'pts')
        report(games, 'ml', f'MONEYLINE ({wlabel})', 'prob')


if __name__ == '__main__':
    main()
