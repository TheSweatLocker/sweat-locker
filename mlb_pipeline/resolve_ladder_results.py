"""Ladder rung resolver (2026-08-13).

Backfills Win/Loss/Push on pending ladder_rung rows after their games grade,
then updates ladder_state.current_streak / longest_streak / last_result
based on the WIN-run tail.

Grading logic per market:
  ml     → home_score vs away_score, matched against pick_side (label
           contains team name)
  spread → apply the ladder rung's implied side to final margin
  total  → applies pick_side (over/under) to home_score + away_score
           vs the pick's line (parsed from label if present, else stored
           in the future line field)

Sport-universal — looks up the resolved score from each sport's canonical
results table (mlb_pipeline_results, nfl_pipeline_results, etc.).

Ladder discipline: streak resets on any LOSS. Push doesn't reset — it's
neutral. Sequence of results is chronological by qualified_at.

CLI:
    python resolve_ladder_results.py [--since 2026-08-01] [--dry-run]
"""
from __future__ import annotations
import argparse, os, sys, re
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# Per-sport results source. Each returns {game_id: (home_score, away_score)}.
# 2026-08-20: fixed table names — the *_pipeline_results tables never
# existed. Real tables are *_game_results. This bug silently produced
# "loaded 0 MLB results" every day since 8/13 → NO ladder rungs ever
# graded → streak counter stuck at 0. Caught during today's audit
# when user noticed ladder result=None on the Brewers pick.
RESULTS_TABLE = {
    'MLB': 'mlb_game_results',
    'NFL': 'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'NCAAB': 'ncaab_game_results',
    'NBA': 'nba_game_results',
    'NHL': 'nhl_game_results',
}

# ══ 2026-11-27 · THE QUALIFIER WENT CROSS-SPORT; THIS MAP DID NOT EXIST ══
# steam_room_ladder.py runs from cross_sport_lines.yml and scans every sport
# with sport_registry.ladder_eligible, so it has been locking NBA and NFL
# prop rungs since November. _grade_prop_rung queried ONLY
# mlb_pipeline_props, so every one of them returned None — silently, because
# None is also what "not graded yet" looks like. Five rungs sat unresolved
# from 11-11 while their source prop rows were graded, one of them a PRIME
# conv-97 Mahomes over that LOST and read in-app as pending.
#
# NCAAF/NCAAB are deliberately absent: college sports publish no props
# (feedback_college_sports_no_props), so a prop rung in those sports is a
# bug upstream, not a lookup to satisfy. _props_table returns None and the
# rung is named in the ungradeable report rather than failing quietly.
PROPS_TABLE = {
    'MLB': 'mlb_pipeline_props',
    'NFL': 'nfl_pipeline_props',
    'NBA': 'nba_pipeline_props',
    'NHL': 'nhl_pipeline_props',
}


def load_results(sport: str, since: str) -> dict:
    tbl = RESULTS_TABLE.get(sport)
    if not tbl: return {}
    r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_READ,
        params={'game_date': f'gte.{since}',
                'select': 'game_id,home_score,away_score,home_team,away_team'},
        timeout=30)
    if r.status_code != 200: return {}
    out = {}
    for row in r.json():
        gid = row.get('game_id')
        hs = row.get('home_score'); aws = row.get('away_score')
        if gid and hs is not None and aws is not None:
            out[gid] = {
                'home_score': int(hs), 'away_score': int(aws),
                'home_team': row.get('home_team'), 'away_team': row.get('away_team'),
            }
    return out


