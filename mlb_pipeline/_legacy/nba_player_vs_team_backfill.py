"""NBA player-vs-team career + recent aggregation backfill (2026-08-21).

Mirror of nfl_qb_vs_team_backfill.py pattern. Pulls ESPN boxscores game by
game, extracts each player's line, aggregates per (player, opponent). Writes
career averages + last-5-vs-opp recent averages to public.nba_player_vs_team.

Feeds prop signals for pts / reb / ast / 3PM / PRA overs/unders — the
'player X vs this defense career' angle we've been missing.

CLI:
  python nba_player_vs_team_backfill.py                   # last full season
  python nba_player_vs_team_backfill.py --start 2025-10-22 --end 2026-06-15
  python nba_player_vs_team_backfill.py --dry-run --limit 5
"""
from __future__ import annotations
import argparse, os, sys, time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

ESPN_BASE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba'


def _get(url, params=None, retries=2):
    import time as _t
    for i in range(retries + 1):
        try:
            r = requests.get(url, params=params or {}, timeout=15)
            if r.status_code == 200: return r.json()
            return None
        except (requests.ConnectionError, requests.Timeout):
            if i < retries: _t.sleep(1.5 ** i)
    return None


def get_completed_games(day_iso):
    """ESPN scoreboard for a date → list of {game_id, home, away, status}."""
    ymd = day_iso.replace('-', '')
    data = _get(f'{ESPN_BASE}/scoreboard', {'dates': ymd})
    out = []
    for evt in (data or {}).get('events', []):
        try:
            comp = evt['competitions'][0]
            status = comp['status']['type']['state']
            if status != 'post': continue  # only completed
            competitors = comp['competitors']
            home = next(c for c in competitors if c['homeAway'] == 'home')
            away = next(c for c in competitors if c['homeAway'] == 'away')
            out.append({
                'game_id': evt['id'],
                'date': day_iso,
                'home_abbr': home['team']['abbreviation'],
                'away_abbr': away['team']['abbreviation'],
            })
        except (KeyError, StopIteration, IndexError):
            continue
    return out


def parse_boxscore(game_id):
    """Return list of {player_id, name, team_abbr, opp_abbr, stats:{pts,reb,ast,...}}."""
    data = _get(f'{ESPN_BASE}/summary', {'event': game_id})
    if not data: return []

    bs = data.get('boxscore') or {}
    teams = bs.get('players') or []
    if len(teams) != 2: return []

    # Team abbreviations for opponent lookup
    abbrs = [t.get('team', {}).get('abbreviation') for t in teams]
    if None in abbrs: return []

    rows = []
    for i, team_block in enumerate(teams):
        team_abbr = abbrs[i]
        opp_abbr = abbrs[1 - i]
        stat_groups = team_block.get('statistics') or []
        if not stat_groups: continue
        sg = stat_groups[0]
        labels = sg.get('labels') or []
        athletes = sg.get('athletes') or []
        for a in athletes:
            if a.get('didNotPlay') or a.get('ejected'): continue
            athlete = a.get('athlete') or {}
            stats_arr = a.get('stats') or []
            if len(stats_arr) != len(labels): continue
            stat_map = dict(zip(labels, stats_arr))

            def _num(k, default=0.0):
                v = stat_map.get(k)
                if v is None or v == '--': return default
                try: return float(v)
                except (TypeError, ValueError): return default

            def _make(k):  # "8-16" -> 8/16
                v = stat_map.get(k)
                if not v or '-' not in str(v): return None, None
                a_, b_ = str(v).split('-', 1)
                try: return float(a_), float(b_)
                except ValueError: return None, None

            fg_m, fg_a = _make('FG')
            tp_m, tp_a = _make('3PT')
            ft_m, ft_a = _make('FT')
            rows.append({
                'player_id': str(athlete.get('id') or ''),
                'name': athlete.get('displayName') or '',
                'team_abbr': team_abbr,
                'opp_abbr': opp_abbr,
                'min': _num('MIN'),
                'pts': _num('PTS'),
                'reb': _num('REB'),
                'ast': _num('AST'),
                'stl': _num('STL'),
                'blk': _num('BLK'),
                'tpm': tp_m if tp_m is not None else 0.0,
                'fg_m': fg_m, 'fg_a': fg_a,
                'tp_a': tp_a, 'ft_m': ft_m, 'ft_a': ft_a,
            })
    return rows


