"""Fade the public: measured per SPORT and per MARKET, on closing money.

══ 2026-10-30 · BEFORE GIVING THE FADE A WEIGHT IN THE ENGINE ═════════════
ANDY, on the Fade publishing the opposite side of our own Sweat Card:
"dont know how i feel about engine saying one thing and another feature in
app like the fade fading our own."

Agreed, and the plan is to stop running it as a separate surface and make it
a weighted term inside the one equation -- so a strong public-money read
CHANGES the engine's pick instead of publishing against it. That only works
if the signal is real, and "the fade" has already shipped one unvalidated
market: project_fade_totals_are_negative_1023 found 26 of 27 receipts were
TOTALS while the backtest that justified them was SPREADS.

So this measures it per sport AND per market before any weight is assigned.
Money flow is documented to invert by sport
(project_split_flags_opposite_by_sport_1012), so one pooled number would be
worse than none.

METHOD
  * line_snapshot, deduped to the CLOSING snapshot per (sport, game_id,
    market). The raw table holds ~15 snapshots per game, and counting all of
    them would multiply-count one opinion
    (feedback_unordered_data_zero_picks_a_vintage).
  * pick_side is the side the PUBLIC is on and money_pct is that side's
    share -- confirmed, not assumed: min 50, median 70, max 98, and 97% of
    rows are above 50, which is what a majority-side column looks like
    (feedback_check_which_side_a_column_indexes).
  * Graded from {sport}_game_results: spread_result, home_win, total_result.
    Pushes excluded.
  * NFL IS EXCLUDED. Its join rate is 0 of 58 -- line_snapshot carries the
    Odds API hash while nfl_game_results keys on the schedule id, the same
    mismatch resolve_nfl_results.fetch_result_map documents. That is a
    WIRING gap, not an absence of edge
    (feedback_zero_rows_is_a_wiring_error), and bridging it is R80.
    Join rates for the rest: NCAAF 99.5%, NBA 100%, NHL 100%, MLB 99.4%.

THE RESULT, money_pct >= 65% (fade% = how often the public's side LOST)

    sport  mkt      n   fade%        95% CI        split-half
    NCAAF  rl     130   59.2%  [50.8, 67.7]   55.4% / 63.1%   <- best
    MLB    rl     366   55.2%  [50.1, 60.3]   55.2% / 55.2%   <- stable
    MLB    total  298   52.7%  [47.0, 58.4]   49.0% / 56.4%
    NCAAF  total  198   48.5%  [41.5, 55.4]   46.5% / 50.5%
    NHL    rl      36   38.9%  [23.0, 54.8]   38.9% / 38.9%   <- INVERTED
    NCAAF  ml     143   35.0%  [27.1, 42.8]   21.1% / 48.6%
    MLB    ml     422   45.0%  [40.3, 49.8]   47.9% / 42.2%
    NBA    ml      44   31.8%  [18.1, 45.6]   31.8% / 31.8%

WHAT IT SAYS, and the three limits on it

1. SPREADS ONLY. NCAAF rl 59.2% and MLB rl 55.2% are the only cells that
   beat the 52.38% spread breakeven, and MLB's is identical in both halves
   (55.2 / 55.2), which is the most convincing stability in the table. NBA
   rl is 55.2% but n=67 at the >=50% threshold and too thin to weight yet.

2. NHL INVERTS. 38.9% at >=65% and 40.0% at >=50%, the same in both halves.
   On NHL the public's spread side WINS. A pooled "fade spreads" weight
   would be actively wrong there, which is the per-sport sign the money-flow
   finding warned about.

3. THE MONEYLINE NUMBERS ARE NOT AN EDGE EITHER WAY. Public ML sides win
   55-68% here, which looks like "follow the public" -- but the public is on
   favourites and ML breakeven RISES with the price: books average -120..-203
   so breakeven is 56-58%, not 52.38%
   (feedback_flat_110_inflates_every_ml_figure). At those prices a 55-65%
   hit rate is roughly breakeven. Read as NO ML edge, not as a follow
   signal, and do not price one without each row's own odds.

   TOTALS: nothing clears anywhere. Consistent with the 10-23 finding.

   Honest caveat on the best cell: even NCAAF rl has a lower CI bound of
   50.8, under the 52.38 breakeven. This is the best-measured thing we have
   and it is still not a lock.

WHAT TO BUILD FROM IT
A market-structure signal that fires ONLY on spreads, with a per-sport sign,
NHL excluded, ML and totals excluded. Its value is not only its hit rate: it
is the one family that systematically argues the side the rest of the
library never does. 2,755 of 3,584 attributed signal sides say HOME, and
measure_family_weight_leverage.py shows families never disagree at all on
MLB (0/202) or NBA (0/56). Heavy public money sits on the favourite, so this
signal opposes the favourite -- it manufactures the disagreement that makes
weighting mean anything.

USAGE
  python measure_public_fade_by_market.py
  python measure_public_fade_by_market.py --heavy 70 --min-n 50
"""
from __future__ import annotations