def grade_rung(rung: dict, res: dict, dry_run: bool = False) -> Optional[str]:
    """Return 'Win' / 'Loss' / 'Push' or None if unresolvable."""
    market = rung.get('market')

    # 2026-09-19: prop rungs are graded from mlb_pipeline_props and never
    # need the game-result row. Dispatching AFTER the `if not res` guard
    # meant a missing mlb_game_results row silently blocked a prop that was
    # already graded. Live case: 9/18 ladder "Gerrit Cole Over 1.5 ER" sat
    # unresolved while the prop itself read Win (final_value=2) — the
    # Yankees@Diamondbacks result row was one of the 10 missing because the
    # freeze_opening_lines trigger had been rejecting MLB writes since 9/17.
    # Grade props first so a gap in one table cannot mask another's answer.
    if market == 'prop':
        return _grade_prop_rung(rung, dry_run=dry_run)

    if not res: return None
    hs = res.get('home_score'); aws = res.get('away_score')
    if hs is None or aws is None: return None
    home = (res.get('home_team') or '').lower()
    away = (res.get('away_team') or '').lower()
    pick = (rung.get('pick_side') or '').lower()

    if market == 'ml':
        pick_home = home and home in pick
        pick_away = away and away in pick
        if not (pick_home or pick_away): return None
        if hs > aws: return 'Win' if pick_home else 'Loss'
        if aws > hs: return 'Win' if pick_away else 'Loss'
        return 'Push'  # unlikely in ML markets

    if market == 'total':
        # Parse line from pick_side ("Under 8.5" / "Over 8.0")
        m = re.search(r'(over|under)\s+(\d+(?:\.\d+)?)', pick, re.I)
        if not m: return None
        side = m.group(1).lower()
        line = float(m.group(2))
        total = hs + aws
        if abs(total - line) < 0.01: return 'Push'
        if side == 'over':
            return 'Win' if total > line else 'Loss'
        return 'Win' if total < line else 'Loss'

    if market == 'spread':
        # Parse line from pick_side ("Team +1.5" / "Team -3")
        m = re.search(r'([+\-]?\d+(?:\.\d+)?)', pick)
        if not m: return None
        line = float(m.group(1))
        pick_home = home and home in pick
        pick_away = away and away in pick
        if not (pick_home or pick_away): return None
        # Home covers +line if hs + line > aws; -line same formula
        if pick_home:
            adj = (hs + line) - aws
        else:
            adj = (aws + line) - hs
        if abs(adj) < 0.01: return 'Push'
        return 'Win' if adj > 0 else 'Loss'

    if market == 'prop':
        # 2026-08-22: prop-market ladder rungs (E-Rod Over 5.5 HA, etc)
        # weren't handled — grader returned None for every prop rung. That
        # caused ladder record to freeze (rungs displayed as "ungraded" even
        # when the underlying prop had been graded in mlb_pipeline_props /
        # prop_playbook_decisions). Now: parse (player, direction, line,
        # prop_type) from pick_side and look up the graded result.
        return _grade_prop_rung(rung, dry_run=dry_run)
    return None


# ══ 2026-11-27 · A HAND-MAINTAINED MAP OF A DETERMINISTIC FUNCTION ══
# This was `_PROP_TYPE_MAP`, 14 hand-listed tokens. The four props tables
# carry 64 distinct prop_type values between them, so 50 of them hit
# `if not prop_family: return None` and were never looked up. threes,
# steals, blocks and pass_tds — every token on the five stuck rungs — were
# among the missing.
#
# The map was never needed. steam_room_ladder.py composes the label with a
# transform that is exactly invertible (steam_room_ladder.py ~line 768):
#
#     pretty = prop_type.replace('_', ' ').replace(' over', '').replace(' under', '')
#     pick_label = f'{player} {dir_word} {line} {pretty.upper()}'
#
# so the token is prop_type minus its direction suffix, underscores spaced,
# uppercased. Inverting it needs no table. Verified round-trip on all 64
# (prop_type, direction) pairs live in mlb/nfl/nba/nhl_pipeline_props:
# 0 failures. A ' over' / ' under' only strips with its leading space, so
# 'turnover_under' -> 'TURNOVER' -> 'turnover_under' survives.
def _prop_type_from_token(token: str, direction: str) -> Optional[str]:
    """Inverse of steam_room_ladder's label transform. 'PASS TDS' + over
    -> 'pass_tds_over'. Returns None only for an empty token."""
    t = ' '.join(str(token or '').strip().lower().split())
    if not t:
        return None
    return t.replace(' ', '_') + '_' + direction


def _props_table(sport: str) -> Optional[str]:
    return PROPS_TABLE.get((sport or '').upper())


def _norm_result(raw) -> Optional[str]:
    v = str(raw or '').strip().upper()
    if v in ('W', 'WIN'):            return 'Win'
    if v in ('L', 'LOSS'):           return 'Loss'
    if v in ('P', 'PUSH', 'V', 'VOID'): return 'Push'
    return None


def _parse_prop_pick(pick: str):
    r"""('Patrick Mahomes', 'over', 1.5, 'PASS TDS') or None.

    2026-11-27: the token group was ([A-Za-z_]+)\s*$, which cannot match a
    token containing a space. 'Patrick Mahomes Over 1.5 PASS TDS' failed
    re.match outright and returned None before any lookup ran. The token is
    now everything after the line; the player-name group stays lazy and the
    direction stays whitespace-bounded, so a surname like 'Overton' cannot
    be mistaken for the direction word.
    """
    m = re.match(r'^(.+?)\s+(over|under)\s+(\d+(?:\.\d+)?)\s+(.+?)\s*$',
                 str(pick or '').strip(), re.I)
    if not m:
        return None
    return m.group(1).strip(), m.group(2).lower(), float(m.group(3)), m.group(4)


