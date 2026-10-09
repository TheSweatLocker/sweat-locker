#!/usr/bin/env python3
"""Margin-based strength of schedule and strength of record, every sport.

WHY THIS EXISTS ALONGSIDE compute_schedule_strength.py
------------------------------------------------------
That script computes SOS and SOR from WIN PERCENTAGE only, shrunk toward .500
with head-to-head removed. It is honest about what it is, but it throws away
the most informative thing on the scoreboard: a one-point win and a thirty-
point win count identically. At three or four games played — which is where
NFL sits in week 4, and where a rating actually gets used — opponent win% is
close to pure noise.

Andy 2026-10-04: "SOR/SOS is very important ... I want improvement now."

THE MODEL is the Simple Rating System, solved the same way as
nfl_opponent_adjusted_epa:

    rating[t] = average( adjusted_margin[t, g] + rating[opponent] )

iterated to convergence with ridge shrinkage. A team's rating is its scoring
margin corrected for who it played, so it answers the question SOR is actually
asking — would an average team have done this against this schedule?

    SOS = average rating of the opponents faced   (in POINTS, not win share)
    SOR = the team's own rating

THREE CORRECTIONS THAT MATTER MORE THAN THE SOLVER
--------------------------------------------------
* HOME FIELD. A road win is worth more than a home win. Margin is adjusted by
  a per-sport HFA before anything else, so beating a team at their place
  outrates beating them at yours.
* BLOWOUT DAMPING. A 45-point win is not three times more informative than a
  15-point win — it mostly measures garbage time and when the starters sat.
  Margins are capped per sport. Without this, one blowout can carry a rating
  for a month.
* SHRINKAGE SCALED TO SCHEDULE LENGTH. K=4 games is right for a 17-game NFL
  season and badly wrong for a 162-game MLB one. Each sport gets its own.

VALIDATED, not assumed — see --validate. It walks forward, rating only on
games strictly before the week being predicted, and compares against both raw
margin and the existing win%-based measure.

    python compute_margin_strength.py --validate --sport NFL
    python compute_margin_strength.py --sport NFL --season 2026 --write
"""
from __future__ import annotations
import argparse, collections, os, sys
from datetime import date, datetime, timezone
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

RESULTS = {
    'MLB': 'mlb_game_results', 'NFL': 'nfl_game_results',
    'NCAAF': 'ncaaf_game_results', 'NHL': 'nhl_game_results',
    'NBA': 'nba_game_results', 'NCAAB': 'ncaab_game_results',
}

# (home-field points, margin cap, ridge in games-equivalent).
# HFA and cap are scale decisions about each sport's scoreboard, not fits:
# an NHL game is decided by two goals, an NCAAF game sometimes by forty.
SPORT_CFG = {
    'NFL':   {'hfa': 2.0, 'cap': 24.0, 'ridge': 4.0},
    'NCAAF': {'hfa': 2.5, 'cap': 28.0, 'ridge': 6.0},
    'NBA':   {'hfa': 2.5, 'cap': 20.0, 'ridge': 8.0},
    'NCAAB': {'hfa': 3.0, 'cap': 20.0, 'ridge': 8.0},
    'NHL':   {'hfa': 0.2, 'cap': 3.0,  'ridge': 10.0},
    'MLB':   {'hfa': 0.2, 'cap': 6.0,  'ridge': 20.0},
}
ITERS = 30


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


def publishable_teams(sport: str, season: int):
    """Teams a rating may be PUBLISHED for. None means 'all of them'.

    NCAAF only. The margin fit should keep every game — an FBS team's win over
    an FCS opponent is a real result and the adjustment needs it — but ranking
    261 programmes together puts Mercyhurst in a table with Alabama and makes
    the rank meaningless. SP+ coverage is the division line already used by
    reconcile_ncaaf_stat_sources (139 rated teams), so reuse it rather than
    invent a second definition of FBS.
    """
    if sport != 'NCAAF':
        return None
    try:
        rows = _pull('ncaaf_team_stats',
                     {'select': 'team,sp_overall', 'season': f'eq.{season}'})
        fbs = {r['team'] for r in rows if r.get('sp_overall') is not None}
        return fbs or None
    except Exception:
        return None          # never block a publish on the gate failing


