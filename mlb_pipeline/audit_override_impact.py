"""What did the overrides actually change, and were they right?

Andy 2026-09-25: "should we eliminate the refit override, does it even work or
does it just constantly cause confusion."

That question was unanswerable. prop_jerry_reads.call_verdict is the SHIPPED
value and eight scripts mutate it in place, so the model's own call was gone by
the time anyone looked. The refit override could be compared to results but
never to the decision it replaced.

Migration 20260925a adds generator_verdict / generator_conviction, an immutable
snapshot written once at insert. This reads both and answers three things:

  1. HOW OFTEN an override moves a call, and in which direction.
  2. WHETHER the move was right — the shipped call's record vs the record the
     generator's call would have produced on the same rows.
  3. PER RULE, so a rule that earns its keep is separable from one that does
     not, instead of judging "the override" as one thing.

That second one is the point. A cap that demotes a 45% population is doing its
job; the same cap on a 60% population is destroying edge. Same rule, opposite
verdict, and until now neither was visible.

NOTE ON WHAT CAN BE ANSWERED. Rows created before 2026-09-25 that were already
overridden carry a NULL snapshot — the original is genuinely unrecoverable and
the migration does not guess. Those rows are reported as "no snapshot" and
excluded from the comparison rather than silently folded in. The picture fills
in from here.

BREAKEVEN is 54.2%, not 52.4% — average published price is -118.6.

    python audit_override_impact.py                  # last 30d, MLB
    python audit_override_impact.py --sport NFL --days 14
    python audit_override_impact.py --since 2026-09-22   # post L5-leak-fix only
"""
import argparse
import os
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import requests

from sweat_dates import et_shift          # DST-correct ET offset

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in (open(os.path.join(_HERE, '.env'), encoding='utf-8')
              if os.path.exists(os.path.join(_HERE, '.env')) else []):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

# Real breakeven at the prices we actually publish, not the -110 ideal.
BREAKEVEN = 54.2

# The L5 lookback leak means anything before this date had inflated form
# signals. Records spanning it are not comparable.
LEAK_FIX_DATE = '2026-09-22'

_RULE_RE = re.compile(r'\[Auto-[a-z-]+ \d{4}-\d{2}-\d{2} ([A-Z_0-9]+)')

# Props live in one table per sport. NCAAF/NCAAB have none by policy
# (CLAUDE.md: "NCAAF + NCAAB: no Props anywhere").
PROP_TABLE = {'MLB': 'mlb_pipeline_props', 'NBA': 'nba_pipeline_props',
              'NFL': 'nfl_pipeline_props', 'NHL': 'nhl_pipeline_props'}


def page(path: str, params: dict) -> list:
    """Paginate — PostgREST caps at 1000 and says so only in a header."""
    out, off = [], 0
    while True:
        q = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, params=q, timeout=90)
        if r.status_code not in (200, 206):
            body = (r.text or '')[:200]
            if 'generator_verdict' in body:
                print('\n  Migration 20260925a has not been applied yet.\n'
                      '  Apply supabase/migrations/20260925a_prop_jerry_'
                      'generator_snapshot.sql, then re-run.\n')
                sys.exit(3)
            print(f'  ERROR {r.status_code}: {body}')
            sys.exit(2)
        b = r.json()
        if not isinstance(b, list):
            print(f'  ERROR: {str(b)[:200]}')
            sys.exit(2)
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def rule_of(row: dict) -> str:
    """Name the mutator that last moved this row.

    == 2026-11-14 . A RULE THAT WRITES NO audit_notes IS NOT 'NO_OVERRIDE' ==
    The eight PATCH-style mutators stamp `audit_notes` with a bracketed
    marker, and that is all this function used to read. `_apply_cell_gate`
    does not: it stamps `input_snapshot.cell_gate` instead. So every
    cell-gate demotion -- 2,671 of them, the single largest demoter in the
    repo -- was filed under 'NO_OVERRIDE', the bucket this report treats as
    the untouched control. A suppression rule hiding inside its own control
    group cannot be judged by its own report.
    """
    gate = (row.get('input_snapshot') or {}).get('cell_gate')
    if isinstance(gate, dict) and gate.get('demoted_from'):
        return f"CELL_GATE({gate.get('gate') or 'unknown'})"
    hits = _RULE_RE.findall(row.get('audit_notes') or '')
    return hits[-1] if hits else 'NO_OVERRIDE'