def _grade_prop_rung(rung: dict, dry_run: bool = False) -> Optional[str]:
    """Look up the graded result of the prop this rung was built from.

    pick_side examples:
      "Eduardo Rodriguez Over 5.5 HA"          (MLB)
      "Patrick Mahomes Over 1.5 PASS TDS"      (NFL, two-word token)
      "Jerami Grant Under 1.5 THREES"          (NBA)
    Format: <player_name> <Over|Under> <line> <PROP TYPE TOKEN>

    Reads the rung's OWN sport's props table. The rung was written from a
    row in that table, so game_id is in the same key space and is used as
    the primary filter — tightening the match that used to rest on an
    ilike on the player name alone (feedback_two_tables_two_key_spaces).
    """
    parsed = _parse_prop_pick(rung.get('pick_side'))
    if not parsed:
        return None
    player_name, direction, line_val, token = parsed
    prop_type = _prop_type_from_token(token, direction)
    if not prop_type:
        return None

    sport = (rung.get('sport') or '').upper()
    gd = rung.get('game_date')
    gid = rung.get('game_id')
    tbl = _props_table(sport)

    # Lookup 1: the rung's own sport props table.
    if tbl:
        params = {'player_name': f'ilike.{player_name}',
                  'prop_type': f'eq.{prop_type}',
                  'prop_line': f'eq.{line_val}',
                  'direction': f'eq.{direction}',
                  'select': 'id,result,final_value,book_over_odds,book_under_odds',
                  'limit': '1'}
        # game_id is the tighter key; game_date is the fallback for any
        # legacy rung written before the qualifier stored one.
        if gid:
            params['game_id'] = f'eq.{gid}'
        else:
            params['game_date'] = f'eq.{gd}'
        try:
            r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_READ,
                             params=params, timeout=10)
            rows = r.json() if r.status_code == 200 else []
            if rows:
                _backfill_rung_odds(rung, rows[0], direction, dry_run=dry_run)
                verdict = _norm_result(rows[0].get('result'))
                if verdict:
                    return verdict
        except Exception as e:
            print(f'    ! {tbl} lookup failed for rung {rung.get("id")}: {e}')

    # Lookup 2: prop_playbook_decisions — MLB-era fallback, kept for legacy
    # rungs whose mlb_pipeline_props row has since been overwritten.
    try:
        r = requests.get(f'{SB}/rest/v1/prop_playbook_decisions', headers=H_READ,
            params={'player_name': f'ilike.{player_name}',
                    'game_date': f'eq.{gd}',
                    'prop_type': f'eq.{prop_type}',
                    'prop_line': f'eq.{line_val}',
                    'direction': f'eq.{direction}',
                    'result': 'not.is.null',
                    'select': 'result',
                    'limit': '1'},
            timeout=10)
        rows = r.json() if r.status_code == 200 else []
        if rows:
            return _norm_result(rows[0].get('result'))
    except Exception:
        pass

    return None


def _backfill_rung_odds(rung: dict, prop_row: dict, direction: str,
                        dry_run: bool = False) -> None:
    """Fill ladder_rung.odds_american from the source prop when the
    qualifier stored none.

    2026-11-27: the 4 stuck NBA rungs all carried odds_american NULL, so
    even once graded they would have priced at the -110 default and
    understated a plus-money win. The price we want is the one on the side
    the rung took (feedback_the_bar_is_never_the_price_the_signal_fires_at).
    Only ever fills a NULL — never overwrites a stored price, because the
    stored one is the snapshot and the props row is live
    (feedback_market_refreshes_the_pick_does_not).
    """
    if rung.get('odds_american') is not None:
        return
    # 2026-11-27: gated on dry_run. The first dry run of this fix PATCHed
    # four NBA rungs' odds before anything had been approved. The values
    # were the right ones, which is exactly why it was easy to miss: a
    # --dry-run that writes is a broken contract even when it writes the
    # truth (feedback_snapshot_before_the_mutation).
    odds = prop_row.get('book_over_odds') if direction == 'over'         else prop_row.get('book_under_odds')
    if odds is None:
        return
    if dry_run:
        print(f'    [DRY] rung {rung["id"]} odds would backfill {int(odds):+d}')
        return
    try:
        requests.patch(f'{SB}/rest/v1/ladder_rung?id=eq.{rung["id"]}',
                       headers=H_WRITE, json={'odds_american': int(odds)},
                       timeout=10)
        rung['odds_american'] = int(odds)
        print(f'    + rung {rung["id"]} odds backfilled {int(odds):+d} '
              f'from {direction} side of source prop')
    except Exception as e:
        print(f'    ! rung {rung["id"]} odds backfill failed: {e}')


