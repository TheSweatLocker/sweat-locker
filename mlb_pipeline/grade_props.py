"""Post-game prop grader (2026-08-08).

Grades every scored prop in mlb_pipeline_props by fetching actual player
stats from MLB Stats API boxscores. Updates result (W/L/P) and
final_value on each row.

Gap discovered 2026-08-08: yesterday's 53 scored props all had result=null.
Nothing was writing outcomes. grade_prop_jerry_reads.py inherits result
from mlb_pipeline_props, so with no upstream grader, downstream jerry_read
grading + bucket_roi rebuild all decay on stale data.

Prop → stat mapping (MLB):
  ks_over/under    → strikeOuts
  bb_over/under    → baseOnBalls
  er_over/under    → earnedRuns
  ha_over/under    → hits (pitcher)
  outs_over/under  → outs recorded from innings pitched
  hits_over        → hits (batter)

Sport-universal via SPORT_PROPS_TABLE. MLB only for now — NFL/NBA/NCAAF/
NCAAB plug in when their prop pipelines ship + we wire stat feeds.

Usage:
    python grade_props.py                    # grades yesterday
    python grade_props.py --date 2026-08-07  # specific date
    python grade_props.py --backfill 14      # backfill last 14 days
    python grade_props.py --dry-run          # print, no writes
"""
from __future__ import annotations
import argparse, json, os, re, sys, unicodedata
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# Set by --force flag in main()
FORCE_REGRADE = False


SPORT_PROPS_TABLE = {
    'MLB': 'mlb_pipeline_props',
    # 2026-09-19: NBA enabled ahead of the 10-21 opener. Props have
    # generated and scored since 08-17 but nothing ever graded them, so
    # there was no feedback loop — no hit rates, no tier calibration, and
    # no basis for an NBA ban policy. Stats come from ESPN via
    # nba_data_client.get_player_boxscores.
    'NBA': 'nba_pipeline_props',
}

# NBA prop_type → boxscore stat key. Families from nba_generate_props
# MARKET_MAP: pts / reb / ast / threes / blocks / steals / turnovers / pra.
STAT_MAP_NBA = {
    'pts_over':       'pts',       'pts_under':       'pts',
    'reb_over':       'reb',       'reb_under':       'reb',
    'ast_over':       'ast',       'ast_under':       'ast',
    'threes_over':    'threes',    'threes_under':    'threes',
    'blocks_over':    'blocks',    'blocks_under':    'blocks',
    'steals_over':    'steals',    'steals_under':    'steals',
    'turnovers_over': 'turnovers', 'turnovers_under': 'turnovers',
    'pra_over':       'pra',       'pra_under':       'pra',
}


# prop_type → boxscore stat key.
# 2026-09-12: added batter prop types (hits_under, total_bases, rbis, runs,
# hr, batter_ks). Prior map only covered pitcher props + hits_over, so
# 700+ batter SKIP-tier props stayed ungraded every day, inflating the
# ungraded queue and hiding the true daily hit rate for batter categories.
STAT_MAP_MLB = {
    # Pitcher stats
    'ks_over':   'ks',    'ks_under':   'ks',
    'bb_over':   'bb',    'bb_under':   'bb',
    'er_over':   'er',    'er_under':   'er',
    'ha_over':   'h_pit', 'ha_under':   'h_pit',
    'outs_over': 'outs',  'outs_under': 'outs',
    # Batter stats
    'hits_over':        'h_bat',  'hits_under':        'h_bat',
    'total_bases_over': 'tb',     'total_bases_under': 'tb',
    'rbis_over':        'rbi',    'rbis_under':        'rbi',
    'runs_over':        'r',      'runs_under':        'r',
    'hr_over':          'hr',     'hr_under':          'hr',
    'batter_ks_over':   'ks_bat', 'batter_ks_under':   'ks_bat',
}


STAT_MAP_BY_SPORT = {
    'MLB': STAT_MAP_MLB,
    'NBA': STAT_MAP_NBA,
}


