#!/usr/bin/env python3
"""Grade EVERY model, per sport, per MARKET — and set its engine weight.

== 2026-10-20 · WHY ==
ANDY: "i want to keep allmodel records and have engine weigh them approatley
by how they perfom if one model is great at getting spreads or totals rigth it
shoudl be wieghed accordingly vice a mdoel that ahs a bad track record ... all
of these ad external records, with modney flow data, ATS sitatyionals, cohrots
should all be weighed for engine pick" — then "i want this complete for every
spoprt tonight".

THE MACHINERY ALREADY EXISTED; THE MODELS WERE NOT IN IT. signal_registry
holds 1,166 rows, every one with a recommended_weight, and ensemble_scorer
already scales each signal's contribution by its hit rate above breakeven,
per sport, with a staleness cutoff. market_scope already splits ml / rl /
total. What was missing is that the MODELS were barely registered, and the few
that were sat on junk samples:

    jerry_pred_spread        MLB ml     hit 25.0%  n=8
    mc_strong_home_ml        MLB ml     hit 16.7%  n=6
    nba_elo_edge_home_spread NBA rl     hit  0.0%  n=1

A weight set off n=6 is noise with authority. This grades every model on every
market it can actually speak to, and writes a weight that is earned.

HOW A MODEL IS GRADED — it has to make a real bet, not have an opinion:
    ml     side = whichever team the model's margin favours
    rl     the model's margin against the POSTED HANDICAP: does its side cover
    total  the model's total against the CLOSE: over or under
Each is then settled on the final score. A model with no number for a game
simply does not vote on it, and the n reflects that.

WEIGHT IS EARNED, NOT ASSUMED:
  * below SAMPLE_FLOOR the weight is 0.0 and the tier is UNPROVEN. The
    standing rule is n>=30 before a percentage means anything, and the junk
    rows above are exactly what ignoring it produces.
  * above the floor, weight scales with measured edge over that market's
    breakeven. A model that cannot beat breakeven gets 0.0 however famous it
    is; one that clearly beats it gets up to 1.0.
  * a model whose edge is clearly NEGATIVE is marked ANTI_VALIDATED with
    direction_hint FADE rather than silently zeroed - being reliably wrong is
    information, and the engine already knows how to read a fade.

BREAKEVEN IS PER MARKET, not a constant: 52.38% on rl/total where both sides
are about -110, and price-implied on ml, where it moves with the favourite.
Treating them alike is what made the pooled 56.8% meaningless.

USAGE
    python grade_model_panel.py --dry-run
    python grade_model_panel.py
    python grade_model_panel.py --sport NHL
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
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
#: home handicap = sign * close_spread. NFL/NCAAF verified against 11 books in
#: October; the others are inferred by whichever orientation minimises the
#: market's own MAE and PRINTED, so an unverified sport is visible not assumed.
HCAP_SIGN = {'NFL': -1.0, 'NCAAF': 1.0, 'NHL': 1.0, 'NBA': 1.0, 'MLB': 1.0}

SAMPLE_FLOOR = 30
FLAT_BREAKEVEN = 52.38
#: edge in percentage points over breakeven -> weight
WEIGHT_BANDS = [(6.0, 1.0), (3.0, 0.7), (1.5, 0.5), (0.0, 0.3)]
ANTI_EDGE = -4.0

#: Every model tile the app renders, per sport, as (name, margin_fields,
#: total_fields). A field may be a column or an (a, b) pair that subtracts to a
#: margin. These are the SAME columns LensGrid reads, so a model's registered
#: record is the record of the number the user actually saw.
PANEL = {
    'MLB': [
        ('Panel', ['panel_implied_margin'], ['panel_implied_total']),
        ('Jerry', ['jerry_pred_spread'], ['jerry_pred_total']),
        ('v3', ['projected_spread'], ['projected_total']),
        ('v4', ['model_pred_spread'], ['model_pred_total']),
        ('MC', ['mc:mc_expected_margin'], ['mc:mc_expected_total']),
    ],
    'NFL': [
        ('v3', ['projected_spread'], ['projected_total']),
        ('v4', ['v4_spread', ('model_pred_home_points', 'model_pred_away_points')],
         ['v4_total', 'model_pred_total']),
        ('Panel', [('panel_pred_home_pts', 'panel_pred_away_pts')], ['panel_pred_total']),
        ('MC', ['mc:mc_expected_margin'], ['mc:mc_expected_total']),
    ],
    'NCAAF': [
        ('v3', ['projected_spread'], ['projected_total']),
        ('SP+', ['sp_plus_pred_spread', ('sp_plus_pred_home_pts', 'sp_plus_pred_away_pts')],
         ['sp_plus_pred_total']),
        ('v4', [('model_pred_home_points', 'model_pred_away_points')], ['model_pred_total']),
        ('EPA', ['epa_pred_spread'], []),
        ('MC', ['mc:mc_expected_margin'], ['mc:mc_expected_total']),
    ],
    'NHL': [
        ('v3', ['projected_spread'], ['projected_total']),
        ('MC', ['mc:mc_expected_margin'], ['mc:mc_expected_total']),
    ],
    'NBA': [
        ('v3', ['projected_spread'], ['projected_total']),
        ('MC', ['mc:mc_expected_margin'], ['mc:mc_expected_total']),
    ],
}


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


def jd(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return {}
    return v if isinstance(v, dict) else {}


def pick_field(ctx, mc, fields):
    """First field that resolves. 'mc:x' reads the MC blob; a tuple subtracts."""
    for fld in fields:
        if isinstance(fld, tuple):
            a, b = f(ctx.get(fld[0])), f(ctx.get(fld[1]))
            if a is not None and b is not None:
                return a - b
            continue
        if isinstance(fld, str) and fld.startswith('mc:'):
            v = f(mc.get(fld[3:]))
            if v is not None:
                return v
            continue
        v = f(ctx.get(fld))
        if v is not None:
            return v
    return None


def american_to_implied(a):
    """American odds -> implied win probability in PERCENT."""
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


def weight_for(edge_pp, n):
    if n < SAMPLE_FLOOR:
        return 0.0, 'UNPROVEN'
    if edge_pp <= ANTI_EDGE:
        return 0.5, 'ANTI_VALIDATED'
    for thr, w in WEIGHT_BANDS:
        if edge_pp >= thr:
            return w, ('VALIDATED' if edge_pp >= 3.0 else 'PROVISIONAL')
    return 0.0, 'REJECTED'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', action='append')
    ap.add_argument('--since', default='2026-06-01')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    payloads = []

    for sport in (a.sport or ['MLB', 'NFL', 'NCAAF', 'NHL', 'NBA']):
        res = {}
        # select '*' because the results schemas differ per sport:
        # nhl_game_results has no close_spread at all, and naming it 400s the
        # whole sport out of the grading (NHL was skipped entirely on the
        # first run for exactly that reason).
        try:
            rows = page(RES[sport], {'select': '*'}, 'game_date.desc')
        except RuntimeError as e:
            print(f'\n=== {sport} === results unreadable: {e}')
            continue
        for x in rows:
            if x.get('home_score') is None or x.get('away_score') is None:
                continue
            res[(nz(x['away_team']), nz(x['home_team']),
                 str(x['game_date'])[:10])] = x
        ctxs = page(CTX[sport], {'select': '*', 'game_date': f'gte.{a.since}'},
                    'game_date.desc')
        games = []
        for c in ctxs:
            g = res.get((nz(c.get('away_team')), nz(c.get('home_team')),
                         str(c.get('game_date'))[:10]))
            if not g:
                continue
            games.append({'ctx': c, 'mc': jd(c.get('mc_probabilities')),
                          'margin': f(g['home_score']) - f(g['away_score']),
                          'total': f(g['home_score']) + f(g['away_score']),
                          'cs': f(c.get('close_spread')) if f(c.get('close_spread')) is not None
                          else f(g.get('close_spread')),
                          'ct': f(c.get('close_total')) if f(c.get('close_total')) is not None
                          else f(g.get('close_total')),
                          # ML column naming differs per sport, verified
                          # rather than guessed: home_ml_close on MLB/NBA,
                          # close_home_ml on NFL/NCAAF/NHL. Results carry it
                          # too when context does not.
                          'ml_home': (f(c.get('close_home_ml'))
                                      or f(c.get('home_ml_close'))
                                      or f(g.get('close_home_ml'))
                                      or f(g.get('home_ml_close'))),
                          'ml_away': (f(c.get('close_away_ml'))
                                      or f(c.get('away_ml_close'))
                                      or f(g.get('close_away_ml'))
                                      or f(g.get('away_ml_close')))})
        sign = HCAP_SIGN[sport]
        print(f'\n{"="*96}\n{sport} · {len(games)} games joined since {a.since}\n{"="*96}')
        if not games:
            print('  no joined games — nothing to grade')
            continue
        print(f'  {"model":<8}{"market":<8}{"W-L-P":>12}{"hit%":>8}{"breakeven":>11}'
              f'{"edge":>8}{"weight":>8}  tier')

        ml_prices = defaultdict(list)
        for name, mfields, tfields in PANEL.get(sport, []):
            buckets = defaultdict(lambda: [0, 0, 0])
            for g in games:
                m = pick_field(g['ctx'], g['mc'], mfields)
                # ── moneyline: the side the model's margin favours ──
                if m is not None and abs(m) > 1e-9:
                    want_home = m > 0
                    if abs(g['margin']) < 1e-9:
                        buckets['ml'][2] += 1
                    else:
                        won = (g['margin'] > 0) == want_home
                        buckets['ml'][0 if won else 1] += 1
                        # the implied probability of the side THIS model took
                        pr = g['ml_home'] if want_home else g['ml_away']
                        d = american_to_implied(pr)
                        if d is not None:
                            ml_prices[name].append(d)
                # ── spread: does the model's side cover the posted number ──
                if m is not None and g['cs'] is not None:
                    hcap = sign * g['cs']
                    edge = m - (-hcap)          # model margin vs market margin
                    if abs(edge) > 1e-9:
                        side_home = edge > 0
                        eff = (g['margin'] + hcap) if side_home else (-g['margin'] - hcap)
                        if abs(eff) < 1e-9:
                            buckets['rl'][2] += 1
                        else:
                            buckets['rl'][0 if eff > 0 else 1] += 1
                # ── total: the model's total against the close ──
                t = pick_field(g['ctx'], g['mc'], tfields) if tfields else None
                if t is not None and g['ct'] is not None and abs(t - g['ct']) > 1e-9:
                    want_over = t > g['ct']
                    if abs(g['total'] - g['ct']) < 1e-9:
                        buckets['total'][2] += 1
                    else:
                        won = (g['total'] > g['ct']) == want_over
                        buckets['total'][0 if won else 1] += 1

            for market in ('ml', 'rl', 'total'):
                w, l, p = buckets[market]
                n = w + l
                if n == 0:
                    continue
                hit = 100.0 * w / n
                # ══ 2026-10-20 · A MONEYLINE DOES NOT BREAK EVEN AT 52.38% ══
                # The first run graded ml against the flat number and handed
                # NCAAF models 68-74% "hit rates" with +21 edge and weight
                # 1.00. That is not skill: in college the better side wins
                # about 70% of the time, so a model that simply picks the
                # favourite "hits" 73% — at -400, which loses money. Crediting
                # that would have the engine weight favourite-picking as its
                # strongest signal.
                #
                # So ml is graded against the PRICE the favourite was actually
                # offered at, averaged over the games the model voted on. With
                # no price there is no breakeven, and the honest answer is to
                # not grade ml at all rather than invent one — reported, not
                # silently skipped.
                if market == 'ml':
                    prices = ml_prices[name]
                    if len(prices) < max(10, n // 2):
                        print(f'  {name:<8}{market:<8}{f"{w}-{l}-{p}":>12}'
                              f'{hit:>7.1f}%     NO PRICE on {n - len(prices)} '
                              f'of {n} — ml not graded')
                        continue
                    be = sum(prices) / len(prices)
                else:
                    be = FLAT_BREAKEVEN
                edge_pp = hit - be
                weight, tier = weight_for(edge_pp, n)
                lo, _hi = wilson(w, n)
                print(f'  {name:<8}{market:<8}{f"{w}-{l}-{p}":>12}{hit:>7.1f}%'
                      f'{be:>10.1f}%{edge_pp:>+8.1f}{weight:>8.2f}  {tier}'
                      + ('' if n >= SAMPLE_FLOOR else f'  (n<{SAMPLE_FLOOR})'))
                payloads.append({
                    'signal_name': f'model_{name.lower().replace("+", "plus")}_{market}',
                    'sport': sport,
                    'market_scope': market,
                    'category': 'model',
                    'description': (f'{name} model graded on {market}: its own '
                                    f'number turned into a bet and settled on '
                                    f'the final score. Breakeven {be:.2f}%.'),
                    'hit_rate': round(hit, 2),
                    'sample_n': n,
                    'edge_pp': round(edge_pp, 2),
                    'tier': tier,
                    'recommended_weight': weight,
                    'direction_hint': 'FADE' if tier == 'ANTI_VALIDATED' else 'FOLLOW',
                    'origin': f'MODEL_PANEL_GRADE_{dt.date.today().isoformat()}',
                    'last_computed_at': now,
                    'updated_at': now,
                })

    print(f'\n{"="*96}\nregistry rows to write: {len(payloads)}')
    by_tier = defaultdict(int)
    for p in payloads:
        by_tier[p['tier']] += 1
    print('  by tier:', dict(by_tier))
    if a.dry_run:
        print('  (dry run — nothing written)')
        return 0
    wrote = 0
    for i in range(0, len(payloads), 200):
        chunk = payloads[i:i + 200]
        r = requests.post(
            f'{SB}/rest/v1/signal_registry'
            f'?on_conflict=signal_name,sport,market_scope',
            headers=H_WRITE, json=chunk, timeout=120)
        if r.status_code not in (200, 201, 204):
            print(f'  x write failed {r.status_code}: {(r.text or "")[:220]}')
            return 1
        wrote += len(chunk)
    back = page('signal_registry', {'select': 'signal_name',
                                    'category': 'eq.model',
                                    'origin': f'like.MODEL_PANEL_GRADE_%'},
                'signal_name.asc')
    print(f'  wrote {wrote} · model rows now in registry: {len(back)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
