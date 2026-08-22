"""Grade prop_playbook_decisions (shadow-mode) rows against actual outcomes.

Companion to grade_props.py. Since 8/17 the prop playbook has been writing
shadow decisions to prop_playbook_decisions, but the table had no result
column so nothing was ever graded. Migration 20260820_prop_playbook_grading
adds the columns; this script fills them in.

Two-pass grading strategy per row:
  1. Look up the matching mlb_pipeline_props row by natural key
     (game_date, player_name, prop_type, direction, prop_line) and copy
     its `result` and `final_value`. Fastest path; also inherits any manual
     Void adjustments legacy graders made.
  2. If no legacy row matches (playbook can score props legacy skipped),
     fall back to MLB Stats API — same logic as grade_props.grade_prop.

Result mapping: mlb_pipeline_props.result stores 'Win'/'Loss'/'Push'/'Void';
per the migration spec we store 'W'/'L'/'P'/'Void' on this table.

Guardrails:
  * Never overwrites a row whose `result` is already populated (unless
    --force is passed).
  * If a row can't be graded, it's LOGGED, not silently skipped.
  * --dry-run prints tallies without writing.

Usage:
    python grade_prop_playbook.py                        # yesterday ET
    python grade_prop_playbook.py --date 2026-08-19      # single date
    python grade_prop_playbook.py --backfill 21          # 21 days ending at --date
    python grade_prop_playbook.py --dry-run
    python grade_prop_playbook.py --sport MLB            # (only MLB supported today)
"""
from __future__ import annotations
import argparse, json, os, sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

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

SPORT_PROPS_TABLE = {'MLB': 'mlb_pipeline_props'}

# prop_type → boxscore stat key (mirrors grade_props.STAT_MAP_MLB)
STAT_MAP_MLB = {
    'ks_over':   'ks',   'ks_under':   'ks',
    'bb_over':   'bb',   'bb_under':   'bb',
    'er_over':   'er',   'er_under':   'er',
    'ha_over':   'h_pit', 'ha_under':  'h_pit',
    'outs_over': 'outs', 'outs_under': 'outs',
    'hits_over': 'h_bat',
}

# mlb_pipeline_props.result → prop_playbook_decisions.result mapping
RESULT_MAP = {
    'Win': 'W', 'Loss': 'L', 'Push': 'P', 'Void': 'Void',
    'W': 'W', 'L': 'L', 'P': 'P',
}


def yesterday_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=28)).strftime('%Y-%m-%d')


# ─── Preflight ─────────────────────────────────────────────────────────
def preflight() -> bool:
    """Confirm grading columns exist. Bail with a nudge if not."""
    r = requests.get(
        f'{SB}/rest/v1/prop_playbook_decisions',
        headers=H_READ,
        params={'select': 'result,graded_at,actual_value,grade_source', 'limit': '1'},
        timeout=10,
    )
    if r.status_code == 200:
        return True
    print('✗ Migration NOT applied. Paste supabase/migrations/20260820_prop_playbook_grading.sql')
    print('  into the Supabase SQL editor, then rerun.')
    print(f'  probe error: HTTP {r.status_code} — {r.text[:200]}')
    return False


# ─── Pass 1: inherit result from mlb_pipeline_props ────────────────────
def fetch_legacy_results_for_date(sport: str, date_str: str) -> dict:
    """{(player, prop_type, direction, prop_line): (result, final_value)}
    from the legacy props table on `date_str`."""
    table = SPORT_PROPS_TABLE.get(sport)
    if not table: return {}
    r = requests.get(
        f'{SB}/rest/v1/{table}',
        headers=H_READ,
        params={
            'game_date': f'eq.{date_str}',
            'result':    'not.is.null',
            'select':    'player_name,prop_type,direction,prop_line,result,final_value',
            'limit':     '5000',
        },
        timeout=30,
    )
    if r.status_code != 200:
        print(f'  ⚠ legacy fetch failed HTTP {r.status_code}: {r.text[:200]}')
        return {}
    out = {}
    for row in r.json():
        line = row.get('prop_line')
        if line is not None:
            line = float(line)
        key = (row['player_name'], row['prop_type'], row['direction'], line)
        out[key] = (row.get('result'), row.get('final_value'))
    return out


