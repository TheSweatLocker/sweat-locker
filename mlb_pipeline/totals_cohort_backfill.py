"""Totals cohort backfill (2026-08-01 · Tier 1).

Sport-universal totals cohort attribution — extracts contextual signals
from historical games and computes OVER/UNDER hit rates per cohort.
Fills the gap surfaced by 7/31 audit: sides had cohort attribution but
totals had none.

MVP v1 signals (MLB only, computable from existing mlb_game_context):
  - bp_taxed_both       — both bullpens fatigued (bp_era_3d > 5)
  - wrc_hot_both        — both offenses hot (wrc_plus > 110)
  - wrc_cold_both       — both offenses cold (wrc_plus < 90)
  - sp_form_bad_both    — both SP struggling (L3 ERA > 5)
  - sp_form_elite_both  — both SP elite (xera < 3.5)
  - temp_cold           — game-time temp ≤ 50°F
  - wind_out_strong     — wind out ≥ 15 mph
  - park_hitter_high    — park run factor ≥ 105
  - park_pitcher_low    — park run factor ≤ 95

v2 signals (need MLB StatsAPI schedule integration):
  - coming_off_road_gte_6
  - bp_extras_used_yday
  - getaway_day
  - venue_scoring_delta (team scoring at this specific park vs season)

Signal computer is a class per sport for cross-sport extension.
Result rows written to totals_cohort_signals table.

Usage:
    python totals_cohort_backfill.py [--sport MLB] [--days 60]
"""
import argparse, os, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}


class MLBTotalsSignals:
    """Extracts totals cohort signals from mlb_game_context rows.

    Each `_sig_*` method returns a list of (cohort_name, direction) tuples
    that fired for the game. `direction` is the side historically favored
    when the cohort fires — the hit rate then measures how often that
    prediction was correct.
    """
    SPORT = 'MLB'

    def _get(self, ctx, key, default=None):
        v = ctx.get(key)
        return default if v in (None, '', 0) else v

    def _sig_bp_taxed_both(self, ctx):
        h = self._get(ctx, 'home_bp_era_3d')
        a = self._get(ctx, 'away_bp_era_3d')
        if h is None or a is None: return None
        try:
            if float(h) > 5.0 and float(a) > 5.0:
                return ('bp_taxed_both', 'OVER',
                        'Both bullpens with 3d ERA > 5.0 — late-inning runs likely')
        except (TypeError, ValueError): pass
        return None

    def _sig_wrc_hot_both(self, ctx):
        h = self._get(ctx, 'home_wrc_plus')
        a = self._get(ctx, 'away_wrc_plus')
        if h is None or a is None: return None
        try:
            if float(h) > 110 and float(a) > 110:
                return ('wrc_hot_both', 'OVER',
                        'Both offenses wRC+ > 110 — pace + power both sides')
        except (TypeError, ValueError): pass
        return None

    def _sig_wrc_cold_both(self, ctx):
        h = self._get(ctx, 'home_wrc_plus')
        a = self._get(ctx, 'away_wrc_plus')
        if h is None or a is None: return None
        try:
            if float(h) < 90 and float(a) < 90:
                return ('wrc_cold_both', 'UNDER',
                        'Both offenses wRC+ < 90 — matchup lacks bats')
        except (TypeError, ValueError): pass
        return None

    def _sig_sp_form_bad_both(self, ctx):
        h = self._get(ctx, 'home_pitcher_last_3_era')
        a = self._get(ctx, 'away_pitcher_last_3_era')
        if h is None or a is None: return None
        try:
            if float(h) > 5.0 and float(a) > 5.0:
                return ('sp_form_bad_both', 'OVER',
                        'Both SP L3 ERA > 5 — both getting hit')
        except (TypeError, ValueError): pass
        return None

    def _sig_sp_form_elite_both(self, ctx):
        h = self._get(ctx, 'home_sp_xera')
        a = self._get(ctx, 'away_sp_xera')
        if h is None or a is None: return None
        try:
            if float(h) < 3.5 and float(a) < 3.5:
                return ('sp_form_elite_both', 'UNDER',
                        'Both SP xERA < 3.5 — pitchers duel setup')
        except (TypeError, ValueError): pass
        return None

    def _sig_temp_cold(self, ctx):
        t = self._get(ctx, 'temperature') or self._get(ctx, 'weather_temp')
        if t is None: return None
        try:
            if float(t) <= 50:
                return ('temp_cold', 'UNDER',
                        f'Game-time temp ≤ 50°F ({t}) — ball does not carry')
        except (TypeError, ValueError): pass
        return None

    def _sig_park_hitter_high(self, ctx):
        p = self._get(ctx, 'park_run_factor')
        if p is None: return None
        try:
            if float(p) >= 105:
                return ('park_hitter_high', 'OVER',
                        f'Park run factor {p} ≥ 105 — hitter-friendly')
        except (TypeError, ValueError): pass
        return None

    def _sig_park_pitcher_low(self, ctx):
        p = self._get(ctx, 'park_run_factor')
        if p is None: return None
        try:
            if float(p) <= 95:
                return ('park_pitcher_low', 'UNDER',
                        f'Park run factor {p} ≤ 95 — pitcher-friendly')
        except (TypeError, ValueError): pass
        return None

    def extract(self, ctx) -> list:
        """Return list of (cohort_name, direction, description) fired for this game."""
        results = []
        for attr in dir(self):
            if attr.startswith('_sig_'):
                hit = getattr(self, attr)(ctx)
                if hit: results.append(hit)
        return results


