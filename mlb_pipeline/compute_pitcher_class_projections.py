"""
Pitcher Offense-Class Projections (Phase A — 2026-05-10)

For each starter on tonight's slate, build a class-bucketed projection of
expected IP / ER / Outs based on the pitcher's actual historical performance
against offenses of similar quality (defined by current opponent wRC+ band).

Output: writes JSON cache to `data/pitcher_class_projections.json` keyed by
pitcher_id. Scout report and (later) generate_props.py read from it.

Why this matters: current ER + Outs prop scoring uses season-level xERA + opp
wRC+ as a single signal. This adds a "vs similar opponents" reality check that
captures pitcher's actual matchup-quality dependence — same logic that powers
the pitcher_vs_team mastery layer, but generalized to opponent CLASS not just
opponent identity. Sample size is much larger (every opponent counts) so
projections work even for first-time matchups.

Pragmatic shortcut: use CURRENT season wRC+ as proxy for "opponent quality at
time of historical game." Less accurate for early-season games (lineups shift)
but feasible to compute today without backfilling historical wRC+ snapshots.

Run order: nightly after team_stats.py refresh, before generate_props.py.
"""
import os
import sys
import json
import time
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

HEADERS = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
CACHE_PATH = Path(__file__).parent / 'data' / 'pitcher_class_projections.json'

# wRC+ class buckets — asymmetric to give better resolution at common opp levels
CLASS_BUCKETS = [
    ('le_90', float('-inf'), 90),
    ('91_100', 91, 100),
    ('101_110', 101, 110),
    ('111_120', 111, 120),
    ('ge_121', 121, float('inf')),
]


def _ip_to_float(ip_str):
    """MLB encodes 6.1 IP as 6.333, 6.2 IP as 6.667."""
    s = str(ip_str or '0')
    return float(s.replace('.1', '.333').replace('.2', '.667') or '0')


def fetch_active_starters_with_ids():
    """Pull tonight's starters with MLB IDs directly from the schedule API.
    mlb_game_context stores names but not pitcher IDs, so we fetch live."""
    today = datetime.now().strftime('%Y-%m-%d')
    try:
        r = requests.get(
            'https://statsapi.mlb.com/api/v1/schedule',
            params={
                'sportId': 1,
                'date': today,
                'hydrate': 'probablePitcher',
            },
            timeout=15,
        )
        data = r.json()
        starters = []
        for game in data.get('dates', [{}])[0].get('games', []):
            for side in ('home', 'away'):
                team_obj = game.get('teams', {}).get(side, {})
                p = team_obj.get('probablePitcher') or {}
                pid = p.get('id')
                pname = p.get('fullName')
                team_name = team_obj.get('team', {}).get('name')
                if pid and pname:
                    starters.append({'name': pname, 'mlb_id': pid, 'team': team_name})
        return starters
    except Exception as e:
        print(f'  Schedule fetch failed: {e}')
        return []


def fetch_team_wrc_lookup():
    """Build {team_id → current season wRC+} from MLB Stats API team-level stats.

    Use the same source as team_stats.py: hitting stats by team for current
    season. Falls back to 100 (league avg) for missing teams.
    """
    season = datetime.now().year
    try:
        r = requests.get(
            'https://statsapi.mlb.com/api/v1/teams/stats',
            params={
                'sportIds': 1,
                'season': season,
                'group': 'hitting',
                'stats': 'season',
            },
            timeout=15,
        )
        data = r.json()
        # Stats API returns by team — extract OPS-like proxy if wRC+ not directly given
        # Actual wRC+ requires woba/league_woba math; for proxy we'll use OPS+ analog
        # OR pull from wOBA which IS in the response.
        lookup = {}
        for split in data.get('stats', [{}])[0].get('splits', []):
            team = split.get('team', {})
            stat = split.get('stat', {})
            tid = team.get('id')
            ops = float(stat.get('ops', 0) or 0)
            # Convert OPS to wRC+-like approximation: league OPS ~0.720
            # wRC+ ≈ (OPS / 0.720) * 100
            if tid and ops > 0:
                lookup[tid] = round((ops / 0.720) * 100)
        return lookup
    except Exception as e:
        print(f'  Failed team wRC+ lookup: {e}')
        return {}


