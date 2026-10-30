"""Re-tier frozen picks under the per-sport tier bars, WITHOUT repicking.

== 2026-10-30 . WHY THIS IS A RE-TIER AND NOT A REGENERATION ==============
ensemble_scorer.TIER_THRESHOLDS_BY_SPORT gave NBA and NHL a tier ladder on
their own score scale (the absolute 0.7 STRONG bar is above NBA's highest
score ever recorded, 0.62 -- see that table's note). Tonight's picks were
already scored and FROZEN under the old bars, and 7 NBA + 3 NHL game reads
are already published as receipts for games that tip in a few hours.

Regenerating the context would re-pull odds and rescore, which can move a
SIDE -- and a published receipt whose side moved is the defect in
project_receipts_grade_the_opposite_side_1028 (179 receipts grading the
opposite side, 148 of them frozen before the read was regenerated). So this
script changes exactly one field, `primary_play.tier`, recomputed from the
`_ensemble_sources` ALREADY STORED on the row. Side, market, line,
conviction and price are never written.

WHAT IT GUARDS AGAINST
  * It imports the bars, the sport ceiling and the credibility rule from
    ensemble_scorer rather than restating them, so the two cannot drift
    (feedback_fix_every_writer_to_the_table).
  * It RE-APPLIES model_edge.cap_tier_for_edge afterwards. Without that, a
    promotion would silently undo the negative-edge market check that
    demoted a pick to COVERAGE -- our own number not beating the price is
    still a reason to refuse, independent of the tier scale.
  * It only ever writes a tier the sport's ceiling allows.
  * The credibility rule here can only use `n >= 15`, because the stored
    chips carry `n` but not the registry `tier` the live gate also accepts.
    That is the STRICTER half, so this can refuse a pick the live engine
    would have allowed, never the reverse.
  * Every PATCH is confirmed by READBACK. A PostgREST patch refused by a
    trigger returns 204 with zero rows, byte-identical to success
    (feedback_pick_lock_refuses_with_a_silent_204).

Idempotent: a second run reports 0 changes.

USAGE
  python retier_from_sport_bars.py --dry-run
  python retier_from_sport_bars.py --apply
  python retier_from_sport_bars.py --apply --date 2026-10-30 --days 2
"""
from __future__ import annotations

import argparse
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
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

from ensemble_scorer import (  # noqa: E402
    MAX_TIER_BY_SPORT, RELATIVE_BAR_SPORTS, _TIER_RANK, bars_for)

CTX_TABLE = {
    'NBA': 'nba_game_context',
    'NHL': 'nhl_game_context',
}

MIN_CREDIBLE_N = 15

#: Shortfall, in points, at which our own projection is taken to CONTRADICT
#: the line we published. Same value consistency_watchdog uses, and for the
#: same reason: a 0.2 gap on a model with multi-point error is agreement.
COHERENCE_SHORTFALL = 1.0


def _proj_conventions():
    """The PROVEN projected_spread orientation per sport.

    Imported from consistency_watchdog rather than restated. That check has
    already shipped TWO sign-convention bugs of its own (an away-pick false
    positive, then an NFL home-margin one), so a second copy of this table
    is the last thing this repo needs -- and a sport missing from it must be
    SKIPPED, not guessed, which is what produced both false alarms.
    """
    try:
        from consistency_watchdog import _PROJ_HOME_POSITIVE
        return dict(_PROJ_HOME_POSITIVE)
    except Exception as e:
        print(f'  ! cannot import projection conventions ({type(e).__name__}) '
              f'-- coherence gate DISABLED, promotions unchecked')
        return {}


_PROJ_HOME_POS = _proj_conventions()


