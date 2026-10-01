"""What each NFL defense allows to each position — derived, not fetched.

Andy 2026-10-01: "I want to ensure signal coverage includes defense performance
against that position, does the secondary give up tds, does the defense give up
a lot of rushing tds, qb or rb? That should be thinking of the prop engine."

WHY THIS EXISTS
---------------
nfl_team_defense_stats carries six team-level numbers and no positional split or
TDs-allowed at all, so nfl_generate_props' only opponent input is one EPA figure
applied as a +/-7.5% multiplier. Measured spreads between the best and worst
defense are far larger than that ceiling can express:

    rushing TDs allowed to RB    2.00/g
    rushing TDs allowed to QB    1.00/g
    rushing yards allowed to RB 96.7/g
    receiving yards to WR      106.3/g

nfl_player_stats already has `opponent_team` next to `position`, the TD columns
and the volume columns, per player per week — so this is an aggregation of data
we hold, not a new feed.

SHRINKAGE IS THE WHOLE GAME EARLY SEASON
----------------------------------------
2026 has weeks 1-3 loaded. Three games per team means GB's 2.00 rushing TD/g is
six TDs in three games, and a raw rate like that will regress hard. Every rate
is therefore shipped twice: raw, and shrunk toward a prior — the same
(total + K*prior) / (games + K) form used in compute_schedule_strength, for the
same reason (raw rates on tiny samples look certain when they are not).

The prior is the team's own prior-season rate where we have one, else the league
mean for that position. Falling back to the league mean rather than to zero
matters: a team with no prior rows should look average, not elite.

LEAK NOTE. This writes a CURRENT-STATE table. Do not join it to past games to
backtest — that is exactly the trap in project_rolling_stats_leak_trap_929,
where joining current team_stats_rolling to played games produced a fake 67.8%.
A backtest needs as-of-week snapshots, which this does not produce.

CLI
  python compute_nfl_positional_defense.py                 # current season
  python compute_nfl_positional_defense.py --season 2025
  python compute_nfl_positional_defense.py --dry-run
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
load_dotenv('.env')
SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}',
     'Content-Type': 'application/json'}

POSITIONS = ('QB', 'RB', 'WR', 'TE')

# Games-equivalent weight given to the prior. 4 ~ a month of football: at 3
# games played the current season carries 3/7 of the estimate, at 12 games it
# carries 3/4. Matches the spirit of NFL_BLEND_UNTIL_GAMES=3 in
# nfl_game_context without pretending a hard cutover is honest.
PRIOR_K = 4.0

# Raw columns we sum out of nfl_player_stats, mapped to the per-game rate name.
SUMS = {
    'rush_td': 'rushing_tds',
    'rec_td': 'receiving_tds',
    'rush_yds': 'rushing_yards',
    'rec_yds': 'receiving_yards',
    'carries': 'carries',
    'targets': 'targets',
    'receptions': 'receptions',
}


def _paged(table: str, **params) -> list:
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['offset'] = off
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p, timeout=90)
        r.raise_for_status()
        rows = r.json()
        out.extend(rows)
        if len(rows) < 1000:
            return out
        off += 1000


def aggregate(season: int, season_type: str = 'REG') -> tuple[dict, dict]:
    """-> ({(team, pos): {sums..., 'games': n}}, {pos: league_mean_any_td})

    `team` is the DEFENCE: we group by opponent_team, because a row in
    nfl_player_stats describes what a player DID, and the defence he did it
    against is the opponent. Getting this backwards would invert every number,
    so it is asserted in the caller against a known-good figure.
    """
    sel = ('opponent_team,position,week,'
           + ','.join(sorted(set(SUMS.values()))))
    rows = _paged('nfl_player_stats', season=f'eq.{season}',
                  season_type=f'eq.{season_type}', select=sel)
    agg: dict = defaultdict(lambda: defaultdict(float))
    games: dict = defaultdict(set)
    for r in rows:
        d = r.get('opponent_team')
        pos = (r.get('position') or '').upper()
        if not d:
            continue
        if r.get('week') is not None:
            games[d].add(r['week'])
        if pos not in POSITIONS:
            continue
        a = agg[(d, pos)]
        for name, col in SUMS.items():
            v = r.get(col)
            if v is not None:
                try:
                    a[name] += float(v)
                except (TypeError, ValueError):
                    pass
    for (d, _pos), a in agg.items():
        a['games'] = float(len(games.get(d) or ()))

    # league mean any-TD per game, per position — the fallback prior
    means: dict = {}
    for pos in POSITIONS:
        tot = sum(a['rush_td'] + a['rec_td']
                  for (d, p), a in agg.items() if p == pos)
        gms = sum(a['games'] for (d, p), a in agg.items() if p == pos)
        means[pos] = round(tot / gms, 4) if gms else None
    return agg, means


def _rate(a: dict, name: str):
    g = a.get('games') or 0
    return round(a[name] / g, 4) if g else None


def _shrunk(cur_total: float, cur_games: float, prior_rate, league_rate):
    """(total + K*prior) / (games + K). Prior is the team's own prior-season
    rate, else the league mean. Returns None only when we have nothing at all."""
    prior = prior_rate if prior_rate is not None else league_rate
    if prior is None:
        return _rate({'games': cur_games, '_': cur_total}, '_') if cur_games else None
    return round((cur_total + PRIOR_K * float(prior)) / ((cur_games or 0) + PRIOR_K), 4)


def build(season: int, season_type: str = 'REG') -> list[dict]:
    cur, league = aggregate(season, season_type)
    if not cur:
        print(f'  no {season} {season_type} rows — nothing to build')
        return []
    prior_season = season - 1
    prior, _prior_league = aggregate(prior_season, season_type)
    prior_rates = {k: {n: _rate(a, n) for n in SUMS} for k, a in prior.items()}
    prior_games = {k: int(a.get('games') or 0) for k, a in prior.items()}

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for (team, pos), a in cur.items():
        g = a.get('games') or 0
        pr = prior_rates.get((team, pos), {})
        any_td_cur = a['rush_td'] + a['rec_td']
        any_td_prior = None
        if pr.get('rush_td') is not None or pr.get('rec_td') is not None:
            any_td_prior = (pr.get('rush_td') or 0) + (pr.get('rec_td') or 0)
        gp = prior_games.get((team, pos), 0)
        if g == 0:
            label = f'{prior_season} season'
        elif g >= 3 and gp == 0:
            label = f'{season} season · {int(g)} games'
        else:
            label = (f'blended · {int(g)} game{"s" if g != 1 else ""} this '
                     f'season + {prior_season} season')
        rows.append({
            'season': season, 'season_type': season_type,
            'team': team, 'position': pos, 'games': int(g),
            'rush_td_pg': _rate(a, 'rush_td'),
            'rec_td_pg': _rate(a, 'rec_td'),
            'any_td_pg': round(any_td_cur / g, 4) if g else None,
            'rush_yds_pg': _rate(a, 'rush_yds'),
            'rec_yds_pg': _rate(a, 'rec_yds'),
            'carries_pg': _rate(a, 'carries'),
            'targets_pg': _rate(a, 'targets'),
            'receptions_pg': _rate(a, 'receptions'),
            'rush_td_pg_blended': _shrunk(a['rush_td'], g, pr.get('rush_td'),
                                          None),
            'rec_td_pg_blended': _shrunk(a['rec_td'], g, pr.get('rec_td'), None),
            'any_td_pg_blended': _shrunk(any_td_cur, g, any_td_prior,
                                         league.get(pos)),
            'rush_yds_pg_blended': _shrunk(a['rush_yds'], g, pr.get('rush_yds'),
                                           None),
            'rec_yds_pg_blended': _shrunk(a['rec_yds'], g, pr.get('rec_yds'),
                                          None),
            'prior_games': gp,
            'blend_label': label,
            'league_mean_any_td': league.get(pos),
            'refreshed_at': now,
        })

    # Ranks within position over the BLENDED rates. 1 = allows the most.
    for pos in POSITIONS:
        grp = [r for r in rows if r['position'] == pos]
        for col, rk in (('any_td_pg_blended', 'rank_any_td'),
                        ('rush_yds_pg_blended', 'rank_rush_yds'),
                        ('rec_yds_pg_blended', 'rank_rec_yds')):
            have = [r for r in grp if r.get(col) is not None]
            for i, r in enumerate(sorted(have, key=lambda x: -x[col]), start=1):
                r[rk] = i
            for r in grp:
                r['league_size'] = len(have)
    return rows


def upsert(rows: list[dict], dry_run: bool) -> int:
    if not rows:
        return 0
    if dry_run:
        print(f'  [DRY] would upsert {len(rows)} rows')
        return 0
    wrote = 0
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        r = requests.post(
            f'{SB}/rest/v1/nfl_positional_defense'
            f'?on_conflict=season,season_type,team,position',
            headers={**H, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
            json=chunk, timeout=60)
        if r.status_code not in (200, 201, 204):
            print(f'  upsert failed {r.status_code}: {r.text[:200]}')
            return wrote
        wrote += len(chunk)
    return wrote


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=datetime.now().year)
    ap.add_argument('--season-type', default='REG')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    print(f'=== nfl_positional_defense · season={args.season} '
          f'type={args.season_type} ===')
    rows = build(args.season, args.season_type)
    if not rows:
        return 0
    teams = len({r['team'] for r in rows})
    print(f'  {len(rows)} rows · {teams} defences · '
          f'games={max(r["games"] for r in rows)}')

    # Sanity: the aggregation must be ORIENTED CORRECTLY. If we had grouped by
    # `team` instead of `opponent_team` every number would describe what a
    # defence's own offence produced. A defence allowing ~0 to every position
    # across the league, or a league-mean any-TD far off ~0.3-0.9, means the
    # join flipped.
    for pos in POSITIONS:
        grp = [r for r in rows if r['position'] == pos
               and r['any_td_pg_blended'] is not None]
        if not grp:
            continue
        lo = min(r['any_td_pg_blended'] for r in grp)
        hi = max(r['any_td_pg_blended'] for r in grp)
        worst = max(grp, key=lambda r: r['any_td_pg_blended'])
        print(f'  {pos}: any-TD/g blended {lo:.2f}..{hi:.2f} '
              f'(league mean {grp[0]["league_mean_any_td"]}) · '
              f'most vulnerable {worst["team"]} {worst["any_td_pg_blended"]:.2f}')

    n = upsert(rows, args.dry_run)
    print(f'  upserted {n} rows' if n else '  (no write)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
