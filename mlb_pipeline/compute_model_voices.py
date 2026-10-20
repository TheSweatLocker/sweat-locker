#!/usr/bin/env python3
"""Compute each sport's PANEL of independent model voices and publish them.

== 2026-10-20 · WHY ==
ANDY: "I want eavh model to wrok as an indepenedtent opinion ... As if it was
a panel of broadcaster with opinions based on real data, maybe model X favors
homefield advantage over model Y ... THis shoudl eb for evey sport" — then
"lets continue and get it done then".

fit_independent_voices.py proved the panel is real. This is the production
half: it refits the coefficients from history on every run, computes each
voice's own margin for the upcoming slate, and publishes them for the app.

WHY IT REFITS EVERY RUN rather than carrying constants. A hardcoded slope is
a number nobody re-checks — the same failure mode as the hand-written
calibration banner that drifted into quoting a record that did not exist. The
fit is cheap and the alternative is a stale constant presented as a model.

EVERY VOICE SHIPS WITH ITS OWN TRACK RECORD (corr, MAE, n, and the market MAE
it is judged against). That is deliberate: a panel member is allowed to be a
weak voice, but the user should be able to see which ones have earned their
seat. NCAAF EXPLOSIVE reads corr -0.01 and it travels WITH that number rather
than being quietly dropped or quietly promoted.

WHERE IT WRITES, and why not a new column: jerry_cache under
`voices_<YYYY-MM-DD>`, one row holding every sport's games. Adding a JSONB
column to five context tables would need a migration, and two migrations are
already sitting unapplied — a third would just block this behind them.

USAGE
    python compute_model_voices.py --dry-run
    python compute_model_voices.py
    python compute_model_voices.py --date 2026-10-21
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import math
import os
import sys

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

sys.path.insert(0, _HERE)
# Reuse the fitter's voice definitions and helpers verbatim, so the panel
# published here cannot drift from the panel that was validated.
from fit_independent_voices import (                           # noqa: E402
    VOICES, CTX, RES, HCAP_SIGN, page, f, nz, jd, fit,
    load_team_stats, ts_gap,
)

#: history window the coefficients are fitted on
FIT_SINCE = '2026-09-01'
#: a voice needs this many fitted games before it is published at all
MIN_FIT_N = 25
#: ══ A VOICE WITH NO CORRELATION IS A CONSTANT, NOT AN OPINION ══
#: NCAAF EXPLOSIVE fits to slope -0.92 / intercept +15.09 / corr -0.01, which
#: means it says "home by 15" on every game regardless of the input. That is a
#: constant wearing a model's name - the same trap as the week whose only
#: CI-clearing slope (+0.567) was matched by a constant (+0.564).
#: Andy's instruction is not to REMOVE models, so these are still published and
#: still visible, but flagged `weak` with the reason so the app can show them
#: without counting them as a seat on the panel.
MIN_OPINION_CORR = 0.10


def et_today():
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--days-ahead', type=int, default=3)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    base = dt.date.fromisoformat(a.date) if a.date else et_today()
    dates = [(base + dt.timedelta(days=i)).isoformat()
             for i in range(a.days_ahead + 1)]
    payload = {'_generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
               '_fit_since': FIT_SINCE, 'sports': {}}

    for sport in ('NHL', 'NBA', 'NFL', 'NCAAF'):
        print(f'\n=== {sport} ===')
        # ── 1. fit on history ──────────────────────────────────────────
        res = {}
        for x in page(RES[sport], {'select': 'game_date,away_team,home_team,'
                                             'home_score,away_score'},
                      'game_date.desc'):
            if x.get('home_score') is None or x.get('away_score') is None:
                continue
            res[(nz(x['away_team']), nz(x['home_team']),
                 str(x['game_date'])[:10])] = x
        hist_ctx = page(CTX[sport], {'select': '*',
                                     'game_date': f'gte.{FIT_SINCE}'},
                        'game_date.desc')
        season = None
        for c in hist_ctx:
            try:
                season = int(c.get('season'))
                break
            except (TypeError, ValueError):
                continue
        tstats = load_team_stats(sport, season or 2026)

        hist = []
        for c in hist_ctx:
            g = res.get((nz(c.get('away_team')), nz(c.get('home_team')),
                         str(c.get('game_date'))[:10]))
            if not g:
                continue
            cs = f(c.get('close_spread'))
            hist.append({'ctx': c, 'mc': jd(c.get('mc_probabilities')),
                         'actual': f(g['home_score']) - f(g['away_score']),
                         'market': None if cs is None
                         else -(HCAP_SIGN[sport] * cs)})
        mk = [h for h in hist if h['market'] is not None]
        mkt_mae = (sum(abs(h['actual'] - h['market']) for h in mk) / len(mk)
                   if mk else None)

        coeffs = {}
        for name, angle, fn in VOICES.get(sport, []):
            pts = [(fn(h['ctx'], h['mc'], tstats), h['actual']) for h in hist]
            pts = [(x, y) for x, y in pts if x is not None]
            if len(pts) < MIN_FIT_N:
                print(f'  {name:<13} n={len(pts)} — below {MIN_FIT_N}, not published')
                continue
            out = fit([p[0] for p in pts], [p[1] for p in pts])
            if not out:
                print(f'  {name:<13} no variance — not published')
                continue
            b, a0, r, mae, n = out
            weak = abs(r) < MIN_OPINION_CORR
            coeffs[name] = {'slope': round(b, 6), 'hfa': round(a0, 3),
                            'corr': round(r, 3), 'mae': round(mae, 2),
                            'n': n, 'angle': angle, 'fn': fn,
                            'weak': weak,
                            'weak_reason': (
                                f'|corr| {abs(r):.2f} below {MIN_OPINION_CORR} '
                                f'— this fit is effectively a constant '
                                f'({a0:+.1f} on every game), not an opinion'
                            ) if weak else None}
            print(f'  {name:<13} slope {b:+.4f}  hfa {a0:+.2f}  '
                  f'corr {r:+.2f}  mae {mae:.2f}  n={n}'
                  + ('   << WEAK: a constant, not an opinion' if weak else ''))
        if not coeffs:
            print('  no publishable voices')
            continue

        # ── 2. apply to the upcoming slate ─────────────────────────────
        up = page(CTX[sport], {'select': '*',
                               'game_date': f'in.({",".join(dates)})'},
                  'game_date.asc')
        games = {}
        for c in up:
            gid = str(c.get('game_id') or '')
            if not gid:
                continue
            voices = {}
            for name, cf in coeffs.items():
                raw = cf['fn'](c, jd(c.get('mc_probabilities')), tstats)
                if raw is None:
                    continue
                voices[name] = {
                    'margin': round(cf['hfa'] + cf['slope'] * raw, 2),
                    'angle': cf['angle'],
                    # the voice's own record travels with its opinion, so a
                    # weak seat is visible as a weak seat
                    'corr': cf['corr'], 'mae': cf['mae'], 'n': cf['n'],
                    'weak': cf['weak'], 'weak_reason': cf['weak_reason'],
                }
            if not voices:
                continue
            games[gid] = {
                'game_date': str(c.get('game_date'))[:10],
                'away_team': c.get('away_team'),
                'home_team': c.get('home_team'),
                'voices': voices,
            }
        print(f'  upcoming games with voices: {len(games)}')
        for gid, g in list(games.items())[:3]:
            spread = ' · '.join(
                f'{k} {v["margin"]:+.1f}{"*" if v.get("weak") else ""}'
                for k, v in sorted(g['voices'].items()))
            print(f'    {str(g["away_team"])[:16]:<16} @ '
                  f'{str(g["home_team"])[:16]:<16} {spread}')
        _seats = sum(1 for v in coeffs.values() if not v['weak'])
        print(f'  panel: {_seats} real seat(s), '
              f'{len(coeffs) - _seats} weak voice(s) shown but not counted')
        payload['sports'][sport] = {
            'market_mae': None if mkt_mae is None else round(mkt_mae, 2),
            'seats': _seats,
            'games': games,
            'panel': {k: {kk: vv for kk, vv in v.items() if kk != 'fn'}
                      for k, v in coeffs.items()},
        }

    key = f'voices_{base.isoformat()}'
    n_games = sum(len(s['games']) for s in payload['sports'].values())
    print(f'\n{key}: {len(payload["sports"])} sports · {n_games} games')
    if a.dry_run:
        print('(dry run — nothing written)')
        return 0
    r = requests.post(
        f'{SB}/rest/v1/jerry_cache?on_conflict=game_id,sport',
        headers={**H, 'Content-Type': 'application/json',
                 'Prefer': 'resolution=merge-duplicates,return=minimal'},
        json={'cache_key': key, 'game_id': key, 'sport': 'ALL',
              'narrative': f'Model voices · {base.isoformat()} · '
                           f'{n_games} games',
              'data': payload,
              'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat()},
        timeout=60)
    print(f'cache write: HTTP {r.status_code}')
    if r.status_code not in (200, 201, 204):
        print(r.text[:300])
        return 1
    # a 2xx is not proof
    back = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H, timeout=60,
                        params={'select': 'cache_key', 'cache_key': f'eq.{key}'})
    print('verified in jerry_cache:', bool(back.json()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
