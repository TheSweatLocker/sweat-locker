"""NCAAF cohort backfill (Phase 1 — item 2).

Computes historical hit rates for CFB-specific cohorts using
ncaaf_game_results (15k+ games backfilled 2022-2025). Writes to
mlb_tier_calibration with sport='NCAAF' — same table + downstream
consumers (audit surfaces, primary_play gates) as MLB/NFL.

Cohorts computed:

  SPREAD/SIDES:
    ncaaf_heavy_home_dog_14+   — home spread ≤ -14 (getting 14+ pts)
    ncaaf_heavy_home_dog_7_13  — home spread -7 to -13 (getting 7-13 pts)
    ncaaf_heavy_home_fav_14+   — home spread ≥ +14 (giving 14+)
    ncaaf_pickem               — |spread| ≤ 2
    ncaaf_home_conference      — home team in conference matchup
    ncaaf_neutral_site         — neutral field games (bowls, kickoff classics)
    ncaaf_road_fav             — away team is the favorite

  TIME:
    ncaaf_thursday             — Thu games
    ncaaf_friday               — Fri games
    ncaaf_saturday_primetime   — Sat 7pm ET+ games

  TOTALS:
    ncaaf_dome_over            — indoor stadium + total ≥ 55
    ncaaf_high_total_55+       — total ≥ 55 regardless of venue
    ncaaf_low_total_46_below   — total ≤ 46
    ncaaf_bowl_over            — postseason + high-scoring convention

  POSTSEASON:
    ncaaf_bowl_game            — any postseason game
    ncaaf_conf_championship    — championship-week matchup (harder to detect
                                  cleanly; using conference_game + week ≥ 14
                                  as proxy)

USAGE:
    python ncaaf_cohort_backfill.py                # all seasons
    python ncaaf_cohort_backfill.py --season 2024
    python ncaaf_cohort_backfill.py --dry-run
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import date, datetime
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


# FBS dome/indoor stadiums (partial + closed retractable roofs)
DOME_VENUES = {
    'Ford Field', 'Alamodome', 'Carrier Dome', 'JMA Wireless Dome',  # Syracuse
    'Fargodome', 'UNI-Dome',
    'Lucas Oil Stadium', 'AT&T Stadium', 'NRG Stadium',
    'Mercedes-Benz Superdome', 'Caesars Superdome',
    'Cajundome',  # UL Monroe indoor
    'Mercedes-Benz Stadium', 'State Farm Stadium',
    'U.S. Bank Stadium', 'Allegiant Stadium',
    'Yulman Stadium',  # Tulane retractable (partial)
    'Sun Bowl Stadium', 'DKR-Texas Memorial Stadium',
}


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(v) if v is not None else None
    except (TypeError, ValueError): return None


def fetch_games(season_filter: Optional[int] = None) -> list:
    """Pull all graded NCAAF games with lines."""
    rows = []; off = 0
    filt = '&home_score=not.is.null&close_spread=not.is.null'
    if season_filter:
        filt += f'&season=eq.{season_filter}'
    while True:
        r = requests.get(
            f'{SB}/rest/v1/ncaaf_game_results?select=season,season_type,week,game_date,'
            f'kickoff_utc,home_team,away_team,close_spread,close_total,'
            f'home_score,away_score,home_win,spread_result,total_result,'
            f'neutral_site,conference_game,venue'
            f'{filt}&order=game_date.desc&limit=1000&offset={off}',
            headers=H_READ, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        off += 1000
    return rows


def _kickoff_day_hour_et(kickoff_utc: str) -> tuple:
    """Return (weekday_int, hour_int_et) or (None, None).
    2026-08-09 fix: use ZoneInfo('America/New_York') for DST-correct ET.
    Previous fixed EST -5 mis-classified late-Sat primetime games during
    EDT (Aug-Nov = all NCAAF season)."""
    if not kickoff_utc: return None, None
    try:
        dt = datetime.fromisoformat(kickoff_utc.replace('Z', '+00:00'))
        try:
            from zoneinfo import ZoneInfo
            dt_et = dt.astimezone(ZoneInfo('America/New_York'))
        except ImportError:
            # Py<3.9 fallback — approximate
            from datetime import timedelta, timezone as _tz
            dt_et = dt.astimezone(_tz(timedelta(hours=-4)))  # EDT default
        return dt_et.weekday(), dt_et.hour
    except Exception:
        return None, None


def compute_cohorts_for_game(g: dict) -> list:
    """Return list of cohort keys this game belongs to."""
    tags = []
    spread = _f(g.get('close_spread'))
    total = _f(g.get('close_total'))
    home_score = _i(g.get('home_score')); away_score = _i(g.get('away_score'))
    venue = g.get('venue') or ''
    is_dome = venue in DOME_VENUES
    neutral = bool(g.get('neutral_site'))
    conf_game = bool(g.get('conference_game'))
    season_type = g.get('season_type') or 'regular'
    week = _i(g.get('week'))
    weekday, hour_et = _kickoff_day_hour_et(g.get('kickoff_utc'))

    # SPREAD-based cohorts
    # CFBD spread convention: positive close_spread = home is dog (getting pts)
    # So spread <= -14 means home is FAV giving 14+ pts.
    # Home DOG cohort is close_spread >= 7 (getting 7+).
    if spread is not None:
        if spread >= 14:
            tags.append('ncaaf_heavy_home_dog_14+')
        elif 7 <= spread < 14:
            tags.append('ncaaf_heavy_home_dog_7_13')
        elif spread <= -14:
            tags.append('ncaaf_heavy_home_fav_14+')
        if abs(spread) <= 2:
            tags.append('ncaaf_pickem')
        # Road favorite (away team favored)
        if spread > 0:
            tags.append('ncaaf_road_fav')

    # Situational
    if conf_game and not neutral:
        tags.append('ncaaf_home_conference')
    if neutral:
        tags.append('ncaaf_neutral_site')

    # Time cohorts
    if weekday == 3:  # Thursday
        tags.append('ncaaf_thursday')
    elif weekday == 4:  # Friday
        tags.append('ncaaf_friday')
    elif weekday == 5 and hour_et is not None and hour_et >= 19:  # Sat 7pm+ ET
        tags.append('ncaaf_saturday_primetime')

    # Totals
    if total is not None:
        if is_dome and total >= 55:
            tags.append('ncaaf_dome_over')
        if total >= 55:
            tags.append('ncaaf_high_total_55+')
        if total <= 46:
            tags.append('ncaaf_low_total_46_below')

    # Postseason
    if season_type == 'postseason':
        tags.append('ncaaf_bowl_game')
    # Conf championship proxy
    if conf_game and week is not None and week >= 14:
        tags.append('ncaaf_conf_championship')

    return tags


def cohort_result(cohort: str, g: dict) -> Optional[str]:
    """Return 'Win' | 'Loss' | 'Push' | None (not applicable)."""
    hs = _i(g.get('home_score')); as_ = _i(g.get('away_score'))
    sr = g.get('spread_result'); tr = (g.get('total_result') or '').lower()
    if hs is None or as_ is None:
        return None

    # Cohorts betting HOME cover
    home_cover_cohorts = {
        'ncaaf_heavy_home_dog_14+',
        'ncaaf_heavy_home_dog_7_13',
        'ncaaf_home_conference',
    }
    # Cohorts betting AWAY cover
    away_cover_cohorts = {
        'ncaaf_heavy_home_fav_14+',   # fade the giving side
        'ncaaf_road_fav',
    }
    # OVER cohorts
    over_cohorts = {'ncaaf_dome_over', 'ncaaf_high_total_55+'}
    # UNDER cohorts
    under_cohorts = {'ncaaf_low_total_46_below'}
    # Info-only cohorts (no direct pick to grade)
    info_only = {'ncaaf_pickem', 'ncaaf_neutral_site', 'ncaaf_thursday',
                 'ncaaf_friday', 'ncaaf_saturday_primetime',
                 'ncaaf_bowl_game', 'ncaaf_conf_championship'}

    if cohort in home_cover_cohorts:
        if sr == 'home_covered': return 'Win'
        if sr == 'away_covered': return 'Loss'
        if sr == 'push': return 'Push'
    if cohort in away_cover_cohorts:
        if sr == 'away_covered': return 'Win'
        if sr == 'home_covered': return 'Loss'
        if sr == 'push': return 'Push'
    if cohort in over_cohorts:
        if tr == 'over': return 'Win'
        if tr == 'under': return 'Loss'
        if tr == 'push': return 'Push'
    if cohort in under_cohorts:
        if tr == 'under': return 'Win'
        if tr == 'over': return 'Loss'
        if tr == 'push': return 'Push'
    if cohort in info_only:
        return None
    return None


def build_calibration_rows(tallies: dict) -> list:
    """Build rows matching canonical mlb_tier_calibration schema:
    hits / total / hit_rate (0-1 float). Cohort key stored in `tier`.
    """
    today = date.today().isoformat()
    rows = []
    for cohort, wl in tallies.items():
        n = wl['W'] + wl['L']
        if n == 0: continue
        rows.append({
            'tier': cohort,   # keep as ncaaf_* prefix (matches nfl_ pattern)
            'sport': 'NCAAF',
            'window_label': 'lifetime',
            'hits': wl['W'],
            'total': n,
            'hit_rate': round(wl['W'] / n, 4),
            'computed_date': today,
        })
    return rows


def upsert(rows: list, dry_run: bool = False) -> int:
    if not rows: return 0
    if dry_run:
        for r in sorted(rows, key=lambda x: -x['hit_rate']):
            pct = r['hit_rate'] * 100
            star = ' ⭐' if pct >= 55 and r['total'] >= 30 else \
                   ' 🚨' if pct < 45 and r['total'] >= 30 else ''
            print(f"  [DRY] {r['tier']:28} {pct:5.1f}% ({r['hits']}/{r['total']}){star}")
        return len(rows)
    r = requests.post(
        f'{SB}/rest/v1/mlb_tier_calibration'
        f'?on_conflict=tier,window_label,computed_date',
        headers=H_WRITE, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(rows)


def run(season_filter: Optional[int] = None, dry_run: bool = False) -> None:
    print(f'=== NCAAF cohort backfill ===')
    games = fetch_games(season_filter)
    print(f'  games loaded (graded + w/ lines): {len(games)}')
    if not games:
        return

    tallies = defaultdict(lambda: {'W': 0, 'L': 0, 'P': 0})
    for g in games:
        cohorts = compute_cohorts_for_game(g)
        for c in cohorts:
            result = cohort_result(c, g)
            if result:
                tallies[c][result[0]] += 1

    rows = build_calibration_rows(tallies)
    print(f'  cohorts computed: {len(rows)}')

    written = upsert(rows, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}wrote {written} calibration rows to mlb_tier_calibration')

    print(f'\n=== Sorted cohort hit rates ===')
    for r in sorted(rows, key=lambda x: -x['hit_rate']):
        pct = r['hit_rate'] * 100
        star = ' ⭐' if pct >= 55 and r['total'] >= 30 else \
               ' 🚨' if pct < 45 and r['total'] >= 30 else ''
        print(f"  {r['tier']:28} {pct:5.1f}%  ({r['hits']}/{r['total']}){star}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=None)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(season_filter=args.season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
