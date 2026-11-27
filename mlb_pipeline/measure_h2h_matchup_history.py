# -*- coding: utf-8 -*-
"""Measure matchup history (h2h) against the closing market, from results.

== WHY THIS EXISTS (2026-11-27) =========================================
Andy: "hand wigthing stats absed on previous matchups is legit data
analytics". He is right, and in MLB the repo proves it -- 15 VALIDATED
`pitcher_vs_team_*` signals at weight 0.78-1.00. In NCAAB the same family
carries NINE signals that have never been measured: every one UNVALIDATED
at n in {0, 2, 6}, all riding recommended_weight 0.30, which is a PRIOR and
not a measurement.

TWO SEPARATE REASONS THEY LOOK UNMEASURED, AND NEITHER IS A BROKEN WRITER.

1. THE CONDITION IS SEASONAL. h2h eligibility needs prior meetings, and
   November is NON-CONFERENCE season. Measured share of games with >= 3
   prior meetings, by month:

       2025-11  0.1%     2026-01  15.6%     2026-03  53.2%
       2025-12  2.7%     2026-02  51.4%     2026-11  2.4%  <- today

   So the refit's default 90-day window, run in November, sees a condition
   that fires on 2.4% of the slate. By February it fires on more than half.
   The n in {0, 2, 6} is the calendar, not a defect -- and the corollary is
   the live risk: an UNMEASURED 0.30 prior is about to start applying to
   half the board in roughly two months.

2. THE CONTEXT TABLE CANNOT BE MEASURED FROM, though it fires correctly.
   `ncaab_game_context` holds 1,195 rows, ALL dated 2026-11-01 or later --
   context rows exist for current and upcoming games only, so there is no
   historical row to measure against. The FIRING path is fine:
   `enrich_team_form_universal.compute_h2h` queries the RESULTS table with
   `game_date lt.<game_date>` and no season bound, so it already looks back
   across seasons. (An earlier version of this note said the signals should
   be rewired to read results. They already do. The gap is measurement, not
   firing.)

So this script derives the history from `ncaab_game_results` -- 12,793
settled games back to 2024-11-04 -- taking for every game the prior
meetings between those two teams STRICTLY BEFORE that game's date.
Walk-forward by construction: there is no "as of" bookkeeping to get wrong,
because a future game cannot have a date before the one being scored.

== WHAT IT TESTS ========================================================
The nine NCAAB h2h signals name EXTREME cells, not a median split. A median
split was the error in the first pass (_stat_diff_edges.py): it lumps 0-2
against 3-5 and discards exactly the 4-0/5-0 dominance the signals are
about ([[feedback_one_sentinel_cap_across_scales]]). So this tests the cells
as named:

    h2h_home_dominant       home won 4-5 of last 5   -> HOME ML
    h2h_away_dominant       home won 0-1 of last 5   -> AWAY ML
    h2h_covers_home         home covered 4-5 of 5    -> HOME ATS
    h2h_over_streak         4-5 of last 5 went OVER  -> OVER
    h2h_under_streak        0-1 of last 5 went OVER  -> UNDER
    h2h_over_after_unders   0-1 of last 5 went OVER  -> OVER      (contra)
    h2h_under_after_overs   4-5 of last 5 went OVER  -> UNDER     (contra)
    *_fade                  the complement side of the same cell

Note what that list means: `h2h_over_streak` and `h2h_under_after_overs`
fire on the SAME condition and bet OPPOSITE sides, as do `h2h_under_streak`
and `h2h_over_after_unders`, and each `_fade` is its base signal's exact
complement. One event stored as two rows -- already documented on 11-25
(NBA h2h_away_dominant 61.7% / _fade 38.3%, same n=81). Measuring one side
measures both, so this prints the complement's rate rather than pretending
it is independent evidence.

== THE THREE WAYS THIS COULD LIE, AND WHAT IS DONE ABOUT EACH ===========

1. A HOME-FIELD DETECTOR WEARING A MATCHUP COSTUME. "Back HOME when home
   won 4 of 5" beats a coin flip simply because home teams win. Every cell
   is therefore shown against a DIRECTION-ONLY CONTROL: the same side, bet
   on every eligible game, h2h condition ignored. The cell has to beat the
   control, not the coin ([[feedback_signal_needs_a_direction_control]],
   [[feedback_favourite_bias_was_the_market_chooser_1029]]).

2. A FAVOURITE SELECTOR. Backing the dominant side selects favourites, and
   favourites win. So each cell prints the mean IMPLIED probability of the
   side it backs, and is priced against the bar that side actually fires at
   -- never a flat league figure
   ([[feedback_the_bar_is_never_the_price_the_signal_fires_at]],
   [[feedback_the_engine_scores_winners_not_prices]]).

3. A SYNTHETIC PRICE. NCAAB's stored moneylines are mostly real (overround
   1.037-1.053 on recent games, a book's true vig) but a block of rows
   carries -100000 / +3300 placeholders on 30-to-40-point mismatches. Those
   are not prices anyone could bet. Every ML row is validated and the
   rejects are COUNTED and REPORTED, not silently dropped
   ([[feedback_a_juice_band_cannot_catch_a_market_error]],
   [[feedback_an_unpriced_moneyline_rate_is_not_a_record]]).

   For ATS and totals there IS no stored juice in NCAAB -- the line is kept,
   the price never is ([[feedback_ncaab_engine_weights_a_favourite_detector]]).
   So those cells are measured as a RATE against the control and against
   the -110 breakeven (52.38%) as a STATED ASSUMPTION, and no ROI is
   claimed for them. An unpriced rate is not a record.

Every figure prints n. No verdict is issued below MIN_CELL
([[feedback_verdicts_issued_without_a_sample_size]],
[[feedback_sample_size_with_pct]]). Paging is KEYSET on game_id, which is a
unique slug -- limit/offset over a non-unique game_date fabricates
duplicates and blank pages, which is how four phantom Sharp picks got
reported once ([[feedback_offset_paging_on_a_nonunique_key_fabricates]]).

    python measure_h2h_matchup_history.py
    python measure_h2h_matchup_history.py --sport NBA --min-meetings 2
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import math
import os
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:                                              # noqa: BLE001
    pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _line in _env.read_text().split('\n'):
        if '=' in _line and not _line.startswith('#'):
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ.get('SUPABASE_URL') or ''
KEY = os.environ.get('SUPABASE_KEY') or ''
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

RESULTS_TABLE = {
    'NCAAB': 'ncaab_game_results',
    'NBA': 'nba_game_results',
    'NHL': 'nhl_game_results',
    'NFL': 'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
}

# A real NCAAB moneyline pair. The band is deliberately wide -- the point is
# to exclude placeholders, not to impose a betting rule.
ML_MIN, ML_MAX = -5000, 5000
OVERROUND_MIN, OVERROUND_MAX = 1.00, 1.15
BE_110 = 100.0 / 210.0            # 52.38%, the stated -110 assumption


def implied(odds) -> float | None:
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0 or -100 < o < 100:
        return None
    return (-o / (-o + 100.0)) if o < 0 else (100.0 / (o + 100.0))


def payout(odds) -> float | None:
    """Units won per 1 risked. A PLUS price pays odds/100, not 100/odds."""
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0 or -100 < o < 100:
        return None
    return (100.0 / -o) if o < 0 else (o / 100.0)


def se(p: float, n: int) -> float:
    return math.sqrt(max(p * (1.0 - p), 1e-9) / n) if n else 0.0


def z_vs(p: float, n: int, q: float, m: int) -> float:
    s = math.sqrt(se(p, n) ** 2 + se(q, m) ** 2)
    return (p - q) / s if s > 0 else 0.0


def keyset_pages(table: str, select: str, chunk: int = 1000) -> list:
    """Page by game_id, which is unique. Never limit/offset on game_date."""
    out, last = [], ''
    while True:
        params = {'select': select, 'order': 'game_id.asc',
                  'limit': chunk, 'game_id': f'gt.{last}'}
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=params,
                         timeout=120)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{table} {r.status_code}: {r.text[:300]}')
        batch = r.json()
        if not isinstance(batch, list) or not batch:
            break
        out += batch
        if len(batch) < chunk:
            break
        last = batch[-1]['game_id']
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAB', choices=sorted(RESULTS_TABLE))
    ap.add_argument('--min-meetings', type=int, default=3,
                    help='prior meetings required to be eligible')
    ap.add_argument('--window', type=int, default=5,
                    help='how many most-recent prior meetings to read')
    ap.add_argument('--min-cell', type=int, default=30,
                    help='no verdict below this n')
    a = ap.parse_args()

    if not SB or not KEY:
        print('! SUPABASE_URL / SUPABASE_KEY not set')
        return 2

    table = RESULTS_TABLE[a.sport]
    sel = ('game_id,game_date,season,home_team,away_team,home_score,'
           'away_score,close_spread,close_total,spread_result,total_result,'
           'home_ml_close,away_ml_close')
    rows = keyset_pages(table, sel)

    print('=' * 78)
    print(f'MATCHUP HISTORY vs THE CLOSE   {a.sport}   {_dt.date.today()}')
    print('=' * 78)
    print(f'\n{table}: {len(rows)} rows read (keyset on game_id)')
    ids = {r['game_id'] for r in rows}
    print(f'  distinct game_id: {len(ids)}   '
          f'{"OK" if len(ids) == len(rows) else "!! DUPLICATES"}')

    settled = [r for r in rows
               if r.get('home_score') is not None
               and r.get('away_score') is not None
               and r.get('game_date')]
    settled.sort(key=lambda r: (r['game_date'], r['game_id']))
    print(f'  settled with scores: {len(settled)}')
    by_season = collections.Counter(str(r.get('season')) for r in settled)
    print('  by season: ' + '  '.join(f'{k}={v}' for k, v in
                                      sorted(by_season.items())))
    n_sr = sum(1 for r in settled if r.get('spread_result'))
    n_tr = sum(1 for r in settled if r.get('total_result'))
    print(f'  graded ATS: {n_sr}    graded total: {n_tr}')

    # ---- price validity, counted out loud -------------------------------
    reasons: collections.Counter = collections.Counter()
    for r in settled:
        h, aw = r.get('home_ml_close'), r.get('away_ml_close')
        if h is None or aw is None:
            reasons['no ML pair stored'] += 1
            continue
        ih, ia = implied(h), implied(aw)
        if ih is None or ia is None:
            reasons['unparseable / inside +-100'] += 1
            continue
        if not (ML_MIN <= float(h) <= ML_MAX
                and ML_MIN <= float(aw) <= ML_MAX):
            reasons[f'outside [{ML_MIN},{ML_MAX}] (placeholder)'] += 1
            continue
        ov = ih + ia
        if not (OVERROUND_MIN <= ov <= OVERROUND_MAX):
            reasons[f'overround {OVERROUND_MIN}-{OVERROUND_MAX} violated'] += 1
            continue
        r['_ml_ok'] = True
        r['_imp_home'], r['_imp_away'] = ih, ia
    n_ok = sum(1 for r in settled if r.get('_ml_ok'))
    print(f'\nMONEYLINE PRICE VALIDATION: {n_ok} of {len(settled)} usable')
    for k, v in reasons.most_common():
        print(f'    dropped {v:6d}  {k}')

    # ---- build matchup history, strictly from prior dates ---------------
    hist: dict = collections.defaultdict(list)
    eligible = []
    for g in settled:
        home = str(g.get('home_team') or '').strip()
        away = str(g.get('away_team') or '').strip()
        if not home or not away:
            continue
        key = tuple(sorted((home, away)))
        prior = hist[key]                     # already date-ordered, < today
        if len(prior) >= a.min_meetings:
            last5 = prior[-a.window:]
            wins = covers = overs = 0
            n_cov = n_ov = 0
            for p in last5:
                p_home = str(p.get('home_team') or '').strip()
                p_hs, p_as = p['home_score'], p['away_score']
                winner = p_home if p_hs > p_as else str(
                    p.get('away_team') or '').strip()
                if winner == home:
                    wins += 1
                sr = str(p.get('spread_result') or '').lower()
                if sr in ('home_covered', 'away_covered'):
                    n_cov += 1
                    cov_team = (p_home if sr == 'home_covered'
                                else str(p.get('away_team') or '').strip())
                    if cov_team == home:
                        covers += 1
                tr = str(p.get('total_result') or '').lower()
                if tr in ('over', 'under'):
                    n_ov += 1
                    if tr == 'over':
                        overs += 1
            gap = None
            try:
                gap = (_dt.date.fromisoformat(g['game_date'])
                       - _dt.date.fromisoformat(last5[-1]['game_date'])).days
            except Exception:                                  # noqa: BLE001
                pass
            eligible.append({
                'g': g, 'n_prior': len(prior), 'n_window': len(last5),
                'wins': wins, 'covers': covers, 'overs': overs,
                'n_cov': n_cov, 'n_ov': n_ov, 'gap_days': gap,
            })
        hist[key].append(g)

    print(f'\nELIGIBILITY  (>= {a.min_meetings} prior meetings, '
          f'window = last {a.window})')
    print(f'  pairs that have ever met: {len(hist)}')
    print(f'  eligible games: {len(eligible)} of {len(settled)}')

    # THE DECISIVE TABLE. Matchup history only accumulates with time, so the
    # eligible games pile up in the LATEST seasons -- which is useless if a
    # late season has no stored market. Printing eligibility beside price
    # coverage per season is what turns "the family has no edge" into "the
    # family cannot be measured yet, and here is the season to fix".
    print(f'\n  ELIGIBLE GAMES vs PRICE COVERAGE, BY SEASON')
    print(f'    {"season":10s} {"eligible":>9s} {"ATS":>7s} {"total":>7s} '
          f'{"ML pair":>8s}  usable')
    per: dict = collections.defaultdict(
        lambda: {'e': 0, 'ats': 0, 'tot': 0, 'ml': 0})
    for e in eligible:
        g = e['g']
        d = per[str(g.get('season'))]
        d['e'] += 1
        d['ats'] += 1 if g.get('spread_result') else 0
        d['tot'] += 1 if g.get('total_result') else 0
        d['ml'] += 1 if g.get('_ml_ok') else 0
    for seas in sorted(per):
        d = per[seas]
        verdict = ('OK' if d['ml'] or d['ats']
                   else 'UNUSABLE -- no stored market')
        print(f'    {seas:10s} {d["e"]:9d} {d["ats"]:7d} {d["tot"]:7d} '
              f'{d["ml"]:8d}  {verdict}')
    # WHEN the condition fires, which for a matchup signal is the whole
    # operational story: non-conference months cannot produce prior
    # meetings, so a refit run in November measures a near-dormant signal
    # and a prior set in November goes live on half the board in February
    # ([[feedback_a_rolling_window_outlives_the_season]],
    #  [[feedback_a_detector_on_an_out_of_season_sport]]).
    print('\n  WHEN IT FIRES, BY MONTH (eligibility is seasonal)')
    by_month_all: dict = collections.Counter()
    by_month_el: dict = collections.Counter()
    for g in settled:
        by_month_all[str(g.get('game_date'))[:7]] += 1
    for e in eligible:
        by_month_el[str(e['g'].get('game_date'))[:7]] += 1
    for m in sorted(by_month_all):
        tot = by_month_all[m]
        el = by_month_el.get(m, 0)
        bar = '#' * int(round(40.0 * el / tot)) if tot else ''
        print(f'    {m}  games {tot:5d}  eligible {el:5d}  '
              f'{100.0 * el / tot:5.1f}%  {bar}')

    lost = sum(d['e'] for d in per.values() if not (d['ml'] or d['ats']))
    if lost:
        pct = 100.0 * lost / max(len(eligible), 1)
        print(f'\n    !! {lost} of {len(eligible)} eligible games '
              f'({pct:.0f}%) sit in a season with NO stored market.')
        print('       That is a DATA COVERAGE fact, not a verdict on '
              'matchup history.')
        print('       Recover it with backfill_historical_closing_odds.py '
              '--sport ' + a.sport)
    if not eligible:
        print('\nNo eligible games. Nothing can be measured; this is a '
              'WIRING or COVERAGE fact, not a verdict on the family.')
        return 1
    gaps = sorted(e['gap_days'] for e in eligible
                  if e['gap_days'] is not None)
    if gaps:
        print(f'  days since the most recent prior meeting: '
              f'median {gaps[len(gaps) // 2]}, '
              f'p10 {gaps[len(gaps) // 10]}, p90 {gaps[-len(gaps) // 10]}')
    full = sum(1 for e in eligible if e['n_window'] == a.window)
    print(f'  with a FULL {a.window}-game window: {full}')
    cov_ok = sum(1 for e in eligible if e['n_cov'] == e['n_window'])
    print(f'  whose whole window has graded ATS history: {cov_ok}  '
          f'(h2h_covers_home depends on this)')

    # ---- controls: the same side, h2h ignored ---------------------------
    def ml_cell(cells, back_home: bool):
        u, n, imp_sum, wins = 0.0, 0, 0.0, 0
        for e in cells:
            g = e['g']
            if not g.get('_ml_ok'):
                continue
            hs, as_ = g['home_score'], g['away_score']
            if hs == as_:
                continue
            home_won = hs > as_
            hit = (home_won == back_home)
            price = g['home_ml_close'] if back_home else g['away_ml_close']
            pay = payout(price)
            if pay is None:
                continue
            u += pay if hit else -1.0
            imp_sum += (g['_imp_home'] if back_home else g['_imp_away'])
            wins += 1 if hit else 0
            n += 1
        if not n:
            return None
        return {'n': n, 'rate': wins / n, 'units': u, 'roi': u / n,
                'implied': imp_sum / n}

    def rate_cell(cells, outcome_fn):
        vals = [outcome_fn(e['g']) for e in cells]
        vals = [v for v in vals if v is not None]
        if not vals:
            return None
        return {'n': len(vals), 'rate': sum(vals) / len(vals)}

    def out_home_cover(g):
        sr = str(g.get('spread_result') or '').lower()
        return True if sr == 'home_covered' else (
            False if sr == 'away_covered' else None)

    def out_over(g):
        tr = str(g.get('total_result') or '').lower()
        return True if tr == 'over' else (
            False if tr == 'under' else None)

    # ---- the cells, as the signals name them ---------------------------
    print('\n' + '=' * 78)
    print('MONEYLINE CELLS, priced at the close')
    print('=' * 78)
    ctl_home = ml_cell(eligible, True)
    ctl_away = ml_cell(eligible, False)
    for lab, ctl in (('back HOME on every eligible game', ctl_home),
                     ('back AWAY on every eligible game', ctl_away)):
        if ctl:
            print(f'  CONTROL  {lab:44s} n={ctl["n"]:5d}  '
                  f'{ctl["rate"] * 100:5.1f}%  implied '
                  f'{ctl["implied"] * 100:5.1f}%  ROI {ctl["roi"] * 100:+6.2f}%')

    for sig, lo, hi, back_home, ctl in (
            ('h2h_home_dominant      -> HOME ML', 4, 5, True, ctl_home),
            ('h2h_home_dominant_fade -> AWAY ML', 4, 5, False, ctl_away),
            ('h2h_away_dominant      -> AWAY ML', 0, 1, False, ctl_away),
            ('h2h_away_dominant_fade -> HOME ML', 0, 1, True, ctl_home)):
        cells = [e for e in eligible if lo <= e['wins'] <= hi]
        res = ml_cell(cells, back_home)
        print()
        if not res:
            print(f'  {sig}   n=0  -- no priced game in this cell')
            continue
        thin = '   THIN - NO VERDICT' if res['n'] < a.min_cell else ''
        print(f'  {sig}')
        # ── THE TWO CELLS ARE NOT EQUALLY REACHABLE ──────────────────────
        # A "4-5 wins" cell cannot fire on a 3-game window; a "0-1 wins"
        # cell can. So the dominant-home and dominant-away cells differ in
        # n by an order of magnitude from WINDOW LENGTH alone, not from any
        # difference in the signals. The production signal reads
        # h2h_last5_home_wins, a raw count over however many meetings
        # exist, so it carries the same asymmetry -- this is faithful to
        # the implementation, and printing it stops the bigger n reading as
        # the stronger signal
        # ([[feedback_a_record_is_a_count_verify_the_denominator]]).
        if cells:
            wl = [e['n_window'] for e in cells]
            fullw = sum(1 for w in wl if w == a.window)
            print(f'      window: mean {sum(wl) / len(wl):.2f} of '
                  f'{a.window}, full on {fullw} of {len(cells)} '
                  f'({100.0 * fullw / len(cells):.0f}%)')
        print(f'      n={res["n"]:5d}  hit {res["rate"] * 100:5.1f}%   '
              f'implied {res["implied"] * 100:5.1f}%   '
              f'ROI {res["roi"] * 100:+6.2f}%   '
              f'{res["units"]:+.2f}u{thin}')
        if ctl:
            dz = z_vs(res['rate'], res['n'], ctl['rate'], ctl['n'])
            print(f'      vs DIRECTION CONTROL {ctl["rate"] * 100:5.1f}% '
                  f'(n={ctl["n"]}):  {(res["rate"] - ctl["rate"]) * 100:+5.1f}pp'
                  f'   {dz:+.2f} SE')
            print(f'      vs ITS OWN PRICE     {res["implied"] * 100:5.1f}%:'
                  f'  {(res["rate"] - res["implied"]) * 100:+5.1f}pp'
                  f'   {z_vs(res["rate"], res["n"], res["implied"], res["n"]):+.2f} SE')

    # ---- ATS and totals: a RATE, not a record --------------------------
    for title, cells_def, outcome_fn, side_lab in (
            ('ATS  (no juice stored in this sport -- rate only, '
             'no ROI claimed)',
             [('h2h_covers_home  -> HOME ATS', 'covers', 4, 5, True),
              ('h2h_covers_home_fade -> AWAY ATS', 'covers', 4, 5, False)],
             out_home_cover, 'home cover'),
            ('TOTALS  (no juice stored -- rate only, no ROI claimed)',
             [('h2h_over_streak       -> OVER', 'overs', 4, 5, True),
              ('h2h_under_after_overs -> UNDER', 'overs', 4, 5, False),
              ('h2h_under_streak      -> UNDER', 'overs', 0, 1, False),
              ('h2h_over_after_unders -> OVER', 'overs', 0, 1, True)],
             out_over, 'over')):
        print('\n' + '=' * 78)
        print(title)
        print('=' * 78)
        base = rate_cell(eligible, outcome_fn)
        if not base:
            print('  no graded outcome among eligible games')
            continue
        print(f'  CONTROL  {side_lab} on every eligible game        '
              f'n={base["n"]:5d}  {base["rate"] * 100:5.1f}%')
        print(f'  stated assumption: -110 both ways, breakeven '
              f'{BE_110 * 100:.2f}%')
        for sig, field, lo, hi, take_true in cells_def:
            cells = [e for e in eligible if lo <= e[field] <= hi]
            res = rate_cell(cells, outcome_fn)
            print()
            if not res:
                print(f'  {sig}   n=0')
                continue
            p = res['rate'] if take_true else 1.0 - res['rate']
            ctlp = base['rate'] if take_true else 1.0 - base['rate']
            thin = '   THIN - NO VERDICT' if res['n'] < a.min_cell else ''
            print(f'  {sig}')
            print(f'      n={res["n"]:5d}  hit {p * 100:5.1f}%{thin}')
            print(f'      vs DIRECTION CONTROL {ctlp * 100:5.1f}%:  '
                  f'{(p - ctlp) * 100:+5.1f}pp   '
                  f'{z_vs(p, res["n"], ctlp, base["n"]):+.2f} SE')
            print(f'      vs -110 breakeven    {BE_110 * 100:5.2f}%:  '
                  f'{(p - BE_110) * 100:+5.1f}pp')

    # ---- the sweep, so a non-monotone shape cannot hide ----------------
    print('\n' + '=' * 78)
    print('THE FULL SWEEP -- every discrete value, so a non-monotone shape '
          'shows')
    print('=' * 78)
    for field, outcome_fn, lab in (
            ('wins', lambda g: (None if g['home_score'] == g['away_score']
                                else g['home_score'] > g['away_score']),
             'home wins in window -> HOME ML win rate'),
            ('covers', out_home_cover, 'home covers in window -> home cover'),
            ('overs', out_over, 'overs in window -> OVER rate')):
        print(f'\n  {lab}')
        buckets = collections.defaultdict(list)
        for e in eligible:
            buckets[e[field]].append(e)
        for v in sorted(buckets):
            res = rate_cell(buckets[v], outcome_fn)
            if not res:
                continue
            tag = '' if res['n'] >= a.min_cell else '   thin'
            print(f'      ={v}  n={res["n"]:5d}  '
                  f'{res["rate"] * 100:5.1f}%{tag}')

    print('\n' + '=' * 78)
    print('Read the DIRECTION CONTROL line before the hit rate. A cell that '
          'beats\nthe coin but not the control is a home/favourite detector, '
          'not matchup\nhistory. No verdict is issued below '
          f'n={a.min_cell}.')
    print('=' * 78)
    return 0


if __name__ == '__main__':
    sys.exit(main())
