"""LR dissent audit — nightly logger for LR-vs-ensemble disagreements.

For every MLB game where the LR predictor disagreed with the ensemble
top pick, record which side won. Feeds lr_dissent_calibration (migration
20260904c) which powers the decision on whether to loosen or tighten
the consensus-dissent gate.

Two disagreement modes we log:

    override — LR overrode the ensemble.
      primary_play._engine == 'lr_v1'
      primary_play._pre_lr has the original ensemble pick
      → shipped side = LR's side, LR won iff shipped won

    blocked — LR was blocked by the consensus-dissent gate.
      primary_play.audit_note LIKE '%LR dissented%'
      primary_play._lr_ml_shadow has LR's suggested side
      → shipped side = ensemble side, LR side = shadow.suggested_side

Usage:
    python mlb_lr_dissent_audit.py                 # yesterday + last 14d re-grade
    python mlb_lr_dissent_audit.py --date 2026-09-03
    python mlb_lr_dissent_audit.py --backfill 30  # last 30 days
    python mlb_lr_dissent_audit.py --show          # print current rollup, no writes

Runs after mlb_pipeline.yml grades the day's game results, so
mlb_game_results.home_win is populated by the time this reads.
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


def _fetch_games(dates: list[str]) -> list[dict]:
    """Pull mlb_game_context rows where LR either overrode OR was blocked."""
    or_filter = 'or=(primary_play->>_engine.eq.lr_v1,primary_play->>audit_note.like.*LR dissented*)'
    date_filter = 'game_date=in.(' + ','.join(dates) + ')'
    url = (f'{SB}/rest/v1/mlb_game_context?'
           f'select=game_id,game_date,home_team,away_team,primary_play&'
           f'{date_filter}&{or_filter}&limit=1000')
    r = requests.get(url, headers=H_READ, timeout=20)
    if r.status_code != 200: return []
    data = r.json()
    return data if isinstance(data, list) else []


def _fetch_results(dates: list[str]) -> dict:
    """Return {game_id: {'home_win', 'total_result', 'total_runs', 'close_total'}}.
    Enough to grade both ML (winning_side) and total (OVER/UNDER)."""
    date_filter = 'game_date=in.(' + ','.join(dates) + ')'
    url = (f'{SB}/rest/v1/mlb_game_results?'
           f'select=game_id,home_win,home_score,away_score,total_result,total_runs,close_total&'
           f'{date_filter}&limit=1000')
    r = requests.get(url, headers=H_READ, timeout=20)
    if r.status_code != 200: return {}
    out = {}
    for row in r.json() or []:
        hw = row.get('home_win')
        hs, aw = row.get('home_score'), row.get('away_score')
        if hw is None and hs is not None and aw is not None:
            if hs > aw: hw = True
            elif aw > hs: hw = False
        # Total grading — prefer explicit total_result, else compute from score
        tr = (row.get('total_result') or '').upper() or None
        if not tr:
            tot = row.get('total_runs')
            if tot is None and hs is not None and aw is not None:
                tot = hs + aw
            line = row.get('close_total')
            if tot is not None and line is not None:
                if tot > line: tr = 'OVER'
                elif tot < line: tr = 'UNDER'
                else: tr = 'PUSH'
        out[row['game_id']] = {'home_win': hw, 'total_result': tr}
    return out


def _extract_dissent(pp: dict) -> Optional[dict]:
    """Decide if this pick is a dissent case; return the delta dict.

    Includes market so the grader knows whether to compare against
    home_win (ml) or total_result (total). Only ML has the consensus-
    dissent gate wired today; total 'blocked' cases don't exist yet
    but we log override rows for both markets."""
    if not isinstance(pp, dict): return None
    engine = pp.get('_engine')
    audit = pp.get('audit_note') or ''
    shadow = pp.get('_lr_ml_shadow') or {}
    pre_lr = pp.get('_pre_lr') or {}
    ensemble = pp.get('_ensemble_sources') or []
    shipped_side_full = pp.get('side')          # HOME/AWAY  (ml)  OR  OVER/UNDER  (total)
    market = (pp.get('type') or '').lower()      # 'ml' | 'total' | 'rl'

    # Only ml + total for now — rl not implemented in LR
    if market not in ('ml', 'total'):
        return None

    consensus_count = None
    if isinstance(ensemble, list) and shipped_side_full:
        if market == 'ml':
            want = f'{shipped_side_full}_ML'
        else:
            want = shipped_side_full   # OVER / UNDER
        consensus_count = sum(1 for s in ensemble if isinstance(s, dict) and s.get('side') == want)

    # Mode 1 — LR overrode the ensemble
    if engine == 'lr_v1' and pre_lr:
        return {
            'mode': 'override',
            'market': market,
            'lr_side': shipped_side_full,
            'shipped_side': shipped_side_full,
            'lr_prob': pp.get('_lr_p_home_win'),
            'consensus_count': consensus_count,
            'consensus_money_pct': None,
        }

    # Mode 2 — LR was blocked by the consensus-dissent gate (ml only today)
    if 'LR dissented' in audit and shadow.get('suggested_side'):
        lr_side = shadow.get('suggested_side')
        if market != 'ml' or lr_side not in ('HOME', 'AWAY'): return None
        money_pct = None
        try:
            import re
            m = re.search(r'(\d+)%\s*money', audit)
            if m: money_pct = int(m.group(1))
            m2 = re.search(r'\((\d+)\s*sources', audit)
            if m2 and consensus_count is None:
                consensus_count = int(m2.group(1))
        except Exception:
            pass
        return {
            'mode': 'blocked',
            'market': market,
            'lr_side': lr_side,
            'shipped_side': shipped_side_full,
            'lr_prob': shadow.get('p_home_win'),
            'consensus_count': consensus_count,
            'consensus_money_pct': money_pct,
        }

    return None


def _grade(delta: dict, result: Optional[dict]) -> dict:
    """Attach lr_won / shipped_won given the actual outcome.
    Market-aware: ml grades vs home_win, total grades vs total_result."""
    out = dict(delta)
    winning_side = None
    if result:
        if delta['market'] == 'ml':
            hw = result.get('home_win')
            if hw is True: winning_side = 'HOME'
            elif hw is False: winning_side = 'AWAY'
        elif delta['market'] == 'total':
            tr = result.get('total_result')
            if tr in ('OVER', 'UNDER', 'PUSH'):
                winning_side = tr
    out['winning_side'] = winning_side
    if winning_side in (None, 'PUSH'):
        out['lr_won'] = None
        out['shipped_won'] = None
    else:
        out['lr_won'] = (winning_side == delta['lr_side'])
        out['shipped_won'] = (winning_side == delta['shipped_side'])
    return out


def audit_date_range(start: date, end: date, dry_run: bool = False) -> tuple[int, int, int]:
    dates = []
    d = start
    while d <= end:
        dates.append(d.isoformat())
        d += timedelta(days=1)

    games = _fetch_games(dates)
    results = _fetch_results(dates)

    inserted = graded = skipped = 0
    for g in games:
        pp = g.get('primary_play') or {}
        delta = _extract_dissent(pp)
        if not delta:
            skipped += 1
            continue
        graded_row = _grade(delta, results.get(g['game_id']))
        row = {
            'game_id':    g['game_id'],
            'game_date':  g['game_date'],
            'sport':      'MLB',
            'market':     graded_row['market'],
            'mode':       graded_row['mode'],
            'lr_side':    graded_row['lr_side'],
            'shipped_side': graded_row['shipped_side'],
            'winning_side': graded_row['winning_side'],
            'lr_won':     graded_row['lr_won'],
            'shipped_won': graded_row['shipped_won'],
            'lr_prob':    graded_row['lr_prob'],
            'consensus_count':    graded_row['consensus_count'],
            'consensus_money_pct': graded_row['consensus_money_pct'],
            'home_team':  g.get('home_team'),
            'away_team':  g.get('away_team'),
        }
        # graded_at set only if we know the outcome
        if graded_row['winning_side']:
            row['graded_at'] = 'now()'   # will be replaced by server-side default? use ISO
            from datetime import datetime, timezone
            row['graded_at'] = datetime.now(timezone.utc).isoformat()
            graded += 1

        if dry_run:
            print(f"  [dry] {row['game_date']} {row['market']:5s} {row['mode']:8s} "
                  f"LR→{row['lr_side']:6s} SHIP→{row['shipped_side']:6s} "
                  f"WIN={str(row['winning_side']):6s} lr_won={row['lr_won']}")
            inserted += 1
            continue

        r = requests.post(f'{SB}/rest/v1/lr_dissent_calibration',
                          headers=H_WRITE, json=row, timeout=15)
        if r.status_code < 300:
            inserted += 1
        else:
            print(f"  ! insert failed for {g['game_id']}: {r.status_code} {r.text[:200]}")

    return inserted, graded, skipped


def show_rollup() -> None:
    r = requests.get(f'{SB}/rest/v1/v_lr_dissent_hitrate?select=*', headers=H_READ, timeout=15)
    if r.status_code != 200:
        print(f'view fetch failed: {r.status_code} {r.text[:200]}'); return
    rows = r.json() or []
    if not rows:
        print('No graded LR dissent rows yet.'); return
    print('\n=== v_lr_dissent_hitrate ===')
    print(f"{'mkt':5s} {'mode':10s} {'window':10s} {'n':>4s} {'lr_w':>5s} {'ship_w':>7s} "
          f"{'lr%':>6s} {'ship%':>6s} {'avg_cons':>10s}")
    for r in rows:
        print(f"{r.get('market',''):5s} {r['mode']:10s} {r['window_key']:10s} {r['n']:>4d} "
              f"{r['lr_wins']:>5d} {r['shipped_wins']:>7d} "
              f"{(r['lr_hit_pct'] or 0):>6.1f} {(r['shipped_hit_pct'] or 0):>6.1f} "
              f"{(r['avg_consensus_count'] or 0):>10.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='ISO date (default: yesterday). Ignored w/ --backfill.')
    ap.add_argument('--backfill', type=int, help='Re-audit last N days.')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--show', action='store_true', help='Print rollup view only.')
    args = ap.parse_args()

    if args.show:
        show_rollup()
        return

    if args.backfill:
        end = date.today()
        start = end - timedelta(days=args.backfill)
    elif args.date:
        start = end = date.fromisoformat(args.date)
    else:
        start = end = date.today() - timedelta(days=1)

    print(f'Auditing LR dissents from {start} to {end} '
          f'({(end - start).days + 1} days)…')
    ins, gr, sk = audit_date_range(start, end, dry_run=args.dry_run)
    print(f'  inserted/updated: {ins}   graded: {gr}   skipped non-dissent: {sk}')
    if not args.dry_run:
        show_rollup()


if __name__ == '__main__':
    main()
