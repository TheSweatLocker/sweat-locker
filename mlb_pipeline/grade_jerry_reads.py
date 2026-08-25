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
    # UFC uses fight_results — different shape; excluded from POTD anyway
}


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


def grade_rl(side: str, line, hs: int, as_: int, run_line_result: str | None) -> str:
    # Prefer server-computed run_line_result when available
    if run_line_result:
        rl = run_line_result.lower()
        if side == 'HOME' and rl == 'home': return 'Win'
        if side == 'AWAY' and rl == 'away': return 'Win'
        return 'Loss'
    if line is None: return None
    # Standard: run line -1.5 means picked side must win by 2+
    margin_picked = (hs - as_) if side == 'HOME' else (as_ - hs)
    return 'Win' if margin_picked > abs(line) else 'Loss'


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
        return grade_rl(side, line, hs, as_, res.get('run_line_result'))
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

    # Pull results
    gid_list = list({r['game_id'] for r in reads if r.get('game_id')})
    gid_in = ','.join(f'"{g}"' for g in gid_list)
    rr = requests.get(f'{SB}/rest/v1/{results_table}',
                      headers=H_READ,
                      params={'game_id': f'in.({gid_in})',
                              'select': 'game_id,home_score,away_score,run_line_result,total_result'},
                      timeout=15)
    results_by_gid = {row['game_id']: row for row in (rr.json() if rr.status_code == 200 else [])}

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
    for read in reads:
        result = grade_one(read, results_by_gid,
                           prop_lookup=prop_lookup,
                           postponed_gids=postponed_gids)
        if not result: continue
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
    print(f'  [{sport}] graded {graded}/{len(reads)} jerry_reads')
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
    p.add_argument('--backfill-days', type=int, default=3,
                   help='Days prior to yesterday to also re-grade. Default 3.')
    args = p.parse_args()
    main(game_date=args.date, sport=args.sport, dry_run=args.dry_run,
         backfill_days=args.backfill_days)
