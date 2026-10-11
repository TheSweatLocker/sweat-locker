"""Does Jerry AGREEING with The Fade earn a 2-unit stake? — measure it first.

ANDY 2026-10-10, scoping the Split rework: "plays that have >70 public money,
and plays that our engine (jerry) agrees with get bumped to 2 units".

The fade rule itself is already built and graded (compute_fade_records,
surface fade_public_ats). The 2-UNIT BUMP is the one untested claim in that
spec, and it is testable BEFORE it ships rather than after.

WHY IT MUST BE TESTED FIRST. A stake multiplier is not a label — it doubles
real exposure. If the Jerry-agrees subset does not hit better than the
Jerry-disagrees subset, a 2u bump doubles variance on the same edge: the
published record gets swingier without getting better. There is already a
documented case of a tier change that moved nothing because it never touched
the stake (feedback_tier_demotion_needs_stake_boundary); this is its mirror —
a stake change on an unmeasured split.

THE POPULATION IS THE PUBLISHED ONE. This reuses compute_fade_records'
own page / latest_readings / load_sport / resolve, so the qualifying set is
identical to the record on the Fade surface rather than a lookalike derived a
second way. ATS-only and tickets-not-handle are inherited from ATS_MARKETS and
the same pct columns — the public's MONEYLINE side WINS (NCAAF 84.6-92.6%,
MLB 58.4-61.8% at >=70% tickets) so fading it is a disaster, and high handle
is SHARP money whereas high tickets is the public.

THE SPLIT
    AGREE     engine primary_play side == the fade side
    DISAGREE  engine picked the other side (it is ON the public side)
    SILENT    no engine pick, or a pick on a different market

The honest comparison is AGREE vs DISAGREE. Comparing AGREE to the pooled
fade is not a test — a subset differs from its own pool by construction.

VERDICT RULE, fixed before seeing the numbers: the bump is earned only if
AGREE beats DISAGREE by more than the 2SE of the difference. Level means
Jerry adds nothing to the fade and the stake stays flat at 1u.

WRITES NOTHING.

CLI
    python test_fade_jerry_agreement.py
    python test_fade_jerry_agreement.py --threshold 70
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import compute_fade_records as F

BREAKEVEN = 52.38


def _blob(v):
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip().startswith('{'):
        try:
            return json.loads(v)
        except Exception:                                   # noqa: BLE001
            return None
    return None


def jerry_call_sides(sport):
    """{game_id: side} from JERRY'S OWN CALL (jerry_reads.call_side), ATS only.

    2026-10-10 · Andy: "Jerry agreement synonmous with the gine pick or
    primary play?" They are NOT the same thing, and for this test the
    difference decides whether it is answerable at all:

      primary_play           the ENGINE's published pick, after the override
                             layers. Market mix: MLB ml 27 / total 13 / rl 2,
                             NCAAF rl 339 / ml 112, NFL rl 139+16 / ml 66,
                             NHL ml 154 / rl 13.
      jerry_reads.call_*     JERRY's own call out of the read. ATS share:
                             MLB 2% (18/812), NCAAF 59%, NFL 51%, NHL 8%.

    The Fade is ATS. MLB carries 326 of the 391 graded fade picks and has
    almost NO ATS opinion in either source, so on MLB the agreement condition
    cannot be evaluated — that is a property of the data, not a thin sample
    that will fill in. NCAAF and NFL are where it is measurable.

    Reads call_side, not call_text — and skips the rows where parse_synthesis
    leaked a whole markdown block into the column. Two rows carry the entire
    "**SIDE:** HOME / **LINE:** NULL / **CALL_TEXT:** ..." block as their
    call_side value, which would otherwise be read as a side.
    """
    _res_tbl, _ctx_tbl = F.SPORTS[sport]
    out = {}
    for z in F.page('jerry_reads', {'select': 'game_id,call_market,call_side',
                                    'sport': f'eq.{sport}'}):
        mkt = str(z.get('call_market') or '').strip().lower()
        side = str(z.get('call_side') or '').strip().upper()
        # Parse-failure guard: a leaked markdown block is long and carries
        # newlines. A real value is 'HOME'/'AWAY'/'rl'/'spread'. A parse
        # failure must not be read as a call (feedback_parse_failure_must_
        # not_overwrite) — skip it rather than guess a side out of it.
        if ('\n' in mkt or '\n' in side
                or len(side) > 5 or len(mkt) > 8):
            continue
        if mkt not in ('rl', 'spread', 'ats'):
            continue
        if side in ('HOME', 'AWAY'):
            out[str(z.get('game_id'))] = side
    return out


def engine_sides(sport):
    """{game_id: side} from the engine's published primary_play, ATS only.

    Reads the SIDE field, never the label — primary_play's prose names BOTH
    teams on 42% of rows, so a name match silently relabels away picks as
    home (feedback_check_which_side_a_column_indexes).
    """
    _res_tbl, ctx_tbl = F.SPORTS[sport]
    out = {}
    for z in F.page(ctx_tbl, {'select': 'game_id,primary_play'}):
        pp = _blob(z.get('primary_play'))
        if not pp:
            continue
        mkt = str(pp.get('type') or '').lower()
        # Only an ATS pick can agree or disagree with an ATS fade. An ml or
        # total pick is SILENT here, not a disagreement.
        if mkt not in ('rl', 'spread', 'ats'):
            continue
        side = str(pp.get('side') or '').upper()
        if side in ('HOME', 'AWAY'):
            out[str(z.get('game_id'))] = side
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--threshold', type=float, default=65.0)
    ap.add_argument('--min-n', type=int, default=20, dest='min_n')
    ap.add_argument('--source', default='jerry',
                    choices=('jerry', 'engine'),
                    help="'jerry' = jerry_reads.call_side (Jerry's own "
                         "call); 'engine' = primary_play (the published "
                         "engine pick). They are different things.")
    a = ap.parse_args()
    thr = a.threshold

    print(f'=== Does Jerry agreeing earn 2u? · fade at >={thr:.0f}% TICKETS '
          f'· ATS only\n')

    buckets = collections.defaultdict(list)       # (sport, group) -> [hit01]
    pooled = collections.defaultdict(list)        # group -> [hit01]
    for sport in sorted(F.SPORTS):
        splits = [r for r in F.page('public_splits_archive',
                                    {'select': 'game_id,sport,market,pick_side,'
                                               'captured_at,cz_bets_pct,'
                                               'fr_bettors_pct,ftp_bets_pct,'
                                               'oc_bets_pct',
                                     'sport': f'eq.{sport}'})
                  if str(r.get('market')) in F.ATS_MARKETS]
        if not splits:
            print(f'  {sport:<6} no ATS split rows')
            continue
        by_gid, by_teams, bridge = F.load_sport(sport)
        eng = (jerry_call_sides(sport) if a.source == 'jerry'
               else engine_sides(sport))
        latest = F.latest_readings(splits)
        qual = 0
        for (gid, side), slot in latest.items():
            pcts = slot['pcts']
            if not pcts:
                continue
            if (sum(v for _n, v in pcts) / len(pcts)) < thr:
                continue
            res, _d, _how = F.resolve(gid, by_gid, by_teams, bridge)
            if res not in ('home_covered', 'away_covered'):
                continue            # push or ungraded -> not a W/L
            qual += 1
            fade_side = 'AWAY' if side == 'HOME' else 'HOME'
            won = 1 if ((res == 'home_covered') == (fade_side == 'HOME')) else 0
            e = eng.get(gid)
            grp = ('SILENT' if e is None
                   else 'AGREE' if e == fade_side else 'DISAGREE')
            buckets[(sport, grp)].append(won)
            pooled[grp].append(won)
        print(f'  {sport:<6} {qual} graded qualifying fade picks')

    def rate(label, vals, pad=38):
        n = len(vals)
        if n < a.min_n:
            print(f'    {label:<{pad}}n={n:<5} under n>={a.min_n} — too thin '
                  f'to call')
            return None
        hit = statistics.fmean(vals) * 100
        se2 = (0.5 / n ** 0.5) * 100 * 2
        w = sum(vals)
        print(f'    {label:<{pad}}{w}-{n - w}  {hit:6.2f}%  n={n:<5} '
              f'+/-{se2:4.1f}pp  {hit - BREAKEVEN:+6.2f} vs BE')
        return hit, n

    print('\n  POOLED ACROSS SPORTS — the comparison that decides the stake:')
    ag = rate('AGREE   (engine on the fade side)', pooled['AGREE'])
    dg = rate('DISAGREE(engine on the public side)', pooled['DISAGREE'])
    si = rate('SILENT  (no ATS engine pick)', pooled['SILENT'])
    allv = pooled['AGREE'] + pooled['DISAGREE'] + pooled['SILENT']
    rate('ALL qualifying fade picks', allv)

    print('\n  BY SPORT:')
    for sport in sorted(F.SPORTS):
        got = any((sport, g) in buckets for g in ('AGREE', 'DISAGREE', 'SILENT'))
        if not got:
            continue
        print(f'    {sport}')
        for g in ('AGREE', 'DISAGREE', 'SILENT'):
            rate(f'      {g}', buckets[(sport, g)], pad=32)

    print('\n' + '=' * 72)
    if ag and dg:
        (ah, an), (dh, dn) = ag, dg
        diff = ah - dh
        # 2SE of a difference of two independent proportions, at p~0.5.
        se_d = math.sqrt(0.25 / an + 0.25 / dn) * 100 * 2
        print(f'  AGREE - DISAGREE = {diff:+.2f}pp, 2SE of the difference '
              f'+/-{se_d:.1f}pp')
        if diff > se_d:
            print('  -> THE BUMP IS EARNED on this sample. Jerry agreeing')
            print('     separates by more than the noise in the split.')
        else:
            print('  -> NOT EARNED. The split does not separate by more than')
            print('     its own noise, so a 2u stake would double exposure')
            print('     on the same edge and only widen the swings. Keep the')
            print('     stake flat at 1u and re-measure as n grows.')
    else:
        print('  Not enough graded picks in both arms to compare. The stake')
        print('  should stay FLAT until it can be measured — an untested')
        print('  multiplier is a real-money change backed by nothing.')
    print('\n  Reminder on the rule itself: 65% TICKETS measured better than')
    print('  70% on the larger sample (MLB +13.0u n=323 vs +2.18u n=225),')
    print('  and handle is the SHARP side, not the public one.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
