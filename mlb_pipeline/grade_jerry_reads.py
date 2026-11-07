"""Post-game grader for jerry_reads (game-level Jerry synthesis).

Runs nightly (or after each game finishes). For every jerry_reads row
where result is null AND the game has resolved, computes W/L/P/void
based on call_market + call_side + call_line vs mlb_game_results.

Sport-universal: dispatches per-sport via RESULTS_TABLE registry
matching the betResolver pattern.

Usage:
    python grade_jerry_reads.py [--date YYYY-MM-DD] [--sport MLB] [--dry-run]
"""
import argparse, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

RESULTS_TABLE = {
    'MLB':   'mlb_game_results',
    'NBA':   'nba_game_results',
    'NFL':   'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'NCAAB': 'ncaab_game_results',
    # 2026-09-08 added: NHL wired for Oct 8 season start. Resolver is
    # `nhl_resolve_results.py` (different naming from other sports) —
    # writes to nhl_game_results with cols home_score/away_score/
    # spread_result/total_result. Column dispatch below.
    'NHL':   'nhl_game_results',
    # UFC uses fight_results — different shape; excluded from POTD anyway
}

# 2026-09-07 sport-specific column dispatch. MLB uses `run_line_result` for
# the ±1.5 run line market; other sports use `spread_result` for their
# point spreads (no ±1.5 concept). Prior grader hardcoded run_line_result
# in the SELECT which returned 400 on NFL/NCAAF/NCAAB/NBA — results dict
# came back empty → zero picks graded, silently. Caught 9/7 morning
# audit: 189 NCAAF picks over 3 days, 0 graded.
SPREAD_COL_BY_SPORT = {
    'MLB':   'run_line_result',
    'NFL':   'spread_result',
    'NCAAF': 'spread_result',
    'NCAAB': 'spread_result',
    'NBA':   'spread_result',
    # 2026-09-08 NHL: uses spread_result column on nhl_game_results.
    # Puckline is typically ±1.5 like a run line but NHL naming
    # convention is 'spread_result' not 'puckline_result'. Grader's
    # grade_rl() handles both value flavors (home/home_covered).
    'NHL':   'spread_result',
}

# 2026-10-03 · the LINE the server's cover flag was computed at, per sport.
# grade_rl needs this to tell "the book's number" from "the number we
# published" — see the note in grade_rl. NHL has no close_spread at all
# (it stores close_puckline), and selecting a missing column 400s the whole
# results fetch, which is the exact failure SPREAD_COL_BY_SPORT above was
# created to fix on 9/7 (189 NCAAF picks, 0 graded, silently). So this is a
# registry, not a constant.
SPREAD_LINE_COL_BY_SPORT = {
    'MLB':   'close_spread',
    'NFL':   'close_spread',
    'NCAAF': 'close_spread',
    'NCAAB': 'close_spread',
    'NBA':   'close_spread',
    'NHL':   'close_puckline',
}

# 🚨 NFL STORES THE AWAY PERSPECTIVE AND EVERY OTHER SPORT STORES THE HOME ONE
# (project_close_spread_sign_bug_914). grade_rl needs the sign to decide
# whether our pick is laying or taking points, so the sign has to mean ONE
# thing by the time it gets there. Re-verified 2026-10-03 against actual
# home margins, which is the only arbiter that cannot itself be mis-signed:
#
#   sport   n      mean home_margin   mean stored line   same sign?
#   NFL     7325   +2.33              +2.25              YES -> away line
#   NCAAF   6380   +6.96              -6.47              no  -> home line
#   NBA     2390   +1.61              -1.91              no  -> home line
#   NCAAB   5027   +5.32              -5.27              no  -> home line
#   NHL     2540   +0.21              -0.41              no  -> home line
#   MLB     2116   +0.01              -0.07              no  -> home line
#
# So flip NFL on the way in and let grade_rl assume home perspective
# throughout. Normalising at the single alias point beats teaching every
# consumer the exception -- that exception is what cost 2x-inflated NCAAF
# edges on 9/16 and a mis-signed grading path on 9/14.
SPREAD_LINE_IS_AWAY_PERSPECTIVE = {'NFL'}


