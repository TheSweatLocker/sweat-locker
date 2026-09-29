"""Final arbiter: make tier and conviction agree, or refuse to publish.

Andy 2026-09-26, after two days of finding contradictory numbers:
  "get to it and assess the situation for a root cause fix"

THE ROOT CAUSE, stated plainly. `tier` and `conviction` are two
independent mutable columns, written by roughly six passes, in an order
nobody controls, with no validation at the end. Each pass can silently
contradict the one before it. That is not a bug in any single script —
every one of them is individually defensible — it is the absence of a
final decision.

One real row from the 2026-09-26 board, Jose Quintana Ks Under 3.5, with
its whole audit trail preserved in `signals`:

    base scorer            _pre_recal_tier PRIME, _pre_recal_conviction 97
    prime_gate             "PRIME tier capped — multi-signal gate not met"
    book_recalibration     _edge_at_book -0.8, "NO EDGE (book priced our
                           signal in)", multiplier 0.3  ->  conviction 29
    lookback fallback      derived 80 from hit rate
    tier-floor fallback    raised to 70
    STORED                 tier PRIME, conviction 29

Six opinions, no arbiter. The prime_gate said the tier was capped and the
tier was never actually lowered. Recalibration correctly killed the
conviction for negative edge and the PRIME badge survived anyway. The
same shape produced PRIME props sitting at conviction 0 that could never
rank onto a card, and it is the same disease as the Blue Jays POTD
carrying 67 on the card, 77 in the engine and 87 in the receipt.

THE FIX IS STRUCTURAL, NOT ANOTHER GATE. Adding a seventh writer to the
pile is what got us here — Andy predicted exactly this: "if no, something
in legacy is going to fuck with whatever you put in I guarantee it." So
this does not add an opinion. It runs LAST and makes tier a FUNCTION of
conviction plus explicit vetoes, which means the two cannot disagree by
construction. Anything upstream is free to move conviction; only this
step names the tier.

    tier = band(conviction), then lowered by any veto that fired

VETOES only ever LOWER. A veto is evidence that a pick is worse than its
number suggests; none of them is evidence it is better. Today:
  * negative edge at the book  — we are taking the worse side of a price
  * prime_gate capped          — the multi-signal gate the scorer wanted
  * banned family              — policy, already enforced elsewhere
  * conviction 0 / missing     — no opinion exists, so no publishable tier

WHAT IT DOES NOT DO. It never invents a conviction. A row nobody scored
stays unscored and drops out of publishable tiers, which is the honest
outcome — a PRIME at conviction 0 was never a pick, it was a label.

    python reconcile_prop_decision.py --dry-run
    python reconcile_prop_decision.py --date 2026-09-26
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _l in (open(os.path.join(_HERE, '.env'), encoding='utf-8')
              if os.path.exists(os.path.join(_HERE, '.env')) else []):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

TABLE = {'MLB': 'mlb_pipeline_props', 'NFL': 'nfl_pipeline_props'}

# One band table. This is the ONLY place a prop tier is named.
# Floors match the sizing bands in generate_sharp_card so a tier and its
# stake cannot drift apart either.
BANDS = [('PRIME', 70), ('STRONG', 55), ('LEAN', 40)]
UNPUBLISHABLE = 'COVERAGE'

_RANK = {'SKIP': 0, 'PASS': 0, 'COVERAGE': 1, 'LEAN': 2, 'STRONG': 3, 'PRIME': 4}


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _page(path: str, params: dict) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H,
                         params=dict(params, limit='1000', offset=str(off)),
                         timeout=120)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{path} -> {r.status_code}: {(r.text or "")[:200]}')
        body = r.json()
        if not isinstance(body, list):
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def _signals(row) -> dict:
    sg = row.get('signals')
    if isinstance(sg, str):
        try:
            sg = json.loads(sg)
        except Exception:
            sg = {}
    return sg if isinstance(sg, dict) else {}


def _band(conviction) -> str:
    try:
        c = float(conviction)
    except (TypeError, ValueError):
        return UNPUBLISHABLE
    for name, floor in BANDS:
        if c >= floor:
            return name
    return UNPUBLISHABLE


def _vetoes(row: dict, sg: dict) -> list[str]:
    """Reasons this pick must sit BELOW what its conviction alone implies."""
    out = []
    c = row.get('conviction')
    if not c:
        out.append('no conviction — nothing scored this row')
    try:
        if float(sg.get('_edge_at_book')) < 0:
            out.append(f"negative edge at the book ({sg['_edge_at_book']})")
    except (TypeError, ValueError):
        pass
    if sg.get('prime_gate'):
        out.append('prime_gate: multi-signal gate not met')
    if sg.get('_lr_family_banned') and sg.get('_ban_gate'):
        out.append('banned prop family')
    return out


def decide(row: dict) -> tuple[str, list[str]]:
    """-> (tier, reasons). The single place a prop tier is named."""
    sg = _signals(row)
    tier = _band(row.get('conviction'))
    vetoes = _vetoes(row, sg)
    if not vetoes:
        return tier, []
    # A veto caps at one step below PRIME, and a missing conviction caps
    # outright — we cannot publish a tier for a pick with no opinion.
    if any(v.startswith('no conviction') for v in vetoes):
        return UNPUBLISHABLE, vetoes
    if _RANK[tier] >= _RANK['PRIME']:
        tier = 'STRONG'
    return tier, vetoes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='MLB', choices=['MLB', 'NFL'])
    ap.add_argument('--date', dest='game_date', default=None)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    gd = args.game_date or _today_et()
    tbl = TABLE[args.sport]

    rows = _page(tbl, {'game_date': f'eq.{gd}',
                       'select': 'id,player_name,prop_type,direction,tier,'
                                 'conviction,result,signals'})
    print(f'=== reconcile {args.sport} props · {gd} · {len(rows)} rows ===')

    changed = failed = agreed = skipped_graded = 0
    for row in rows:
        # A graded pick is a receipt. Never restate it.
        if row.get('result') not in (None, '', 'Pending', 'PENDING'):
            skipped_graded += 1
            continue
        stored = (row.get('tier') or '').upper()

        # 2026-09-26 — THIS STEP ONLY EVER LOWERS.
        #
        # The first dry run tried to "fix" 1,179 rows by raising SKIP to
        # COVERAGE, because a SKIP carries conviction 0 and 0 lands in the
        # bottom band. That was badly wrong: SKIP is a DECISION — written
        # deliberately by the coverage-kill gate, the directional-edge gate
        # and the ban policy — not an empty band waiting to be filled.
        # Undoing those would have resurrected every prop those gates
        # killed, which is the exact failure mode this file exists to stop.
        #
        # So the arbiter is a one-way ratchet. Promotion stays owned by the
        # scorers upstream; this step only refuses to let a tier sit HIGHER
        # than its own conviction and vetoes justify. That also keeps it
        # safe to drop into the existing chain in any position, which was
        # Andy's concern about legacy fighting anything new.
        if stored in ('SKIP', 'PASS'):
            agreed += 1
            continue
        tier, reasons = decide(row)
        if _RANK.get(tier, 0) >= _RANK.get(stored, 0):
            agreed += 1
            continue
        print(f'  LOWER {row["player_name"]:<22} {row["prop_type"]:<12} '
              f'{stored}/{row.get("conviction")} -> {tier}'
              + (f'   [{"; ".join(reasons)}]' if reasons else ''))
        if args.dry_run:
            changed += 1
            continue
        sg = _signals(row)
        sg['_reconciled'] = {
            'from_tier': stored, 'to_tier': tier,
            'conviction': row.get('conviction'), 'reasons': reasons,
            'at': datetime.now(timezone.utc).isoformat(),
        }
        r = requests.patch(f'{SB}/rest/v1/{tbl}?id=eq.{row["id"]}',
                           headers=H_WRITE,
                           json={'tier': tier, 'signals': sg}, timeout=30)
        if r.status_code in (200, 204):
            changed += 1
        else:
            failed += 1
            print(f'      ✖ {r.status_code}: {(r.text or "")[:140]}')

    print(f'\n  agreed {agreed} · changed {changed} · graded-skipped '
          f'{skipped_graded} · failed {failed}')

    if not args.dry_run:
        # Read-back. A 2xx is not proof — this pipeline has been bitten by
        # writes that returned 200 and were reverted by a trigger.
        back = _page(tbl, {'game_date': f'eq.{gd}',
                           'select': 'tier,conviction,result,signals'})
        bad = 0
        for row in back:
            if row.get('result') not in (None, '', 'Pending', 'PENDING'):
                continue
            stored = (row.get('tier') or '').upper()
            if stored in ('SKIP', 'PASS'):
                continue
            want, _ = decide(row)
            if _RANK.get(want, 0) < _RANK.get(stored, 0):
                bad += 1
        print(f'  read-back — rows still disagreeing with their conviction: {bad}')
        return 1 if bad else 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
