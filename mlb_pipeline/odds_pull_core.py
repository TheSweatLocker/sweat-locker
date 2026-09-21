"""Forward-schedule + live-lines pull for the pro leagues (NBA, NHL).

Andy 2026-09-21: "I want to build those and make sure both are caught up
completely ... keep grinding until NHL and NBA are where they need to be."

WHY THIS IS THE BLOCKER
There was no nhl_odds_pull.py or nba_odds_pull.py, and an odds pull is
what creates FORWARD schedule rows. Measured 2026-09-21:

    nfl_game_results    241 forward games
    ncaaf_game_results  104 forward
    nhl / nba / ncaab     0 forward

Zero forward games means no slate, which means no context row, no game
card, no props and nothing for the external pullers to match against.
Everything downstream was waiting on this one script.

The Odds API has both schedules already: 33 NHL games from 2026-09-29,
41 NBA games from 2026-10-20.

TEAM NAMES — the two leagues are NOT the same problem
  NBA  The Odds API names match our stored names exactly, 27/27. No map.
  NHL  ZERO of 32 match. nhl_data_client writes the NHL API's
       `placeName`, which is the CITY — so nhl_game_results holds
       "Boston", "New York", "Los Angeles". That makes Rangers and
       Islanders BOTH "New York", and the Kings "Los Angeles":
       genuinely ambiguous, un-joinable against any odds feed, and the
       reason the external matcher scored 0/32.

       So NHL carries an explicit 32-team map and this puller writes the
       FULL team name. A fixed league of 32 is exactly where a hardcoded
       map is the honest tool rather than fuzzy matching.

SIGN CONVENTION
close_spread / close_puckline are stored HOME-PERSPECTIVE, NEGATIVE =
home favored — matching NBA/NHL context builders and MLB/NCAAB. NFL is
the outlier that stores positive = home favored. Getting this backwards
silently inverts every spread grade, which has happened three times in
this codebase already (project_close_spread_sign_bug_914).
"""
from __future__ import annotations

import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
SB_KEY = os.environ.get('SUPABASE_KEY')
ODDS_KEY = os.environ.get('ODDS_API_KEY')
H_READ = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}
ODDS_API_BASE = 'https://api.the-odds-api.com/v4/sports'

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


# The Odds API full name -> (abbrev, place name as stored historically).
# NHL only; NBA needs no map.
NHL_TEAMS = {
    'Anaheim Ducks': ('ANA', 'Anaheim'),
    'Boston Bruins': ('BOS', 'Boston'),
    'Buffalo Sabres': ('BUF', 'Buffalo'),
    'Calgary Flames': ('CGY', 'Calgary'),
    'Carolina Hurricanes': ('CAR', 'Carolina'),
    'Chicago Blackhawks': ('CHI', 'Chicago'),
    'Colorado Avalanche': ('COL', 'Colorado'),
    'Columbus Blue Jackets': ('CBJ', 'Columbus'),
    'Dallas Stars': ('DAL', 'Dallas'),
    'Detroit Red Wings': ('DET', 'Detroit'),
    'Edmonton Oilers': ('EDM', 'Edmonton'),
    'Florida Panthers': ('FLA', 'Florida'),
    'Los Angeles Kings': ('LAK', 'Los Angeles'),
    'Minnesota Wild': ('MIN', 'Minnesota'),
    'Montreal Canadiens': ('MTL', 'Montréal'),
    'Montréal Canadiens': ('MTL', 'Montréal'),
    'Nashville Predators': ('NSH', 'Nashville'),
    'New Jersey Devils': ('NJD', 'New Jersey'),
    'New York Islanders': ('NYI', 'New York'),
    'New York Rangers': ('NYR', 'New York'),
    'Ottawa Senators': ('OTT', 'Ottawa'),
    'Philadelphia Flyers': ('PHI', 'Philadelphia'),
    'Pittsburgh Penguins': ('PIT', 'Pittsburgh'),
    'San Jose Sharks': ('SJS', 'San Jose'),
    'Seattle Kraken': ('SEA', 'Seattle'),
    'St Louis Blues': ('STL', 'St. Louis'),
    'St. Louis Blues': ('STL', 'St. Louis'),
    'Tampa Bay Lightning': ('TBL', 'Tampa Bay'),
    'Toronto Maple Leafs': ('TOR', 'Toronto'),
    'Utah Hockey Club': ('UTA', 'Utah'),
    'Utah Mammoth': ('UTA', 'Utah'),
    'Vancouver Canucks': ('VAN', 'Vancouver'),
    'Vegas Golden Knights': ('VGK', 'Vegas'),
    'Washington Capitals': ('WSH', 'Washington'),
    'Winnipeg Jets': ('WPG', 'Winnipeg'),
}


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _et_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _median(vals):
    vals = [v for v in vals if v is not None]
    return round(statistics.median(vals), 2) if vals else None


