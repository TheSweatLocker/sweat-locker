"""Grade signal_attribution snapshots against final game results.

For every pending signal_attribution row (result IS NULL) whose game
has settled, compute whether the picked side hit (per pick_market)
and mark the signal with W / L / P.

Signal grading semantics:
  - ML picks: hit if home_score > away_score (HOME pick) or reverse
  - Spread/RL picks: hit if (margin + close_spread) > 0 for HOME etc.
  - Total picks: hit if (h + a) > close_total for OVER etc.

Each signal snapshot inherits the pick_side result — so LR_SHADOW
kind=ok gets marked W when the pick hits, L when it misses. That
gives us "when LR agrees with pick, hit rate = X%" over time.

Run:
  python signal_attribution_grade.py                 # today's date, NFL + NCAAF
  python signal_attribution_grade.py --days 7
  python signal_attribution_grade.py --sport NFL
"""
import argparse
import os
import sys
from datetime import date, timedelta
from typing import Optional

import requests
from dotenv import load_dotenv

for _p in ('mlb_pipeline/.env', '.env'):
    if os.path.exists(_p):
        load_dotenv(_p); break

SB = os.environ.get('SUPABASE_URL')
K = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY'))
if not (SB and K):
    sys.exit('SUPABASE_URL / SUPABASE_KEY not set')

H_READ = {'apikey': K, 'Authorization': f'Bearer {K}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def _grade_pick(pick_market: str, pick_side: str,
                home_score: float, away_score: float,
                close_spread, close_total) -> str:
    """Return W / L / P for the pick side."""
    margin = home_score - away_score
    total = home_score + away_score
    if pick_market == 'ml':
        if home_score == away_score: return 'P'
        home_won = home_score > away_score
        return 'W' if ((pick_side == 'HOME' and home_won) or
                       (pick_side == 'AWAY' and not home_won)) else 'L'
    if pick_market in ('spread', 'rl'):
        sp = _f(close_spread)
        if sp is None: return None
        # close_spread convention (per Sweat Shop): positive = home favored.
        # HOME cover: (margin - sp) > 0. AWAY cover: (margin - sp) < 0.
        # Push if exact.
        diff = margin - sp
        if diff == 0: return 'P'
        home_cover = diff > 0
        return 'W' if ((pick_side == 'HOME' and home_cover) or
                       (pick_side == 'AWAY' and not home_cover)) else 'L'
    if pick_market == 'total':
        tot = _f(close_total)
        if tot is None: return None
        if total == tot: return 'P'
        went_over = total > tot
        return 'W' if ((pick_side == 'OVER' and went_over) or
                       (pick_side == 'UNDER' and not went_over)) else 'L'
    return None


def _fetch_results(sport: str, date_lo: str) -> dict:
    """{game_id: {home_score, away_score, close_spread, close_total}}."""
    tbl = 'nfl_game_results' if sport == 'NFL' else 'ncaaf_game_results'
    all_rows: list = []
    for page in range(5):
        r = requests.get(f'{SB}/rest/v1/{tbl}',
                         headers={**H_READ, 'Range-Unit': 'items',
                                  'Range': f'{page*1000}-{(page+1)*1000-1}'},
                         params={
                             'game_date': f'gte.{date_lo}',
                             'select': 'game_id,home_score,away_score,close_spread,close_total',
                         },
                         timeout=20)
        if r.status_code not in (200, 206): break
        page_rows = r.json() or []
        if not isinstance(page_rows, list): break
        all_rows.extend(page_rows)
        if len(page_rows) < 1000: break
    return {r['game_id']: r for r in all_rows if isinstance(r, dict) and r.get('game_id')}


def _fetch_pending(sport: str, date_lo: str) -> list:
    """signal_attribution rows for `sport` since date_lo with result IS NULL."""
    all_rows: list = []
    for page in range(20):
        r = requests.get(f'{SB}/rest/v1/signal_attribution',
                         headers={**H_READ, 'Range-Unit': 'items',
                                  'Range': f'{page*1000}-{(page+1)*1000-1}'},
                         params={
                             'sport': f'eq.{sport}',
                             'game_date': f'gte.{date_lo}',
                             'result': 'is.null',
                             'select': 'id,game_id,pick_market,pick_side',
                         },
                         timeout=20)
        if r.status_code not in (200, 206): break
        page_rows = r.json() or []
        if not isinstance(page_rows, list): break
        all_rows.extend(page_rows)
        if len(page_rows) < 1000: break
    return all_rows


def run(sport: Optional[str] = None, days: int = 3) -> None:
    date_lo = (date.today() - timedelta(days=days)).isoformat()
    sports = [sport] if sport else ['NFL', 'NCAAF']
    from datetime import datetime as _dt, timezone as _tz
    now_iso = _dt.now(_tz.utc).isoformat()
    print(f'=== signal_attribution_grade · since {date_lo} · sports={sports} ===')

    for sp in sports:
        results = _fetch_results(sp, date_lo)
        settled = {gid: r for gid, r in results.items() if r.get('home_score') is not None}
        pending = _fetch_pending(sp, date_lo)
        print(f'  {sp}: {len(settled)} settled games · {len(pending)} pending signal rows')

        graded = 0
        skipped_no_result = 0
        by_result = {'W': 0, 'L': 0, 'P': 0}
        for row in pending:
            gid = row.get('game_id')
            settled_game = settled.get(gid)
            if not settled_game:
                skipped_no_result += 1
                continue
            result = _grade_pick(
                row.get('pick_market'),
                row.get('pick_side'),
                float(settled_game.get('home_score') or 0),
                float(settled_game.get('away_score') or 0),
                settled_game.get('close_spread'),
                settled_game.get('close_total'),
            )
            if not result:
                skipped_no_result += 1
                continue

            patch = {
                'result': result,
                'resolved_at': now_iso,
                'close_line': settled_game.get('close_spread'),
                'actual_margin': (float(settled_game.get('home_score') or 0)
                                  - float(settled_game.get('away_score') or 0)),
                'actual_total': (float(settled_game.get('home_score') or 0)
                                 + float(settled_game.get('away_score') or 0)),
            }
            pr = requests.patch(
                f'{SB}/rest/v1/signal_attribution?id=eq.{row["id"]}',
                headers=H_WRITE, json=patch, timeout=10,
            )
            if pr.status_code in (200, 204):
                graded += 1
                by_result[result] += 1
        print(f'  {sp}: graded {graded} · skipped {skipped_no_result} '
              f'(no result / no market data) · verdicts={by_result}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=['NFL', 'NCAAF'])
    p.add_argument('--days', type=int, default=3)
    args = p.parse_args()
    run(sport=args.sport, days=args.days)


if __name__ == '__main__':
    main()
