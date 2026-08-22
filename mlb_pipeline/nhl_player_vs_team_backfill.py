"""NHL player-vs-team backfill (2026-08-21).

Mirror of nfl_qb_vs_team + nba_player_vs_team backfills. Pulls NHL API
boxscores per game, extracts each skater and goalie line, aggregates per
(player, opponent). Writes to public.nhl_player_vs_team.

Skaters get goals/assists/points/shots/toi averages. Goalies get SV%/GAA/
saves + wins/losses. Position field distinguishes rows at read time.

CLI:
  python nhl_player_vs_team_backfill.py               # 2024-25 regular season
  python nhl_player_vs_team_backfill.py --start 2024-10-04 --end 2025-06-24
  python nhl_player_vs_team_backfill.py --dry-run --limit 5
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

NHL_BASE = 'https://api-web.nhle.com/v1'
UA = {'User-Agent': 'SweatLocker/1.0 (data-collection)'}


def _get(url):
    try:
        r = requests.get(url, headers=UA, timeout=15)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def get_completed_games(day_iso):
    """Schedule endpoint returns the whole week; filter to date + FINAL/OFF."""
    data = _get(f'{NHL_BASE}/schedule/{day_iso}')
    if not data: return []
    out = []
    for day in data.get('gameWeek', []):
        if day.get('date') != day_iso: continue
        for g in day.get('games', []):
            state = g.get('gameState')
            if state not in ('OFF', 'FINAL'): continue
            home = g.get('homeTeam', {})
            away = g.get('awayTeam', {})
            home_score = home.get('score') or 0
            away_score = away.get('score') or 0
            out.append({
                'game_id': str(g.get('id')),
                'date': day_iso,
                'home_abbr': home.get('abbrev'),
                'away_abbr': away.get('abbrev'),
                'home_score': home_score,
                'away_score': away_score,
            })
    return out


def _toi_to_min(toi_str):
    if not toi_str or ':' not in str(toi_str): return None
    try:
        m, s = str(toi_str).split(':')
        return round(int(m) + int(s) / 60, 2)
    except (ValueError, TypeError):
        return None


def parse_boxscore(game):
    """Return list of per-player rows from NHL boxscore.

    Skater fields: goals, assists, points, sog, toi, position
    Goalie fields: saves, shots_faced, sv_pct, goals_against, decision, starter
    """
    gid = game['game_id']
    data = _get(f'{NHL_BASE}/gamecenter/{gid}/boxscore')
    if not data: return []
    pbg = data.get('playerByGameStats') or {}
    home_stats = pbg.get('homeTeam') or {}
    away_stats = pbg.get('awayTeam') or {}

    rows = []
    for stats, team_abbr, opp_abbr in [
        (home_stats, game['home_abbr'], game['away_abbr']),
        (away_stats, game['away_abbr'], game['home_abbr']),
    ]:
        # Skaters (forwards + defense)
        for pos_group, group_label in [('forwards', 'F'), ('defense', 'D')]:
            for p in stats.get(pos_group, []) or []:
                name = (p.get('name') or {}).get('default') if isinstance(p.get('name'), dict) else p.get('name')
                rows.append({
                    'player_id': str(p.get('playerId') or ''),
                    'name': name or '',
                    'position': p.get('position') or group_label,
                    'team_abbr': team_abbr,
                    'opp_abbr': opp_abbr,
                    'is_goalie': False,
                    'goals': p.get('goals') or 0,
                    'assists': p.get('assists') or 0,
                    'points': p.get('points') or 0,
                    'sog': p.get('sog') or 0,
                    'pp_pts': (p.get('powerPlayGoals') or 0),  # goals only; API doesn't split PP asts on skater level
                    'toi_min': _toi_to_min(p.get('toi')),
                })
        # Goalies
        for g in stats.get('goalies', []) or []:
            name = (g.get('name') or {}).get('default') if isinstance(g.get('name'), dict) else g.get('name')
            # saveShotsAgainst formatted like "29/31"
            saves = shots = None
            ssa = g.get('saveShotsAgainst')
            if ssa and '/' in str(ssa):
                try:
                    a, b = str(ssa).split('/', 1)
                    saves, shots = int(a), int(b)
                except ValueError:
                    pass
            decision = g.get('decision')
            rows.append({
                'player_id': str(g.get('playerId') or ''),
                'name': name or '',
                'position': 'G',
                'team_abbr': team_abbr,
                'opp_abbr': opp_abbr,
                'is_goalie': True,
                'saves': saves,
                'shots_faced': shots,
                'sv_pct': g.get('savePctg'),
                'goals_against': g.get('goalsAgainst') or 0,
                'starter': bool(g.get('starter')),
                'decision': decision,  # 'W' / 'L' / 'O'
                'toi_min': _toi_to_min(g.get('toi')),
            })
    return rows


def aggregate(games_stats):
    bucket = defaultdict(list)
    for gdate, rows in games_stats:
        for r in rows:
            if not r['player_id']: continue
            bucket[(r['player_id'], r['opp_abbr'])].append((gdate, r))

    def _avg(rows, k):
        vals = [r[k] for r in rows if r.get(k) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    out = []
    for (pid, opp), entries in bucket.items():
        entries.sort(key=lambda x: x[0], reverse=True)
        career = [e[1] for e in entries]
        recent = career[:5]
        n = len(career); nr = len(recent)
        name = career[0]['name']
        pos = career[0]['position']
        is_g = career[0]['is_goalie']

        row = {
            'player_id': pid,
            'player_name': name,
            'position': pos,
            'opponent_team': opp,
            'career_games': n,
            'last_faced_date': entries[0][0],
            'recent_n_games': nr,
        }

        if is_g:
            wins = sum(1 for r in career if r.get('decision') == 'W')
            losses = sum(1 for r in career if r.get('decision') == 'L')
            starts = sum(1 for r in career if r.get('starter'))
            row.update({
                'career_starts': starts,
                'career_sv_pct': _avg(career, 'sv_pct'),
                'career_saves_avg': _avg(career, 'saves'),
                'career_shots_faced_avg': _avg(career, 'shots_faced'),
                'career_gaa': _avg(career, 'goals_against'),  # per-game GA; not per-60
                'career_wins': wins,
                'career_losses': losses,
                'recent_sv_pct': _avg(recent, 'sv_pct'),
                'recent_saves_avg': _avg(recent, 'saves'),
            })
        else:
            row.update({
                'career_goals_avg': _avg(career, 'goals'),
                'career_assists_avg': _avg(career, 'assists'),
                'career_points_avg': _avg(career, 'points'),
                'career_shots_avg': _avg(career, 'sog'),
                'career_pp_pts_avg': _avg(career, 'pp_pts'),
                'career_toi_avg': _avg(career, 'toi_min'),
                'recent_points_avg': _avg(recent, 'points'),
                'recent_shots_avg': _avg(recent, 'sog'),
            })
        out.append(row)
    return out


def upsert_batch(rows, dry_run=False):
    if dry_run or not rows:
        print(f'  [dry] would upsert {len(rows)} vs-team rows')
        return len(rows)
    # PostgREST batch requires every object in the batch to have identical
    # keys — skater rows carry goals/assists/points/shots, goalie rows carry
    # saves/sv_pct/goals_against/decision. Union all keys and fill missing
    # with None so the payload validates. (feedback_postgrest_batch_normalize_keys)
    all_keys = set()
    for r in rows:
        all_keys.update(r.keys())
    normalized = [{k: r.get(k) for k in all_keys} for r in rows]
    total = 0
    for i in range(0, len(normalized), 500):
        chunk = normalized[i:i+500]
        r = requests.post(f'{SB}/rest/v1/nhl_player_vs_team',
                          headers=H_WRITE, json=chunk, timeout=30)
        if r.status_code in (201, 204): total += len(chunk)
        else: print(f'  ! upsert failed chunk {i}: {r.status_code} {r.text[:200]}')
    return total


def run(start_iso, end_iso, dry_run=False, limit_games=None):
    print(f'=== nhl_player_vs_team_backfill · {start_iso} → {end_iso} ===')
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    day = start
    games_stats = []
    games_seen = 0
    while day <= end:
        games = get_completed_games(day.isoformat())
        for g in games:
            if limit_games and games_seen >= limit_games: break
            stats = parse_boxscore(g)
            if stats:
                games_stats.append((g['date'], stats))
                games_seen += 1
                if games_seen % 25 == 0:
                    print(f'  scanned {games_seen} games ({day.isoformat()})')
            time.sleep(0.15)
        if limit_games and games_seen >= limit_games: break
        day += timedelta(days=1)

    print(f'  scanned {games_seen} completed games total')
    aggregates = aggregate(games_stats)
    print(f'  aggregated {len(aggregates)} unique (player, opp) rows')
    written = upsert_batch(aggregates, dry_run=dry_run)
    print(f'  ✓ upserted {written} rows to nhl_player_vs_team')


def main():
    p = argparse.ArgumentParser()
    # 2024-25 NHL regular season Oct 4 2024 → Apr 17 2025; playoffs to late June
    p.add_argument('--start', default='2024-10-04')
    p.add_argument('--end', default='2025-06-24')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--limit', type=int)
    args = p.parse_args()
    run(args.start, args.end, dry_run=args.dry_run, limit_games=args.limit)


if __name__ == '__main__':
    main()
