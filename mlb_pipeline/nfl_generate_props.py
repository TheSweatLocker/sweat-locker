"""NFL prop-projection generator — Phase 3 Week 1 launch target.

For every player prop offered on today's + upcoming NFL slate:
  1. Pull the market line from Odds API (americanfootball_nfl props markets)
  2. Look up player's L4 + season averages from nfl_player_stats
  3. Apply opponent defensive adjustment from nfl_team_stats
  4. Compare projected vs line → OVER/UNDER pick with tier gate
  5. Upsert to nfl_props with structured signals blob for Jerry

Prop markets covered (v1, matches PROP_MARKETS.NFL in app):
  - player_pass_yds      → passing_yards
  - player_rush_yds      → rushing_yards
  - player_reception_yds → receiving_yards
  - player_receptions    → receptions
  - player_anytime_td    → any TD (v1.1 — needs snap counts / red-zone target share)

v1 blend formula:
  projected = 0.60 * l4_avg + 0.35 * season_avg + 0.05 * league_baseline
    then multiplied by opp_adj:
      opp_adj = 1 + (opp_rank_pct - 0.5) * 0.15
    (i.e. worst-defense opponent → +7.5%, best-defense opponent → -7.5%)

Tier thresholds (calibrate post-Week-4 with live audit):
  edge >= line * 0.15 → PRIME       (or exactly 15pct)
  edge >= line * 0.10 → STRONG
  edge >= line * 0.06 → LIGHT
  else → skip

Sign conventions:
  edge = projected - line              (OVER lens)
  For UNDER: use pick_side=UNDER when edge < 0 with same absolute threshold.

Anytime-TD scaffold present but returns 0 picks in v1 (needs red-zone
target share which is not yet computed server-side; ship v1.1).

Usage:
    python nfl_generate_props.py               # today + upcoming week
    python nfl_generate_props.py --dry-run
    python nfl_generate_props.py --player 'Patrick Mahomes'   # test one player
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
ODDS_KEY = os.environ.get('ODDS_API_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

ODDS_API_BASE = 'https://api.the-odds-api.com/v4/sports'


# ─────────────────────────────────────────────────────────────
# Prop registry — maps Odds API market key → player_stats column
# ─────────────────────────────────────────────────────────────
PROP_CONFIG = {
    'player_pass_yds': {
        'col': 'passing_yards',
        'position': 'QB',
        'league_baseline': 235.0,     # league avg per game
        'opp_col': 'def_pass_def',    # higher pass_def = tougher for QB
        'label': 'Pass Yds',
    },
    'player_rush_yds': {
        'col': 'rushing_yards',
        'position': 'RB',
        'league_baseline': 55.0,
        'opp_col': 'def_sacks',       # loose proxy — sackier defenses stop the run too
        'label': 'Rush Yds',
    },
    'player_reception_yds': {
        'col': 'receiving_yards',
        'position': None,             # WR/TE/RB — filter looser
        'league_baseline': 42.0,
        'opp_col': 'def_pass_def',
        'label': 'Rec Yds',
    },
    'player_receptions': {
        'col': 'receptions',
        'position': None,
        'league_baseline': 3.2,
        'opp_col': 'def_pass_def',
        'label': 'Receptions',
    },
}


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(v) if v is not None else None
    except (TypeError, ValueError): return None


def load_alias_map() -> dict:
    r = requests.get(
        f'{SB}/rest/v1/nfl_team_aliases?select=canonical_name,odds_api_name,full_name',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200: return {}
    aliases = {}
    for row in r.json():
        canonical = row.get('canonical_name')
        for field in ('odds_api_name', 'full_name'):
            n = row.get(field)
            if n and canonical: aliases[n] = canonical
    return aliases


def load_opponent_defense(season: int) -> dict:
    """Map team → defensive stat dict for opponent-adjustment."""
    r = requests.get(
        f'{SB}/rest/v1/nfl_team_stats?season=eq.{season}&season_type=eq.REG&select=team,def_sacks,def_ints,def_pass_def',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200: return {}
    return {row['team']: row for row in r.json()}


def opp_rank(opp_map: dict, opp_team: str, opp_col: str) -> Optional[float]:
    """Return 0.0 (best defense) → 1.0 (worst) rank as fraction. None if data missing."""
    if opp_team not in opp_map: return None
    values = sorted(
        [(t, (row.get(opp_col) or 0)) for t, row in opp_map.items()],
        key=lambda x: -x[1],   # higher = better defense for this stat
    )
    for i, (t, _) in enumerate(values):
        if t == opp_team:
            return i / max(1, len(values) - 1)   # 0.0 = best, 1.0 = worst
    return None


def player_rolling(player_id: str, prop_col: str, current_season: int) -> tuple:
    """Return (l4_avg, season_avg, games_played) for a player-prop.
    Reads from nfl_player_stats — pulls most recent 20 rows, computes L4 + season."""
    r = requests.get(
        f'{SB}/rest/v1/nfl_player_stats'
        f'?player_id=eq.{player_id}'
        f'&order=season.desc,week.desc'
        f'&limit=20'
        f'&select=season,week,{prop_col}',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200:
        return None, None, 0
    rows = r.json() or []
    if not rows:
        return None, None, 0
    vals = [_f(r.get(prop_col)) for r in rows if r.get(prop_col) is not None]
    if not vals:
        return None, None, 0

    l4 = vals[:4]
    l4_avg = round(sum(l4) / len(l4), 2) if l4 else None
    # Season avg = current season only
    season_vals = [_f(r.get(prop_col)) for r in rows
                   if r.get('season') == current_season and r.get(prop_col) is not None]
    season_avg = round(sum(season_vals) / len(season_vals), 2) if season_vals else None
    return l4_avg, season_avg, len(vals)


def project(l4_avg: Optional[float], season_avg: Optional[float],
            league_baseline: float, opp_rank_pct: Optional[float]) -> Optional[float]:
    """Blend L4 (60%) + season (35%) + league baseline (5%), then opp-adjust."""
    if l4_avg is None and season_avg is None:
        return None
    l4 = l4_avg if l4_avg is not None else (season_avg or league_baseline)
    season = season_avg if season_avg is not None else (l4_avg or league_baseline)
    base = 0.60 * l4 + 0.35 * season + 0.05 * league_baseline
    if opp_rank_pct is not None:
        opp_adj = 1.0 + (opp_rank_pct - 0.5) * 0.15
        base *= opp_adj
    return round(base, 2)


def tier_from_edge_pct(edge_pct: float) -> Optional[str]:
    """Edge as fraction of line: 0.15 = 15% above/below → PRIME."""
    a = abs(edge_pct)
    if a >= 0.15: return 'PRIME'
    if a >= 0.10: return 'STRONG'
    if a >= 0.06: return 'LIGHT'
    return None


def conviction_from_edge_pct(edge_pct: float) -> int:
    a = abs(edge_pct)
    return min(100, max(30, int(50 + a * 300)))


# ─────────────────────────────────────────────────────────────
# Odds API
# ─────────────────────────────────────────────────────────────
def fetch_events(sport_key: str = 'americanfootball_nfl') -> list:
    if not ODDS_KEY: return []
    r = requests.get(
        f'{ODDS_API_BASE}/{sport_key}/events'
        f'?apiKey={ODDS_KEY}',
        timeout=15,
    )
    if r.status_code != 200:
        print(f'  ⚠ events {sport_key}: {r.status_code}')
        return []
    return r.json()


def fetch_event_props(event_id: str, sport_key: str) -> dict:
    """Player-prop markets require the per-event endpoint (Odds API v4).
    Uses paid quota — 1 call per event × per market batch."""
    markets = ','.join(PROP_CONFIG.keys())
    r = requests.get(
        f'{ODDS_API_BASE}/{sport_key}/events/{event_id}/odds'
        f'?apiKey={ODDS_KEY}&regions=us&markets={markets}&oddsFormat=american',
        timeout=15,
    )
    if r.status_code != 200:
        return {}
    return r.json()


def player_id_lookup(name: str, position: Optional[str] = None) -> Optional[dict]:
    """Fuzzy lookup player_id + team from nfl_player_stats latest season.
    Returns {player_id, player_name, team, position} or None."""
    # Odds API uses common name (Patrick Mahomes), nflverse uses same.
    # Filter by position when we know it, order by season desc so we get most recent team.
    q = f'{SB}/rest/v1/nfl_player_stats?player_name=ilike.{name}&order=season.desc,week.desc&limit=1&select=player_id,player_name,team,position'
    if position:
        q += f'&position=eq.{position}'
    r = requests.get(q, headers=H_READ, timeout=15)
    if r.status_code == 200 and r.json():
        return r.json()[0]
    return None


# ─────────────────────────────────────────────────────────────
# Row build + orchestration
# ─────────────────────────────────────────────────────────────
def build_prop_row(event: dict, market: dict, outcome: dict, opp_map: dict,
                   aliases: dict, season: int) -> Optional[dict]:
    """Build one nfl_props row from an Odds API prop outcome."""
    market_key = market.get('key')
    cfg = PROP_CONFIG.get(market_key)
    if not cfg: return None

    player_name = outcome.get('description')
    line = _f(outcome.get('point'))
    side = (outcome.get('name') or '').upper()   # 'Over' | 'Under'
    odds = _i(outcome.get('price'))
    if not player_name or line is None or side not in ('OVER', 'UNDER'):
        return None

    # Player lookup
    player = player_id_lookup(player_name, position=cfg['position'])
    if not player:
        return None
    player_id = player['player_id']
    player_team = player['team']
    position = player.get('position')

    # Opponent = whichever team isn't the player's
    home_raw = event.get('home_team'); away_raw = event.get('away_team')
    home_canon = aliases.get(home_raw); away_canon = aliases.get(away_raw)
    if not home_canon or not away_canon: return None
    opp_team = away_canon if player_team == home_canon else home_canon

    l4, season_avg, gp = player_rolling(player_id, cfg['col'], season)
    if l4 is None and season_avg is None:
        return None
    opp_pct = opp_rank(opp_map, opp_team, cfg['opp_col'])
    proj = project(l4, season_avg, cfg['league_baseline'], opp_pct)
    if proj is None: return None

    edge = round(proj - line, 2)
    edge_pct = edge / line if line > 0 else 0
    # For UNDER picks: flip sign — model says lower → UNDER edge
    directional_edge = edge_pct if side == 'OVER' else -edge_pct
    if directional_edge <= 0:
        # Wrong-side pick — skip
        return None

    tier = tier_from_edge_pct(directional_edge)
    if not tier: return None
    conv = conviction_from_edge_pct(directional_edge)

    return {
        'game_id': event.get('id'),
        'game_date': (event.get('commence_time') or '')[:10],
        'season': season,
        'week': None,   # nflverse week schedule join (Phase 3.1)
        'home_team': home_canon,
        'away_team': away_canon,
        'player_id': player_id,
        'player_name': player['player_name'],
        'team': player_team,
        'opponent_team': opp_team,
        'position': position,
        'prop_type': market_key.replace('player_', ''),
        'pick_side': side,
        'pick_line': line,
        'odds_american': odds,
        'projected': proj,
        'edge': edge,
        'l4_avg': l4,
        'season_avg': season_avg,
        'opp_rank': int(round(opp_pct * 32)) if opp_pct is not None else None,
        'tier': tier,
        'conviction': conv,
        'signals': {
            'l4': l4, 'season_avg': season_avg,
            'league_baseline': cfg['league_baseline'],
            'opp_pct': opp_pct, 'opp_col': cfg['opp_col'],
            'edge_pct': round(directional_edge * 100, 1),
            'games_used': gp,
            'label': cfg['label'],
        },
    }


def upsert_props(rows: list, dry_run: bool = False) -> int:
    if not rows: return 0
    if dry_run:
        for r in rows:
            print(f"  [DRY] {r['player_name']:22} {r['prop_type']:16} "
                  f"{r['pick_side']:5} {r['pick_line']:>5.1f}  "
                  f"proj={r['projected']:>5.1f} edge={r['edge']:+.1f}  "
                  f"tier={r['tier']:<6} conv={r['conviction']}")
        return len(rows)
    r = requests.post(
        f'{SB}/rest/v1/nfl_props?on_conflict=game_id,player_name,prop_type,pick_side',
        headers=H_WRITE, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(rows)


def run(dry_run: bool = False, single_player: Optional[str] = None) -> None:
    print(f'=== NFL props generator · {_et_now().date()} ===')
    if not ODDS_KEY:
        print('  ✗ ODDS_API_KEY missing — abort')
        return

    aliases = load_alias_map()
    if not aliases:
        print('  ✗ nfl_team_aliases empty')
        return

    season = _et_now().year if _et_now().month >= 9 else _et_now().year - 1
    opp_map = load_opponent_defense(season)
    print(f'  aliases={len(aliases)}  opp_map={len(opp_map)} teams  season={season}')

    events = fetch_events('americanfootball_nfl') + fetch_events('americanfootball_nfl_preseason')
    print(f'  events with props potentially available: {len(events)}')

    all_rows = []
    events_with_props = 0
    for evt in events:
        eid = evt.get('id')
        if not eid: continue
        # NOTE: fetch_event_props hits paid Odds API quota. Cap at reasonable
        # per-run — in production we'd only pull events kicking off in next 24h.
        commence = evt.get('commence_time', '')
        try:
            dt = datetime.fromisoformat(commence.replace('Z', '+00:00'))
            hrs_out = (dt - datetime.now(timezone.utc)).total_seconds() / 3600
            if hrs_out > 168 or hrs_out < -1:  # only next 7 days
                continue
        except Exception:
            continue

        event_data = fetch_event_props(eid, 'americanfootball_nfl')
        markets_seen = 0
        for book in (event_data.get('bookmakers') or []):
            for market in (book.get('markets') or []):
                if market.get('key') not in PROP_CONFIG:
                    continue
                markets_seen += 1
                for outcome in market.get('outcomes') or []:
                    if single_player and single_player.lower() not in (outcome.get('description') or '').lower():
                        continue
                    row = build_prop_row(evt, market, outcome, opp_map, aliases, season)
                    if row:
                        all_rows.append(row)
            if markets_seen: break   # first book that has props is enough
        if markets_seen:
            events_with_props += 1

    print(f'  events with props pulled: {events_with_props}')
    print(f'  prop picks generated: {len(all_rows)}')

    # Dedup on (game_id, player_name, prop_type, pick_side) — keep highest conv
    dedup: dict = {}
    for r in all_rows:
        key = (r['game_id'], r['player_name'], r['prop_type'], r['pick_side'])
        if key not in dedup or (dedup[key]['conviction'] or 0) < (r['conviction'] or 0):
            dedup[key] = r
    rows = list(dedup.values())

    written = upsert_props(rows, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}wrote {written} rows to nfl_props')

    by_tier = defaultdict(int)
    for r in rows:
        by_tier[r['tier']] += 1
    print(f'  by tier: {dict(by_tier)}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--player', default=None, help='Test one player (partial match)')
    args = ap.parse_args()
    run(dry_run=args.dry_run, single_player=args.player)


if __name__ == '__main__':
    main()