# Sports whose results table stores `season` as a hyphenated label
# ("2026-27") rather than an integer year. Filtering those with eq.2026
# matches ZERO rows and the run exits "no NBA games for 2026" — which is how
# NBA reached opening night with no ratings at all.
HYPHEN_SEASON = {'NBA', 'NCAAB'}

# Games-per-team at which the CURRENT season is trusted on its own. Below it,
# the prior season is blended in with a linearly decaying weight. Scoped to
# the two sports that need it: NBA opened 10-03 with ONE scored game, and a
# league rated on one game is noise. Deliberately NOT applied to NFL/NCAAF/
# MLB/NHL — their published ratings are pinned pending Andy's 10-05 review and
# must not move tonight.
CARRYOVER_FULL = {'NBA': 25, 'NCAAB': 15}


def season_label(sport: str, year: int):
    """The value this sport's `season` column actually holds for `year`."""
    if sport in HYPHEN_SEASON:
        return f'{year}-{str(year + 1)[2:]}'
    return year


def prior_label(sport: str, year: int):
    return season_label(sport, year - 1)


def _season_start(sport: str, season: int) -> str:
    """Season opener from sport_registry, so PRESEASON never rates a team.

    NHL's results table has no `season` column, and a naive Aug-1 window swept
    in the 09-19..09-26 preseason — games already excluded from every published
    record by _is_preseason. Rating on them would contradict the records the
    same teams are judged by. Falls back to Aug 1 only if the registry has no
    row, which is worse but never silently wrong about which sport it is.
    """
    try:
        r = requests.get(f'{SB}/rest/v1/sport_registry', headers=H, timeout=30,
                         params={'select': 'season_start', 'sport': f'eq.{sport}'})
        if r.status_code == 200 and r.json():
            ss = r.json()[0].get('season_start')
            if ss and str(ss)[:4] == str(season):
                return str(ss)[:10]
    except Exception:
        pass
    return f'{season}-08-01'


def load_games(sport: str, season=None) -> list[dict]:
    tbl = RESULTS[sport]
    params = {'select': '*'}
    if season is not None:
        # nhl_game_results has no `season` column — filtering on it 400s and
        # would take the whole run down. Fall back to a date window, which is
        # what `season` encodes anyway.
        try:
            probe = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, timeout=60,
                                 params={'select': 'season', 'limit': 1})
            has_season = probe.status_code == 200
        except Exception:
            has_season = False
        if has_season:
            _lbl = season if isinstance(season, str) else season_label(sport, season)
            params['season'] = f'eq.{_lbl}'
        else:
            params['game_date'] = f'gte.{_season_start(sport, season)}'
            params['and'] = f'(game_date.lte.{season + 1}-07-31)'
    rows = _pull(tbl, params)
    out = []
    for g in rows:
        hs, as_ = _f(g.get('home_score')), _f(g.get('away_score'))
        h, a = g.get('home_team'), g.get('away_team')
        if hs is None or as_ is None or not h or not a:
            continue
        # close_spread is the HOME handicap in every sport EXCEPT NFL, where it
        # is the AWAY line — verified on n=7,325 (project_close_spread_sign_bug_914).
        # Getting this backwards silently inverts every cover, so it is resolved
        # here once rather than at each call site.
        cs = _f(g.get('close_spread'))
        home_line = None
        if cs is not None:
            home_line = -cs if sport == 'NFL' else cs
        out.append({'home': h, 'away': a, 'margin': hs - as_,
                    'date': g.get('game_date'), 'season': g.get('season'),
                    'neutral': bool(g.get('neutral_site')),
                    'home_line': home_line})
    return out