def rec(rows: list) -> tuple:
    """(wins, losses, hit%) over decided rows only; pushes excluded."""
    w = sum(1 for x in rows if str(x.get('result') or '').upper().startswith('W'))
    l = sum(1 for x in rows if str(x.get('result') or '').upper().startswith('L'))
    return w, l, (w / (w + l) * 100 if (w + l) else None)


def fmt(w, l, pct, n_floor=30) -> str:
    n = w + l
    if not n:
        return f'{"—":>16s}'
    flag = '' if n >= n_floor else ' (n<30)'
    return f'{w:3d}-{l:<3d} n={n:4d} {pct:5.1f}%{flag}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='MLB')
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--since', default=None,
                    help='explicit start date; overrides --days')
    args = ap.parse_args()

    # et_shift(), not timedelta(hours=4): a hardcoded -4 disagrees with true
    # ET for one hour a night through EST and silently shifts the window a
    # day (feedback_hardcoded_et_offset_breaks_in_est).
    since = args.since or (
        (datetime.now(timezone.utc) - et_shift()).date()
        - timedelta(days=args.days)).isoformat()

    rows = page('prop_jerry_reads', {
        'select': 'id,game_date,player_name,prop_type,direction,prop_line,'
                  'call_verdict,conviction,generator_verdict,'
                  'generator_conviction,audit_notes,result,book_odds,'
                  'input_snapshot',
        'sport': f'eq.{args.sport}', 'game_date': f'gte.{since}',
        'order': 'game_date.asc'})

    print(f'=== override impact · {args.sport} · since {since} ===')
    print(f'    {len(rows)} reads')
    if since < LEAK_FIX_DATE:
        print(f'    ⚠ window starts before the L5 lookback leak fix '
              f'({LEAK_FIX_DATE}). Rows before that date carry inflated form '
              f'signals — measured at +6.7pp on untouched props — so records '
              f'spanning it are not comparable. Use --since {LEAK_FIX_DATE}.')

    snap = [x for x in rows if x.get('generator_verdict')]
    print(f'    {len(snap)} with a generator snapshot, '
          f'{len(rows) - len(snap)} without (pre-migration overridden rows)')
    if not snap:
        print('\n  No snapshots yet. Reads created from now on will carry one; '
              'this fills in as the pipeline runs.')
        return

    moved = [x for x in snap
             if (x.get('generator_verdict') or '') != (x.get('call_verdict') or '')
             or (x.get('generator_conviction') is not None
                 and x.get('generator_conviction') != x.get('conviction'))]
    verdict_moved = [x for x in moved
                     if (x.get('generator_verdict') or '') != (x.get('call_verdict') or '')]
    print(f'    {len(moved)} touched by a mutator '
          f'({len(verdict_moved)} had the VERDICT changed, '
          f'{len(moved) - len(verdict_moved)} conviction only)')

    print('\n--- 1. WHERE CALLS MOVED (generator -> shipped) ---')
    trans = Counter((x.get('generator_verdict'), x.get('call_verdict'))
                    for x in verdict_moved)
    if not trans:
        print('    no verdict changes in window')
    for (g, c), n in trans.most_common(12):
        sel = [x for x in verdict_moved
               if x.get('generator_verdict') == g and x.get('call_verdict') == c]
        w, l, p = rec(sel)
        print(f'    {str(g):8s} -> {str(c):8s} {n:4d}   shipped went {fmt(w, l, p)}')

    print('\n--- 2. WAS THE OVERRIDE RIGHT? ---')
    print('    On rows a mutator touched, the record the SHIPPED call produced.')
    print('    A demotion is correct when that record is below breakeven —')
    print('    it kept a losing population off the cards.')
    w, l, p = rec(moved)
    print(f'\n    touched by a mutator : {fmt(w, l, p)}')
    # Membership by id — `x not in moved` compares dicts field by field across
    # the whole list, which is O(n²) on a 12k-row table.
    moved_ids = {x['id'] for x in moved}
    un = [x for x in snap if x['id'] not in moved_ids]
    w2, l2, p2 = rec(un)
    print(f'    untouched            : {fmt(w2, l2, p2)}')
    print(f'    breakeven            : {BREAKEVEN}%')
    # 2026-11-14: both sides need n>=30 before this prints a direction.
    # On NFL it read "mutators are touching the PROFITABLE population --
    # Inverted, investigate" off 4-1, n=5, against an untouched n=934. The
    # n<30 flag was already on the figure and the verdict ignored it, which
    # is how a flagged number still ends up as a conclusion
    # (feedback_sample_size_with_pct).
    if p is not None and p2 is not None and (w + l) >= 30 and (w2 + l2) >= 30:
        if p < BREAKEVEN <= p2:
            print('\n    -> mutators are demoting a losing population while '
                  'leaving a profitable one alone. Working as intended.')
        elif p >= BREAKEVEN > p2:
            print('\n    -> mutators are touching the PROFITABLE population and '
                  'leaving the losing one. Inverted — investigate before trusting.')
        else:
            print('\n    -> both sides land on the same side of breakeven; '
                  'this window does not separate them.')
    else:
        print(f'\n    -> not called: needs n>=30 on BOTH sides '
              f'(touched n={w + l}, untouched n={w2 + l2}). Note that a '
              f'demotion\n       ships PASS and grades NO_ACTION, so the '
              f'touched side is usually n=0 here —\n       section 4 is '
              f'where a suppressed population becomes measurable.')

    print('\n--- 3. PER RULE ---')
    by_rule = defaultdict(list)
    for x in snap:
        by_rule[rule_of(x)].append(x)
    print(f'    {"rule":30s} {"shipped record":>22s}   {"avg conv move":>13s}')
    for r, sel in sorted(by_rule.items(), key=lambda z: -len(z[1])):
        w, l, p = rec(sel)
        deltas = [x['conviction'] - x['generator_conviction'] for x in sel
                  if x.get('conviction') is not None
                  and x.get('generator_conviction') is not None]
        dv = f'{sum(deltas)/len(deltas):+6.1f}' if deltas else '     —'
        verdict = ''
        if p is not None and (w + l) >= 30:
            verdict = '  ✓ earns it' if (
                (r != 'NO_OVERRIDE' and p < BREAKEVEN) or
                (r == 'NO_OVERRIDE' and p >= BREAKEVEN)
            ) else '  ⚠ check'
        print(f'    {r:30s} {fmt(w, l, p):>22s}   {dv:>13s}{verdict}')

    print('\n    "earns it" on a mutator rule means the population it touched '
          'lost money,\n    so demoting it was correct. It is not a claim the '
          'rule made money.')

    _suppression_section(args.sport, since, snap)


