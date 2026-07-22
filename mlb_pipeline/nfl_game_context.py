"""NFL game context pipeline — server-side analog of mlb_game_context /
ncaab_game_context.

Pulls today's + upcoming NFL games from the Odds API, joins nfl_team_stats,
computes EPA-based projections + signal confluence + sweat score +
primary_play, and upserts to nfl_game_context.

Model (v1 — Week 1 baseline, iterates once we have live 2026 data):
  home_off_rating = (pass_epa + rush_epa) / games         (from nfl_team_stats)
  power_diff       = home_off_rating - away_off_rating
  projected_spread = power_diff * K_PTS + HOME_FIELD      (positive = home fav)
  projected_total  = base_total + weather/roof adjustments

Cohort tags computed inline (mirrors nfl_cohort_backfill logic).

Sign convention: nflverse standard (close_spread > 0 = home favored).
nfl_odds_pull.py flips Odds API native at write time to match. This
script assumes nflverse convention throughout.

Usage:
    python nfl_game_context.py             # today + next 7 days
    python nfl_game_context.py --dry-run

Required env: SUPABASE_URL, SUPABASE_KEY, ODDS_API_KEY
"""
import argparse
import os
import sys
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

# Calibration constants (revisit after Week 4 with live 2026 data)
K_PTS = 0.15               # EPA-diff → spread points scaling
HOME_FIELD_PTS = 2.2       # league-avg HFA in modern era
BASE_TOTAL = 44.5          # 2022-2025 league-avg total
DOME_ADJ = 1.5             # dome → +1.5 total
COLD_ADJ = -2.0            # temp <= 35°F → -2.0 total
WIND_ADJ = -1.5            # wind >= 15 → -1.5 total

# From nfl_cohort_backfill
NFL_DIVISION = {
    'BUF':'AFC East','MIA':'AFC East','NE':'AFC East','NYJ':'AFC East',
    'BAL':'AFC North','CIN':'AFC North','CLE':'AFC North','PIT':'AFC North',
    'HOU':'AFC South','IND':'AFC South','JAX':'AFC South','TEN':'AFC South',
    'DEN':'AFC West','KC':'AFC West','LV':'AFC West','LAC':'AFC West',
    'DAL':'NFC East','NYG':'NFC East','PHI':'NFC East','WAS':'NFC East',
    'CHI':'NFC North','DET':'NFC North','GB':'NFC North','MIN':'NFC North',
    'ATL':'NFC South','CAR':'NFC South','NO':'NFC South','TB':'NFC South',
    'ARI':'NFC West','LA':'NFC West','SF':'NFC West','SEA':'NFC West',
}

WEST_COAST = {'LA', 'LAC', 'SF', 'SEA'}


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