def aggregate(games_stats):
    """Fold list of per-game per-player rows into vs-team aggregates.

    Returns dict[(player_id, opp)] -> aggregate row ready for upsert.
    Each entry carries career_* averages across all games and recent_*
    averages across the most recent 5.
    """
    # (player_id, opp) -> list of (date, stats)
    bucket = defaultdict(list)
    for gdate, rows in games_stats:
        for r in rows:
            if not r['player_id']: continue
            bucket[(r['player_id'], r['opp_abbr'])].append((gdate, r))

    out = []
    for (pid, opp), entries in bucket.items():
        entries.sort(key=lambda x: x[0], reverse=True)
        career = [e[1] for e in entries]
        recent = career[:5]
        n = len(career); nr = len(recent)
        name = career[0]['name']

        def _avg(rows, k):
            vals = [r[k] for r in rows if r.get(k) is not None]
            return round(sum(vals) / len(vals), 3) if vals else None

        def _pct(rows, mk, ak):
            m = sum((r[mk] or 0) for r in rows)
            a = sum((r[ak] or 0) for r in rows)
            return round(m / a, 4) if a else None

        out.append({
            'player_id': pid,
            'player_name': name,
            'opponent_team': opp,
            'career_games': n,
            'career_pts_avg': _avg(career, 'pts'),
            'career_reb_avg': _avg(career, 'reb'),
            'career_ast_avg': _avg(career, 'ast'),
            'career_stl_avg': _avg(career, 'stl'),
            'career_blk_avg': _avg(career, 'blk'),
            'career_3pm_avg': _avg(career, 'tpm'),
            'career_minutes_avg': _avg(career, 'min'),
            'career_fg_pct': _pct(career, 'fg_m', 'fg_a'),
            'career_3p_pct': _pct(career, 'tpm', 'tp_a'),
            'career_ft_pct': _pct(career, 'ft_m', 'ft_a'),
            'recent_n_games': nr,
            'recent_pts_avg': _avg(recent, 'pts'),
            'recent_reb_avg': _avg(recent, 'reb'),
            'recent_ast_avg': _avg(recent, 'ast'),
            'recent_minutes_avg': _avg(recent, 'min'),
            'recent_fg_pct': _pct(recent, 'fg_m', 'fg_a'),
            'last_faced_date': entries[0][0],
        })
    return out


def upsert_batch(rows, dry_run=False):
    if dry_run or not rows:
        print(f'  [dry] would upsert {len(rows)} vs-team rows')
        return len(rows)
    # PostgREST batch size cap; chunk to be safe
    total = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i:i+500]
        r = requests.post(f'{SB}/rest/v1/nba_player_vs_team',
                          headers={**H_WRITE, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
                          json=chunk, timeout=30)
        if r.status_code in (201, 204): total += len(chunk)
        else: print(f'  ! upsert failed chunk {i}: {r.status_code} {r.text[:200]}')
    return total


def run(start_iso, end_iso, dry_run=False, limit_games=None):
    print(f'=== nba_player_vs_team_backfill · {start_iso} → {end_iso} ===')
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    day = start
    games_stats = []
    games_seen = 0
    while day <= end:
        games = get_completed_games(day.isoformat())
        for g in games:
            if limit_games and games_seen >= limit_games: break
            stats = parse_boxscore(g['game_id'])
            if stats:
                games_stats.append((g['date'], stats))
                games_seen += 1
                if games_seen % 25 == 0:
                    print(f'  scanned {games_seen} games ({day.isoformat()})')
            time.sleep(0.15)  # be polite to ESPN
        if limit_games and games_seen >= limit_games: break
        day += timedelta(days=1)

    print(f'  scanned {games_seen} completed games total')
    aggregates = aggregate(games_stats)
    print(f'  aggregated {len(aggregates)} unique (player, opp) rows')
    written = upsert_batch(aggregates, dry_run=dry_run)
    print(f'  ✓ upserted {written} rows to nba_player_vs_team')


def main():
    p = argparse.ArgumentParser()
    # 2025-26 NBA regular season Oct 22, 2025 → Apr 13, 2026; playoffs to mid-June
    p.add_argument('--start', default='2025-10-22')
    p.add_argument('--end', default='2026-06-15')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--limit', type=int, help='cap games for smoke test')
    args = p.parse_args()
    run(args.start, args.end, dry_run=args.dry_run, limit_games=args.limit)


if __name__ == '__main__':
    main()
