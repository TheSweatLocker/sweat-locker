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
  python signal_attribution_grade.py                 # today's date, NFL + NCAAF + MLB
  python signal_attribution_grade.py --days 7
  python signal_attribution_grade.py --sport NFL
"""
import argparse
import os
import sys
from datetime import date, timedelta
from typing import Optional

import requests
from signal_outcome import signal_was_right as _swr
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
                close_spread, close_total, sport: str = 'NFL') -> str:
    """Return W / L / P for the pick side.

    2026-09-14: close_spread sign convention DIFFERS by sport:
      NFL   → POSITIVE = HOME favored (e.g., cs=+3.5 means home laying 3.5)
      MLB   → NEGATIVE = HOME favored (e.g., cs=-1.5 means home laying 1.5)
      NCAAF → NEGATIVE = HOME favored (same as MLB)
    So the home-cover formula must flip based on sport, else spreads on
    MLB/NCAAF grade with inverted magnitude and consistently mis-label
    one-run/one-point home wins as covers when they aren't.
    """
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
        # Normalize to NFL convention (positive = home favored) so the
        # cover math is a single formula. For MLB/NCAAF, negate the
        # spread. Everywhere below treats home_line as "points home is
        # laying (positive if favored)".
        home_line = sp if sport in _HOME_POSITIVE_SPREAD_SPORTS else -sp
        diff = margin - home_line
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


_RESULTS_TBL = {
    'NFL':   'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'MLB':   'mlb_game_results',
    # 2026-10-27 · NHL/NBA/NCAAB added. Without them the attribution rows
    # written for those sports could never be graded, so their signals could
    # never earn an evidence-based weight -- half the sports were locked out
    # of the attribution -> grade -> refit -> weight chain entirely.
    'NHL':   'nhl_game_results',
    'NBA':   'nba_game_results',
    'NCAAB': 'ncaab_game_results',
}

# The spread column is NOT called the same thing in every sport.
# nhl_game_results has NO `close_spread` -- a select naming it 400s and
# returns nothing, which would have looked like "no NHL games settled"
# rather than a wiring error.
_SPREAD_COL = {
    'NHL': 'close_puckline',
}

# POSITIVE close-spread means HOME FAVOURED only on NFL. Measured, not
# assumed, by correlating the stored line against the realised home margin:
#   NFL   +0.425  positive = home favoured
#   NCAAF -0.721  negative = home favoured
#   NBA   -0.516  negative
#   NCAAB -0.644  negative
#   MLB   -0.134  negative
#   NHL   -0.171  negative  (close_puckline, n=2,707, values -1.5/0/+1.5)
# Every sport except NFL therefore takes the negate branch in _grade_pick.
# Getting this backwards grades every spread inverted -- see
# project_close_spread_sign_bug_914.
_HOME_POSITIVE_SPREAD_SPORTS = frozenset({'NFL'})


def _fetch_results(sport: str, date_lo: str) -> tuple[dict, dict]:
    """Returns two lookup dicts:
      1. by game_id (works when signal_attribution and results share IDs — NCAAF, MLB)
      2. by (game_date, home_team, away_team) tuple (NFL — MD5 ctx vs
         date+teams results ID mismatch per project_nfl_game_id_mismatch_911)
    """
    tbl = _RESULTS_TBL.get(sport)
    if not tbl:
        return {}, {}
    all_rows: list = []
    for page in range(5):
        r = requests.get(f'{SB}/rest/v1/{tbl}',
                         headers={**H_READ, 'Range-Unit': 'items',
                                  'Range': f'{page*1000}-{(page+1)*1000-1}'},
                         params={
                             'game_date': f'gte.{date_lo}',
                             # alias the sport's own spread column to
                             # close_spread so the grader reads one name
                             'select': ('game_id,game_date,home_team,'
                                        'away_team,home_score,away_score,'
                                        'close_spread:%s,close_total'
                                        % _SPREAD_COL.get(sport,
                                                          'close_spread')),
                         },
                         timeout=20)
        if r.status_code not in (200, 206): break
        page_rows = r.json() or []
        if not isinstance(page_rows, list): break
        all_rows.extend(page_rows)
        if len(page_rows) < 1000: break
    by_id = {r['game_id']: r for r in all_rows if isinstance(r, dict) and r.get('game_id')}
    by_tuple = {(r.get('game_date'), r.get('home_team'), r.get('away_team')): r
                for r in all_rows if isinstance(r, dict)}
    return by_id, by_tuple


_CTX_TBL = {
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'MLB':   'mlb_game_context',
    'NHL':   'nhl_game_context',
    'NBA':   'nba_game_context',
    'NCAAB': 'ncaab_game_context',
}


def _fetch_ctx_teams(sport: str, date_lo: str) -> dict:
    """Map ctx game_id → (game_date, home_team, away_team) so we can
    bridge NFL's MD5-vs-abbrev id mismatch. MLB/NCAAF ids match results
    directly so this map is unused for them but cheap to compute."""
    tbl = _CTX_TBL.get(sport)
    if not tbl: return {}
    all_rows: list = []
    for page in range(5):
        r = requests.get(f'{SB}/rest/v1/{tbl}',
                         headers={**H_READ, 'Range-Unit': 'items',
                                  'Range': f'{page*1000}-{(page+1)*1000-1}'},
                         params={
                             'game_date': f'gte.{date_lo}',
                             'select': 'game_id,game_date,home_team,away_team',
                         },
                         timeout=20)
        if r.status_code not in (200, 206): break
        page_rows = r.json() or []
        if not isinstance(page_rows, list): break
        all_rows.extend(page_rows)
        if len(page_rows) < 1000: break
    return {r['game_id']: (r.get('game_date'), r.get('home_team'), r.get('away_team'))
            for r in all_rows if isinstance(r, dict) and r.get('game_id')}


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
    # 2026-10-27: default to EVERY sport in the results map. The old
    # default was ['NFL','NCAAF','MLB'], so the nightly run silently
    # skipped NHL/NBA/NCAAB even once they were wired -- the same
    # shape of omission as the sport choices list.
    sports = [sport] if sport else list(_RESULTS_TBL)
    from datetime import datetime as _dt, timezone as _tz
    now_iso = _dt.now(_tz.utc).isoformat()
    print(f'=== signal_attribution_grade · since {date_lo} · sports={sports} ===')

    for sp in sports:
        results_by_id, results_by_tuple = _fetch_results(sp, date_lo)
        ctx_teams = _fetch_ctx_teams(sp, date_lo)  # ctx.game_id → tuple bridge
        settled_by_id = {gid: r for gid, r in results_by_id.items()
                         if r.get('home_score') is not None}
        pending = _fetch_pending(sp, date_lo)
        print(f'  {sp}: {len(settled_by_id)} settled games · {len(pending)} pending signal rows')

        graded = 0
        skipped_no_result = 0
        by_result = {'W': 0, 'L': 0, 'P': 0}
        for row in pending:
            gid = row.get('game_id')
            settled_game = settled_by_id.get(gid)
            # 2026-09-13: NFL game_id mismatch bridge — signal_attribution
            # uses the ctx MD5 id; nfl_game_results uses date+teams. Look
            # up the ctx row's teams then re-query results by tuple.
            if not settled_game:
                bridge = ctx_teams.get(gid)
                if bridge:
                    settled_game = results_by_tuple.get(bridge)
                    if settled_game and settled_game.get('home_score') is None:
                        settled_game = None
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
                sport=sp,
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
            # == 2026-10-29 . GRADE THE SIGNAL, NOT JUST THE PICK ==========
            # `result` above is the PICK's outcome, stamped onto every signal
            # row that fired on that pick. That is the right thing to store
            # and the wrong thing to average per signal -- and v_signal_records
            # was averaging it, which is what the game card tooltip shows a
            # user. Measured gap on signals with n>=25 both ways: 11 of 34
            # inflated, worst NCAAF COHORT_NCAAF_HEAVY_HOME_FAV 61.7% shown
            # vs 50.9% real, NFL LR_SHADOW 53.3% vs 42.7%. The error always
            # runs the same way -- it penalises a signal for DISAGREEING with
            # the engine, which is the dissenting voice we most want measured.
            #
            # signal_outcome.signal_was_right is the one definition of the
            # rule and already handles the per-sport sign convention. Writing
            # it here means SQL never has to reimplement that convention
            # (feedback_publishable_view_drift) and both the view and the
            # refit read the same column.
            try:
                patch['signal_was_right'] = _swr({**row, **patch}, sp)
            except Exception as e:                             # noqa: BLE001
                # Never lose a grade because the extra field failed.
                print(f'  ! signal_was_right failed on id={row.get("id")}: {e}')
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
    p.add_argument('--sport', choices=['NFL', 'NCAAF', 'MLB',
                                       'NHL', 'NBA', 'NCAAB'])
    p.add_argument('--days', type=int, default=3)
    args = p.parse_args()
    run(sport=args.sport, days=args.days)


if __name__ == '__main__':
    main()