def update_state_from_rungs():
    """Recompute ladder_state.current_streak / longest_streak / last_result
    from all graded ladder_rung rows (chronological).

    2026-08-21 BUG FIX: prior version reset the state to 'reset' whenever
    the latest-graded result was Loss, even if the CURRENT active rung is
    a NEW rung locked after the loss and still in flight. Consequence:
    tonight's E-Rod Over 5.5 HA (rung 6) got flipped to reset because
    yesterday's Grayson rung 5 late-graded as Loss. Fix: only reset if
    the graded LOSS is on the currently-active rung id.
    """
    r = requests.get(f'{SB}/rest/v1/ladder_rung', headers=H_READ,
        params={'result': 'not.is.null', 'order': 'qualified_at.asc',
                'select': 'id,result,qualified_at'}, timeout=15)
    if r.status_code != 200: return
    rows = r.json()
    if not isinstance(rows, list): return
    current = 0; longest = 0; last_result = None; last_rung_id = None
    for row in rows:
        res = row.get('result')
        last_result = res
        last_rung_id = row.get('id')
        if res == 'Win':
            current += 1
            longest = max(longest, current)
        elif res == 'Loss':
            current = 0
        # Push: neither reset nor extend
    st = requests.get(f'{SB}/rest/v1/ladder_state?id=eq.1', headers=H_READ,
        timeout=10).json()
    cur_state = st[0] if isinstance(st, list) and st else {}
    new_status = cur_state.get('status') or 'waiting'
    note = cur_state.get('note') or ''
    active_rung_id = cur_state.get('active_rung_id')
    if (last_result == 'Loss' and cur_state.get('status') == 'active'
            and last_rung_id == active_rung_id):
        # Only reset when the graded LOSS is on the currently-active rung.
        # If a newer rung has been locked, keep the active status — the
        # loss on the older rung is history, not the current in-flight play.
        new_status = 'reset'
        note = f'Reset after loss. New run starts on next qualifier.'
    payload = {
        'status': new_status,
        'current_streak': current,
        'longest_streak': longest,
        'last_result': last_result,
        'last_updated_at': datetime.now(timezone.utc).isoformat(),
        'note': note,
    }
    requests.patch(f'{SB}/rest/v1/ladder_state?id=eq.1',
        headers=H_WRITE, json=payload, timeout=10)
    print(f'  ladder_state → streak {current} · longest {longest} · last {last_result}')


#: A rung still ungraded this long after its game date is a DEFECT, not a
#: game in flight, and makes main() exit non-zero. One day of grace covers a
#: late-night game whose game_date is the day before its UTC finish, and the
#: resolver's own place late in the nightly chain.
STALE_GRACE_DAYS = 1


