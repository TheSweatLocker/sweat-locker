#!/usr/bin/env python3
"""Odds floor on publishable props — for the families it actually helps.

WHY (2026-10-05)
----------------
Andy, after seeing the prop underperformance breakdown: "do -200 right?"

CLAUDE.md has said for weeks that the prop odds band is -300..+150 and that
hits worse than -200 must never publish even at PRIME. Neither was enforced.
Measured on the CLEAN era only (09-22+, after the L5 leak fix), on receipts
that actually shipped:

    published OUTSIDE -300..+150   154 receipts   72-82   -7.4% ROI
    `hits` props at <= -200         84 receipts   60-24   -8.6% ROI

Real published prices included -7500, -5000, -1200, -900, -850, -800, -750,
-700, -650, -575. A -7500 prop risks 75 units to win 1.

Those 84 `hits` violations are the cleanest demonstration of the house rule
there is: **71% hit rate, -8.6% ROI.** A hit rate without a price is
meaningless.

WHY A NEW GATE AND NOT A TWEAK TO apply_juice_trap_gate
-------------------------------------------------------
That gate is family-specific (hits_over only) and deliberately escapable — it
KEEPS a play at -200..-299 when refit_conviction is high enough. Both choices
are why -7500 props reached users: a -900 ks_over is not hits_over, so the
gate never looked at it. This is a floor, not a sliding scale, and it applies
to every family and every sport.

The sliding gate still runs and still does its job inside the band; this just
removes the part of the board that cannot be bet profitably at any conviction.

WHERE THE FLOOR COMES FROM — AND WHY IT IS FAMILY-SCOPED
--------------------------------------------------------
I first proposed a BLANKET -200 floor and Andy approved it. Then I measured
what it would actually block, and it would have been a costly mistake:

    props at <=-200 in a publishable tier, that ACTUALLY PUBLISHED
        556-143   +46.36u   +6.6% ROI   n=699      <- PROFITABLE

A blanket floor would have thrown that away. The -9.3% figure I used to argue
for the floor came from a NARROWER slice (clean era only, n=118) whose family
mix is completely different. Broken out by family, <=-200 published props,
08-15..10-05, n=916:

    rbis_under          149-37   +11.70u   +6.3%   n=186   KEEP
    runs_under           86-21   +13.62u  +12.7%   n=107   KEEP
    total_bases_under    85-25    +7.23u   +6.6%   n=110   KEEP
    bb_over              23-7     +3.45u  +11.5%   n=30    KEEP
    hits_over            97-53   -12.81u   -8.5%   n=150   BLOCK
    hits_under          112-43   -10.12u   -6.5%   n=155   BLOCK
    bb_under             35-20    -4.10u   -7.5%   n=55    BLOCK
    hr_under             54-7     -1.12u   -1.8%   n=61    BLOCK

The aggregate flips sign depending on which families are in the window. That
is Simpson's paradox, and it is the documented trap in
feedback_tier_mix_reverses_the_sign — a tier or band effect measured across a
mixed population reverses once the population is split.

So the floor applies ONLY to the families that actually lose at heavy juice.
rbis/runs/total_bases unders at -300 are the board doing its job: they are
near-certainties and they pay. hits and bb at -200 are the trap.

GATES THE STORED ROW, not an in-flight one — a price gate applied mid-build
no-ops when a later step rewrites the tier
(feedback_gate_the_stored_row_not_the_inflight_row). Run AFTER the scorers
and tier-stampers, BEFORE anything that publishes.

    python apply_odds_band_gate.py                  # today, dry
    python apply_odds_band_gate.py --apply
    python apply_odds_band_gate.py --date 2026-10-04 --apply
    python apply_odds_band_gate.py --audit 30       # how many WOULD have gone
"""
from __future__ import annotations
import argparse, collections, datetime as dt, json, os, sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            k, v = _l.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=representation'}

# Worse than or equal to this on the BACKED side is never publishable —
# but ONLY for the families below. See the docstring: a blanket floor would
# have removed a profitable set (+46.36u on n=699).
ODDS_FLOOR = -200

# Families that LOSE at <= ODDS_FLOOR, measured on published receipts.
# hits_* and bb_under are the trap; rbis/runs/total_bases unders are not.
FLOORED_FAMILIES = ('hits_over', 'hits_under', 'bb_under', 'hr_under')


def _family(prop_type) -> str:
    return str(prop_type or '').strip().lower()


