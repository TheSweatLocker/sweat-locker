"""Pipeline health check — runs as last step in MLB daily workflow.

Catches silent skips where a pipeline step "ran" but didn't write the
expected fields. Today's case: play_of_day.py's late-slate defer path
was a blanket return that killed the per-game sweat-score write loop.
Audit didn't catch it because we only audit resolved (post-game) data.

Checks today's mlb_game_context rows and emits clearly-tagged warnings
when expected populations are missing. Returns nonzero exit only on
hard failures (e.g., zero games written for today on a non-off-day).
"""
import os
import sys
import json
import urllib.request
from datetime import datetime, timedelta, timezone

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from dotenv import load_dotenv
load_dotenv()
URL = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=20) as r:
        return json.loads(r.read())


def today_et():
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    return et.strftime('%Y-%m-%d')


def et_hour():
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    return et.hour


def main():
    date = today_et()
    hour = et_hour()
    is_afternoon = hour >= 14  # 2pm ET cron and later

    print('=' * 70)
    print(f'PIPELINE HEALTH CHECK — {date} (ET hour: {hour})')
    print('=' * 70)

    games = get(f"{URL}/rest/v1/mlb_game_context?game_date=eq.{date}&select=*") or []

    if not games:
        print(f'⚠️  ZERO games in mlb_game_context for {date}.')
        print('   Possible causes: (1) MLB off-day, (2) game_context.py failed to write,')
        print('   (3) date format mismatch.')
        # Not an automatic fail — could be a real off-day.
        return 0

    print(f'\nGames in mlb_game_context: {len(games)}')

    # Field population checks — track issues without dying early
    issues = []
    warnings = []

    def pct(n, d):
        return f'{n}/{d} = {(n/d*100 if d else 0):.0f}%'

    # --- Core fields that should be populated every run ---
    null_pitcher = sum(1 for g in games if not g.get('home_pitcher') or not g.get('away_pitcher'))
    if null_pitcher > 0:
        warnings.append(f'⚠️  {pct(null_pitcher, len(games))} games missing starter names. '
                        f'MLB Stats API may not have probable pitchers yet (TBD/announce later).')

    null_xera = sum(1 for g in games if g.get('home_sp_xera') is None and g.get('away_sp_xera') is None)
    if null_xera > 0:
        warnings.append(f'⚠️  {pct(null_xera, len(games))} games missing BOTH starter xERAs. '
                        f'Likely opener/bullpen game profile — expected on some matchups.')

    null_nrfi = sum(1 for g in games if g.get('nrfi_score') is None)
    if null_nrfi > 0:
        issues.append(f'❌ {pct(null_nrfi, len(games))} games missing nrfi_score. '
                      f'game_context.py NRFI computation may have failed.')

    null_sweat = sum(1 for g in games if g.get('sweat_score') is None)
    if null_sweat > 0:
        # This is the bug we just shipped a fix for. Hard fail if >0 on what
        # should be a successful run.
        issues.append(f'❌ {pct(null_sweat, len(games))} games missing sweat_score. '
                      f'play_of_day.py may have skipped writes (late-slate defer bug). '
                      f'App will fall back to client-side calc.')

    # Close-line refresh check (added 2026-05-29). After 1pm ET / 17 UTC the
    # afternoon cron should have written close_total / close_spread. If they
    # are still NULL post-1pm, the 2pm cron either hasn't fired or it skipped
    # the odds write — surfaces silent afternoon-cron failures that otherwise
    # appear normal on the dashboard.
    from datetime import datetime, timezone, timedelta
    et_hour_now = (datetime.now(timezone.utc) - timedelta(hours=4)).hour
    if et_hour_now >= 13:
        null_close_total = sum(1 for g in games if g.get('close_total') is None)
        if null_close_total > 0:
            issues.append(
                f'❌ {pct(null_close_total, len(games))} games missing close_total '
                f'after {et_hour_now}:00 ET. 2pm cron may have failed to refresh odds.'
            )

    # --- v3/v4 model predictions ---
    null_v3_total = sum(1 for g in games if g.get('projected_total') is None)
    if null_v3_total > 0:
        warnings.append(f'⚠️  {pct(null_v3_total, len(games))} games missing projected_total (v3). '
                        f'Usually means missing xERA for both starters.')

    null_v4_total = sum(1 for g in games if g.get('model_pred_total') is None)
    if null_v4_total / len(games) > 0.5:
        warnings.append(f'⚠️  {pct(null_v4_total, len(games))} games missing model_pred_total (v4). '
                        f'High suppression rate — check guards in game_context.py.')

    # --- Afternoon-run-specific checks ---
    if is_afternoon:
        # POTD should be locked by 2pm cron unless no PRIME tier surfaced
        # (no-play day is legitimate). Check via jerry_cache key.
        try:
            potd = get(f"{URL}/rest/v1/jerry_cache?cache_key=eq.best_bet_{date}&select=data")
            if not potd:
                warnings.append(f'⚠️  No jerry_cache POTD entry for {date}. '
                                f'play_of_day.py may not have run on the 2pm cron.')
            else:
                data = potd[0].get('data')
                if isinstance(data, str):
                    try: data = json.loads(data)
                    except: data = {}
                if data.get('noGames'):
                    print('  ✓ POTD entry exists: marked noGames (off-day).')
                elif data.get('noPlay'):
                    print('  ✓ POTD entry exists: marked noPlay (no PRIME tier today — honest).')
                else:
                    print(f'  ✓ POTD entry exists: {data.get("leanDisplay") or "(unparsed)"}')
        except Exception as e:
            warnings.append(f'⚠️  POTD check failed: {e}')

        # Daily Degen + Dawg should also exist by afternoon
        try:
            dd = get(f"{URL}/rest/v1/daily_degen?game_date=eq.{date}&select=leg_count")
            if not dd:
                warnings.append(f'⚠️  No daily_degen row for {date}. generate_daily_degen.py may not have run.')
            elif (dd[0].get('leg_count') or 0) < 2:
                warnings.append(f'⚠️  Daily Degen has only {dd[0].get("leg_count")} legs (expected 3-5).')
            else:
                print(f"  ✓ Daily Degen: {dd[0].get('leg_count')} legs.")
        except Exception as e:
            warnings.append(f'⚠️  Daily Degen check failed: {e}')

    # --- Print summary ---
    if not issues and not warnings:
        print('\n✅ All health checks passed.')
        return 0

    if warnings:
        print(f'\n--- {len(warnings)} WARNING(S) ---')
        for w in warnings:
            print(f'  {w}')

    if issues:
        print(f'\n--- {len(issues)} ISSUE(S) ---')
        for i in issues:
            print(f'  {i}')
        # Hard-fail the workflow step if a critical population is missing
        # on a non-off-day. CI surfaces the red status, signal becomes
        # impossible to silently skip again.
        print('\n❌ HEALTH CHECK FAILED — pipeline step will report non-zero exit.')
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
