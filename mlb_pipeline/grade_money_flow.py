#!/usr/bin/env python3
"""Grade money flow per sport per market, and register it with a DIRECTION.

== 2026-10-20 · WHY ==
ANDY wants the engine weighing "all of these ad external records, with modney
flow data, ATS sitatyionals, cohrots ... for engine pick". Model records landed
in signal_registry tonight; external_pick, situational, cohort and team_form
families were already there. Money flow was the one family carrying NO weight
at all — and it is the most dangerous one to leave unweighted, because we have
measured that ITS DIRECTION FLIPS BY SPORT:

    project_sharp_money_is_a_fade_929        MLB  -> fade the public
    project_sharp_money_football_opposite_930  NCAAF -> follow it
    project_split_flags_opposite_by_sport_1012  two independent families agree

A single global money-flow treatment is therefore guaranteed wrong for about
half the book. So this does not assume a direction anywhere: for each sport,
market and bucket it grades BOTH sides and registers whichever one the data
chose, as direction_hint FOLLOW or FADE.

WHAT IT GRADES. line_snapshot carries the OddsCrowd split per (sport, game,
market): bets_pct is the share of TICKETS, money_pct the share of HANDLE. The
public is tickets. Buckets:
    public_heavy_N    tickets >= N% on one side
    money_vs_bets_N   handle at least N points AWAY from tickets - the classic
                      "few bettors, big money" sharp shape
Each is settled on the final score at the stored closing number.

THREE RULES CARRIED FROM TODAY'S MISTAKES:
  * ml is graded against the PRICE-implied breakeven, never the flat 52.38%.
    Grading moneylines flat is what credited favourite-picking as skill in the
    first model-panel run this evening.
  * nothing under SAMPLE_FLOOR gets a weight, and the threshold is reported on
    an EARLY/LATE split so a bucket that only worked in one half is visible as
    such. A threshold chosen on all the data and reported on all the data is
    just the best of several guesses.
  * a bucket that is reliably WRONG is registered as a FADE rather than
    dropped - being inversely predictive is information.

USAGE
    python grade_money_flow.py --dry-run
    python grade_money_flow.py
"""
from __future__ import annotations
import argparse
import datetime as dt
import math
import os
import sys
from collections import defaultdict

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV = os.path.join(_HERE, '.env')
for _l in (open(_ENV, encoding='utf-8') if os.path.exists(_ENV) else []):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

CTX = {'MLB': 'mlb_game_context', 'NFL': 'nfl_game_context',
       'NCAAF': 'ncaaf_game_context', 'NHL': 'nhl_game_context',
       'NBA': 'nba_game_context'}
RES = {'MLB': 'mlb_game_results', 'NFL': 'nfl_game_results',
       'NCAAF': 'ncaaf_game_results', 'NHL': 'nhl_game_results',
       'NBA': 'nba_game_results'}
#: home handicap = sign * close_spread. NFL/NCAAF verified against 11 books.
HCAP_SIGN = {'NFL': -1.0, 'NCAAF': 1.0, 'NHL': 1.0, 'NBA': 1.0, 'MLB': 1.0}
OPP = {'HOME': 'AWAY', 'AWAY': 'HOME', 'OVER': 'UNDER', 'UNDER': 'OVER'}

SAMPLE_FLOOR = 30
FLAT_BREAKEVEN = 52.38
WEIGHT_BANDS = [(6.0, 1.0), (3.0, 0.7), (1.5, 0.5), (0.0, 0.3)]
ANTI_EDGE = -4.0
PUBLIC_BANDS = (60, 65, 70)
GAP_BANDS = (-15, -10)


def page(tbl, params, order):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, timeout=240,
                         params=dict(params, limit='1000', offset=str(off),
                                     order=order))
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{tbl} -> {r.status_code}: {(r.text or "")[:160]}')
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def nz(s):
    return ''.join(c for c in str(s or '').lower() if c.isalnum())


def implied(a):
    a = f(a)
    if a is None or a == 0:
        return None
    return (100.0 / (a + 100.0) * 100.0) if a > 0 else (abs(a) / (abs(a) + 100.0) * 100.0)


