#!/usr/bin/env python3
"""Fill player_id / team_abbrev / opp_abbrev / player_position on NHL props.

Prerequisite 1 of the NHL prop scoring layer.

Andy 2026-10-03, on the Prop Jerry vision: "we cant just populate prop jerry
with a shit ton on props, there should be some filtering right -- our job is
to surface props that have a matchup edge based on performance history,
pattern from our data, and overall is it a good spot or not."

The matchup half of that needs a prop to reach the player's record against
THIS opponent. Today it cannot:

  * player_id        does not exist on the table before 20261003c
  * team_abbrev      NULL on all 13,174 rows
  * opp_abbrev       NULL on all 13,174 rows
  * player_position  NULL on all 13,174 rows

nhl_player_vs_team holds 24,450 rows over 1,094 players (8,080 with 3+
career games vs an opponent) and keys on player_id. Measured on the
2026-10-03 board, 0 of 497 prop players joined to it. The data and the
consumer both existed; the key between them did not.

WHAT THIS DOES
--------------
nhl_generate_props stays a thin faithful mirror of the book -- it writes what
the Odds API returns and nothing else. Identity is resolved here instead, in
one place, so there is a single owner for "who is this player and who are
they playing".

  player_id / team_abbrev / player_position
      from nhl_player_log.resolve_player(), which builds an exact index from
      all 32 club rosters (one call per club, cached per process) and falls
      back to a UNIQUE surname only. It refuses to guess between two players
      with the same surname -- that refusal is the point, and it is why this
      uses ids rather than the 90%-accurate initial+surname transform:
      10 of 1,094 nhl_player_vs_team names already collide on
      initial+surname (A. Lee, J. Slavin, C. Smith, J. Anderson...), so a
      name join attaches ~1% of props to the wrong athlete, invisibly. That
      is the Kopylov failure (project_stat_integrity_audit_1002).

  opp_abbrev
      the OTHER side of `matchup`, which is populated on 100% of rows as
      "Away Team @ Home Team". Team names are matched against the inverted
      TEAM_NAMES map already maintained in nhl_team_stats_pull, normalised
      for the two spellings that differ between sources ("St Louis Blues" vs
      "St. Louis Blues", and the accent in "Montréal Canadiens").

UNRESOLVED IS A FIRST-CLASS OUTCOME. A player the rosters do not know (a
recall, an emergency goalie, a book typo) gets left NULL and counted, not
guessed at. The scoring layer must read NULL player_id as "no matchup
history available" rather than falling back to a name.

USAGE
    python enrich_nhl_prop_identity.py                  # today ET
    python enrich_nhl_prop_identity.py --date 2026-10-03
    python enrich_nhl_prop_identity.py --days 5         # today and back 4
    python enrich_nhl_prop_identity.py --dry-run
"""
from __future__ import annotations
import argparse
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV, encoding='utf-8'):
        _ln = _ln.strip()
        if _ln and not _ln.startswith('#') and '=' in _ln:
            _k, _v = _ln.split('=', 1)
            os.environ.setdefault(_k, _v.strip().strip('"'))

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
if not SB or not KEY:
    print('enrich_nhl_prop_identity: no Supabase credentials — skipping')
    sys.exit(0)
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}
TABLE = 'nhl_pipeline_props'

try:
    from nhl_player_log import resolve_player, build_name_index
except ImportError as e:
    print(f'enrich_nhl_prop_identity: nhl_player_log unavailable ({e}) '
          f'— cannot resolve identities')
    sys.exit(1)

try:
    from nhl_team_stats_pull import TEAM_NAMES
except ImportError:
    TEAM_NAMES = {}


def _norm_team(name: str) -> str:
    """Fold the spellings that differ between our sources.

    'St. Louis Blues' vs 'St Louis Blues' and the accent in 'Montréal
    Canadiens' are the two real disagreements; everything else is already
    identical. Stripping punctuation and accents covers both without a
    per-team alias list.
    """
    import unicodedata as _u
    n = _u.normalize('NFKD', str(name or ''))
    n = ''.join(c for c in n if not _u.combining(c))
    return ''.join(ch for ch in n.lower() if ch.isalnum())


# full normalised name -> abbrev. Inverted from the map nhl_team_stats_pull
# already maintains, so there is ONE list of 32 clubs in the repo and not a
# second one here to drift from it.
ABBREV_BY_NAME = {_norm_team(v): k for k, v in TEAM_NAMES.items()}