import argparse
import collections
import math
import os
import sys
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

#: NFL omitted deliberately -- see the module docstring. Adding it here
#: without bridging the game_id would report 0 rows as 0 edge.
RESULT_TABLE = {
    'NCAAF': 'ncaaf_game_results',
    'NBA': 'nba_game_results',
    'NHL': 'nhl_game_results',
    'MLB': 'mlb_game_results',
}

#: Spread breakeven at -110. ML breakeven is NOT this -- it rises with the
#: price, which is why no ML verdict is printed.
BREAKEVEN_SPREAD = 52.38


def page(table: str, params: dict):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['offset'] = off
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p,
                         timeout=120)
        if r.status_code != 200:
            print(f'  ! {table} HTTP {r.status_code} {r.text[:140]}')
            return None
        d = r.json()
        out += d
        if len(d) < 1000:
            return out
        off += 1000


def ci95(w: int, n: int):
    if not n:
        return (0.0, 0.0)
    p = w / n
    h = 1.96 * math.sqrt(max(p * (1 - p), 1e-9) / n)
    return (100.0 * max(0.0, p - h), 100.0 * min(1.0, p + h))


def public_won(market: str, side: str, res: dict):
    """Did the side the PUBLIC is on win? None when ungradeable."""
    s = str(side or '').upper()
    m = str(market or '').lower()
    if m in ('rl', 'spread'):
        sr = str(res.get('spread_result') or '').lower()
        if not sr or 'push' in sr:
            return None
        if s == 'HOME':
            return sr == 'home_covered'
        if s == 'AWAY':
            return sr == 'away_covered'
        return None
    if m == 'ml':
        hw = res.get('home_win')
        if hw is None:
            return None
        return bool(hw) if s == 'HOME' else (not bool(hw))
    if m == 'total':
        tr = str(res.get('total_result') or '').lower()
        if not tr or 'push' in tr:
            return None
        if s == 'OVER':
            return tr == 'over'
        if s == 'UNDER':
            return tr == 'under'
        return None
    return None


def closing_snapshots():
    """One row per (sport, game_id, market): the latest snapshot."""
    # bets_pct is selected even though the money-share table does not
    # use it: run_divergence needs it, and an explicit SELECT that omits
    # a column hands the caller a KeyError or a silent blank rather than
    # an error (feedback_explicit_select_silent_blanks). Leaving it out
    # made the whole follow table print EMPTY on the first run.
    snap = page('line_snapshot',
                {'select': 'sport,game_id,market,pick_side,money_pct,'
                           'bets_pct,snapshot_ts'})
    if snap is None:
        return None
    latest = {}
    for r in snap:
        k = (str(r.get('sport')).upper(), str(r.get('game_id')),
             str(r.get('market')).lower())
        cur = latest.get(k)
        if cur is None or str(r.get('snapshot_ts')) > str(cur.get('snapshot_ts')):
            latest[k] = r
    return latest


