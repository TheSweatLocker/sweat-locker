#!/usr/bin/env python3
"""Which team-stat edges actually translate into covering the spread?

WHY (2026-10-05)
----------------
Andy: "if a team has a better margin in penalties with a better defense do
they cover the spread? Other questions like these? Gaps in SOR/SOS spread
coverage rate this last week."

WHAT THIS IS, AND WHAT IT IS NOT — read before quoting a number
---------------------------------------------------------------
`team_computed_stats` is a CURRENT-SEASON SNAPSHOT. It has one row per
(sport, team, stat_key) with a single `refreshed_at` and no history. So a
team's penalty margin today already INCLUDES the games this script is
scoring. That makes every number below a DESCRIPTIVE ASSOCIATION — "teams
that ended the season better at X also covered more often" — and NOT a
backtest. It cannot be used to claim we could have bet it.

That distinction is the whole reason SP+ backtests were thrown out on 09-26
and why team_stats_rolling is flagged current-only. Treating an association
as an edge is how a model gets shipped that loses.

So this is a HYPOTHESIS GENERATOR. Anything that looks strong here earns a
proper point-in-time walk-forward test before it goes near the engine, the
same bar nba_model_validate held sides and totals to.

The one number here that IS honest as a performance report is the SOS/SOR
cover-rate section at the end: that asks how teams we RATED highly actually
did against the number last week, which is a report on our own published
rating, not a prediction.

    python stat_vs_cover.py --sport NFL
    python stat_vs_cover.py --sport NCAAF --min-n 40
"""
from __future__ import annotations
import argparse, collections, datetime as dt, os, statistics, sys
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

RESULTS = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results',
           'NBA': 'nba_game_results', 'NHL': 'nhl_game_results',
           'MLB': 'mlb_game_results'}
BREAKEVEN = 110 / 210

# close_spread is the HOME handicap everywhere EXCEPT NFL, where it is the
# AWAY line (verified n=7,325, project_close_spread_sign_bug_914).
def home_line(sport, cs):
    if cs is None:
        return None
    try:
        v = float(cs)
    except (TypeError, ValueError):
        return None
    return -v if sport == 'NFL' else v


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


def cover(margin, hl):
    """+1 home covered, -1 away covered, 0 push."""
    adj = margin + hl
    if abs(adj) < 1e-9:
        return 0
    return 1 if adj > 0 else -1


def load(sport):
    stats = collections.defaultdict(dict)        # key -> team -> value
    direction = {}
    for r in page('team_computed_stats',
                  {'select': 'team,stat_key,raw_value,direction',
                   'sport': f'eq.{sport}'}):
        try:
            stats[r['stat_key']][r['team']] = float(r['raw_value'])
        except (TypeError, ValueError):
            continue
        if r.get('direction'):
            direction[r['stat_key']] = r['direction']
    games = []
    for g in page(RESULTS[sport],
                  {'select': 'game_date,home_team,away_team,home_score,'
                             'away_score,close_spread',
                   'game_date': 'gte.2026-08-01'}):
        if g.get('home_score') is None or g.get('away_score') is None:
            continue
        hl = home_line(sport, g.get('close_spread'))
        if hl is None:
            continue
        games.append({'date': g['game_date'], 'h': g['home_team'],
                      'a': g['away_team'],
                      'margin': g['home_score'] - g['away_score'],
                      'hl': hl})
    return stats, direction, games


def test_stat(games, values, flip=False):
    """Back the team with the better value. -> (w,l,p) and by-gap buckets."""
    tot = [0, 0, 0]
    buckets = collections.defaultdict(lambda: [0, 0, 0])
    for g in games:
        hv, av = values.get(g['h']), values.get(g['a'])
        if hv is None or av is None:
            continue
        diff = (hv - av) * (-1 if flip else 1)
        if abs(diff) < 1e-12:
            continue
        back_home = diff > 0
        c = cover(g['margin'], g['hl'])
        res = 0 if c == 0 else (1 if (c > 0) == back_home else -1)
        idx = 2 if res == 0 else (0 if res > 0 else 1)
        tot[idx] += 1
        # bucket by how big the stat gap is, in standard deviations
        buckets[_gap_bucket(abs(diff), values)][idx] += 1
    return tot, buckets


def _gap_bucket(d, values):
    vals = list(values.values())
    sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    if sd <= 0:
        return 'n/a'
    z = d / sd
    if z < 0.5:
        return '0.0-0.5sd'
    if z < 1.0:
        return '0.5-1.0sd'
    if z < 2.0:
        return '1.0-2.0sd'
    return '2.0sd+'


