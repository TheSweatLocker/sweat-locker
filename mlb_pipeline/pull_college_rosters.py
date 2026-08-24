"""Universal college roster physicality pull — NCAAF + NCAAB (2026-08-23).

WHAT IT DOES
────────────
Scrapes ESPN's public teams + roster endpoints, computes team-level
physical + experience aggregates, upserts to roster_physicality.

Feeds shadow signals for:
  NCAAF: ncaaf_ol_weight_advantage, ncaaf_dl_weight_advantage,
         ncaaf_experience_edge_early_season
  NCAAB: ncaab_frontcourt_height_advantage, ncaab_experience_edge,
         ncaab_size_advantage

WHY IT EXISTS
─────────────
Roster physicality is theoretically load-bearing in college sports
where recruiting variance creates larger physical mismatches than
pros (where NFL/NBA scouting compresses the distribution). Weeks 1-3
of NCAAF specifically: freshman-heavy vs upperclass-heavy is a real
4-6pp ATS edge historically.

Shadow-mode only — signals fire but weights start low. Reweight after
30-45 days of graded evaluations via refresh_prop_signal_calibration.

USAGE
─────
    python pull_college_rosters.py --sport NCAAF                  # current season
    python pull_college_rosters.py --sport NCAAB                  # current season
    python pull_college_rosters.py --sport NCAAF --season 2026    # explicit
    python pull_college_rosters.py --sport NCAAF --team Alabama   # single team

Idempotent — on_conflict=(sport, team, season). Safe to re-run.

DATA SOURCE
───────────
ESPN's public site.web.api.espn.com endpoints (no auth, no rate limits
beyond politeness). Rosters refresh ~weekly during season, so cron
weekly is enough.

  NCAAF: apis/site/v2/sports/football/college-football/teams
  NCAAB: apis/site/v2/sports/basketball/mens-college-basketball/teams
  Roster: {base}/teams/{id}/roster

CAVEATS
───────
- ESPN rosters don't include DOB → avg_age is NULL for college.
  Class year (FR/SO/JR/SR/GR) is the proxy for experience.
- Position taxonomy differs by year in ESPN's data — normalizer maps
  variants to canonical groups.
- Some FCS/D1 mid-major teams aren't in ESPN's endpoint. Skipped
  silently — signal just returns None for those matchups.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ─── env bootstrap ─────────────────────────────────────────────
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

ESPN_HEADERS = {'User-Agent': 'Mozilla/5.0'}

# ─── sport config ──────────────────────────────────────────────
SPORT_CFG = {
    'NCAAF': {
        'teams_url': 'https://site.api.espn.com/apis/site/v2/sports/'
                     'football/college-football/teams?limit=500',
        'roster_url': ('https://site.web.api.espn.com/apis/site/v2/sports/'
                       'football/college-football/teams/{team_id}/roster'),
        'alias_table': 'ncaaf_team_aliases',
        # Position group taxonomy — normalize ESPN's per-player position
        # ("OT", "OG", "C" → "ol"; "DE", "DT", "NT" → "dl"; etc.)
        'position_map': {
            # Offensive line
            'OT': 'ol', 'OG': 'ol', 'C': 'ol', 'OL': 'ol', 'G': 'ol',
            # Defensive line
            'DE': 'dl', 'DT': 'dl', 'NT': 'dl', 'DL': 'dl',
            # Skill positions (grouped separately for optional future use)
            'QB': 'qb',
            'RB': 'rb', 'FB': 'rb', 'HB': 'rb',
            'WR': 'wr',
            'TE': 'te',
            'LB': 'lb', 'OLB': 'lb', 'ILB': 'lb', 'MLB': 'lb',
            'CB': 'db', 'S': 'db', 'FS': 'db', 'SS': 'db', 'DB': 'db',
            'K': 'st', 'P': 'st', 'LS': 'st',
        },
    },
    'NCAAB': {
        'teams_url': 'https://site.api.espn.com/apis/site/v2/sports/'
                     'basketball/mens-college-basketball/teams?limit=500',
        'roster_url': ('https://site.web.api.espn.com/apis/site/v2/sports/'
                       'basketball/mens-college-basketball/teams/{team_id}/roster'),
        'alias_table': 'ncaab_team_aliases',
        # Basketball position map — frontcourt = C/PF, backcourt = PG/SG/SF
        # SF straddles but treat as backcourt for perimeter shooting default
        'position_map': {
            'PG': 'backcourt', 'SG': 'backcourt', 'SF': 'backcourt', 'G': 'backcourt',
            'PF': 'frontcourt', 'C': 'frontcourt', 'F': 'frontcourt',
        },
    },
}

# ─── helpers ────────────────────────────────────────────────────
def _get(url: str, retries: int = 2) -> Optional[dict]:
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=ESPN_HEADERS)
            return json.loads(urllib.request.urlopen(req, timeout=15).read())
        except Exception:
            if attempt < retries:
                time.sleep(1.5 ** attempt)
                continue
            return None


_HT_RE = re.compile(r"(\d+)['′]\s*(\d+)?", flags=re.UNICODE)

def parse_height(raw) -> Optional[int]:
    """ESPN returns either "6' 2\"" string or inches int. Return inches."""
    if raw is None: return None
    if isinstance(raw, (int, float)): return int(raw)
    if isinstance(raw, str):
        m = _HT_RE.search(raw)
        if not m: return None
        ft = int(m.group(1))
        inch = int(m.group(2) or 0)
        return ft * 12 + inch
    return None


_CLASS_MAP = {
    'freshman': 1, 'fr': 1, 'redshirt freshman': 1, 'r-fr': 1,
    'sophomore': 2, 'so': 2, 'redshirt sophomore': 2, 'r-so': 2,
    'junior': 3, 'jr': 3, 'redshirt junior': 3, 'r-jr': 3,
    'senior': 4, 'sr': 4, 'redshirt senior': 4, 'r-sr': 4,
    'graduate': 5, 'gr': 5, 'grad': 5,
}

def parse_class(raw) -> Optional[int]:
    if not raw: return None
    s = str(raw).strip().lower()
    return _CLASS_MAP.get(s)


def normalize_position(pos: str, sport: str) -> Optional[str]:
    if not pos: return None
    return SPORT_CFG[sport]['position_map'].get(pos.upper())


def _build_espn_team_index(sport: str) -> dict:
    """Fetch ESPN's teams endpoint → {display_name_lower: espn_id}.

    ESPN caps each response at 500 teams regardless of limit param.
    CFB has 700+ teams across all divisions, so we paginate until we
    get an empty page. Missing this caused 14 FBS teams (Tennessee,
    TCU, Purdue, SMU, Texas Tech, ...) to silently skip on 8/24 first
    full scrape — 2026-08-24 fix.
    """
    cfg = SPORT_CFG[sport]
    idx = {}
    seen_teams = 0
    for page in range(1, 6):  # hard cap 6 pages / ~3000 teams
        url = f'{cfg["teams_url"]}&page={page}' if '?' in cfg['teams_url'] else f'{cfg["teams_url"]}?page={page}'
        print(f'[fetch] page {page}: {url}', flush=True)
        data = _get(url)
        if not data: break
        try:
            teams = data['sports'][0]['leagues'][0]['teams']
        except (KeyError, IndexError, TypeError):
            break
        if not teams: break
        for t in teams:
            team = t.get('team') or {}
            tid = str(team.get('id') or '')
            for key in ('displayName', 'name', 'nickname', 'location', 'shortDisplayName'):
                val = team.get(key)
                if val and tid:
                    idx[str(val).lower().strip()] = tid
        seen_teams += len(teams)
        if len(teams) < 500:  # last page reached
            break
    print(f'[index] {sport}: {len(idx)} name→id mappings from {seen_teams} teams', flush=True)
    return idx


def _load_aliases(sport: str) -> list[dict]:
    """Read canonical_name + name-variant fields. Schema differs per sport:
      NCAAF alias table: full_name, location, nickname, alt_names
      NCAAB alias table: odds_api_name, kenpom_name, bart_name, alt_names, espn_id
    """
    cfg = SPORT_CFG[sport]
    if sport == 'NCAAB':
        select = 'canonical_name,odds_api_name,kenpom_name,bart_name,alt_names,espn_id'
    else:  # NCAAF
        select = 'canonical_name,full_name,location,nickname,alt_names'
    r = urllib.request.Request(
        f'{SB}/rest/v1/{cfg["alias_table"]}?select={select}',
        headers=H_READ,
    )
    try:
        return json.loads(urllib.request.urlopen(r, timeout=15).read())
    except Exception as e:
        print(f'[error] alias load failed: {e}', flush=True)
        return []


def match_team_to_espn_id(alias: dict, espn_idx: dict) -> Optional[str]:
    """Fast-path: if alias row already has espn_id (NCAAB), use it.
    Fallback: try full_name/location/nickname/canonical_name/alt_names
    against the ESPN name→id index (NCAAF path)."""
    # Fast path — trust seeded espn_id if present
    seeded = alias.get('espn_id')
    if seeded:
        return str(seeded)
    candidates = []
    for key in ('full_name', 'odds_api_name', 'location',
                'nickname', 'kenpom_name', 'bart_name', 'canonical_name'):
        v = alias.get(key)
        if v: candidates.append(v)
    for a in (alias.get('alt_names') or []):
        candidates.append(a)
    for c in candidates:
        if not c: continue
        k = str(c).lower().strip()
        if k in espn_idx:
            return espn_idx[k]
    return None


def fetch_roster(sport: str, team_id: str) -> list[dict]:
    """Fetch roster for one team → list of player dicts."""
    cfg = SPORT_CFG[sport]
    url = cfg['roster_url'].format(team_id=team_id)
    data = _get(url)
    if not data:
        return []
    players = []
    # ESPN response: athletes[] where each element may be a grouped bucket
    # (with athletes: [...] sub-list) OR a bare athlete. Handle both.
    for group in data.get('athletes') or []:
        if isinstance(group, dict) and 'items' in group:
            for a in group.get('items') or []:
                players.append(a)
        elif isinstance(group, dict) and 'athletes' in group:
            for a in group.get('athletes') or []:
                players.append(a)
        elif isinstance(group, dict):
            players.append(group)
    return players


def aggregate_roster(players: list[dict], sport: str) -> dict:
    """Compute team-level physicality metrics from player list."""
    cfg = SPORT_CFG[sport]

    heights, weights, class_years = [], [], []
    # position groups: {group_name: {"hts":[], "wts":[], "classes":[]}}
    groups: dict[str, dict] = {}

    for p in players:
        # Physical
        ht = parse_height(p.get('displayHeight') or p.get('height'))
        wt = None
        w_raw = p.get('displayWeight') or p.get('weight')
        if w_raw:
            m = re.search(r'\d+', str(w_raw))
            if m:
                try: wt = int(m.group(0))
                except ValueError: pass
        # Class year
        cls_raw = None
        exp = p.get('experience')
        if isinstance(exp, dict):
            cls_raw = exp.get('displayValue') or exp.get('abbreviation')
        cy = parse_class(cls_raw)

        # Position
        pos_raw = None
        pos_field = p.get('position')
        if isinstance(pos_field, dict):
            pos_raw = pos_field.get('abbreviation') or pos_field.get('name')
        elif isinstance(pos_field, str):
            pos_raw = pos_field

        group = normalize_position(pos_raw, sport)

        if ht: heights.append(ht)
        if wt: weights.append(wt)
        if cy: class_years.append(cy)

        if group:
            g = groups.setdefault(group, {'hts': [], 'wts': [], 'classes': []})
            if ht: g['hts'].append(ht)
            if wt: g['wts'].append(wt)
            if cy: g['classes'].append(cy)

    def _avg(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    upper = [c for c in class_years if c >= 3]
    pct_up = round(len(upper) / len(class_years), 3) if class_years else None

    pos_groups_out = {}
    for g, data in groups.items():
        pos_groups_out[g] = {
            'n': len(data['hts']) or len(data['wts']),
            'avg_ht_in': _avg(data['hts']),
            'avg_wt_lb': _avg(data['wts']),
            'avg_class': _avg(data['classes']),
        }

    return {
        'n_players': len(players),
        'avg_ht_in': _avg(heights),
        'avg_wt_lb': _avg(weights),
        'avg_class_year': _avg(class_years),
        'pct_upperclass': pct_up,
        'position_groups': pos_groups_out,
    }


def upsert_row(sport: str, team: str, season: int, agg: dict, team_id: str):
    payload = {
        'sport': sport,
        'team': team,
        'season': season,
        'n_players': agg['n_players'],
        'avg_ht_in': agg['avg_ht_in'],
        'avg_wt_lb': agg['avg_wt_lb'],
        'avg_class_year': agg['avg_class_year'],
        'pct_upperclass': agg['pct_upperclass'],
        'position_groups': agg['position_groups'],
        'source': 'espn',
        'source_url': f'espn_team_id={team_id}',
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }
    body = json.dumps([payload]).encode('utf-8')
    url = f'{SB}/rest/v1/roster_physicality?on_conflict=sport,team,season'
    req = urllib.request.Request(url, data=body, headers=H_WRITE, method='POST')
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception as e:
        print(f'[write-fail] {team}: {e}', flush=True)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, choices=['NCAAF', 'NCAAB'])
    ap.add_argument('--season', type=int, default=datetime.now().year)
    ap.add_argument('--team', help='Single team canonical_name (for testing)')
    ap.add_argument('--sleep', type=float, default=0.35,
                    help='Sleep between per-team roster fetches (politeness)')
    args = ap.parse_args()

    sport = args.sport
    print(f'\n[start] {sport} roster pull season={args.season}', flush=True)

    espn_idx = _build_espn_team_index(sport)
    if not espn_idx:
        print('[abort] no ESPN team index', flush=True)
        return

    aliases = _load_aliases(sport)
    if args.team:
        aliases = [a for a in aliases if a['canonical_name'] == args.team]
        if not aliases:
            print(f'[abort] team {args.team} not in aliases', flush=True)
            return
    print(f'[aliases] {len(aliases)} teams to process', flush=True)

    ok, skip, fail = 0, 0, 0
    for i, alias in enumerate(aliases, 1):
        team = alias['canonical_name']
        tid = match_team_to_espn_id(alias, espn_idx)
        if not tid:
            skip += 1
            if i % 25 == 0:
                print(f'[progress] {i}/{len(aliases)} ok={ok} skip={skip} fail={fail}', flush=True)
            continue
        players = fetch_roster(sport, tid)
        if not players:
            skip += 1
            continue
        agg = aggregate_roster(players, sport)
        if upsert_row(sport, team, args.season, agg, tid):
            ok += 1
        else:
            fail += 1
        time.sleep(args.sleep)
        if i % 25 == 0:
            print(f'[progress] {i}/{len(aliases)} ok={ok} skip={skip} fail={fail}', flush=True)

    print(f'\n[done] {sport} ok={ok} skip={skip} fail={fail}', flush=True)


if __name__ == '__main__':
    main()
