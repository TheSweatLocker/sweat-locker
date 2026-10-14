#!/usr/bin/env python3
"""Reconstruct point-in-time team features for every historical game.

Steps 1-2 of the feature-history project (see
supabase/migrations/20261013a_feature_history.sql for why it exists).

THE ONE INVARIANT
-----------------
Every feature on a game's row is computed from games that finished STRICTLY
BEFORE that game, in the same season. Nothing else. If this invariant breaks
anywhere, the table is a leak generator and every model trained on it will
backtest beautifully and lose money -- which is exactly what
project_models_dont_beat_the_close_1005 measured when 65-79% ATS turned out to
be entirely leak.

Two mechanical guarantees:
  * Games are grouped by DATE and the rating is fit on earlier dates only, so
    games sharing a date cannot see each other. A Sunday NFL slate sees
    Thursday and before.
  * Seasons never mix. Football ratings in this repo do not carry over
    (CARRYOVER_FULL has no NFL/NCAAF entry), so week 1 rows are honestly
    UNFED rather than quietly seeded from last year.

WHAT IS REBUILT, AND WHAT IS NOT
--------------------------------
Rebuildable from (date, teams, scores, closing line) -- which the results
tables carry in full:

  ratings     srs, sos, srs_diff, sos_diff          [reuses fit_srs/sos_from]
  record      games_played, win_pct, pt_diff_pg, pf_pg, pa_pg, streak
  form        last3_margin, last5_margin
  market      ats_covers/losses/pct, ou_overs/unders,
              resid_l5  (mean of margin-beaten-the-number over last 5)
  schedule    rest_days, h2h_last3_margin

NOT rebuildable, deliberately excluded rather than imputed: SP+, talent,
returning production, Madden, EPA, success rate, explosiveness, OL/DL weight.
Those come from third-party snapshots that have no as-of-date history here.

SIGN CONVENTIONS ARE NOT RE-DERIVED HERE
----------------------------------------
`compute_margin_strength.load_games` already resolves the one that has bitten
this repo -- close_spread is the HOME handicap in every sport EXCEPT NFL where
it is the AWAY line (project_close_spread_sign_bug_914, verified n=7,325). It
returns `home_line` already corrected, and this module uses only that. A
second implementation of that rule is how it gets inverted again.

Given a home handicap `home_line`, home covers iff `margin + home_line > 0`,
so the home residual -- how much home beat the number by -- is
`margin + home_line`, and a team's own residual is that value negated when it
was the away side.

USAGE
    python build_feature_history.py --sport NFL                  # summary only
    python build_feature_history.py --sport NFL --out nfl.jsonl
    python build_feature_history.py --sport NFL --apply
    python build_feature_history.py --sport NCAAF --since-season 2023 --apply

NEXT (step 3, the gate): validate these reconstructions against the live
`*_game_context` columns on the ~6-week window where both exist. If
reconstructed SOR on 2026-10-04 does not match what was stored that day, the
rebuild is wrong and nothing downstream is trustworthy. Do that BEFORE
training anything.
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
from datetime import date, datetime, timezone

import requests

from compute_margin_strength import (
    SPORT_CFG, H, SB, clean_games, fit_srs, load_games, sos_from,
)

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

H_WRITE = {**H, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

#: Prior games a team needs before its RATING means anything. Below this the
#: row is marked unfed rather than carrying a shrunk-toward-zero number that
#: looks like a measurement. Four is roughly where SRS stops being noise in a
#: 17-game football season.
MIN_GAMES_SRS = 4

#: Prior games WITH a closing line before the ATS/OU/residual block is real.
MIN_GAMES_MARKET = 4

FORM_WINDOWS = (3, 5)
RESID_WINDOW = 5
H2H_WINDOW = 3


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 4) if xs else None


def team_history(games: list[dict]) -> dict:
    """{team: [game dicts, chronological]} from that team's own perspective."""
    per = collections.defaultdict(list)
    for g in games:
        hl = g.get('home_line')
        resid_home = (g['margin'] + hl) if hl is not None else None
        for team, opp, margin, resid, at_home in (
                (g['home'], g['away'], g['margin'], resid_home, True),
                (g['away'], g['home'], -g['margin'],
                 (-resid_home if resid_home is not None else None), False)):
            per[team].append({
                'date': g['date'], 'opp': opp, 'margin': margin,
                'resid': resid, 'home': at_home,
                'total': g.get('total'), 'close_total': g.get('close_total'),
            })
    for t in per:
        per[t].sort(key=lambda x: str(x['date']))
    return per


