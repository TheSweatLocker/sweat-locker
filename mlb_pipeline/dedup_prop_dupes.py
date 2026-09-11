"""Post-sweep prop deduplication (2026-08-23).

CONTEXT
-------
generate_props.py already dedups its top-N pool to one row per
(player, stat_family) — keeping the winning direction. But then
sweep_prop_coverage.py runs and inserts the OPPOSITE direction as
a COVERAGE stub so the shadow playbook has coverage on both sides.
apply_refit_verdict_override.py then auto-demotes COVERAGE → LEAN 55.

Result users see: pitcher X has both ks_over LEAN 55 AND ks_under
LEAN 55 as separate cards, indistinguishable except for direction.
Confusing (which side to play?) and clutters the surface.

FIX
---
Post-sweep, delete the losing-side row when BOTH sides exist for
the same (player, stat_family). Rule:
  - If ONE side has fired signals (opinionated pick from
    generate_props) and the OTHER only has `_coverage_stub`
    metadata, keep the opinionated one and delete the stub.
  - If BOTH have fired signals (rare — happens when generate_props
    dedup didn't collapse cleanly), keep the higher-tier / higher-
    conviction one.
  - If NEITHER has fired signals, keep the one with higher
    refit_conviction (breaks ties from COVERAGE demote uniform 55).

This runs AFTER apply_refit_verdict_override in the cron so the
COVERAGE→LEAN demote has already happened. Deletes are permanent —
the shadow playbook already scored on both sides in-memory earlier
in the cron, so the DB dup isn't needed for calibration.

Usage
-----
    python dedup_prop_dupes.py                # today ET
    python dedup_prop_dupes.py --date 2026-08-23
    python dedup_prop_dupes.py --dry-run
"""
import argparse, os, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json'}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _stat_family(prop_type: str) -> str:
    if not prop_type: return ''
    for suf in ('_over', '_under'):
        if prop_type.endswith(suf): return prop_type[:-len(suf)]
    return prop_type


def _fired_signal_count(signals) -> int:
    """Count non-underscore signal keys — proxy for 'opinionated pick'."""
    if not isinstance(signals, dict): return 0
    return sum(1 for k in signals.keys() if not k.startswith('_'))


def _rank(prop: dict) -> tuple:
    """Higher tuple wins the (player, family) matchup.

    Priority:
      1. Has fired signals (opinionated > stub)
      2. Tier rank (PRIME > STRONG > LEAN > SKIP > COVERAGE)
      3. Refit conviction
      4. Legacy conviction
    """
    tier_ord = {'PRIME': 4, 'STRONG': 3, 'LEAN': 2, 'SKIP': 1, 'COVERAGE': 0, 'PASS': 0}
    fired = _fired_signal_count(prop.get('signals'))
    tier = tier_ord.get((prop.get('tier') or '').upper(), 0)
    refit = float(prop.get('refit_conviction') or 0)
    conv = float(prop.get('conviction') or 0)
    return (fired > 0, tier, refit, conv)


# 2026-08-23 sport-universal registry — same pattern as coverage_audit /
# healthcheck / signal_tracker. NFL / NCAAF / etc auto-dedup when their
# prop pipelines produce rows. Add sport to registry to enable.
SPORT_REG = {
    'MLB':   ('mlb_pipeline_props',   'MLB'),
    'NFL':   ('nfl_pipeline_props',   'NFL'),
    'NCAAF': ('ncaaf_pipeline_props', 'NCAAF'),
    'NCAAB': ('ncaab_pipeline_props', 'NCAAB'),
    'NHL':   ('nhl_pipeline_props',   'NHL'),
    'NBA':   ('nba_pipeline_props',   'NBA'),
}


