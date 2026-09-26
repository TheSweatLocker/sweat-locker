"""Write the playbook scorer's verdict onto the live prop rows.

Andy 2026-09-26: "lets redo the conviction portion."

THE HOLE THIS FILLS
Traced end-to-end on 2026-09-26:

    generate_props            ingests + scores        1,407
    sweep_prop_coverage       adds ~500 STUBS           502 rows, legacy_tier NULL
    family ban -> eligible                              137
    backfill_prop_lookback    L5/L10 written            126
    conviction > 0                                       20   <-- 106 scored ZERO
    published                                            30

prop_ensemble_scorer DOES score every one of those props — 1,005 today —
but it writes ONLY to prop_playbook_decisions. It never touches
mlb_pipeline_props. The workflow step is literally named "Prop playbook
scorer (shadow mode)". So the pipeline decided Connelly Early outs_under
was PRIME at conviction 97, on a prop that hit 10 of its last 10, and the
live row stayed conviction=0 / tier=SKIP.

sweep_prop_coverage creates the stubs and nothing ever scores them into
the table the app reads. That is the structural hole; which scorer is
better is a separate question.

WRITING CONVICTION IS NOT PUBLISHING. This script sets conviction and
tier so the board can be EVALUATED. Whether a prop then reaches users is
still decided downstream by the coverage-kill gate, the family ban, the
directional-edge gate and the publishable view. Conflating the two is how
we ended up with a board that is neither scored nor surfaced.

EVIDENCE FOR THE DEFAULT TIER FLOOR. Measured on prop_playbook_decisions
with result set, split by the L5 leak window because the all-time numbers
are contaminated (878 of legacy's 915 PRIME rows sit inside it):

    period                   legacy PRIME        playbook PRIME
    clean  <= 09-02          54.1% (n=37)        60.7% (n=178)
    LEAKED 09-03..09-22      73.6% (n=878) fake  68.3% (n=41)
    clean  >= 09-23          none produced       LEAN 55.6% (n=160)

On uncontaminated data playbook PRIME is the best-evidenced tier either
scorer has. playbook STRONG is NOT (52.5% pre-leak, 32% post-fix, both
under the 54.2% breakeven), so it is shadow-only by default.

    python promote_playbook_conviction.py --dry-run
    python promote_playbook_conviction.py
    python promote_playbook_conviction.py --tiers PRIME,STRONG   # widen
    python promote_playbook_conviction.py --all-tiers            # everything
"""
from __future__ import annotations
import argparse
import os
import sys
from collections import Counter

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

PROPS_TABLE = {'MLB': 'mlb_pipeline_props', 'NFL': 'nfl_pipeline_props'}

# PRIME only by default — see the evidence block in the docstring.
DEFAULT_TIERS = ('PRIME',)


