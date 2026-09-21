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


def count_rows(table_path: str) -> int:
    """Return exact row count for a PostgREST query using Content-Range.

    Bypasses the 1000-row payload cap that would silently truncate a naive
    `select=id` fetch. Used by the prop volume floor check (2026-09-17)
    to protect against the Sept-11-class silent-truncation regression.
    """
    h = dict(H, **{'Prefer': 'count=exact', 'Range-Unit': 'items', 'Range': '0-0'})
    req = urllib.request.Request(u_root(table_path), headers=h, method='GET')
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            cr = r.headers.get('Content-Range') or ''
            # Format is "0-0/12345" — split on '/'
            if '/' in cr:
                tail = cr.split('/', 1)[1]
                return int(tail) if tail.isdigit() else -1
    except Exception:
        pass
    return -1


def u_root(path: str) -> str:
    return f'{URL}/rest/v1/{path.lstrip("/")}'


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

    # --- Pre-flight schema validation (added 2026-06-01) ---
    # Source of truth: supabase/migrations/*.sql. If any migration declares
    # columns that the live DB doesn't have, the pipeline has been writing
    # into stripped columns for some period (silent data loss). Hard-fail
    # here so the workflow turns red on the first run after a forgotten
    # migration instead of slowly bleeding through silent warnings.
    # 5/30→6/1 incident: recent_mastery columns sat un-applied for 2 days
    # before being noticed; this check would have failed the 5/31 morning
    # run and surfaced the gap immediately.
    try:
        from pathlib import Path
        from schema_validator import validate
        repo_root = Path(__file__).resolve().parent.parent
        # Scope to tables this pipeline writes to. Other tables (auth,
        # storage, third-party) are managed elsewhere and would create
        # noise if validated here.
        pipeline_tables = {
            'mlb_game_context', 'mlb_game_results', 'mlb_pipeline_props',
            'mlb_team_offense', 'mlb_team_vs_opp_recent', 'mlb_tier_calibration',
            'pitcher_projections', 'model_health', 'cohort_display_config',
            'tier_integrity_findings', 'prompt_templates',
        }
        drift = validate(
            repo_root / 'supabase' / 'migrations', URL, KEY,
            tables_to_check=pipeline_tables,
        )
        if drift:
            print('\n❌ SCHEMA DRIFT — migrations declare columns the live DB is missing:')
            for table, cols in sorted(drift.items()):
                print(f'   {table}:')
                for c in cols:
                    print(f'     - {c}')
            print('\nFix: apply the corresponding supabase/migrations/*.sql files in the')
            print('Supabase SQL editor (or via `supabase db push` if CLI configured).')
            print('Until applied, the pipeline silently strips these columns on write.')
            return 1
        print('✓ Schema in sync with migrations.')
    except Exception as e:
        # Don't let a validator bug brick the health check — degrade gracefully
        # but flag the failure so we can fix the validator itself.
        print(f'⚠️  Schema validator errored (not fatal): {type(e).__name__}: {e}')

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
        # 2026-07-17: previously hard-failed on any null. That block prevented
        # the AM cron from continuing on 7/17 when the Odds API had only
        # posted lines for 8 of 15 games — the health check saw 8/8 = 100%
        # null (because play_of_day.py hadn't run yet in the workflow order
        # OR ran and only wrote to a subset) and exited 1, killing card +
        # POTD generation for the day.
        # Threshold gated: >25% missing = real bug (issue), <=25% = warn.
        null_rate = null_sweat / len(games)
        if null_rate > 0.25:
            issues.append(f'❌ {pct(null_sweat, len(games))} games missing sweat_score. '
                          f'play_of_day.py may have skipped writes (>25% threshold). '
                          f'App will fall back to client-side calc.')
        else:
            warnings.append(f'⚠️  {pct(null_sweat, len(games))} games missing sweat_score '
                            f'(under 25% threshold — likely late-added games not yet scored).')

    # Close-line refresh check (added 2026-05-29). After 1pm ET / 17 UTC the
    # afternoon cron should have written close_total / close_spread. If they
    # are still NULL post-1pm, the 2pm cron either hasn't fired or it skipped
    # the odds write — surfaces silent afternoon-cron failures that otherwise
    # appear normal on the dashboard.
    # Close-line refresh check — percentage-gated 5/30 PM.
    # Original logic flagged ANY missing close_total after 1pm ET as
    # critical. False alarm: late-starting games (8pm+ ET first pitch)
    # often don't have close lines posted until ~1hr before game time.
    # New gate: >25% missing = critical (real Odds API failure), 1-25%
    # = warning (likely late games settling), 0 = silent pass. Avoids
    # firing red on 1-2 late games while still catching the real bug
    # where the whole slate fails to refresh.
    from datetime import datetime, timezone, timedelta
    et_hour_now = (datetime.now(timezone.utc) - timedelta(hours=4)).hour
    if et_hour_now >= 13:
        null_close_total = sum(1 for g in games if g.get('close_total') is None)
        if len(games) > 0:
            null_rate = null_close_total / len(games)
            if null_rate > 0.25:
                issues.append(
                    f'❌ {pct(null_close_total, len(games))} games missing close_total '
                    f'after {et_hour_now}:00 ET. >25% threshold breached — 2pm cron may have '
                    f'failed to refresh odds, or Odds API is down.'
                )
            elif null_close_total > 0:
                warnings.append(
                    f'⚠️  {pct(null_close_total, len(games))} games missing close_total '
                    f'after {et_hour_now}:00 ET — likely late-starting games still settling, monitor.'
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

    # --- Props pipeline: book-line attachment rate ---
    # Phase 1 (5/28) + Phase 2 (5/29) attach real book lines to pitcher
    # props and recalibrate conviction against the bettable edge. When
    # ODDS_API_KEY isn't set (or the Odds API is down), attach_book_lines
    # returns empty silently and every prop ships at the internal suggested
    # line — exactly the trust-killer those phases were built to prevent.
    #
    # 5/30 INCIDENT: ODDS_API_KEY was missing from the generate_props
    # workflow env block for ~3 days. Brandon Young U 5.5 K's shipped as
    # STRONG 80 when book was 3.5 (no edge, should have been SKIP 24). No
    # alarm fired because downstream just sees the prop publish — nothing
    # aggregated attachment rate. This check closes the loop: if pitcher
    # props are publishing but <50% have book_line attached, flag critical.
    try:
        props = get(f"{URL}/rest/v1/mlb_pipeline_props?game_date=eq.{date}&select=prop_type,book_line,tier")
        pitcher_types = ('ks_over', 'ks_under', 'bb_over', 'bb_under',
                         'ha_over', 'ha_under', 'outs_over', 'outs_under',
                         'er_over', 'er_under')
        pitcher_props = [p for p in props if p.get('prop_type') in pitcher_types]
        # Only flag when we have a meaningful sample (>=4 pitcher props). Earlier
        # in the day there may be 0-1 because the slate isn't fully scored yet.
        if len(pitcher_props) >= 4:
            with_book = sum(1 for p in pitcher_props if p.get('book_line') is not None)
            attach_rate = with_book / len(pitcher_props)
            if attach_rate < 0.50:
                # Bucket counts for context
                by_type = {}
                for p in pitcher_props:
                    by_type.setdefault(p.get('prop_type'), {'total': 0, 'with': 0})
                    by_type[p['prop_type']]['total'] += 1
                    if p.get('book_line') is not None:
                        by_type[p['prop_type']]['with'] += 1
                worst = sorted(by_type.items(), key=lambda kv: kv[1]['with']/max(1, kv[1]['total']))[:3]
                worst_str = ', '.join(f"{k} {v['with']}/{v['total']}" for k, v in worst)
                issues.append(
                    f'❌ Props book-line attach rate {with_book}/{len(pitcher_props)} '
                    f'({attach_rate*100:.0f}%) — Phase 1+2 recalibration not firing. '
                    f'Most likely: ODDS_API_KEY missing from generate_props workflow env, '
                    f'OR Odds API down. Worst categories: {worst_str}. '
                    f'Props will ship at internal lines with stale tiers until fixed.'
                )
            elif attach_rate < 0.80:
                # Partial coverage is normal — books only post lines for some
                # starters (fringe arms get skipped). 50-80% range is expected
                # mid-afternoon; <50% means the integration is broken.
                warnings.append(
                    f'⚠️  Props book-line attach rate {with_book}/{len(pitcher_props)} '
                    f'({attach_rate*100:.0f}%) — partial coverage, monitor.'
                )
            else:
                print(f'  ✓ Props book-line attach rate: {with_book}/{len(pitcher_props)} ({attach_rate*100:.0f}%)')
    except Exception as e:
        warnings.append(f'⚠️  Props book-line check failed: {e}')

    # --- Prop volume floor: Sept-11-class truncation protection ---
    # Pre-2026-09-11 the pipeline silently truncated the props fetch at the
    # PostgREST 1000-row cap, so composers saw ~300 PRIME/day instead of
    # ~250-300 out of the true ~3000-prop universe. If someone reverts one
    # of the pagination fixes for "performance" the regression would be
    # silent — attach-rate and grade counts would look fine, but the pool
    # composers pick from would shrink 10x. This check catches that.
    #
    # 2026-09-21 FIX: the floor was ABSOLUTE (issue <500, warn <1500, message
    # text "Expected 2000-4000"). Those numbers were read off a 15-game
    # September slate and assumed every slate looks like that. They don't:
    # late September has 3-game days, and the postseason runs 1-2 games. A
    # correct 3-game slate produced 301 props and failed the pipeline.
    #
    # Props scale with GAMES, not with the calendar. Measured 2026-09-17..21:
    #   09-21:  3 games ·  301 props · 100/game
    #   09-20: 15 games · 1597 props · 106/game
    #   09-19: 15 games · 1348 props ·  90/game
    #   09-18: 15 games · 1699 props · 113/game
    #   09-17:  9 games ·  905 props · 101/game
    # Tight band of 90-113. So gate on the RATIO and let slate size float.
    # Floors are deliberately slack against that band (40 = under half the
    # worst day) so this fires on regressions, not on thin coverage.
    _PPG_ISSUE = 40      # below half the observed floor = something is broken
    _PPG_WARN  = 70      # below the band but survivable = look at it
    try:
        n_props = count_rows(f'mlb_pipeline_props?game_date=eq.{date}')
        n_games = len(games)
        ppg = (n_props / n_games) if n_games else 0
        if n_props < 0:
            warnings.append('⚠️  Prop volume count query failed (Content-Range missing)')
        elif n_props == 0:
            # Slate exists but zero props — treat as issue only in afternoon
            if is_afternoon:
                issues.append(f'❌ 0 props on {date} — prop pipeline dead')
        elif not n_games:
            warnings.append(
                f'⚠️  {n_props} props on {date} but 0 games in context — '
                f'cannot rate-check prop volume.'
            )
        elif ppg < _PPG_ISSUE:
            issues.append(
                f'❌ Prop volume floor breach: {n_props} props across {n_games} games '
                f'on {date} = {ppg:.0f}/game (floor {_PPG_ISSUE}). Expected 90-113/game. '
                f'Likely a pagination regression (Sept-11-class truncation). '
                f'Check prop_synth / sharp_card_aggregator / grading_zero_fail fetch loops.'
            )
        elif ppg < _PPG_WARN:
            warnings.append(
                f'⚠️  Prop volume low: {n_props} props across {n_games} games on {date} '
                f'= {ppg:.0f}/game (expected 90-113). Monitor — partial coverage '
                f'or partial regression.'
            )
        else:
            print(f'  ✓ Prop volume: {n_props} props / {n_games} games ({ppg:.0f} per game)')

        # The 1000-row PostgREST cap has its own fingerprint, and the ratio
        # check above CANNOT see it on a small slate: 9 games x 111 = 1000
        # passes the ratio while being exactly truncated. Catch the cap
        # directly — a count parked just under 1000 on a slate that should
        # clear it is the Sept-11 signature.
        if 980 <= n_props <= 1000 and n_games >= 10:
            issues.append(
                f'❌ Prop count {n_props} on a {n_games}-game slate sits on the '
                f'PostgREST 1000-row cap. This is the Sept-11 truncation '
                f'signature — a fetch loop lost its pagination.'
            )
    except Exception as e:
        warnings.append(f'⚠️  Prop volume check failed: {e}')

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