# ══ 2026-11-14 · A SUPPRESSION GATE CANNOT BE JUDGED BY WHAT IT SHIPS ══════
# Everything above reads `prop_jerry_reads.result`. A demotion ships
# call_verdict=PASS, and grade_prop_jerry_reads maps PASS -> 'NO_ACTION'
# ("Jerry didn't bet"). `rec()` counts neither a W nor an L for NO_ACTION, so
# a demoted row contributes n=0 and the gate's population is unmeasurable
# through this report. Measured 2026-11-14: 2,671 gated reads, 2,263
# NO_ACTION and 408 NULL, **0 graded**.
#
# That closes the loop the workflow comment calls "the documented path to
# opening that surface on evidence": demote -> never scored -> no evidence ->
# stays demoted, for ever, whether or not the gate is right
# (feedback_suppression_gate_needs_shadow).
#
# The outcome does exist. `<sport>_pipeline_props` is graded by its own
# grader, so the prop the read was built from carries Win/Loss/Void. Joining
# to it makes the suppressed population falsifiable in both directions.
#
# TWO THINGS THIS IS NOT. It is not our record -- we did not place these, so
# it is printed as a counterfactual and never folded into a surface record
# (feedback_surface_records_trust_levels). And a cell that clears breakeven
# here is not yet an allowlist candidate: it must also beat the BLIND
# direction-only control printed beside it. That control is the whole reason
# this section is trustworthy. On the first run NBA steals_under showed
# 70.4% n=206 ROI +12.9% inside the demoted set, which looks like real
# suppressed edge until the blind column lands: steals_under across every
# graded NBA prop is 51.8% n=1,733 at -2.9%, and its 65.3% line-1.5 bucket
# is priced -200 for +0.4% -- the juice trap CLAUDE.md already names.
# Without the control that cell gets allowlisted off a hot fortnight
# (feedback_signal_needs_a_direction_control).