def _today_et() -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def page(path: str, params: dict) -> list:
    out, off = [], 0
    while True:
        q = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, params=q, timeout=90)
        if r.status_code not in (200, 206):
            print(f'  ⚠ read {path} -> {r.status_code}: {(r.text or "")[:200]}')
            sys.exit(2)
        body = r.json()
        if not isinstance(body, list):
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def _key(r: dict) -> tuple:
    return (str(r.get('player_name') or '').strip(),
            str(r.get('prop_type') or '').strip(),
            str(r.get('direction') or '').strip().lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='MLB')
    ap.add_argument('--date', default=None)
    ap.add_argument('--tiers', default=None,
                    help='comma list, e.g. PRIME,STRONG (default: PRIME)')
    ap.add_argument('--all-tiers', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    sport = args.sport.upper()
    tbl = PROPS_TABLE.get(sport)
    if not tbl:
        print(f'no props table for {sport}')
        sys.exit(2)
    gd = args.date or _today_et()
    if args.all_tiers:
        tiers = None
    else:
        tiers = tuple(t.strip().upper() for t in
                      (args.tiers.split(',') if args.tiers else DEFAULT_TIERS) if t.strip())

    print(f'=== promote_playbook_conviction · {sport} · {gd}'
          f'{" [DRY]" if args.dry_run else ""} ===')
    print(f'  promoting tiers: {",".join(tiers) if tiers else "ALL"}')

    decisions = page('prop_playbook_decisions', {
        'game_date': f'eq.{gd}', 'sport': f'eq.{sport}',
        'select': 'player_name,prop_type,direction,playbook_tier,'
                  'playbook_conviction,playbook_side,legacy_tier'})
    props = page(tbl, {
        'game_date': f'eq.{gd}',
        'select': 'id,player_name,prop_type,direction,tier,conviction'})
    print(f'  playbook decisions: {len(decisions)}   live props: {len(props)}')
    if not decisions or not props:
        print('  nothing to do.')
        return

    by_key = {}
    for p in props:
        by_key.setdefault(_key(p), p)

    # PASS is a verdict, not a tier. mlb_pipeline_props only understands
    # PRIME / STRONG / LEAN / COVERAGE / SKIP, so writing 'PASS' into the
    # tier column would invent a sixth value that every downstream gate,
    # view and grader would have to learn about. Caught in dry run.
    REAL_TIERS = {'PRIME', 'STRONG', 'LEAN'}

    todo, skipped = [], Counter()
    for d in decisions:
        t = (d.get('playbook_tier') or '').upper()
        conv = d.get('playbook_conviction')
        if conv is None:
            skipped['no playbook conviction'] += 1
            continue
        # playbook_side is a VERDICT (BACK / FADE / PASS), not a direction.
        # 2026-09-26: the first version of this script compared it against
        # over/under and skipped all 1,005 rows — the dry run returning
        # exactly zero is what exposed it.
        #
        #   BACK — back this prop's own direction, conviction transfers
        #   FADE — the playbook wants the OPPOSITE side, so writing its
        #          conviction onto THIS row would attach a number that
        #          argues against the pick it sits on. That is precisely
        #          the directional-edge defect from 09-25; it needs its own
        #          row facing the other way, not a transplant onto this one.
        #   PASS — no play. Leave the row alone.
        verdict = str(d.get('playbook_side') or '').strip().upper()
        if verdict == 'FADE':
            skipped['playbook FADEs this side (needs its own row)'] += 1
            continue
        if verdict != 'BACK':
            skipped[f'playbook verdict {verdict or "none"}'] += 1
            continue
        live = by_key.get(_key(d))
        if not live:
            skipped['no matching live prop'] += 1
            continue
        # Never clobber a real legacy score. This exists to fill the HOLE
        # left by unscored coverage stubs, not to overrule a scorer that
        # already had an opinion.
        if (live.get('conviction') or 0) > 0:
            skipped['live prop already scored'] += 1
            continue
        # WRITING CONVICTION IS NOT PUBLISHING — the whole point of this
        # script. Conviction is always written so the board can be
        # evaluated; the TIER only moves when the playbook tier is real AND
        # cleared by the tier filter (default PRIME, on the evidence in the
        # docstring). Everything else keeps the tier it has, so a LEAN-rated
        # stub becomes a scored SKIP rather than a surfaced play.
        promote_tier = t if (t in REAL_TIERS and (tiers is None or t in tiers)) else None
        if promote_tier is None:
            skipped[f'conviction only (tier {t or "none"} not promoted)'] += 1
        todo.append((live, promote_tier, int(conv)))

    print(f'  to promote: {len(todo)}')
    for k, v in skipped.most_common():
        print(f'    skipped · {k}: {v}')
    if not todo:
        return
    print()
    for live, t, conv in todo[:12]:
        shown = t if t else f'{live.get("tier")} (tier held)'
        print(f'    {live["player_name"]:<22} {live["prop_type"]:<14} '
              f'{str(live.get("direction")):<6} {live.get("tier")} /'
              f'{live.get("conviction")} -> {shown} / {conv}')
    if len(todo) > 12:
        print(f'    … and {len(todo) - 12} more')

    if args.dry_run:
        print('\n  DRY RUN — no writes.')
        return

    ok = fail = 0
    for live, t, conv in todo:
        body = {'conviction': conv}
        if t:
            body['tier'] = t
        r = requests.patch(f'{SB}/rest/v1/{tbl}', headers=H_W, timeout=30,
                           params={'id': f'eq.{live["id"]}'}, json=body)
        if r.status_code not in (200, 204):
            fail += 1
            if fail <= 3:
                print(f'  ⚠ id={live["id"]} -> {r.status_code} {(r.text or "")[:160]}')
            continue
        ok += 1

    # Read back. mlb_pipeline_props carries the publish_lock trigger, which
    # preserves the old tier and STILL returns 204 — that is how 985
    # discipline demotions were lost for days. A status code is not proof.
    ids = [str(l['id']) for l, _, _ in todo]
    landed = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        rows = page(tbl, {'id': f'in.({",".join(chunk)})',
                          'select': 'id,conviction'})
        landed += sum(1 for r in rows if (r.get('conviction') or 0) > 0)
    print(f'\n  patched {ok}/{len(todo)}' + (f', {fail} failed' if fail else ''))
    print(f'  verified by read-back: {landed}/{len(todo)} now carry conviction > 0')
    if landed < ok:
        print('  🚨 fewer rows changed than were accepted — publish_lock is '
              'reverting these writes. Move the lock or unlock before rerunning.')


if __name__ == '__main__':
    main()
