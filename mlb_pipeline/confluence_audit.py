"""Nightly confluence audit — cross-check Jerry picks against
independent signals (2026-08-07).

Pulls the 4 signals that emerged from tonight's manual analysis session
and produces a unified per-game view:
  1. JERRY pick (call_market, call_side, call_line, conviction)
  2. LINE MOVEMENT (open → close, direction, magnitude)
  3. SHARP $ (oddscrowd_snapshot bets%/money% divergence)
  4. HISTORICAL similar games (k-NN retrieval + outcome summary)

Then flags games where:
  ✓ ALIGNED — all signals agree (higher confidence)
  ⚠ MIXED — signals disagree (warning — analyst attention)
  · SILENT — insufficient data on one or more channels

Sport-universal — pass --sport MLB / NFL / NCAAF / etc. once those
sports have all four data streams populated.

Runs as a diagnostic, not a decision maker. Output feeds analyst
judgment; nothing writes to Jerry or the sweat card. If a pattern
emerges (e.g. "3-signal warning stack loses 70% of the time"),
Phase C would wire that pattern into a conviction adjustment.

Usage:
    python confluence_audit.py [--date YYYY-MM-DD] [--sport MLB]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

from similar_games import find_similar, load_history
from pipeline_consistency import check_pick_vs_model


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _tier_str(conv):
    if not conv: return '-'
    if conv >= 80: return 'PRIME'
    if conv >= 65: return 'STRONG'
    if conv >= 50: return 'LEAN'
    return 'READ'


def line_movement_signal(g, market):
    """Returns dict {direction: 'up'|'down'|'flat', magnitude: float, note: str}."""
    if market == 'total':
        o, c = g.get('open_total'), g.get('close_total') or g.get('current_total')
    elif market in ('spread', 'rl'):
        o, c = g.get('open_spread'), g.get('close_spread')
    elif market == 'ml':
        o, c = g.get('home_ml_open'), g.get('home_ml_close') or g.get('home_ml_odds')
    else:
        return {'direction': None, 'magnitude': 0, 'note': '-'}
    if o is None or c is None:
        return {'direction': None, 'magnitude': 0, 'note': 'n/a'}
    try:
        d = float(c) - float(o)
    except (TypeError, ValueError):
        return {'direction': None, 'magnitude': 0, 'note': 'parse'}
    if abs(d) < 0.01:
        return {'direction': 'flat', 'magnitude': 0, 'note': f'{c} flat'}
    return {'direction': 'up' if d > 0 else 'down',
            'magnitude': round(d, 2),
            'note': f'{o} → {c} ({d:+.1f})'}


def sharp_money_signal(g, market):
    """Extract sharp-money signal from oddscrowd_snapshot for a market.
    Returns dict {side, money_pct, bets_pct, div, strength}."""
    oc = g.get('oddscrowd_snapshot')
    if not isinstance(oc, dict): return {'side': None, 'div': None}
    m = oc.get(market) if market in ('ml', 'rl', 'total') else None
    if not isinstance(m, dict): return {'side': None, 'div': None}
    div = m.get('div')
    if not isinstance(div, (int, float)): return {'side': None, 'div': None}
    strength = 'STRONG' if abs(div) >= 25 else 'MED' if abs(div) >= 15 else 'WEAK'
    return {
        'side': m.get('pick'),
        'money_pct': m.get('money'),
        'bets_pct': m.get('bets'),
        'div': div,
        'strength': strength,
    }


def _direction_agrees(jerry_side, line_move, market):
    """Does the line move confirm Jerry's pick direction?
    Total: Jerry UNDER + line moved down = agree
    Total: Jerry OVER + line moved up = agree
    ML: Jerry HOME + home_ml moved down (more juice on home) = agree
    ML: Jerry AWAY + home_ml moved up (less juice on home) = agree
    RL/Spread: similar to ML."""
    if not jerry_side or not line_move.get('direction') or line_move['direction'] == 'flat':
        return None
    js = jerry_side.upper()
    d = line_move['direction']
    if market == 'total':
        if js == 'OVER' and d == 'up': return True
        if js == 'UNDER' and d == 'down': return True
        if (js == 'OVER' and d == 'down') or (js == 'UNDER' and d == 'up'): return False
    elif market == 'ml':
        # home_ml down (more negative) = home stronger, home_ml up = home weaker
        if js == 'HOME' and d == 'down': return True
        if js == 'AWAY' and d == 'up': return True
        if (js == 'HOME' and d == 'up') or (js == 'AWAY' and d == 'down'): return False
    return None


def _sharp_agrees(jerry_side, sharp):
    if not jerry_side or not sharp.get('side'):
        return None
    return jerry_side.upper() == sharp['side'].upper()


def _historical_agrees(jerry_side, sim_summary, market):
    """Does the majority historical outcome agree with Jerry's pick?"""
    if not jerry_side or not sim_summary: return None
    js = jerry_side.upper()
    if market == 'ml':
        pct = sim_summary.get('ml_home_pct')
        if pct is None: return None
        if js == 'HOME': return pct > 55
        if js == 'AWAY': return pct < 45
        return None
    if market == 'total':
        pct = sim_summary.get('total_over_pct')
        if pct is None: return None
        if js == 'OVER': return pct > 55
        if js == 'UNDER': return pct < 45
        return None
    return None


