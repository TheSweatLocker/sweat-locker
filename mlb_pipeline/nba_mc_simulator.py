"""NBA Monte Carlo simulator — the lens NBA did not have.

ANDY 2026-10-13, working down the readiness list: NBA Monte Carlo before the
10-20 opener.

WHY THIS WAS THE TOP GAP. mc_probabilities was populated on NFL 106/316,
NCAAF 382/536, NHL 136/187 and MLB 26/36 — and 0 of 81 on NBA. It is also
not a nice-to-have: in the NFL model audit, mc_expected_margin was the
single best voter available, the only one operating at market scale
(regressed on the closing line, slope +0.783 against panel's +0.477) and the
most independent of the others in deviation terms.

THE INPUTS ARE DELIBERATELY NOT THE EXISTING PROJECTION. nba_game_context
already carries projected_spread, projected_total and Elo. Building an "MC"
on top of those would produce another duplicate voter, which is exactly the
failure this codebase spent 2026-10-13 documenting (NCAAF has six spread
columns and one real opinion; NFL's consensus reads a constant). So this
reads nba_team_stats — off_rating, def_rating, pace — and nothing else from
the model stack. Different data, same truth.

THE MODEL

    league-relative efficiency, combined the standard way:
        home_rtg = LG + (home_off - LG) + (away_def - LG)
        away_rtg = LG + (away_off - LG) + (home_def - LG)
    possessions:
        poss = (home_pace + away_pace) / 2
    expected points:
        home_pts = home_rtg * poss / 100 + HFA/2
        away_pts = away_rtg * poss / 100 - HFA/2

EVERY CONSTANT IS MEASURED FROM OUR OWN RESULTS, not borrowed folklore.
From 2,755 graded NBA games in nba_game_results:

    home score      mean 115.0   sd 13.41
    away score      mean 113.2   sd 13.46
    margin          mean +1.82   sd 16.22
    total           mean 228.2   sd 21.41

So HFA is +1.82 points — not the 2.5-3.0 usually quoted, and worth having
measured rather than assumed.

VARIANCE IS TWO-COMPONENT, BECAUSE ONE COMPONENT CANNOT FIT. Simulating two
independent team scores at the observed per-team sd of 13.41 would imply a
margin sd of 13.41*sqrt(2) = 18.97, against the 16.22 actually observed —
scores are positively correlated, since a fast, loose game inflates both. So
each sim draws a shared game-environment term plus an independent team term:

    sd_indep  = 16.22 / sqrt(2) = 11.47     (fixes margin sd exactly)
    sd_common = sqrt(13.41^2 - 11.47^2) = 6.95   (fixes team sd exactly)

Check: implied total sd = sqrt(2*11.47^2 + 4*6.95^2) = 21.3 against the
observed 21.41. All three moments reproduced, which a single-component model
cannot do.

SEASON FALLBACK. nba_team_stats holds 2025-26 and 2024-25 only — there is no
2026-27 data until games are played. The most recent season with data is used
and reported in the blob as mc_stats_season, the same discipline as NFL's
prior_season_regressed fallback. It is stated in the output rather than
hidden, because a rating from last season is a weaker claim than one from
this season and the reader should know which they have.

Writes nba_game_context.mc_probabilities in the same shape the other sports
use, so ensemble_scorer's existing mc reader works unchanged.

CLI
    python nba_mc_simulator.py --dry-run
    python nba_mc_simulator.py
    python nba_mc_simulator.py --date 2026-10-20 --days 3
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

# ══ MEASURED FROM 2,755 GRADED NBA GAMES — see module docstring ══
HFA_POINTS = 1.82
SD_INDEP = 11.47          # per-team independent component
SD_COMMON = 6.95          # shared game-environment component
N_SIMS = 10000

#: abbrev -> full team name as nba_game_context stores it. Same map as
#: enrich_nba_injuries_to_context; the two tables disagree on naming and that
#: mismatch has already silently zeroed one NBA job today.
ABBREV = {
    'ATL': 'Atlanta Hawks', 'BOS': 'Boston Celtics', 'BKN': 'Brooklyn Nets',
    'BRK': 'Brooklyn Nets', 'CHA': 'Charlotte Hornets',
    'CHI': 'Chicago Bulls', 'CLE': 'Cleveland Cavaliers',
    'DAL': 'Dallas Mavericks', 'DEN': 'Denver Nuggets',
    'DET': 'Detroit Pistons', 'GS': 'Golden State Warriors',
    'GSW': 'Golden State Warriors', 'HOU': 'Houston Rockets',
    'IND': 'Indiana Pacers', 'LAC': 'LA Clippers',
    'LAL': 'Los Angeles Lakers', 'MEM': 'Memphis Grizzlies',
    'MIA': 'Miami Heat', 'MIL': 'Milwaukee Bucks',
    'MIN': 'Minnesota Timberwolves', 'NO': 'New Orleans Pelicans',
    'NOP': 'New Orleans Pelicans', 'NY': 'New York Knicks',
    'NYK': 'New York Knicks', 'OKC': 'Oklahoma City Thunder',
    'ORL': 'Orlando Magic', 'PHI': 'Philadelphia 76ers',
    'PHX': 'Phoenix Suns', 'PHO': 'Phoenix Suns',
    'POR': 'Portland Trail Blazers', 'SAC': 'Sacramento Kings',
    'SA': 'San Antonio Spurs', 'SAS': 'San Antonio Spurs',
    'TOR': 'Toronto Raptors', 'UTA': 'Utah Jazz', 'UTAH': 'Utah Jazz',
    'WAS': 'Washington Wizards', 'WSH': 'Washington Wizards',
}


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _et_today():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def page(table, params, cap=50000):
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H_READ, params=q,
                         timeout=120)
        if r.status_code not in (200, 206):
            print(f'  ✗ {table} HTTP {r.status_code}: {r.text[:140]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000 or len(out) >= cap:
            return out
        off += 1000


def load_team_stats():
    """-> (by_team_fullname, season_label, league_avg_rtg).

    Uses the most recent season that actually has rows. Reported in the blob
    so a prior-season rating is never mistaken for a current one.
    """
    rows = page('nba_team_stats', {'select': '*'})
    if not rows:
        return {}, None, None
    # ══ 2026-10-30 · THE LATEST SEASON LABEL IS NOT THE LATEST RATINGS ══
    # Two writers disagree about which season they own, and this loader
    # believed the newer label:
    #   nba_four_factors_pull  writes the CURRENT season (2026-27) -- pace,
    #                          eFG, TOV, ORB, and NO ratings
    #   nba_elo train          writes resolve_season(), which stays on
    #                          2025-26 until 200 games are finished -- the
    #                          ratings, and nothing else
    # So 2026-27 existed with pace on all 30 teams and off_rating NULL on all
    # 30. seasons[-1] picked it, offs came back empty, and this function
    # returned lg=None -- which aborted the simulator with "nba_team_stats
    # unusable" on EVERY run. mc_probabilities was therefore empty on all 21
    # forward NBA games, which blinded steam_room_ladder (it needs a model
    # probability and treats None as -999 edge) and left the NBA engine with
    # no model number at all.
    # Walk seasons newest-first and take the newest one that actually carries
    # ratings. The chosen label is already reported as mc_stats_season, so a
    # prior-season vintage stays visible rather than silently assumed current.
    seasons = sorted({str(x.get('season')) for x in rows if x.get('season')})
    season, sub, offs = None, [], []
    for cand in reversed(seasons):
        c_sub = [x for x in rows if str(x.get('season')) == cand]
        c_offs = [v for v in (_f(x.get('off_rating')) for x in c_sub)
                  if v is not None]
        if c_offs:
            season, sub, offs = cand, c_sub, c_offs
            if cand != seasons[-1]:
                print(f'  ⚠ newest season {seasons[-1]} has no off_rating — '
                      f'falling back to {cand} (reported as mc_stats_season)')
            break
    if not offs:
        return {}, (seasons[-1] if seasons else None), None
    lg = statistics.fmean(offs)
    by = {}
    for x in sub:
        name = (x.get('team_name')
                or ABBREV.get(str(x.get('team_abbrev') or '').upper()))
        if not name:
            continue
        o, d, p = (_f(x.get('off_rating')), _f(x.get('def_rating')),
                   _f(x.get('pace')))
        if o is None or d is None:
            continue
        by[str(name)] = {'off': o, 'def': d, 'pace': p or 99.4,
                         'abbrev': x.get('team_abbrev')}
        ab = str(x.get('team_abbrev') or '').upper()
        if ab and ABBREV.get(ab):
            by[ABBREV[ab]] = by[str(name)]
    return by, season, lg


def simulate(home, away, lg, rng):
    """-> blob dict. home/away are stat dicts from load_team_stats."""
    poss = (home['pace'] + away['pace']) / 2.0
    home_rtg = lg + (home['off'] - lg) + (away['def'] - lg)
    away_rtg = lg + (away['off'] - lg) + (home['def'] - lg)
    mu_h = home_rtg * poss / 100.0 + HFA_POINTS / 2.0
    mu_a = away_rtg * poss / 100.0 - HFA_POINTS / 2.0

    hw = aw = ties = 0
    margins = []
    totals = []
    for _ in range(N_SIMS):
        common = rng.gauss(0.0, SD_COMMON)
        h = mu_h + common + rng.gauss(0.0, SD_INDEP)
        a = mu_a + common + rng.gauss(0.0, SD_INDEP)
        margins.append(h - a)
        totals.append(h + a)
        if h > a:
            hw += 1
        elif a > h:
            aw += 1
        else:
            ties += 1
    return {
        'mc_p_home': round(hw / N_SIMS, 4),
        'mc_p_away': round(aw / N_SIMS, 4),
        'mc_p_sim_tie': round(ties / N_SIMS, 4),
        'mc_expected_margin': round(statistics.fmean(margins), 2),
        'mc_expected_total': round(statistics.fmean(totals), 2),
        'mc_stddev_margin': round(statistics.pstdev(margins), 2),
        'mc_sims': N_SIMS,
        'mc_home_mu': round(mu_h, 2),
        'mc_away_mu': round(mu_a, 2),
        'mc_possessions': round(poss, 1),
    }, margins, totals


def run(game_date, days, dry_run):
    stats, season, lg = load_team_stats()
    if not stats or lg is None:
        print('  ✗ nba_team_stats unusable — aborting rather than writing '
              'guesses')
        return 1
    print(f'=== NBA Monte Carlo · {game_date} (+{days}d) ===')
    print(f'  ratings from season {season} · league avg off_rating {lg:.1f}')
    print(f'  measured constants: HFA {HFA_POINTS}, sd_indep {SD_INDEP}, '
          f'sd_common {SD_COMMON}, sims {N_SIMS}')
    until = (datetime.fromisoformat(game_date)
             + timedelta(days=days)).date().isoformat()
    rows = [g for g in page('nba_game_context', {'select': '*'})
            if game_date <= str(g.get('game_date'))[:10] <= until]
    print(f'  {len(rows)} context rows in window\n')
    rng = random.Random(20261013)
    wrote = missing = 0
    for g in sorted(rows, key=lambda z: str(z.get('game_date'))):
        h, a = str(g.get('home_team')), str(g.get('away_team'))
        sh, sa = stats.get(h), stats.get(a)
        if not sh or not sa:
            missing += 1
            print(f"  ⚠ no ratings for {a if not sa else h} — skipped "
                  f"({a} @ {h})")
            continue
        blob, margins, totals = simulate(sh, sa, lg, rng)
        blob['mc_stats_season'] = season
        blob['generated_at'] = datetime.now(timezone.utc).isoformat()
        ct = _f(g.get('close_total'))
        if ct is not None:
            blob['mc_p_over_line'] = round(
                sum(1 for t in totals if t > ct) / N_SIMS, 4)
        cs = _f(g.get('close_spread'))
        if cs is not None:
            # NBA close_spread is NEGATIVE for a home favourite, so the
            # home side covers when margin > -close_spread.
            blob['mc_p_home_covers'] = round(
                sum(1 for m in margins if m > -cs) / N_SIMS, 4)
        blob['mc_confidence_high'] = bool(
            abs(blob['mc_p_home'] - 0.5) >= 0.12)
        line_txt = f"close={cs}" if cs is not None else 'no line'
        print(f"  {g.get('game_date')} {a[:22]:<24} @ {h[:22]:<24} "
              f"margin {blob['mc_expected_margin']:+6.2f} total "
              f"{blob['mc_expected_total']:6.1f} p_home "
              f"{blob['mc_p_home']:.3f}  {line_txt}")
        if dry_run:
            wrote += 1
            continue
        r = requests.patch(
            f"{SB}/rest/v1/nba_game_context?game_id=eq.{g['game_id']}",
            headers=H_WRITE, json={'mc_probabilities': blob}, timeout=20)
        if r.status_code in (200, 204):
            wrote += 1
        else:
            print(f'    ✗ patch {r.status_code}: {r.text[:140]}')
    print(f"\n{'[DRY] would write' if dry_run else '✓ wrote'} {wrote} rows"
          + (f' · {missing} skipped for missing ratings' if missing else ''))
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', default=None)
    p.add_argument('--days', type=int, default=10)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    return run(a.date or _et_today(), a.days, a.dry_run)


if __name__ == '__main__':
    sys.exit(main())