def wilson(w, n):
    if not n:
        return (0.0, 0.0)
    z = 1.96
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - h), 100 * (c + h))


def settle(sport, market, side, g):
    """WIN/LOSS/PUSH for `side` of `market` on game row g."""
    hs, as_ = f(g.get('home_score')), f(g.get('away_score'))
    if hs is None or as_ is None:
        return None
    m = hs - as_
    s = str(side).upper()
    if market in ('rl', 'spread', 'puckline'):
        cs = f(g.get('close_spread'))
        if cs is None or s not in ('HOME', 'AWAY'):
            return None
        hcap = HCAP_SIGN[sport] * cs
        eff = (m + hcap) if s == 'HOME' else (-m - hcap)
        return 'PUSH' if abs(eff) < 1e-9 else ('WIN' if eff > 0 else 'LOSS')
    if market in ('total', 'ou'):
        ct = f(g.get('close_total'))
        if ct is None or s not in ('OVER', 'UNDER'):
            return None
        tot = hs + as_
        if abs(tot - ct) < 1e-9:
            return 'PUSH'
        return 'WIN' if ((s == 'OVER') == (tot > ct)) else 'LOSS'
    if market == 'ml':
        if s not in ('HOME', 'AWAY'):
            return None
        if abs(m) < 1e-9:
            return 'PUSH'
        return 'WIN' if ((s == 'HOME') == (m > 0)) else 'LOSS'
    return None


