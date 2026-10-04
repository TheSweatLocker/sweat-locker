#!/usr/bin/env python3
"""Opponent-adjusted team EPA for NFL, fitted and validated out of sample.

WHY. Our NFL spread projection is a 96%-correlated copy of the closing line
that adds nothing given the line (joint-regression coefficient -0.067 on the
2026 sample). It has no independent features. Raw team EPA is not independent
either — it is mostly a statement about who you played. Adjusting for opponent
is the cheapest way to turn a descriptive stat into a predictive one.

THE MODEL. For every team-week, total offensive EPA satisfies roughly

    off_epa[team, game] ≈ off[team] + def[opponent] + home_field

so off[] and def[] are recovered by alternating least squares with ridge
shrinkage toward zero. Shrinkage matters more than the fit: at week 3 a team
has three games, and an unshrunk rating is mostly noise about its schedule.

WHAT IT IS NOT. This is a better STAT, not a bet. It is validated here against
future scoring margin, not against the closing line, because beating the line
is a different and much higher bar — see the note at the bottom of the run.

    python nfl_opponent_adjusted_epa.py --validate
    python nfl_opponent_adjusted_epa.py --season 2026 --write
"""
from __future__ import annotations
import argparse, collections, json, os, sys
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
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}

RIDGE = 4.0      # games-equivalent of shrinkage toward league mean
ITERS = 25


def _pull(table: str, params: dict) -> list[dict]:
    out, off = [], 0
    while True:
        q = dict(params)
        q.update({'limit': 1000, 'offset': off})
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=q, timeout=180)
        r.raise_for_status()
        chunk = r.json()
        out += chunk
        if len(chunk) < 1000:
            return out
        off += 1000


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def team_week_epa(rows) -> dict:
    """-> {(season, week, team): {'off': x, 'opp': opponent}}"""
    agg = {}
    for r in rows:
        if str(r.get('season_type') or 'REG') != 'REG':
            continue
        t, o = r.get('team'), r.get('opponent_team')
        s, w = r.get('season'), r.get('week')
        if not t or not o or s is None or w is None:
            continue
        key = (int(s), int(w), t)
        cell = agg.setdefault(key, {'off': 0.0, 'opp': o, 'n': 0})
        for fld in ('passing_epa', 'rushing_epa'):
            v = _f(r.get(fld))
            if v is not None:
                cell['off'] += v
                cell['n'] += 1
    return {k: v for k, v in agg.items() if v['n'] > 0}