def _pkey(game_date, player, prop_type, direction, line) -> tuple:
    """The join key, and game_date is NOT optional in it.

    == 2026-11-14 . THE SAME MEASUREMENT TWICE, TWO DIFFERENT ANSWERS ======
    Written first without game_date. (player, prop_type, direction, line) is
    not unique across a season -- 31,895 graded NBA props collapse onto
    11,730 such keys -- so a last-write-wins index kept ONE arbitrary night
    per key, and the read for 11-05 was scored against whichever night the
    unordered PostgREST page happened to deliver last. Two runs of the same
    query disagreed by 11.7 points of ROI on reb_under (+13.2% then +1.5%)
    and by 5 points of hit rate; the honest figure with the date in the key
    is +3.4%. With game_date the key is unique on all 31,895 rows.
    feedback_unordered_data_zero_picks_a_vintage.
    """
    try:
        line = str(float(line))
    except (TypeError, ValueError):
        line = str(line)
    return (str(game_date), str(player).strip().lower(),
            str(prop_type).strip().lower(),
            str(direction).strip().lower(), line)


def _roi(rows: list, price_key=lambda r: r.get('book_odds')) -> tuple:
    """(roi_pct, n_priced) at the real published price, never a flat -110
    (feedback_flat_110_inflates_every_ml_figure)."""
    units, n = 0.0, 0
    for r in rows:
        res = str(r.get('_outcome') or '').upper()
        if not (res.startswith('W') or res.startswith('L')):
            continue
        o = price_key(r)
        if o in (None, ''):
            continue
        try:
            o = float(o)
        except (TypeError, ValueError):
            continue
        n += 1
        units += (o / 100.0 if o > 0 else 100.0 / abs(o)) \
            if res.startswith('W') else -1.0
    return ((units / n * 100.0) if n else None), n


def _wl(rows: list) -> tuple:
    w = sum(1 for r in rows if str(r.get('_outcome') or '').upper().startswith('W'))
    l = sum(1 for r in rows if str(r.get('_outcome') or '').upper().startswith('L'))
    return w, l, (w / (w + l) * 100 if (w + l) else None)