def yesterday_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=28)).strftime('%Y-%m-%d')


def fetch_nba_player_stats_for_date(date_str: str):
    """(stats_map, n_final_games) for NBA, shaped like the MLB fetcher.

    n_final_games is counted from the scoreboard rather than inferred
    from the stat rows: grade_date aborts when it is 0, and "no games"
    and "games played but stats not published yet" must not look alike.
    """
    try:
        from nba_data_client import get_player_boxscores, get_scoreboard
    except ImportError:
        print('  ⚠ nba_data_client unavailable — cannot grade NBA')
        return {}, 0
    try:
        finals = [g for g in (get_scoreboard(date_str) or [])
                  if g.get('home_score') is not None]
    except Exception:
        finals = []
    stats = get_player_boxscores(date_str, finals_only=True)
    return stats, len(finals)


def _norm_name(name) -> str:
    """Fold a player name to an accent-free, punctuation-free key.

    2026-09-23. The boxscore is the MLB Stats API's spelling and carries
    diacritics; mlb_pipeline_props carries the odds feed's spelling and
    does not. `name.lower()` leaves 'josé ramírez' != 'jose ramirez', so
    those props matched nothing and were counted as "player not found in
    boxscore (postponed / late scratch?)" — a message that reads like a
    data gap and hid the bug.

    Measured on 2026-09-22: 20 of 47 unmatched players were nothing but
    accents — Ramírez, Suárez, Peña, Báez, Rodríguez, Díaz, Herrera,
    Caballero. Their props never graded on any day they appeared.

    Same defect and same fix as grade_ufc_jerry_reads on the same day.
    """
    n = unicodedata.normalize('NFKD', str(name or ''))
    n = ''.join(c for c in n if not unicodedata.combining(c))
    # The odds feed disambiguates same-named players with a birth year -
    # 'Max Muncy (2002)'. The boxscore carries no such suffix, so the
    # parenthetical has to come off or that player never grades.
    n = re.sub(r'\s*\([^)]*\)', '', n)
    return ' '.join(n.replace('.', '').replace("'", '').lower().split())


def fetch_player_stats_for_date(date_str: str) -> dict:
    """Return {normalized_name: {ks, bb, er, h_pit, outs, h_bat}} for
    every player who appeared in an MLB game on date_str."""
    sched = json.load(urllib.request.urlopen(
        f'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_str}', timeout=15))
    game_pks = []
    postponed_matchups = set()
    for d in sched.get('dates', []):
        for g in d.get('games', []):
            state = g.get('status', {}).get('detailedState', '')
            # 2026-09-23: a postponed game's props can never grade — the
            # game did not happen on this date. Left alone they sit at
            # result=NULL forever and are reported every morning as
            # "player not found in boxscore (postponed / late scratch?)",
            # a message that describes the symptom and hides the cause.
            # Collect them so the caller can Void instead of ignore.
            if state in ('Postponed', 'Cancelled', 'Canceled'):
                try:
                    postponed_matchups.add(
                        f"{g['teams']['away']['team']['name']} @ "
                        f"{g['teams']['home']['team']['name']}")
                except (KeyError, TypeError):
                    pass
                continue
            # Only grade if game is Final (skip in-progress)
            if 'Final' in state or 'Game Over' in state or state == 'Completed Early':
                game_pks.append(g['gamePk'])
    stats = {}
    for pk in game_pks:
        try:
            box = json.load(urllib.request.urlopen(
                f'https://statsapi.mlb.com/api/v1/game/{pk}/boxscore', timeout=15))
        except Exception as e:
            print(f'  ⚠ boxscore {pk} failed: {e}')
            continue
        for team_key in ('home', 'away'):
            team = box.get('teams', {}).get(team_key, {})
            for _, player in team.get('players', {}).items():
                name = player.get('person', {}).get('fullName')
                if not name: continue
                pit = player.get('stats', {}).get('pitching', {}) or {}
                bat = player.get('stats', {}).get('batting', {}) or {}
                ip_str = pit.get('inningsPitched', '0.0')
                outs = 0
                try:
                    ip_int, ip_frac = str(ip_str).split('.')
                    outs = int(ip_int) * 3 + int(ip_frac)
                except (ValueError, AttributeError):
                    pass
                stats[_norm_name(name)] = {
                    # Pitcher
                    'ks':    pit.get('strikeOuts', 0) if pit else 0,
                    'bb':    pit.get('baseOnBalls', 0) if pit else 0,
                    'er':    pit.get('earnedRuns', 0) if pit else 0,
                    'h_pit': pit.get('hits', 0) if pit else 0,
                    'outs':  outs,
                    # Batter (2026-09-12 added tb/rbi/r/hr/ks_bat so batter
                    # props grade instead of being silently skipped).
                    'h_bat': bat.get('hits', 0) if bat else 0,
                    'tb':    bat.get('totalBases', 0) if bat else 0,
                    'rbi':   bat.get('rbi', 0) if bat else 0,
                    'r':     bat.get('runs', 0) if bat else 0,
                    'hr':    bat.get('homeRuns', 0) if bat else 0,
                    'ks_bat':bat.get('strikeOuts', 0) if bat else 0,
                }
    return stats, len(game_pks), postponed_matchups


