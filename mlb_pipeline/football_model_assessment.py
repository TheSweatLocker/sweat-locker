#!/usr/bin/env python3
"""Every NFL / NCAAF model, graded against the closing line it has to beat.

WHY (2026-10-05)
----------------
Andy: "full model assessment for NFL/NCAAF all 5 model assessed overall
engine assessed."

Each sport's context table carries several independent spread and total
projections. This grades every one of them the only way that matters: take
the side the model disagrees with the market on, at -110, and see whether it
clears the 52.4% breakeven.

NOT LEAKY. Unlike team_computed_stats (a current-season snapshot that already
contains the games being scored — see _sor_leak_proof.py, where the apparent
65-79% ATS edge collapses to ~50% once refit point-in-time), these columns
are the projection STORED BEFORE the game. They are a genuine out-of-sample
record of what the model actually said at the time.

THE JOIN. nfl_game_context keys game_id as an md5 while nfl_game_results uses
the nflverse form, so they cannot be joined directly
(project_nfl_game_id_mismatch_911). Joined on (game_date, away, home) with
team names canonicalised through nfl_teams.canon, which returns None rather
than guessing.

SIGN. close_spread is the HOME handicap in every sport EXCEPT NFL, where it
is the AWAY line (verified n=7,325). Resolved once, here.

    python football_model_assessment.py
"""
from __future__ import annotations
import collections, os, sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            k, v = _l.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
BE = 110 / 210

from nfl_teams import canon as nfl_canon

SPREAD_MODELS = {
    'NFL': ['projected_spread', 'projected_spread_raw', 'projected_spread_v2',
            'v3_spread', 'v4_spread'],
    'NCAAF': ['projected_spread', 'projected_spread_raw', 'epa_pred_spread',
              'sp_plus_pred_spread'],
}
TOTAL_MODELS = {
    'NFL': ['projected_total', 'panel_pred_total', 'v3_total', 'v4_total'],
    'NCAAF': ['projected_total', 'sp_plus_pred_total', 'sp_plus_matchup_total'],
}


def page(table, params):
    out, off = [], 0
    while True:
        p = dict(params)
        p.update({'limit': 1000, 'offset': off})
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=120, params=p)
        if r.status_code not in (200, 206):
            print(f'  ⚠ {table} {r.status_code}: {r.text[:120]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def roi(w, l):
    return ((w * (100 / 110) - l) / (w + l)) if (w + l) else 0.0


def key(sport, date, away, home):
    if sport == 'NFL':
        a, h = nfl_canon(away), nfl_canon(home)
        if not (a and h):
            return None
        return (str(date)[:10], a, h)
    return (str(date)[:10], str(away or '').strip().lower(),
            str(home or '').strip().lower())


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def run(sport):
    res_tbl = f'{sport.lower()}_game_results'
    ctx_tbl = f'{sport.lower()}_game_context'
    results = {}
    for g in page(res_tbl, {'select': 'game_date,home_team,away_team,home_score,'
                                      'away_score,close_spread,close_total',
                            'game_date': 'gte.2026-08-01'}):
        if g.get('home_score') is None:
            continue
        k = key(sport, g['game_date'], g['away_team'], g['home_team'])
        if k:
            results[k] = g
    cols = ','.join(['game_date', 'home_team', 'away_team', 'close_spread',
                     'close_total'] + SPREAD_MODELS[sport] + TOTAL_MODELS[sport])
    ctx = page(ctx_tbl, {'select': cols, 'game_date': 'gte.2026-08-01'})
    joined = []
    for c in ctx:
        k = key(sport, c['game_date'], c['away_team'], c['home_team'])
        r = results.get(k) if k else None
        if r:
            joined.append((c, r))
    print(f'\n{"="*76}')
    print(f'  {sport}: {len(results)} completed games · {len(ctx)} context rows '
          f'· {len(joined)} joined')
    print(f'  breakeven at -110 = {100*BE:.1f}%   (model must beat the CLOSE)')
    print(f'{"="*76}')

    print('\n  SPREAD MODELS — back the side the model likes vs the close')
    print('  %-26s %14s %7s %9s %7s' % ('model', 'W-L-P', 'hit', 'ROI', 'n'))
    print('  ' + '-' * 70)
    for m in SPREAD_MODELS[sport]:
        w = l = p = 0
        for c, r in joined:
            pred = _f(c.get(m))
            cs = _f(r.get('close_spread'))
            if pred is None or cs is None:
                continue
            hl = -cs if sport == 'NFL' else cs          # home handicap
            mkt_home_margin = -hl
            # Model's own spread is stored in the same convention as
            # close_spread for that sport, so convert it the same way.
            pred_home_margin = -(-pred if sport == 'NFL' else pred)
            if abs(pred_home_margin - mkt_home_margin) < 0.25:
                continue                                # no disagreement
            back_home = pred_home_margin > mkt_home_margin
            adj = (r['home_score'] - r['away_score']) + hl
            if abs(adj) < 1e-9:
                p += 1
            elif (adj > 0) == back_home:
                w += 1
            else:
                l += 1
        if w + l:
            flag = ('EDGE' if w / (w + l) > BE + 0.02 else
                    'FADE' if w / (w + l) < BE - 0.02 else 'flat')
            if w + l < 40:
                flag += ' (n small)'
            print('  %-26s %5d-%-4d-%-3d %6.1f%% %+8.1f%% %6d  %s'
                  % (m, w, l, p, 100 * w / (w + l), 100 * roi(w, l), w + l + p, flag))
        else:
            print('  %-26s no disagreements / no data' % m)

    print('\n  TOTAL MODELS — back over/under vs the close')
    print('  %-26s %14s %7s %9s %7s' % ('model', 'W-L-P', 'hit', 'ROI', 'n'))
    print('  ' + '-' * 70)
    for m in TOTAL_MODELS[sport]:
        w = l = p = 0
        for c, r in joined:
            pred = _f(c.get(m))
            ct = _f(r.get('close_total'))
            if pred is None or ct is None or abs(pred - ct) < 0.5:
                continue
            tot = r['home_score'] + r['away_score']
            if abs(tot - ct) < 1e-9:
                p += 1
            elif (tot > ct) == (pred > ct):
                w += 1
            else:
                l += 1
        if w + l:
            flag = ('EDGE' if w / (w + l) > BE + 0.02 else
                    'FADE' if w / (w + l) < BE - 0.02 else 'flat')
            if w + l < 40:
                flag += ' (n small)'
            print('  %-26s %5d-%-4d-%-3d %6.1f%% %+8.1f%% %6d  %s'
                  % (m, w, l, p, 100 * w / (w + l), 100 * roi(w, l), w + l + p, flag))
        else:
            print('  %-26s no disagreements / no data' % m)


if __name__ == '__main__':
    for sp in ('NFL', 'NCAAF'):
        try:
            run(sp)
        except Exception as e:
            print(f'\n{sp}: FAILED {type(e).__name__}: {e}')
    print()
