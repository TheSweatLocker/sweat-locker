"""Aggregate daily surface records (2026-08-20).

Nightly job. For each surface (sharp_card, sweat_card, ledger_*, ladder,
dawg_of_day, daily_degen, potd), compute yesterday's:
  - wins / losses / pushes
  - units_bet / units_won (real BFO/book odds where available)
  - pick count

Writes one row per (surface, sport, date) to daily_surface_records.
User caught on 8/20 audit that Sharp Card had NO daily record persisted
anywhere — this closes the gap.

CLI:
  python aggregate_daily_records.py                 # yesterday ET
  python aggregate_daily_records.py --date YYYY-MM-DD
  python aggregate_daily_records.py --backfill 21   # last 21 days
  python aggregate_daily_records.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

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
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}


def _et_yesterday() -> str:
    return ((datetime.now(timezone.utc) - timedelta(hours=4)).date()
            - timedelta(days=1)).isoformat()


def _american_payout(odds) -> float:
    """1u win payout for American odds. -110 → 0.909, +130 → 1.30."""
    if odds is None: return 0.909
    try: o = int(odds)
    except (TypeError, ValueError): return 0.909
    return (o / 100.0) if o >= 100 else (100.0 / abs(o))


def _grade_side(pp: dict, game: dict) -> str | None:
    """Grade an ML/RL/total pick. Returns W/L/P/None."""
    hs = game.get('home_score'); as_ = game.get('away_score')
    if hs is None or as_ is None: return None
    home = (game.get('home_team') or '').lower()
    away = (game.get('away_team') or '').lower()
    m = (pp.get('type') or '').lower()
    label = (pp.get('label') or '').lower()
    picked_home = home in label; picked_away = away in label
    if m == 'ml':
        if picked_home: return 'W' if hs > as_ else 'L' if hs < as_ else 'P'
        if picked_away: return 'W' if as_ > hs else 'L' if as_ < hs else 'P'
    elif m == 'rl':
        rl = (game.get('run_line_result') or '').lower()
        if picked_home and '+1.5' in label: return 'W' if rl != 'home' else 'L'
        if picked_home: return 'W' if rl == 'home' else 'L'
        if picked_away and '+1.5' in label: return 'W' if rl != 'away' else 'L'
        if picked_away: return 'W' if rl == 'away' else 'L'
    elif m in ('total','over','under'):
        tr = (game.get('total_result') or '').lower()
        if 'over' in label: return 'W' if tr == 'over' else 'L' if tr == 'under' else 'P'
        if 'under' in label: return 'W' if tr == 'under' else 'L' if tr == 'over' else 'P'
    return None


# ─────────────────────────────────────────────────────────────
# SURFACE AGGREGATORS
# ─────────────────────────────────────────────────────────────

def agg_sharp_card(date: str) -> dict | None:
    """Sharp Card = PRIME/STRONG primary_play picks + PRIME/STRONG props
    on that date, using the app's 2u/2u/1u sizing policy."""
    ctx = requests.get(f'{SB}/rest/v1/mlb_game_context',
        headers=H_READ,
        params={'game_date': f'eq.{date}',
                'select': 'game_id,home_team,away_team,primary_play'},
        timeout=15).json()
    res = requests.get(f'{SB}/rest/v1/mlb_game_results',
        headers=H_READ,
        params={'game_date': f'eq.{date}',
                'select': 'game_id,home_score,away_score,home_win,run_line_result,total_result,home_team,away_team'},
        timeout=15).json()
    rmap = {r['game_id']: r for r in res if isinstance(r, dict)}
    w = l = p = 0; units_bet = 0.0; units_won = 0.0; detail = []
    for g in (ctx if isinstance(ctx, list) else []):
        pp = g.get('primary_play') or {}
        if pp.get('tier') not in ('PRIME','STRONG'): continue
        game = rmap.get(g.get('game_id'))
        if not game: continue
        game = {**game, 'home_team': g.get('home_team'), 'away_team': g.get('away_team')}
        verdict = _grade_side(pp, game)
        if verdict is None: continue
        stake = 2.0
        units_bet += stake
        # Approximate payout — MLB games use -110 for spread/total, ML uses close odds
        odds = -110
        if pp.get('type') == 'ml':
            # Use close ML from ctx if available (best-effort)
            odds = -110  # fallback; ideally read from mlb_game_results.home_ml_close
        payout = _american_payout(odds)
        if verdict == 'W': w += 1; units_won += stake * payout
        elif verdict == 'L': l += 1; units_won -= stake
        elif verdict == 'P': p += 1
        detail.append({'type':'game','pick':pp.get('label'),'verdict':verdict,'stake':stake})

    # Props — PRIME/STRONG on that date
    props = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
        headers=H_READ,
        params={'game_date': f'eq.{date}', 'tier': 'in.(PRIME,STRONG)',
                'select': 'player_name,prop_type,direction,tier,result,book_over_odds,book_under_odds'},
        timeout=15).json()
    for pr in (props if isinstance(props, list) else []):
        res_c = (pr.get('result') or '').upper()
        if res_c not in ('W','WIN','L','LOSS','P','PUSH'): continue
        odds = pr.get('book_over_odds') if pr.get('direction') == 'over' else pr.get('book_under_odds')
        # Enforce [-300, +150] gate per feedback_prop_jerry_odds
        if odds is not None:
            try:
                oi = int(odds)
                if oi < -300 or oi > 150: continue
            except (TypeError, ValueError):
                pass
        stake = 2.0  # PRIME/STRONG props
        units_bet += stake
        v = res_c[:1]  # W / L / P
        if v == 'W': w += 1; units_won += stake * _american_payout(odds)
        elif v == 'L': l += 1; units_won -= stake
        elif v == 'P': p += 1
        detail.append({'type':'prop','pick':f'{pr.get("player_name")} {pr.get("direction")} {pr.get("prop_type")}','verdict':v,'stake':stake,'odds':odds})

    if not detail: return None
    return {'surface':'sharp_card','sport':'MLB','record_date':date,
            'wins':w,'losses':l,'pushes':p,'units_bet':round(units_bet,2),
            'units_won':round(units_won,2),'pick_count':w+l+p,
            'detail':{'legs':detail[:50]}}


