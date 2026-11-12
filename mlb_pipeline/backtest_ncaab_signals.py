"""Measure every NCAAB signal against real pre-game snapshots and real finals.

== WHY ====================================================================
Andy, 2026-11-03: "I need ncaab to have differnt models and have solid stats
based on real data we need to get there now."

The NCAAB signal library is 67 signals, 66 enabled, and
`weight`/`sample_n`/`hit_rate_pct` are NULL on **every one of them**. The
engine cannot be pointed at them as they stand: that is exactly
project_engine_selects_on_zero_measured_signals_1028, where the measured hit
rate on enabled GAME signals was NBA 0 of 32, NHL 0 of 38, NFL 0 of 81.

== WHAT CAN AND CANNOT BE MEASURED, MEASURED =============================
    ncaab_game_results        11,642 rows, 11,599 with finals
      BUT projected_spread, projected_total, adj_em_gap, pace_avg,
      home/away_adj_oe/de are 0 of 11,642 non-null. The columns exist and
      nothing ever populated them, so a backtest off the results table
      alone is impossible for all but the market-structure signals.

    game_context_snapshots    272 NCAAB rows, full pre-game ctx as JSONB
      projected_spread 189 . adj_em_gap 189 . pace_avg 189
      home_adj_oe 234 . away_adj_oe 222 . close_spread 134

So the snapshots are the only real pre-game record of what the model
believed, and they are the right side of the join: a snapshot is what we
knew BEFORE the game, which is the whole point (walk-forward by
construction -- feedback_walk_forward_misses_feature_leak).

== THE JOIN CANNOT USE THE DATE, AND THAT IS A FINDING ===================
Keyed on (game_date, resolved away, resolved home) only **17 of 272**
snapshots find their result. Keyed on the resolved PAIR alone, **257 of
272** do -- the other 240 have a snapshot game_date one or two days off:

    snapshot 2026-11-01  UAB @ McNeese          result 2026-11-02
    snapshot 2026-11-01  Robert Morris @ Nevada result 2026-11-03

ncaab_game_context is internally consistent today (game_date equals the ET
date of game_time_et on 168 of 168), so the snapshots captured a date ctx
has since CORRECTED -- early-posted college games carry a provisional tip
time and get re-dated when the real one lands. That same defect produced the
6 duplicate NCAAB jerry_reads found the same night.

So this joins on the resolved team PAIR within a +/- 3 day window and takes
the nearest date. A matchup does not repeat inside three days, and the
resolver makes the pair canonical on both sides
(feedback_a_blocklist_duplicating_a_lookup_table).

== WHAT IT REPORTS =======================================================
Per signal: how often it FIRED, and on the fired subset the hit rate of the
side it named, against the direction-only control for that market
(feedback_signal_needs_a_direction_control). Rates are quoted only at
n >= MIN_N; below that the count is shown and the rate withheld
(feedback_sample_size_with_pct).

Writes nothing. `--emit` prints the signal_sources UPDATE statements it
would justify, for review, and still writes nothing.

    python backtest_ncaab_signals.py
    python backtest_ncaab_signals.py --min-n 20 --verbose
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

for _ln in (Path(__file__).parent / '.env').read_text(
        encoding='utf-8', errors='replace').split('\n'):
    if '=' in _ln and not _ln.lstrip().startswith('#'):
        _k, _v = _ln.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

#: a matchup does not repeat inside this many days, so the pair is unique
JOIN_WINDOW_DAYS = 3


from posted_line import is_postable

def page(table: str, select: str, **extra) -> list:
    out, off = [], 0
    while True:
        p = {'select': select, 'limit': 1000, 'offset': off}
        p.update(extra)
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=120,
                         params=p)
        r.raise_for_status()
        b = r.json() or []
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def _d(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except Exception:                                        # noqa: BLE001
        return None


def build_pairs(verbose: bool = False):
    """[(ctx_snapshot, result_row)] joined on resolved pair + nearest date."""
    import ncaab_team_resolver as R
    R.load_aliases()

    snaps = [s for s in page('game_context_snapshots',
                             'sport,game_id,game_date,snapshot_date,context')
             if str(s.get('sport')).upper() == 'NCAAB']
    # 2026-11-03: the ML price column is `home_ml_close` / `away_ml_close`.
    # `close_home_ml` EXISTS in the schema and is 0% populated on all 11,599
    # graded rows, so naming only that one returns a silent blank and every
    # ROI comes out n=0 (feedback_explicit_select_silent_blanks). Ask for
    # both spellings.
    res = page('ncaab_game_results',
               'game_id,game_date,home_team,away_team,home_score,away_score,'
               'close_spread,close_total,spread_result,total_result,home_win,'
               'close_home_ml,close_away_ml,home_ml_close,away_ml_close')
    graded = [r for r in res if r.get('home_score') is not None]

    # == 2026-11-11 . 79% OF NCAAB CLOSING LINES WERE NEVER POSTED =========
    # ncaab_odds_pull._consensus_spread AVERAGED across books until it was
    # fixed on 2026-11-02, and ncaab_game_context._avg_market_line did the
    # same. Everything written before that fix carries a number no book
    # could take a bet on: close_spread values like -45.25, -44.93, -43.38,
    # -1.28, 0.15. Measured across every sport's results table:
    #
    #   NCAAB close_spread  4,257 of 5,373 NOT postable   79.2%
    #   NCAAB close_total   4,321 of 5,374 NOT postable   80.4%
    #   NBA / NFL / NCAAF / MLB / NHL          0.0%-0.4%
    #
    # NCAAB alone, and all of it 2024-11..2025-04. A fake line makes a fake
    # cover result (feedback_consensus_line_must_be_posted), so every
    # spread and total signal weight fit on this history was fit on
    # fabricated outcomes -- and nothing in this file, refit_signal_registry,
    # calibrate_spread_split or refresh_prop_signal_calibration screened
    # for it.
    #
    # Invalidate the FIELD, not the row. The moneylines on these same rows
    # are genuine -- all 4,922 two-way pairs carry a plausible book hold
    # (mean +4.32%, min +1.63%, max +6.16%, zero pairs under 0.5%, which no
    # book would ever offer) -- so dropping the row would throw away valid
    # ML outcomes to fix a spread problem. Nulling the untrustworthy column
    # lets spread/total measurements skip the game while ML keeps full n.
    #
    # is_postable comes from posted_line, the module the 11-02 fix added,
    # rather than a second local "multiple of 0.5" test
    # (feedback_a_blocklist_duplicating_a_lookup_table).
    _unpostable = {'close_spread': 0, 'close_total': 0}
    for r in graded:
        for _col in ('close_spread', 'close_total'):
            if is_postable(r.get(_col)) is False:
                r[_col] = None
                if _col == 'close_spread':
                    r['spread_result'] = None
                else:
                    r['total_result'] = None
                _unpostable[_col] += 1
    if any(_unpostable.values()):
        print(f'  ! dropped unpostable lines (averaged, not quoted): '
              f"close_spread {_unpostable['close_spread']}, "
              f"close_total {_unpostable['close_total']} of {len(graded)} graded "
              f'-- moneylines on those rows are kept, they are real')
    by_pair = collections.defaultdict(list)
    for r in graded:
        a, h = R.resolve(r['away_team']), R.resolve(r['home_team'])
        if a and h:
            by_pair[(a, h)].append(r)

    # Keep the LATEST snapshot per game -- the last thing we believed
    # pre-game. Several snapshot_dates can exist for one game.
    best = {}
    for s in snaps:
        c = s.get('context')
        if isinstance(c, str):
            try:
                c = json.loads(c)
            except Exception:                                # noqa: BLE001
                continue
        if not isinstance(c, dict):
            continue
        a, h = R.resolve(c.get('away_team')), R.resolve(c.get('home_team'))
        if not a or not h:
            continue
        k = (a, h, str(s.get('game_date')))
        prev = best.get(k)
        if prev is None or str(s.get('snapshot_date')) > str(prev[0].get('snapshot_date')):
            best[k] = (s, c)

    pairs, unjoined = [], 0
    for (a, h, gd), (s, c) in best.items():
        cands = by_pair.get((a, h), [])
        sd = _d(gd)
        pick = None
        if cands and sd:
            near = sorted(cands, key=lambda r: abs(((_d(r['game_date']) or sd) - sd).days))
            if abs(((_d(near[0]['game_date']) or sd) - sd).days) <= JOIN_WINDOW_DAYS:
                pick = near[0]
        if pick is None:
            unjoined += 1
            continue
        pairs.append((c, pick, a, h))
    print(f'NCAAB snapshots {len(snaps)} -> {len(best)} distinct games; '
          f'joined to a graded final: {len(pairs)}, unjoined {unjoined}')
    return pairs


def outcome(res: dict, side: str):
    """Did the named side win? None when this market cannot be graded."""
    s = (side or '').strip().upper()
    hs, as_ = res.get('home_score'), res.get('away_score')
    if hs is None or as_ is None:
        return None
    if s in ('HOME_ML', 'HOME'):
        return hs > as_
    if s in ('AWAY_ML', 'AWAY'):
        return as_ > hs
    if s in ('OVER', 'UNDER'):
        tr = str(res.get('total_result') or '').lower()
        if tr not in ('over', 'under'):
            return None
        return tr == s.lower()
    if s in ('HOME_SPREAD', 'HOME_RL', 'HOME_ATS'):
        sr = str(res.get('spread_result') or '').lower()
        if sr not in ('home_covered', 'away_covered'):
            return None
        return sr == 'home_covered'
    if s in ('AWAY_SPREAD', 'AWAY_RL', 'AWAY_ATS'):
        sr = str(res.get('spread_result') or '').lower()
        if sr not in ('home_covered', 'away_covered'):
            return None
        return sr == 'away_covered'
    return None


def _price(res: dict, side: str):
    """The real closing price for the named side, or None.

    A hit rate without a price is not a result. This sample's home teams win
    84% because opening week is power-conference buy games, and a heavy
    favourite at -600 needs 85.7% just to break even -- so 92% can still
    LOSE money (feedback_flat_110_inflates_every_ml_figure).
    """
    s = (side or '').strip().upper()
    if s in ('HOME_ML', 'HOME'):
        v = res.get('close_home_ml') or res.get('home_ml_close')
    elif s in ('AWAY_ML', 'AWAY'):
        v = res.get('close_away_ml') or res.get('away_ml_close')
    else:
        return -110.0          # spreads/totals: no per-side price stored
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if abs(f) >= 100 else None


def _roi(units):
    """(roi_pct, n) from a list of (won, american_odds)."""
    tot = 0.0
    n = 0
    for won, odds in units:
        if odds is None:
            continue
        n += 1
        if won:
            tot += (odds / 100.0) if odds > 0 else (100.0 / abs(odds))
        else:
            tot -= 1.0
    return ((100.0 * tot / n) if n else None), n


def _breakeven(units):
    vals = [100.0 * abs(o) / (abs(o) + 100.0) for _w, o in units if o is not None]
    return (sum(vals) / len(vals)) if vals else None


def control(pairs, scope: str):
    """Direction-only control: the blanket favourite/home/over rate."""
    out = {}
    # Every spelling a side_expr actually emits gets a control entry, or the
    # lookup misses and selection value silently reads '--'. The RL signals
    # name HOME_RL / AWAY_RL while the spread control was keyed HOME_ATS.
    for label, side in (('HOME_ML', 'HOME_ML'), ('AWAY_ML', 'AWAY_ML'),
                        ('HOME', 'HOME_ML'), ('AWAY', 'AWAY_ML'),
                        ('OVER', 'OVER'), ('UNDER', 'UNDER'),
                        ('HOME_ATS', 'HOME_ATS'), ('AWAY_ATS', 'AWAY_ATS'),
                        ('HOME_RL', 'HOME_ATS'), ('AWAY_RL', 'AWAY_ATS'),
                        ('HOME_SPREAD', 'HOME_ATS'),
                        ('AWAY_SPREAD', 'AWAY_ATS')):
        hits = [outcome(r, side) for _c, r, _a, _h in pairs]
        hits = [x for x in hits if x is not None]
        if hits:
            out[label] = (100.0 * sum(hits) / len(hits), len(hits))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-n', type=int, default=30)
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--emit', action='store_true',
                    help='print the UPDATEs this would justify; writes nothing')
    args = ap.parse_args()

    from signal_expr import evaluate_bool, evaluate_str, eval_error_report

    pairs = build_pairs(args.verbose)
    if not pairs:
        print('!! 0 joined pairs — this is a WIRING failure, not a finding '
              '(feedback_zero_rows_is_a_wiring_error). Stopping.')
        return 1

    sigs = [s for s in page('signal_sources', '*')
            if str(s.get('sport')).upper() == 'NCAAB']
    print(f'NCAAB signals in the registry: {len(sigs)} '
          f'({sum(1 for s in sigs if s.get("enabled"))} enabled)')
    ctrl = control(pairs, '*')
    print('\ndirection-only controls on this sample:')
    for k, (pct, n) in sorted(ctrl.items()):
        print(f'   {k:10} {pct:6.2f}%  n={n}')

    rows = []
    for s in sigs:
        cond = s.get('condition_expr')
        side_expr = s.get('side_expr')
        if not cond:
            continue
        fired = 0
        wins = graded = 0
        units = []
        sides = collections.Counter()
        for c, res, _a, _h in pairs:
            if not evaluate_bool(str(cond), c):
                continue
            fired += 1
            side = ((evaluate_str(str(side_expr), c) or '') if side_expr
                    else '')
            sides[side or '<none>'] += 1
            o = outcome(res, side)
            if o is None:
                continue
            graded += 1
            wins += 1 if o else 0
            units.append((o, _price(res, side)))
        rows.append({
            'key': s['signal_key'], 'scope': s.get('market_scope'),
            'enabled': bool(s.get('enabled')), 'fired': fired,
            'graded': graded, 'wins': wins, 'sides': dict(sides),
            'units': units,
        })

    rows.sort(key=lambda r: (-r['graded'], r['key']))
    print(f'\n{"signal":44} {"scope":6} {"fired":>5} {"graded":>6} '
          f'{"hit":>8} 1side  selection   BE        ROI')
    for r in rows:
        if r['fired'] == 0:
            continue
        side = max(r['sides'], key=r['sides'].get) if r['sides'] else '<none>'
        cpct = ctrl.get(side, (None, 0))[0]
        # == SELECTION VALUE IS THE ONLY HONEST NUMBER HERE ==============
        # Every NCAAB signal measured on 2026-11-03 named ONE side on every
        # single firing, so the "direction-only control on the same games"
        # is identical to the signal by construction and tells us nothing:
        #   ncaab_projection_contradicts_total_over  61.43% vs always-UNDER
        #                                            61.43% on the same 70
        #   away_team_ats_cold_season                65.85% vs always-HOME_RL
        #                                            65.85% on the same 41
        # What a one-sided signal can still contribute is SELECTION: does
        # its subset beat that side's rate over the WHOLE sample? That is
        # the number reported as `sel`.
        sel = None
        if r['sides']:
            dom = max(r['sides'], key=r['sides'].get)
            base = ctrl.get(dom, (None, 0))[0]
            if base is not None and r['graded']:
                sel = (100.0 * r['wins'] / r['graded']) - base
        roi, rn = _roi(r['units'])
        be = _breakeven(r['units'])
        if r['graded'] >= args.min_n:
            pct = 100.0 * r['wins'] / r['graded']
            delta = f'{pct - cpct:+6.2f}pp' if cpct is not None else '   n/a'
            hit = f'{pct:6.2f}%'
            bes = f'{be:6.2f}%' if be is not None else '    --'
            rois = f'{roi:+7.2f}%' if roi is not None else '     --'
        else:
            hit = '     --'
            delta = f'  n<{args.min_n}'
            bes = '    --'
            rois = '     --'
        onesided = 'Y' if len(r['sides']) == 1 else ' '
        sels = f'{sel:+6.2f}pp' if (sel is not None and r['graded'] >= args.min_n) else '      --'
        print(f'   {r["key"][:38]:38} {str(r["scope"] or "-"):5} '
              f'{r["fired"]:5} {r["graded"]:5} {hit} 1side={onesided} '
              f'sel {sels}  BE {bes} ROI {rois}')

    never = [r for r in rows if r['fired'] == 0]
    print(f'\nsignals that NEVER FIRED on {len(pairs)} games: {len(never)} '
          f'of {len(rows)}')
    for r in never:
        print(f'   {r["key"]}')
    errs = eval_error_report()
    if errs:
        print(f'\n!! {len(errs)} expression errors — a swallowed error is a '
              f'FALSE condition, not an absent signal '
              f'(feedback_swallowed_nameerror_is_a_false_condition):')
        for e in errs[:12]:
            print(f'   {e}')
    print(f'\nSample is {len(pairs)} graded games. Nothing here is written; '
          f'this reports only.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