def audit_slate(date_str: str, sport: str = 'MLB'):
    print(f'=== confluence_audit · {sport} · {date_str} ===')

    # 1. Jerry reads for date
    jr = requests.get(
        f'{SB}/rest/v1/jerry_reads',
        headers=H,
        params={
            'game_date': f'eq.{date_str}',
            'sport': f'eq.{sport}',
            'order': 'conviction.desc',
            'select': 'game_id,call_market,call_side,call_line,call_text,conviction',
        }, timeout=15).json()
    if not jr:
        print('  no jerry reads for this date')
        return
    print(f'  {len(jr)} jerry reads')

    # 2. Game context (per-game features + line moves + sharp $)
    ids = ','.join(str(r['game_id']) for r in jr if r.get('game_id'))
    ctx = requests.get(
        f'{SB}/rest/v1/mlb_game_context',
        headers=H,
        params={
            'game_id': f'in.({ids})',
            'select': ('game_id,home_team,away_team,close_total,open_total,'
                       'current_total,close_spread,open_spread,home_ml_odds,'
                       'home_ml_close,home_ml_open,oddscrowd_snapshot,'
                       'jerry_pred_total,jerry_pred_spread'),
        }, timeout=15).json()
    games = {g['game_id']: g for g in ctx}

    # 3. Load historical corpus ONCE (avoid re-fetching per game)
    print(f'  loading historical corpus...')
    history = load_history(sport)
    print(f'  {len(history)} historical games loaded')

    # 4. Per-game features for similar-games lookup
    target_ctx = requests.get(
        f'{SB}/rest/v1/mlb_game_context',
        headers=H,
        params={
            'game_id': f'in.({ids})',
            'select': ('game_id,game_date,home_team,away_team,'
                       'close_total,close_spread,park_run_factor,'
                       'temperature,wind_speed,is_dome,'
                       'home_sp_xera,away_sp_xera,'
                       'home_wrc_plus,away_wrc_plus,'
                       'home_bullpen_era,away_bullpen_era'),
        }, timeout=15).json()
    target_map = {g['game_id']: g for g in target_ctx}

    print()
    print(f'{"MATCH":30s}  {"CONV":>4}  {"PICK":15s}  {"LINE":>18}  {"SHARP":>18}  {"HIST(n30)":>16}  {"MDL✓":>5}  CONF')
    print('-' * 138)

    aligned_games = []
    mixed_games = []
    silent_games = []
    inverted_picks = []  # picks where pipeline direction contradicts model

    for row in jr:
        g = games.get(row.get('game_id'), {})
        target = target_map.get(row.get('game_id'))
        away, home = g.get('away_team', '?'), g.get('home_team', '?')
        match = f'{away[:14]:14s} @ {home[:14]:14s}'
        conv = row.get('conviction') or 0
        call_text = row.get('call_text') or row.get('call_side') or '-'
        jerry_side = (row.get('call_side') or '').upper() or None
        # Sometimes call_side is null; parse from call_text
        if not jerry_side:
            for tok in ('OVER', 'UNDER', 'HOME', 'AWAY'):
                if tok in (row.get('call_text') or '').upper():
                    jerry_side = tok; break
        mkt = (row.get('call_market') or '').lower()

        line_sig = line_movement_signal(g, mkt)
        sharp_sig = sharp_money_signal(g, mkt)

        # Similar games (skip if target features unavailable)
        sim_summary = None
        if target and mkt in ('ml', 'total'):
            sim = find_similar(target, k=30, sport=sport, history=history)
            sim_summary = sim.get('outcome_summary')

        # Pipeline vs model direction consistency (GAP 2)
        model_check = check_pick_vs_model(g, mkt, jerry_side, row.get('call_line'))
        if model_check.get('consistent') is False:
            inverted_picks.append((match, model_check['note']))

        # Confluence classification
        line_ok = _direction_agrees(jerry_side, line_sig, mkt)
        sharp_ok = _sharp_agrees(jerry_side, sharp_sig)
        hist_ok = _historical_agrees(jerry_side, sim_summary, mkt)
        agrees = [x for x in (line_ok, sharp_ok, hist_ok) if x is not None]
        agree_count = sum(1 for x in agrees if x)
        disagree_count = sum(1 for x in agrees if not x)

        if len(agrees) == 0:
            conf_label = 'SILENT'
            silent_games.append(match)
        elif agree_count == len(agrees):
            conf_label = f'ALIGNED (+{agree_count})'
            aligned_games.append(match)
        elif disagree_count == len(agrees):
            conf_label = f'OPPOSED (-{disagree_count})'
            mixed_games.append((match, 'all against'))
        else:
            conf_label = f'MIXED ({agree_count}✓ {disagree_count}✗)'
            mixed_games.append((match, f'{agree_count}v{disagree_count}'))

        # Line summary
        line_disp = line_sig.get('note', '-')[:18]
        # Sharp summary
        if sharp_sig.get('side'):
            sharp_disp = f'{sharp_sig["side"]} m{sharp_sig["money_pct"]}/d{sharp_sig["div"]:+d}'[:18]
        else:
            sharp_disp = '-'
        # Hist summary
        if sim_summary:
            if mkt == 'ml':
                hist_disp = f'H{sim_summary.get("ml_home_pct","?")}%'[:16]
            elif mkt == 'total':
                hist_disp = f'O{sim_summary.get("total_over_pct","?")}%'[:16]
            else:
                hist_disp = '-'
        else:
            hist_disp = '-'

        # Model consistency column
        mc = model_check.get('consistent')
        if mc is True: mdl_disp = 'OK'
        elif mc is False: mdl_disp = '⚠INV'
        else: mdl_disp = '-'

        print(f'{match:30s}  {conv:>4}  {call_text[:15]:15s}  {line_disp:>18}  {sharp_disp:>18}  {hist_disp:>16}  {mdl_disp:>5}  {conf_label}')

    print()
    print(f'ALIGNED (all signals agree): {len(aligned_games)}')
    for m in aligned_games:
        print(f'    ✓ {m}')
    print(f'MIXED (signals disagree): {len(mixed_games)}')
    for m, note in mixed_games:
        print(f'    ⚠ {m}  [{note}]')
    print(f'SILENT (insufficient data): {len(silent_games)}')
    if inverted_picks:
        print()
        print(f'🚨 PIPELINE INVERSIONS ({len(inverted_picks)}) — pick direction opposes jerry_pred model:')
        for m, note in inverted_picks:
            print(f'    ⚠ {m}')
            print(f'        {note}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=_today_et())
    ap.add_argument('--sport', default='MLB')
    args = ap.parse_args()
    audit_slate(args.date, args.sport)


if __name__ == '__main__':
    main()