def _suppression_section(sport: str, since: str, snap: list) -> None:
    gated = [x for x in snap
             if isinstance((x.get('input_snapshot') or {}).get('cell_gate'), dict)]
    print('\n--- 4. THE SUPPRESSED POPULATION (counterfactual, not our record) ---')
    if not gated:
        print('    no cell-gate demotions in window')
        return
    table = PROP_TABLE.get(sport.upper())
    if not table:
        print(f'    {len(gated)} demotions, but {sport} has no prop table to '
              f'resolve them against')
        return

    props = page(table, {
        'select': 'game_date,player_name,prop_type,direction,prop_line,result,'
                  'book_over_odds,book_under_odds',
        'game_date': f'gte.{since}', 'result': 'not.is.null'})
    if not props:
        # An empty read here would print "the gate suppressed nothing
        # measurable", which is indistinguishable from a clean result
        # (feedback_zero_rows_is_a_wiring_error).
        print(f'    ⚠ 0 graded rows read from {table} since {since}. That is a '
              f'WIRING problem, not a finding — {len(gated)} demotions cannot '
              f'be resolved. Fix the read before trusting this section.')
        return

    index = defaultdict(list)
    for p in props:
        index[_pkey(p['game_date'], p['player_name'], p['prop_type'],
                    p['direction'], p['prop_line'])].append(p)
    ambiguous = sum(1 for v in index.values() if len(v) > 1)
    if ambiguous:
        # Not silently resolvable. A duplicated key means two graded rows
        # claim the same prop on the same night, and picking either is a
        # coin flip on the number this section prints.
        print(f'    ⚠ {ambiguous} join keys match more than one graded row in '
              f'{table}. Those reads are EXCLUDED rather than resolved '
              f'arbitrarily — fix the duplicates before trusting the rest.')

    joined, unjoined, skipped = [], 0, 0
    for x in gated:
        rows = index.get(_pkey(x.get('game_date'), x['player_name'],
                               x['prop_type'], x['direction'],
                               x.get('prop_line')))
        if not rows:
            unjoined += 1
            continue
        if len(rows) > 1:
            skipped += 1
            continue
        p = rows[0]
        side_odds = (p.get('book_over_odds')
                     if str(x['direction']).lower() == 'over'
                     else p.get('book_under_odds'))
        joined.append({**x, '_outcome': p.get('result'), '_side_odds': side_odds})

    print(f'    {len(gated)} demotions · {len(joined)} resolved against '
          f'{table} · {unjoined} unresolved · {skipped} ambiguous')
    print(f'    breakeven {BREAKEVEN}%. "blind" = the same cell across EVERY '
          f'graded prop in window,')
    print(f'    which is the control a cell must beat before it is an '
          f'allowlist candidate.')

    w, l, pct = _wl(joined)
    roi, nroi = _roi(joined, lambda r: r.get('_side_odds'))
    roi_s = f'{roi:+.1f}%' if roi is not None else '—'
    print(f'\n    ALL DEMOTED           {fmt(w, l, pct)}   ROI {roi_s} '
          f'(n={nroi})')

    # By the tier the GENERATOR assigned, which is the literal form of the
    # question migration 20260925a was added for: the model said PRIME and
    # the gate said no -- who was right? Only answerable at all because the
    # snapshot is restored and the outcome is joined.
    print('\n    by the tier the generator assigned, before the gate:')
    by_tier = defaultdict(list)
    for x in joined:
        by_tier[str(((x.get('input_snapshot') or {}).get('cell_gate') or {})
                    .get('demoted_from') or '?').upper()].append(x)
    for tier in ('PRIME', 'STRONG', 'LEAN', 'BACK', 'FADE', '?'):
        sel = by_tier.get(tier)
        if not sel:
            continue
        tw, tl, tp = _wl(sel)
        troi, _tn = _roi(sel, lambda r: r.get('_side_odds'))
        ts = f'{troi:+.1f}%' if troi is not None else '—'
        print(f'      {tier:7s} {fmt(tw, tl, tp)}   ROI {ts}')

    # Blind, direction-only control per cell.
    blind = defaultdict(list)
    for p in props:
        side_odds = (p.get('book_over_odds')
                     if str(p['direction']).lower() == 'over'
                     else p.get('book_under_odds'))
        blind[str(p['prop_type']).lower()].append(
            {'_outcome': p.get('result'), '_side_odds': side_odds})

    cells = defaultdict(list)
    for x in joined:
        cells[str(x['prop_type']).lower()].append(x)

    print(f'\n    {"cell":15s} {"demoted":>21s} {"ROI":>7s} {"med":>6s}  '
          f'{"blind same cell":>21s} {"ROI":>7s}  {"margin vs 2SE":>16s}')
    flagged = []
    for cell, sel in sorted(cells.items(), key=lambda z: -len(z[1])):
        w, l, pct = _wl(sel)
        if (w + l) < 30:                     # CLAUDE.md: every % shows n, n>=30
            continue
        roi, _n = _roi(sel, lambda r: r.get('_side_odds'))
        med = _median_price(sel)
        bw, bl, bpct = _wl(blind.get(cell, []))
        broi, _bn = _roi(blind.get(cell, []), lambda r: r.get('_side_odds'))
        rs = f'{roi:+.1f}%' if roi is not None else '—'
        bs = f'{broi:+.1f}%' if broi is not None else '—'
        ms = f'{med:.0f}' if med is not None else '—'
        # THE TEST IS THE MARGIN, not whether the blind cell is profitable.
        # A blind cell at -3.4% whose selected subset runs +13% is the
        # selector WORKING; demanding a profitable blind control would
        # reject exactly the cells where selection adds the most. First
        # draft of this got that backwards and printed "no cell clears"
        # over a cell at +15.1pp.
        real = ''
        if pct is not None and bpct is not None:
            pa, pb = pct / 100.0, bpct / 100.0
            se = math.sqrt(pa * (1 - pa) / (w + l)
                           + pb * (1 - pb) / max(bw + bl, 1))
            marg, two = (pa - pb) * 100, 2 * se * 100
            real = f'{marg:+5.1f} vs {two:4.1f} {"REAL" if marg > two else "noise"}'
            if marg > two and roi is not None and roi > 0 and pct > BREAKEVEN:
                flagged.append((cell, pct, w + l, roi, marg, two, med))
        print(f'    {cell:15s} {fmt(w, l, pct):>21s} {rs:>7s} {ms:>6s}  '
              f'{fmt(bw, bl, bpct):>21s} {bs:>7s}  {real:>16s}')

    print(f'\n    cells shown have n>=30 decided. Smaller cells are omitted '
          f'rather than quoted.')
    if flagged:
        print('\n    ⚠ CANDIDATES — beat their own blind control by more than '
              '2SE, clear\n      breakeven, and return positive ROI at the '
              'real published price:')
        for cell, pct, n, roi, marg, two, med in flagged:
            print(f'       {cell}: demoted {pct:.1f}% n={n} ROI {roi:+.1f}% '
                  f'at median {med:.0f}, blind +{marg:.1f}pp (2SE {two:.1f})')
            if med is not None and med <= -200:
                print(f'          ⚠ median price {med:.0f} is outside the '
                      f'publishable juice band. CLAUDE.md refuses worse '
                      f'than -200\n            even at PRIME, so clearing '
                      f'breakeven here is not enough to publish.')
        print('\n       Split by LINE before acting — this view pools lines, '
              'and a cell can\n       look real while each of its line '
              'buckets sits inside its own noise.\n       Evidence to '
              'backtest, never a licence to publish\n       '
              '(feedback_unmeasured_signal_must_not_outweigh_measured).')
    else:
        print('\n    No cell beats its blind control by more than 2SE. '
              'The gate stays as is.')


def _median_price(rows: list):
    prices = []
    for r in rows:
        res = str(r.get('_outcome') or '').upper()
        if not (res.startswith('W') or res.startswith('L')):
            continue
        o = r.get('_side_odds')
        if o in (None, ''):
            continue
        try:
            prices.append(float(o))
        except (TypeError, ValueError):
            pass
    if not prices:
        return None
    prices.sort()
    return prices[len(prices) // 2]


if __name__ == '__main__':
    main()