def get_total_result(res_row, projected_total):
    """Return 'OVER'/'UNDER'/'PUSH' based on final vs projected."""
    hs, as_ = res_row.get('home_score'), res_row.get('away_score')
    if hs is None or as_ is None or projected_total is None: return None
    total = int(hs) + int(as_)
    if total == projected_total: return 'PUSH'
    return 'OVER' if total > projected_total else 'UNDER'


def backfill_mlb(days: int = 60) -> None:
    sig_computer = MLBTotalsSignals()
    now = datetime.now(timezone.utc)
    start_date = (now - timedelta(days=days)).strftime('%Y-%m-%d')
    end_date = (now - timedelta(days=1)).strftime('%Y-%m-%d')   # thru yesterday
    print(f'=== MLB totals cohort backfill · {start_date} → {end_date} ===')

    # Pull all game contexts
    ctx_all = []
    offset = 0
    while True:
        params = [('game_date', f'gte.{start_date}'),
                  ('game_date', f'lte.{end_date}'),
                  ('select', '*'),
                  ('limit', '500'), ('offset', str(offset))]
        r = requests.get(f'{SB}/rest/v1/mlb_game_context',
                         headers=H_READ, params=params, timeout=30).json()
        if not isinstance(r, list) or not r: break
        ctx_all += r
        if len(r) < 500: break
        offset += 500
    print(f'  {len(ctx_all)} game context rows')

    # Pull results with totals info
    params = [('game_date', f'gte.{start_date}'),
              ('game_date', f'lte.{end_date}'),
              ('select', 'game_id,home_score,away_score,total_result'),
              ('limit', '2000')]
    res_rows = requests.get(f'{SB}/rest/v1/mlb_game_results',
                            headers=H_READ, params=params, timeout=30).json()
    res_by = {r['game_id']: r for r in res_rows if isinstance(r, dict)}
    print(f'  {len(res_by)} result rows')

    # Aggregate per cohort
    # cohort_name -> direction -> (wins, total)
    stats = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    # Also track by rolling windows
    stats_30d = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    stats_14d = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    descriptions = {}

    cutoff_30 = (now - timedelta(days=30)).date()
    cutoff_14 = (now - timedelta(days=14)).date()

    for ctx in ctx_all:
        if not isinstance(ctx, dict): continue
        gid = ctx.get('game_id')
        res = res_by.get(gid)
        if not res or res.get('home_score') is None: continue
        # Prefer projected total if present, fall back to line
        proj = ctx.get('projected_total') or ctx.get('close_total')
        if proj is None: continue
        try: proj = float(proj)
        except (TypeError, ValueError): continue
        result = get_total_result(res, proj)
        if result not in ('OVER', 'UNDER'): continue
        # Fire signals
        fires = sig_computer.extract(ctx)
        if not fires: continue
        try:
            gd = datetime.strptime(ctx.get('game_date',''), '%Y-%m-%d').date()
        except Exception: gd = None
        for cohort, direction, desc in fires:
            descriptions[(cohort, direction)] = desc
            stats[cohort][direction][1] += 1
            if result == direction:
                stats[cohort][direction][0] += 1
            if gd and gd >= cutoff_30:
                stats_30d[cohort][direction][1] += 1
                if result == direction: stats_30d[cohort][direction][0] += 1
            if gd and gd >= cutoff_14:
                stats_14d[cohort][direction][1] += 1
                if result == direction: stats_14d[cohort][direction][0] += 1

    # Write rows + print
    written = 0
    print(f'\n{"Cohort":<24}{"Dir":<8}{"Lifetime":<18}{"30d":<15}{"14d"}')
    print('-' * 80)
    for cohort in sorted(stats.keys()):
        for direction in ('OVER', 'UNDER'):
            w, n = stats[cohort][direction]
            if n == 0: continue
            w30, n30 = stats_30d[cohort][direction]
            w14, n14 = stats_14d[cohort][direction]
            pct = round(100 * w / n, 1)
            pct30 = round(100 * w30 / n30, 1) if n30 else None
            pct14 = round(100 * w14 / n14, 1) if n14 else None
            print(f'  {cohort[:22]:<22}  {direction:<6}  {pct}% ({w}-{n-w})     '
                  f'{pct30 if pct30 is not None else "-"}% n={n30}     '
                  f'{pct14 if pct14 is not None else "-"}% n={n14}')
            payload = {
                'sport': 'MLB',
                'cohort_name': cohort,
                'direction': direction,
                'lifetime_pct': pct,
                'lifetime_n': n,
                'last_30d_pct': pct30,
                'last_30d_n': n30,
                'last_14d_pct': pct14,
                'last_14d_n': n14,
                'description': descriptions.get((cohort, direction), ''),
                'computed_at': datetime.now(timezone.utc).isoformat(),
            }
            wr = requests.post(f'{SB}/rest/v1/totals_cohort_signals',
                               headers=H_WRITE, json=payload, timeout=15)
            if wr.status_code in (200, 201, 204):
                written += 1
            else:
                print(f'  ⚠ upsert {wr.status_code}: {wr.text[:150]}')
    print(f'\n=== wrote {written} cohort rows ===')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB')
    p.add_argument('--days', type=int, default=60)
    args = p.parse_args()
    if args.sport == 'MLB':
        backfill_mlb(days=args.days)
    else:
        print(f'sport {args.sport} not yet supported — MLB only in v1')