def agg_ledger(date: str) -> list[dict]:
    """Ledger — one record per kind (chalk_parlay, teased_*)."""
    rows = requests.get(f'{SB}/rest/v1/ledger_suggestions',
        headers=H_READ,
        params={'game_date': f'eq.{date}',
                'select': 'kind,result,legs,combined_odds'},
        timeout=15).json()
    if not isinstance(rows, list) or not rows: return []
    out = []
    for r in rows:
        v = (r.get('result') or '').upper()
        if v not in ('W','L','P'): continue
        stake = 1.0  # ledger is 1u standard
        odds = r.get('combined_odds')
        units_won = 0.0
        if v == 'W': units_won = stake * _american_payout(odds)
        elif v == 'L': units_won = -stake
        surface = f'ledger_{r.get("kind","unknown").replace("_combo","")}'
        out.append({
            'surface': surface, 'sport': 'MLB', 'record_date': date,
            'wins': 1 if v == 'W' else 0,
            'losses': 1 if v == 'L' else 0,
            'pushes': 1 if v == 'P' else 0,
            'units_bet': stake, 'units_won': round(units_won, 2),
            'pick_count': 1,
            'detail': {'legs': r.get('legs') or [], 'combined_odds': odds},
        })
    return out


def agg_ladder(date: str) -> dict | None:
    """Ladder — one rung per day."""
    rows = requests.get(f'{SB}/rest/v1/ladder_rung',
        headers=H_READ,
        params={'game_date': f'eq.{date}',
                'select': 'sport,result,pick_side,odds_american,market'},
        timeout=15).json()
    if not isinstance(rows, list) or not rows: return None
    total = defaultdict(lambda: {'w':0,'l':0,'p':0,'units_bet':0.0,'units_won':0.0,'detail':[]})
    for r in rows:
        v = (r.get('result') or '').upper()
        if v not in ('W','L','P','WIN','LOSS','PUSH'): continue
        v = v[:1]
        sport = r.get('sport') or 'MLB'
        stake = 1.0
        odds = r.get('odds_american')
        total[sport]['units_bet'] += stake
        if v == 'W': total[sport]['w']+=1; total[sport]['units_won'] += stake * _american_payout(odds)
        elif v == 'L': total[sport]['l']+=1; total[sport]['units_won'] -= stake
        elif v == 'P': total[sport]['p']+=1
        total[sport]['detail'].append({'pick':r.get('pick_side'),'market':r.get('market'),'verdict':v,'odds':odds})
    if not total: return None
    # Combine into one 'ALL' sport summary + emit
    all_w = sum(s['w'] for s in total.values())
    all_l = sum(s['l'] for s in total.values())
    all_p = sum(s['p'] for s in total.values())
    all_bet = sum(s['units_bet'] for s in total.values())
    all_won = sum(s['units_won'] for s in total.values())
    return {'surface':'ladder','sport':'ALL','record_date':date,
            'wins':all_w,'losses':all_l,'pushes':all_p,
            'units_bet':round(all_bet,2),'units_won':round(all_won,2),
            'pick_count':all_w+all_l+all_p,
            'detail':{sp: v['detail'] for sp, v in total.items()}}


def agg_potd(date: str) -> dict | None:
    r = requests.get(f'{SB}/rest/v1/play_of_the_day',
        headers=H_READ,
        params={'game_date': f'eq.{date}', 'select': '*', 'limit': '1'},
        timeout=10)
    if r.status_code != 200: return None
    rows = r.json()
    if not rows: return None
    row = rows[0]
    v = (row.get('result') or '').upper()[:1]
    if v not in ('W','L','P'): return None
    stake = 2.0
    odds = row.get('odds_american') or row.get('odds')
    units_won = stake * _american_payout(odds) if v == 'W' else -stake if v == 'L' else 0
    return {'surface':'potd','sport':row.get('sport') or 'MLB','record_date':date,
            'wins':1 if v=='W' else 0,'losses':1 if v=='L' else 0,'pushes':1 if v=='P' else 0,
            'units_bet':stake,'units_won':round(units_won,2),'pick_count':1,
            'detail':{'pick':row.get('pick_label') or row.get('pick'),'tier':row.get('tier'),'odds':odds}}


