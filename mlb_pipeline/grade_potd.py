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
import argparse, os, re, sys, json
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

# Sport → prop-pipeline table (used only when POTD is a player prop —
# e.g. "Jared Jones Under 15.5 Outs"). Grader looks up the pre-graded
# row by (game_id, player_name, prop_type, direction) and mirrors its
# result. Falls back to Void if the row exists but hasn't been graded.
PROP_TABLE_BY_SPORT = {
    'MLB': 'mlb_pipeline_props',
    'NFL': 'nfl_pipeline_props',
}

# Trailing-word → prop_type key. Order matters: check the longer
# multi-word phrases before single-word ones so "hits allowed"
# resolves as `ha_*` before "hits" alone matches `hits_*`.
_PROP_STAT_ALIASES = [
    ('hits allowed',   'ha'),
    ('earned runs',    'er'),
    ('total bases',    'total_bases'),
    ('rbis',           'rbis'),
    ('strikeouts',     'ks'),
    ('walks',          'bb'),
    ('outs recorded',  'outs'),
    ('outs',           'outs'),
    ('ks',             'ks'),
    ('bb',             'bb'),
    ('home runs',      'hr'),
    ('runs',           'runs'),
    ('hits',           'hits'),
]


def _parse_prop_from_lean(lean: str) -> dict | None:
    """Return {player_name, direction, line, prop_type} if `lean` looks
    like a player-prop POTD (e.g. "Jared Jones Under 15.5 Outs"),
    else None.

    Handles:
      "Player Name Over 5.5 Hits Allowed"
      "Player Name Under 15.5 Outs"
      "Player Name Over 3.5 KS"
      "Player Name Under 2.5 Earned Runs"
    Ignores the "(Jerry NN/100)" parenthetical suffix.
    """
    if not lean or not isinstance(lean, str):
        return None
    # Strip trailing parenthetical (Jerry 80/100), (Model 74%), etc.
    txt = re.sub(r'\([^)]*\)\s*$', '', lean).strip()
    m = re.match(r'^(.+?)\s+(Over|Under)\s+(\d+(?:\.\d+)?)\s+(.+)$',
                 txt, re.IGNORECASE)
    if not m:
        return None
    player = m.group(1).strip()
    direction = m.group(2).lower()  # 'over' | 'under'
    line = float(m.group(3))
    stat_raw = m.group(4).strip().lower()
    prop_key = None
    for alias, key in _PROP_STAT_ALIASES:
        if stat_raw == alias or stat_raw.startswith(alias + ' '):
            prop_key = key; break
    if not prop_key:
        return None
    prop_type = f'{prop_key}_{direction}'  # e.g. 'outs_under', 'ha_over'
    return {
        'player_name': player,
        'direction': direction,
        'line': line,
        'prop_type': prop_type,
    }