def run(heavy: float, min_n: int) -> int:
    latest = closing_snapshots()
    if latest is None:
        print('CANNOT VERIFY -- treating as a failure')
        return 2
    results = {}
    joined = collections.Counter()
    seen = collections.Counter()
    for sport, tbl in RESULT_TABLE.items():
        rows = page(tbl, {'select': 'game_id,game_date,home_win,'
                                    'spread_result,total_result'})
        if rows is None:
            return 2
        for x in rows:
            results[(sport, str(x['game_id']))] = x

    agg = collections.defaultdict(lambda: [0, 0, []])
    for (sport, gid, market), r in latest.items():
        if sport not in RESULT_TABLE:
            continue
        seen[sport] += 1
        res = results.get((sport, gid))
        if not res:
            continue
        joined[sport] += 1
        try:
            if r.get('money_pct') is None or float(r['money_pct']) < heavy:
                continue
        except (TypeError, ValueError):
            continue
        pub = public_won(market, r.get('pick_side'), res)
        if pub is None:
            continue
        a = agg[(sport, market)]
        a[1] += 1
        if not pub:
            a[0] += 1
        a[2].append((str(res.get('game_date')), not pub))

    print(f'=== fade the public · closing snapshot · money_pct >= {heavy:g}% '
          f'===')
    print('join rate (a low rate is a WIRING gap, not an absence of edge):')
    for sport in RESULT_TABLE:
        n, j = seen[sport], joined[sport]
        print(f'   {sport:6s} {j:5d}/{n:<5d} '
              f'{(100.0 * j / n) if n else 0:5.1f}%')
    print(f'\n{"sport":6s} {"mkt":6s} {"n":>5s} {"fade%":>7s} {"95% CI":>16s}'
          f'   split-half            verdict')
    best = []
    for (sport, market), (w, n, ds) in sorted(agg.items()):
        if n < min_n:
            continue
        lo, hi = ci95(w, n)
        ds.sort()
        h = len(ds) // 2
        e, l = ds[:h], ds[h:]
        ew = sum(1 for _, x in e if x)
        lw = sum(1 for _, x in l if x)
        pct = 100.0 * w / n
        if market in ('rl', 'spread'):
            if pct > BREAKEVEN_SPREAD and min(ew / max(1, len(e)),
                                              lw / max(1, len(l))) * 100 \
                    > 50.0:
                verdict = 'FADE (holds both halves)'
                best.append((sport, market, pct, n))
            elif pct < 45.0:
                verdict = 'INVERTED - public side wins'
            else:
                verdict = 'no edge'
        elif market == 'ml':
            verdict = 'ML breakeven is 56-58% - not priced here'
        else:
            verdict = 'no edge' if abs(pct - 50) < 4 else 'unstable'
        print(f'{sport:6s} {market:6s} {n:5d} {pct:6.1f}% '
              f'[{lo:5.1f},{hi:5.1f}]  {100.0 * ew / max(1, len(e)):5.1f}%'
              f' n={len(e):<4d}/{100.0 * lw / max(1, len(l)):5.1f}% '
              f'n={len(l):<4d} {verdict}')

    print('\n--- what may carry a weight ---')
    if not best:
        print('nothing clears breakeven and holds both halves at this '
              'threshold.')
    for sport, market, pct, n in sorted(best, key=lambda x: -x[2]):
        print(f'  {sport} {market}: {pct:.1f}% n={n}')
    print('\nSpreads only. Per-sport sign. ML and totals carry no weight, '
          'and a sport absent above is NOT a zero -- it is unmeasured.')
    return 0