def team_features(hist: list[dict], as_of: str, rating: dict,
                  sos: dict, team: str) -> dict:
    """Features for one team from its games strictly before `as_of`."""
    past = [g for g in hist if str(g['date']) < as_of]
    n = len(past)
    f: dict = {'games_used': n}
    if n == 0:
        return f

    wins = sum(1 for g in past if g['margin'] > 0)
    losses = sum(1 for g in past if g['margin'] < 0)
    f['win_pct'] = round(wins / n, 4)
    f['wins'] = wins
    f['losses'] = losses
    f['pt_diff_pg'] = round(sum(g['margin'] for g in past) / n, 3)

    # Signed streak off the most recent games.
    streak = 0
    for g in reversed(past):
        s = 1 if g['margin'] > 0 else (-1 if g['margin'] < 0 else 0)
        if s == 0:
            break
        if streak == 0 or (streak > 0) == (s > 0):
            streak += s
        else:
            break
    f['streak'] = streak

    for w in FORM_WINDOWS:
        f[f'last{w}_margin'] = _mean([g['margin'] for g in past[-w:]])

    # Rest: days since this team last played. Derived, not read, so it exists
    # on every sport and season rather than only where a feed populated it.
    try:
        d_asof = date.fromisoformat(as_of)
        d_prev = date.fromisoformat(str(past[-1]['date'])[:10])
        f['rest_days'] = (d_asof - d_prev).days
    except (ValueError, TypeError):
        f['rest_days'] = None

    # Market block. Only games that actually had a closing line count toward
    # market_games, so a team in a season with 40% line coverage is not
    # credited with a full ATS record.
    priced = [g for g in past if g['resid'] is not None]
    f['market_games'] = len(priced)
    if priced:
        f['ats_covers'] = sum(1 for g in priced if g['resid'] > 0)
        f['ats_losses'] = sum(1 for g in priced if g['resid'] < 0)
        denom = f['ats_covers'] + f['ats_losses']
        f['ats_pct'] = round(f['ats_covers'] / denom, 4) if denom else None
        f['resid_mean'] = _mean([g['resid'] for g in priced])
        f[f'resid_l{RESID_WINDOW}'] = _mean(
            [g['resid'] for g in priced[-RESID_WINDOW:]])

    f['srs'] = round(rating[team], 4) if team in rating else None
    f['sos'] = round(sos[team], 4) if team in sos else None
    f['home_games'] = sum(1 for g in past if g['home'])
    return f


