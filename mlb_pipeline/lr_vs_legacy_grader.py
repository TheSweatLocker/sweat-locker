"""Daily grader: LR predictions vs legacy scorer outcomes.

For each resolved game/prop with both a LR shadow prediction and a
legacy pick preserved, computes:
  - Did LR agree or disagree with legacy?
  - Who was right?
  - Cumulative W-L per system, per sport, per market
  - ROI at -110 assumed juice

Writes summary rows to `lr_vs_legacy_grades` table (creates if missing
via first upsert). Run nightly after resolve_game_results.

USAGE:
    python lr_vs_legacy_grader.py                # yesterday
    python lr_vs_legacy_grader.py --date 2026-09-02
    python lr_vs_legacy_grader.py --days 7       # last 7 days
    python lr_vs_legacy_grader.py --print-only   # no DB write
"""
import argparse, os, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())
SB = os.environ['SUPABASE_URL']; K = os.environ['SUPABASE_KEY']
H_R = {'apikey': K, 'Authorization': f'Bearer {K}'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


def _ml_won(pp, res, sport='MLB'):
    """Did the ML pick win? True/False, or None if can't grade."""
    if not res: return None
    side = str(pp.get('side', '')).upper()
    home_win = res.get('home_win')
    if home_win is None: return None
    return (home_win and side == 'HOME') or (not home_win and side == 'AWAY')


def _total_won(pp, res):
    side = str(pp.get('side', '')).upper()
    tot = res.get('total_runs') if 'total_runs' in res else (res.get('total_points') or (res.get('home_score', 0) + res.get('away_score', 0)))
    line = res.get('close_total')
    if tot is None or line is None: return None
    try:
        return (float(tot) > float(line)) == (side == 'OVER')
    except (TypeError, ValueError):
        return None


def grade_mlb_ml(date_str: str, print_only: bool = False) -> dict:
    """For each MLB game with an ML pick + resolved result, grade LR vs legacy."""
    # Pull game contexts + results for the date
    r = requests.get(f'{SB}/rest/v1/mlb_game_context',
        params={'select': 'game_id,primary_play', 'game_date': f'eq.{date_str}', 'limit': 40},
        headers=H_R, timeout=15)
    ctxs = {g['game_id']: g.get('primary_play') for g in (r.json() or [])
            if isinstance(g.get('primary_play'), dict)}
    r = requests.get(f'{SB}/rest/v1/mlb_game_results',
        params={'select': 'game_id,home_win,total_runs,close_total,close_spread',
                'game_date': f'eq.{date_str}', 'home_score': 'not.is.null'},
        headers=H_R, timeout=15)
    results = {g['game_id']: g for g in (r.json() or [])}

    tallies = {'lr_w': 0, 'lr_l': 0, 'leg_w': 0, 'leg_l': 0, 'agree': 0, 'disagree': 0,
               'lr_active': 0, 'leg_active': 0}
    detail = []
    for gid, pp in ctxs.items():
        res = results.get(gid)
        if not res: continue
        # Only grade ML market
        if str(pp.get('type', '')).lower() != 'ml': continue
        # LR pick (from _lr_p_home_win or shadow)
        lr_p = pp.get('_lr_p_home_win')
        if lr_p is None:
            lr_shadow = pp.get('_lr_ml_shadow') or {}
            lr_p = lr_shadow.get('p_home_win')
        if lr_p is None: continue  # no LR prediction
        # LR pick side
        try: lr_p_val = float(lr_p)
        except (TypeError, ValueError): continue
        if 0.45 <= lr_p_val < 0.55: continue  # LR PASS — no bet
        lr_side = 'HOME' if lr_p_val >= 0.55 else 'AWAY'
        # Grade LR
        lr_won = (res.get('home_win') and lr_side == 'HOME') or (not res.get('home_win') and lr_side == 'AWAY')
        # Legacy pick side (may have been overridden — preserve was in _pre_lr)
        pre = pp.get('_pre_lr') or {}
        legacy_side = str(pre.get('side') or pp.get('side') or '').upper()
        if not legacy_side or legacy_side not in ('HOME', 'AWAY'): continue
        legacy_won = (res.get('home_win') and legacy_side == 'HOME') or (not res.get('home_win') and legacy_side == 'AWAY')

        tallies['lr_active'] += 1
        tallies['leg_active'] += 1
        if lr_won: tallies['lr_w'] += 1
        else:      tallies['lr_l'] += 1
        if legacy_won: tallies['leg_w'] += 1
        else:          tallies['leg_l'] += 1
        agree = (lr_side == legacy_side)
        if agree: tallies['agree'] += 1
        else:     tallies['disagree'] += 1
        detail.append({'gid': gid, 'lr_side': lr_side, 'legacy_side': legacy_side,
                       'winner': 'HOME' if res.get('home_win') else 'AWAY',
                       'agree': agree, 'lr_won': lr_won, 'legacy_won': legacy_won})
    return {'tallies': tallies, 'detail': detail}


def grade_mlb_props(date_str: str) -> dict:
    """For each MLB prop with LR shadow + resolved result, grade LR vs legacy."""
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
        params={'select': 'game_id,player_name,prop_type,direction,tier,conviction,signals,result',
                'game_date': f'eq.{date_str}', 'resolved_at': 'not.is.null',
                'result': 'in.(Win,Loss)', 'limit': 500},
        headers=H_R, timeout=20)
    rows = r.json() or []
    tallies = {'lr_w': 0, 'lr_l': 0, 'leg_w': 0, 'leg_l': 0, 'agree_win': 0,
               'agree_loss': 0, 'lr_only_w': 0, 'lr_only_l': 0,
               'leg_only_w': 0, 'leg_only_l': 0}
    for row in rows:
        sigs = row.get('signals') or {}
        if not isinstance(sigs, dict): continue
        lr_tier_raw = sigs.get('_lr_tier_raw')  # what LR thought (PRIME/FADE/COIN etc.)
        lr_p = sigs.get('_lr_p_hit')
        pre_lr_tier = sigs.get('_pre_lr_tier')  # legacy tier before override
        cur_tier = row.get('tier')
        won = (row.get('result') == 'Win')

        # LR "actionable" = PRIME/STRONG/LEAN (LR predicts side hits)
        lr_active = lr_tier_raw in ('PRIME', 'STRONG', 'LEAN')
        # Legacy actionable = PRIME/STRONG/LEAN
        leg_active = (pre_lr_tier or '').upper() in ('PRIME', 'STRONG', 'LEAN')

        if lr_active:
            if won: tallies['lr_w'] += 1
            else: tallies['lr_l'] += 1
        if leg_active:
            if won: tallies['leg_w'] += 1
            else: tallies['leg_l'] += 1
        if lr_active and leg_active:
            if won: tallies['agree_win'] += 1
            else: tallies['agree_loss'] += 1
        elif lr_active and not leg_active:
            if won: tallies['lr_only_w'] += 1
            else: tallies['lr_only_l'] += 1
        elif leg_active and not lr_active:
            if won: tallies['leg_only_w'] += 1
            else: tallies['leg_only_l'] += 1
    return {'tallies': tallies}