_HAS_PLAYER_ID: bool | None = None


def _has_player_id() -> bool:
    """Is migration 20261003c applied yet?

    The other three identity columns (team_abbrev / opp_abbrev /
    player_position) already exist on the table, so there is no reason for
    this pass to do nothing while the migration is pending -- it fills what
    it can and picks up player_id on the next run. Selecting a column that
    does not exist 400s the WHOLE read and returns zero props, which is how
    this first reported "no props" on a 5,443-row slate.
    """
    global _HAS_PLAYER_ID
    if _HAS_PLAYER_ID is not None:
        return _HAS_PLAYER_ID
    r = requests.get(f'{SB}/rest/v1/{TABLE}', headers=H,
                     params={'select': 'player_id', 'limit': '1'}, timeout=30)
    _HAS_PLAYER_ID = (r.status_code == 200)
    if not _HAS_PLAYER_ID:
        print('  ℹ player_id column not present yet (apply '
              'supabase/migrations/20261003c_nhl_props_player_id.sql) — '
              'filling team_abbrev / opp_abbrev / player_position only')
    return _HAS_PLAYER_ID


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def page(path: str, params: dict) -> list:
    out, off = [], 0
    while True:
        q = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, params=q,
                         timeout=90)
        if r.status_code not in (200, 206):
            print(f'  ⚠ read {path} -> {r.status_code}: {(r.text or "")[:180]}')
            return out
        body = r.json()
        if not isinstance(body, list):
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def _patch(params: dict, body: dict, tries: int = 4):
    """PATCH with backoff. Returns the response, or None after `tries`.

    2026-10-03: a bare requests.patch killed a full run partway through with
    ConnectionResetError(10054) -- the same class that took out the overnight
    prop grader, which is why grade_props.py grew _patch_row with backoff.
    A 13k-row enrichment makes hundreds of calls; one reset should cost a
    retry, not the slate.
    """
    import time
    for i in range(tries):
        try:
            return requests.patch(f'{SB}/rest/v1/{TABLE}', headers=H_W,
                                  params=params, json=body, timeout=30)
        except requests.exceptions.RequestException as e:
            if i == tries - 1:
                print(f'    ⚠ PATCH gave up after {tries} tries '
                      f'({type(e).__name__})')
                return None
            time.sleep(1.5 * (2 ** i))
    return None


def _sides(matchup: str):
    """'Away @ Home' -> (away_abbrev, home_abbrev), or (None, None)."""
    if not matchup or ' @ ' not in str(matchup):
        return None, None
    a, h = str(matchup).split(' @ ', 1)
    return (ABBREV_BY_NAME.get(_norm_team(a)),
            ABBREV_BY_NAME.get(_norm_team(h)))