# ─── Pass 2: fall back to MLB Stats API ────────────────────────────────
def _norm_name(s: str) -> str:
    """Lowercased + diacritics stripped. Matches boxscore names ('Jesús
    Luzardo') to prop-table names ('Jesus Luzardo'). 2026-08-22: fix for
    34 residual ungraded 8/21 decisions where the boxscore had the
    accented name but the prop was stored ASCII-only, so plain .lower()
    lookup missed."""
    import unicodedata
    if not s: return ''
    normed = unicodedata.normalize('NFKD', str(s))
    ascii_ = ''.join(c for c in normed if not unicodedata.combining(c))
    return ascii_.lower().strip()


def fetch_player_stats_for_date(date_str: str) -> tuple[dict, int]:
    """Return ({player_name_normalized: stats_dict}, n_final_games)."""
    try:
        sched = json.load(urllib.request.urlopen(
            f'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_str}',
            timeout=15))
    except Exception as e:
        print(f'  ⚠ schedule fetch failed: {e}')
        return {}, 0
    game_pks = []
    for d in sched.get('dates', []):
        for g in d.get('games', []):
            state = g.get('status', {}).get('detailedState', '')
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
                    'ks':    pit.get('strikeOuts', 0) if pit else 0,
                    'bb':    pit.get('baseOnBalls', 0) if pit else 0,
                    'er':    pit.get('earnedRuns', 0) if pit else 0,
                    'h_pit': pit.get('hits', 0) if pit else 0,
                    'outs':  outs,
                    'h_bat': bat.get('hits', 0) if bat else 0,
                }
    return stats, len(game_pks)


def grade_from_stats(prop_type: str, direction: str, line, player_name: str,
                     stats_map: dict) -> tuple:
    """Return (short_result, actual_value) — short_result in {'W','L','P',None}."""
    stat_key = STAT_MAP_MLB.get(prop_type)
    if not stat_key: return None, None
    stats = stats_map.get(_norm_name(player_name))
    if not stats: return None, None
    actual = stats.get(stat_key)
    if actual is None: return None, None
    if line is None: return None, actual
    line = float(line)
    d = (direction or '').lower()
    if d == 'over':
        if actual > line: return 'W', actual
        if actual < line: return 'L', actual
        return 'P', actual
    else:  # under
        if actual < line: return 'W', actual
        if actual > line: return 'L', actual
        return 'P', actual