def run_divergence(min_div: float, min_n: int) -> int:
    """The OTHER predicate: money share exceeding bets share, followed.

    == 2026-10-30 . THE LIVE SIGNALS USE THIS ONE, AND IT DOES NOT WORK ==
    The engine already has a public-money family -- signal_sources class
    `oddscrowd_direction`, eight rows, several enabled. They do NOT key on
    money share. align_status_common.py:196 sets

        fade_note = 'boost'   # money heavier than bets = sharp

    so `fade == 'boost'` means money% > bets% and side_expr takes `pick`,
    the side the money is on. Despite "fade" in every one of their names
    they are FOLLOW-the-sharp-money signals, and side_expr is right for
    that.

    Which means the money-share table above does not judge them, and my
    first reading of their enablement as "backwards" was comparing two
    different predicates. This measures theirs.

    It does not clear anywhere. NCAAF rl -- the market with the most data --
    is 45.5% at >=10pp, actively negative, and its halves run 54.5 -> 36.4.
    MLB ml reaches 57.1% n=191 and is stable across halves (56.8 / 57.3),
    but ML breakeven RISES with the price to 56-58%, so that is breakeven,
    not edge (feedback_flat_110_inflates_every_ml_figure).

    So the engine's live public-money family fires on a predicate with no
    measured support, while the predicate that DOES clear on spreads --
    heavy money share, faded -- has no signal at all.
    """
    latest = closing_snapshots()
    if latest is None:
        return 2
    results = {}
    for sport, tbl in RESULT_TABLE.items():
        rows = page(tbl, {'select': 'game_id,game_date,home_win,'
                                    'spread_result,total_result'})
        if rows is None:
            return 2
        for x in rows:
            results[(sport, str(x['game_id']))] = x

    agg = collections.defaultdict(lambda: [0, 0, []])
    for (sport, gid, market), r in latest.items():
        res = results.get((sport, gid))
        if not res:
            continue
        try:
            div = float(r['money_pct']) - float(r['bets_pct'])
        except (TypeError, ValueError, KeyError):
            continue
        if div < min_div:
            continue
        w = public_won(market, r.get('pick_side'), res)
        if w is None:
            continue
        a = agg[(sport, market)]
        a[1] += 1
        if w:
            a[0] += 1
        a[2].append((str(res.get('game_date')), w))

    print(f'\n=== FOLLOW the sharp side (money% - bets% >= {min_div:g}pp) '
          f'— the predicate the LIVE signals use ===')
    print(f'{"sport":6s} {"mkt":6s} {"n":>5s} {"win%":>7s} {"95% CI":>16s}'
          f'   split-half            verdict')
    for (sport, market), (w, n, ds) in sorted(agg.items()):
        if n < min_n:
            continue
        lo, hi = ci95(w, n)
        ds.sort()
        h = len(ds) // 2
        e, l = ds[:h], ds[h:]
        ew = sum(1 for _, x in e if x)
        lw = sum(1 for _, x in l if x)
        pct = 100.0 * w / n
        if market == 'ml':
            verdict = 'ML breakeven 56-58% - at best breakeven'
        elif pct < 50.0:
            verdict = 'NEGATIVE - following loses'
        elif lo > BREAKEVEN_SPREAD:
            verdict = 'clears'
        else:
            verdict = 'does not clear'
        print(f'{sport:6s} {market:6s} {n:5d} {pct:6.1f}% '
              f'[{lo:5.1f},{hi:5.1f}]  {100.0 * ew / max(1, len(e)):5.1f}%'
              f' n={len(e):<4d}/{100.0 * lw / max(1, len(l)):5.1f}% '
              f'n={len(l):<4d} {verdict}')
    print('\nThe live oddscrowd_direction signals key on THIS predicate. '
          'Nothing here clears, and NCAAF rl is negative.')
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--heavy', type=float, default=65.0,
                    help='minimum money_pct on the public side (default 65)')
    ap.add_argument('--min-div', type=float, default=10.0,
                    help='minimum money%%-bets%% for the follow table')
    ap.add_argument('--min-n', type=int, default=30)
    a = ap.parse_args()
    rc = run(a.heavy, a.min_n)
    rc2 = run_divergence(a.min_div, a.min_n)
    raise SystemExit(rc or rc2)


if __name__ == '__main__':
    main()
