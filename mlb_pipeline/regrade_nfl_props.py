"""Re-derive NFL prop grades that were decided against a bad stat read.

The normal resolver (resolve_nfl_props_espn.py) only picks up rows where
`result IS NULL`. Rows already carrying a WRONG verdict, or parked at
UNGRADEABLE, are invisible to it forever. This re-derives those.

WHY THESE ROWS ARE WRONG (fixed at source in commit 2f51d31a):
PROP_TO_ESPN mapped pass_attempts->'ATT' and pass_completions->'CMP',
but ESPN's passing labels are ['C/ATT','YDS','AVG','TD','INT',...].
Neither exists, so the extractor returned None and the caller stored
final_value = 0.0, then graded against that zero. Verified wrong:
  Caleb Williams pass_completions O20.5 -> Loss, actually 21/29 = WIN
  Aaron Rodgers  pass_completions O20.5 -> Loss, actually 24/40 = WIN
  C.J. Stroud    pass_completions U19.5 -> Win,  actually 26/38 = LOSS

TARGETS (nothing else is touched):
  A. final_value 0.0 on a stat that cannot be zero if the player
     appeared (outs / pass_attempts / pass_completions)
  B. result = 'UNGRADEABLE'

STRICT NAME MATCHING — this does NOT use the production matcher, which
falls back to `last_name in name`. On 2026-09-13 that collided "Malik
Washington" with "Parker Washington" and produced identical stats for
both. A regrade that writes records cannot use fuzzy matching.

Nothing is written without --apply, and a row is only rewritten when the
newly-derived value is a real number.

    python regrade_nfl_props.py --dry-run
    python regrade_nfl_props.py --apply
"""
from __future__ import annotations
import argparse, os, sys
from collections import Counter
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).parent))
_env = Path(__file__).parent / '.env'
for line in _env.read_text().split('\n'):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

import resolve_nfl_props_espn as R

ZERO_IMPLAUSIBLE = ('outs', 'pass_attempts', 'pass_completions')
_BOX: dict[str, list] = {}


def boxes(date_str: str) -> list:
    if date_str not in _BOX:
        _BOX[date_str] = [b for b in
                          (R._espn_boxscore(e['id']) for e in R._espn_events_on_date(date_str))
                          if b]
    return _BOX[date_str]


def strict_stat(bx, player_name: str, prop_type: str):
    """Exact name, or first-initial + last name. No last-name-only fallback."""
    if not bx:
        return None
    base = prop_type.replace('_over', '').replace('_under', '')
    cfg = R.PROP_TO_ESPN.get(base)
    if not cfg:
        return None
    category, label, side = cfg
    want = player_name.lower().strip()
    parts = want.split()
    want_alt = f'{parts[0][0]}.{parts[-1]}' if len(parts) >= 2 else None
    for team in bx.get('boxscore', {}).get('players', []):
        for grp in team.get('statistics', []):
            if grp.get('name') != category:
                continue
            labels = grp.get('labels', [])
            for ath in grp.get('athletes', []):
                a = ath.get('athlete', {})
                nm = (a.get('displayName') or '').lower().strip()
                short = (a.get('shortName') or '').lower().strip().replace(' ', '')
                if nm != want and (not want_alt or short != want_alt):
                    continue
                v = R._parse_athlete_stat(ath.get('stats', []), label, labels, side)
                if v is not None:
                    return v
    return None


def actual_for(date_str: str, player: str, prop_type: str):
    """Stored date first. Widen +/-1 day ONLY when that date has no games
    at all (the date-drift case) — never on a date that has games, since
    that turns a genuine miss into a wrong match."""
    from datetime import date as _d, timedelta as _td
    for bx in boxes(date_str):
        v = strict_stat(bx, player, prop_type)
        if v is not None:
            return v, date_str
    if boxes(date_str):
        return None, None
    base = _d.fromisoformat(date_str)
    for delta in (-1, 1):
        ds = (base + _td(days=delta)).isoformat()
        for bx in boxes(ds):
            v = strict_stat(bx, player, prop_type)
            if v is not None:
                return v, ds
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    ap.add_argument('--since', default='2026-09-01')
    args = ap.parse_args()
    dry = args.dry_run

    rows, off = [], 0
    while off < 9000:
        r = requests.get(f'{SB}/rest/v1/nfl_pipeline_props',
                         params={'game_date': f'gte.{args.since}', 'limit': '1000',
                                 'offset': str(off),
                                 'select': 'id,game_date,player_name,prop_type,prop_line,'
                                           'direction,tier,result,final_value'},
                         headers=H, timeout=30)
        if r.status_code != 200:
            print(f'fetch failed {r.status_code}: {r.text[:200]}'); return 1
        b = r.json()
        if not b: break
        rows.extend(b); off += 1000
        if len(b) < 1000: break

    targets = [x for x in rows
               if (x.get('result') == 'UNGRADEABLE')
               or (x.get('final_value') in (0, 0.0)
                   and any(s in (x.get('prop_type') or '') for s in ZERO_IMPLAUSIBLE))]
    print(f'NFL props since {args.since}: {len(rows)}   targets: {len(targets)}'
          f'  {"(DRY RUN)" if dry else "(APPLY)"}\n')

    fixed = unchanged = skipped = 0
    changes = []
    for x in targets:
        act, used = actual_for(x['game_date'], x['player_name'], x['prop_type'])
        if act is None:
            skipped += 1; continue
        new = R._grade_prop(float(x['prop_line']), x['direction'], act)
        if new == x['result'] and x.get('final_value') == act:
            unchanged += 1; continue
        changes.append({**x, 'actual': act, 'new': new, 'date_used': used})
        drift = '' if used == x['game_date'] else f'  [found on {used}]'
        print(f"  {x['game_date']} {x['player_name']:22} {x['prop_type']:24} "
              f"{x['direction']:5} {str(x['prop_line']):>6}  "
              f"{str(x['result']):<12} val={str(x['final_value']):>6}  ->  "
              f"{new:<5} val={act}  [{x['tier']}]{drift}")
        if not dry:
            pr = requests.patch(f'{SB}/rest/v1/nfl_pipeline_props?id=eq.{x["id"]}',
                                headers=HW,
                                json={'result': new, 'final_value': act}, timeout=20)
            if pr.status_code not in (200, 204):
                print(f'      ! patch failed {pr.status_code}: {pr.text[:120]}')
                continue
        fixed += 1

    verdict_flips = [c for c in changes if c['result'] in ('Win', 'Loss', 'Push')
                     and c['new'] != c['result']]
    newly = [c for c in changes if c['result'] == 'UNGRADEABLE']
    print(f'\n=== {"WOULD APPLY" if dry else "APPLIED"} ===')
    print(f'  rows rewritten      : {fixed}')
    print(f'  verdict CHANGED     : {len(verdict_flips)}'
          f'  {dict(Counter(f"{c['result']}->{c['new']}" for c in verdict_flips))}')
    print(f'  UNGRADEABLE resolved: {len(newly)}'
          f'  {dict(Counter(c["new"] for c in newly))}')
    print(f'  already correct     : {unchanged}')
    print(f'  no ESPN stat (DNP)  : {skipped}')
    if dry:
        print('\n--apply to write.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