def _home_persp_line(sport, raw):
    """Stored spread line normalised to HOME perspective (neg = home fav)."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return -v if sport in SPREAD_LINE_IS_AWAY_PERSPECTIVE else v


def yesterday_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=28)).strftime('%Y-%m-%d')


def grade_ml(side: str, hs: int, as_: int) -> str:
    if hs == as_: return 'Push'
    won = (side == 'HOME' and hs > as_) or (side == 'AWAY' and as_ > hs)
    return 'Win' if won else 'Loss'


def grade_total(side: str, line, hs: int, as_: int) -> str:
    if line is None: return None
    total = hs + as_
    if total == line: return 'Push'
    if side == 'OVER':  return 'Win' if total > line else 'Loss'
    if side == 'UNDER': return 'Win' if total < line else 'Loss'
    return None


def grade_rl(side: str, line, hs: int, as_: int, run_line_result: str | None,
             results_line=None) -> str:
    """Grade run-line (MLB ±1.5) or spread (NFL/NCAAF/etc).

    Server-computed *_result values use varying labels across sports:
      MLB run_line_result: 'home' / 'away' / 'push'
      NFL/NCAAF/NCAAB/NBA spread_result: 'home_covered' / 'away_covered' / 'push'

    Both flavors accepted here so the same function grades both markets.

    2026-10-03 · GRADE AT THE LINE WE PUBLISHED, NOT THE BOOK'S CLOSE.
    ------------------------------------------------------------------
    `run_line_result` / `spread_result` is a cover flag the results table
    computed against ITS OWN stored line. This function used to return on
    that flag unconditionally, so `line` -- the number we actually put in
    front of users -- was never read. When the two disagree the pick gets
    graded at a line nobody could have bet.

    Replayed old-logic vs new-logic over every graded rl read (the only
    comparison that isolates this change -- comparing against the STORED
    result conflates it with pre-existing grader drift, which is how I
    first mis-read this as 10 MLB flips). Exactly 2 grades move, both
    NCAAF, and they cancel:

      Nebraska -24.5  won by 27  book line -27.5  Loss -> WIN
      Illinois -39.5  won by 38  book line -35.5  Win  -> LOSS

    MLB/NFL/NBA/NHL: zero change. MLB is structurally immune because the
    run line is always ±1.5 on both sides, so the magnitudes always match.
    22 of 182 NCAAF reads carried a line differing from the book's; most
    land the same side of the result anyway.

    So: trust the server flag only when it was computed at our magnitude;
    otherwise grade from the margin.

    The old fallback was also wrong on its own terms -- `margin_picked >
    abs(line)` grades a DOG pick as if it were laying the points, so
    "Buffalo +13.5" needed Buffalo to win by 14. It was dormant because the
    cover flag is nearly always present; widening the fallback would have
    activated it.

    Direction comes from the MARKET (`results_line`, negative = home
    favoured, verified 52/0 against the moneyline), magnitude from our own
    `line`. That is deliberate: `call_line`'s SIGN is unreliable -- it
    contradicts its own label on 16 of 299 rl reads, 12 of them dated
    09-12 from the retired `ncaaf_game_read_v2_2026-08-25` generator (e.g.
    call_line +23.5 on a row labelled "Oregon -23.5"). The magnitude is
    right on 299 of 303, so use the half that is trustworthy.
    """
    margin_picked = (hs - as_) if side == 'HOME' else (as_ - hs)

    lines_match = (
        line is None or results_line is None
        or abs(abs(float(results_line)) - abs(float(line))) <= 0.01
    )
    if run_line_result and lines_match:
        rl = str(run_line_result).lower()
        if 'push' in rl: return 'Push'
        home_won = ('home' in rl and 'covered' in rl) or rl == 'home'
        away_won = ('away' in rl and 'covered' in rl) or rl == 'away'
        if side == 'HOME' and home_won: return 'Win'
        if side == 'AWAY' and away_won: return 'Win'
        return 'Loss'
    if line is None: return None

    if results_line is not None:
        # Who is favoured is a fact about the market, not about our sign.
        home_is_fav = float(results_line) < 0
        laying = (side == 'HOME') == home_is_fav
    else:
        laying = float(line) < 0
    thresh = abs(float(line)) if laying else -abs(float(line))
    if abs(margin_picked - thresh) < 1e-9: return 'Push'
    return 'Win' if margin_picked > thresh else 'Loss'


def _normalize_market(raw: str) -> str:
    """Strip LLM markdown junk from call_market before matching.

    2026-08-09 audit found rows like:
      call_market = '** ml  \\n**side:** home  \\n**line:** null  ...'
    Jerry occasionally wrote raw markdown into the enum column. Take the
    first alphabetic token, lowercase. Empty → ''.
    """
    if not raw:
        return ''
    s = str(raw).strip()
    # Strip leading markdown markers, colons, asterisks
    s = s.lstrip('*').lstrip(':').strip()
    # Take first whitespace-delimited token
    tok = s.split()[0] if s.split() else ''
    tok = tok.lower().strip('*:,.\n\r\t')
    return tok


def _normalize_side(raw) -> str:
    """Same defensive strip for call_side (HOME/AWAY/OVER/UNDER)."""
    if raw is None: return ''
    s = str(raw).strip().lstrip('*').lstrip(':').strip()
    tok = s.split()[0] if s.split() else ''
    return tok.upper().strip('*:,.\n\r\t')


def grade_one(read: dict, results_by_gid: dict, prop_lookup: dict | None = None,
              postponed_gids: set | None = None) -> str | None:
    """Return 'Win' | 'Loss' | 'Push' | 'Void' | 'NO_ACTION' | 'TRACKED_ELSEWHERE' | None (pending)."""
    gid = read.get('game_id')
    market = _normalize_market(read.get('call_market'))
    if market == 'pass':
        return 'NO_ACTION'
    # Postponement → mark Void so it stops counting as ungraded
    if postponed_gids and gid in postponed_gids:
        return 'Void'
    res = results_by_gid.get(gid)
    if not res or res.get('home_score') is None:
        # No result row → check postponement inference: game_date >4 days
        # old with no result = postponed/cancelled. Void it.
        return None
    hs, as_ = int(res['home_score']), int(res['away_score'])
    side = _normalize_side(read.get('call_side'))
    line = read.get('call_line')
    if market == 'ml':
        return grade_ml(side, hs, as_)
    if market == 'total':
        return grade_total(side, line, hs, as_)
    if market == 'rl':
        # `spread_line` is aliased in by run_for_sport from the sport's own
        # line column (see SPREAD_LINE_COL_BY_SPORT) so grade_rl can tell
        # our published number from the book's close.
        return grade_rl(side, line, hs, as_, res.get('run_line_result'),
                        results_line=res.get('spread_line'))
    if market == 'prop':
        # Jerry occasionally calls a prop inside a game read (rare).
        # If prop_lookup matches the same game_id, inherit its result.
        # Otherwise mark TRACKED_ELSEWHERE so it exits the ungraded queue.
        if prop_lookup:
            match = prop_lookup.get(gid)
            if match and match.get('result'):
                return match['result']
        return 'TRACKED_ELSEWHERE'
    return None


def run_for_sport(sport: str, game_date: str, dry_run: bool = False) -> int:
    results_table = RESULTS_TABLE.get(sport)
    if not results_table:
        print(f'  [{sport}] no results table registered — skip')
        return 0

    # Pull unresolved jerry_reads for this sport/date
    r = requests.get(f'{SB}/rest/v1/jerry_reads',
                     headers=H_READ,
                     params={'sport': f'eq.{sport}', 'game_date': f'eq.{game_date}',
                             'result': 'is.null',
                             'select': 'id,game_id,call_market,call_side,call_line'},
                     timeout=15)
    reads = r.json() if r.status_code == 200 else []
    if not reads:
        print(f'  [{sport}] no ungraded jerry_reads on {game_date}')
        return 0

    # Pull results — sport-specific SELECT so NFL/NCAAF/etc queries don't
    # 400 on the MLB-only `run_line_result` column. Grader normalizes the
    # value into an rl-result field the grade_rl call downstream reads.
    gid_list = list({r['game_id'] for r in reads if r.get('game_id')})
    gid_in = ','.join(f'"{g}"' for g in gid_list)
    spread_col = SPREAD_COL_BY_SPORT.get(sport, 'spread_result')
    line_col = SPREAD_LINE_COL_BY_SPORT.get(sport, 'close_spread')

    # 2026-09-11 game_id mismatch fix for NFL/NCAAF. jerry_reads uses the
    # Odds API event hash while nfl_game_results / ncaaf_game_results use
    # a schedule-format id (20260910_NE_SEA / ncaaf_20261212_Army_Navy).
    # in.() lookup returned zero matches → results_by_gid empty → every
    # NFL/NCAAF jerry_read stayed ungraded since launch. Same class of
    # bug that just landed in resolve_nfl_results (249d71cb).
    # Fix: for these sports, resolve teams via sport_game_context (which
    # DOES share game_id with jerry_reads), then match results by
    # (away, home, week_bucket) tuple with ET/UTC date-window tolerance.
    # 249d71cb applied the sibling fix in resolve_nfl_results.py.
    # == 2026-11-07 - NCAAB READS COULD NOT REACH THEIR RESULT ROW =======
    # Same class as the 2026-09-11 NFL/NCAAF fix below, third location
    # (price_picks was the second). `jerry_reads.game_id` and
    # `ncaab_game_context.game_id` are both the Odds-API 32-char hex;
    # `ncaab_game_results.game_id` is the composed slug
    # `ncaab_20261106_UT_Arlington_Portland`. Measured 2026-11-07: 0 of 60
    # recent read ids appear in the results table, and grading sits at 0 of
    # 129 on the settled dates 11-03..11-06 while those same dates are
    # 26/28, 21/21, 58/58 and 67/68 SCORED in the results table. The scores
    # were there the whole time; only the join was missing.
    #
    # DO NOT rebuild the slug. Measured over those 129 games:
    #     raw ctx names        -> slug    20 of 129
    #     resolved names       -> slug    45 of 129
    #     (date, resolved away, resolved home) vs the results table's OWN
    #     team columns                   128 of 129
    # ctx carries mascots ('UT-Arlington Mavericks', 'Oklahoma St Cowboys')
    # and the slug does not, so composing a key means re-deriving a naming
    # convention -- exactly what price_picks.ncaab_key warns against and
    # what [[feedback_prefix_strip_can_name_another_school]] cost. Join on
    # resolved names and let the slug stay opaque.
    #
    # The 129th game is why this does NOT fall back to an
    # orientation-insensitive match: ctx has `Portland @ UT-Arlington`,
    # results has `UT Arlington @ Portland`. One of them has the sides
    # backwards, and every spread/total result on the row is read from the
    # HOME perspective, so guessing would grade the wrong team -- a WRONG
    # grade, which is worse than an ungraded row
    # ([[feedback_grade_each_row_not_the_date]]). An inverted hit is
    # reported and skipped.
    if sport == 'NCAAB' and gid_list:
        try:
            import ncaab_team_resolver as _ncaab_res
            _ncaab_res.load_aliases()
            _rv = _ncaab_res.resolve
        except Exception as _e:
            print(f'  [NCAAB] resolver unavailable ({_e}) - cannot join reads '
                  f'to results; this is a WIRING ERROR, not an empty slate')
            _rv = None
        results_by_gid = {}
        if _rv is not None:
            # in.(...) rides in the URL and truncates past ~2KB
            # ([[feedback_in_filter_url_truncates_silently]]) - chunk at 20.
            ctx_by_gid = {}
            for _i in range(0, len(gid_list), 20):
                _q = ','.join(f'"{g}"' for g in gid_list[_i:_i + 20])
                _cr = requests.get(f'{SB}/rest/v1/ncaab_game_context',
                                   headers=H_READ,
                                   params={'game_id': f'in.({_q})',
                                           'select': 'game_id,away_team,'
                                                     'home_team,game_date'},
                                   timeout=20)
                if _cr.status_code != 200:
                    print(f'  [NCAAB] ctx chunk failed {_cr.status_code}: '
                          f'{_cr.text[:120]} - ABORT rather than grade a '
                          f'partial slate')
                    ctx_by_gid = {}
                    break
                for _row in (_cr.json() or []):
                    if isinstance(_row, dict) and _row.get('game_id'):
                        ctx_by_gid[_row['game_id']] = _row
            from datetime import date as _d, timedelta as _td
            _dates = [r.get('game_date') for r in ctx_by_gid.values()
                      if r.get('game_date')]
            if _dates:
                _lo = (_d.fromisoformat(min(_dates)) - _td(days=2)).isoformat()
                _hi = (_d.fromisoformat(max(_dates)) + _td(days=2)).isoformat()
                _rr = requests.get(f'{SB}/rest/v1/{results_table}',
                                   headers=H_READ,
                                   params={'game_date': f'gte.{_lo}',
                                           'and': f'(game_date.lte.{_hi})',
                                           'select': f'game_id,game_date,'
                                                     f'away_team,home_team,'
                                                     f'home_score,away_score,'
                                                     f'{spread_col},{line_col},'
                                                     f'total_result',
                                           'limit': '2000'},
                                   timeout=30)
                _rows = _rr.json() if _rr.status_code == 200 else []
                if _rr.status_code != 200:
                    print(f'  [NCAAB] results query failed '
                          f'({_rr.status_code}): {str(_rr.text)[:150]}')
                _fwd, _inv = {}, {}
                for _row in _rows:
                    if not isinstance(_row, dict):
                        continue
                    _row['run_line_result'] = _row.get(spread_col)
                    _row['spread_line'] = _home_persp_line(sport,
                                                           _row.get(line_col))
                    _ra = _rv(_row.get('away_team'))
                    _rh = _rv(_row.get('home_team'))
                    if not (_ra and _rh):
                        continue
                    _dk = str(_row.get('game_date'))[:10]
                    _k = (_dk, _ra, _rh)
                    _prev = _fwd.get(_k)
                    if _prev is None or (_prev.get('home_score') is None
                                         and _row.get('home_score') is not None):
                        _fwd[_k] = _row
                    _inv.setdefault((_dk, _rh, _ra), _row)
                _flipped = 0
                _unmatched = 0
                for _gid, _c in ctx_by_gid.items():
                    _dk = str(_c.get('game_date'))[:10]
                    _ca = _rv(_c.get('away_team'))
                    _ch = _rv(_c.get('home_team'))
                    _hit = _fwd.get((_dk, _ca, _ch))
                    if _hit is not None:
                        results_by_gid[_gid] = _hit
                        continue
                    if _inv.get((_dk, _ca, _ch)) is not None:
                        _flipped += 1
                        print(f'  [NCAAB] ORIENTATION CONFLICT {_dk} '
                              f'ctx says away={_c.get("away_team")!r} '
                              f'home={_c.get("home_team")!r}, results has '
                              f'them reversed - NOT graded (a '
                              f'home-perspective result on a reversed row '
                              f'grades the wrong team)')
                        continue
                    _unmatched += 1
                print(f'  [NCAAB] joined {len(results_by_gid)} of '
                      f'{len(ctx_by_gid)} ctx games to results'
                      + (f' - {_flipped} orientation conflict(s)'
                         if _flipped else '')
                      + (f' - {_unmatched} unmatched' if _unmatched else ''))
        raw_rows = list(results_by_gid.values())
    elif sport in ('NFL', 'NCAAF') and gid_list:
        ctx_table = 'nfl_game_context' if sport == 'NFL' else 'ncaaf_game_context'
        ctx_r = requests.get(f'{SB}/rest/v1/{ctx_table}', headers=H_READ,
                             params={'game_id': f'in.({gid_in})',
                                     'select': 'game_id,away_team,home_team,game_date'},
                             timeout=15)
        ctx_by_gid = {row['game_id']: row for row in (ctx_r.json() or [])
                      if isinstance(row, dict) and row.get('game_id')}
        # Build a wide date-window fetch on results table
        from datetime import date as _d, timedelta as _td
        dates = [row.get('game_date') for row in ctx_by_gid.values() if row.get('game_date')]
        results_by_gid = {}
        if dates:
            dmin = min(dates); dmax = max(dates)
            lo = (_d.fromisoformat(dmin) - _td(days=3)).isoformat()
            hi = (_d.fromisoformat(dmax) + _td(days=3)).isoformat()
            rr = requests.get(f'{SB}/rest/v1/{results_table}', headers=H_READ,
                              params={'game_date': f'gte.{lo}',
                                      'and': f'(game_date.lte.{hi})',
                                      'select': f'game_id,game_date,away_team,home_team,'
                                                f'home_score,away_score,{spread_col},{line_col},total_result'},
                              timeout=25)
            result_rows = rr.json() if rr.status_code == 200 else []

            def _week_bucket(dstr):
                d = _d.fromisoformat(dstr)
                return (d - _td(days=(d.weekday() - 3 + 7) % 7)).isoformat()

            by_tuple = {}
            for row in result_rows:
                if not isinstance(row, dict): continue
                row['run_line_result'] = row.get(spread_col)
                row['spread_line'] = _home_persp_line(sport, row.get(line_col))
                key = (row.get('away_team'), row.get('home_team'),
                       _week_bucket(row.get('game_date') or dmin))
                existing = by_tuple.get(key)
                if existing is None or (existing.get('home_score') is None and row.get('home_score') is not None):
                    by_tuple[key] = row
            # Re-key by the jerry_read game_id (Odds hash) so downstream code
            # keeps using results_by_gid[read['game_id']] unchanged.
            for gid, ctx_row in ctx_by_gid.items():
                key = (ctx_row.get('away_team'), ctx_row.get('home_team'),
                       _week_bucket(ctx_row.get('game_date') or dmin))
                hit = by_tuple.get(key)
                if hit is not None:
                    results_by_gid[gid] = hit
        raw_rows = list(results_by_gid.values())
        if rr.status_code != 200 if 'rr' in locals() else False:
            print(f'  [{sport}] results query failed')
    else:
        rr = requests.get(f'{SB}/rest/v1/{results_table}',
                          headers=H_READ,
                          params={'game_id': f'in.({gid_in})',
                                  'select': f'game_id,home_score,away_score,{spread_col},{line_col},total_result'},
                          timeout=15)
        raw_rows = rr.json() if rr.status_code == 200 else []
        if rr.status_code != 200:
            print(f'  [{sport}] results query failed ({rr.status_code}): {str(rr.text)[:150]}')
        # Normalize: alias the sport's spread column into `run_line_result` key
        # so downstream grade_rl works uniformly regardless of sport.
        results_by_gid = {}
        for row in raw_rows:
            if isinstance(row, dict):
                row['run_line_result'] = row.get(spread_col)
                row['spread_line'] = _home_persp_line(sport, row.get(line_col))
                results_by_gid[row['game_id']] = row

    # Postponement inference: game_date more than 4 days old + no result row
    # or result row with null scores = postponed/cancelled. Void them so they
    # exit the ungraded queue. Prevents the 8/1 LAA @ MIL rain-out pattern
    # (still surfaced as ungraded 8 days later).
    postponed_gids = set()
    try:
        from datetime import date, timedelta
        d0 = date.fromisoformat(game_date)
        if (date.today() - d0).days > 4:
            for gid in gid_list:
                res = results_by_gid.get(gid)
                if not res or res.get('home_score') is None:
                    postponed_gids.add(gid)
            if postponed_gids:
                print(f'  [{sport}] {len(postponed_gids)} postponed/void (>4d old, no result)')
    except Exception:
        pass

    # Prop-in-game-read lookup: for market='prop' calls, look up the same
    # game in mlb_pipeline_props to inherit its resolved result. Only MLB
    # has pipeline_props today.
    prop_lookup = {}
    if sport == 'MLB':
        pr = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
                          headers=H_READ,
                          params={'game_date': f'eq.{game_date}',
                                  'game_id': f'in.({gid_in})',
                                  'select': 'game_id,result'},
                          timeout=15)
        for row in (pr.json() if pr.status_code == 200 else []):
            if row.get('game_id') and row.get('result'):
                # Keep first non-null per game_id
                prop_lookup.setdefault(row['game_id'], row)

    graded = 0
    voided = 0
    for read in reads:
        result = grade_one(read, results_by_gid,
                           prop_lookup=prop_lookup,
                           postponed_gids=postponed_gids)
        if not result:
            # 2026-09-11 VOID PATH — was `continue`, leaving rows ungraded
            # forever. NCAAF audit found 45/125 jerry_reads over past 14d
            # stuck as ungraded because call_market/call_side/call_line
            # were all None (LLM wrote prose but no pick — a de-facto Pass).
            # grade_one() returned None → row stayed result=null → next
            # grader run picked it up again → same None. Result: bloated
            # ungraded queue + no signal to distinguish "grader hasn't run
            # yet" from "no pick to grade." Fix: when the row has no pick
            # data AND the game is settled (result row exists w/ score) OR
            # is >3d old, patch result='Void'. Void rows exit the queue
            # but aren't counted in W-L records.
            has_pick = bool(read.get('call_market')
                            and str(read.get('call_market')).lower() != 'pass')
            res_row = results_by_gid.get(read.get('game_id')) or {}
            game_settled = res_row.get('home_score') is not None
            try:
                from datetime import date as _d
                gd_age = (_d.today() - _d.fromisoformat(read.get('game_date') or '2099-01-01')).days
            except Exception:
                gd_age = 0
            if not has_pick and (game_settled or gd_age > 3):
                if dry_run:
                    voided += 1; continue
                pr = requests.patch(f'{SB}/rest/v1/jerry_reads?id=eq.{read["id"]}',
                                    headers=H_WRITE,
                                    json={'result': 'Void',
                                          'resolved_at': datetime.now(timezone.utc).isoformat()},
                                    timeout=15)
                if pr.status_code in (200, 204): voided += 1
            continue
        actual = {'home_score': results_by_gid.get(read['game_id'], {}).get('home_score'),
                  'away_score': results_by_gid.get(read['game_id'], {}).get('away_score')}
        if dry_run:
            print(f'  [DRY] {sport} id={read["id"]} {read["call_market"]}/{read["call_side"]} → {result}')
            graded += 1
            continue
        pr = requests.patch(f'{SB}/rest/v1/jerry_reads?id=eq.{read["id"]}',
                            headers=H_WRITE,
                            json={'result': result,
                                  'actual_outcome': actual,
                                  'resolved_at': datetime.now(timezone.utc).isoformat()},
                            timeout=15)
        if pr.status_code in (200, 204):
            graded += 1
        else:
            print(f'  ⚠ patch id={read["id"]}: {pr.status_code} {pr.text[:120]}')
    print(f'  [{sport}] graded {graded}/{len(reads)} jerry_reads'
          + (f' · voided {voided} (no-pick rows)' if voided else ''))
    return graded


def main(game_date: str | None = None, sport: str | None = None,
         dry_run: bool = False, backfill_days: int = 3):
    """Grade jerry_reads. Default runs yesterday + backfill_days prior to
    catch late-arriving results (games delayed past midnight ET, rows
    generated after cron, grader schedule misses).

    Coverage audit 2026-08-06 found stragglers surviving the 1-day-only
    grader. Backfill window closes that gap.
    """
    if game_date:
        dates = [game_date]
    else:
        base = yesterday_et()
        from datetime import date, timedelta
        base_d = date.fromisoformat(base)
        dates = [(base_d - timedelta(days=i)).isoformat() for i in range(backfill_days + 1)]

    sports = [sport] if sport else list(RESULTS_TABLE.keys())
    total = 0
    for gd in dates:
        print(f'=== grade_jerry_reads · {gd}{" · dry-run" if dry_run else ""} ===')
        for s in sports:
            total += run_for_sport(s, gd, dry_run=dry_run)
    print(f'\n=== graded {total} jerry_reads total across {len(dates)} date(s) ===')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD; defaults yesterday + backfill window')
    p.add_argument('--sport', help='MLB/NBA/NFL/NCAAF/NCAAB (default: all)')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--backfill-days', type=int, default=7,
                   help='Days prior to yesterday to also re-grade. Default 7. '
                        '(2026-09-11: bumped 3→7 so weekly NFL/NCAAF slates + '
                        'late-arriving results get swept — 9/5 NCAAF stragglers '
                        'sat un-graded for 6 days outside the old 3-day window.)')
    args = p.parse_args()
    main(game_date=args.date, sport=args.sport, dry_run=args.dry_run,
         backfill_days=args.backfill_days)
