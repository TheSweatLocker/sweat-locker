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
import re
import statistics
import sys
import urllib.parse
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


def is_pregame(event: dict, grace_min: int = 0) -> bool:
    """True only if this event has NOT started yet.

    2026-09-21, added before raising poll cadence. The Odds API /odds
    endpoint returns IN-PROGRESS games with live in-game prices, and none
    of these pullers filtered on commence_time. At one pull a day that
    rarely mattered. Polling every 30 minutes it matters a great deal:
    a live line would be written straight into close_spread / close_total,
    which is the number grading compares against — so we would silently
    corrupt the closing line and therefore every spread and total result
    derived from it. Live prices would also manufacture fake "steam" in
    line_history, since an in-game total has no relationship to the
    pre-game one.

    This is the same hazard mlb_line_poller guards by locking close_total
    within 10 min of first pitch (project_pm_cron_live_game_prop_overwrite).
    Here we simply drop started games entirely: the last pre-game poll is
    the close, which is what the column is supposed to mean.
    """
    ct = event.get('commence_time')
    if not ct:
        return True          # no timestamp to judge by — keep, don't guess
    try:
        dt = datetime.fromisoformat(str(ct).replace('Z', '+00:00'))
    except ValueError:
        return True
    return dt > datetime.now(timezone.utc) + timedelta(minutes=grace_min)


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
    # ══ 2026-10-01 · CAPTURE THE PRICE, NOT JUST THE LINE ══
    # This function already received `price` on every outcome (the request at
    # fetch() asks for h2h,spreads,totals) and kept it only for h2h, dropping
    # it for spreads and totals. No results table anywhere stored a spread or
    # total price as a result, so compute_surface_records graded every rl and
    # total pick at a flat -110.
    #
    # That approximation is roughly fine for a football spread, which really
    # is near -110. It is badly wrong for an NHL puck line: +/-1.5 is a huge
    # cushion in a 6-goal game, so it trades around -250 on the favourite and
    # +190 on the dog. That is exactly why four NHL rl signals look like
    # 70-72% winners (project_nhl_engine_one_sided_1001) against a 65.2%
    # baseline and still cannot be shown to be profitable.
    #
    # A PRICE IS ONLY MEANINGFUL AT A GIVEN LINE, so we cannot median prices
    # across books posting different lines — one book on -1.0 and another on
    # -1.5 are quoting different bets. Take the median LINE first, then median
    # only the prices quoted AT that line. NHL is almost always +/-1.5 so this
    # matches cleanly; the `or` fallback keeps a price when no book sits
    # exactly on the median (possible on a half-point split) rather than
    # silently returning None.
    spreads, totals, home_ml, away_ml = [], [], [], []
    sp_home, sp_away, tot_over, tot_under = [], [], [], []   # (point, price)
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
                elif key == 'spreads':
                    if nm == home:
                        spreads.append(_f(point))
                        sp_home.append((_f(point), _f(price)))
                    elif nm == away:
                        sp_away.append((_f(point), _f(price)))
                elif key == 'totals':
                    side = str(nm).lower()
                    if side == 'over':
                        totals.append(_f(point))
                        tot_over.append((_f(point), _f(price)))
                    elif side == 'under':
                        tot_under.append((_f(point), _f(price)))

    def _price_at(pairs, line):
        """Median price among books quoting exactly `line`; else across all."""
        if line is None or not pairs:
            return None
        at = [p for pt, p in pairs if pt is not None and p is not None
              and abs(pt - line) < 0.01]
        if not at:
            at = [p for _pt, p in pairs if p is not None]
        m = _median(at)
        return int(m) if m is not None else None

    sp_line = _median(spreads)        # home perspective, negative = home fav
    tot_line = _median(totals)
    return {
        'spread': sp_line,
        'total': tot_line,
        'home_ml': int(_median(home_ml)) if _median(home_ml) is not None else None,
        'away_ml': int(_median(away_ml)) if _median(away_ml) is not None else None,
        # Away spread is the negation of the home line, so price it there.
        'spread_home_price': _price_at(sp_home, sp_line),
        'spread_away_price': _price_at(sp_away, -sp_line if sp_line is not None else None),
        'total_over_price': _price_at(tot_over, tot_line),
        'total_under_price': _price_at(tot_under, tot_line),
        'books': len(event.get('bookmakers') or []),
    }