def run_date(game_date: str, season_year: int, dry_run: bool) -> tuple:
    _pid = _has_player_id()
    _sel = ('id,player_name,matchup,team_abbrev,opp_abbrev,player_position'
            + (',player_id' if _pid else ''))
    props = page(TABLE, {'game_date': f'eq.{game_date}', 'select': _sel})
    if not props:
        print(f'  {game_date}: no props')
        return 0, 0, 0

    # Resolve each DISTINCT name once — ~500 names against 13k rows.
    names = sorted({p['player_name'] for p in props if p.get('player_name')})
    resolved: dict = {}
    for n in names:
        try:
            resolved[n] = resolve_player(n, season_year)
        except Exception as e:
            print(f'    ⚠ resolve failed for {n!r} ({type(e).__name__})')
            resolved[n] = None

    unresolved = sorted(n for n in names if not resolved.get(n))
    patched = skipped = 0
    bad_matchup = Counter()
    # ══ 2026-10-03 · WRITE PER PLAYER, NOT PER ROW ══
    # Identity is a property of (date, player), not of an individual prop
    # row: every sog/goals/points/assists line for one skater on one night
    # carries the same player_id, team, opponent and position. A first pass
    # PATCHed row-by-row -- 13,174 requests for 497 players' worth of facts,
    # and slow enough that it was still running after thousands of rows.
    # Grouping collapses it to one PATCH per (date, player), ~500 per slate,
    # using the game_date + player_name filter the new index covers.
    by_player: dict = {}
    for p in props:
        got = resolved.get(p.get('player_name'))
        away, home = _sides(p.get('matchup'))
        if away is None or home is None:
            bad_matchup[p.get('matchup')] += 1
        body = {}
        if got:
            pid, team, pos = got
            if _pid:
                body['player_id'] = str(pid)
            body['team_abbrev'] = team
            if pos:
                body['player_position'] = pos
            # The opponent is whichever side of the matchup is NOT the
            # player's own club. If the resolved team is neither side the
            # book has the player on a team not in this game -- a trade or a
            # stale roster -- so opp is left NULL rather than guessed.
            if team and away and home:
                if team == home:
                    body['opp_abbrev'] = away
                elif team == away:
                    body['opp_abbrev'] = home
        if not body:
            skipped += 1
            continue
        nm = p.get('player_name')
        prev = by_player.get(nm)
        if prev is None:
            by_player[nm] = {'body': body, 'stale': False}
            prev = by_player[nm]
        # Any row of this player's that disagrees with the resolved identity
        # means the group needs writing. A group where every row already
        # matches is skipped entirely.
        if not all(p.get(k) == v for k, v in body.items()):
            prev['stale'] = True
    updates = [(nm, v['body']) for nm, v in by_player.items() if v['stale']]

    print(f'  {game_date}: {len(props)} props · {len(names)} distinct players '
          f'· resolved {len(names) - len(unresolved)} · unresolved '
          f'{len(unresolved)} · rows to write {len(updates)}')
    if bad_matchup:
        print(f'    ⚠ matchup unparsed on {sum(bad_matchup.values())} rows: '
              f'{list(bad_matchup)[:3]}')
    if unresolved:
        print(f'    unresolved names (left NULL, never guessed): '
              f'{unresolved[:8]}{" ..." if len(unresolved) > 8 else ""}')
    if dry_run:
        return 0, skipped, len(unresolved)

    for nm, body in updates:
        # Filters go through params= so requests percent-encodes names with
        # apostrophes, accents or spaces. Interpolating them into the URL is
        # what made "Texas A&M" eat every write today
        # (feedback_unencoded_url_filter_eats_writes).
        r = _patch({'game_date': f'eq.{game_date}',
                    'player_name': f'eq.{nm}'}, body)
        if r is None:
            continue
        if r.status_code not in (200, 204):
            print(f'    ⚠ PATCH {nm!r} -> {r.status_code}: '
                  f'{(r.text or "")[:140]}')
            continue
        try:
            rows = r.json() if (r.text or '').strip() else []
        except ValueError:
            rows = []
        # A PATCH that matched nothing returns 200 with an empty body, which
        # is indistinguishable from success by status alone
        # (feedback_204_is_not_a_write).
        if rows:
            patched += len(rows)
        else:
            print(f'    ⚠ PATCH {nm!r} matched NO ROWS — write discarded')
    return patched, skipped, len(unresolved)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--days', type=int, default=1,
                    help='process this many dates ending at --date/today')
    ap.add_argument('--season', type=int,
                    help='NHL season start year (default: derived)')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    end = a.date or _et_today()
    # NHL season spans the new year: Jan-Jun belongs to the season that
    # STARTED the prior calendar year.
    y = int(end[:4])
    season = a.season or (y if int(end[5:7]) >= 7 else y - 1)

    print(f'=== enrich_nhl_prop_identity · season {season} '
          f'{"(DRY)" if a.dry_run else "(APPLY)"} ===')
    try:
        idx = build_name_index(season)
        print(f'  roster index: {len(idx)} players across 32 clubs')
    except Exception as e:
        print(f'  ⚠ roster index failed ({type(e).__name__}: {e})')
        return 1
    if not idx:
        print('  roster index empty — refusing to run rather than NULL '
              'every row')
        return 1
    if not ABBREV_BY_NAME:
        print('  ⚠ TEAM_NAMES unavailable — opp_abbrev cannot be derived')

    dates = [(datetime.fromisoformat(end).date()
              - timedelta(days=i)).isoformat() for i in range(max(a.days, 1))]
    tot_p = tot_s = tot_u = 0
    for d in sorted(dates):
        p, s, u = run_date(d, season, a.dry_run)
        tot_p += p; tot_s += s; tot_u += u
    print(f'\n  patched {tot_p} · skipped(unresolvable) {tot_s} · '
          f'unresolved names {tot_u}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