def coherence_ok(sport: str, ctx_row: dict, pp: dict) -> tuple[bool, str]:
    """False when our own projection does not cover the line we published.

    == 2026-10-30 . THE RE-TIER PROMOTED A SELF-CONTRADICTING PICK ========
    The first apply of this script moved Houston Rockets -6.5 from COVERAGE
    to STRONG on signal agreement alone (score 0.620, 3 classes -- NBA's
    best-evidenced pick on the slate) and it went onto the Sweat Card. The
    Daily Card watchdog then flagged it CRITICAL: our projection has Houston
    by 5.5 while we published -6.5, so our own number does not cover the
    line we are laying.

    Both facts were true at once. Signal agreement says WHICH SIDE; it says
    nothing about whether the projection clears the POSTED NUMBER, and a
    tier is a claim about the bet, not about the side. The watchdog only
    judges picks at PRIME/STRONG/LEAN, so a COVERAGE pick is exempt -- which
    means promoting a pick can EXPOSE an incoherence that was always there.
    Any promotion therefore has to clear the same bar the published surface
    is held to.
    """
    market = str(pp.get('type') or '').lower()
    if market not in ('rl', 'spread'):
        return True, ''
    hp = _PROJ_HOME_POS.get(str(sport or '').upper())
    if hp is None:
        return True, ''          # convention unproven for this sport -- skip
    ps, line = ctx_row.get('projected_spread'), pp.get('line')
    side = str(pp.get('side') or '').upper()
    if ps is None or line is None or side not in ('HOME', 'AWAY'):
        return True, ''
    try:
        norm = float(ps) * (1.0 if hp else -1.0)       # +ve = home favoured
        ln = float(line)
    except (TypeError, ValueError):
        return True, ''
    if ln >= 0:
        return True, ''          # taking points, not laying them
    need = abs(ln)
    got = norm if side == 'HOME' else -norm
    if (need - got) >= COHERENCE_SHORTFALL:
        return False, (f'projection has {side} by {got:.1f} but the pick lays '
                       f'{ln:+g} - our own number does not cover it')
    return True, ''


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def page(table, params):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['offset'] = off
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H_READ, params=p,
                         timeout=120)
        if r.status_code != 200:
            print(f'  ! {table} HTTP {r.status_code} {r.text[:180]}')
            return out
        d = r.json()
        out += d
        if len(d) < 1000:
            return out
        off += 1000


def features(pp: dict):
    """(win_score, margin, n_classes, chips) from the STORED ensemble trail."""
    src = pp.get('_ensemble_sources')
    if not isinstance(src, list) or not src:
        return None
    chips = [s for s in src if isinstance(s, dict)]
    if not chips:
        return None
    win = sum(_f(s.get('contribution')) or 0.0 for s in chips)
    ru = (pp.get('_ensemble_runner_up') or {})
    ru_src = ru.get('sources') if isinstance(ru, dict) else None
    runner = 0.0
    if isinstance(ru_src, list):
        runner = sum(_f(s.get('contribution')) or 0.0
                     for s in ru_src if isinstance(s, dict))
    classes = len({s.get('class') for s in chips if s.get('class')})
    return round(win, 4), round(win - runner, 4), classes, chips


def tier_for(sport: str, win: float, margin: float, classes: int,
             chips: list) -> str:
    """The tier these frozen inputs earn under the sport's own bars."""
    bars = bars_for(sport)
    ceiling = MAX_TIER_BY_SPORT.get(sport)
    tier = 'LEAN'
    for cand in ('PRIME', 'STRONG'):
        if ceiling and _TIER_RANK[cand] > _TIER_RANK[ceiling]:
            continue
        th = bars[cand]
        if (win >= th['min_score'] and classes >= th['min_classes']
                and margin >= th['min_margin']):
            tier = cand
            break
    # LEAN must also clear its own bar, else it is not even a lean.
    if tier == 'LEAN':
        th = bars['LEAN']
        if not (win >= th['min_score'] and classes >= th['min_classes']
                and margin >= th['min_margin']):
            return 'COVERAGE'
    # Credibility: every bettable tier in a percentile-barred sport needs at
    # least one chip with a real sample behind it.
    if tier in ('PRIME', 'STRONG', 'LEAN') and sport in RELATIVE_BAR_SPORTS:
        if not any((_f(c.get('n')) or 0) >= MIN_CREDIBLE_N for c in chips):
            return 'COVERAGE'
    return tier


def apply_edge_cap(pp: dict, tier: str) -> tuple[str, str | None]:
    """Re-apply the negative-edge market check on top of the new tier."""
    try:
        from model_edge import cap_tier_for_edge
    except ImportError:
        return tier, None
    if str(pp.get('type') or '').lower() != 'ml':
        return tier, None
    price = pp.get('_ml_price_at_pick')
    if price is None:
        return tier, None
    after, reason = cap_tier_for_edge(tier, pp.get('conviction'), price, 'ml')
    return after, reason