def fit_srs(games, cfg, iters: int = ITERS) -> dict:
    """-> {team: rating}, in points, opponent-adjusted."""
    if not games:
        return {}
    hfa, cap, ridge = cfg['hfa'], cfg['cap'], cfg['ridge']
    obs = collections.defaultdict(list)
    for g in games:
        m = g['margin'] - (0.0 if g.get('neutral') else hfa)
        m = max(-cap, min(cap, m))
        # 2026-10-04: observations carry a weight so a PRIOR season can anchor
        # an early-season rating without dominating it once real games exist.
        # Absent/!=1 weights leave every previously-published rating identical.
        w = float(g.get('weight', 1.0) or 1.0)
        obs[g['home']].append((g['away'], m, w))
        obs[g['away']].append((g['home'], -m, w))
    rating = collections.defaultdict(float)
    for _ in range(iters):
        nxt = {}
        for t, lst in obs.items():
            num = sum(w * (m + rating[o]) for o, m, w in lst)
            den = sum(w for _, _, w in lst) + ridge
            nxt[t] = num / den
        # Re-centre so ratings are relative to an average team, which is what
        # makes "average opponent rating" readable as schedule strength.
        mu = sum(nxt.values()) / len(nxt) if nxt else 0.0
        rating = collections.defaultdict(float, {t: v - mu for t, v in nxt.items()})
    return dict(rating)


def sos_from(games, rating) -> dict:
    """Average rating of the opponents each team actually played."""
    opp = collections.defaultdict(list)
    for g in games:
        opp[g['home']].append(rating.get(g['away'], 0.0))
        opp[g['away']].append(rating.get(g['home'], 0.0))
    return {t: sum(v) / len(v) for t, v in opp.items() if v}