def load_team_stats(season: int) -> dict:
    """Return dict[team_abbrev → stats row] for the given season."""
    r = requests.get(
        f'{SB}/rest/v1/nfl_team_stats?season=eq.{season}&season_type=eq.REG&select=*',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200: return {}
    return {row['team']: row for row in r.json()}


def compute_off_rating(stats: dict) -> Optional[float]:
    """EPA per game (pass + rush). Higher = better offense.
    League-avg ≈ 0 by definition of EPA; strong teams 30-50, weak -20 to -40.
    """
    if not stats: return None
    games = stats.get('games') or 0
    if games < 1: return None
    pe = stats.get('pass_epa') or 0
    re = stats.get('rush_epa') or 0
    return round(float(pe + re) / float(games), 3)


def compute_projections(home_stats: dict, away_stats: dict, roof: str,
                        temp: Optional[int], wind: Optional[int]) -> dict:
    """EPA-based projected spread + total with venue adjustments."""
    h_rate = compute_off_rating(home_stats)
    a_rate = compute_off_rating(away_stats)

    out = {
        'home_off_rating': h_rate,
        'away_off_rating': a_rate,
        'power_diff': None,
        'projected_spread': None,
        'projected_total': None,
        'model_pred_home_points': None,
        'model_pred_away_points': None,
    }
    if h_rate is None or a_rate is None:
        return out

    power_diff = round(h_rate - a_rate, 2)
    projected_spread = round(power_diff * K_PTS + HOME_FIELD_PTS, 2)

    # Total = base + venue adjustments
    total = BASE_TOTAL
    if roof and roof.lower() in ('dome', 'closed'):
        total += DOME_ADJ
    if temp is not None and int(temp) <= 35:
        total += COLD_ADJ
    if wind is not None and int(wind) >= 15:
        total += WIND_ADJ

    # Split total using power_diff (favorite gets ~55/45)
    fav_share = 0.50 + min(0.10, abs(power_diff) * 0.005)
    if power_diff >= 0:
        home_pts = total * fav_share
        away_pts = total * (1 - fav_share)
    else:
        away_pts = total * fav_share
        home_pts = total * (1 - fav_share)

    out.update({
        'power_diff': power_diff,
        'projected_spread': projected_spread,
        'projected_total': round(total, 2),
        'model_pred_home_points': round(home_pts, 1),
        'model_pred_away_points': round(away_pts, 1),
    })
    return out


def compute_confluence(home_stats: dict, away_stats: dict,
                       roof: str, temp: Optional[int], wind: Optional[int]) -> tuple:
    """Count signals leaning home vs away. Returns (net, breakdown_dict).
    Positive = home lean."""
    breakdown = {}

    # 1. Offensive EPA rating
    h_off = compute_off_rating(home_stats)
    a_off = compute_off_rating(away_stats)
    if h_off is not None and a_off is not None:
        if h_off - a_off >= 5:
            breakdown['off_epa'] = 'home'
        elif a_off - h_off >= 5:
            breakdown['off_epa'] = 'away'

    # 2. Defensive splash plays (sacks + INTs — signal for pressure defense)
    for stats, side in [(home_stats, 'home'), (away_stats, 'away')]:
        splash = (stats.get('def_sacks') or 0) + (stats.get('def_ints') or 0) * 1.5
        stats['_splash_score'] = splash
    if home_stats and away_stats:
        h_splash = home_stats.get('_splash_score') or 0
        a_splash = away_stats.get('_splash_score') or 0
        if h_splash - a_splash >= 10:
            breakdown['def_splash'] = 'home'
        elif a_splash - h_splash >= 10:
            breakdown['def_splash'] = 'away'

    # 3. CPOE — passing efficiency (home QB)
    h_cpoe = _f((home_stats or {}).get('pass_cpoe'))
    a_cpoe = _f((away_stats or {}).get('pass_cpoe'))
    if h_cpoe is not None and a_cpoe is not None:
        if h_cpoe - a_cpoe >= 2:
            breakdown['cpoe'] = 'home'
        elif a_cpoe - h_cpoe >= 2:
            breakdown['cpoe'] = 'away'

    # 4. Rush EPA (some teams win with ground game)
    h_re = _f((home_stats or {}).get('rush_epa'))
    a_re = _f((away_stats or {}).get('rush_epa'))
    if h_re is not None and a_re is not None:
        # normalize by games — otherwise late-season teams overweight
        hg = (home_stats or {}).get('games') or 1
        ag = (away_stats or {}).get('games') or 1
        h_rpg = h_re / hg
        a_rpg = a_re / ag
        if h_rpg - a_rpg >= 3:
            breakdown['rush_epa'] = 'home'
        elif a_rpg - h_rpg >= 3:
            breakdown['rush_epa'] = 'away'

    # 5. Home-field default (always +1 for home in NFL, cohort-audited)
    breakdown['hfa'] = 'home'

    h = sum(1 for v in breakdown.values() if v == 'home')
    a = sum(1 for v in breakdown.values() if v == 'away')
    return h - a, breakdown


def compute_cohort_tags(row: dict) -> list:
    """Mirror nfl_cohort_backfill.compute_cohorts_for_game but for pre-game."""
    tags = []
    home = row.get('home_team'); away = row.get('away_team')
    spread = _f(row.get('close_spread'))
    total = _f(row.get('close_total'))
    roof = (row.get('roof') or '').lower()
    temp = _i(row.get('temp'))
    wind = _i(row.get('wind'))

    if spread is not None and spread <= -7.0:
        tags.append('nfl_heavy_home_dog')
    if roof in ('outdoors', 'open') and (
        (temp is not None and temp <= 40) or (wind is not None and wind >= 12)
    ):
        tags.append('nfl_outdoor_under')
    if home and away and NFL_DIVISION.get(home) == NFL_DIVISION.get(away) and NFL_DIVISION.get(home):
        tags.append('nfl_div_home_cover')
    if roof in ('dome', 'closed') and total is not None and total >= 47:
        tags.append('nfl_dome_over')
    if spread is not None and spread > 0:
        tags.append('nfl_home_fav')
    return tags


def compute_sweat_score(proj_spread, close_spread, conf_net, proj_total, close_total) -> int:
    """Universal 0-100 sweat score. Matches MLB/NCAAB banding
    (PRIME 80+, STRONG 65+, LIGHT 50+, PASS <50)."""
    score = 45  # base
    if proj_spread is not None and close_spread is not None:
        edge = abs(proj_spread - close_spread)   # both nflverse convention, so subtract
        if edge >= 5.0: score += 25
        elif edge >= 3.5: score += 18
        elif edge >= 2.0: score += 12
        elif edge >= 1.0: score += 6

    abs_conf = abs(conf_net)
    if abs_conf >= 4: score += 15
    elif abs_conf >= 3: score += 10
    elif abs_conf >= 2: score += 5

    if proj_total is not None and close_total is not None:
        te = abs(proj_total - close_total)
        if te >= 6: score += 8
        elif te >= 4: score += 5
        elif te >= 2.5: score += 3

    return min(100, max(0, score))


def sweat_tier(score):
    if score >= 80: return 'PRIME'
    if score >= 65: return 'STRONG'
    if score >= 50: return 'LIGHT_LEAN'
    return 'PASS'


def compute_primary_play(ctx: dict) -> Optional[dict]:
    """Analog of ncaab_game_context.compute_primary_play. Spread/total/ML only.
    Tier gates align with audit (nfl_heavy_home_dog is the only pre-Week-1
    audit-validated PRIME cohort)."""
    conf = ctx.get('signal_confluence_net') or 0
    proj_spread = ctx.get('projected_spread')
    close_spread = ctx.get('close_spread')
    home_team = ctx.get('home_team') or 'Home'
    away_team = ctx.get('away_team') or 'Away'
    proj_total = ctx.get('projected_total')
    close_total = ctx.get('close_total')

    spread_edge = None
    if proj_spread is not None and close_spread is not None:
        spread_edge = round(float(proj_spread) - float(close_spread), 2)
    abs_edge = abs(spread_edge) if spread_edge is not None else 0.0
    fav = home_team if (proj_spread is not None and float(proj_spread) > 0) else away_team

    total_edge = None
    if proj_total is not None and close_total is not None:
        total_edge = round(float(proj_total) - float(close_total), 2)

    tags = ctx.get('cohort_tags') or []

    # 1. HEAVY HOME DOG cohort PRIME override — 63.1% audit hit rate
    if 'nfl_heavy_home_dog' in tags:
        return {
            'type': 'spread',
            'tier': 'PRIME',
            'label': f'{home_team} +{abs(float(close_spread)):.1f}',
            'sub': f'nfl_heavy_home_dog cohort — 63.1% lifetime (n=65)',
            'signal_floor': 85,
        }

    # 2. STRONG spread — big model-vs-market gap + confluence agrees
    if abs_edge >= 3.5 and abs(conf) >= 2:
        return {
            'type': 'spread',
            'tier': 'STRONG',
            'label': f'{fav} spread cover',
            'sub': f'Model {proj_spread:+.1f} vs market {close_spread:+.1f} (edge {abs_edge:.1f}, conf {conf:+d})',
            'signal_floor': 72,
        }

    # 3. STRONG total — model disagreement ≥ 4
    if total_edge is not None and abs(total_edge) >= 4.0:
        side = 'Over' if total_edge > 0 else 'Under'
        return {
            'type': 'total',
            'tier': 'STRONG',
            'label': f'{side} {close_total}',
            'sub': f'Model projects {proj_total:.1f} vs market {close_total} ({total_edge:+.1f})',
            'signal_floor': 70,
        }

    # 4. LIGHT spread lean
    if abs_edge >= 2.0:
        return {
            'type': 'spread',
            'tier': 'LIGHT',
            'label': f'{fav} spread lean',
            'sub': f'Edge {abs_edge:.1f}',
            'signal_floor': 60,
        }

    return None


# ─── Odds API + row build ────────────────────────────────────────────────

def fetch_odds_events(sport_key: str = 'americanfootball_nfl') -> list:
    if not ODDS_KEY: return []
    url = (f'{ODDS_API_BASE}/{sport_key}/odds/'
           f'?apiKey={ODDS_KEY}&regions=us&markets=spreads,totals,h2h&oddsFormat=american')
    r = requests.get(url, timeout=20)
    if r.status_code != 200:
        print(f'  ⚠ Odds API {sport_key}: {r.status_code}')
        return []
    return r.json()


def _pick_book(event: dict, market_key: str) -> dict:
    for book in event.get('bookmakers', []):
        for m in book.get('markets', []):
            if m['key'] == market_key:
                return {'book': book['title'], 'outcomes': m['outcomes']}
    return {}


def build_row(event: dict, aliases: dict, team_stats: dict) -> Optional[dict]:
    home_raw = event.get('home_team'); away_raw = event.get('away_team')
    home = aliases.get(home_raw); away = aliases.get(away_raw)
    if not home or not away:
        return None

    commence = event.get('commence_time', '')
    try:
        dt = datetime.fromisoformat(commence.replace('Z', '+00:00'))
        game_date = dt.date().isoformat()
    except Exception:
        dt = _et_now()
        game_date = dt.date().isoformat()

    row = {
        'game_id': event.get('id'),
        'game_date': game_date,
        'season': dt.year,
        'season_type': 'REG',
        'home_team': home,
        'away_team': away,
        'kickoff_utc': commence,
        'div_game': NFL_DIVISION.get(home) == NFL_DIVISION.get(away) and bool(NFL_DIVISION.get(home)),
    }

    # Odds API spreads (native negative-fav) → nflverse convention (positive-fav)
    sp = _pick_book(event, 'spreads')
    for o in sp.get('outcomes') or []:
        if o['name'] == home_raw:
            pt = _f(o.get('point'))
            row['close_spread'] = -pt if pt is not None else None
        # Away spread price not in nfl_game_context schema; captured
        # in nfl_odds_pull → nfl_game_results if we ever need it.

    tot = _pick_book(event, 'totals')
    for o in tot.get('outcomes') or []:
        if o['name'] == 'Over':
            row['close_total'] = _f(o.get('point'))

    ml = _pick_book(event, 'h2h')
    for o in ml.get('outcomes') or []:
        if o['name'] == home_raw:
            row['close_home_ml'] = _i(o.get('price'))
        elif o['name'] == away_raw:
            row['close_away_ml'] = _i(o.get('price'))

    # Mirror close → open on first pull
    row.setdefault('open_spread', row.get('close_spread'))
    row.setdefault('open_total', row.get('close_total'))

    # Model
    home_stats = team_stats.get(home) or {}
    away_stats = team_stats.get(away) or {}
    proj = compute_projections(
        home_stats, away_stats,
        roof=row.get('roof'), temp=row.get('temp'), wind=row.get('wind'),
    )
    row.update(proj)

    conf_net, breakdown = compute_confluence(
        home_stats, away_stats,
        row.get('roof'), row.get('temp'), row.get('wind'),
    )
    row['signal_confluence_net'] = conf_net
    row['signal_confluence_breakdown'] = breakdown

    row['cohort_tags'] = compute_cohort_tags(row)

    score = compute_sweat_score(
        row.get('projected_spread'), row.get('close_spread'), conf_net,
        row.get('projected_total'), row.get('close_total'),
    )
    row['sweat_score'] = score
    row['sweat_tier'] = sweat_tier(score)
    row['primary_play'] = compute_primary_play(row)

    return row


def upsert_context(rows: list, dry_run: bool = False) -> int:
    if not rows: return 0
    if dry_run:
        for r in rows:
            pp = r.get('primary_play') or {}
            print(f"  [DRY] {r['game_id'][:12]}... {r['away_team']} @ {r['home_team']}  "
                  f"sp={r.get('close_spread'):+.1f} proj={r.get('projected_spread'):+.1f}  "
                  f"conf={r.get('signal_confluence_net'):+d}  ss={r['sweat_score']} {r['sweat_tier']}"
                  + (f"  → {pp.get('tier')} {pp.get('label')}" if pp else ''))
        return len(rows)
    r = requests.post(
        f'{SB}/rest/v1/nfl_game_context?on_conflict=game_id',
        headers=H_WRITE, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(rows)


def run(dry_run: bool = False) -> None:
    print(f'=== NFL game context · {_et_now().date()} ===')
    if not ODDS_KEY:
        print('  ✗ ODDS_API_KEY missing — abort')
        return

    aliases = load_alias_map()
    if not aliases:
        print('  ✗ nfl_team_aliases empty — run nfl_seed_aliases.py')
        return

    season = _et_now().year if _et_now().month >= 9 else _et_now().year - 1
    team_stats = load_team_stats(season)
    print(f'  aliases={len(aliases)}  team_stats={len(team_stats)} teams for {season}')

    events = []
    for sk in ('americanfootball_nfl', 'americanfootball_nfl_preseason'):
        e = fetch_odds_events(sk)
        events.extend(e)
    print(f'  Odds API events: {len(events)}')

    rows = []
    skipped = 0
    for e in events:
        r = build_row(e, aliases, team_stats)
        if r is None:
            skipped += 1
            continue
        rows.append(r)
    if skipped:
        print(f'  ⚠ skipped {skipped} events with unmapped teams')

    written = upsert_context(rows, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}wrote {written} rows to nfl_game_context')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