def fetch_pitcher_gamelog(pitcher_id):
    """Pull pitcher's gameLog from current + prior season. Returns list of
    per-game stat dicts with opponent_id."""
    games = []
    for season in (datetime.now().year, datetime.now().year - 1):
        try:
            r = requests.get(
                f'https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats',
                params={'stats': 'gameLog', 'group': 'pitching', 'season': season},
                timeout=12,
            )
            stats_block = r.json().get('stats', [])
            splits = stats_block[0].get('splits', []) if stats_block else []
            for sp in splits:
                stat = sp.get('stat', {})
                opp_id = sp.get('opponent', {}).get('id')
                ip = _ip_to_float(stat.get('inningsPitched', '0'))
                if ip < 1.0:  # skip relief / very short outings
                    continue
                games.append({
                    'opp_id': opp_id,
                    'ip': ip,
                    'er': int(stat.get('earnedRuns', 0) or 0),
                    'k': int(stat.get('strikeOuts', 0) or 0),
                    'bb': int(stat.get('baseOnBalls', 0) or 0),
                    'hits': int(stat.get('hits', 0) or 0),
                    'hr': int(stat.get('homeRuns', 0) or 0),
                    'date': sp.get('date'),
                    'season': season,
                })
        except Exception:
            continue
    return games


def bucket_for_wrc(wrc):
    """Return bucket label for a given wRC+ value."""
    if wrc is None:
        return None
    for label, lo, hi in CLASS_BUCKETS:
        if lo <= wrc <= hi:
            return label
    return None


def aggregate_by_class(games, team_wrc_lookup):
    """Aggregate pitcher's games by opponent wRC+ class. Returns dict keyed
    by bucket label with avg IP / ER / Outs / K / BB / Hits / HR plus n."""
    buckets = {label: {'ip': 0.0, 'er': 0, 'k': 0, 'bb': 0, 'hits': 0, 'hr': 0, 'n': 0}
               for label, _, _ in CLASS_BUCKETS}
    for g in games:
        opp_id = g.get('opp_id')
        opp_wrc = team_wrc_lookup.get(opp_id)
        bucket = bucket_for_wrc(opp_wrc)
        if not bucket:
            continue
        b = buckets[bucket]
        b['ip'] += g['ip']
        b['er'] += g['er']
        b['k'] += g['k']
        b['bb'] += g['bb']
        b['hits'] += g.get('hits', 0)
        b['hr'] += g['hr']
        b['n'] += 1

    # Compute averages, only emit buckets with n >= 2
    out = {}
    for label, raw in buckets.items():
        if raw['n'] < 2:
            continue
        out[label] = {
            'n': raw['n'],
            'avg_ip': round(raw['ip'] / raw['n'], 2),
            'avg_er': round(raw['er'] / raw['n'], 2),
            'avg_outs': round(raw['ip'] / raw['n'] * 3, 1),  # outs = IP * 3
            'avg_k': round(raw['k'] / raw['n'], 2),
            'avg_bb': round(raw['bb'] / raw['n'], 2),
            'avg_hits': round(raw['hits'] / raw['n'], 2),
            'avg_hr': round(raw['hr'] / raw['n'], 2),
            'era_in_class': round((raw['er'] * 9.0) / raw['ip'], 2) if raw['ip'] > 0 else None,
        }
    return out


def aggregate_l7_rolling(games):
    """Most recent 7 starts: avg BB and H per start + BB/9 and H/9.
    Used to surface 'has allowed less than 2 BB per game over L7 starts' style
    signals for pitcher BB / H prop scoring."""
    if not games:
        return None
    # Sort by date descending, take last 7 with valid date
    dated = [g for g in games if g.get('date')]
    dated.sort(key=lambda g: g['date'], reverse=True)
    recent = dated[:7]
    if len(recent) < 3:
        return None
    total_ip = sum(g['ip'] for g in recent)
    total_bb = sum(g['bb'] for g in recent)
    total_hits = sum(g.get('hits', 0) for g in recent)
    total_k = sum(g['k'] for g in recent)
    total_er = sum(g['er'] for g in recent)
    n = len(recent)
    return {
        'n_starts': n,
        'avg_ip': round(total_ip / n, 2),
        'avg_bb': round(total_bb / n, 2),
        'avg_hits': round(total_hits / n, 2),
        'avg_k': round(total_k / n, 2),
        'avg_er': round(total_er / n, 2),
        'bb_per_9': round((total_bb * 9.0) / total_ip, 2) if total_ip > 0 else None,
        'hits_per_9': round((total_hits * 9.0) / total_ip, 2) if total_ip > 0 else None,
        'k_per_9': round((total_k * 9.0) / total_ip, 2) if total_ip > 0 else None,
        'era': round((total_er * 9.0) / total_ip, 2) if total_ip > 0 else None,
        'whip': round((total_bb + total_hits) / total_ip, 2) if total_ip > 0 else None,  # (BB+H)/IP — baserunner traffic
    }