def run(game_date: str | None = None, dry_run: bool = False, sport: str = 'MLB') -> int:
    gd = game_date or _et_today()
    if sport not in SPORT_REG:
        print(f'  ⚠ unknown sport: {sport}')
        return 0
    props_table, sport_str = SPORT_REG[sport]
    print(f'=== dedup_prop_dupes · {sport} · {gd} ===')

    # 2026-09-11 limit bump 500 → 5000. Prior 500 cap on a 3191-row MLB
    # slate silently pulled only the first 500 rows into `props`, so the
    # `alive` set (line ~174) missed thousands of legitimate props. The
    # orphan-cleanup step then wiped ~360 prop_jerry_reads rows every
    # cron run — including all their input_snapshot.render_sections
    # payloads → no L5/L10 charts on the app cards. Symptom user saw:
    # graphs appeared after generate_prop_jerry_synthesis ran, then
    # disappeared minutes later after this script fired.
    r = requests.get(f'{SB}/rest/v1/{props_table}',
                     headers=H_READ,
                     params={'game_date': f'eq.{gd}',
                             'select': 'id,player_name,prop_type,direction,'
                                       'tier,conviction,refit_conviction,signals',
                             'limit': 5000},
                     timeout=30)
    if r.status_code != 200:
        print(f'  ⚠ fetch failed: {r.status_code}')
        return 0
    props = r.json() or []
    print(f'  {len(props)} total props')
    # Belt-and-suspenders: if we're within 100 of the limit, log a warning
    # so a future volume spike doesn't silently re-introduce the same bug.
    if len(props) >= 4900:
        print(f'  ⚠ warning: prop count {len(props)} near fetch limit — '
              f'consider bumping the limit again to avoid orphan wipe.')

    # Group by (player_name.lower, stat_family)
    groups: dict = defaultdict(list)
    for p in props:
        k = ((p.get('player_name') or '').lower(),
             _stat_family(p.get('prop_type') or ''))
        groups[k].append(p)

    dupes = {k: rows for k, rows in groups.items() if len(rows) > 1}
    print(f'  {len(dupes)} (player, family) pairs have duplicates')

    losers_to_delete: list = []
    loser_props: list = []  # keep full row so orphan cleanup can match on
                            # (player, prop_type, direction) — see safety
                            # rewrite of the orphan-sweep block below.
    for k, rows in dupes.items():
        rows.sort(key=_rank, reverse=True)
        winner = rows[0]
        losers = rows[1:]
        for loser in losers:
            losers_to_delete.append(loser['id'])
            loser_props.append(loser)
            print(f'  drop id={loser["id"]:>6} {loser["player_name"]:<22} '
                  f'{loser["prop_type"]:<10} {loser["direction"]:<5} '
                  f'[{loser["tier"]} {loser["conviction"]}, refit={loser.get("refit_conviction")}, '
                  f'fired={_fired_signal_count(loser.get("signals"))}]  '
                  f'← keep id={winner["id"]} '
                  f'{winner["prop_type"]}/{winner["direction"]} '
                  f'[{winner["tier"]} {winner["conviction"]}, '
                  f'fired={_fired_signal_count(winner.get("signals"))}]')

    if dry_run:
        print(f'\n  [DRY-RUN] would delete {len(losers_to_delete)} loser rows')
        return len(losers_to_delete)

    if not losers_to_delete:
        print('  ✅ no duplicates to clean up')
        return 0

    deleted = 0
    CHUNK = 100
    for i in range(0, len(losers_to_delete), CHUNK):
        chunk = losers_to_delete[i:i+CHUNK]
        id_list = ','.join(str(x) for x in chunk)
        dr = requests.delete(f'{SB}/rest/v1/{props_table}?id=in.({id_list})',
                              headers=H_WRITE, timeout=30)
        if dr.status_code in (200, 204):
            deleted += len(chunk)
        else:
            print(f'  ⚠ delete chunk failed: {dr.status_code} {dr.text[:200]}')

    # 2026-09-11 SAFETY REWRITE. Prior logic built `alive` from the
    # in-memory `props` slice — if that slice was truncated (which it was:
    # limit=500 vs 3191-row slate), thousands of legitimate props were
    # missing from `alive` → the orphan sweep classified their jerry_reads
    # as orphans and DELETED them. Symptom: prop_jerry_reads dropped from
    # ~400 to ~27 every cron cycle → app cards lost their L5/L10 charts.
    #
    # New rule: only delete jerry_reads that match a prop we KNOW we just
    # deleted this run (loser_props). Never delete a jerry_read just
    # because its parent prop wasn't in our `props` fetch — that's a
    # symptom of pagination, not an orphan.
    if deleted and loser_props:
        deleted_keys = {(lp['player_name'], lp['prop_type'], lp['direction'])
                        for lp in loser_props}
        jr = requests.get(f'{SB}/rest/v1/prop_jerry_reads',
                          headers=H_READ,
                          params={'game_date': f'eq.{gd}', 'sport': f'eq.{sport_str}',
                                  'select': 'id,player_name,prop_type,direction',
                                  'limit': 5000},
                          timeout=20).json() or []
        orphan_ids = [j['id'] for j in jr
                      if (j['player_name'], j['prop_type'], j['direction']) in deleted_keys]
        for i in range(0, len(orphan_ids), CHUNK):
            chunk = orphan_ids[i:i+CHUNK]
            id_list = ','.join(str(x) for x in chunk)
            requests.delete(f'{SB}/rest/v1/prop_jerry_reads?id=in.({id_list})',
                            headers=H_WRITE, timeout=30)
        print(f'  🧹 cleaned {len(orphan_ids)} orphaned prop_jerry_reads rows '
              f'(matched to {len(deleted_keys)} deleted props)')

    print(f'\n  ✅ deleted {deleted} duplicate prop rows')
    print(f'  now: {len(props) - deleted} unique (player, family) picks')
    return deleted


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--sport', default='MLB', choices=list(SPORT_REG.keys()))
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, dry_run=args.dry_run, sport=args.sport)


if __name__ == '__main__':
    main()
