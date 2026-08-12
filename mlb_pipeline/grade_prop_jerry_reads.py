"""Post-game grader for prop_jerry_reads.

Trivial — inherits result from the matching mlb_pipeline_props row
(which the prop pipeline already grades). No new grading logic
needed since prop_jerry_reads shares the natural key with the props
table.

For BACK verdicts: result = Win if prop hit, Loss if missed.
For FADE verdicts: result = Win if prop MISSED (fade cashed),
                             Loss if prop hit (fade failed).
For PASS verdicts: result = NO_ACTION (Jerry didn't bet).

Sport-universal via PROPS_TABLE registry (MLB only for now; NBA/NFL
when their prop pipelines ship).

Usage:
    python grade_prop_jerry_reads.py [--date YYYY-MM-DD] [--dry-run]
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

PROPS_TABLE = {
    'MLB': 'mlb_pipeline_props',
    'NFL': 'nfl_pipeline_props',      # enabled 2026-08-03 Sprint 2
    # 'NBA': 'nba_pipeline_props',
}

# 2026-08-12: MLB API fallback for UNGRADEABLE picks.
# Prior grader relied entirely on props table lookup. Yesterday audit found
# 75% of BACK/FADE picks came back UNGRADEABLE because Jerry emits takes on
# prop_types that aren't in the props table (er_under for Ober, outs_over
# for Anderson — sportsbook doesn't offer these markets for every pitcher).
# Fetch the pitcher's actual game stat from MLB Stats API and grade directly.
MLB_SEARCH = 'https://statsapi.mlb.com/api/v1/people/search'
MLB_GAMELOG = 'https://statsapi.mlb.com/api/v1/people/{pid}/stats'
_PID_CACHE_DIR = Path(__file__).parent / '.pitcher_cache'
_PID_CACHE_DIR.mkdir(exist_ok=True)

# JR prop_type → (MLB API pitching stat field, aggregation)
_MLB_STAT_MAP = {
    'ks_over': 'strikeOuts', 'ks_under': 'strikeOuts',
    'bb_over': 'baseOnBalls', 'bb_under': 'baseOnBalls',
    'ha_over': 'hits', 'ha_under': 'hits',
    'er_over': 'earnedRuns', 'er_under': 'earnedRuns',
    'outs_over': 'inningsPitched', 'outs_under': 'inningsPitched',
    # hits_over / hits_under are BATTER props — handled separately
}


def _lookup_pid(name: str, is_pitcher: bool = True) -> int | None:
    """Cached MLB API player-ID lookup. Shared with pitcher_trend_gate."""
    import json as _json
    cache = _PID_CACHE_DIR / 'name_pid_index.json'
    idx = {}
    if cache.exists():
        try: idx = _json.loads(cache.read_text())
        except Exception: pass
    if name in idx: return idx[name]
    try:
        r = requests.get(MLB_SEARCH, params={'names': name, 'sportId': 1, 'active': True}, timeout=8)
        if r.status_code != 200: return None
        ppl = r.json().get('people', [])
        if is_pitcher:
            filtered = [p for p in ppl if (p.get('primaryPosition') or {}).get('abbreviation') == 'P']
        else:
            filtered = [p for p in ppl if (p.get('primaryPosition') or {}).get('abbreviation') != 'P']
        if not filtered: filtered = ppl  # fallback
        if not filtered: return None
        pid = filtered[0]['id']
        idx[name] = pid
        try: cache.write_text(_json.dumps(idx))
        except Exception: pass
        return pid
    except Exception:
        return None


def _fetch_stat_for_date(pid: int, stat_field: str, game_date: str,
                          group: str = 'pitching') -> float | None:
    """Fetch actual stat value for a specific game date. Returns None if
    the player didn't play or MLB API is unreachable. Uses gameLog for
    the current season so late-scratched pitchers naturally return None.

    For inningsPitched, MLB returns as string "5.2" meaning 5 innings + 2
    outs = 17 outs. We convert to integer outs for outs_over/under grading.
    """
    try:
        season = game_date[:4]
        r = requests.get(MLB_GAMELOG.format(pid=pid),
            params={'stats': 'gameLog', 'group': group,
                    'season': season, 'sportId': 1}, timeout=10)
        if r.status_code != 200: return None
        splits = r.json().get('stats', [{}])[0].get('splits', [])
        # Find the split matching game_date
        target_str = game_date  # YYYY-MM-DD
        match = None
        for s in splits:
            if s.get('date') == target_str or (s.get('date') or '').startswith(target_str):
                match = s; break
        if not match: return None
        raw = (match.get('stat') or {}).get(stat_field)
        if raw is None: return None
        # inningsPitched string → outs (integer)
        if stat_field == 'inningsPitched':
            try:
                s_ip = str(raw)
                if '.' in s_ip:
                    whole, frac = s_ip.split('.')
                    outs = int(whole) * 3 + int(frac)
                else:
                    outs = int(float(s_ip)) * 3
                return float(outs)
            except (TypeError, ValueError): return None
        try: return float(raw)
        except (TypeError, ValueError): return None
    except Exception:
        return None


def _grade_from_mlb_api(read: dict, gd: str) -> tuple[str, float | None] | None:
    """Fallback: grade this prop_jerry_read against real MLB API stats.
    Returns (result, final_value) or None if ungradeable via API too.
    Result is the raw BACK-side outcome; caller applies FADE flip.

    Handles pitcher props today. Batter hits_over/under handled separately
    (uses 'hitting' stat group instead of 'pitching').
    """
    pt = read.get('prop_type') or ''
    direction = (read.get('direction') or '').lower()
    line = read.get('prop_line')
    if line is None: return None
    try: line = float(line)
    except (TypeError, ValueError): return None

    # Pitcher props via MLB API
    stat_field = _MLB_STAT_MAP.get(pt)
    if stat_field:
        pid = _lookup_pid(read.get('player_name', ''), is_pitcher=True)
        if not pid: return None
        actual = _fetch_stat_for_date(pid, stat_field, gd, group='pitching')
        if actual is None: return None
        # For outs, line is in outs (14.5 = 14.5 outs = 4.5 IP); actual is outs
        if actual == line: result = 'Push'
        elif (direction == 'over' and actual > line) or (direction == 'under' and actual < line):
            result = 'Win'
        else:
            result = 'Loss'
        return (result, actual)

    # Batter hits props
    if pt in ('hits_over', 'hits_under'):
        pid = _lookup_pid(read.get('player_name', ''), is_pitcher=False)
        if not pid: return None
        actual = _fetch_stat_for_date(pid, 'hits', gd, group='hitting')
        if actual is None: return None
        if actual == line: result = 'Push'
        elif (direction == 'over' and actual > line) or (direction == 'under' and actual < line):
            result = 'Win'
        else:
            result = 'Loss'
        return (result, actual)

    return None


def yesterday_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=28)).strftime('%Y-%m-%d')


def flip_for_fade(prop_result: str, verdict: str) -> str:
    """FADE cashes when the underlying prop misses. Flip W↔L."""
    verdict = (verdict or '').upper()
    if verdict != 'FADE':
        return prop_result   # BACK: inherit as-is
    if prop_result == 'Win':  return 'Loss'
    if prop_result == 'Loss': return 'Win'
    return prop_result  # Push/Void pass through


def run_for_sport(sport: str, gd: str, dry_run: bool = False) -> int:
    props_table = PROPS_TABLE.get(sport)
    if not props_table:
        print(f'  [{sport}] no props table registered — skip')
        return 0

    r = requests.get(f'{SB}/rest/v1/prop_jerry_reads',
                     headers=H_READ,
                     params={'sport': f'eq.{sport}', 'game_date': f'eq.{gd}',
                             'result': 'is.null',
                             # 2026-08-12: include prop_line so MLB API fallback
                             # can compare actual stat vs line for grading.
                             'select': 'id,game_id,player_name,prop_type,prop_line,direction,call_verdict'},
                     timeout=15)
    reads = r.json() if r.status_code == 200 else []
    if not reads:
        print(f'  [{sport}] no ungraded prop_jerry_reads on {gd}')
        return 0

    graded = 0
    for read in reads:
        verdict = (read.get('call_verdict') or '').upper()
        if verdict == 'PASS':
            if dry_run:
                print(f'  [DRY] id={read["id"]} PASS → NO_ACTION'); graded += 1
                continue
            requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{read["id"]}',
                           headers=H_WRITE,
                           json={'result': 'NO_ACTION',
                                 'resolved_at': datetime.now(timezone.utc).isoformat()},
                           timeout=10)
            graded += 1
            continue

        # Normalize prop_type (2026-08-02): Jerry synth sometimes emits the
        # bare prop family ("bb", "ha", "outs") without the direction suffix,
        # while mlb_pipeline_props always stores "bb_over"/"bb_under"/etc.
        # Was killing 119 BACK/FADE per day silently.
        jerry_ptype = read['prop_type']
        direction = (read.get('direction') or '').lower()
        opposite = 'under' if direction == 'over' else ('over' if direction == 'under' else None)
        if jerry_ptype and '_' not in jerry_ptype and direction in ('over', 'under'):
            family = jerry_ptype
        elif jerry_ptype and '_' in jerry_ptype:
            family = jerry_ptype.rsplit('_', 1)[0]  # ks_over → ks
        else:
            family = jerry_ptype

        lookup_same = f'{family}_{direction}' if direction else family
        lookup_flip = f'{family}_{opposite}' if opposite else None
        flipped = False

        # Try exact match (same family, same direction)
        pr = requests.get(f'{SB}/rest/v1/{props_table}',
                          headers=H_READ,
                          params={'game_id': f'eq.{read["game_id"]}',
                                  'player_name': f'eq.{read["player_name"]}',
                                  'prop_type': f'eq.{lookup_same}',
                                  'direction': f'eq.{direction}',
                                  'game_date': f'eq.{gd}',
                                  'select': 'result,final_value'},
                          timeout=10)
        if pr.status_code != 200:
            print(f'  ⚠ id={read["id"]} lookup HTTP {pr.status_code}: {pr.text[:200]}')
            prop_rows = []
        else:
            prop_rows = pr.json()

        # If no same-direction row, try opposite direction (grade with flip)
        if not prop_rows and lookup_flip:
            pr2 = requests.get(f'{SB}/rest/v1/{props_table}',
                               headers=H_READ,
                               params={'game_id': f'eq.{read["game_id"]}',
                                       'player_name': f'eq.{read["player_name"]}',
                                       'prop_type': f'eq.{lookup_flip}',
                                       'direction': f'eq.{opposite}',
                                       'game_date': f'eq.{gd}',
                                       'select': 'result,final_value'},
                               timeout=10)
            prop_rows = pr2.json() if pr2.status_code == 200 else []
            if prop_rows:
                flipped = True  # Jerry called opposite side — flip result

        # 2026-08-12: MLB API fallback before UNGRADEABLE stamp.
        # Yesterday audit found 75% UNGRADEABLE rate because Jerry emits takes
        # on prop_types the sportsbook doesn't offer for that pitcher (no props
        # table row exists). Instead of giving up, fetch the pitcher's actual
        # game stat from MLB Stats API and grade directly against JR's line.
        if not prop_rows and sport == 'MLB':
            api_result = _grade_from_mlb_api(read, gd)
            if api_result is not None:
                base_result, final_value = api_result
                final_result = flip_for_fade(base_result, verdict)
                if dry_run:
                    print(f'  [DRY-API] id={read["id"]} {read["player_name"]} {verdict} '
                          f'actual={final_value} vs {read.get("prop_line")} → {final_result}')
                    graded += 1
                    continue
                pu = requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{read["id"]}',
                                    headers=H_WRITE,
                                    json={'result': final_result,
                                          'actual_pa': final_value,
                                          'resolved_at': datetime.now(timezone.utc).isoformat()},
                                    timeout=10)
                if pu.status_code in (200, 204):
                    graded += 1
                continue

        # No matching prop at all AND MLB API fallback didn't fire — mark
        # UNGRADEABLE so it stops being Pending forever. Preserves auditability.
        if not prop_rows:
            if dry_run:
                print(f'  [DRY] id={read["id"]} {read["player_name"]} {jerry_ptype}/{direction} → UNGRADEABLE (no pipeline row, MLB API fallback failed)')
                graded += 1
                continue
            requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{read["id"]}',
                           headers=H_WRITE,
                           json={'result': 'UNGRADEABLE',
                                 'resolved_at': datetime.now(timezone.utc).isoformat()},
                           timeout=10)
            graded += 1
            continue

        if prop_rows[0].get('result') in (None, 'Pending'):
            continue  # underlying prop still pending

        base_result = prop_rows[0]['result']
        # If we matched on OPPOSITE direction (Jerry called over, pipeline stored
        # under), flip the base_result first — the underlying outcome is the same
        # (player's actual K/BB/HA count) but Win/Loss reads inverse from the
        # opposite line. Then apply FADE flip on top of that if verdict is FADE.
        if flipped:
            if base_result == 'Win':  base_result = 'Loss'
            elif base_result == 'Loss': base_result = 'Win'
        final_result = flip_for_fade(base_result, verdict)
        actual = {'prop_result': base_result, 'final_value': prop_rows[0].get('final_value')}

        if dry_run:
            print(f'  [DRY] id={read["id"]} {read["player_name"]} {verdict} → {final_result}'); graded += 1
            continue
        pu = requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{read["id"]}',
                            headers=H_WRITE,
                            json={'result': final_result,
                                  'resolved_at': datetime.now(timezone.utc).isoformat()},
                            timeout=10)
        if pu.status_code in (200, 204):
            graded += 1

    print(f'  [{sport}] graded {graded}/{len(reads)} prop_jerry_reads')
    return graded


def main(game_date: str | None = None, dry_run: bool = False,
         backfill_days: int = 3):
    """Grade prop_jerry_reads. Default runs on yesterday + backfill_days prior
    to catch late-arriving results (games delayed, rows created after cron,
    grader schedule misses).

    Coverage audit 2026-08-06 found 2 ungraded rows on 8/1 that survived the
    1-day-only grader (previous behavior). Backfill window catches those.
    """
    if game_date:
        # Explicit date — just that one
        dates = [game_date]
    else:
        # Yesterday + N days prior for backfill of any straggling rows
        base = yesterday_et()
        from datetime import date, timedelta
        base_d = date.fromisoformat(base)
        dates = [(base_d - timedelta(days=i)).isoformat() for i in range(backfill_days + 1)]

    total = 0
    for gd in dates:
        print(f'=== grade_prop_jerry_reads · {gd}{" · dry-run" if dry_run else ""} ===')
        for sport in PROPS_TABLE.keys():
            total += run_for_sport(sport, gd, dry_run=dry_run)
    print(f'\n=== graded {total} prop_jerry_reads total across {len(dates)} date(s) ===')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='Specific date; if omitted, grades yesterday + backfill window')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--backfill-days', type=int, default=3,
                   help='Days prior to yesterday to also re-grade (catches late-arriving results). Default 3.')
    args = p.parse_args()
    main(game_date=args.date, dry_run=args.dry_run, backfill_days=args.backfill_days)
