# -*- coding: utf-8 -*-
"""Audit stored closing prices per season: coverage AND plausibility.

== WHY THIS EXISTS (2026-11-27) =========================================
Shipped beside the 2025-26 NCAAB closing-odds backfill. That run writes
~5,000 rows of market data into the exact columns grading compares against,
so it needs a check that is not "the script said it patched N rows".

Two questions, and coverage alone answers only the first:

  1. IS IT THERE? Per season, how many games carry a spread, a total, a
     moneyline pair, and a graded ATS/total result. This is the table that
     turned "NCAAB matchup history has no edge" into "97% of its eligible
     games sit in a season with no stored market" -- the whole 2025-26
     season was 0/5,521 on every price column while the registry quietly
     tiered NCAAB signals as if three seasons had been measured.

  2. IS IT COHERENT? A filled column is not necessarily a price. This repo
     has shipped on a stored value that could not be bet -- NHL
     close_puckline holding MONEYLINE prices (-290..+240) when a puck line
     is always +/-1.5. So every moneyline pair is scored by OVERROUND --
     implied(home) + implied(away). A real book sits near 1.02-1.08. Below
     1.00 is an arb nobody posted; far above it is a stale or mismatched
     pair.

     VALIDATE A PRICE BY ITS OVERROUND, NOT BY ITS SIGN OR ITS SIZE. Both
     of today's first guesses were wrong, and the overround settled both:

       * Two NEGATIVE moneylines are normal on a pickem -- -109/-110 sums
         to 1.045. I called 238 such NCAAB rows "an impossible market pair"
         from the sign alone.
       * An EXTREME price is not a placeholder. 452 NCAAB rows carry
         moneylines past +/-5000, out to -100000/+3300, and I called them
         placeholders. All 452 have a coherent overround (1.01-1.06), sit
         on a mean 26.6-point closing spread, and the favourite won 450 of
         452. They are real prices for real mismatches.

     That second one matters for weighting, not just for labelling.
     Dropping those rows from a breakeven BAR would remove precisely the
     prices a favourite signal cannot beat, flattering every such signal --
     cherry-picking in the exact direction that manufactures edge
     ([[feedback_the_bar_is_never_the_price_the_signal_fires_at]],
     [[feedback_a_juice_band_cannot_catch_a_market_error]]). So this audit
     reports them as EXTREME and does not count them against the sport, and
     refit_signal_registry deliberately keeps them in the bar.

Also reports the SPREAD/TOTAL juice columns, because their absence is what
stops NCAAB signals being priced at the bar they fire at: the line is
stored, the price never is
([[feedback_ncaab_engine_weights_a_favourite_detector]],
[[feedback_the_bar_is_never_the_price_the_signal_fires_at]]).

Paging is KEYSET on game_id ([[feedback_offset_paging_on_a_nonunique_key_fabricates]],
[[feedback_deep_offset_paging_times_out]]).

Exit 1 if any season has prices but more than --max-bad-pct of its
moneyline pairs implausible. A season with NO prices exits 0 with a loud
note -- that is a coverage gap to fix, not a corruption to block on.

    python audit_market_price_coverage.py --sport NCAAB
    python audit_market_price_coverage.py --sport NHL --verbose
"""
from __future__ import annotations

import argparse
import collections
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

SPORTS = {
    'NCAAB': {'table': 'ncaab_game_results', 'line': 'close_spread'},
    'NBA': {'table': 'nba_game_results', 'line': 'close_spread'},
    'NHL': {'table': 'nhl_game_results', 'line': 'close_puckline'},
    'NFL': {'table': 'nfl_game_results', 'line': 'close_spread'},
    'NCAAF': {'table': 'ncaaf_game_results', 'line': 'close_spread'},
}

# A book's two-way moneyline. Outside this, the pair was not posted.
OV_LO, OV_HI = 1.00, 1.15
ML_ABS_MAX = 5000


def implied(odds):
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0 or -100 < o < 100:
        return None
    return (-o / (-o + 100.0)) if o < 0 else (100.0 / (o + 100.0))


