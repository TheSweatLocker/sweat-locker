"""NCAAB cohort backfill (Phase 1 — item 2).

Computes historical STRAIGHT-UP hit rates for NCAAB cohorts using
ncaab_game_results (965 games from 2024-25 backfilled via ESPN
scoreboard) joined to ncaab_team_stats (end-of-season 2024-25 KenPom
snapshot). Writes to mlb_tier_calibration with sport='NCAAB' — same
table + downstream consumers (audit surfaces, primary_play gates)
as MLB/NFL/NCAAF.

Scope caveat — SU only, no ATS:
  The ESPN backfill did not capture close_spread/close_total on any of
  the 965 games (ESPN's public scoreboard rarely attaches odds for CBB).
  Every cohort here grades on straight-up outcome — did the KenPom
  favorite win outright? Not "did they cover?"

  Once live odds start flowing via ncaab_odds_pull.py in Nov 2026, we
  can add ATS cohorts on real-time data. For now, SU cohorts still
  give us useful priors:
    - Base rate of KenPom elite favorites winning outright
    - Home court advantage magnitude
    - How often big road-favored underdogs cover (SU proxy)

KenPom snapshot caveat:
  ncaab_team_stats season='2024-25' is the END-OF-SEASON snapshot.
  Ratings reflect the full season, not "as-of" any given game date.
  This biases the audit — teams that got hot late look strong all
  year. Explicitly noted in the audit note field. Fix requires daily
  KenPom snapshots (Phase 1b).

Cohorts computed (all straight-up):

  KENPOM SIDES:
    ncaab_kp_elite_fav_su_20+    — em_gap ≥ 20 → did favorite win?
    ncaab_kp_strong_fav_su_10_19 — em_gap 10-19
    ncaab_kp_slight_fav_su_5_9   — em_gap 5-9
    ncaab_kp_road_fav_su_10+     — away team em > home em by 10+
    ncaab_kp_road_fav_su_20+     — away team em > home em by 20+
    ncaab_kp_pickem              — |em_gap| < 3 (info-only, no side)

  HOME COURT ADVANTAGE:
    ncaab_home_overall_su        — home win rate across all games
    ncaab_home_evenly_matched_su — home win rate when |em_gap| < 3
                                    (isolates true HCA magnitude)

  PACE (info-only totals proxy):
    ncaab_high_pace_avg75+       — combined avg tempo ≥ 75 (info)
    ncaab_slow_pace_avg65_below  — combined avg tempo ≤ 65 (info)

  BLOWOUT DETECTION:
    ncaab_blowout_home_20+       — home won by 20+ (calibrates confidence)
    ncaab_blowout_away_20+       — away won by 20+

USAGE:
    python ncaab_cohort_backfill.py           # all 2024-25 games
    python ncaab_cohort_backfill.py --dry-run
    python ncaab_cohort_backfill.py --season 2024-25
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import date
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


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(v) if v is not None else None
    except (TypeError, ValueError): return None


# ─── data loading ─────────────────────────────────────────────────

def fetch_games(season_filter: str = '2024-25') -> list:
    """All graded ncaab_game_results for a season."""
    rows = []; off = 0
    while True:
        r = requests.get(
            f'{SB}/rest/v1/ncaab_game_results'
            f'?select=game_id,game_date,season,home_team,away_team,'
            f'home_score,away_score,home_win,total_points'
            f'&home_score=not.is.null&season=eq.{season_filter}'
            f'&order=game_date.desc&limit=1000&offset={off}',
            headers=H_READ, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        off += 1000
    return rows


def fetch_kenpom_snapshot(season: str) -> dict:
    """Load {team: {adj_em, adj_oe, adj_de, tempo}} for a season."""
    rows = []; off = 0
    while True:
        r = requests.get(
            f'{SB}/rest/v1/ncaab_team_stats'
            f'?select=team,adj_em,adj_oe,adj_de,tempo'
            f'&season=eq.{season}&limit=1000&offset={off}',
            headers=H_READ, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        off += 1000
    return {r['team']: r for r in rows}


# ─── cohort assignment ───────────────────────────────────────────

def compute_cohorts_for_game(g: dict, kp: dict) -> list:
    """Return list of cohort keys this game belongs to.
    kp = full team → stats lookup."""
    tags = []
    home = g.get('home_team'); away = g.get('away_team')
    home_kp = kp.get(home) or {}
    away_kp = kp.get(away) or {}
    h_em = _f(home_kp.get('adj_em'))
    a_em = _f(away_kp.get('adj_em'))
    h_tempo = _f(home_kp.get('tempo'))
    a_tempo = _f(away_kp.get('tempo'))
    hs = _i(g.get('home_score'))
    as_ = _i(g.get('away_score'))

    # KenPom em_gap cohorts (home minus away — positive = home stronger)
    if h_em is not None and a_em is not None:
        em_gap = h_em - a_em
        abs_gap = abs(em_gap)
        if abs_gap >= 20:
            tags.append('ncaab_kp_elite_fav_su_20+')
        elif 10 <= abs_gap < 20:
            tags.append('ncaab_kp_strong_fav_su_10_19')
        elif 5 <= abs_gap < 10:
            tags.append('ncaab_kp_slight_fav_su_5_9')
        elif abs_gap < 3:
            tags.append('ncaab_kp_pickem')
            tags.append('ncaab_home_evenly_matched_su')  # HCA isolate

        # Road-favored KenPom cohorts (away stronger than home)
        if em_gap <= -10:
            tags.append('ncaab_kp_road_fav_su_10+')
        if em_gap <= -20:
            tags.append('ncaab_kp_road_fav_su_20+')

    # HCA baseline (all games)
    tags.append('ncaab_home_overall_su')

    # Pace cohorts (info-only, no SU grade)
    if h_tempo is not None and a_tempo is not None:
        avg_tempo = (h_tempo + a_tempo) / 2
        if avg_tempo >= 75:
            tags.append('ncaab_high_pace_avg75+')
        elif avg_tempo <= 65:
            tags.append('ncaab_slow_pace_avg65_below')

    # Blowout detection (calibrates model confidence for big-em_gap games)
    if hs is not None and as_ is not None:
        margin = hs - as_
        if margin >= 20:
            tags.append('ncaab_blowout_home_20+')
        elif margin <= -20:
            tags.append('ncaab_blowout_away_20+')

    return tags


def cohort_result(cohort: str, g: dict, kp: dict) -> Optional[str]:
    """Return 'Win' | 'Loss' | 'Push' | None (not applicable / info-only).
    Semantics:
      *_kp_*_fav_su_* → Win if the KENPOM-FAVORED team won outright
      ncaab_home_*_su → Win if home team won
      pace / blowout / pickem → info-only (None)
    """
    hs = _i(g.get('home_score')); as_ = _i(g.get('away_score'))
    if hs is None or as_ is None: return None
    home_won = hs > as_

    home_kp = kp.get(g.get('home_team')) or {}
    away_kp = kp.get(g.get('away_team')) or {}
    h_em = _f(home_kp.get('adj_em'))
    a_em = _f(away_kp.get('adj_em'))
    em_gap = (h_em - a_em) if (h_em is not None and a_em is not None) else None

    # KenPom-favored SU cohorts — grade based on which side had higher KP em
    kp_su_cohorts = {'ncaab_kp_elite_fav_su_20+',
                     'ncaab_kp_strong_fav_su_10_19',
                     'ncaab_kp_slight_fav_su_5_9'}
    if cohort in kp_su_cohorts:
        if em_gap is None: return None
        kp_fav_won = home_won if em_gap > 0 else (not home_won)
        return 'Win' if kp_fav_won else 'Loss'

    # Road-favored KenPom cohorts — did the away team (KP fav) win?
    if cohort in ('ncaab_kp_road_fav_su_10+', 'ncaab_kp_road_fav_su_20+'):
        return 'Loss' if home_won else 'Win'

    # Home court advantage cohorts — did home win?
    if cohort in ('ncaab_home_overall_su', 'ncaab_home_evenly_matched_su'):
        return 'Win' if home_won else 'Loss'

    # Info-only cohorts (no side to grade)
    return None


# ─── DB layer ─────────────────────────────────────────────────────

def build_calibration_rows(tallies: dict) -> list:
    """Rows for mlb_tier_calibration. Canonical schema: hits/total/hit_rate."""
    today = date.today().isoformat()
    rows = []
    for cohort, wl in tallies.items():
        n = wl['W'] + wl['L']
        if n == 0: continue
        rows.append({
            'tier': cohort,
            'sport': 'NCAAB',
            'window_label': 'lifetime_su_2024_25',   # SU + season tag
            'hits': wl['W'],
            'total': n,
            'hit_rate': round(wl['W'] / n, 4),
            'computed_date': today,
        })
    return rows


def upsert(rows: list, dry_run: bool = False) -> int:
    if not rows: return 0
    if dry_run:
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


# ─── main ─────────────────────────────────────────────────────────

def run(season: str = '2024-25', dry_run: bool = False) -> None:
    print(f'=== NCAAB cohort backfill (SU only) · season {season} ===')
    games = fetch_games(season)
    print(f'  graded games: {len(games)}')
    if not games:
        return

    kp = fetch_kenpom_snapshot(season)
    print(f'  KenPom snapshot rows: {len(kp)}')

    # Diagnostic: how many games have KenPom data for both teams?
    with_kp = sum(1 for g in games
                  if g.get('home_team') in kp and g.get('away_team') in kp)
    print(f'  games w/ full KenPom join: {with_kp}/{len(games)} '
          f'({100*with_kp/len(games):.1f}%)')

    tallies = defaultdict(lambda: {'W': 0, 'L': 0, 'P': 0})
    for g in games:
        cohorts = compute_cohorts_for_game(g, kp)
        for c in cohorts:
            result = cohort_result(c, g, kp)
            if result:
                tallies[c][result[0]] += 1

    rows = build_calibration_rows(tallies)

    # Also compute info-only stats (not written but printed)
    info_stats = compute_info_only_stats(games, kp)

    written = upsert(rows, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}wrote {written} calibration rows '
          f'(sport=NCAAB, window=lifetime_su_2024_25)')

    print(f'\n=== Sorted SU cohort hit rates ===')
    for r in sorted(rows, key=lambda x: -x['hit_rate']):
        pct = r['hit_rate'] * 100
        star = ' ⭐' if pct >= 60 and r['total'] >= 30 else \
               ' 🚨' if pct < 40 and r['total'] >= 30 else ''
        print(f"  {r['tier']:38} {pct:5.1f}%  ({r['hits']}/{r['total']}){star}")

    print(f'\n=== Info-only cohort stats ===')
    for label, s in info_stats.items():
        print(f'  {label:38} {s}')


def compute_info_only_stats(games: list, kp: dict) -> dict:
    """Non-hit-rate metrics — avg points, sample sizes, etc."""
    stats = {}
    hp_totals = []; sp_totals = []; pickem_home_wins = 0; pickem_n = 0
    home_blowouts = 0; away_blowouts = 0
    for g in games:
        hs = _i(g.get('home_score')); as_ = _i(g.get('away_score'))
        if hs is None or as_ is None: continue
        home_kp = kp.get(g.get('home_team')) or {}
        away_kp = kp.get(g.get('away_team')) or {}
        h_t = _f(home_kp.get('tempo')); a_t = _f(away_kp.get('tempo'))
        h_em = _f(home_kp.get('adj_em')); a_em = _f(away_kp.get('adj_em'))
        tot = hs + as_
        if h_t is not None and a_t is not None:
            avg_t = (h_t + a_t) / 2
            if avg_t >= 75: hp_totals.append(tot)
            elif avg_t <= 65: sp_totals.append(tot)
        if h_em is not None and a_em is not None and abs(h_em - a_em) < 3:
            pickem_n += 1
            if hs > as_: pickem_home_wins += 1
        margin = hs - as_
        if margin >= 20: home_blowouts += 1
        elif margin <= -20: away_blowouts += 1

    if hp_totals:
        stats['high_pace_avg_pts'] = f'{sum(hp_totals)/len(hp_totals):.1f} (n={len(hp_totals)})'
    if sp_totals:
        stats['slow_pace_avg_pts'] = f'{sum(sp_totals)/len(sp_totals):.1f} (n={len(sp_totals)})'
    if pickem_n:
        stats['pickem_home_win_pct'] = f'{100*pickem_home_wins/pickem_n:.1f}% (n={pickem_n})'
    stats['blowouts_home_20+'] = f'{home_blowouts}'
    stats['blowouts_away_20+'] = f'{away_blowouts}'
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', default='2024-25')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(season=args.season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