class OddsPuller:
    def __init__(self, sport_code, odds_sport, results_table, id_prefix,
                 spread_col, total_col, season, team_map=None,
                 write_abbrev=False, schedule_fn=None, price_cols=None):
        self.sport_code = sport_code
        self.odds_sport = odds_sport
        self.results_table = results_table
        self.id_prefix = id_prefix
        self.spread_col = spread_col
        self.total_col = total_col
        self.season = season
        self.team_map = team_map
        self.write_abbrev = write_abbrev
        # ══ 2026-10-01 · OPT-IN SPREAD/TOTAL PRICE COLUMNS ══
        # Maps the four logical price keys produced by _consensus to this
        # sport's column names:
        #   {'spread_home_price': 'close_puckline_home_price', ...}
        # Opt-in and defaults to {} because a results table that lacks the
        # column would 400 the whole upsert — per
        # feedback_explicit_select_silent_blanks, a column that isn't there is
        # not a soft failure. Only sports whose migration has landed pass it.
        self.price_cols = price_cols or {}
        # schedule_fn(date_iso) -> [{game_id, home_team, away_team}, ...]
        # Lets the pull adopt the league's CANONICAL game id instead of
        # minting its own. See _canonical_id.
        self.schedule_fn = schedule_fn
        self._sched_cache: dict = {}

    def _canonical_id(self, game_date: str, away: str, home: str):
        """The league's own game id for this fixture, or None.

        Without this the odds pull invents composite ids
        ('nhl_20260929_Florida_Panthers_Carolina_Hurricanes') while the
        context builder stores the league id ('2026020004' for NHL,
        ESPN's '401909090' for NBA) — measured overlap between the two
        was 0 of 5 on both sports. That is the same context-vs-results id
        split that left NFL Vault Match with zero graded games since
        launch, and it silently breaks every downstream game_id join.

        Falls back to the composite id when the schedule has no match, so
        a fixture the league has not published yet still gets stored.
        """
        if not self.schedule_fn:
            return None
        if game_date not in self._sched_cache:
            try:
                self._sched_cache[game_date] = self.schedule_fn(game_date) or []
            except Exception as e:
                print(f'  ⚠ schedule lookup failed for {game_date}: {e}')
                self._sched_cache[game_date] = []
        def n(s):
            return re.sub(r'[^a-z0-9]', '', str(s or '').lower())
        for g in self._sched_cache[game_date]:
            if n(g.get('home_team')) == n(home) and n(g.get('away_team')) == n(away):
                return g.get('game_id')
        return None

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
        # Keep (event, game_id, game_date) so run() can also emit line_history.
        self._event_ids = []
        started = 0
        for ev in events:
            if not is_pregame(ev):
                started += 1
                continue
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
            gid = self._canonical_id(gd, away, home) or (
                f'{self.id_prefix}_{dt_et.strftime("%Y%m%d")}_'
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
            # Spread/total prices, for sports whose columns exist. Written
            # even when None so a book that stops quoting a side clears the
            # stale value instead of leaving yesterday's price in place.
            for _logical, _col in self.price_cols.items():
                row[_col] = c.get(_logical)
            if self.season:
                row['season'] = self.season
            if self.write_abbrev and h_ab:
                row['home_abbrev'] = h_ab
                row['away_abbrev'] = a_ab
            rows.append(row)
            self._event_ids.append((ev, gid, gd))
        if started:
            print(f'  skipped {started} in-progress game(s) — live prices must '
                  f'not overwrite the close')
        return rows, unmapped


    def _write_line_history(self) -> int:
        """Emit line_history rows from the slate we just pulled.

        2026-09-21. line_history is what detect_line_movement reads, which
        feeds line_movement_flags, which feeds classify_line_moves and the
        Steam Room Split view. It had ZERO rows for NHL, NBA and NCAAB, and
        NFL/NCAAF stopped dead on 2026-09-09 — so football has had no line
        movement detection for twelve days OF THE SEASON, and hockey has
        never had any.

        Cause: write_line_history.py reads odds_cache, which is populated
        only when someone opens the Games tab in the app. The newest
        odds_games_* row for any sport is 2026-09-09. MLB is unaffected
        because line_poller calls write_line_history_from_event directly —
        that writer exists precisely to bypass odds_cache and has been
        MLB-only since 09-11.

        Every sport already pulls the same slate response here, so this is
        the same fix applied where it belongs: once, in the shared core.
        Never fatal — a line_history failure must not cost us the odds pull
        itself, which is the row that actually prices the game.
        """
        pairs = getattr(self, '_event_ids', None)
        if not pairs:
            return 0

        # 2026-09-21b PROXIMITY GATE. Writing history for every forward
        # game is almost pure waste: measured today, all four sports had
        # ZERO games inside 36h, yet a single poll wrote 5,960 rows — NHL
        # 782, NBA 810, NFL 964, NCAAF 3,404 — all for fixtures days out
        # whose lines barely move. At a 30-minute cadence that is 214,560
        # rows/day, 6.4M/month, against a line_history table that is
        # already 2.44M rows and 55.8% of the database.
        #
        # Line movement is worth sampling densely near puck drop and
        # sparsely before it. So the POLLER gates to games starting soon
        # (env, set by the workflow), while the per-sport daily pipeline
        # runs with no env and still writes the full slate — giving roughly
        # one sample a day for distant games and high resolution once a
        # game is close. Same signal, a fraction of the rows.
        max_out = os.environ.get('LINE_HISTORY_MAX_HOURS_OUT')
        if max_out:
            try:
                cutoff = datetime.now(timezone.utc) + timedelta(hours=float(max_out))
                kept = []
                for ev, gid, gd in pairs:
                    ct = ev.get('commence_time')
                    if not ct:
                        kept.append((ev, gid, gd)); continue
                    try:
                        if datetime.fromisoformat(str(ct).replace('Z', '+00:00')) <= cutoff:
                            kept.append((ev, gid, gd))
                    except ValueError:
                        kept.append((ev, gid, gd))
                skipped = len(pairs) - len(kept)
                pairs = kept
                if skipped:
                    print(f'  line_history: skipped {skipped} game(s) beyond '
                          f'{max_out}h out (proximity gate)')
                if not pairs:
                    return 0
            except ValueError:
                pass
        try:
            from book_lines_writer import write_line_history_from_event
        except Exception as e:
            print(f'  ⚠ line_history writer unavailable ({e})')
            return 0
        total = 0
        failed = 0
        for ev, gid, gd in pairs:
            try:
                total += write_line_history_from_event(ev, self.sport_code, gid, gd) or 0
            except Exception:
                failed += 1
        note = f'  line_history: {total} rows from {len(pairs)} events'
        if failed:
            note += f' ({failed} event(s) failed)'
        print(note)
        return total

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
        self._write_line_history()
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
        self._prune_composite_twins(rows)
        return w

    def _prune_composite_twins(self, rows: list) -> int:
        """Delete composite-id rows once the canonical id exists for the fixture.

        ══ 2026-10-01 · WHY THESE ROWS ARE IMMORTAL WITHOUT THIS ══
        _canonical_id falls back to f'{prefix}_{YYYYMMDD}_{away}_{home}' when the
        league schedule has not published the fixture yet. That fallback is
        correct and should stay — it is how a forward game gets stored at all.
        The bug is that nothing ever removes it: once the canonical id appears,
        the upsert writes a SECOND row keyed on game_id, and the composite twin
        survives forever. nhl_resolve_results.py only ever fills the canonical
        row, so the twin sits score-NULL permanently.

        Measured on NHL before this existed: 10 composite rows, 9 duplicating a
        canonical row for the same (date, away, home) and 1 a phantom — a
        9/30 Chicago-at-Vegas for a fixture that actually played 9/29 under
        2026020005. None carried a score. Grading was already defended (res_map
        prefers the scored row) so nothing was mis-graded, but they inflate any
        count of games played, which is part of how NHL's record looked deeper
        than it was.

        NEVER deletes a row carrying a score. If a composite row somehow holds a
        real result, that is data to migrate, not discard — so it is reported and
        kept.
        """
        canon = {}
        for r in rows:
            gid = str(r.get('game_id') or '')
            if gid.startswith(f'{self.id_prefix}_'):
                continue        # this run also minted a composite — nothing to pair
            canon[(r.get('game_date'), r.get('away_team'), r.get('home_team'))] = gid
        if not canon:
            return 0
        try:
            got = requests.get(
                f'{SB}/rest/v1/{self.results_table}',
                headers=H_READ,
                params={'game_id': f'like.{self.id_prefix}_*',
                        'select': 'game_id,game_date,away_team,home_team,'
                                  'home_score,away_score',
                        'limit': 1000},
                timeout=30).json()
        except Exception as e:
            print(f'  prune: read failed ({str(e)[:70]}) — skipped')
            return 0
        if not isinstance(got, list):
            return 0
        removed = kept = 0
        for x in got:
            key = (x.get('game_date'), x.get('away_team'), x.get('home_team'))
            if key not in canon:
                continue        # no canonical twin yet — this row is still load-bearing
            if x.get('home_score') is not None or x.get('away_score') is not None:
                print(f'  prune: KEEPING scored composite row {x["game_id"]} — '
                      f'holds a result, migrate rather than delete')
                kept += 1
                continue
            gid = urllib.parse.quote(str(x['game_id']), safe='')
            d = requests.delete(
                f'{SB}/rest/v1/{self.results_table}?game_id=eq.{gid}',
                headers=H_WRITE, timeout=30)
            if d.status_code in (200, 204):
                removed += 1
        if removed or kept:
            print(f'  prune: removed {removed} composite-id row(s) superseded by '
                  f'a canonical id' + (f', kept {kept} scored' if kept else ''))
        return removed