def _grade_prop_via_pipeline(sport: str, game_id: str, date_str: str,
                             prop: dict) -> str | None:
    """Read the already-graded prop row from the sport's prop-pipeline
    table and return its result. Returns None (caller treats as Pending)
    if the row exists but hasn't been graded yet, or the row is missing.

    Match keys are (game_date, game_id, player_name, prop_type, direction)
    — same tuple `apply_prop_refit` writes the graded `result` to.
    """
    tbl = PROP_TABLE_BY_SPORT.get(sport)
    if not tbl or not game_id or not prop:
        return None
    try:
        r = requests.get(f'{SB}/rest/v1/{tbl}',
                         headers=H_R,
                         params={
                             'game_date': f'eq.{date_str}',
                             'game_id': f'eq.{game_id}',
                             'player_name': f'eq.{prop["player_name"]}',
                             'prop_type': f'eq.{prop["prop_type"]}',
                             'direction': f'eq.{prop["direction"]}',
                             'select': 'result,final_value,prop_line',
                             'limit': '1',
                         }, timeout=15)
        if r.status_code != 200 or not r.json():
            return None
        row = r.json()[0]
        res = (row.get('result') or '').strip()
        # Prop grader writes 'Win'|'Loss'|'Push'|None (Pending)
        if res in ('Win', 'Loss', 'Push'):
            return res
        # Fallback: compute from final_value when the grader hasn't
        # stamped result yet (rare — prop grader runs before POTD grader,
        # but keep the belt-and-suspenders path for late-arriving stats).
        fv = row.get('final_value')
        pl = row.get('prop_line') or prop.get('line')
        if fv is not None and pl is not None:
            try:
                fv = float(fv); pl = float(pl)
                if abs(fv - pl) < 0.001:
                    return 'Push'
                went_over = fv > pl
                if prop['direction'] == 'over':
                    return 'Win' if went_over else 'Loss'
                return 'Loss' if went_over else 'Win'
            except (TypeError, ValueError):
                pass
        return None
    except Exception:
        return None


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
    prop_info = None
    # If we only have leanDisplay text like "Toronto Blue Jays ML",
    # "Over 8.5", "Texas Rangers RL +1.5", or "Jared Jones Under 15.5 Outs",
    # parse it.
    if not call_side and pick_side_raw:
        s = str(pick_side_raw).strip()
        # 2026-09-11: strip any trailing parenthetical suffix ONCE up front
        # ("(Jerry 80/100)" etc.) so every branch below sees clean text.
        s = re.sub(r'\s*\([^)]*\)\s*$', '', s).strip()
        low = s.lower()
        # 1. Player prop pattern ("Player Name Over/Under N.N StatName")
        #    Must be checked BEFORE the totals branch or "Under 15.5" in the
        #    middle of the string never wins vs the totals startswith check.
        prop_info = _parse_prop_from_lean(s)
        if prop_info:
            call_market = 'prop'
            call_side = prop_info['direction'].upper()
            call_line = prop_info['line']
        elif ' ml' in low or low.endswith('ml'):
            call_market = call_market or 'ml'
            # Team side determined via game_id lookup — leave call_side blank
            # and let the grader look up which side won
            call_side = '__PARSE_TEAM__'
            call_side_team = s[:-3].strip() if low.endswith(' ml') else s.replace(' ML', '').strip()
        elif ' rl ' in f' {low} ' or ' spread ' in f' {low} ':
            # 2026-09-11: "Team Name RL +1.5" / "Team Name -1.5 Spread"
            # → market=rl, team + signed line; grader resolves side later.
            call_market = call_market or 'rl'
            m_line = re.search(r'([+-]\s*\d+(?:\.\d+)?)', s)
            if m_line:
                try: call_line = float(m_line.group(1).replace(' ', ''))
                except (TypeError, ValueError): call_line = None
            # Team name is everything before "RL"/"Spread" or the signed line
            call_side = '__PARSE_TEAM__'
            _team = re.split(r'\s+(?:rl|spread)\b|\s+[+-]\d', s, maxsplit=1,
                             flags=re.IGNORECASE)[0].strip()
            call_side_team = _team or None
        elif low.startswith('over'):
            call_market = call_market or 'total'
            call_side = 'OVER'
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
        'prop_info': prop_info,
    }


