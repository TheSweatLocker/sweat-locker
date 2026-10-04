#!/usr/bin/env python3
"""Grade public_receipts rows that the normal grader cannot see.

WHY (2026-10-04). Andy: the Sweat Card recap never resolved Ohtani and Kim,
and both hit. They did — Ohtani hits UNDER 1.5 finished on 1, Kim hits UNDER
0.5 finished on 0. The underlying mlb_pipeline_props rows were graded at
11:40; the RECEIPTS were not.

Root cause: sweat_card prop receipts are written with player_name, prop_type,
pick_line and pick_side all NULL. Everything identifying the play lives in
`source_id` as a packed string:

    source_id = 'prop:Shohei Ohtani|hits_under|1.5'

The grader matches on the structured columns, so these rows are invisible to
it. prop_jerry receipts populate those columns and graded normally — same
table, same day, different writer.

This script resolves each stranded receipt by PARSING source_id back to its
prop row and grading from final_value, which is the authoritative number. It
is a repair tool, not the fix: the writer must populate the columns (see the
tackle list). Running it twice is safe — it only touches result IS NULL.

NEVER GUESSES. A receipt whose displayed line disagrees with its source row is
reported and skipped, because the grade depends on which line was actually
published and that is a receipts question, not a data question. Verified by
reading the row back — a PATCH that matches nothing returns 200 with an empty
body.

    python grade_stranded_receipts.py --date 2026-10-03
    python grade_stranded_receipts.py --date 2026-10-03 --apply
"""
from __future__ import annotations
import argparse, json, os, sys
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


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_source_id(sid: str):
    """'prop:Shohei Ohtani|hits_under|1.5' -> (player, prop_type, line)."""
    if not sid or not str(sid).startswith('prop:'):
        return None
    body = str(sid)[len('prop:'):]
    parts = body.split('|')
    if len(parts) != 3:
        return None
    return parts[0].strip(), parts[1].strip(), _f(parts[2])


def grade_from_final(prop_type: str, line, final):
    """-> 'Win' | 'Loss' | 'Push' | None, from the authoritative final value."""
    if line is None or final is None:
        return None
    pt = str(prop_type).lower()
    if pt.endswith('_under'):
        return 'Win' if final < line else ('Push' if final == line else 'Loss')
    if pt.endswith('_over'):
        return 'Win' if final > line else ('Push' if final == line else 'Loss')
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    r = requests.get(f'{SB}/rest/v1/public_receipts', headers=H, timeout=90,
                     params={'select': '*', 'game_date': f'eq.{args.date}',
                             'result': 'is.null'})
    r.raise_for_status()
    stranded = r.json()
    print(f'=== stranded receipts on {args.date}: {len(stranded)} ===\n')

    pr = requests.get(f'{SB}/rest/v1/mlb_pipeline_props', headers=H, timeout=90,
                      params={'select': 'player_name,prop_type,direction,prop_line,'
                                        'result,final_value',
                              'game_date': f'eq.{args.date}'})
    pr.raise_for_status()
    props = pr.json()
    # Index by (player, stat) so an UNDER receipt can be graded off the OVER
    # row's final_value — the final is the same number either way.
    by_player_stat = {}
    for p in props:
        stat = str(p.get('prop_type') or '').rsplit('_', 1)[0]
        key = (str(p.get('player_name') or '').lower(), stat)
        if p.get('final_value') is not None:
            by_player_stat.setdefault(key, p)

    fixed, skipped = [], []
    for rc in stranded:
        parsed = parse_source_id(rc.get('source_id'))
        if not parsed:
            skipped.append((rc, 'source_id is not a prop reference'))
            continue
        player, ptype, line = parsed
        stat = ptype.rsplit('_', 1)[0]
        src = by_player_stat.get((player.lower(), stat))
        if not src:
            skipped.append((rc, f'no graded prop row for {player} {stat}'))
            continue
        final = _f(src.get('final_value'))
        # The displayed label must agree with the line we are grading, or the
        # grade is a coin flip between two different bets.
        label = str(rc.get('pick_label') or '')
        import re
        m = re.search(r'(\d+(?:\.\d+)?)', label)
        shown = _f(m.group(1)) if m else None
        if shown is not None and line is not None and abs(shown - line) > 1e-9:
            skipped.append((rc, f'LABEL/SOURCE LINE MISMATCH: card shows {shown}, '
                                f'source_id says {line} — grade differs by line'))
            continue
        verdict = grade_from_final(ptype, line, final)
        if verdict is None:
            skipped.append((rc, 'cannot grade from final_value'))
            continue
        fixed.append((rc, player, ptype, line, final, verdict))

    print('%-12s %-36s %-8s %-7s %s' % ('surface', 'play', 'final', 'line', 'verdict'))
    print('-' * 80)
    for rc, player, ptype, line, final, verdict in fixed:
        print('%-12s %-36s %-8s %-7s %s'
              % (rc['surface'], f'{player} {ptype} {line}', final, line, verdict))
    if skipped:
        print('\n--- NOT graded, reported instead ---')
        for rc, why in skipped:
            print('  %-12s %-40s %s' % (rc['surface'], str(rc['pick_label'])[:40], why))

    if args.apply and fixed:
        print()
        ok = 0
        for rc, player, ptype, line, final, verdict in fixed:
            pr2 = requests.patch(
                f'{SB}/rest/v1/public_receipts', headers=H_W, timeout=60,
                params={'id': f'eq.{rc["id"]}'},
                data=json.dumps({'result': verdict,
                                 'actual_value': final,
                                 'player_name': player,
                                 'prop_type': ptype,
                                 'pick_line': line,
                                 'pick_side': ('UNDER' if ptype.endswith('_under')
                                               else 'OVER'),
                                 'graded_at': 'now()'}))
            body = pr2.json() if pr2.content else []
            if pr2.status_code not in (200, 204) or not body:
                print(f'  x write failed for {player}: {pr2.status_code} '
                      f'{pr2.text[:120]}')
                continue
            ok += 1
        print(f'graded {ok} of {len(fixed)} receipts (each verified by read-back)')
    elif fixed:
        print(f'\n{len(fixed)} gradable — re-run with --apply')
    return 0


if __name__ == '__main__':
    sys.exit(main())