def run(date_str: str = None, days: int = 1, print_only: bool = True):
    if date_str is None:
        date_str = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    dates = [(datetime.fromisoformat(date_str).date() - timedelta(days=i)).isoformat()
             for i in range(days)]

    print(f'== LR vs LEGACY GRADER ({days}d ending {date_str}) ==\n')

    for d in dates:
        print(f'--- {d} ---')
        ml_res = grade_mlb_ml(d)
        prop_res = grade_mlb_props(d)
        mt = ml_res['tallies']
        pt = prop_res['tallies']
        print(f'  MLB ML: LR {mt["lr_w"]}-{mt["lr_l"]}  Legacy {mt["leg_w"]}-{mt["leg_l"]}  '
              f'agree={mt["agree"]}/disagree={mt["disagree"]}  active={mt["lr_active"]}')
        print(f'  MLB Props (LR active OR legacy active):')
        print(f'    Both active: {pt["agree_win"]}-{pt["agree_loss"]}')
        print(f'    LR-only:     {pt["lr_only_w"]}-{pt["lr_only_l"]}  (LR promoted, legacy would SKIP)')
        print(f'    Legacy-only: {pt["leg_only_w"]}-{pt["leg_only_l"]}  (legacy PRIME/STRONG, LR would SKIP)')
        print(f'    LR total: {pt["lr_w"]}-{pt["lr_l"]}   Legacy total: {pt["leg_w"]}-{pt["leg_l"]}')
        print()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--days', type=int, default=1)
    ap.add_argument('--print-only', action='store_true')
    args = ap.parse_args()
    run(date_str=args.date, days=args.days, print_only=args.print_only)