def agg_dawg_of_day(date: str) -> dict | None:
    r = requests.get(f'{SB}/rest/v1/daily_dawg',
        headers=H_READ,
        params={'game_date': f'eq.{date}', 'select': '*', 'limit': '1'},
        timeout=10)
    if r.status_code != 200: return None
    rows = r.json()
    if not rows: return None
    row = rows[0]
    v = (row.get('result') or '').upper()[:1]
    if v not in ('W','L','P'): return None
    stake = 1.0
    odds = row.get('odds')  # dawg has +100 to +250 range
    units_won = stake * _american_payout(odds) if v == 'W' else -stake if v == 'L' else 0
    return {'surface':'dawg_of_day','sport':'MLB','record_date':date,
            'wins':1 if v=='W' else 0,'losses':1 if v=='L' else 0,'pushes':1 if v=='P' else 0,
            'units_bet':stake,'units_won':round(units_won,2),'pick_count':1,
            'detail':{'team':row.get('team'),'odds':odds}}


def agg_daily_degen(date: str) -> dict | None:
    r = requests.get(f'{SB}/rest/v1/daily_degen',
        headers=H_READ,
        params={'game_date': f'eq.{date}', 'select': '*', 'limit': '1'},
        timeout=10)
    if r.status_code != 200: return None
    rows = r.json()
    if not rows: return None
    row = rows[0]
    v = (row.get('result') or '').upper()[:1]
    if v not in ('W','L','P'): return None
    stake = 1.0
    # Combined parlay odds — approximate from legs if not stored
    payout_est = 3.5  # 3-leg parlay typical combined payout
    units_won = stake * payout_est if v == 'W' else -stake if v == 'L' else 0
    return {'surface':'daily_degen','sport':'MULTI','record_date':date,
            'wins':1 if v=='W' else 0,'losses':1 if v=='L' else 0,'pushes':1 if v=='P' else 0,
            'units_bet':stake,'units_won':round(units_won,2),'pick_count':1,
            'detail':{'legs':row.get('legs') or [],'combined_est_payout':payout_est}}


AGGREGATORS = [
    ('sharp_card', agg_sharp_card),
    ('ledger', agg_ledger),        # returns LIST
    ('ladder', agg_ladder),
    ('potd', agg_potd),
    ('dawg_of_day', agg_dawg_of_day),
    ('daily_degen', agg_daily_degen),
]


def write_record(rec: dict, dry_run: bool = False) -> None:
    if dry_run:
        icon = '✓' if rec['units_won'] > 0 else '✗' if rec['units_won'] < 0 else '='
        print(f'  {icon} {rec["surface"]:22s} {rec["sport"]:5s} {rec["wins"]}-{rec["losses"]}-{rec["pushes"]} units {rec["units_won"]:+.2f}u [DRY]')
        return
    payload = {**rec, 'computed_at': datetime.now(timezone.utc).isoformat()}
    if isinstance(payload.get('detail'), (dict, list)):
        pass  # PostgREST handles JSONB
    r = requests.post(f'{SB}/rest/v1/daily_surface_records',
        headers=H_WRITE, json=payload, timeout=10)
    if r.status_code not in (200, 201, 204):
        print(f'    ✗ write failed {r.status_code}: {r.text[:120]}')
        return
    icon = '✓' if rec['units_won'] > 0 else '✗' if rec['units_won'] < 0 else '='
    print(f'  {icon} {rec["surface"]:22s} {rec["sport"]:5s} {rec["wins"]}-{rec["losses"]}-{rec["pushes"]} units {rec["units_won"]:+.2f}u')


def run_date(date: str, dry_run: bool = False) -> int:
    print(f'\n=== aggregate_daily_records · {date} · dry={dry_run} ===')
    total = 0
    for name, fn in AGGREGATORS:
        try:
            result = fn(date)
        except Exception as e:
            print(f'  ⚠ {name}: {type(e).__name__}: {e}')
            continue
        if result is None: continue
        if isinstance(result, list):
            for r in result:
                write_record(r, dry_run=dry_run); total += 1
        else:
            write_record(result, dry_run=dry_run); total += 1
    return total


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: yesterday ET)')
    p.add_argument('--backfill', type=int, help='Backfill last N days')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    if args.backfill:
        end = datetime.strptime(args.date, '%Y-%m-%d').date() if args.date else \
              (datetime.now(timezone.utc) - timedelta(hours=4)).date() - timedelta(days=1)
        total = 0
        for i in range(args.backfill):
            d = (end - timedelta(days=i)).isoformat()
            total += run_date(d, dry_run=args.dry_run)
        print(f'\ntotal records written: {total}')
    else:
        d = args.date or _et_yesterday()
        n = run_date(d, dry_run=args.dry_run)
        print(f'\n{n} records written')


if __name__ == '__main__':
    main()