def fit_ratings(games, ridge: float = RIDGE, iters: int = ITERS):
    """games: list of (team, opponent, off_epa). -> (off, def_) dicts."""
    if not games:
        return {}, {}
    mu = sum(g[2] for g in games) / len(games)
    off = collections.defaultdict(float)
    dfn = collections.defaultdict(float)
    by_team = collections.defaultdict(list)
    by_opp = collections.defaultdict(list)
    for t, o, v in games:
        by_team[t].append((o, v))
        by_opp[o].append((t, v))
    for _ in range(iters):
        for t, lst in by_team.items():
            resid = sum(v - mu - dfn[o] for o, v in lst)
            off[t] = resid / (len(lst) + ridge)
        for d, lst in by_opp.items():
            resid = sum(v - mu - off[t] for t, v in lst)
            dfn[d] = resid / (len(lst) + ridge)
    return dict(off), dict(dfn), mu


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--validate', action='store_true')
    ap.add_argument('--season', type=int, default=2026)
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--ridge', type=float, default=RIDGE)
    args = ap.parse_args()

    print('loading nfl_player_stats …')
    rows = _pull('nfl_player_stats', {
        'select': 'season,week,team,opponent_team,season_type,'
                  'passing_epa,rushing_epa'})
    tw = team_week_epa(rows)
    print(f'  {len(rows)} player-weeks -> {len(tw)} team-weeks')

    if args.validate:
        res = _pull('nfl_game_results', {
            'select': 'season,week,home_team,away_team,home_score,away_score'})
        games = [g for g in res if g.get('home_score') is not None]
        print(f'  {len(games)} graded games for validation\n')

        # Walk forward: rate on everything STRICTLY BEFORE this week, then
        # predict this week's margin. Anything else leaks.
        def corr(xs, ys):
            n = len(xs)
            if n < 3:
                return 0.0
            mx, my = sum(xs) / n, sum(ys) / n
            sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
            sxx = sum((a - mx) ** 2 for a in xs)
            syy = sum((b - my) ** 2 for b in ys)
            return sxy / ((sxx * syy) ** 0.5) if sxx and syy else 0.0

        by_season = collections.defaultdict(list)
        for k, v in tw.items():
            by_season[k[0]].append((k[1], k[2], v))

        raw_p, adj_p, act = [], [], []
        for season, entries in sorted(by_season.items()):
            weeks = sorted({w for w, _, _ in entries})
            for wk in weeks:
                if wk < 4:
                    continue          # need a few games to rate on
                hist = [(t, c['opp'], c['off']) for w, t, c in entries if w < wk]
                if len(hist) < 40:
                    continue
                off, dfn, mu = fit_ratings(hist, ridge=args.ridge)
                raw = collections.defaultdict(list)
                for w, t, c in entries:
                    if w < wk:
                        raw[t].append(c['off'])
                raw_mean = {t: sum(v) / len(v) for t, v in raw.items()}
                for g in games:
                    if g.get('season') != season or g.get('week') != wk:
                        continue
                    h, a = g['home_team'], g['away_team']
                    if h not in off or a not in off:
                        continue
                    margin = float(g['home_score']) - float(g['away_score'])
                    # fit_ratings solves v ~ mu + off[t] + dfn[opp], so the
                    # expected offence for h is off[h] + dfn[a]. ADD the
                    # defensive term; subtracting it inverts every defence and
                    # drove OOS corr DOWN to +0.139 vs +0.302 raw.
                    adj_p.append((off.get(h, 0) + dfn.get(a, 0))
                                 - (off.get(a, 0) + dfn.get(h, 0)))
                    raw_p.append(raw_mean.get(h, 0) - raw_mean.get(a, 0))
                    act.append(margin)
        print('=== out-of-sample, walk-forward (rate on prior weeks only) ===')
        print(f'  n = {len(act)} games')
        print(f'  raw team EPA differential      corr = {corr(raw_p, act):+.3f}')
        print(f'  opponent-adjusted differential corr = {corr(adj_p, act):+.3f}')
        print('\n  (correlation with ACTUAL MARGIN. Beating the closing line is '
              'a separate,\n   much higher bar — this is a better stat, not yet '
              'a bet.)')
        return 0

    # ── fit the requested season and optionally publish ────────────────────
    entries = [(k[1], k[2], v) for k, v in tw.items() if k[0] == args.season]
    hist = [(t, c['opp'], c['off']) for _, t, c in entries]
    off, dfn, mu = fit_ratings(hist, ridge=args.ridge)
    print(f'\n=== {args.season} opponent-adjusted EPA (ridge={args.ridge}) ===')
    print('%-6s %10s %10s %10s' % ('team', 'off_adj', 'def_adj', 'net'))
    for t in sorted(off, key=lambda x: -(off[x] - dfn.get(x, 0))):
        print('%-6s %10.2f %10.2f %10.2f'
              % (t, off[t], dfn.get(t, 0.0), off[t] - dfn.get(t, 0.0)))

    if args.write:
        payload = []
        rank_net = sorted(off, key=lambda x: -(off[x] - dfn.get(x, 0)))
        for i, t in enumerate(rank_net, 1):
            payload.append({'sport': 'NFL', 'team': t, 'season': args.season,
                            'stat_key': 'epa_adj_net', 'raw_value': round(off[t] - dfn.get(t, 0.0), 3),
                            'rank': i, 'league_size': len(rank_net),
                            'direction': 'higher', 'display_label': 'Adjusted EPA (net)',
                            'unit': ''})
        r = requests.post(f'{SB}/rest/v1/team_computed_stats'
                          '?on_conflict=sport,team,season,stat_key',
                          headers=H_W, json=payload, timeout=60)
        print(f'\nwrite -> {r.status_code} {r.text[:160]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