def line(label, t, note=''):
    w, l, p = t
    if w + l == 0:
        return None
    hit = w / (w + l)
    return ('  %-34s %4d-%-4d-%-3d %6.1f%% %+7.1f%%  %s'
            % (label[:34], w, l, p, 100 * hit, 100 * roi(w, l), note))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NFL', choices=sorted(RESULTS))
    ap.add_argument('--min-n', type=int, default=30)
    args = ap.parse_args()

    stats, direction, games = load(args.sport)
    print(f'\n{"="*78}')
    print(f'  {args.sport} · stat edge vs ATS cover · {len(games)} games with a '
          f'closing line')
    print(f'  ASSOCIATION ONLY — team_computed_stats is a current-season')
    print(f'  snapshot, so these stats already contain the games being scored.')
    print(f'  Breakeven at -110 is {100*BREAKEVEN:.1f}%. n>={args.min_n} shown.')
    print(f'{"="*78}')

    rows = []
    for key, values in stats.items():
        if len(values) < 8:
            continue
        # "better" depends on the stat: for allowed/penalty stats lower is
        # better. team_computed_stats.direction says which, when present.
        flip = str(direction.get(key, '')).lower() in ('lower', 'asc', 'low')
        if not flip:
            flip = any(w in key.lower() for w in
                       ('allowed', 'penalt', 'turnover', 'ints', 'sacks_suffered',
                        'points_against', 'def_epa'))
        t, buckets = test_stat(games, values, flip=flip)
        if t[0] + t[1] < args.min_n:
            continue
        hit = t[0] / (t[0] + t[1])
        rows.append((abs(hit - BREAKEVEN), key, t, buckets, flip))

    rows.sort(reverse=True)
    print('\n  %-34s %-14s %6s %8s  %s'
          % ('stat (backing the better team)', 'W-L-P', 'hit', 'ROI', 'note'))
    print('  ' + '-' * 74)
    for _d, key, t, _b, flip in rows[:22]:
        hit = t[0] / (t[0] + t[1])
        note = ('ABOVE breakeven' if hit > BREAKEVEN + 0.02 else
                'BELOW breakeven' if hit < BREAKEVEN - 0.02 else 'flat')
        if flip:
            note += ' (lower=better)'
        out = line(key, t, note)
        if out:
            print(out)

    # ── Andy's specific question ──────────────────────────────────────
    print(f'\n  {"-"*74}')
    print('  ANDY\'S QUESTION: better penalty margin AND better defence')
    print(f'  {"-"*74}')
    pen = next((k for k in stats if 'penalty' in k.lower()), None)
    dfn = None
    for cand in ('points_allowed_pg', 'def_epa_per_play', 'yds_allowed_pg',
                 'def_success_rate_allowed'):
        if cand in stats:
            dfn = cand
            break
    if not pen or not dfn:
        print(f'  cannot run — penalty key={pen!r} defence key={dfn!r}')
    else:
        print(f'  penalty stat = {pen} (lower better) · defence = {dfn} (lower better)')
        pv, dv = stats[pen], stats[dfn]
        both = [0, 0, 0]
        pen_only = [0, 0, 0]
        dfn_only = [0, 0, 0]
        neither = [0, 0, 0]
        for g in games:
            ph, pa = pv.get(g['h']), pv.get(g['a'])
            dh, da = dv.get(g['h']), dv.get(g['a'])
            if None in (ph, pa, dh, da):
                continue
            # home is better when its value is LOWER on both
            pen_edge_home = ph < pa
            dfn_edge_home = dh < da
            c = cover(g['margin'], g['hl'])
            for flagp, flagd, bucket in ((True, True, both), (True, False, pen_only),
                                         (False, True, dfn_only), (False, False, neither)):
                pass
            if pen_edge_home == dfn_edge_home:
                back_home = pen_edge_home
                tgt = both
            elif pen_edge_home:
                back_home, tgt = True, pen_only
            else:
                back_home, tgt = True, dfn_only
            res = 0 if c == 0 else (1 if (c > 0) == back_home else -1)
            tgt[2 if res == 0 else (0 if res > 0 else 1)] += 1
        for lab, t in (('BOTH edges same team', both),
                       ('penalty edge only (home)', pen_only),
                       ('defence edge only (home)', dfn_only)):
            out = line(lab, t)
            print(out if out else f'  {lab:<34} no qualifying games')

    # ── SOS / SOR, as a report on our own rating ──────────────────────
    print(f'\n  {"-"*74}')
    print('  OUR RATINGS vs THE NUMBER — last 7 days (performance report,')
    print('  not a prediction: this is how teams we rated higher actually did)')
    print(f'  {"-"*74}')
    cutoff = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    recent = [g for g in games if str(g['date']) >= cutoff]
    print(f'  games in last 7 days with a closing line: {len(recent)}')
    for key in ('sor', 'sos', 'sor_margin', 'sos_margin', 'sweat_rating'):
        if key not in stats:
            print(f'  {key:<34} not published for {args.sport}')
            continue
        t, _b = test_stat(recent, stats[key], flip=False)
        out = line(f'{key} (back higher-rated)', t)
        print(out if out else f'  {key:<34} n too small ({t})')
        tall, _ = test_stat(games, stats[key], flip=False)
        out2 = line(f'   ^ same, full season', tall)
        if out2:
            print(out2)
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