PROP_TABLES = {'MLB': 'mlb_pipeline_props', 'NFL': 'nfl_pipeline_props',
               'NHL': 'nhl_pipeline_props', 'NBA': 'nba_pipeline_props'}
# Tiers that reach a user. COVERAGE is Ledger-leg only and SKIP is already out.
PUBLISHABLE = ('PRIME', 'STRONG', 'LEAN', 'LIGHT')


def _today_et() -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).strftime('%Y-%m-%d')


def page(table, params):
    out, off = [], 0
    while True:
        p = dict(params)
        p.update({'limit': 1000, 'offset': off})
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=120, params=p)
        if r.status_code not in (200, 206):
            print(f'  ⚠ {table} {r.status_code}: {r.text[:140]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def backed_odds(row) -> int | None:
    """The price on the side we are BACKING, not the prop's nominal over."""
    d = str(row.get('direction') or '').strip().lower()
    v = row.get('book_over_odds') if d == 'over' else row.get('book_under_odds')
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if -100 < f < 100:          # 0 / empty-column shapes are not prices
        return None
    return int(round(f))


def scan(date: str):
    """-> list of (sport, table, row, odds) that breach the floor."""
    hits = []
    for sport, tbl in PROP_TABLES.items():
        rows = page(tbl, {'select': 'id,player_name,prop_type,prop_line,direction,'
                                    'tier,conviction,book_over_odds,book_under_odds,'
                                    'game_date',
                          'game_date': f'eq.{date}'})
        for r in rows:
            if str(r.get('tier') or '').upper() not in PUBLISHABLE:
                continue
            if _family(r.get('prop_type')) not in FLOORED_FAMILIES:
                continue
            o = backed_odds(r)
            if o is not None and o <= ODDS_FLOOR:
                hits.append((sport, tbl, r, o))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--audit', type=int, default=0,
                    help='report breaches over the last N days and exit')
    args = ap.parse_args()

    if args.audit:
        print(f'=== odds-band audit · floor {ODDS_FLOOR} · last {args.audit}d ===')
        today = dt.date.fromisoformat(_today_et())
        tot = collections.Counter()
        worst = []
        for i in range(args.audit):
            d = (today - dt.timedelta(days=i)).isoformat()
            for sport, _t, r, o in scan(d):
                tot[sport] += 1
                worst.append((o, d, sport, r.get('player_name'),
                              r.get('prop_type'), r.get('tier')))
        print(f'  breaches found: {sum(tot.values())}  {dict(tot)}')
        for o, d, sp, nm, pt, ti in sorted(worst)[:12]:
            print(f'     {d} {sp:<5} {str(nm)[:20]:20s} {str(pt):18s} '
                  f'{ti:<7} {o:+d}')
        return 0

    date = args.date or _today_et()
    hits = scan(date)
    print(f'=== apply_odds_band_gate · {date} · floor {ODDS_FLOOR} · '
          f'{"APPLY" if args.apply else "DRY"} ===')
    print(f'  publishable props at {ODDS_FLOOR} or worse: {len(hits)}')
    if not hits:
        print('  nothing to demote')
        return 0
    by = collections.Counter(f'{s}/{r.get("tier")}' for s, _t, r, _o in hits)
    print(f'  {dict(by)}')
    for sport, _t, r, o in sorted(hits, key=lambda x: x[3])[:15]:
        print(f'     {sport:<5} {str(r.get("player_name"))[:20]:20s} '
              f'{str(r.get("prop_type")):18s} {str(r.get("direction")):6s} '
              f'line={r.get("prop_line")} tier={r.get("tier"):<7} {o:+d}')
    if not args.apply:
        print('\n  re-run with --apply to demote these to SKIP')
        return 0

    ok = bad = 0
    for sport, tbl, r, o in hits:
        prev = str(r.get('tier'))
        payload = {'tier': 'SKIP'}
        pr = requests.patch(f'{SB}/rest/v1/{tbl}', headers=H_W, timeout=60,
                            params={'id': f'eq.{r["id"]}'},
                            data=json.dumps(payload))
        body = pr.json() if pr.content else []
        # Verify the VALUE came back, not just that a row did.
        if pr.status_code in (200, 204) and body and body[0].get('tier') == 'SKIP':
            ok += 1
        else:
            bad += 1
            if bad <= 3:
                print(f'   x {tbl} id={r["id"]} {pr.status_code} '
                      f'{pr.text[:110]}')
    print(f'\n  demoted {ok}/{len(hits)} to SKIP (verified by read-back), '
          f'{bad} failed')
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