def _corr(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    return sxy / ((sxx * syy) ** 0.5) if sxx and syy else 0.0


def validate(sport: str, cfg) -> int:
    games = clean_games(sport, load_games(sport))
    games = [g for g in games if g.get('date')]
    games.sort(key=lambda g: g['date'])
    print(f'{sport}: {len(games)} graded games\n')

    by_season = collections.defaultdict(list)
    for g in games:
        by_season[g.get('season') or str(g['date'])[:4]].append(g)

    srs_p, winp_p, rawm_p, act = [], [], [], []
    for season, gs in sorted(by_season.items(), key=lambda kv: str(kv[0])):
        gs.sort(key=lambda g: g['date'])
        n = len(gs)
        if n < 60:
            continue
        # Walk forward in thirds: rate on the past, predict the next slice.
        for cut in (int(n * 0.4), int(n * 0.6), int(n * 0.8)):
            hist, fut = gs[:cut], gs[cut:int(cut + n * 0.2)]
            if len(hist) < 40 or not fut:
                continue
            rating = fit_srs(hist, cfg)
            # win%-based control, the measure we ship today
            w = collections.defaultdict(lambda: [0, 0])
            tot = collections.defaultdict(list)
            for g in hist:
                hw = g['margin'] > 0
                w[g['home']][0 if hw else 1] += 1
                w[g['away']][1 if hw else 0] += 1
                tot[g['home']].append(g['margin'])
                tot[g['away']].append(-g['margin'])
            winpct = {t: v[0] / max(1, v[0] + v[1]) for t, v in w.items()}
            rawm = {t: sum(v) / len(v) for t, v in tot.items()}
            for g in fut:
                h, a = g['home'], g['away']
                if h not in rating or a not in rating:
                    continue
                srs_p.append(rating[h] - rating[a])
                winp_p.append(winpct.get(h, .5) - winpct.get(a, .5))
                rawm_p.append(rawm.get(h, 0) - rawm.get(a, 0))
                act.append(g['margin'])
    print('=== out-of-sample, walk-forward (rate on earlier games only) ===')
    print(f'  n = {len(act)} games')
    print(f'  win% differential  (what we ship today) corr = {_corr(winp_p, act):+.3f}')
    print(f'  raw margin differential                 corr = {_corr(rawm_p, act):+.3f}')
    print(f'  SRS opponent-adjusted margin            corr = {_corr(srs_p, act):+.3f}')
    return 0


# ── NBA/NCAAB data hygiene ────────────────────────────────────────────
# Three defects found 2026-10-04, all of which were silently feeding the
# rating before this gate existed:
#
#  1. ALL-STAR GAMES. "Team Stars", "Team Stripes", "World", "Team Chuck",
#     "Team Shaq", "Team Kenny", "Team Candace" — 11 exhibition games across
#     two Februaries, and two of those fake teams were ranking inside the
#     published top 20.
#  2. INTERNATIONAL PRESEASON EXHIBITIONS. Melbourne United, Melbourne Pnx,
#     Hapoel Jerusalem, Guangzhou Loong-Lions. LAC beat Guangzhou 142-95;
#     a +47 margin against a non-NBA club inflates a real franchise.
#  3. PRESEASON between two real NBA teams. 65 of them in 2025-26 (Oct 2-20).
#     `_season_start` exists precisely to stop this, but NBA filters on the
#     `season` COLUMN — which includes preseason — so the gate never applied.
#
# Plus an alias split: one row says "Los Angeles Clippers" where every other
# row says "LA Clippers". Left alone it would fork a franchise in two.
NBA_ALIASES = {'Los Angeles Clippers': 'LA Clippers'}

# Franchise count per league, used to separate real teams from exhibition
# squads by appearance count rather than by maintaining a name list.
FRANCHISES = {'NBA': 30}

# Earliest plausible regular-season date, as MM-DD of the season's first year.
# NBA openers sit in the Oct 21-22 window: sport_registry gives 2026-10-21,
# and the 2024-25 data independently begins 10-22. Anything before this in a
# league whose `season` column spans preseason is an exhibition.
REG_START_MMDD = {'NBA': '10-20'}


def _season_first_year(season) -> int | None:
    t = str(season or '')
    return int(t[:4]) if len(t) >= 4 and t[:4].isdigit() else None


def clean_games(sport: str, games: list[dict], quiet: bool = False) -> list[dict]:
    """Drop exhibitions and preseason; fold alias spellings together."""
    if sport not in FRANCHISES and sport not in REG_START_MMDD:
        return games
    alias = NBA_ALIASES if sport == 'NBA' else {}
    for g in games:
        g['home'] = alias.get(g['home'], g['home'])
        g['away'] = alias.get(g['away'], g['away'])
    appear = collections.Counter()
    for g in games:
        appear[g['home']] += 1
        appear[g['away']] += 1
    n_fr = FRANCHISES.get(sport)
    real = set(t for t, _ in appear.most_common(n_fr)) if n_fr else set(appear)
    mmdd = REG_START_MMDD.get(sport)
    kept, drop_exh, drop_pre = [], 0, 0
    for g in games:
        if n_fr and (g['home'] not in real or g['away'] not in real):
            drop_exh += 1
            continue
        if mmdd:
            y = _season_first_year(g.get('season'))
            d = str(g.get('date') or '')
            if y and d and d < f'{y}-{mmdd}':
                drop_pre += 1
                continue
        kept.append(g)
    if not quiet and (drop_exh or drop_pre):
        print(f'  hygiene: dropped {drop_exh} exhibition / non-franchise and '
              f'{drop_pre} preseason game(s); {len(kept)} remain')
    return kept


def load_with_carryover(sport: str, season: int, quiet: bool = False):
    """Current-season games, plus the PRIOR season at a decaying weight.

    NBA opened 2026-10-03 with exactly ONE scored game in `2026-27`. Rating a
    30-team league on one game produces noise, and shrinking that noise toward
    the mean produces a flat rating nobody can read — which is precisely the
    state NHL is in tonight (sd 0.4 across 32 teams).

    A preseason power rating is supposed to start from last season and move as
    evidence arrives, so that is what this does. The prior season enters at

        w = 1 - (games_per_team / CARRYOVER_FULL[sport])

    so on opening night the rating IS essentially last season, and by 25
    games-per-team (NBA) the prior is gone and the current season stands alone.
    No discontinuity, and no pretending we know something on day one.

    Returns the games unchanged for any sport without a CARRYOVER_FULL entry,
    so NFL / NCAAF / MLB / NHL ratings are untouched.
    """
    cur = clean_games(sport, load_games(sport, season), quiet)
    full = CARRYOVER_FULL.get(sport)
    if not full:
        return cur
    teams = {t for g in cur for t in (g['home'], g['away'])}
    gpt = (2 * len(cur) / len(teams)) if teams else 0.0
    w = max(0.0, 1.0 - gpt / float(full))
    if w <= 0:
        if not quiet:
            print(f'  carryover: none needed ({gpt:.1f} games/team >= {full})')
        return cur
    prior = clean_games(sport, load_games(sport, season - 1), quiet)
    for g in prior:
        g['weight'] = w
        # Explicit flag: consumers must not infer "is this a prior season?"
        # from the weight. On opening night w is exactly 1.0, so a
        # weight-based test silently treats last season as current and any
        # staleness discount never fires.
        g['prior_season'] = True
    if not quiet:
        print(f'  carryover: {gpt:.1f} games/team this season -> prior season '
              f'({len(prior)} games) blended at weight {w:.2f}')
    return cur + prior


#: One timestamp for the whole run, so every row of a single write
#: carries the same clock rather than drifting across chunks.
_NOW = datetime.now(timezone.utc).isoformat()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, choices=sorted(RESULTS))
    ap.add_argument('--season', type=int)
    ap.add_argument('--validate', action='store_true')
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()
    cfg = SPORT_CFG[args.sport]

    if args.validate:
        return validate(args.sport, cfg)

    season = args.season or date.today().year
    games = load_with_carryover(args.sport, season)
    if not games:
        print(f'no {args.sport} games for {season}')
        return 0
    rating = fit_srs(games, cfg)
    sos = sos_from(games, rating)
    print(f'=== {args.sport} {season} · margin SOS/SOR '
          f'(hfa={cfg["hfa"]} cap={cfg["cap"]} ridge={cfg["ridge"]}) ===')
    print('%-6s %9s %9s' % ('team', 'SOR', 'SOS'))
    for t in sorted(rating, key=lambda x: -rating[x]):
        print('%-6s %9.2f %9.2f' % (t, rating[t], sos.get(t, 0.0)))

    if args.write:
        keep = publishable_teams(args.sport, season)
        if keep is not None:
            print(f'  publishing {len(keep)} of {len(rating)} teams '
                  f'(FBS only; the fit still used every game)')
        payload = []
        for key, vals, label in (('sor_margin', rating, 'Strength of Record (margin)'),
                                 ('sos_margin', sos, 'Strength of Schedule (margin)')):
            if keep is not None:
                vals = {t: v for t, v in vals.items() if t in keep}
            order = sorted(vals, key=lambda x: -vals[x])
            for i, t in enumerate(order, 1):
                payload.append({'sport': args.sport, 'team': t, 'season': season,
                                'stat_key': key, 'raw_value': round(vals[t], 3),
                                'rank': i, 'league_size': len(order),
                                'direction': 'higher', 'display_label': label,
                                # ══ 2026-10-09 · STAMP refreshed_at EXPLICITLY ══
                                # team_computed_stats.refreshed_at is
                                # `timestamptz DEFAULT now()`, and a DEFAULT only
                                # fires on INSERT. This script upserts with
                                # merge-duplicates, so every nightly run took the
                                # UPDATE path and the timestamp stayed pinned to
                                # the row's first insert.
                                #
                                # Measured today: sos_margin / sor_margin read
                                # refreshed_at = 2026-10-04T14:47:59 on every
                                # sport. I concluded the margin ratings were five
                                # days stale and went looking for a failing
                                # workflow step. They were not stale at all — the
                                # nightly had run fine every night (heartbeat
                                # confirms nightly_cross_sport completed
                                # 2026-10-08T19:05) and a manual --write of 64 NFL
                                # rows returned 200 while the timestamp did not
                                # move at all. Fresh values, lying clock.
                                #
                                # That is worse than having no timestamp, because
                                # it makes current data look abandoned — and could
                                # equally make abandoned data look current.
                                #
                                # compute_schedule_strength.py hit this exact bug
                                # and fixed it on 2026-09-30 with the same
                                # explicit stamp. Its sibling never got the fix.
                                'refreshed_at': _NOW,
                                'unit': ''})
        r = requests.post(f'{SB}/rest/v1/team_computed_stats'
                          '?on_conflict=sport,team,season,stat_key',
                          headers=H_W, json=payload, timeout=90)
        print(f'\nwrite {len(payload)} rows -> {r.status_code} {r.text[:140]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