def keyset(table: str, select: str, chunk: int = 1000) -> list:
    out, last = [], ''
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=120,
                         params={'select': select, 'order': 'game_id.asc',
                                 'limit': chunk, 'game_id': f'gt.{last}'})
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{table} {r.status_code}: {r.text[:300]}')
        b = r.json()
        if not isinstance(b, list) or not b:
            break
        out += b
        if len(b) < chunk:
            break
        last = b[-1]['game_id']
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAB', choices=sorted(SPORTS))
    ap.add_argument('--max-bad-pct', type=float, default=5.0,
                    help='fail if >this %% of a priced season is implausible')
    ap.add_argument('--verbose', action='store_true',
                    help='print example implausible rows')
    a = ap.parse_args()

    if not SB or not KEY:
        print('! SUPABASE_URL / SUPABASE_KEY not set')
        return 2

    cfg = SPORTS[a.sport]
    line_col = cfg['line']
    sel = (f'game_id,game_date,season,home_team,away_team,home_score,'
           f'{line_col},close_total,home_ml_close,away_ml_close,'
           f'spread_result,total_result')
    try:
        rows = keyset(cfg['table'], sel)
    except RuntimeError as e:
        # A 400 names the column that does not exist in THIS sport.
        print(f'! read failed -- the select may name a column this sport '
              f'does not have:\n  {e}')
        return 2

    print('=' * 86)
    print(f'MARKET PRICE COVERAGE AND PLAUSIBILITY   {a.sport}   '
          f'{cfg["table"]}')
    print('=' * 86)
    print(f'\n{len(rows)} rows (keyset on game_id)')

    per = collections.defaultdict(lambda: collections.Counter())
    bad_examples = collections.defaultdict(list)
    ov_all = []
    for r in rows:
        s = str(r.get('season') or '(none)')
        d = per[s]
        d['rows'] += 1
        if r.get('home_score') is None:
            d['unsettled'] += 1
            continue
        d['settled'] += 1
        if r.get(line_col) is not None:
            d['line'] += 1
        if r.get('close_total') is not None:
            d['total_line'] += 1
        if r.get('spread_result'):
            d['ats_graded'] += 1
        if r.get('total_result'):
            d['tot_graded'] += 1
        h, aw = r.get('home_ml_close'), r.get('away_ml_close')
        if h is None or aw is None:
            continue
        d['ml_pair'] += 1
        ih, ia = implied(h), implied(aw)
        why = None
        if ih is None or ia is None:
            why = 'unparseable / inside +-100'
        else:
            ov = ih + ia
            ov_all.append(ov)
            if ov < OV_LO:
                why = f'overround {ov:.3f} < {OV_LO} (an arb nobody posted)'
            elif ov > OV_HI:
                why = f'overround {ov:.3f} > {OV_HI}'
        if why:
            d['ml_bad'] += 1
            if len(bad_examples[s]) < 4:
                bad_examples[s].append((r, why))
        else:
            d['ml_ok'] += 1
            # Real, coherent, and far outside a bettable stake. Counted so
            # a unit-ROI calculation can exclude them KNOWINGLY, never as
            # a data-quality reject.
            try:
                if (abs(float(h)) > ML_ABS_MAX
                        or abs(float(aw)) > ML_ABS_MAX):
                    d['ml_extreme'] += 1
            except (TypeError, ValueError):
                pass

    hdr = (f'  {"season":10s} {"settled":>8s} {"line":>7s} {"total":>7s} '
           f'{"ATS":>7s} {"totG":>7s} {"MLpair":>7s} {"MLok":>7s} '
           f'{"MLbad":>6s} {"extreme":>8s}')
    print('\n' + hdr)
    print('  ' + '-' * (len(hdr) - 2))
    rc = 0
    for s in sorted(per):
        d = per[s]
        print(f'  {s:10s} {d["settled"]:8d} {d["line"]:7d} '
              f'{d["total_line"]:7d} {d["ats_graded"]:7d} '
              f'{d["tot_graded"]:7d} {d["ml_pair"]:7d} {d["ml_ok"]:7d} '
              f'{d["ml_bad"]:6d} {d["ml_extreme"]:8d}')
        if d['settled'] and not (d['line'] or d['ml_pair']):
            print(f'      !! NO STORED MARKET AT ALL in {s} '
                  f'({d["settled"]} settled games). Nothing in this season '
                  f'can be graded against a close.')
            print('         Recover with backfill_historical_closing_odds.py '
                  f'--sport {a.sport}')
        if d['ml_pair']:
            pct = 100.0 * d['ml_bad'] / d['ml_pair']
            if pct > a.max_bad_pct:
                print(f'      !! {d["ml_bad"]} of {d["ml_pair"]} moneyline '
                      f'pairs implausible ({pct:.1f}%) -- over the '
                      f'{a.max_bad_pct}% bar')
                rc = 1
            elif d['ml_bad']:
                print(f'      ~  {d["ml_bad"]} of {d["ml_pair"]} implausible '
                      f'({pct:.1f}%), under the bar')
            if d['ml_extreme']:
                print(f'      i  {d["ml_extreme"]} pair(s) past '
                      f'+/-{ML_ABS_MAX} -- REAL prices on big mismatches, '
                      f'not rejects. Keep them in a\n         breakeven '
                      f'bar; exclude them from unit ROI only, and say so.')
            if a.verbose:
                for r, why in bad_examples[s]:
                    print(f'         {r["game_date"]} '
                          f'{str(r["away_team"])[:18]:18s} @ '
                          f'{str(r["home_team"])[:18]:18s} '
                          f'{r["home_ml_close"]}/{r["away_ml_close"]}  {why}')

    if ov_all:
        ov_all.sort()
        n = len(ov_all)
        print(f'\nOVERROUND on {n} plausible pairs  '
              f'(a real book sits ~1.02-1.08)')
        for lab, idx in (('p05', n // 20), ('median', n // 2),
                         ('p95', min(n - 1, 19 * n // 20))):
            print(f'    {lab:7s} {ov_all[idx]:.4f}')

    # The juice question, separate from the line question.
    print('\nSPREAD / TOTAL JUICE')
    probe = requests.get(f'{SB}/rest/v1/{cfg["table"]}', headers=H,
                         timeout=30, params={'select': '*', 'limit': 1})
    cols = sorted(probe.json()[0].keys()) if probe.status_code == 200 else []
    juice = [c for c in cols
             if any(t in c for t in ('juice', 'vig', 'price'))
             and 'ml' not in c]
    if juice:
        print(f'    columns present: {juice}')
    else:
        print(f'    NONE. {a.sport} stores the LINE and never the PRICE, so '
              f'no spread or total\n    signal here can be priced at the bar '
              f'it actually fires at -- a rate\n    against a -110 '
              f'assumption is the most that can be claimed.')

    print('\n' + '=' * 86)
    return rc


if __name__ == '__main__':
    sys.exit(main())