def run(date_from: str, days: int, apply: bool, sports: list[str]):
    hi = (datetime.fromisoformat(date_from).date()
          + timedelta(days=max(1, days) - 1)).isoformat()
    print(f'=== retier_from_sport_bars {date_from}..{hi} '
          f'{"APPLY" if apply else "DRY RUN"} ===')
    tot_w = tot_f = 0
    for sport in sports:
        tbl = CTX_TABLE[sport]
        rows = page(tbl, {'select': 'game_id,game_date,primary_play,'
                                    'projected_spread',
                          'game_date': [f'gte.{date_from}', f'lte.{hi}']})
        moved = same = noinfo = failed = 0
        print(f'\n  {sport}  {len(rows)} games  (bars {bars_for(sport)["STRONG"]}'
              f', ceiling {MAX_TIER_BY_SPORT.get(sport) or "none"})')
        for row in rows:
            pp = row.get('primary_play')
            if not isinstance(pp, dict) or not pp:
                noinfo += 1
                continue
            feat = features(pp)
            if not feat:
                noinfo += 1
                continue
            win, margin, classes, chips = feat
            old = str(pp.get('tier') or '').upper()
            new = tier_for(sport, win, margin, classes, chips)
            new, cap_reason = apply_edge_cap(pp, new)
            # A promotion must clear the published surface's own bar.
            if new in ('PRIME', 'STRONG', 'LEAN'):
                _ok, _why = coherence_ok(sport, row, pp)
                if not _ok:
                    new = 'COVERAGE'
                    cap_reason = f'incoherent: {_why}'
            if new == old:
                same += 1
                continue
            label = pp.get('label') or pp.get('side')
            arrow = f'{old or "?"} -> {new}'
            print(f'    {arrow:22s} {str(pp.get("type")):5s} '
                  f'{str(label)[:34]:34s} score={win:.3f} cls={classes} '
                  f'mgn={margin:.3f}' + (f'  [{cap_reason}]' if cap_reason else ''))
            if not apply:
                moved += 1
                continue
            new_pp = dict(pp)
            new_pp['tier'] = new
            new_pp['_retier'] = {
                'from': old, 'to': new, 'at': datetime.now(timezone.utc).isoformat(),
                'basis': 'ensemble_scorer.TIER_THRESHOLDS_BY_SPORT/2026-10-30',
                'win_score': win, 'margin': margin, 'classes': classes,
                'edge_cap': cap_reason,
            }
            pr = requests.patch(
                f'{SB}/rest/v1/{tbl}', headers=H_WRITE, timeout=60,
                params={'game_id': f'eq.{row["game_id"]}',
                        'game_date': f'eq.{row["game_date"]}'},
                json={'primary_play': new_pp})
            ok = False
            if pr.status_code in (200, 201):
                back = pr.json() or []
                ok = bool(back) and (
                    (back[0].get('primary_play') or {}).get('tier') == new)
            if ok:
                moved += 1
            else:
                failed += 1
                print(f'      ! NOT CONFIRMED (HTTP {pr.status_code}) '
                      f'{row["game_id"]}')
                # ══ 2026-10-30 · A REFUSED WRITE CAN STILL LAND HALF ══
                # nba_game_context has no pick_locked_at column, so the
                # refusal here is a TRIGGER that reverts primary_play->tier
                # while accepting every other key. Houston Rockets -6.5 was
                # left with tier=STRONG and a _retier stamp announcing
                # to=COVERAGE: the audit trail claiming a demotion the live
                # field never took. That is the partial-write class of
                # project_potd_partial_mirror_faked_a_win_1015, and a stamp
                # that lies is worse than no stamp. So restore the row and
                # record the ATTEMPT instead of the outcome.
                _keep = dict(pp)
                _keep.pop('_retier', None)
                _keep['_retier_refused'] = {
                    'wanted': new, 'kept': old, 'why': cap_reason,
                    'at': datetime.now(timezone.utc).isoformat(),
                    'note': 'tier write reverted by a lock trigger',
                }
                _rb = requests.patch(
                    f'{SB}/rest/v1/{tbl}', headers=H_WRITE, timeout=60,
                    params={'game_id': f'eq.{row["game_id"]}',
                            'game_date': f'eq.{row["game_date"]}'},
                    json={'primary_play': _keep})
                print(f'        restored row (HTTP {_rb.status_code}); '
                      f'tier stays {old}')
        print(f'    {sport}: {moved} re-tiered, {same} unchanged, '
              f'{noinfo} no ensemble trail, {failed} failed')
        tot_w += moved
        tot_f += failed
    print(f'\n=== {"applied" if apply else "DRY RUN"}: {tot_w} re-tiered, '
          f'{tot_f} failed ===')
    if not apply:
        print('Re-run with --apply to write.')
    return tot_w, tot_f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='YYYY-MM-DD (default today ET)')
    ap.add_argument('--days', type=int, default=1)
    ap.add_argument('--sport', action='append', choices=sorted(CTX_TABLE))
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    d = a.date or (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()
    run(d, a.days, a.apply and not a.dry_run, a.sport or sorted(CTX_TABLE))


if __name__ == '__main__':
    main()
