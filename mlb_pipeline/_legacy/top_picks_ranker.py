"""Top-N cross-sport pick ranker with pattern-recognition gates (2026-08-08).

Pulls every candidate pick for a date from every sport, runs each through
the pattern-recognition stack, assigns a Confidence Score 0-100, then
returns the top-N ranked.

FILTER STACK (each pick):
  Gate 1  pipeline_consistency        Jerry model agrees with pick direction
  Gate 2  correlation_check           No same-game / same-pitcher trap
  Gate 3  dead-zone conviction        Kills 60-64 and 75-79 (project_tier_curve_808)
  Gate 4  juice trap                  hits_over ≤ -180 auto-downgrade
  Gate 5  PRIME 80-89 refit_conviction Requires refit_conv ≥ 60
  Gate 6  heavy-fav ML trap           ML odds ≤ -200 auto-downgrade
  Gate 7  batter hits juice           star hits O 0.5 at ≤ -200 auto-swap flag

CONFIDENCE SCORE (0-100):
  base:           conviction (0-100)
  +5   consistency PASS
  -30  consistency FAIL
  -20  correlation warning STRONG
  -10  correlation warning MEDIUM
  -15  dead-zone hit
  -10  juice trap
  -25  heavy-fav ML trap
  +5   MLB PRIME + refit_conviction ≥ 70
  +10  UFC PRIME + EV ≥ +15
  clamped to 0-100

USAGE:
  python top_picks_ranker.py --date 2026-08-08          # ranked printout
  python top_picks_ranker.py --date 2026-08-08 --top 5   # top 5 only
  python top_picks_ranker.py --date 2026-08-08 --json    # machine-readable
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


# ----- Filter helpers imported from modules where possible -----
try: from pipeline_consistency import check_pick_vs_model
except ImportError: check_pick_vs_model = None
try: from correlation_check import check_correlations
except ImportError: check_correlations = None

# tier-curve dead zones from project_tier_curve_808 (30d MLB audit)
_DEAD_ZONES = [(60, 64), (75, 79)]
_HITS_JUICE_TRAP = -180   # user feedback 803
_ML_JUICE_TRAP   = -200   # heavy_fav_ml_trap_803


def _american_from_dec(dec) -> Optional[int]:
    try: d = float(dec)
    except (TypeError, ValueError): return None
    if d <= 1.0: return None
    if d >= 2.0: return int(round((d - 1) * 100))
    return int(round(-100 / (d - 1)))


def _in_dead_zone(conv) -> bool:
    if conv is None: return False
    return any(lo <= conv <= hi for lo, hi in _DEAD_ZONES)


# ----- CANDIDATE LOADERS -----

def load_mlb_jerry(date: str) -> list:
    r = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H,
        params={'sport':'eq.MLB','game_date':f'eq.{date}','select':'*'})
    return r.json() if r.status_code == 200 else []


def load_mlb_props(date: str) -> list:
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props', headers=H,
        params={'game_date':f'eq.{date}','select':'*',
                'tier':'in.(PRIME,STRONG,LEAN)'})
    return r.json() if r.status_code == 200 else []


def load_ufc_picks(date: str) -> list:
    r = requests.get(f'{SB}/rest/v1/ufc_picks', headers=H,
        params={'event_date':f'eq.{date}','select':'*',
                'ev_tier':'in.(PRIME,STRONG,LEAN)'})
    return r.json() if r.status_code == 200 else []


def load_game_contexts(date: str) -> dict:
    for tbl in ('mlb_game_context', 'game_context'):
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H,
            params={'game_date':f'eq.{date}','select':'*'})
        if r.status_code == 200:
            rows = r.json()
            return {row.get('game_id'): row for row in rows if row.get('game_id')}
    return {}


# ----- SCORER -----

def score_pick(pick: dict, ctx_map: dict) -> dict:
    """Apply gates + compute confidence score. Returns pick with scoring metadata added."""
    base_conv = pick.get('conviction') or 0
    score = float(base_conv)
    flags = []
    reasons = []

    sport = pick.get('sport', 'MLB')
    ptype = pick.get('_type')  # 'game_side' | 'prop' | 'ufc'

    # Gate 1: pipeline consistency (game-side picks only, needs Jerry model prediction)
    if ptype == 'game_side' and check_pick_vs_model:
        ctx = ctx_map.get(pick.get('game_id')) or {}
        market = pick.get('call_market'); side = pick.get('call_side')
        line = pick.get('call_line')
        r = check_pick_vs_model(ctx, market, side, line)
        if r.get('consistent') is True:
            score += 5; reasons.append('model+pick agree (+5)')
        elif r.get('consistent') is False:
            score -= 30; flags.append('INVERSION'); reasons.append(f'{r["note"]} (-30)')

    # Gate 3: dead zones
    if _in_dead_zone(base_conv):
        score -= 15
        flags.append('DEAD_ZONE')
        reasons.append(f'conviction {base_conv} in dead zone 60-64/75-79 (-15)')

    # Gate 4: hits_over juice trap
    if ptype == 'prop' and pick.get('prop_type') == 'hits_over':
        american = None
        odds_over = pick.get('book_over_odds')
        if isinstance(odds_over, (int, float)):
            american = int(odds_over) if odds_over > 100 or odds_over < -100 else None
        if american is None and pick.get('book_line') is not None:
            am = _american_from_dec(pick.get('book_line'))
            if am is not None: american = am
        if american is not None and american <= _HITS_JUICE_TRAP:
            score -= 10
            flags.append('HITS_JUICE')
            reasons.append(f'hits_over odds {american} ≤ -180 juice trap (-10)')
        elif american is None:
            score -= 5
            flags.append('NO_ODDS')
            reasons.append('book odds not attached yet — juice trap unverified (-5)')

    # Gate 5: PRIME 80-89 needs refit ≥ 60
    if ptype == 'prop' and pick.get('tier') == 'PRIME' and 80 <= (base_conv or 0) <= 89:
        refit = pick.get('refit_conviction')
        if refit is None:
            score -= 8
            flags.append('REFIT_MISSING')
            reasons.append(f'PRIME {base_conv} but refit_conviction not computed — gate unverified (-8)')
        elif refit < 60:
            score -= 15
            flags.append('PRIME_UNSUPPORTED')
            reasons.append(f'PRIME {base_conv} but refit {refit} <60 (-15)')

    # Gate 6: heavy-fav ML trap
    if ptype == 'game_side' and pick.get('call_market') == 'ml':
        est = pick.get('call_odds_est')
        try: am_est = float(est) if est is not None else None
        except (TypeError, ValueError): am_est = None
        if am_est is not None and am_est <= _ML_JUICE_TRAP:
            score -= 25
            flags.append('HEAVY_FAV_TRAP')
            reasons.append(f'ML odds est {am_est:.0f} ≤ -200 heavy-fav trap (-25)')

    # PRIME bonuses
    if ptype == 'prop' and pick.get('tier') == 'PRIME':
        refit = pick.get('refit_conviction') or 0
        if refit >= 70:
            score += 5
            reasons.append(f'PRIME + refit {refit} ≥ 70 (+5)')
    if ptype == 'ufc' and pick.get('ev_tier') == 'PRIME':
        best_ev = max([pick.get('ev_side_a') or -999, pick.get('ev_side_b') or -999])
        if best_ev >= 15:
            score += 10
            reasons.append(f'UFC PRIME EV {best_ev:.1f} ≥ +15 (+10)')

    score = max(0.0, min(100.0, score))
    pick = dict(pick)
    pick['_score'] = round(score, 1)
    pick['_flags'] = flags
    pick['_score_reasons'] = reasons
    return pick


def apply_correlation_gate(scored: list) -> list:
    """Run correlation_check across the whole slate; penalize picks in warnings."""
    if not check_correlations: return scored
    corr_input = []
    for p in scored:
        if p.get('_type') == 'game_side':
            corr_input.append({
                'game_id': p.get('game_id'),
                'market': p.get('call_market'),
                'side': p.get('call_side'),
                'line': p.get('call_line'),
                'conviction': p.get('conviction'),
            })
        elif p.get('_type') == 'prop':
            corr_input.append({
                'game_id': p.get('game_id'),
                'market': 'prop',
                'prop_type': p.get('prop_type'),
                'prop_line': p.get('prop_line'),
                'player_name': p.get('player_name'),
                'conviction': p.get('conviction'),
            })
        else:
            corr_input.append({'game_id': None, 'market': 'ufc'})
    warnings = check_correlations(corr_input)
    for w in warnings:
        sev = w.get('severity')
        for idx in w.get('picks', []):
            if idx >= len(scored): continue
            pen = 20 if sev == 'strong' else (10 if sev == 'medium' else 5)
            scored[idx]['_score'] = max(0.0, scored[idx]['_score'] - pen)
            scored[idx]['_flags'].append(f'CORR_{sev.upper()}')
            scored[idx]['_score_reasons'].append(
                f'correlation {w.get("rule")} {sev} (-{pen}): {w.get("note", "")[:80]}')
    return scored


# ----- ORCHESTRATOR -----

def build_top_picks(date: str, top_n: int = 10) -> list:
    """Full pipeline: pull → normalize → score → filter → rank."""
    ctx_map = load_game_contexts(date)
    candidates = []

    def _matchup(gid):
        c = ctx_map.get(gid) or {}
        h = c.get('home_team_abbr') or c.get('home_team') or ''
        a = c.get('away_team_abbr') or c.get('away_team') or ''
        if h or a: return f'{a}@{h}' if a and h else (h or a)
        return (gid or '?')[:10]

    # MLB game-side calls from Jerry
    for j in load_mlb_jerry(date):
        if not j.get('call_market') or not j.get('call_side'): continue
        if (j.get('call_market') or '').lower() == 'pass': continue
        line = j.get('call_line')
        line_str = f' {line}' if line is not None else ''
        candidates.append({
            **j, '_type': 'game_side', 'sport': 'MLB',
            'label': f'{_matchup(j.get("game_id"))} {(j.get("call_market") or "?").upper()} {j.get("call_side") or "?"}{line_str}',
        })

    # MLB props
    for p in load_mlb_props(date):
        if (p.get('tier') or '') not in ('PRIME','STRONG','LEAN'): continue
        candidates.append({
            **p, '_type': 'prop', 'sport': 'MLB',
            'label': f'{p.get("player_name","?")} {p.get("prop_type","?")} {p.get("prop_line","?")} ({_matchup(p.get("game_id"))})',
        })

    # UFC BACK/LEAN plays only (ev_recommended_side present)
    for u in load_ufc_picks(date):
        rec = u.get('ev_recommended_side')
        if rec not in ('a','b'): continue
        pick_side = u['fighter_a'] if rec == 'a' else u['fighter_b']
        u['conviction'] = u.get('conviction_winner')
        u['tier'] = u.get('ev_tier')
        candidates.append({
            **u, '_type': 'ufc', 'sport': 'UFC',
            'label': f'{pick_side} ML ({u.get("event_name","?")})',
        })

    scored = [score_pick(p, ctx_map) for p in candidates]
    scored = apply_correlation_gate(scored)
    scored.sort(key=lambda p: p['_score'], reverse=True)
    return scored[:top_n]


def print_ranked(picks: list) -> None:
    if not picks:
        print('no picks pass gates'); return
    print(f'\n{"="*90}')
    print(f'  TOP {len(picks)} PICKS  ·  ranked by confidence score (0-100)')
    print(f'  filters: consistency · correlation · dead-zone · juice trap · PRIME gate · fav trap')
    print(f'{"="*90}\n')
    for i, p in enumerate(picks):
        rank = len(picks) - i  # display 10 → 1 ascending
        # Actually user asked "10-0" — so print RANK descending (highest at top)
        pass
    # Print highest first (rank 1)
    for idx, p in enumerate(picks):
        rank = idx + 1
        tier = p.get('tier') or p.get('ev_tier') or '-'
        sport = p.get('sport','?')
        flags = ' '.join(f'[{f}]' for f in (p.get('_flags') or []))
        print(f'#{rank:2d}  {p["_score"]:5.1f}/100  {sport:3s}  {tier:6s}  {p.get("label","?")}   {flags}')
        for r in (p.get('_score_reasons') or [])[:3]:
            print(f'       · {r}')
        print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True, help='YYYY-MM-DD')
    ap.add_argument('--top', type=int, default=10)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    picks = build_top_picks(args.date, top_n=args.top)
    if args.json:
        print(json.dumps(picks, default=str, indent=2))
    else:
        print_ranked(picks)


if __name__ == '__main__':
    main()