def grade_prop(prop: dict, stats_map: dict) -> tuple:
    """Return (result_str, actual_value) — result_str in
    {'Win','Loss','Push', None}. None if can't grade.

    2026-09-11 SUSPECT-ZERO GUARD: for outs props (outs_over/outs_under)
    with actual=0, refuse to grade. Real starting pitchers always record
    ≥ 3 outs (usually 15+); 0 outs almost always means the grader fetched
    boxscore before MLB API populated pitching stats — a race between
    game state flipping to Final and the stats push. Locking in 'Win' on
    a bogus 0 corrupts records for days until a manual regrade. Deferring
    (return None) leaves result=null so the next grader run picks it up
    once stats are real. Every outs misgrade last week traced to this.
    """
    sport = (prop.get('_sport') or 'MLB').upper()
    stat_key = STAT_MAP_BY_SPORT.get(sport, STAT_MAP_MLB).get(prop.get('prop_type'))
    if not stat_key: return None, None
    stats = stats_map.get(_norm_name(prop.get('player_name')))
    if not stats: return None, None

    # 2026-09-19 DNP GUARD (NBA). A player who never appeared has 0 in
    # every column, and 0 grades every UNDER as a Win. That exact shape
    # produced the NFL C/ATT misgrades and the Zach Thornton
    # outs_under 16.5 "Win" on final_value 0. The ESPN boxscore states
    # participation directly (didNotPlay + minutes), so a DNP returns
    # 'Void' rather than being silently scored.
    if sport == 'NBA' and stats.get('played') is False:
        return 'Void', None

    actual = stats.get(stat_key)
    if actual is None: return None, None
    # Zero-outs safety net — pitcher props only
    if stat_key == 'outs' and actual == 0:
        return None, None
    line = prop.get('prop_line')
    if line is None: return None, actual
    line = float(line)
    direction = (prop.get('direction') or '').lower()
    if direction == 'over':
        if actual > line: return 'Win', actual
        if actual < line: return 'Loss', actual
        return 'Push', actual
    else:  # under
        if actual < line: return 'Win', actual
        if actual > line: return 'Loss', actual
        return 'Push', actual