def run():
    print(f'=== Pitcher Class Projections {datetime.now().strftime("%Y-%m-%d")} ===')
    starters = fetch_active_starters_with_ids()
    print(f'Active starters tonight: {len(starters)}')
    if not starters:
        print('No starters found — aborting')
        return

    print('Building team wRC+ lookup...')
    team_wrc = fetch_team_wrc_lookup()
    print(f'  Got wRC+ for {len(team_wrc)} teams')
    if not team_wrc:
        print('  No team wRC+ data — aborting (would produce no buckets)')
        return

    projections = {}
    for s in starters:
        try:
            games = fetch_pitcher_gamelog(s['mlb_id'])
            if len(games) < 5:
                print(f'  ⚠️  {s["name"]}: only {len(games)} historical starts — skipping')
                continue
            class_agg = aggregate_by_class(games, team_wrc)
            l7_rolling = aggregate_l7_rolling(games)
            if not class_agg and not l7_rolling:
                print(f'  ⚠️  {s["name"]}: no class buckets met n≥2 and no L7 sample — skipping')
                continue
            projections[str(s['mlb_id'])] = {
                'name': s['name'],
                'team': s['team'],
                'total_starts_analyzed': len(games),
                'classes': class_agg,
                'l7_rolling': l7_rolling,
                'updated_at': datetime.now().isoformat(),
            }
            classes_str = ', '.join(f'{c}=n{v["n"]}/avg {v["avg_ip"]}IP/{v["avg_er"]}ER/{v["avg_bb"]}BB/{v["avg_hits"]}H' for c, v in class_agg.items())
            l7_str = ''
            if l7_rolling:
                l7_str = f' | L7({l7_rolling["n_starts"]}): {l7_rolling["avg_bb"]}BB/{l7_rolling["avg_hits"]}H/{l7_rolling["avg_k"]}K per start'
            print(f'  ✅ {s["name"]} ({len(games)} starts): {classes_str}{l7_str}')
            time.sleep(0.4)  # rate limit MLB API
        except Exception as e:
            print(f'  ❌ {s["name"]}: {e}')

    # Write JSON cache (used by generate_props.py + scout_report.py locally)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, 'w') as f:
        json.dump(projections, f, indent=2)
    print(f'\nWrote {len(projections)} pitcher projections to {CACHE_PATH}')

    # Upsert to Supabase so the app can render the Pitcher Scouting panel
    # on the game detail screen. Table: pitcher_projections
    #   CREATE TABLE pitcher_projections (
    #     pitcher_name TEXT PRIMARY KEY,
    #     mlb_id INTEGER, team TEXT,
    #     l7_rolling JSONB, classes JSONB,
    #     total_starts_analyzed INTEGER,
    #     updated_at TIMESTAMPTZ DEFAULT NOW()
    #   );
    if SUPABASE_URL and SUPABASE_KEY and projections:
        rows = []
        for pid, p in projections.items():
            rows.append({
                'pitcher_name': p['name'],
                'mlb_id': int(pid) if str(pid).isdigit() else None,
                'team': p.get('team'),
                'l7_rolling': p.get('l7_rolling'),
                'classes': p.get('classes'),
                'total_starts_analyzed': p.get('total_starts_analyzed'),
                'updated_at': datetime.now().isoformat(),
            })
        try:
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/pitcher_projections?on_conflict=pitcher_name",
                headers={**HEADERS, 'Content-Type': 'application/json',
                         'Prefer': 'resolution=merge-duplicates,return=minimal'},
                json=rows, timeout=20,
            )
            if r.status_code in (200, 201, 204):
                print(f"  ✅ Upserted {len(rows)} rows to pitcher_projections")
            else:
                print(f"  ⚠️ pitcher_projections upsert {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"  ⚠️ pitcher_projections upsert failed: {e}")


if __name__ == '__main__':
    run()
