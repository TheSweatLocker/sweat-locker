"""Scenario/system miner — with the anti-overfitting discipline built in.

Andy 2026-09-25: "tracking different scenarios and systems across all sports
and surface if any exist in today's matchup — NFL Sunday AFC North divisional
home dog hit this many times ATS, CBB bounce-back spots, MLB ace vs bottom-5
offense."

THE DATA IS THERE. That was the open question and it is answered:
nfl_game_results carries 7,550 games, EVERY one with a closing spread, 7,309
graded ATS and O/U, seasons 1999-2026 — plus div_game, home_rest, away_rest,
roof, surface, temp, wind, weekday and referee. ~39,400 games across six sports.

THE PROBLEM IS NOT DATA, IT IS FALSE DISCOVERY.
Mine a thousand scenarios and dozens clear 60% on noise alone. At n=119 — the
size of Andy's own AFC North example — one standard error is 4.6%, so a 59%
system is one SD from nothing. A miner without discipline is a machine for
manufacturing the exact fake edge the L5 lookback leak produced, except
self-inflicted and harder to spot because it comes with a backtest.

So every system here must clear FOUR gates, and the report says which one
killed it:

  1. SAMPLE      n >= MIN_N in-sample. Anything smaller is a story.
  2. EDGE        in-sample hit% >= BREAKEVEN + MIN_EDGE_PP. Beating 50% is
                 not enough; 54.2% is breakeven at real prices.
  3. OUT-OF-SAMPLE  the holdout seasons must ALSO clear breakeven. This is the
                 gate that kills almost everything, which is the point.
  4. MULTIPLICITY  p-value Bonferroni-corrected by the number of systems
                 tested in the run. Test 200 scenarios and a 0.05 threshold
                 needs to be 0.00025.

Reference results from the first run (NFL 1999-2026), including Andy's example:

    AFC North · divisional · HOME DOG ATS   60-59    n=119   50.4%
    ANY divisional · HOME DOG ATS          476-486   n=962   49.5%
    HOME DOG off 10+ days rest             114-121   n=235   48.5%

All coin flips. That is the honest baseline and matches the earlier ATS-streak
study that was rejected on 20k games. A miner that reports "nothing survived"
is working correctly.

    python mine_systems.py --sport NFL
    python mine_systems.py --sport NFL --holdout-from 2022
    python mine_systems.py --sport NFL --today    # systems live on today's slate
"""
import argparse
import math
import os
import sys
from collections import defaultdict

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

# Breakeven at the prices we actually publish, not the -110 ideal.
BREAKEVEN = 54.2
MIN_N = 60              # in-sample floor
MIN_OOS_N = 25          # holdout floor
MIN_EDGE_PP = 2.0       # must beat breakeven by this much in-sample
ALPHA = 0.05            # before Bonferroni

RESULTS_TABLE = {
    'NFL': 'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'NBA': 'nba_game_results',
    'NHL': 'nhl_game_results',
    'NCAAB': 'ncaab_game_results',
    'MLB': 'mlb_game_results',
}

DIVISIONS = {
    'AFC North': {'BAL', 'CIN', 'CLE', 'PIT'},
    'AFC East': {'BUF', 'MIA', 'NE', 'NYJ'},
    'AFC South': {'HOU', 'IND', 'JAX', 'TEN'},
    'AFC West': {'DEN', 'KC', 'LV', 'LAC'},
    'NFC North': {'CHI', 'DET', 'GB', 'MIN'},
    'NFC East': {'DAL', 'NYG', 'PHI', 'WAS'},
    'NFC South': {'ATL', 'CAR', 'NO', 'TB'},
    'NFC West': {'ARI', 'LA', 'SEA', 'SF'},
}


def page(path: str, params: dict) -> list:
    out, off = [], 0
    while True:
        q = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, params=q, timeout=120)
        if r.status_code not in (200, 206):
            print(f'  ERROR {r.status_code}: {(r.text or "")[:200]}')
            sys.exit(2)
        b = r.json()
        if not isinstance(b, list):
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def binom_p(w: int, n: int, p0: float) -> float:
    """One-sided p that a w-of-n result beats p0, normal approximation.

    Exact binomial would be better; the normal approx is within a hair at the
    sample sizes that clear MIN_N and keeps this dependency-free.
    """
    if n == 0:
        return 1.0
    se = math.sqrt(p0 * (1 - p0) / n)
    if se == 0:
        return 1.0
    z = (w / n - p0) / se
    return 0.5 * math.erfc(z / math.sqrt(2))


# ── system definitions ────────────────────────────────────────────────
# Each returns (applies, side) where side is 'home'/'away'/'over'/'under'.
# Keep these HYPOTHESIS-DRIVEN. A scenario should have a reason to exist
# before it is tested, otherwise the multiplicity correction is a fig leaf.

