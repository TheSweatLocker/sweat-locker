"""Are our stored closing lines numbers a book actually POSTED?

== 2026-11-11 . NCAAB WAS 79% FABRICATED AND NOTHING WAS WATCHING ======
`ncaab_odds_pull._consensus_spread` AVERAGED across books until it was
fixed on 2026-11-02, and `ncaab_game_context._avg_market_line` did the
same. Everything written before that carries a number no book could take
a bet on -- close_spread values like -45.25, -44.93, -43.38, -1.28, 0.15.

A fake line makes a fake cover result
(feedback_consensus_line_must_be_posted), so every spread and total
signal weight fit on that history was fit on fabricated outcomes. Four
scripts read those columns for measurement -- backtest_ncaab_signals,
refit_signal_registry, calibrate_spread_split,
refresh_prop_signal_calibration -- and on the day this was found, NONE of
them screened for it.

First run, every sport's results table:

    sport   column            checked  postable  NOT postable    %bad
    NCAAB   close_spread         5373      1116          4257   79.2%
    NCAAB   close_total          5374      1053          4321   80.4%
    NBA     close_spread         2584      2584             0    0.0%
    NBA     close_total          2584      2584             0    0.0%
    NFL     close_spread         7550      7550             0    0.0%
    NFL     close_total          7550      7550             0    0.0%
    NCAAF   close_spread         6833      6833             0    0.0%
    NCAAF   close_total          6823      6823             0    0.0%
    NHL     close_puckline       2819      2819             0    0.0%
    NHL     close_total          2912      2899            13    0.4%
    MLB     close_spread         2157      2157             0    0.0%
    MLB     close_total          2212      2212             0    0.0%

NCAAB alone. Every contaminated row was WRITTEN before the 2026-11-02 fix
-- including the four whose GAME dates fall in November 2026, which were
written 2026-10-24/25 and read as a live regression until this audit was
taught to bucket by created_at. ncaab_game_context since 2026-11-10 is 0
unpostable, so the live writers are clean. The
point of running this on a schedule is that a writer can regress, and the
only cheap way to notice is that the stored numbers stop sitting on a half
point.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not repair or delete rows. The games are real and their final
scores are real; only the line is wrong, and we cannot recover a line that
was never recorded. The fix that matters is that MEASUREMENT skips those
fields, which belongs in the backtests (backtest_ncaab_signals does this
now, per field rather than per row).

It also does not flag moneylines as unpostable by the half-point test,
because an American price has no such granularity. The right test for a
two-way price is the implied HOLD: a posted pair holds roughly 2-10%, and
a modelled or averaged pair holds about zero or goes negative, which no
book offers. All 4,922 NCAAB two-way ML pairs pass that test -- mean
+4.32%, min +1.63%, max +6.16%, zero pairs under 0.5% -- so the
moneylines on those same contaminated rows are genuine and usable.

USAGE
    python audit_unpostable_lines.py
    python audit_unpostable_lines.py --sport NCAAB
    python audit_unpostable_lines.py --max-bad-pct 1.0   # exit 1 over this
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _l in _env.read_text().split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

from posted_line import is_postable          # noqa: E402  the one definition

#: Per sport: results table, then the line columns to test. NHL's spread is
#: `close_puckline`; naming close_spread there 400s and the read comes back
#: empty, which reads as "clean" (feedback_select_star_before_concluding_absent).
TABLES = {
    'NCAAB': ('ncaab_game_results', ('close_spread', 'close_total')),
    'NBA':   ('nba_game_results',   ('close_spread', 'close_total')),
    'NFL':   ('nfl_game_results',   ('close_spread', 'close_total')),
    'NCAAF': ('ncaaf_game_results', ('close_spread', 'close_total')),
    'NHL':   ('nhl_game_results',   ('close_puckline', 'close_total')),
    'MLB':   ('mlb_game_results',   ('close_spread', 'close_total')),
}


def _page(table: str, col: str) -> list:
    """Every non-null value of `col`, with the date, paged past the 1k cap."""
    out, offset = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=180,
                         params={col: 'not.is.null', 'select': f'{col},game_date,created_at',
                                 'limit': '1000', 'offset': str(offset)})
        if r.status_code != 200:
            raise RuntimeError(f'{table}.{col} read failed {r.status_code}: '
                               f'{r.text[:200]}')
        page = r.json()
        out.extend(page)
        if len(page) < 1000:
            return out
        offset += 1000


def run(only: str | None = None, max_bad_pct: float = 1.0) -> int:
    sports = [only.upper()] if only else list(TABLES)
    print('== audit_unpostable_lines ==')
    print('a closing line a book posted sits on a half point; an AVERAGE does not\n')
    print(f"{'sport':7s} {'column':16s} {'checked':>8s} {'postable':>9s} "
          f"{'NOT postable':>13s} {'%bad':>6s}")
    worst = 0.0
    offenders: list[tuple[str, str, float, dict]] = []
    for sp in sports:
        if sp not in TABLES:
            print(f'{sp:7s} no results table registered — skip')
            continue
        table, cols = TABLES[sp]
        for col in cols:
            try:
                rows = _page(table, col)
            except RuntimeError as e:
                print(f'{sp:7s} {col:16s} READ FAILED — {e}')
                return 2
            vals = [r for r in rows if r.get(col) is not None]
            bad = [r for r in vals if is_postable(r[col]) is False]
            pct = 100.0 * len(bad) / len(vals) if vals else 0.0
            worst = max(worst, pct)
            flag = '  <-- CONTAMINATED' if pct > max_bad_pct else ''
            print(f'{sp:7s} {col:16s} {len(vals):8d} {len(vals) - len(bad):9d} '
                  f'{len(bad):13d} {pct:5.1f}%{flag}')
            if pct > max_bad_pct:
                offenders.append((sp, col, pct,
                                  dict(sorted(Counter(
                                      r['game_date'][:7] for r in bad
                                      if r.get('game_date')).items())),
                                  dict(sorted(Counter(
                                      str(r['created_at'])[:7] for r in bad
                                      if r.get('created_at')).items()))))
    for sp, col, pct, by_game, by_written in offenders:
        print()
        print(f'  {sp}.{col} - {pct:.1f}% unpostable')
        print(f'    by GAME date   : {by_game}')
        print(f'    by WRITTEN date: {by_written}   <-- the one that names a live writer')
        print('    A written-date range that STOPS is a legacy backfill. One that'
              ' reaches this month is a writer still averaging now.')
    if offenders:
        print(f'\n  FAIL: {len(offenders)} column(s) over {max_bad_pct:.1f}% '
              f'unpostable. Measurement on those fields is measurement on '
              f'numbers no book offered.')
        return 1
    print(f'\n  OK: every column at or under {max_bad_pct:.1f}% unpostable')
    return 0


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--sport')
    p.add_argument('--max-bad-pct', type=float, default=1.0)
    a = p.parse_args()
    raise SystemExit(run(only=a.sport, max_bad_pct=a.max_bad_pct))