def grade_date(date_str: str, sport: str = 'MLB', dry_run: bool = False) -> dict:
    """Grade all ungraded props for a single date. Returns tally dict."""
    table = SPORT_PROPS_TABLE.get(sport.upper())
    if not table:
        print(f'sport {sport} not supported yet'); return {}
    print(f'=== grade_props · {sport} · {date_str} ===')

    # Fetch player stats first (single pass against that sport's source)
    if sport.upper() == 'NBA':
        stats_map, n_games = fetch_nba_player_stats_for_date(date_str)
        postponed: set = set()
        _dnp = sum(1 for v in stats_map.values() if v.get('played') is False)
        print(f'  loaded {len(stats_map)} player stat rows from {n_games} '
              f'final games  ({_dnp} DNP → Void, never graded as a low line)')
    else:
        stats_map, n_games, postponed = fetch_player_stats_for_date(date_str)
        print(f'  loaded {len(stats_map)} player stat rows from {n_games} final games')
        if postponed:
            print(f'  postponed: {", ".join(sorted(postponed))[:110]}'
                  f'  → their props Void, not left ungraded')
    if n_games == 0:
        print(f'  no final games on {date_str} — skipping'); return {}

    # Fetch props for the date. When --force, include already-graded rows
    # too so we can overwrite stale/buggy values from prior grader runs.
    #
    # 2026-09-11 BUGGY-ZERO SWEEP: also pick up any outs prop whose
    # final_value locked at 0 from a prior race-condition grade (see
    # grade_prop SUSPECT-ZERO GUARD). PostgREST `or=(...)` joins the
    # ungraded set with the buggy set so both flow through the same
    # regrade path — self-healing without needing a separate script.
    # 2026-09-12 PAGINATION FIX. PostgREST default limit is 1000 rows;
    # ungraded prop counts have exceeded that daily since prop inventory
    # expanded (2962 ungraded on 9/11). The old single-request fetch
    # returned only the first 1000 rows — sorted by default (id asc),
    # which are the earliest-inserted SKIP-tier batter props. The
    # high-conviction PRIME pitcher props at higher ids never made it
    # into the loop and stayed ungraded FOREVER. Root cause of Andy's
    # "5-0 with 3 pending" on Sweat Card yesterday and every prior day's
    # silent grading gap. Fix: paginate in 1000-row chunks using
    # Range header until fewer than 1000 come back.
    params = {
        'game_date': f'eq.{date_str}',
        'tier': 'in.(PRIME,STRONG,LEAN,SKIP,COVERAGE)',
        'select': 'id,player_name,prop_type,prop_line,direction,tier,conviction,result,final_value,matchup',
        'order': 'id.asc',
    }
    if not FORCE_REGRADE:
        params['or'] = (
            '(result.is.null,'
            'and(prop_type.in.(outs_over,outs_under),final_value.eq.0))'
        )
    r = []
    _page = 0
    while True:
        _lo = _page * 1000
        _hi = _lo + 999
        _headers = {**H_READ, 'Range-Unit': 'items', 'Range': f'{_lo}-{_hi}'}
        _resp = requests.get(f'{SB}/rest/v1/{table}', headers=_headers,
                             params=params, timeout=25)
        if _resp.status_code not in (200, 206):
            print(f'  ⚠ fetch page {_page} {_resp.status_code}: {_resp.text[:150]}')
            break
        _chunk = _resp.json() if isinstance(_resp.json(), list) else []
        r.extend(_chunk)
        if len(_chunk) < 1000: break
        _page += 1
        if _page > 20:  # 20 * 1000 = 20k safety cap
            print(f'  ⚠ hit 20-page safety cap — {len(r)} rows fetched')
            break
    print(f'  {len(r)} props to check ({"force-regrade all" if FORCE_REGRADE else "ungraded + buggy-zero sweep"})')

    # 'V' = Void (NBA DNP). Without this key tally[result[0]] raises
    # KeyError on the first DNP and takes the whole grading run down.
    tally = {'graded': 0, 'skipped_no_stat': 0, 'skipped_no_player': 0, 'errors': 0,
             'W': 0, 'L': 0, 'P': 0, 'V': 0}
    for prop in r:
        prop['_sport'] = sport.upper()   # grade_prop picks the stat map from this
        result, actual = grade_prop(prop, stats_map)
        if result is None:
            # 2026-09-23: a postponed game's props are Void, not
            # ungraded. Left NULL they are re-fetched and re-skipped
            # every run forever, and they inflate the "ungraded" count
            # the morning audit reports. Toronto @ Baltimore was rained
            # out on 09-22 and left 115 props in that state.
            if str(prop.get('matchup') or '') in postponed:
                if not dry_run:
                    requests.patch(
                        f'{SB}/rest/v1/{table}?id=eq.{prop["id"]}',
                        headers=H_WRITE, timeout=10,
                        data=json.dumps({
                            'result': 'Void',
                            'resolved_at': datetime.now(timezone.utc).isoformat()}))
                tally['V'] += 1
                continue
            if stats_map.get(_norm_name(prop.get('player_name'))) is None:
                # 2026-09-24. A player who never appeared in a FINAL game did
                # not "fail to grade" — his prop resolved to no action. Left
                # as NULL these accumulate forever and are re-reported every
                # single morning as "player not found in boxscore", a message
                # that names the symptom and buries the cause. 48 props from
                # 09-22 and 09-23 were sitting in exactly that state.
                #
                # The postponed-game branch above already Voids for the same
                # reason; this is the late-scratch and never-appeared case,
                # which it never covered. Void is already the established
                # outcome for unresolvable props (2,189 rows carry it).
                #
                # Gated on the game being final: an in-progress or unstarted
                # game has a legitimately absent boxscore line and must stay
                # pending. fetch_player_stats_for_date only loads final
                # games, so a non-empty stats_map is that guarantee.
                tally['skipped_no_player'] += 1
                if stats_map:
                    if not dry_run:
                        requests.patch(
                            f'{SB}/rest/v1/{table}?id=eq.{prop["id"]}',
                            headers=H_WRITE, timeout=10,
                            data=json.dumps({
                                'result': 'Void',
                                'resolved_at': datetime.now(timezone.utc).isoformat()}))
                    tally['V'] += 1
            else:
                tally['skipped_no_stat'] += 1
            continue
        tally['graded'] += 1
        tally[result[0]] += 1  # W/L/P
        if dry_run:
            continue
        # Patch the row
        patch = {'result': result, 'final_value': actual,
                 'resolved_at': datetime.now(timezone.utc).isoformat()}
        r2 = requests.patch(
            f'{SB}/rest/v1/{table}?id=eq.{prop["id"]}',
            headers=H_WRITE, data=json.dumps(patch), timeout=10,
        )
        if r2.status_code not in (200, 204):
            tally['errors'] += 1

    print(f'  graded {tally["graded"]}: {tally["W"]}W {tally["L"]}L {tally["P"]}P'
          + (f' {tally["V"]}Void' if tally['V'] else ''))
    # Void is excluded from the hit rate on purpose — a player who did not
    # appear is a returned stake, not a win and not a loss. Counting DNPs
    # either way is how an UNDER book looks artificially good.
    dec = tally['W'] + tally['L']
    if dec:
        print(f'  hit rate: {100*tally["W"]/dec:.1f}%')
    if tally['skipped_no_player']:
        print(f'  ⚠ {tally["skipped_no_player"]} props skipped — player not found in boxscore (postponed / late scratch?)')
    if tally['skipped_no_stat']:
        print(f'  ⚠ {tally["skipped_no_stat"]} props skipped — stat not found (unusual)')
    if tally['errors']:
        print(f'  ⚠ {tally["errors"]} DB write errors')
    return tally


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=yesterday_et())
    ap.add_argument('--sport', default='MLB')
    ap.add_argument('--backfill', type=int, default=0,
                    help='Backfill this many days ending at --date (default 0 = single date)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true',
                    help='Regrade already-graded rows too (overwrites stale/buggy grades from prior graders)')
    args = ap.parse_args()

    global FORCE_REGRADE
    FORCE_REGRADE = args.force

    if args.backfill > 0:
        end_date = datetime.strptime(args.date, '%Y-%m-%d')
        for i in range(args.backfill):
            d = (end_date - timedelta(days=i)).strftime('%Y-%m-%d')
            grade_date(d, args.sport, args.dry_run)
            print()
    else:
        grade_date(args.date, args.sport, args.dry_run)


if __name__ == '__main__':
    main()