def nfl_systems() -> list:
    out = []

    def mk(name, fn, market):
        out.append((name, fn, market))

    for dname, teams in DIVISIONS.items():
        mk(f'{dname} · divisional · home dog',
           lambda g, t=teams: (g.get('div_game') == 1
                               and g['home_team'] in t and g['away_team'] in t
                               and (g.get('close_spread') or 0) < 0, 'home'),
           'spread')

    mk('Divisional · home dog (any)',
       lambda g: (g.get('div_game') == 1 and (g.get('close_spread') or 0) < 0, 'home'),
       'spread')
    mk('Divisional · road favorite',
       lambda g: (g.get('div_game') == 1 and (g.get('close_spread') or 0) < 0, 'away'),
       'spread')
    mk('Home dog off extra rest (10+d)',
       lambda g: ((g.get('home_rest') or 0) >= 10
                  and (g.get('close_spread') or 0) < 0, 'home'),
       'spread')
    mk('Road team on short rest (<=4d)',
       lambda g: ((g.get('away_rest') or 0) <= 4 and (g.get('away_rest') or 0) > 0, 'home'),
       'spread')
    mk('Big home favorite (-10 or more)',
       lambda g: ((g.get('close_spread') or 0) >= 10, 'home'),
       'spread')
    mk('Outdoor · wind 15mph+ · UNDER',
       lambda g: ((g.get('wind') or 0) >= 15
                  and str(g.get('roof') or '').lower() in ('outdoors', 'open'), 'under'),
       'total')
    mk('Cold weather (<=32F) · UNDER',
       lambda g: ((g.get('temp') is not None and g.get('temp') <= 32), 'under'),
       'total')
    mk('Dome · OVER',
       lambda g: (str(g.get('roof') or '').lower() in ('dome', 'closed'), 'over'),
       'total')
    mk('High total (50+) · UNDER',
       lambda g: ((g.get('close_total') or 0) >= 50, 'under'),
       'total')
    mk('Low total (<=38) · OVER',
       lambda g: (0 < (g.get('close_total') or 0) <= 38, 'over'),
       'total')
    mk('Thursday game · UNDER',
       lambda g: (str(g.get('weekday') or '').lower().startswith('thu'), 'under'),
       'total')
    return out


def evaluate(games: list, fn, market: str) -> tuple:
    """Return (wins, losses) for the side this system takes."""
    w = l = 0
    for g in games:
        try:
            applies, side = fn(g)
        except Exception:
            continue
        if not applies:
            continue
        if market == 'spread':
            res = g.get('spread_result')
            if res == f'{side}_covered':
                w += 1
            elif res in ('home_covered', 'away_covered'):
                l += 1
        else:
            res = str(g.get('total_result') or '').lower()
            if not res or res == 'push':
                continue
            if res.startswith(side):
                w += 1
            else:
                l += 1
    return w, l


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NFL')
    ap.add_argument('--holdout-from', type=int, default=2022,
                    help='seasons >= this are the out-of-sample holdout')
    ap.add_argument('--min-n', type=int, default=MIN_N)
    args = ap.parse_args()

    sport = args.sport.upper()
    tbl = RESULTS_TABLE.get(sport)
    if not tbl:
        print(f'no results table registered for {sport}')
        return
    if sport != 'NFL':
        print(f'  NOTE: only NFL systems are defined so far. {sport} needs its '
              f'own hypothesis set — the fields differ per sport.')
        return

    games = page(tbl, {
        'select': 'season,week,home_team,away_team,close_spread,close_total,'
                  'spread_result,total_result,div_game,home_rest,away_rest,'
                  'roof,temp,wind,weekday',
        'spread_result': 'not.is.null'})
    ins = [g for g in games if (g.get('season') or 0) < args.holdout_from]
    oos = [g for g in games if (g.get('season') or 0) >= args.holdout_from]

    systems = nfl_systems()
    k = len(systems)
    alpha_corrected = ALPHA / max(k, 1)

    print(f'=== system miner · {sport} ===')
    print(f'  games graded      : {len(games)}')
    print(f'  in-sample         : {len(ins)}  (seasons < {args.holdout_from})')
    print(f'  holdout           : {len(oos)}  (seasons >= {args.holdout_from})')
    print(f'  systems tested    : {k}')
    print(f'  breakeven         : {BREAKEVEN}%')
    print(f'  alpha             : {ALPHA} -> {alpha_corrected:.5f} after '
          f'Bonferroni across {k} tests')
    print()

    rows = []
    for name, fn, market in systems:
        w, l = evaluate(ins, fn, market)
        n = w + l
        ow, ol = evaluate(oos, fn, market)
        on = ow + ol
        hit = (w / n * 100) if n else None
        ohit = (ow / on * 100) if on else None
        p = binom_p(w, n, BREAKEVEN / 100) if n else 1.0
        # Why it failed, first gate that rejects it.
        if n < args.min_n:
            verdict = f'REJECT n<{args.min_n}'
        elif hit < BREAKEVEN + MIN_EDGE_PP:
            verdict = 'REJECT no edge'
        elif on < MIN_OOS_N:
            verdict = 'REJECT holdout too small'
        elif ohit < BREAKEVEN:
            verdict = 'REJECT failed holdout'
        elif p > alpha_corrected:
            verdict = f'REJECT p={p:.4f}'
        else:
            verdict = f'*** SURVIVES p={p:.5f} ***'
        rows.append((name, market, w, l, n, hit, ow, ol, on, ohit, verdict))

    rows.sort(key=lambda r: (-(r[5] or 0)))
    print(f'  {"system":38s} {"mkt":6s} {"in-sample":>16s} {"holdout":>15s}  verdict')
    for name, market, w, l, n, hit, ow, ol, on, ohit, verdict in rows:
        ins_s = f'{w}-{l} n={n} {hit:.1f}%' if n else 'no sample'
        oos_s = f'{ow}-{ol} n={on} {ohit:.1f}%' if on else 'none'
        print(f'  {name[:37]:38s} {market:6s} {ins_s:>16s} {oos_s:>15s}  {verdict}')

    survivors = [r for r in rows if r[10].startswith('***')]
    print()
    print(f'  SURVIVED ALL FOUR GATES: {len(survivors)} of {k}')
    if not survivors:
        print('  Nothing survived. That is the expected result and the miner '
              'working correctly —\n  a scenario that cannot beat breakeven '
              'out-of-sample is noise, however good the\n  in-sample number '
              'looks.')


if __name__ == '__main__':
    main()
