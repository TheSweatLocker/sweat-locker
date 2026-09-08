"""grade_potd — grade Pick of the Day (best_bet_YYYY-MM-DD) rows.

Written 2026-09-08 to fill the gap where POTD narratives were being
generated + cached in jerry_cache but never graded post-game. Users
saw ungraded POTD entries every morning ("POTD: MIN ML — PENDING")
even for games that had finished the day before.

Reads:
  jerry_cache.game_id = 'best_bet_YYYY-MM-DD'
  jerry_cache.data → { sport, game (matchup + id), pick side/market, ...}

Looks up the game result:
  MLB → mlb_game_context.away_score, home_score
  NCAAF → ncaaf_game_results.home_score, away_score
  (extensible per SPORT_RESULT_TABLE map)

Computes W/L based on:
  ML: winner matches pick
  SPREAD / RL: adjusted margin vs line
  TOTAL: sum vs line
  Push handled explicitly.

Writes back to jerry_cache.data:
  result: 'Win' | 'Loss' | 'Push' | 'Void'
  graded_at: ISO timestamp

Idempotent — safe to re-run.

USAGE:
    python grade_potd.py                       # yesterday ET
    python grade_potd.py --date 2026-09-06     # specific date
    python grade_potd.py --backfill 7          # last 7 days
    python grade_potd.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys, json
import datetime as dt
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests

SB  = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


SPORT_CTX_TABLE = {
    # sport → (results_table, home_score_col, away_score_col)
    # NB: results tables not context tables — grade_jerry_reads uses these.
    'MLB':   ('mlb_game_results',   'home_score', 'away_score'),
    'NFL':   ('nfl_game_results',   'home_score', 'away_score'),
    'NCAAF': ('ncaaf_game_results', 'home_score', 'away_score'),
    'NCAAB': ('ncaab_game_results', 'home_score', 'away_score'),
    'NBA':   ('nba_game_results',   'home_score', 'away_score'),
    'NHL':   ('nhl_game_results',   'home_score', 'away_score'),
}


def _today_et() -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).strftime('%Y-%m-%d')


def _fetch_potd(date_str: str) -> dict | None:
    r = requests.get(f'{SB}/rest/v1/jerry_cache',
                     headers=H_R,
                     params={'game_id': f'eq.best_bet_{date_str}',
                             'select': 'game_id,data,narrative,fetched_at'},
                     timeout=15)
    if r.status_code != 200 or not r.json():
        return None
    return r.json()[0]


def _fetch_game_result(sport: str, game_id: str) -> dict | None:
    cfg = SPORT_CTX_TABLE.get(sport)
    if not cfg: return None
    tbl, home_col, away_col = cfg
    r = requests.get(f'{SB}/rest/v1/{tbl}',
                     headers=H_R,
                     params={'game_id': f'eq.{game_id}',
                             'select': f'{home_col},{away_col},home_team,away_team'},
                     timeout=15)
    if r.status_code != 200 or not r.json():
        return None
    row = r.json()[0]
    hs = row.get(home_col); as_ = row.get(away_col)
    if hs is None or as_ is None: return None
    return {
        'home_score': float(hs), 'away_score': float(as_),
        'home_team': row.get('home_team'), 'away_team': row.get('away_team'),
    }


def _grade_pick(sport: str, market: str, side: str, line, home_score: float,
                away_score: float) -> str:
    """Return 'Win' | 'Loss' | 'Push' | 'Void' given a graded final."""
    market = (market or '').lower()
    side = (side or '').upper()
    try:
        line = float(line) if line not in (None, '') else None
    except (TypeError, ValueError):
        line = None
    if market in ('ml', 'moneyline', 'fight'):
        if home_score == away_score: return 'Push'
        winner = 'HOME' if home_score > away_score else 'AWAY'
        return 'Win' if side == winner else 'Loss'
    if market in ('total',):
        if line is None: return 'Void'
        total = home_score + away_score
        if abs(total - line) < 0.001: return 'Push'
        went_over = total > line
        if side in ('OVER', 'O'):  return 'Win' if went_over else 'Loss'
        if side in ('UNDER', 'U'): return 'Loss' if went_over else 'Win'
        return 'Void'
    if market in ('rl', 'spread', 'runline'):
        if line is None: return 'Void'
        # `line` is the pick's own side line (e.g. HOME -1.5 → line = -1.5)
        margin = (home_score - away_score) if side == 'HOME' else (away_score - home_score)
        adj = margin + line  # line is signed relative to the side taken
        if abs(adj) < 0.001: return 'Push'
        return 'Win' if adj > 0 else 'Loss'
    return 'Void'


def _lookup_game_id_by_teams(sport: str, date_str: str, away: str, home: str) -> str | None:
    """POTD stores home/away team names but not game_id. Look it up."""
    cfg = SPORT_CTX_TABLE.get(sport)
    if not cfg or not away or not home: return None
    tbl = cfg[0]
    date_col = 'game_date'
    r = requests.get(f'{SB}/rest/v1/{tbl}',
                     headers=H_R,
                     params={date_col: f'eq.{date_str}',
                             'home_team': f'eq.{home}',
                             'away_team': f'eq.{away}',
                             'select': 'game_id'},
                     timeout=15)
    if r.status_code == 200 and r.json():
        return r.json()[0].get('game_id')
    return None


def _extract_pick_from_potd(data: dict, date_str: str) -> dict:
    """Best-effort extraction of pick side/market/line from the POTD data blob."""
    game = data.get('game') or {}
    sport = data.get('sport') or 'MLB'
    game_id = (data.get('game_id') or game.get('id') or game.get('game_id')
               or _lookup_game_id_by_teams(sport, date_str,
                                           game.get('away_team'), game.get('home_team')))
    # Pick side / market are stored in a few places depending on how POTD was
    # composed. Try each key in order.
    pick_side_raw = (data.get('leanDisplay') or data.get('pick') or
                     data.get('call_text') or data.get('call_side') or '')
    call_market = (data.get('call_market') or data.get('market') or '').lower()
    call_side = data.get('call_side') or ''
    call_line = data.get('call_line') or data.get('prop_line') or data.get('line')
    call_side_team = None
    # If we only have leanDisplay text like "Toronto Blue Jays ML" or
    # "Over 8.5", parse it.
    if not call_side and pick_side_raw:
        s = str(pick_side_raw).strip()
        low = s.lower()
        if ' ml' in low or low.endswith('ml'):
            call_market = call_market or 'ml'
            # Team side determined via game_id lookup — leave call_side blank
            # and let the grader look up which side won
            call_side = '__PARSE_TEAM__'
            call_side_team = s[:-3].strip() if low.endswith(' ml') else s.replace(' ML', '').strip()
        elif low.startswith('over'):
            call_market = call_market or 'total'
            call_side = 'OVER'
            # Strip any parenthetical suffix like " (Jerry 74/100)" first
            try: call_line = float(low.replace('over','').split('(')[0].strip().split()[0])
            except (ValueError, IndexError): pass
        elif low.startswith('under'):
            call_market = call_market or 'total'
            call_side = 'UNDER'
            try: call_line = float(low.replace('under','').split('(')[0].strip().split()[0])
            except (ValueError, IndexError): pass
    return {
        'sport': sport, 'game_id': game_id, 'call_market': call_market,
        'call_side': call_side, 'call_line': call_line,
        'call_side_team': call_side_team,
    }


def grade_potd(date_str: str, dry_run: bool = False) -> str:
    row = _fetch_potd(date_str)
    if not row:
        print(f'  {date_str}: no best_bet row'); return 'no-row'
    data = row.get('data') or {}
    if isinstance(data, str):
        try: data = json.loads(data)
        except (json.JSONDecodeError, TypeError): data = {}
    if data.get('result'):
        print(f'  {date_str}: already graded ({data.get("result")})'); return 'already'
    pick = _extract_pick_from_potd(data, date_str)
    if not pick['game_id'] or not pick['call_market']:
        print(f'  {date_str}: cannot extract pick (game_id={pick["game_id"]!r}, '
              f'market={pick["call_market"]!r}) - marking as no-pick')
        result_payload = {'result': 'no-pick', 'graded_at': dt.datetime.now(dt.timezone.utc).isoformat()}
    else:
        game = _fetch_game_result(pick['sport'], pick['game_id'])
        if not game:
            print(f'  {date_str}: game result missing '
                  f'(sport={pick["sport"]}, gid={pick["game_id"][:16]}) — cannot grade yet')
            return 'pending'
        # Resolve team ML picks to HOME/AWAY
        call_side = pick['call_side']
        if call_side == '__PARSE_TEAM__':
            team = (pick['call_side_team'] or '').lower()
            ht = (game['home_team'] or '').lower()
            at = (game['away_team'] or '').lower()
            # Startswith / contains — team names can vary in length
            if team and (team in ht or ht.startswith(team.split()[0]) or team.startswith(ht.split()[0])):
                call_side = 'HOME'
            elif team and (team in at or at.startswith(team.split()[0]) or team.startswith(at.split()[0])):
                call_side = 'AWAY'
            else:
                print(f'  {date_str}: team ML pick "{pick["call_side_team"]}" '
                      f'does not match home({game["home_team"]}) or away({game["away_team"]})')
                return 'unmatched-team'
        result = _grade_pick(pick['sport'], pick['call_market'], call_side,
                             pick['call_line'], game['home_score'], game['away_score'])
        print(f'  {date_str}: {pick["sport"]} {pick["call_market"].upper()} {call_side} '
              f'{game["away_team"]} @ {game["home_team"]} '
              f'({int(game["away_score"])}-{int(game["home_score"])}) -> {result}')
        result_payload = {'result': result,
                          'graded_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                          'graded_by': 'grade_potd.py'}
    if dry_run:
        print(f'    [DRY] would patch: {result_payload}')
        return 'dry'
    # Patch data JSONB
    updated_data = {**data, **result_payload}
    r = requests.patch(f'{SB}/rest/v1/jerry_cache',
                       headers=H_W,
                       params={'game_id': f'eq.best_bet_{date_str}'},
                       json={'data': updated_data},
                       timeout=15)
    if r.status_code in (200, 204):
        return 'graded'
    print(f'  {date_str}: PATCH failed {r.status_code}: {r.text[:200]}')
    return 'patch-failed'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='YYYY-MM-DD (default: yesterday ET)')
    ap.add_argument('--backfill', type=int, default=0,
                    help='Grade last N days (default 0 = single date)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if args.date:
        target = args.date
    else:
        target = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4, days=1)).strftime('%Y-%m-%d')

    dates = [target]
    if args.backfill:
        base = dt.datetime.strptime(target, '%Y-%m-%d')
        dates = [(base - dt.timedelta(days=i)).strftime('%Y-%m-%d')
                 for i in range(args.backfill)]

    print(f'=== grade_potd · {len(dates)} date(s) · dry_run={args.dry_run} ===')
    counts = {'graded': 0, 'already': 0, 'pending': 0, 'no-row': 0,
              'dry': 0, 'unmatched-team': 0, 'patch-failed': 0}
    for d in dates:
        r = grade_potd(d, dry_run=args.dry_run)
        counts[r] = counts.get(r, 0) + 1
    print(f'\nSummary: {dict((k,v) for k,v in counts.items() if v > 0)}')


if __name__ == '__main__':
    main()
