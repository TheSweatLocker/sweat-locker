"""The fade-consensus exception: does the game actually go the other way?

ANDY 2026-10-10: "I dont really care about beating the closing spread, i care
about results of the games. We need to figure out why we are publishing wrong
sides even if the data and projections are going one way."

FINDING 1 — THE CONSENSUS GUARD CANNOT FIRE ON A SPREAD PICK.
ensemble_scorer._fade_consensus_ok exists to stop an auto-fade flipping a
pick when our own models agree with the original side. Its docstring: "if
Monte Carlo + market money + our own runs projection all AGREE with orig_side
by a meaningful margin, the auto-fade is fighting live model consensus.
Suppress the flip in those cases."

It blocks only when `votes_checked >= 2 and votes_for_orig >= 2`. But count
the voters it can actually check per market:

    market=rl     MC: NO rl BRANCH | OC: yes | Jerry: totals only  -> max 1
    market=ml     MC: yes          | OC: yes | Jerry: totals only  -> max 2
    market=total  MC: yes          | OC: yes | Jerry: yes          -> max 3

The Monte Carlo voter has `if market == 'total': ... elif market == 'ml': ...`
and no `rl` arm; the Jerry voter is totals-only. So on a SPREAD pick at most
one vote is ever checked, `votes_checked >= 2` is never satisfied, and the
function always returns True. Verified by calling it directly with every
consensus input maximally agreeing with the original side:

    orig_side=HOME_RL -> True    (flip ALLOWED — guard cannot fire)
    orig_side=HOME_ML -> False   (flip blocked — guard works)
    orig_side=OVER    -> False   (flip blocked — guard works)

NCAAF picks are overwhelmingly `rl`, so for most of the college board the
mechanism meant to stop the engine flipping off our own number is dead code.
That is the structural answer to "why do we publish the other side".

FINDING 2 — A SECOND, SMALLER DEFECT IN THE SAME FUNCTION.
A 2026-08-26 exception lets a "compound fade signal" override the guard, and
its own comment conditions that on "signal_registry ... hit_rate >=0.60 with
n>=15". The function body never queries signal_registry or any hit_rate —
only the two COMMENT lines mention them. The file loads signal_registry and
uses it to weight opinions elsewhere, so the machinery exists; this gate does
not use it. For NCAAF the exception is moot anyway, because it reads
ctx['jerry_pred_spread'] and that is not a column on ncaaf_game_context.

FINDING 3 — AND A CORRECTION TO MY OWN EARLIER CLAIM.
I previously described projected_spread as uniformly "compressed", 15.8
points light on big favourites. That was WRONG as a characterisation. The
error is bimodal: median |our number - market| is 3.4 pts, but p90 is 25.5
and p99 is 60.2. It is concentrated entirely on mismatch lines —

    market fav 0-7   mean |edge|  3.3      14-28  mean |edge|  9.2
    market fav 7-14  mean |edge|  5.8      28+    mean |edge| 26.5

— and ncaaf_game_context.py:1183 already diagnosed exactly why, on
2026-09-26: SP+ exists only for FBS teams, so an FBS-vs-FCS game drops to an
EPA fallback built on known-bad `games` counts and emits a near-zero margin
against a 30-40 point line. "spread_edge then reads as a 30-40 point edge,
which is not an edge, it is the absence of information." The guard added then
(_epa_starved / _absurd_edge, EDGE_SANITY_MAX_PTS=10) only suppresses a
sweat_score boost — it does NOT null projected_spread and does NOT stop the
pick, so the non-number still drives a published side.

CONSEQUENCE FOR READING ANY "we went against our own projection" RATE:
on those games the projection is noise and overriding it is CORRECT, so an
uncontrolled straight-up win rate for "published against our projection"
(74%, 91-32) measures nothing but the size of the favourite. It must be
controlled for the market line, which is what this script does.

WHAT THIS SCRIPT MEASURES — on RESULTS, not on beating the close
Andy's metric. For games where our projection liked one side by >0.5 and we
published the OTHER side:
  1. did the team we published WIN THE GAME (straight up)?
  2. did the side our own projection liked win the game?
  3. the same split for games where we AGREED with our projection,
  4. all of it banded by market favourite size, against the
     back-the-market-favourite baseline — because in the 0-10 bands that
     baseline is 58-63% and nothing here beats it.

WRITES NOTHING.

CLI
    python audit_fade_consensus_gate.py
    python audit_fade_consensus_gate.py --sport NFL
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import statistics
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
CTX = {'NCAAF': 'ncaaf_game_context', 'NFL': 'nfl_game_context'}
RES = {'NCAAF': 'ncaaf_game_results', 'NFL': 'nfl_game_results'}
#: NCAAF stores a NEGATIVE close_spread for a HOME favourite, NFL POSITIVE.
HOME_FAV_SIGN = {'NCAAF': -1.0, 'NFL': +1.0}


def _page(t, p, cap=400000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:150]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _blob(v):
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip().startswith('{'):
        try:
            return json.loads(v)
        except Exception:                                   # noqa: BLE001
            return None
    return None


def show(label, rows, key, floor=20):
    """Report a straight-up win rate. No breakeven framing — Andy asked for
    results, and a straight-up winner has no vig to clear."""
    if len(rows) < floor:
        print(f'    {label:<46}n={len(rows)} under n>={floor}')
        return
    w = sum(r[key] for r in rows)
    pct = w / len(rows) * 100
    se2 = (0.5 / len(rows) ** 0.5) * 100 * 2
    print(f'    {label:<46}{pct:6.1f}%  {w}-{len(rows) - w}  '
          f'n={len(rows):<4} +/-{se2:4.1f}pp')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF', choices=('NCAAF', 'NFL'))
    ap.add_argument('--edge', type=float, default=0.5,
                    help='the half-point threshold the gate itself uses')
    a = ap.parse_args()
    sport, sign = a.sport, HOME_FAV_SIGN[a.sport]

    # NB: jerry_pred_spread is NOT a column on ncaaf_game_context (a 400 names
    # the column). It only ever exists on the in-memory ctx dict, which is
    # itself part of the finding — see the header.
    ctx = _page(CTX[sport], {'select': 'game_id,game_date,home_team,away_team,'
                                       'primary_play,projected_spread,'
                                       'close_spread'})
    res = {}
    for x in _page(RES[sport], {'select': 'game_id,home_score,away_score,'
                                          'close_spread,spread_result'}):
        res[str(x['game_id'])] = x

    rows = []
    for g in ctx:
        r0 = res.get(str(g['game_id']))
        if not r0:
            continue
        hs, as_ = _f(r0.get('home_score')), _f(r0.get('away_score'))
        mkt = _f(r0.get('close_spread'))
        proj = _f(g.get('projected_spread'))
        jp = None
        if None in (hs, as_, mkt, proj) or hs == as_:
            continue
        pp = _blob(g.get('primary_play')) or {}
        side = str(pp.get('side') or '').upper()
        if side not in ('HOME', 'AWAY'):
            continue
        mh = mkt * sign                     # market's home margin
        edge = proj - mh                    # >0 = our number likes HOME
        proj_side = 'HOME' if edge > 0 else 'AWAY'
        home_won = 1 if hs > as_ else 0
        sr = str(r0.get('spread_result') or '').lower()
        rows.append({
            'g': f"{g.get('away_team')} @ {g.get('home_team')}",
            'd': str(g.get('game_date'))[:10],
            'edge': edge, 'absedge': abs(edge),
            'proj_side': proj_side, 'side': side,
            'jp_edge': (jp - mh) if jp is not None else None,
            # Andy's metric: did the team we published WIN THE GAME?
            'pub_won': home_won if side == 'HOME' else 1 - home_won,
            'proj_won': home_won if proj_side == 'HOME' else 1 - home_won,
            'pub_covered': (1 if (sr == 'home_covered') == (side == 'HOME')
                            else 0) if sr in ('home_covered',
                                              'away_covered') else None,
            'margin': hs - as_, 'mh': mh, 'proj': proj,
            'home_won': home_won,
            'tier': str(pp.get('tier') or ''),
        })

    print(f'=== {sport} FADE-CONSENSUS GATE · {len(rows)} games with a final '
          f'score, our projection and a published side')
    if len(rows) < 40:
        print('    too few rows.')
        return 0

    agree = [r for r in rows if r['side'] == r['proj_side']]
    against = [r for r in rows if r['side'] != r['proj_side']]
    print(f'    we AGREED with our own projection on {len(agree)} · '
          f'published AGAINST it on {len(against)}')

    print('\n  DID THE TEAM WE PUBLISHED WIN THE GAME? (straight up)')
    show('published side, when we AGREED with our number', agree, 'pub_won')
    show('published side, when we went AGAINST it', against, 'pub_won')
    print()
    show('our PROJECTION\'s side, on those same against games',
         against, 'proj_won')
    print('    ^ the two lines above are one measurement from both ends:')
    print('      on a game with a winner, backing the other team inverts it.')

    print('\n  THE GATE\'S OWN CONDITION: |edge| > '
          f'{a.edge} and we published the other side.')
    print('  This is the population the half-point test admits.')
    fired = [r for r in against if r['absedge'] > a.edge]
    small = [r for r in against if r['absedge'] <= a.edge]
    show(f'published AGAINST a >{a.edge}pt edge', fired, 'pub_won')
    show(f'  ...control: AGAINST a <={a.edge}pt edge', small, 'pub_won')
    print('\n  BY HOW BIG THE EDGE WE OVERRODE WAS:')
    print('    (read with FINDING 3: a huge "edge" on a mismatch line is the')
    print('     projection being noise, not an edge we overrode)')
    for lo, hi in ((0.5, 3), (3, 7), (7, 14), (14, 999)):
        sub = [r for r in against if lo <= r['absedge'] < hi]
        show(f'  overrode an edge of {lo}-{hi if hi < 999 else "+"} pts',
             sub, 'pub_won')

    print('\n  CONTROLLED FOR THE MARKET LINE — the only fair comparison.')
    print('  Backing a 45-point favourite wins almost always, so an')
    print('  uncontrolled rate measures favourite size, not skill.')
    for hi, lbl in ((3, 'market within 3'), (7, 'market within 7'),
                    (10, 'market within 10')):
        sub = [r for r in rows if abs(r['mh']) < hi]
        if len(sub) < 20:
            continue
        ag = [r for r in sub if r['side'] == r['proj_side']]
        ab = [r for r in sub if r['side'] != r['proj_side']]
        fav = sum(1 for r in sub if (r['mh'] > 0) == (r['home_won'] == 1))
        print(f'    {lbl}  (n={len(sub)})')
        show('    agreed with our number', ag, 'pub_won')
        show('    published against it', ab, 'pub_won')
        print(f'    {"    BASELINE: back the market favourite":<46}'
              f'{fav / len(sub) * 100:6.1f}%  {fav}-{len(sub) - fav}  '
              f'n={len(sub)}')

    print('\n  WHERE BOTH JERRY AND THE PROJECTION AGREED, AND WE WENT THE')
    print('  OTHER WAY — the exact compound condition in the code:')
    both = [r for r in against if r['jp_edge'] is not None
            and r['absedge'] > a.edge and abs(r['jp_edge']) > a.edge
            and (r['edge'] > 0) == (r['jp_edge'] > 0)]
    show('jerry AND projection agreed, we published against', both, 'pub_won')
    if len(both) >= 20:
        show('  ...the side they both liked', both, 'proj_won')
    else:
        jp_pop = sum(1 for r in rows if r['jp_edge'] is not None)
        print(f'    jerry_pred_spread populated on {jp_pop}/{len(rows)} rows'
              f' — cannot isolate the compound condition without it.')

    print('\n  SPREAD COVER, reported second because it is not the metric'
          ' asked for:')
    for lbl, sub in (('agreed with our number', agree),
                     ('published against it', against)):
        cv = [r for r in sub if r['pub_covered'] is not None]
        if len(cv) >= 20:
            w = sum(r['pub_covered'] for r in cv)
            print(f'    {lbl:<46}{w / len(cv) * 100:6.1f}%  '
                  f'{w}-{len(cv) - w}  n={len(cv)}')

    print('\n  WORST OVERRIDES BY EDGE SIZE (we published against our own'
          ' number by the most):')
    for r in sorted(against, key=lambda z: -z['absedge'])[:12]:
        print(f"    {r['d']}  {r['g'][:34]:<34} our# {r['proj']:+6.1f} vs "
              f"mkt {r['mh']:+6.1f} (edge {r['edge']:+5.1f}) -> published "
              f"{r['side']:<4} {r['tier'][:6]:<6} "
              f"{'WON' if r['pub_won'] else 'LOST'} by {abs(r['margin']):.0f}")

    print('\n' + '=' * 74)
    print('  THE CODE DEFECT, independent of these rates:')
    print('  _fade_consensus_ok\'s docstring conditions the flip-permitting')
    print('  exception on "signal_registry ... hit_rate >=0.60 with n>=15".')
    print('  The function body never queries signal_registry or any hit_rate')
    print('  — only two COMMENT lines mention them. So the exception fires on')
    print('  a hardcoded half-point test for every game, with no check that')
    print('  the fade pattern it invokes was ever validated. That is a')
    print('  comment asserting a measurement the code does not perform.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
