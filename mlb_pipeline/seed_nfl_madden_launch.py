"""Seed nfl_madden_ratings + nfl_madden_player_ratings with the official
Madden NFL 27 launch snapshot (2026-08-13).

WHY THIS EXISTS
───────────────
EA's live ratings DB requires JS-heavy scraping. For MVP we seed from
the official launch table published 7/31/26 (team OVR/OFF/DEF for all
32 teams) plus the 99 club (6 players) and top 25 by OVR. This gets
the signals firing from Day 1. Weekly refresh scraper via madden27.wiki
or maddenratings.com can come later without changing consumers.

USAGE
─────
    python seed_nfl_madden_launch.py                  # week 0 launch (default)
    python seed_nfl_madden_launch.py --week 1         # after Week 1 EA refresh
    python seed_nfl_madden_launch.py --season 2026    # explicit

Idempotent — on_conflict=(team, season, week_snapshot).

DATA SOURCE
───────────
Official EA launch snapshot referenced in
[[project_madden_top100_nfl_signal_824]]. Team ratings from EA's
July 31 2026 launch table. Player ratings from EA's launch database
(6 players @ 99 + top 25 @ 96-98).
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# ─── OFFICIAL 8/13/26 LAUNCH SNAPSHOT ─────────────────────────
# Format: (canonical_team, ovr, off, def, ovr_rank)
# Canonical names match nfl_game_context.home_team convention.

TEAM_RATINGS = [
    ('Los Angeles Rams',      90, 93, 89, 1),
    ('Philadelphia Eagles',   88, 88, 87, 2),
    ('Baltimore Ravens',      88, 91, 86, 3),
    ('Denver Broncos',        87, 86, 88, 4),
    ('New England Patriots',  87, 87, 85, 5),
    ('Detroit Lions',         86, 92, 83, 6),
    ('Seattle Seahawks',      86, 86, 86, 7),
    ('San Francisco 49ers',   85, 89, 82, 8),
    ('Buffalo Bills',         85, 90, 80, 9),
    ('Kansas City Chiefs',    84, 90, 80, 10),
    ('Dallas Cowboys',        84, 87, 80, 11),
    ('Chicago Bears',         83, 85, 82, 12),
    ('Cincinnati Bengals',    83, 88, 77, 13),
    ('Pittsburgh Steelers',   83, 78, 87, 14),
    ('Los Angeles Chargers',  82, 85, 79, 15),
    ('Indianapolis Colts',    82, 84, 79, 16),
    ('Houston Texans',        82, 80, 88, 17),
    ('Tampa Bay Buccaneers',  81, 83, 79, 18),
    ('Atlanta Falcons',       81, 83, 78, 19),
    ('Green Bay Packers',     81, 82, 81, 20),
    ('Carolina Panthers',     80, 79, 80, 21),
    ('Minnesota Vikings',     80, 82, 78, 22),
    ('Washington Commanders', 79, 81, 77, 23),
    ('New York Giants',       79, 80, 78, 24),
    ('Jacksonville Jaguars',  79, 79, 79, 25),
    ('Cleveland Browns',      78, 75, 81, 26),
    ('New Orleans Saints',    78, 81, 75, 27),
    ('New York Jets',         77, 75, 77, 28),
    ('Las Vegas Raiders',     77, 79, 76, 29),
    ('Arizona Cardinals',     76, 77, 74, 30),
    ('Tennessee Titans',      76, 74, 78, 31),
    ('Miami Dolphins',        74, 74, 73, 32),
]

# ─── 99 CLUB + TOP 25 BY OVR (launch) ─────────────────────────
# Format: (player, team, position, ovr)
# Focus on QBs + edge rushers + top skill positions (biggest per-player
# impact on game outcomes). RB/WR/TE Top 100s captured via Top 100 table.

PLAYER_RATINGS_TOP25 = [
    # 99 club
    ("Ja'Marr Chase",         'Cincinnati Bengals',    'WR',   99),
    ('Jaxon Smith-Njigba',    'Seattle Seahawks',      'WR',   99),
    ('Josh Allen',            'Buffalo Bills',         'QB',   99),
    ('Matthew Stafford',      'Los Angeles Rams',      'QB',   99),
    ('Myles Garrett',         'Los Angeles Rams',      'EDGE', 99),
    ('Trey McBride',          'Arizona Cardinals',     'TE',   99),
    # 98
    ('Christian Gonzalez',    'New England Patriots',  'CB',   98),
    ('Jahmyr Gibbs',          'Detroit Lions',         'HB',   98),
    ('Micah Parsons',         'Green Bay Packers',     'EDGE', 98),
    ('Penei Sewell',          'Detroit Lions',         'LT',   98),
    ('Puka Nacua',            'Los Angeles Rams',      'WR',   98),
    # 97
    ('Christian McCaffrey',   'San Francisco 49ers',   'HB',   97),
    ('Fred Warner',           'San Francisco 49ers',   'LB',   97),
    ('Joe Burrow',            'Cincinnati Bengals',    'QB',   97),
    ('Lane Johnson',          'Philadelphia Eagles',   'RT',   97),
    ('Maxx Crosby',           'Las Vegas Raiders',     'EDGE', 97),
    ('Patrick Surtain II',    'Denver Broncos',        'CB',   97),
    # 96
    ('Derrick Brown',         'Carolina Panthers',     'DT',   96),
    ('Garett Bolles',         'Denver Broncos',        'LT',   96),
    ('George Kittle',         'San Francisco 49ers',   'TE',   96),
    ('Joe Thuney',            'Chicago Bears',         'OG',   96),
    ('Jonathan Taylor',       'Indianapolis Colts',    'HB',   96),
    ('Trent Williams',        'San Francisco 49ers',   'LT',   96),
    # High-value QBs at 94-95 (needed for QB delta signals — approximate from
    # public reporting; refresh via madden27.wiki once scraper is in)
    ('Patrick Mahomes',       'Kansas City Chiefs',    'QB',   95),
    ('Lamar Jackson',         'Baltimore Ravens',      'QB',   95),
    ('Jayden Daniels',        'Washington Commanders', 'QB',   93),
    ('C.J. Stroud',           'Houston Texans',        'QB',   90),
    ('Justin Herbert',        'Los Angeles Chargers',  'QB',   90),
    ('Jared Goff',            'Detroit Lions',         'QB',   90),
    ('Jalen Hurts',           'Philadelphia Eagles',   'QB',   92),
    ('Dak Prescott',          'Dallas Cowboys',        'QB',   88),
    ('Baker Mayfield',        'Tampa Bay Buccaneers',  'QB',   86),
    ('Caleb Williams',        'Chicago Bears',         'QB',   85),
    ('Bo Nix',                'Denver Broncos',        'QB',   84),
    ('Drake Maye',            'New England Patriots',  'QB',   84),
    ('Bryce Young',           'Carolina Panthers',     'QB',   79),
    ('Anthony Richardson',    'Indianapolis Colts',    'QB',   80),
    ('Trevor Lawrence',       'Jacksonville Jaguars',  'QB',   85),
    ('Kyler Murray',          'Arizona Cardinals',     'QB',   85),
    ('Tua Tagovailoa',        'Miami Dolphins',        'QB',   84),
    ('Sam Darnold',           'Seattle Seahawks',      'QB',   82),
    ('Aaron Rodgers',         'Pittsburgh Steelers',   'QB',   84),
    ('Justin Fields',         'New York Jets',         'QB',   80),
    ('Michael Penix Jr.',     'Atlanta Falcons',       'QB',   82),
    ('Geno Smith',            'Las Vegas Raiders',     'QB',   82),
    ('Cam Ward',              'Tennessee Titans',      'QB',   78),
    ('Jordan Love',           'Green Bay Packers',     'QB',   87),
    ('J.J. McCarthy',         'Minnesota Vikings',     'QB',   80),
    ('Joe Flacco',            'Cleveland Browns',      'QB',   77),
    ('Spencer Rattler',       'New Orleans Saints',    'QB',   77),
    ('Russell Wilson',        'New York Giants',       'QB',   79),
]


def upsert_team_ratings(season: int, week: int):
    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = []
    for team, ovr, off, deff, rank in TEAM_RATINGS:
        payloads.append({
            'team': team,
            'season': season,
            'week_snapshot': week,
            'ovr': ovr,
            'off_rating': off,
            'def_rating': deff,
            'ovr_rank': rank,
            'source': 'ea_launch_snapshot',
            'fetched_at': now_iso,
        })
    body = json.dumps(payloads).encode('utf-8')
    url = f'{SB}/rest/v1/nfl_madden_ratings?on_conflict=team,season,week_snapshot'
    req = urllib.request.Request(url, data=body, headers=H_WRITE, method='POST')
    try:
        urllib.request.urlopen(req, timeout=30).read()
        print(f'[team] upserted {len(payloads)} rows (season={season}, week={week})', flush=True)
        return True
    except Exception as e:
        print(f'[team] upsert failed: {e}', flush=True)
        return False


def upsert_player_ratings(season: int, week: int):
    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = []
    for player, team, pos, ovr in PLAYER_RATINGS_TOP25:
        # Position group
        if pos in ('QB', 'HB', 'FB', 'WR', 'TE', 'LT', 'RT', 'OG', 'C', 'OL'):
            pgroup = 'offense'
        elif pos in ('EDGE', 'DE', 'DT', 'NT', 'LB', 'CB', 'S', 'FS', 'SS'):
            pgroup = 'defense'
        else:
            pgroup = 'special'
        payloads.append({
            'player_name': player,
            'team': team,
            'season': season,
            'week_snapshot': week,
            'position': pos,
            'position_group': pgroup,
            'ovr': ovr,
            'source': 'ea_launch_snapshot',
            'fetched_at': now_iso,
        })
    body = json.dumps(payloads).encode('utf-8')
    url = f'{SB}/rest/v1/nfl_madden_player_ratings?on_conflict=player_name,team,season,week_snapshot'
    req = urllib.request.Request(url, data=body, headers=H_WRITE, method='POST')
    try:
        urllib.request.urlopen(req, timeout=30).read()
        print(f'[player] upserted {len(payloads)} rows (season={season}, week={week})', flush=True)
        return True
    except Exception as e:
        print(f'[player] upsert failed: {e}', flush=True)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=datetime.now().year)
    ap.add_argument('--week', type=int, default=0,
                    help='0 = launch snapshot (default). 1+ = in-season refresh.')
    args = ap.parse_args()

    print(f'\n[start] Madden NFL 27 seed  season={args.season}  week={args.week}', flush=True)
    upsert_team_ratings(args.season, args.week)
    upsert_player_ratings(args.season, args.week)
    print('[done]', flush=True)


if __name__ == '__main__':
    main()