def build(sport: str, since_season=None, quiet: bool = False,
          until: str | None = None) -> list[dict]:
    """`until` truncates the INPUT games to that date — used by --leak-test to
    prove the output does not depend on games that had not happened yet."""
    cfg = SPORT_CFG[sport]
    games = clean_games(sport, load_games(sport), quiet=True)
    games = [g for g in games if g.get('date')]
    if until:
        games = [g for g in games if str(g['date'])[:10] <= until]
    if not games:
        print(f'{sport}: no games loaded')
        return []

    by_season = collections.defaultdict(list)
    for g in games:
        by_season[str(g.get('season') or str(g['date'])[:4])].append(g)

    seasons = sorted(by_season)
    if since_season is not None:
        seasons = [s for s in seasons if s >= str(since_season)]
    if not quiet:
        print(f'=== {sport}: {len(games)} played games, '
              f'{len(by_season)} seasons; building {len(seasons)}')

    rows: list[dict] = []
    for season in seasons:
        sg = sorted(by_season[season], key=lambda g: str(g['date']))
        hist = team_history(sg)
        dates = sorted({str(g['date'])[:10] for g in sg})
        # One SRS fit per distinct date, not per game: every game on a date
        # shares the same prior history, and refitting per game would be
        # ~7.3k fits for NFL to produce identical numbers.
        fed_rows = 0
        for d in dates:
            prior = [g for g in sg if str(g['date'])[:10] < d]
            rating = fit_srs(prior, cfg) if prior else {}
            sos = sos_from(prior, rating) if prior else {}
            for g in (x for x in sg if str(x['date'])[:10] == d):
                hf = team_features(hist[g['home']], d, rating, sos, g['home'])
                af = team_features(hist[g['away']], d, rating, sos, g['away'])

                h2h = [x for x in hist[g['home']]
                       if x['opp'] == g['away'] and str(x['date'])[:10] < d]
                srs_fed = (hf['games_used'] >= MIN_GAMES_SRS
                           and af['games_used'] >= MIN_GAMES_SRS)
                mkt_fed = (hf.get('market_games', 0) >= MIN_GAMES_MARKET
                           and af.get('market_games', 0) >= MIN_GAMES_MARKET)
                fed_rows += 1 if srs_fed else 0

                feats = {f'home_{k}': v for k, v in hf.items()}
                feats.update({f'away_{k}': v for k, v in af.items()})
                feats['h2h_last3_margin'] = _mean(
                    [x['margin'] for x in h2h[-H2H_WINDOW:]])
                feats['h2h_games'] = len(h2h)
                feats['neutral_site'] = bool(g.get('neutral'))
                for a, b, name in (('srs', 'srs', 'srs_diff'),
                                   ('sos', 'sos', 'sos_diff'),
                                   ('pt_diff_pg', 'pt_diff_pg', 'pt_diff_diff'),
                                   ('win_pct', 'win_pct', 'win_pct_diff')):
                    hv, av = hf.get(a), af.get(b)
                    feats[name] = (round(hv - av, 4)
                                   if hv is not None and av is not None else None)

                rows.append({
                    'sport': sport,
                    'game_id': str(g.get('game_id') or ''),
                    'game_date': d,
                    'season': season,
                    'home_team': g['home'],
                    'away_team': g['away'],
                    'home_srs': hf.get('srs'),
                    'away_srs': af.get('srs'),
                    'srs_diff': feats['srs_diff'],
                    'home_sos': hf.get('sos'),
                    'away_sos': af.get('sos'),
                    'sos_diff': feats['sos_diff'],
                    'home_resid_l5': hf.get(f'resid_l{RESID_WINDOW}'),
                    'away_resid_l5': af.get(f'resid_l{RESID_WINDOW}'),
                    'home_games_used': hf['games_used'],
                    'away_games_used': af['games_used'],
                    'srs_fed': srs_fed,
                    'market_fed': mkt_fed,
                    'features': feats,
                })
        if not quiet:
            print(f'  {season}: {len(sg):>5} games, {len(dates):>3} dates, '
                  f'{fed_rows:>5} srs-fed')
    return rows


def summarise(rows: list[dict]) -> None:
    n = len(rows)
    if not n:
        return
    fed = sum(1 for r in rows if r['srs_fed'])
    mkt = sum(1 for r in rows if r['market_fed'])
    print(f'\n=== {n} rows built')
    print(f'  srs_fed    {fed:>6} ({100*fed/n:.1f}%)')
    print(f'  market_fed {mkt:>6} ({100*mkt/n:.1f}%)')
    missing_id = sum(1 for r in rows if not r['game_id'])
    if missing_id:
        print(f'  ⚠ {missing_id} rows have NO game_id — these cannot be '
              f'written (unique key is (sport, game_id))')
    sd = [r['srs_diff'] for r in rows if r['srs_fed'] and r['srs_diff'] is not None]
    if sd:
        print(f'  srs_diff on fed rows: mean {statistics.mean(sd):+.2f} '
              f'sd {statistics.pstdev(sd):.2f} '
              f'min {min(sd):+.1f} max {max(sd):+.1f}')
    rl = [r['home_resid_l5'] for r in rows
          if r['market_fed'] and r['home_resid_l5'] is not None]
    if rl:
        print(f'  home_resid_l5 on market-fed rows: mean '
              f'{statistics.mean(rl):+.3f} sd {statistics.pstdev(rl):.2f}')
    byseason = collections.Counter(r['season'] for r in rows)
    print(f'  seasons: {len(byseason)}  '
          f'({min(byseason)}..{max(byseason)})')