def weight_for(edge_pp, n, halves=None, be=None, wins=None):
    """Weight and tier, with the EARLY/LATE split actually enforced.

    ══ 2026-10-20 · THE FIRST VERSION REPORTED THE SPLIT AND IGNORED IT ══
    The docstring at the top of this file says the halves are shown "so a
    bucket that only worked in one half is visible as such" - and then the
    tier was computed from the pooled edge alone. The dry run duly handed
    weight 1.00 / VALIDATED to:

        NCAAF public_heavy_65 rl   58.5%  n=94   halves 51% / 66%
        NCAAF public_heavy_70 rl   62.3%  n=53   halves 54% / 70%

    A first half at 51% against a 52.4% breakeven is a coin flip. Whatever
    those buckets are, they are not a stable edge measured across the window -
    they are something that started working recently, which is exactly the
    shape of a regime change or a leak. Reporting that in a column while
    stamping VALIDATED on the row is the mistake in
    feedback_split_the_leak_window_before_quoting, committed by the very
    script that prints the split.

    So the halves now gate the tier: VALIDATED requires BOTH halves to clear
    breakeven. One half below it caps the row at PROVISIONAL and 0.5, which
    keeps the signal alive and in shadow instead of letting it carry full
    weight into an engine pick on half a sample.
    """
    if n < SAMPLE_FLOOR:
        return 0.0, 'UNPROVEN'
    if edge_pp <= ANTI_EDGE:
        return 0.5, 'ANTI_VALIDATED'
    # ══ 2026-10-20 · wilson() WAS DEFINED IN THIS FILE AND NEVER CALLED ══
    # Found reviewing the second dry run. NCAAF public_heavy_70 rl came back
    # VALIDATED at weight 1.00 on 33-20 (62.3%, n=53) with both halves above
    # breakeven, so the half-gate let it through - and its 95% Wilson lower
    # bound is 49.3% against a 52.4% breakeven. The point estimate is high;
    # the interval still contains "no edge". MLB money_vs_bets_15 total is the
    # same story: 57.7% on n=97 decided, lower bound 47.9%.
    #
    # Weight 1.00 means the engine leans on it like a fact. A bucket whose
    # interval spans breakeven is not a fact, and this repo has already paid
    # for that mistake at scale - project_models_dont_beat_the_close_1005
    # ("65-79% ATS was ALL leak"), project_nfl_curated_spread_is_the_leak_1018
    # (27.1% with CI[16.6, 41.0]).
    #
    # So VALIDATED now requires the LOWER BOUND to clear breakeven, not the
    # point estimate. Everything else is capped at PROVISIONAL, which keeps
    # the signal in shadow where it belongs until the sample earns more. The
    # expected consequence is that very little is VALIDATED - that is the
    # finding, not a bug in the gate.
    lower_clears = True
    if wins is not None and be is not None and n:
        lo, _hi = wilson(wins, n)
        lower_clears = lo > be
    both_halves_clear = True
    if halves and be is not None:
        vals = [h for h in halves if h is not None]
        # Only a complete pair can disqualify; one gradeable half is not
        # evidence either way.
        if len(vals) == 2 and min(vals) < be:
            both_halves_clear = False
    for thr, w in WEIGHT_BANDS:
        if edge_pp >= thr:
            if edge_pp >= 3.0 and both_halves_clear and lower_clears:
                return w, 'VALIDATED'
            if edge_pp >= 3.0 and not both_halves_clear:
                return min(w, 0.5), 'PROVISIONAL_ONE_HALF'
            if edge_pp >= 3.0:
                return min(w, 0.5), 'PROVISIONAL_WIDE_CI'
            return w, 'PROVISIONAL'
    return 0.0, 'REJECTED'



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc).isoformat()

    snaps = page('line_snapshot',
                 {'select': 'sport,game_id,market,pick_side,bets_pct,'
                            'money_pct,snapshot_ts'}, 'snapshot_ts.desc')
    # the LAST snapshot per (sport, game, market) is the settled public number
    last = {}
    for s in snaps:
        k = (str(s.get('sport')), str(s.get('game_id')), str(s.get('market')))
        if k not in last:
            last[k] = s
    print(f'line_snapshot rows {len(snaps)} · unique (game, market) {len(last)}')

    ctx_idx, res_idx, res_gid = {}, {}, {}
    for sport in CTX:
        try:
            for c in page(CTX[sport], {'select': '*'}, 'game_date.desc'):
                ctx_idx[(sport, str(c.get('game_id')))] = c
        except RuntimeError as e:
            print(f'  !! ctx {sport}: {e}')
        try:
            for g in page(RES[sport], {'select': '*'}, 'game_date.desc'):
                if g.get('home_score') is None:
                    continue
                res_idx[(sport, nz(g.get('away_team')), nz(g.get('home_team')),
                         str(g.get('game_date'))[:10])] = g
                # ══ 2026-10-20 · A PRUNED CONTEXT ROW MUST NOT LOSE THE GAME ══
                # First run reported `no context row: 1967` - 68% of every
                # split we hold. It was NOT a join bug: measured per sport,
                # snapshot game_ids match context at NFL 100%, NCAAF 99%,
                # NHL 100%, NBA 100%. MLB matched 3%, because
                # mlb_game_context retains only 28 rows while line_snapshot
                # holds 675 MLB games - the known retention behaviour, not a
                # mismatch.
                # mlb_game_results keeps 4,833 rows and carries the closing
                # line AND the final score, and its game_id matches the
                # snapshots on 99% (671 of 675), with close_spread on 670,
                # close_total on 667 and a final score on 668. So results is
                # indexed by game_id too and used when context is gone.
                # MLB goes from 48 gradeable games to ~668 - and MLB is the
                # sport where the fade is actually documented
                # (project_sharp_money_is_a_fade_929).
                if g.get('game_id') is not None:
                    res_gid[(sport, str(g.get('game_id')))] = g
        except RuntimeError as e:
            print(f'  !! results {sport}: {e}')

    rows = []
    unresolved = defaultdict(int)
    for (sport, gid, market), s in last.items():
        if market not in ('rl', 'spread', 'total', 'ml'):
            continue
        c = ctx_idx.get((sport, gid))
        if c:
            g = res_idx.get((sport, nz(c.get('away_team')),
                             nz(c.get('home_team')),
                             str(c.get('game_date'))[:10]))
        else:
            # No context row (see the note above - MLB retention, not a join
            # bug). The results row alone carries everything needed: closing
            # line, final score and date. `c` is then set to it so every
            # downstream reader keeps working; the price lookup below already
            # reads both naming conventions out of (c, g).
            g = res_gid.get((sport, gid))
            c = g
        if not g:
            unresolved['no final score'] += 1
            continue
        if not c.get('game_date'):
            unresolved['no game_date'] += 1
            continue
        bets, money = f(s.get('bets_pct')), f(s.get('money_pct'))
        ps = str(s.get('pick_side') or '').upper()
        if bets is None or ps not in OPP:
            unresolved['no split or side'] += 1
            continue
        # which side is the PUBLIC on
        if bets >= 50:
            public, pub_bets = ps, bets
            pub_money = money
        else:
            public, pub_bets = OPP[ps], 100.0 - bets
            pub_money = None if money is None else 100.0 - money
        mk = 'rl' if market in ('rl', 'spread', 'puckline') else market
        # ══ 2026-10-20 · PRICE EACH SIDE, DO NOT COMPLEMENT THE OTHER ══
        # The first run took only the PUBLIC side's implied probability and
        # used (100 - that) as the breakeven when fading. That is the VIG-FREE
        # complement, not the dog's actual price: the two sides' implied
        # probabilities sum to well over 100, so complementing UNDERSTATES a
        # fade's breakeven and flatters every ml fade. Same family of error as
        # grading ml against a flat 52.38% an hour ago.
        imp_home = imp_away = None
        if mk == 'ml':
            for src in (c, g):
                for cc in ('close_home_ml', 'home_ml_close'):
                    if imp_home is None:
                        imp_home = implied(src.get(cc))
                for cc in ('close_away_ml', 'away_ml_close'):
                    if imp_away is None:
                        imp_away = implied(src.get(cc))
        rows.append({'sport': sport, 'market': mk, 'public': public,
                     'bets': pub_bets, 'money': pub_money,
                     'gap': None if pub_money is None else pub_money - pub_bets,
                     'date': str(c.get('game_date'))[:10], 'g': g,
                     'imp_home': imp_home, 'imp_away': imp_away})
    rows.sort(key=lambda r: r['date'])
    print(f'gradeable split rows: {len(rows)}   unresolved: {dict(unresolved)}')

    payloads = []
    bysport = defaultdict(list)
    for r in rows:
        bysport[r['sport']].append(r)

    for sport in sorted(bysport):
        srows = bysport[sport]
        print(f'\n{"="*100}\n{sport} · {len(srows)} games with a split\n{"="*100}')
        print(f'  {"bucket":<22}{"market":<7}{"side":<7}{"W-L-P":>12}{"hit%":>8}'
              f'{"be":>8}{"edge":>8}{"wt":>6}  tier       early/late')
        for market in ('rl', 'total', 'ml'):
            mrows = [r for r in srows if r['market'] == market]
            if len(mrows) < SAMPLE_FLOOR:
                if mrows:
                    print(f'  {"(all)":<22}{market:<7}{"":7}{len(mrows):>12}'
                          f'   n<{SAMPLE_FLOOR} — not graded')
                continue
            buckets = [(f'public_heavy_{b}', [r for r in mrows if r['bets'] >= b])
                       for b in PUBLIC_BANDS]
            buckets += [(f'money_vs_bets_{abs(b)}',
                         [r for r in mrows if r['gap'] is not None and r['gap'] <= b])
                        for b in GAP_BANDS]
            for bname, brows in buckets:
                if len(brows) < SAMPLE_FLOOR:
                    continue
                # grade BOTH sides; the data picks the direction
                best = None
                for direction in ('FADE', 'FOLLOW'):
                    w = l = p = 0
                    imps = []
                    for r in brows:
                        side = OPP[r['public']] if direction == 'FADE' else r['public']
                        out = settle(sport, market, side, r['g'])
                        if out == 'WIN':
                            w += 1
                        elif out == 'LOSS':
                            l += 1
                        elif out == 'PUSH':
                            p += 1
                        if market == 'ml':
                            # the ACTUAL posted price of the side taken
                            taken = (r['public'] if direction == 'FOLLOW'
                                     else OPP[r['public']])
                            pi = (r['imp_home'] if taken == 'HOME'
                                  else r['imp_away'])
                            if pi is not None:
                                imps.append(pi)
                    n = w + l
                    if n < SAMPLE_FLOOR:
                        continue
                    if market == 'ml':
                        if len(imps) < max(10, n // 2):
                            continue
                        be = sum(imps) / len(imps)
                    else:
                        be = FLAT_BREAKEVEN
                    hit = 100.0 * w / n
                    edge = hit - be
                    if best is None or edge > best['edge']:
                        best = {'direction': direction, 'w': w, 'l': l, 'p': p,
                                'n': n, 'hit': hit, 'be': be, 'edge': edge,
                                'rows': brows}
                if not best:
                    continue
                # early/late, so a bucket that only worked once is visible
                half = len(best['rows']) // 2
                halves, half_pct = [], []
                for part in (best['rows'][:half], best['rows'][half:]):
                    hw = hl = 0
                    for r in part:
                        side = (OPP[r['public']] if best['direction'] == 'FADE'
                                else r['public'])
                        out = settle(sport, market, side, r['g'])
                        if out == 'WIN':
                            hw += 1
                        elif out == 'LOSS':
                            hl += 1
                    halves.append(f'{100.0*hw/(hw+hl):.0f}%' if (hw + hl) else '-')
                    half_pct.append(100.0 * hw / (hw + hl) if (hw + hl) else None)
                weight, tier = weight_for(best['edge'], best['n'],
                                          half_pct, best['be'],
                                          wins=best['w'])
                print(f'  {bname:<22}{market:<7}{best["direction"]:<7}'
                      f'{f"{best["w"]}-{best["l"]}-{best["p"]}":>12}'
                      f'{best["hit"]:>7.1f}%{best["be"]:>7.1f}%'
                      f'{best["edge"]:>+8.1f}{weight:>6.2f}  {tier:<21} '
                      f'{halves[0]} / {halves[1]}'
                      f'   CI[{wilson(best["w"], best["n"])[0]:.0f},'
                      f'{wilson(best["w"], best["n"])[1]:.0f}]')
                payloads.append({
                    'signal_name': f'moneyflow_{bname}_{market}',
                    'sport': sport, 'market_scope': market,
                    'category': 'money_flow',
                    'description': (f'Public tickets bucket {bname} on {market}. '
                                    f'Measured direction {best["direction"]} for '
                                    f'{sport}; breakeven {best["be"]:.2f}%. '
                                    f'Halves {halves[0]} / {halves[1]}. '
                                    f'Wilson CI '
                                    f'[{wilson(best["w"], best["n"])[0]:.1f},'
                                    f'{wilson(best["w"], best["n"])[1]:.1f}].'),
                    'hit_rate': round(best['hit'], 2),
                    'sample_n': best['n'],
                    'edge_pp': round(best['edge'], 2),
                    'tier': tier,
                    'recommended_weight': weight,
                    'direction_hint': ('FADE' if (best['direction'] == 'FADE')
                                       != (tier == 'ANTI_VALIDATED') else 'FOLLOW'),
                    'origin': f'MONEYFLOW_GRADE_{dt.date.today().isoformat()}',
                    'last_computed_at': now, 'updated_at': now,
                })

    print(f'\n{"="*100}\nregistry rows to write: {len(payloads)}')
    bt = defaultdict(int)
    for pl in payloads:
        bt[f'{pl["tier"]}/{pl["direction_hint"]}'] += 1
    print('  by tier/direction:', dict(bt))
    if a.dry_run:
        print('  (dry run — nothing written)')
        return 0
    if not payloads:
        print('  nothing to write')
        return 0
    r = requests.post(
        f'{SB}/rest/v1/signal_registry?on_conflict=signal_name,sport,market_scope',
        headers=H_WRITE, json=payloads, timeout=120)
    print(f'  write HTTP {r.status_code}' + ('' if r.status_code in (200, 201, 204)
                                             else f' {r.text[:200]}'))
    back = page('signal_registry', {'select': 'signal_name',
                                    'category': 'eq.money_flow'}, 'signal_name.asc')
    print(f'  money_flow rows now in registry: {len(back)}')
    return 0 if r.status_code in (200, 201, 204) else 1


if __name__ == '__main__':
    raise SystemExit(main())