def _consensus(event: dict, home: str, away: str) -> dict:
    """Median line across every bookmaker in the response.

    Median not mean: one stale book posting an outlier should not drag
    the consensus, and with ~9 books a single bad quote is exactly the
    kind of thing that moves a mean.
    """
    spreads, totals, home_ml, away_ml = [], [], [], []
    for bk in event.get('bookmakers') or []:
        for mk in bk.get('markets') or []:
            key = mk.get('key')
            for oc in mk.get('outcomes') or []:
                nm, price, point = oc.get('name'), oc.get('price'), oc.get('point')
                if key == 'h2h':
                    if nm == home:
                        home_ml.append(_f(price))
                    elif nm == away:
                        away_ml.append(_f(price))
                elif key == 'spreads' and nm == home:
                    spreads.append(_f(point))
                elif key == 'totals' and str(nm).lower() == 'over':
                    totals.append(_f(point))
    return {
        'spread': _median(spreads),      # home perspective, negative = home fav
        'total': _median(totals),
        'home_ml': int(_median(home_ml)) if _median(home_ml) is not None else None,
        'away_ml': int(_median(away_ml)) if _median(away_ml) is not None else None,
        'books': len(event.get('bookmakers') or []),
    }


class OddsPuller:
    def __init__(self, sport_code, odds_sport, results_table, id_prefix,
                 spread_col, total_col, season, team_map=None,
                 write_abbrev=False):
        self.sport_code = sport_code
        self.odds_sport = odds_sport
        self.results_table = results_table
        self.id_prefix = id_prefix
        self.spread_col = spread_col
        self.total_col = total_col
        self.season = season
        self.team_map = team_map
        self.write_abbrev = write_abbrev

    def _resolve(self, api_name: str):
        """-> (stored_team_name, abbrev). None when unmappable."""
        if not self.team_map:
            return api_name, None
        hit = self.team_map.get(api_name)
        if not hit:
            return None, None
        abbrev, _place = hit
        # Write the FULL name, not the historical place name: "New York"
        # cannot distinguish Rangers from Islanders.
        return api_name, abbrev

    def fetch(self) -> list:
        if not ODDS_KEY:
            print('  ✗ ODDS_API_KEY not set')
            return []
        r = requests.get(
            f'{ODDS_API_BASE}/{self.odds_sport}/odds',
            params={'apiKey': ODDS_KEY, 'regions': 'us',
                    'markets': 'h2h,spreads,totals', 'oddsFormat': 'american'},
            timeout=40)
        if r.status_code != 200:
            print(f'  ✗ Odds API {r.status_code}: {r.text[:200]}')
            return []
        print(f'  Odds API ok · quota left {r.headers.get("x-requests-remaining")}')
        return r.json()

    def build_rows(self, events: list) -> tuple:
        rows, unmapped = [], []
        for ev in events:
            api_home, api_away = ev.get('home_team'), ev.get('away_team')
            home, h_ab = self._resolve(api_home)
            away, a_ab = self._resolve(api_away)
            if not home or not away:
                unmapped.append(f'{api_away} @ {api_home}')
                continue
            ct = ev.get('commence_time') or ''
            try:
                dt_utc = datetime.fromisoformat(ct.replace('Z', '+00:00'))
            except ValueError:
                continue
            # Game DATE is the ET calendar day — a 7pm ET tip is the same
            # day, but a late start lands on the next UTC day and would
            # otherwise file under tomorrow.
            dt_et = dt_utc - timedelta(hours=4)
            gd = dt_et.date().isoformat()
            c = _consensus(ev, api_home, api_away)
            gid = (f'{self.id_prefix}_{dt_et.strftime("%Y%m%d")}_'
                   f'{away}_{home}').replace(' ', '_')
            row = {
                'game_id': gid,
                'game_date': gd,
                'home_team': home,
                'away_team': away,
                self.spread_col: c['spread'],
                self.total_col: c['total'],
                'close_home_ml': c['home_ml'],
                'close_away_ml': c['away_ml'],
            }
            if self.season:
                row['season'] = self.season
            if self.write_abbrev and h_ab:
                row['home_abbrev'] = h_ab
                row['away_abbrev'] = a_ab
            rows.append(row)
        return rows, unmapped

    def run(self, dry_run: bool = False) -> int:
        print(f'=== {self.sport_code} odds pull · {_et_now():%Y-%m-%d %H:%M} ET ===')
        events = self.fetch()
        if not events:
            return 0
        rows, unmapped = self.build_rows(events)
        print(f'  events: {len(events)} · rows built: {len(rows)}')
        if unmapped:
            # Loud, never silent — an unmapped team is a game we simply
            # will not have, and that must be visible rather than inferred
            # later from a thin slate.
            print(f'  ⚠ UNMAPPED ({len(unmapped)}) — these games are NOT stored:')
            for u in unmapped[:10]:
                print(f'      {u}')
        dates = sorted({r['game_date'] for r in rows})
        if dates:
            print(f'  date span: {dates[0]} .. {dates[-1]} ({len(dates)} dates)')
        if dry_run:
            for r in rows[:5]:
                print(f'    [DRY] {r["game_id"]}  sp={r.get(self.spread_col)} '
                      f'tot={r.get(self.total_col)} ml={r.get("close_home_ml")}/'
                      f'{r.get("close_away_ml")}')
            return len(rows)
        w = 0
        for i in range(0, len(rows), 200):
            batch = rows[i:i + 200]
            resp = requests.post(
                f'{SB}/rest/v1/{self.results_table}?on_conflict=game_id',
                headers=H_WRITE, json=batch, timeout=40)
            if resp.status_code in (200, 201, 204):
                w += len(batch)
            else:
                print(f'  ⚠ write {resp.status_code}: {resp.text[:220]}')
        print(f'  ✓ upserted {w}/{len(rows)} games into {self.results_table}')
        return w