def grade_potd(date_str: str, dry_run: bool = False) -> str:
    row = _fetch_potd(date_str)
    if not row:
        print(f'  {date_str}: no best_bet row'); return 'no-row'
    data = row.get('data') or {}
    if isinstance(data, str):
        try: data = json.loads(data)
        except (json.JSONDecodeError, TypeError): data = {}
    if data.get('result') and data.get('result') != 'no-pick':
        # 'no-pick' is a parser-failure marker (e.g. an old grade that
        # couldn't decode a prop-type POTD before this file learned the
        # prop pattern). Re-grade those rows instead of treating them as
        # settled — that's how the 9/10 Jared Jones POTD gets its result.
        # 2026-09-09 dual-write reconciliation for already-graded rows:
        # older grades wrote only to jerry_cache. Backfill daily_best_bet_history
        # if it's stale (still 'Pending'). Non-fatal.
        _existing = data.get('result')
        if _existing in ('Win', 'Loss', 'Push', 'Void'):
            try:
                hc = requests.get(f'{SB}/rest/v1/daily_best_bet_history',
                                  headers=H_R,
                                  params={'bet_date': f'eq.{date_str}',
                                          'select': 'result', 'limit': '1'},
                                  timeout=10)
                if hc.status_code == 200 and hc.json():
                    hist_res = (hc.json()[0] or {}).get('result')
                    if hist_res in (None, 'Pending', ''):
                        _resolved_at = data.get('graded_at') or dt.datetime.now(dt.timezone.utc).isoformat()
                        requests.patch(f'{SB}/rest/v1/daily_best_bet_history',
                                       headers=H_W,
                                       params={'bet_date': f'eq.{date_str}'},
                                       json={'result': _existing,
                                             'resolved_at': _resolved_at},
                                       timeout=15)
                        print(f'  {date_str}: already graded ({_existing}) → history backfilled')
                        return 'already'
            except Exception:
                pass
        print(f'  {date_str}: already graded ({data.get("result")})'); return 'already'
    pick = _extract_pick_from_potd(data, date_str)
    if not pick['game_id'] or not pick['call_market']:
        print(f'  {date_str}: cannot extract pick (game_id={pick["game_id"]!r}, '
              f'market={pick["call_market"]!r}) - marking as no-pick')
        result_payload = {'result': 'no-pick', 'graded_at': dt.datetime.now(dt.timezone.utc).isoformat()}
    elif pick['call_market'] == 'prop':
        # 2026-09-11: prop-type POTDs (e.g. "Jared Jones Under 15.5 Outs")
        # grade via the sport's *_pipeline_props table where the prop grader
        # already wrote a `result`. Prior version fell through to the totals
        # branch, mis-parsed the line as the player's first name, and left
        # a `no-pick` row on the Receipts calendar even though the prop was
        # graded. 9/10 Jones Under 15.5 Outs (21 actual = Loss) hit exactly
        # this path.
        pres = _grade_prop_via_pipeline(pick['sport'], pick['game_id'],
                                         date_str, pick['prop_info'])
        if pres is None:
            print(f'  {date_str}: prop grade pending / row missing '
                  f'({pick["prop_info"]["player_name"]} {pick["prop_info"]["prop_type"]})')
            return 'pending'
        print(f'  {date_str}: {pick["sport"]} PROP '
              f'{pick["prop_info"]["player_name"]} {pick["prop_info"]["direction"].upper()} '
              f'{pick["prop_info"]["line"]} {pick["prop_info"]["prop_type"]} -> {pres}')
        result_payload = {'result': pres,
                          'graded_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                          'graded_by': 'grade_potd.py (prop path)'}
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
    # Patch data JSONB in jerry_cache
    updated_data = {**data, **result_payload}
    r = requests.patch(f'{SB}/rest/v1/jerry_cache',
                       headers=H_W,
                       params={'game_id': f'eq.best_bet_{date_str}'},
                       json={'data': updated_data},
                       timeout=15)
    if r.status_code not in (200, 204):
        print(f'  {date_str}: jerry_cache PATCH failed {r.status_code}: {r.text[:200]}')
        return 'patch-failed'

    # 2026-09-09 DUAL-WRITE FIX. Root cause of "Sept 7 not graded on
    # Receipts calendar" bug — the calendar reads daily_best_bet_history
    # (via app/index.tsx:5498) not jerry_cache. Grader wrote to
    # jerry_cache.data.result but daily_best_bet_history.result stayed
    # 'Pending' forever → calendar showed ungraded cell for a day that
    # jerry_cache knew was a Win.
    # Fix: mirror the grade into daily_best_bet_history so the calendar
    # source of truth stays in sync. Non-fatal — jerry_cache patch above
    # is authoritative; the history patch is calendar convenience.
    try:
        result_val = result_payload.get('result')  # 'Win' | 'Loss' | 'Push' | 'Void'
        if result_val:
            hr = requests.patch(f'{SB}/rest/v1/daily_best_bet_history',
                                headers=H_W,
                                params={'bet_date': f'eq.{date_str}'},
                                json={'result': result_val,
                                      'resolved_at': result_payload.get('graded_at')},
                                timeout=15)
            if hr.status_code not in (200, 204):
                print(f'  {date_str}: history mirror failed {hr.status_code}: {hr.text[:120]}')
    except Exception as _e:
        print(f'  {date_str}: history mirror exception {_e}')

    return 'graded'


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