def run(since: str, dry_run: bool = False) -> tuple:
    """Returns (graded, stale_ungradeable)."""
    # 2026-11-27 - --since BOUNDED THE PENDING FETCH.
    # The default is 14 days back, so a rung that fails to grade for a
    # fortnight leaves the query window and can never be retried by a
    # scheduled run. The 11-11 NBA rung had already aged out. ladder_rung is
    # one play per day - 67 rows in its entire history - so there is no
    # reason to bound the PENDING side at all. --since now bounds only the
    # per-sport results preload, and even that is widened to reach the
    # oldest pending rung. (feedback_a_rolling_window_needs_a_season_floor)
    r = requests.get(f'{SB}/rest/v1/ladder_rung', headers=H_READ,
        params={'result': 'is.null',
                'select': 'id,sport,game_id,market,pick_side,game_date,'
                          'matchup,odds_american'},
        timeout=15)
    if r.status_code != 200:
        print(f'  fetch failed: {r.status_code}'); return (0, 0)
    pending = r.json()
    if not isinstance(pending, list) or not pending:
        print('  no pending rungs to resolve'); update_state_from_rungs(); return (0, 0)

    load_floor = min([since] + [p['game_date'] for p in pending if p.get('game_date')])
    if load_floor != since:
        print(f'  results preload widened {since} -> {load_floor} '
              f'to reach the oldest pending rung')

    # Load results per sport once
    sports = set(r['sport'] for r in pending)
    results_by_sport = {}
    for s in sports:
        results_by_sport[s] = load_results(s, load_floor)
        print(f'  loaded {len(results_by_sport[s])} {s} results')

    graded = 0
    ungradeable: list = []
    for rung in pending:
        res = results_by_sport.get(rung['sport'], {}).get(rung['game_id'])
        # Prop rungs resolve from mlb_pipeline_props, so a missing
        # mlb_game_results row must not skip them (see grade_rung).
        if not res and rung.get('market') != 'prop':
            continue
        verdict = grade_rung(rung, res, dry_run=dry_run)
        if not verdict:
            # ══ 2026-10-08 · A SKIPPED RUNG MUST NOT BE SILENT ══
            # This was a bare `continue`, so a rung the grader could not read
            # vanished into "graded 0 rungs" with no reason given. The 10-07
            # rung "Mike Yastrzemski Under 0.5 HITS" sat ungraded two days
            # that way: mlb_pipeline_props holds total_bases_under, rbis_over,
            # runs_over and hr_under for him but NO hits row, so there was
            # nothing to mirror and nothing said so.
            #
            # The ladder is ONE PLAY A DAY. A single unreported gap freezes
            # the streak and the published record, which is what Andy saw.
            # Name every rung we decline, and why.
            _why = ''
            if rung.get('market') == 'prop' and not _props_table(rung.get('sport')):
                _why = f' [no props table for {rung.get("sport")}]'
            elif not res:
                _why = ' [no game-results row]'
            ungradeable.append((
                f'rung {rung["id"]} {rung.get("game_date")} '
                f'{rung.get("sport")} {rung.get("market")} '
                f'{rung.get("pick_side")!r}' + _why,
                rung.get('game_date')))
            continue
        if dry_run:
            print(f'  [DRY] rung {rung["id"]} · {rung["matchup"]} · {rung["pick_side"]} → {verdict}')
            graded += 1
            continue
        pr = requests.patch(f'{SB}/rest/v1/ladder_rung?id=eq.{rung["id"]}',
            headers=H_WRITE, json={
                'result': verdict,
                'resolved_at': datetime.now(timezone.utc).isoformat(),
            }, timeout=10)
        if pr.status_code in (200, 201, 204):
            graded += 1
            print(f'  ✓ {rung["matchup"]} · {rung["pick_side"]} → {verdict}')
    print(f'\n{"[DRY] would grade" if dry_run else "graded"} {graded} rungs')
    # 2026-11-27 - NAMING A GAP IS NOT REPORTING IT.
    # The 10-08 fix made this loop PRINT every rung it declined, which was
    # the right instinct and still exited 0. run_step.sh records a step by
    # its exit code, so five rungs stuck for 16 days produced no step_failed
    # row and no heartbeat - nightly_cross_sport ran clean every night while
    # the ladder was frozen. A gap older than its game date now exits
    # non-zero so the failure is in the DB, not only in a log nobody reads.
    # run_step.sh always exits 0 itself, so the blast radius is one recorded
    # step failure and the workflow continues
    # (feedback_read_step_failures_from_the_db, feedback_a_guards_blast_radius).
    stale_cut = (datetime.now(timezone.utc).date()
                 - timedelta(days=STALE_GRACE_DAYS)).isoformat()
    stale = [u for (u, gdate) in ungradeable if gdate and gdate < stale_cut]
    if ungradeable:
        print(f'  ! {len(ungradeable)} rung(s) COULD NOT BE GRADED - the '
              f'ladder record is INCOMPLETE until these resolve:')
        for _u, _g in ungradeable:
            print(f'      {_u}')
    if not dry_run: update_state_from_rungs()
    if stale:
        print(f'  FATAL: {len(stale)} rung(s) still ungraded more than '
              f'{STALE_GRACE_DAYS}d past their game date (cut {stale_cut}). '
              f'The published ladder record is wrong until these resolve.',
              file=sys.stderr)
        for _u in stale:
            print(f'      {_u}', file=sys.stderr)
    return (graded, len(stale))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--since', help='YYYY-MM-DD; defaults to 14 days back')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    since = args.since or (datetime.now(timezone.utc) - timedelta(days=14)).date().isoformat()
    print(f'=== resolve_ladder_results · since {since} ===')
    _graded, _stale = run(since, dry_run=args.dry_run)
    if _stale and not args.dry_run:
        sys.exit(1)


if __name__ == '__main__':
    main()
