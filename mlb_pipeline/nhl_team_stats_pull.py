"""Populate team_stats_rolling for all 32 NHL teams from MoneyPuck.

Andy 2026-09-27: "lets make NHL ready, that is priority as NHL will be
nearly daily games on sport schedule to support non-football days ... I
would love score predictions based on rolling live stats."

Everything he asked for — score projections, a Monte Carlo, five models
each with a take, opponent-aware props — needs team ratings for all 32
teams. Before this, NHL had advanced stats for FOUR:

    corsi_5v5 / xgf_per60 / xga_per60 / pp_pct / pk_pct /
    high_danger_for / high_danger_against      4 teams each
    sos / sor                                 32 teams

TWO CAUSES, BOTH FIXED HERE.

1. THE SOURCE HAS NO CURRENT SEASON YET. MoneyPuck's
   seasonSummary/2026/regular/teams.csv returns HTTP 404 — the 2026-27
   season opens 2026-09-29 and the file is published once games are
   played. 2025 returns 32 teams across 5 situation splits.

   So this falls back to the most recent season that HAS data and records
   which season it used. Prior-season ratings are the correct prior on
   opening night — every rating system starts a season on last year's
   numbers and blends the new ones in as sample accrues. What is NOT
   acceptable is showing them as if they were current, so `season` on the
   row carries the season the numbers actually came from.

2. IT RANKED WITHIN WHATEVER SUBSET IT HAPPENED TO PROCESS. The four
   populated teams carried `league_size: 4` and ranks 1-4, because the
   context builder wrote stats only for teams that had a game that night
   and ranked them against each other. A team can be "1st of 4" and 20th
   in the league. Ranks here are computed across all 32.

COMPETITION RANKING. Equal values share a rank (1,2,2,4), which the
2026-09-26 SOS bug made necessary: 28 teams tied at one value had been
handed 28 different ranks, so identical numbers rendered wildly different
percentiles.

ONE DOWNLOAD, NOT 32. nhl_data_client.get_team_analytics_mp re-fetched
the entire CSV once per team and filtered to one row. This reads it once.

CLI:
  python nhl_team_stats_pull.py
  python nhl_team_stats_pull.py --season 2025
  python nhl_team_stats_pull.py --dry-run
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = dict(H_READ, **{'Content-Type': 'application/json',
                          'Prefer': 'resolution=merge-duplicates'})

MP_URL = ('https://moneypuck.com/moneypuck/playerData/seasonSummary/'
          '{season}/regular/teams.csv')
UA = 'Mozilla/5.0 (compatible; SweatLocker/1.0)'

# MoneyPuck team code -> the full name team_stats_rolling already uses for
# NHL (verified against the 32 rows written for sos/sor).
TEAM_NAMES = {
    'ANA': 'Anaheim Ducks',         'BOS': 'Boston Bruins',
    'BUF': 'Buffalo Sabres',        'CAR': 'Carolina Hurricanes',
    'CBJ': 'Columbus Blue Jackets', 'CGY': 'Calgary Flames',
    'CHI': 'Chicago Blackhawks',    'COL': 'Colorado Avalanche',
    'DAL': 'Dallas Stars',          'DET': 'Detroit Red Wings',
    'EDM': 'Edmonton Oilers',       'FLA': 'Florida Panthers',
    'LAK': 'Los Angeles Kings',     'MIN': 'Minnesota Wild',
    'MTL': 'Montréal Canadiens',    'NJD': 'New Jersey Devils',
    'NSH': 'Nashville Predators',   'NYI': 'New York Islanders',
    'NYR': 'New York Rangers',      'OTT': 'Ottawa Senators',
    'PHI': 'Philadelphia Flyers',   'PIT': 'Pittsburgh Penguins',
    'SEA': 'Seattle Kraken',        'SJS': 'San Jose Sharks',
    'STL': 'St. Louis Blues',       'TBL': 'Tampa Bay Lightning',
    'TOR': 'Toronto Maple Leafs',   'UTA': 'Utah Mammoth',
    'VAN': 'Vancouver Canucks',     'VGK': 'Vegas Golden Knights',
    'WPG': 'Winnipeg Jets',         'WSH': 'Washington Capitals',
}

# stat_key -> (display_label, unit, direction). `direction` drives whether
# a HIGH value ranks first; the client colours off rank, so getting this
# wrong inverts the read on that row.
STAT_META = {
    # 2026-09-28 · LABELS SHORTENED TO FIT THE CARD.
    # Andy: "Labels are truncated. 'Expected goals agai...' and 'Expected
    # goals for /...' are cut off; shorten them to xGA/60 and xGF/60."
    # The Team Stats row puts the label in a centre column between two value
    # columns, so anything past ~18 characters ellipsises — and an ellipsised
    # label is worse than an abbreviation, because "Expected goals agai..."
    # and "Expected goals for /..." are indistinguishable at a glance, which
    # is exactly the pair a reader most needs to tell apart.
    # These strings land in team_computed_stats.display_label and are what
    # the client renders verbatim, so this is the one place to change them.
    'xgf_per60':           ('xGF /60', '', 'higher'),
    'xga_per60':           ('xGA /60', '', 'lower'),
    'corsi_5v5':           ('5v5 CF %', '%', 'higher'),
    'high_danger_for':     ('HD For /60', '', 'higher'),
    'high_danger_against': ('HD Against /60', '', 'lower'),
    'gf_per60':            ('Goals For /60', '', 'higher'),
    'ga_per60':            ('Goals Against /60', '', 'lower'),
    'pp_pct':              ('PP xG /60', '', 'higher'),
    'pk_pct':              ('PK xGA /60', '', 'lower'),
    'shots_for_per60':     ('Shots For /60', '', 'higher'),
    'save_pct_5v5':        ('5v5 Save %', '%', 'higher'),
}


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# Andy 2026-09-27: "use end of last year season until we have a week of
# games then shift." NHL teams play 3-4 times a week, so four games is the
# one-week mark. The threshold matters more than the 404 fallback does: the
# moment the season opens MoneyPuck WILL publish 2026, and a one-game
# sample is a far worse rating than last season's full 82 — xGF/60 off a
# single game swings wildly and would reorder the whole league nightly.
# So the switch is driven by sample size, not by mere availability.
MIN_GAMES_TO_SHIFT = 4


def _games_played(rows: list) -> float:
    """Median games_played across 5on5 rows — robust to one odd team."""
    gp = sorted(_f(r.get('games_played')) for r in rows
                if r.get('situation') == '5on5')
    if not gp:
        return 0.0
    return gp[len(gp) // 2]


def _get(season: int):
    try:
        r = requests.get(MP_URL.format(season=season),
                         headers={'User-Agent': UA}, timeout=25)
    except Exception as e:
        print(f'  ⚠ {season}: {e}')
        return None
    if r.status_code != 200 or len(r.content) < 1000:
        print(f'  {season}: HTTP {r.status_code} — not published yet')
        return None
    rows = list(csv.DictReader(io.StringIO(r.text)))
    return rows or None


def fetch_season(season: int):
    """-> (rows, season_used, reason) or (None, None, reason).

    Prefers the CURRENT season only once it carries at least a week of
    games; otherwise holds on the prior season's finished ratings.
    """
    cur = _get(season)
    if cur:
        gp = _games_played(cur)
        n_teams = len({x.get('team') for x in cur})
        print(f'  {season}: {len(cur)} rows, {n_teams} teams, '
              f'median {gp:.0f} games played')
        if gp >= MIN_GAMES_TO_SHIFT:
            return cur, season, f'current season has {gp:.0f} games played'
        print(f'  {season}: only {gp:.0f} games played — holding on prior '
              f'season until {MIN_GAMES_TO_SHIFT} (about one week)')

    prev = _get(season - 1)
    if prev:
        gp = _games_played(prev)
        print(f'  {season - 1}: {len(prev)} rows, '
              f'{len({x.get("team") for x in prev})} teams, '
              f'{gp:.0f} games played (full season)')
        return prev, season - 1, (
            f'{season} has under {MIN_GAMES_TO_SHIFT} games played; using '
            f'{season - 1} final ratings')
    return None, None, 'no data for either season'


def build_stats(rows: list) -> dict:
    """-> {team_code: {stat_key: value}} computed per 60 minutes of ice time."""
    by_sit: dict = {}
    for r in rows:
        by_sit.setdefault(r.get('situation'), {})[r.get('team')] = r

    ev = by_sit.get('5on5') or {}
    pp = by_sit.get('5on4') or {}
    pk = by_sit.get('4on5') or {}
    out: dict = {}

    for code, r in ev.items():
        if code not in TEAM_NAMES:
            continue
        # iceTime is in seconds; every rate below is per 60 minutes so the
        # numbers are comparable across teams regardless of games played.
        hours = max(_f(r.get('iceTime'), 1.0) / 3600.0, 0.01)
        s = {
            'xgf_per60': _f(r.get('xGoalsFor')) / hours,
            'xga_per60': _f(r.get('xGoalsAgainst')) / hours,
            'corsi_5v5': _f(r.get('corsiPercentage')) * 100.0,
            'high_danger_for': _f(r.get('highDangerShotsFor')) / hours,
            'high_danger_against': _f(r.get('highDangerShotsAgainst')) / hours,
            'gf_per60': _f(r.get('goalsFor')) / hours,
            'ga_per60': _f(r.get('goalsAgainst')) / hours,
            'shots_for_per60': _f(r.get('shotsOnGoalFor')) / hours,
        }
        # 5v5 save percentage — saves over shots faced. A real goaltending
        # signal that survives small samples better than GAA.
        sog_a = _f(r.get('shotsOnGoalAgainst'))
        if sog_a > 0:
            s['save_pct_5v5'] = 100.0 * (1.0 - _f(r.get('goalsAgainst')) / sog_a)

        # Special teams expressed as xG rate while up/down a man. Not the
        # conversion percentage the labels suggest, so the labels say xG.
        if code in pp:
            ph = max(_f(pp[code].get('iceTime'), 1.0) / 3600.0, 0.01)
            s['pp_pct'] = _f(pp[code].get('xGoalsFor')) / ph
        if code in pk:
            kh = max(_f(pk[code].get('iceTime'), 1.0) / 3600.0, 0.01)
            s['pk_pct'] = _f(pk[code].get('xGoalsAgainst')) / kh

        out[code] = s
    return out


def rank_competition(values: dict, higher_is_better: bool) -> dict:
    """{team: value} -> {team: rank}. Equal values share a rank (1,2,2,4).

    Straight enumerate() gave 28 teams tied on one SOS value 28 different
    ranks on 2026-09-26, so identical numbers rendered as wildly different
    percentiles. Ties must tie.
    """
    order = sorted(values.items(), key=lambda kv: kv[1],
                   reverse=higher_is_better)
    out, prev_val, prev_rank = {}, None, 0
    for i, (team, v) in enumerate(order):
        if prev_val is not None and v == prev_val:
            out[team] = prev_rank
        else:
            prev_rank = i + 1
            prev_val = v
            out[team] = prev_rank
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=None,
                    help='season to try first (default: current NHL season)')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    now = datetime.now(timezone.utc)
    # NHL seasons are labelled by their START year, and the season opens in
    # late September, so anything from September on belongs to this year.
    season = a.season or (now.year if now.month >= 9 else now.year - 1)

    print(f'=== NHL team stats pull · target season {season} ===')
    rows, used, reason = fetch_season(season)
    if not rows:
        print(f'  ✗ nothing written — {reason}')
        return 1
    print(f'  → using season {used}: {reason}')
    if used != season:
        print(f'    NUMBERS ARE FROM {used}, carried forward as the {season} '
              f'rating. Re-running once {season} reaches '
              f'{MIN_GAMES_TO_SHIFT} games switches over on its own — no '
              f'flag, no edit. Disclosure for users lives in '
              f'sport_registry.state_message for NHL.')

    stats = build_stats(rows)
    print(f'  built stats for {len(stats)}/32 teams')
    missing = sorted(set(TEAM_NAMES) - set(stats))
    if missing:
        print(f'  ⚠ no row for: {", ".join(missing)}')

    # ══ WRITTEN UNDER THE CURRENT SEASON, ON PURPOSE ══
    #
    # These go to team_computed_stats, whose rows OVERRIDE the matview per
    # (sport, season, stat_key) — see 20260926d. Two consequences decide
    # the stamp:
    #
    #   * The client prefers the current season per stat_key and only
    #     borrows the prior one for keys missing entirely. Stamping 2025
    #     would leave the matview's bogus 4-team rows (league_size 4,
    #     ranks 1-4) winning for those four teams, so four teams would be
    #     ranked out of 4 while the other 28 were ranked out of 32 — two
    #     incompatible scales in one column.
    #   * The override is designed so that when a stat_key is computed,
    #     the computed set is the WHOLE universe for it. Stamping the
    #     current season is what makes that true here.
    #
    # This is a rating carried forward, which is what every rating system
    # does on opening night, and what Andy asked for: "use end of last
    # year season until we have a week of games then shift."
    #
    # The DISCLOSURE lives in sport_registry.state_message — one banner on
    # the sport tab — rather than appended to eleven display labels. The
    # run log below always names the season the numbers came from.
    stamp = season
    payload = []
    for key, (label, unit, direction) in STAT_META.items():
        vals = {c: s[key] for c, s in stats.items() if s.get(key) is not None}
        if not vals:
            continue
        ranks = rank_competition(vals, direction == 'higher')
        for code, v in vals.items():
            payload.append({
                'sport': 'NHL',
                'team': TEAM_NAMES[code],
                'season': stamp,
                'stat_key': key,
                'raw_value': round(v, 4),
                'rank': ranks[code],
                # Always the true league size, never the size of whatever
                # subset happened to be processed — see the module note.
                'league_size': len(vals),
                'direction': direction,
                'display_label': label,
                'unit': unit,
                'refreshed_at': now.isoformat(),
            })

    print(f'  {len(payload)} rows across {len(STAT_META)} stat keys')
    if a.dry_run:
        for p in payload[:10]:
            print(f'    [DRY] {p["team"]:24s} {p["stat_key"]:20s} '
                  f'{p["raw_value"]:8.3f} rank {p["rank"]}/{p["league_size"]}')
        return 0

    # team_stats_rolling is a VIEW (matview UNION team_computed_stats) and
    # a UNION view is not insertable — PostgREST returns 55000. The override
    # table is the write target, which is also what gives these rows
    # precedence over the matview's 4-team rows.
    ok = 0
    for i in range(0, len(payload), 200):
        chunk = payload[i:i + 200]
        r = requests.post(
            f'{SB}/rest/v1/team_computed_stats'
            '?on_conflict=sport,team,season,stat_key',
            headers=H_WRITE, json=chunk, timeout=60)
        if r.status_code in (200, 201, 204):
            ok += len(chunk)
        else:
            print(f'    ✗ upsert {r.status_code}: {r.text[:200]}')
    print(f'  ✓ upserted {ok}/{len(payload)} rows '
          f'(stamped season {stamp}, numbers from {used})')
    return 0 if ok == len(payload) else 1


if __name__ == '__main__':
    raise SystemExit(main())
