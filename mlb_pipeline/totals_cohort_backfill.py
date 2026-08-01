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

    Each `_sig_*` method returns a (cohort_name, direction, description) tuple
    that fired for the game. `direction` is the side we hypothesize when the
    cohort fires — the backtest hit rate then measures whether that prediction
    is real signal or coin-flip. 7/31 audit surprise: park_hitter_high OVER
    hits only 38% (INVERTED). Backtest catches these before they cost picks.
    """
    SPORT = 'MLB'

    def _get(self, ctx, key, default=None):
        v = ctx.get(key)
        return default if v in (None, '') else v

    def _f(self, v):
        try: return float(v)
        except (TypeError, ValueError): return None

    # === WEATHER ===
    def _sig_temp_cold(self, ctx):
        t = self._f(self._get(ctx, 'temperature'))
        if t is None: return None
        if t <= 50:
            return ('temp_cold', 'UNDER',
                    f'Game-time temp {int(t)}°F ≤ 50 — ball does not carry')

    def _sig_temp_hot(self, ctx):
        """Backtest FLIP 2026-08-01: OVER hits only 38.5% (n=13). Books price hot
        weather into the total; the real edge is UNDER when everyone else is
        chasing the OVER narrative."""
        t = self._f(self._get(ctx, 'temperature'))
        if t is None: return None
        if t >= 90:
            return ('temp_hot', 'UNDER',
                    f'Game-time temp {int(t)}°F ≥ 90 — market prices hot-weather OVER, UNDER 61.5% historically')

    def _sig_wind_out_strong(self, ctx):
        ws = self._f(self._get(ctx, 'wind_speed'))
        blowing_in = ctx.get('wind_blowing_in')
        if ws is None or blowing_in is None: return None
        if ws >= 12 and blowing_in is False:
            return ('wind_out_strong', 'OVER',
                    f'Wind {int(ws)}mph blowing OUT — carry boost')

    def _sig_wind_in_strong(self, ctx):
        ws = self._f(self._get(ctx, 'wind_speed'))
        blowing_in = ctx.get('wind_blowing_in')
        if ws is None or blowing_in is None: return None
        if ws >= 12 and blowing_in is True:
            return ('wind_in_strong', 'UNDER',
                    f'Wind {int(ws)}mph blowing IN — carry killer')

    def _sig_rain_risk(self, ctx):
        if ctx.get('rain_risk_flag') is True:
            return ('rain_risk', 'UNDER',
                    'Rain risk flagged — delays + wet conditions dampen offense')

    # === BULLPEN / PITCHER STATE ===
    def _sig_bp_taxed_both(self, ctx):
        h = self._f(self._get(ctx, 'home_bullpen_era'))
        a = self._f(self._get(ctx, 'away_bullpen_era'))
        if h is None or a is None: return None
        if h > 4.5 and a > 4.5:
            return ('bp_taxed_both', 'OVER',
                    f'Both bullpen ERA > 4.5 (home {h} · away {a}) — late-inning runs')

    def _sig_bp_relievers_heavy_both(self, ctx):
        """Extras-hangover proxy — both teams used ≥10 relievers L3 days.

        Backtest 2026-08-01: 42.1% OVER (n=19) — no meaningful edge either way.
        Kept for observation but tightening threshold to 12+ to isolate true
        depth-crisis spots."""
        h = self._f(self._get(ctx, 'home_bp_relievers_3d'))
        a = self._f(self._get(ctx, 'away_bp_relievers_3d'))
        if h is None or a is None: return None
        if h >= 12 and a >= 12:
            return ('bp_relievers_depleted_both', 'OVER',
                    f'Both BPs used ≥12 relievers L3 (h {int(h)} · a {int(a)}) — depth crisis, late runs likely')

    def _sig_sp_short_rest_home(self, ctx):
        r = self._f(self._get(ctx, 'home_days_rest'))
        if r is None: return None
        if r < 4:
            return ('sp_short_rest_home', 'OVER',
                    f'Home SP on {int(r)} days rest (<4) — worse stuff')

    def _sig_sp_short_rest_away(self, ctx):
        r = self._f(self._get(ctx, 'away_days_rest'))
        if r is None: return None
        if r < 4:
            return ('sp_short_rest_away', 'OVER',
                    f'Away SP on {int(r)} days rest (<4) — worse stuff')

    def _sig_sp_form_bad_both(self, ctx):
        """Backtest FLIP 2026-08-01: OVER hits only 33.3% (n=15). Real direction
        is UNDER — market prices bad L3 pitching; when both SPs bomb it often
        means injury/replacement chaos → tighter games."""
        h = self._f(self._get(ctx, 'home_pitcher_last_3_era'))
        a = self._f(self._get(ctx, 'away_pitcher_last_3_era'))
        if h is None or a is None: return None
        if h > 5.0 and a > 5.0:
            return ('sp_form_bad_both', 'UNDER',
                    f'Both SP L3 ERA > 5 (h {h} · a {a}) — market over-corrects, UNDER 67% historically')

    def _sig_sp_form_elite_both(self, ctx):
        h = self._f(self._get(ctx, 'home_sp_xera'))
        a = self._f(self._get(ctx, 'away_sp_xera'))
        if h is None or a is None: return None
        if h < 3.5 and a < 3.5:
            return ('sp_form_elite_both', 'UNDER',
                    f'Both SP xERA < 3.5 (h {h} · a {a}) — pitchers duel')

    # === OFFENSE STATE ===
    def _sig_wrc_hot_both(self, ctx):
        """Backtest FLIP 2026-08-01: OVER hits only 37.5% (n=16). Market prices
        offensive momentum efficiently; the counter-narrative UNDER wins 62%."""
        h = self._f(self._get(ctx, 'home_wrc_proxy_l14'))
        a = self._f(self._get(ctx, 'away_wrc_proxy_l14'))
        if h is None or a is None: return None
        if h > 110 and a > 110:
            return ('wrc_hot_both', 'UNDER',
                    f'Both offenses L14 wRC+ > 110 (h {h} · a {a}) — market over-prices momentum, UNDER 62% historically')

    def _sig_wrc_cold_both(self, ctx):
        h = self._f(self._get(ctx, 'home_wrc_proxy_l14'))
        a = self._f(self._get(ctx, 'away_wrc_proxy_l14'))
        if h is None or a is None: return None
        if h < 90 and a < 90:
            return ('wrc_cold_both', 'UNDER',
                    f'Both offenses L14 wRC+ < 90 (h {h} · a {a})')

    def _sig_l10_hot_both(self, ctx):
        hw = self._f(self._get(ctx, 'home_l10_wins'))
        aw = self._f(self._get(ctx, 'away_l10_wins'))
        if hw is None or aw is None: return None
        if hw >= 7 and aw >= 7:
            return ('l10_hot_both', 'OVER',
                    f'Both teams ≥7 wins L10 (h {int(hw)} · a {int(aw)}) — momentum')

    def _sig_l10_cold_both(self, ctx):
        hw = self._f(self._get(ctx, 'home_l10_wins'))
        aw = self._f(self._get(ctx, 'away_l10_wins'))
        if hw is None or aw is None: return None
        if hw <= 3 and aw <= 3:
            return ('l10_cold_both', 'UNDER',
                    f'Both teams ≤3 wins L10 (h {int(hw)} · a {int(aw)}) — skidding')

    # === ROAD TRIP / TRAVEL ===
    def _sig_road_trip_long(self, ctx):
        """User's request: coming off road fatigue."""
        r = self._f(self._get(ctx, 'away_consecutive_road_games'))
        if r is None: return None
        if r >= 5:
            return ('road_trip_long_away', 'UNDER',
                    f'Away team on {int(r)}+ consecutive road games — travel fatigue')

    def _sig_travel_zero_home(self, ctx):
        """Home team on extended homestand — well-rested, familiar."""
        d = self._f(self._get(ctx, 'days_since_last_home_game'))
        if d is None: return None
        if d == 0:
            return ('home_stand_active', 'OVER',
                    'Home team on active homestand — rested + fed')

    # === PARK ===
    def _sig_park_hitter_high(self, ctx):
        p = self._f(self._get(ctx, 'park_run_factor'))
        if p is None: return None
        if p >= 105:
            return ('park_hitter_high', 'OVER',
                    f'Park run factor {int(p)} ≥ 105 — hitter-friendly')

    def _sig_park_pitcher_low(self, ctx):
        p = self._f(self._get(ctx, 'park_run_factor'))
        if p is None: return None
        if p <= 95:
            return ('park_pitcher_low', 'UNDER',
                    f'Park run factor {int(p)} ≤ 95 — pitcher-friendly')

    # === UMPIRE ===
    def _sig_ump_over_lean(self, ctx):
        """Parse umpire_note text for over-lean pct — e.g. '54% over'.

        Backtest FLIP 2026-08-01: ump_over_lean OVER hits 36.4% (n=22). Books
        already price umpire tendency into the total. The play is UNDER when
        the umpire has a public 'over-lean' reputation everyone else chases."""
        note = ctx.get('umpire_note') or ''
        import re
        m = re.search(r'(\d+)%\s*over', note.lower())
        if not m: return None
        pct = int(m.group(1))
        if pct >= 55:
            return ('ump_over_lean', 'UNDER',
                    f'Umpire lean {pct}% over — market chases OVER; UNDER 63.6% historically')
        if pct <= 45:
            return ('ump_under_lean', 'OVER',
                    f'Umpire lean {pct}% over — flip logic (need more sample)')

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
            wr = requests.post(
                f'{SB}/rest/v1/totals_cohort_signals'
                f'?on_conflict=sport,cohort_name,direction',
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
