"""Re-score NBA / NHL picks after the row they were scored on is finished.

══ 2026-10-30 · NBA SCORES ONCE, EARLY, ON AN INCOMPLETE ROW ══════════════
ANDY: "The Steam Room needs to find a way to play nba, ncaab when online and
all other sports when live. THIS IS A MUST." The Sharp takes almost no NBA
because the ENGINE has almost no NBA opinion -- 86 of 107 games COVERAGE
over 14 days, 17 bettable.

That is not a bar being too high. nba_game_context._apply_ensemble runs
inside `Build NBA game context` (workflow step 493) and scores the row the
builder has in memory AT THAT MOMENT. Everything that enriches the row
arrives afterwards:

    521  Enrich NBA game context with injuries
    551  NBA Monte Carlo simulator
    871  ScoresAndOdds - scrape NBA splits
    880  Splits v2 aggregator
    910  Compute alignment (oddscrowd_snapshot)
    947  Derive cover probability
    965  Write line_snapshot

So the pick is formed before injuries, before the MC sim, and before every
piece of market data. NBA then never re-scores. NCAAF and NFL both have a
"Recompute primary_play with enriched signals" step for exactly this reason;
NBA and NHL never got one.

MEASURED, by re-scoring tonight's ten stored NBA rows as they now stand:
five of ten change tier. Lakers, Denver and Indiana rise COVERAGE->STRONG;
Detroit and Houston fall STRONG->COVERAGE. The engine's own current opinion
differs from the published pick on half the slate.

Read that carefully in both directions. It is NOT a claim that the later
read is better -- it is proof the pick is made from a row that is materially
incomplete. Two of the changes are DOWNGRADES, including Houston -6.9, the
pick whose receipt was voided tonight for failing its own coherence check.
A recompute that only ever promoted would be a volume trick; this one
demotes too, which is what makes it a correction rather than a loosening.

WHY THIS IS ONE SCRIPT AND NOT A THIRD COPY
recompute_ncaaf_primary_play.py and recompute_nfl_primary_play.py are
373 and ~400 lines of sport-specific near-duplicate, and tonight has been a
long lesson in what two copies of one rule costs. This calls each sport's
OWN _apply_ensemble, so every gate those builders apply -- the season gate,
the defensive gates, apply_to_pick -- comes along unchanged and cannot drift
from the build path. The only sport-specific thing here is a table name.

NCAAB is deliberately absent: ncaab_game_context has no _apply_ensemble, it
scores through its own compute_primary_play, so it is not this script's job.

SAFETY, mirroring the NCAAF recompute rather than inventing new semantics:
  * pick_lock.lock_active(sport) is honoured per sport, and when a published
    pick exists under an active lock the published one is KEPT and the
    difference is printed. Unlocking is PICK_EMERGENCY_UNLOCK or
    {SPORT}_PICK_EMERGENCY_UNLOCK, never a --force flag, because pipelines
    pass --force on routine steps.
  * manual_pick_override.preserve runs so a hand-set pick is never
    statistically overwritten (feedback_lock_must_see_a_manual_decision).
  * A recompute that produces NO decision leaves the stored pick alone. An
    absence is not an improvement, and erasing a published pick because a
    later pass went quiet is how a surface goes dark.
  * updated_at is stamped, because the leak guards compare it to tip-off and
    a recompute that moves the pick without moving the stamp makes every
    downstream backtest wrong (see the 2026-09-30 correction in the NCAAF
    recompute).
  * Writes are confirmed by READBACK, since a refused PATCH returns 204 and
    looks identical to success (feedback_204_is_not_a_write).

USAGE
  python recompute_ensemble_primary_play.py --sport NBA            # dry run
  python recompute_ensemble_primary_play.py --sport NBA --apply
  python recompute_ensemble_primary_play.py --sport NBA --sport NHL --days 2
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

#: sport -> (context table, module exposing _apply_ensemble)
SPORTS = {
    'NBA': ('nba_game_context', 'nba_game_context'),
    'NHL': ('nhl_game_context', 'nhl_game_context'),
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _label(pp) -> str | None:
    if isinstance(pp, dict):
        return pp.get('label')
    return pp


def _tier(pp) -> str:
    if isinstance(pp, dict):
        return str(pp.get('tier') or 'NONE')
    return 'NONE'


def _preserve_manual(old_pp, new_pp):
    """A hand-set pick is a decision, never a candidate for re-scoring."""
    try:
        from manual_pick_override import preserve
        return preserve(old_pp or {}, new_pp)
    except Exception as e:                                   # noqa: BLE001
        print(f'  ! manual-override preserve unavailable ({e}) -- '
              f'leaving the stored pick alone to be safe')
        return old_pp, 'preserve-unavailable'


def _locked(sport: str, table: str, game_id, new_pp) -> bool:
    try:
        from pick_lock import lock_active
    except Exception as e:                                   # noqa: BLE001
        print(f'  ⚠ pick_lock unavailable ({type(e).__name__}) -- refusing '
              f'to write rather than bypass a lock we cannot read')
        return True
    if not lock_active(sport):
        return False
    try:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H_R, timeout=20,
                         params={'game_id': f'eq.{game_id}',
                                 'select': 'primary_play'})
        rows = r.json() if r.status_code == 200 else []
    except Exception:                                        # noqa: BLE001
        return True              # cannot verify under a lock -> refuse
    if not rows:
        return False
    published = rows[0].get('primary_play')
    if not published:
        return False             # a gap is not a change
    old, new = _label(published), _label(new_pp)
    if old != new:
        print(f'    🔒 {sport} lock: kept published {old!r} '
              f'(recompute wanted {new!r})')
    return True


def _patch(table: str, game_id, pp: dict) -> bool:
    payload = {'primary_play': pp,
               'updated_at': datetime.now(timezone.utc).isoformat()}
    r = requests.patch(
        f'{SB}/rest/v1/{table}', headers=H_W, timeout=30,
        params={'game_id': f'eq.{game_id}'}, json=payload)
    if r.status_code not in (200, 201, 204):
        print(f'    ! HTTP {r.status_code} {r.text[:120]}')
        return False
    chk = requests.get(f'{SB}/rest/v1/{table}', headers=H_R, timeout=20,
                       params={'game_id': f'eq.{game_id}',
                               'select': 'primary_play'})
    rows = chk.json() if chk.status_code == 200 else []
    if not rows:
        print('    ! readback found no row')
        return False
    return _label(rows[0].get('primary_play')) == _label(pp)


def write_shadow(sport: str, date_from: str, picks: list) -> bool:
    """Record the recomputed opinion WITHOUT touching the published pick.

    == 2026-10-30 . A SECOND ENGINE MUST EARN ITS PLACE ================
    The dry run changes 9 of 10 NBA picks and promotes 5, which would take
    NBA from 2 bettable to 5 -- exactly the volume Andy is asking for. It
    goes to a shadow anyway, for two measured reasons.

    First, NBA has 0 of 32 enabled game signals with a measured hit rate
    (project_engine_selects_on_zero_measured_signals_1028), so promoting
    these swaps one unmeasured opinion for another and calls the churn
    progress.

    Second, this repo has already run the experiment. lr_v1 was a second
    engine that OVERWROTE primary_play on 457 picks, destroyed
    _ensemble_sources, and on the 63 judgeable side flips it LOST --
    NHL+NCAAF 42.0% against the ensemble's 57.1% on n=50
    (project_lr_v1_override_erases_attribution_1027). The lesson was
    "make it MERGE, not replace"; the cheaper version of that lesson is
    "make it SHADOW until it wins".

    And the NHL output argues for caution on its own: all three promotions
    are OVER totals, the same shape as the five identical NCAAB UNDERs --
    one calibration bias restated, in a market measured at 43.2% and
    unstable.

    Written to jerry_cache rather than a new column so this needs no
    migration and can be graded against the live pick later. Append-only by
    date and sport.
    """
    key = f'ensemble_recompute_shadow_{sport}_{date_from}'
    # jerry_cache's unique key is cache_key, NOT game_id -- an on_conflict
    # on game_id returns 42P10, "no unique or exclusion constraint matching
    # the ON CONFLICT specification", which is the same mistake as naming a
    # conflict target signal_sources lacks
    # (feedback_execute_the_migration_not_just_read_it). Convention taken
    # from buy_down_calibration.py:214, which already upserts this table.
    # Both columns are populated because readers query either one.
    payload = {
        'cache_key': key,
        'game_id': key,
        'sport': sport,
        # jerry_cache.narrative is NOT NULL (23502 without it). The
        # shadow is machine-read, but the column still has to say
        # something true if a human opens the row.
        'narrative': (f'Shadow re-score of {sport} picks on the '
                      f'completed context row. The published '
                      f'primary_play is untouched.'),
        'data': {'sport': sport, 'slate_date': date_from,
                 'generated_at': datetime.now(timezone.utc).isoformat(),
                 'engine': 'ensemble_v2 re-scored on the completed row',
                 'note': ('shadow only - the published primary_play is '
                          'untouched; promote only on a graded record that '
                          'beats it'),
                 'picks': picks},
    }
    r = requests.post(f'{SB}/rest/v1/jerry_cache?on_conflict=cache_key',
                      headers={**H_W, 'Prefer': 'resolution=merge-duplicates,'
                                                'return=representation'},
                      json=[payload], timeout=30)
    if r.status_code not in (200, 201, 204):
        print(f'  ! shadow write failed {r.status_code} {r.text[:140]}')
        return False
    chk = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H_R, timeout=20,
                       params={'cache_key': f'eq.{key}',
                               'select': 'cache_key'})
    ok = bool(chk.json() if chk.status_code == 200 else [])
    print(f'  {"CONFIRMED" if ok else "NOT CONFIRMED"} shadow -> '
          f'jerry_cache.{key} ({len(picks)} pick(s))')
    return ok

def run_sport(sport: str, date_from: str, days: int, apply: bool) -> dict:
    table, modname = SPORTS[sport]
    try:
        mod = importlib.import_module(modname)
        apply_ensemble = getattr(mod, '_apply_ensemble')
    except Exception as e:                                   # noqa: BLE001
        print(f'  ! {sport}: cannot load {modname}._apply_ensemble '
              f'({type(e).__name__}: {e}) -- skipped')
        return {'err': 1}
    hi = (datetime.fromisoformat(date_from).date()
          + timedelta(days=max(1, days) - 1)).isoformat()
    r = requests.get(f'{SB}/rest/v1/{table}', headers=H_R, timeout=120,
                     params={'select': '*',
                             'game_date': [f'gte.{date_from}', f'lte.{hi}'],
                             'limit': '400'})
    if r.status_code != 200:
        print(f'  ! {sport}: HTTP {r.status_code} {r.text[:140]}')
        return {'err': 1}
    rows = r.json()
    shadow: list = []
    st = {'rows': len(rows), 'changed': 0, 'promoted': 0, 'demoted': 0,
          'locked': 0, 'no_decision': 0, 'manual': 0, 'failed': 0,
          'unchanged': 0}
    print(f'\n  {sport}: {len(rows)} row(s) {date_from}..{hi}')
    for row in rows:
        old_pp = row.get('primary_play')
        probe = dict(row)
        # Clear it so "no decision" is distinguishable from "unchanged".
        probe['primary_play'] = None
        try:
            apply_ensemble(probe)
        except Exception as e:                               # noqa: BLE001
            print(f'    ! {row.get("game_id")}: scorer raised '
                  f'{type(e).__name__}: {e}')
            st['failed'] += 1
            continue
        new_pp = probe.get('primary_play')
        if not new_pp:
            st['no_decision'] += 1
            continue
        new_pp, note = _preserve_manual(old_pp, new_pp)
        if note:
            st['manual'] += 1
            continue
        if _label(old_pp) == _label(new_pp) and _tier(old_pp) == _tier(new_pp):
            st['unchanged'] += 1
            continue
        rank = {'NONE': 0, 'COVERAGE': 1, 'PASS': 1, 'LIGHT_LEAN': 2,
                'LEAN': 3, 'STRONG': 4, 'PRIME': 5}
        o, n = rank.get(_tier(old_pp), 0), rank.get(_tier(new_pp), 0)
        arrow = '->' if n == o else ('UP' if n > o else 'DOWN')
        if n > o:
            st['promoted'] += 1
        elif n < o:
            st['demoted'] += 1
        st['changed'] += 1
        mm = f"{str(row.get('away_team'))[:16]} @ {str(row.get('home_team'))[:16]}"
        print(f'    {mm:36s} {_tier(old_pp):10s} {arrow:4s} '
              f'{_tier(new_pp):10s} {str(_label(new_pp))[:26]}')
        shadow.append({'game_id': row.get('game_id'), 'matchup': mm,
                       'was_tier': _tier(old_pp), 'was': _label(old_pp),
                       'now_tier': _tier(new_pp), 'now': _label(new_pp),
                       'direction': arrow,
                       'market': (new_pp or {}).get('type'),
                       'side': (new_pp or {}).get('side'),
                       'line': (new_pp or {}).get('line'),
                       'conviction': (new_pp or {}).get('conviction')})
        if not apply:
            continue
        if _locked(sport, table, row.get('game_id'), new_pp):
            st['locked'] += 1
            continue
        if _patch(table, row.get('game_id'), new_pp):
            pass
        else:
            st['failed'] += 1
            print('      ! NOT CONFIRMED')
    if shadow:
        write_shadow(sport, date_from, shadow)
    print(f'    {sport}: changed={st["changed"]} '
          f'(promoted={st["promoted"]} demoted={st["demoted"]}) '
          f'unchanged={st["unchanged"]} no_decision={st["no_decision"]} '
          f'manual={st["manual"]} locked={st["locked"]} '
          f'failed={st["failed"]}')
    return st


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', action='append', choices=sorted(SPORTS))
    ap.add_argument('--date', default=None)
    ap.add_argument('--days', type=int, default=1)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    d = a.date or _et_today()
    sports = a.sport or sorted(SPORTS)
    print(f'=== recompute_ensemble_primary_play '
          f'{"APPLY" if a.apply else "DRY RUN"} · {", ".join(sports)} ===')
    tot = {'changed': 0, 'failed': 0}
    for sp in sports:
        st = run_sport(sp, d, a.days, a.apply)
        tot['changed'] += st.get('changed', 0)
        tot['failed'] += st.get('failed', 0) + st.get('err', 0)
    print(f'\n=== {tot["changed"]} pick(s) would change, '
          f'{tot["failed"]} failure(s) ===')
    if not a.apply:
        print('Re-run with --apply to write. An active pick lock will still '
              'keep the published pick.')
    raise SystemExit(1 if tot['failed'] else 0)


if __name__ == '__main__':
    main()