# ─── Main per-date grader ──────────────────────────────────────────────
def grade_date(date_str: str, sport: str = 'MLB',
               dry_run: bool = False, force: bool = False) -> dict:
    """Grade all ungraded prop_playbook_decisions rows for a single date."""
    print(f'=== grade_prop_playbook · {sport} · {date_str} ===')

    # Fetch ungraded rows (or all if --force)
    params = {
        'sport':     f'eq.{sport}',
        'game_date': f'eq.{date_str}',
        'select':    'id,player_name,prop_type,direction,prop_line,playbook_tier,result',
        'limit':     '5000',
    }
    if not force:
        params['result'] = 'is.null'
    r = requests.get(f'{SB}/rest/v1/prop_playbook_decisions',
                     headers=H_READ, params=params, timeout=30)
    if r.status_code != 200:
        print(f'  ⚠ ppd fetch failed HTTP {r.status_code}: {r.text[:200]}')
        return {}
    rows = r.json()
    if not rows:
        print(f'  no {"" if force else "ungraded "}rows on {date_str}')
        return {}
    print(f'  {len(rows)} rows to grade ({"force-regrade all" if force else "ungraded only"})')

    # Pass 1: legacy lookup
    legacy = fetch_legacy_results_for_date(sport, date_str)
    print(f'  legacy graded props on this date: {len(legacy)}')

    # Pass 2: stats API only if we have unmatched rows
    stats_map = None

    tally = defaultdict(int)
    updates = []
    unresolved_examples = []  # keep up to 5 for logging

    for row in rows:
        line = row.get('prop_line')
        if line is not None:
            try: line = float(line)
            except (TypeError, ValueError): line = None
        key = (row['player_name'], row['prop_type'], row['direction'], line)

        # ── Pass 1
        leg = legacy.get(key)
        if leg is not None:
            legacy_result, actual = leg
            short = RESULT_MAP.get(legacy_result)
            if short is None:
                tally['bad_legacy_result'] += 1
                unresolved_examples.append((row['id'], key, f'legacy result={legacy_result!r}'))
                continue
            updates.append({
                'id': row['id'],
                'result': short,
                'actual_value': actual,
                'grade_source': 'mlb_pipeline_props',
            })
            tally['from_legacy'] += 1
            tally[short] += 1
            continue

        # ── Pass 2: stats API
        if stats_map is None:
            stats_map, n_games = fetch_player_stats_for_date(date_str)
            print(f'  loaded {len(stats_map)} player stat rows from {n_games} final games (stats API)')
            if n_games == 0:
                # No games are final yet — everything else is unresolvable
                pass

        short, actual = grade_from_stats(
            row['prop_type'], row['direction'], line, row['player_name'], stats_map or {})
        if short is None:
            tally['unresolved'] += 1
            if len(unresolved_examples) < 5:
                if not (stats_map or {}).get(_norm_name(row['player_name'])):
                    reason = 'player not in any boxscore'
                elif STAT_MAP_MLB.get(row['prop_type']) is None:
                    reason = f'no stat map for prop_type={row["prop_type"]!r}'
                else:
                    reason = 'stat missing / line missing'
                unresolved_examples.append((row['id'], key, reason))
            continue
        updates.append({
            'id': row['id'],
            'result': short,
            'actual_value': actual,
            'grade_source': 'stats_api',
        })
        tally['from_stats_api'] += 1
        tally[short] += 1

    # ── Report
    print(f'  → resolved {len(updates)}: '
          f'{tally["W"]}W {tally["L"]}L {tally["P"]}P '
          f'(legacy={tally["from_legacy"]}, stats_api={tally["from_stats_api"]})')
    dec = tally['W'] + tally['L']
    if dec:
        print(f'    hit rate: {100*tally["W"]/dec:.1f}% (playbook side-blind, over/under literal)')
    if tally['unresolved']:
        print(f'  ⚠ {tally["unresolved"]} rows unresolved:')
        for _id, key, reason in unresolved_examples:
            print(f'      id={_id} {key} — {reason}')
    if tally['bad_legacy_result']:
        print(f'  ⚠ {tally["bad_legacy_result"]} rows had unrecognised legacy.result — skipped')

    # ── Write
    if dry_run:
        print(f'  --dry-run: would PATCH {len(updates)} rows')
        return dict(tally)

    now_iso = datetime.now(timezone.utc).isoformat()
    errors = 0
    for u in updates:
        patch = {
            'result':       u['result'],
            'actual_value': u['actual_value'],
            'grade_source': u['grade_source'],
            'graded_at':    now_iso,
        }
        r2 = requests.patch(
            f'{SB}/rest/v1/prop_playbook_decisions?id=eq.{u["id"]}',
            headers=H_WRITE, data=json.dumps(patch), timeout=10)
        if r2.status_code not in (200, 204):
            errors += 1
            if errors <= 3:
                print(f'    write error id={u["id"]} HTTP {r2.status_code} — {r2.text[:200]}')
    if errors:
        print(f'  ⚠ {errors} write errors')
    else:
        print(f'  ✓ patched {len(updates)} rows')
    return dict(tally)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=yesterday_et(), help='YYYY-MM-DD (default: yesterday ET)')
    ap.add_argument('--sport', default='MLB', choices=list(SPORT_PROPS_TABLE.keys()))
    ap.add_argument('--backfill', type=int, default=0,
                    help='Backfill this many days ending at --date (0 = single date)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true',
                    help='Regrade rows that already have a result (defensive: default is off)')
    args = ap.parse_args()

    if not preflight():
        sys.exit(1)

    if args.backfill > 0:
        end_date = datetime.strptime(args.date, '%Y-%m-%d')
        for i in range(args.backfill):
            d = (end_date - timedelta(days=i)).strftime('%Y-%m-%d')
            grade_date(d, args.sport, args.dry_run, args.force)
            print()
    else:
        grade_date(args.date, args.sport, args.dry_run, args.force)


if __name__ == '__main__':
    main()