def leak_test(sport: str, since_season, cut: str) -> int:
    """THE INVARIANT TEST: a point-in-time row must not change when games
    AFTER it are added.

    Build twice — once on games truncated at `cut`, once on everything — and
    compare every row dated on or before `cut`. Any difference means a feature
    read the future, and a model trained on that will backtest beautifully and
    lose money (project_models_dont_beat_the_close_1005: 65-79% ATS, all leak).

    This is a stronger test than eyeballing `games_used`, because it also
    catches leakage inside the SRS fit, where a rating is solved jointly across
    all games handed to it.
    """
    print(f'=== LEAK TEST · {sport} · cut {cut}')
    full = {(r['game_id'], r['game_date']): r
            for r in build(sport, since_season, quiet=True)}
    trunc = build(sport, since_season, quiet=True, until=cut)
    checked = diffs = 0
    shown = 0
    for r in trunc:
        if str(r['game_date']) > cut:
            continue
        ref = full.get((r['game_id'], r['game_date']))
        if ref is None:
            print(f"  ⚠ row missing from full build: {r['game_id']}")
            diffs += 1
            continue
        checked += 1
        for k in ('home_srs', 'away_srs', 'srs_diff', 'home_sos', 'away_sos',
                  'home_resid_l5', 'away_resid_l5', 'home_games_used',
                  'away_games_used', 'srs_fed', 'market_fed'):
            if r[k] != ref[k]:
                diffs += 1
                if shown < 10:
                    shown += 1
                    print(f"  ⚠ LEAK {r['game_date']} {r['away_team']} @ "
                          f"{r['home_team']}  {k}: truncated={r[k]} "
                          f"full={ref[k]}")
                break
        else:
            if r['features'] != ref['features']:
                diffs += 1
                if shown < 10:
                    shown += 1
                    ks = [k for k in ref['features']
                          if r['features'].get(k) != ref['features'].get(k)]
                    print(f"  ⚠ LEAK {r['game_date']} {r['away_team']} @ "
                          f"{r['home_team']}  features differ: {ks[:6]}")
    print(f'  compared {checked} rows dated <= {cut}')
    if diffs:
        print(f'  ✗ {diffs} ROWS DEPEND ON FUTURE GAMES — do not train on this')
        return 1
    print('  ✓ every row identical — no future information reached any feature')
    return 0


def write_db(rows: list[dict], chunk: int = 500) -> int:
    rows = [r for r in rows if r['game_id']]
    ok = 0
    for i in range(0, len(rows), chunk):
        batch = rows[i:i + chunk]
        resp = requests.post(
            f'{SB}/rest/v1/feature_history?on_conflict=sport,game_id',
            headers=H_WRITE, json=batch, timeout=120)
        if resp.status_code in (200, 201, 204):
            ok += len(batch)
        else:
            print(f'  ⚠ write {resp.status_code}: {resp.text[:300]}')
            if resp.status_code == 404 or 'PGRST205' in resp.text:
                print('    feature_history does not exist yet — apply '
                      'supabase/migrations/20261013a_feature_history.sql')
                return ok
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, choices=sorted(SPORT_CFG))
    ap.add_argument('--since-season')
    ap.add_argument('--out', help='write JSONL locally (no DB needed)')
    ap.add_argument('--apply', action='store_true', help='write to feature_history')
    ap.add_argument('--leak-test', metavar='YYYY-MM-DD',
                    help='prove rows on or before this date are unchanged when '
                         'later games are added. Run this before --apply.')
    a = ap.parse_args()

    if a.leak_test:
        return leak_test(a.sport, a.since_season, a.leak_test)

    rows = build(a.sport, a.since_season)
    summarise(rows)

    if a.out:
        with open(a.out, 'w', encoding='utf-8') as fh:
            for r in rows:
                fh.write(json.dumps(r, default=str) + '\n')
        print(f'\n  wrote {len(rows)} rows -> {a.out}')
    if a.apply:
        n = write_db(rows)
        print(f'\n  upserted {n}/{len(rows)} rows into feature_history')
    elif not a.out:
        print('\n  (no --out and no --apply: nothing written)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
