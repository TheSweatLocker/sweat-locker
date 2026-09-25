"""UFC picks grader (2026-08-09).

Backfills winner_actual / method_actual / rounds_actual / pick_result
onto ufc_picks after events end. Uses ESPN core API for results.

Result normalization:
  ESPN winner_id → cross-ref to ufc_picks.fighter_a_url / fighter_b_url
                    or match by name → set winner_actual = 'a' | 'b'
  ESPN result.description → 'KO', 'TKO', 'SUB', 'DEC', 'DQ'
  ESPN result.endTime → rounds_actual
  distance_actual = (method == 'DEC') or (endTime = last-scheduled-round-end)

pick_result:
  If ev_recommended_side == winner_actual → 'W'
  If winner_actual == 'draw' or 'nc' → 'PUSH'
  Otherwise → 'L'

CLI:
    python ufc_grader.py --event-date 2026-08-08
    python ufc_grader.py --backfill-all           # every unresolved event
    python ufc_grader.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys, re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


# ESPN MMA scoreboard endpoint — returns finished events with results
ESPN_SCOREBOARD = 'https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard'


def _norm(name: str) -> str:
    import unicodedata
    if not name: return ''
    n = unicodedata.normalize('NFKD', name).encode('ascii','ignore').decode('ascii')
    return re.sub(r'\s+', ' ', n).strip().lower()


def _alpha(s: str) -> str:
    return re.sub(r'[^a-z]', '', _norm(s))


def fetch_espn_event_results(event_date: str) -> list:
    """Return ESPN's scoreboard events for a date. Each has competitions[]
    with per-fight results."""
    # ESPN dates as YYYYMMDD
    ymd = event_date.replace('-','')
    r = requests.get(ESPN_SCOREBOARD, params={'dates': ymd}, timeout=15)
    if r.status_code != 200:
        print(f'  ⚠ ESPN scoreboard {r.status_code}: {r.text[:150]}')
        return []
    return r.json().get('events', [])


# Detail strings the method parser could not classify. Surfaced at the end of
# a run so a parser that silently stops matching becomes visible immediately
# rather than after 108 fights of fabricated "decisions".
_UNPARSED_METHODS: list = []


def parse_fight_result(comp: dict) -> Optional[dict]:
    """From an ESPN competition (fight), extract winner + method + rounds.
    Returns dict or None if not yet complete."""
    if comp.get('status', {}).get('type', {}).get('state') != 'post':
        return None
    competitors = comp.get('competitors', [])
    if len(competitors) < 2: return None

    winner_name = None
    for c in competitors:
        if c.get('winner'):
            ath = c.get('athlete') or {}
            winner_name = ath.get('displayName') or ath.get('fullName')
            break

    # Method + round from status/detail
    status = comp.get('status', {})
    detail = (status.get('type', {}).get('detail') or '').lower()

    # 2026-09-25 ROOT FIX. The docstring claims ESPN details look like
    # "Final - KO/TKO Round 3 3:24". They do not, and have not: every fight
    # returns detail == 'Final', full stop. So the tag loop below never matched
    # and the old `default to DEC` line fired on ALL 108 graded fights —
    # method_actual='DEC' and distance_actual=True on every single one, which
    # is why the distance model has never been scored against a real outcome.
    #
    # The method actually lives in comp['details'][].type.text:
    #     "Unofficial Winner Kotko"       -> KO/TKO  (ESPN's own typo)
    #     "Unofficial Winner Submission"  -> SUB
    #     "Unofficial Winner Decision"    -> DEC
    # Verified on the 2026-09-19 card: 5 decisions, 7 finishes — the realistic
    # split, against the 12/12 "decisions" the old parser would have recorded.
    method = None
    _texts = ' '.join(
        str(((d or {}).get('type') or {}).get('text') or '')
        for d in (comp.get('details') or [])).lower()
    for tag, m in (('kotko', 'KO'), ('ko/tko', 'KO'), ('submission', 'SUB'),
                   ('decision', 'DEC'), ('disqualif', 'DQ'),
                   ('no contest', 'NC'), ('draw', 'DRAW')):
        if tag in _texts:
            method = m
            break

    # Fallback to the status detail string, in case ESPN ever populates it
    # the way the original docstring assumed.
    if not method:
        for tag, m in (('ko/tko','KO'), ('sub','SUB'), ('submission','SUB'),
                        ('tko','TKO'), ('ko ','KO'),
                        ('decision','DEC'), ('unan','DEC'), ('split','DEC'),
                        ('majority','DEC'), ('dq','DQ'), ('disqualif','DQ'),
                        ('no contest','NC'), ('draw','DRAW')):
            if tag in detail:
                method = m
                break
    # 2026-09-25: was `if not method and 'final' in detail: method = 'DEC'`
    # — a silent default that ASSERTED a decision whenever the detail string
    # failed to match any method tag. It matched every single fight: all 108
    # graded rows carry method_actual='DEC' and distance_actual=True, which is
    # impossible (roughly half of UFC fights end inside the distance).
    #
    # So the method and distance columns are not measurements, they are the
    # default. p_distance and the method probabilities have never once been
    # scored against a real outcome, and any "goes the distance" record
    # computed from this is fabricated.
    #
    # Never assert an outcome we did not observe: leave it NULL and count it,
    # so a parser that stops matching shows up as missing data instead of as
    # 100% decisions. Winner grading is unaffected — it comes from the
    # competitor payload, not this string, which is why winner_actual looks
    # sane (52 'a' / 56 'b') while method does not.
    if not method:
        _UNPARSED_METHODS.append(detail[:120])

    # Round the fight ended in. status.period carries it directly (verified:
    # period=1 with displayClock 1:57 on a round-1 finish, period=3 clock 5:00
    # on a decision), so the regex on the detail string is only a fallback.
    rounds = status.get('period')
    if rounds is None:
        m = re.search(r'round\s*(\d+)', detail)
        if m: rounds = int(m.group(1))
    if rounds is None and method == 'DEC':
        # Full-distance: scheduled rounds when ESPN gives them, else 3.
        rounds = ((comp.get('format') or {}).get('regulation') or {}).get('periods') or 3

    # None, not False, when the method is unknown — False would claim we saw
    # the fight end early.
    distance = (method == 'DEC') if method else None

    return {'winner_name': winner_name, 'method': method,
            'rounds': rounds, 'distance': distance,
            'detail': detail}


def grade_event(event_date: str, dry_run: bool = False,
                regrade: bool = False) -> int:
    """Backfill grades for one event_date.

    regrade=True re-processes rows that already carry graded_at. Added
    2026-09-25 to repair the 108 fights whose method_actual/distance_actual
    were written by the old `default to DEC` line rather than observed —
    every one of them recorded as a decision that went the distance.
    """
    picks = requests.get(f'{SB}/rest/v1/ufc_picks', headers=H_R,
        params={'event_date': f'eq.{event_date}', 'select': '*',
                'order': 'fight_order.asc'}, timeout=15).json()
    if not isinstance(picks, list) or not picks:
        print(f'  no ufc_picks for {event_date}')
        return 0
    ungraded = picks if regrade else [p for p in picks if not p.get('graded_at')]
    if not ungraded:
        print(f'  all {len(picks)} picks already graded for {event_date}'
              f'{" (use --regrade to redo)" if not regrade else ""}')
        return 0
    print(f'  {len(ungraded)} ungraded picks on {event_date}')

    events = fetch_espn_event_results(event_date)
    print(f'  ESPN returned {len(events)} events for {event_date}')

    # Flatten all competitions across all events
    comps = []
    for ev in events:
        for comp in ev.get('competitions', []):
            comps.append(comp)
    print(f'  {len(comps)} total fights on ESPN scoreboard')

    updated = 0
    now = datetime.now(timezone.utc).isoformat()
    for pk in ungraded:
        fa, fb = pk['fighter_a'], pk['fighter_b']
        fa_n, fb_n = _norm(fa), _norm(fb)
        fa_a, fb_a = _alpha(fa), _alpha(fb)
        # Find matching ESPN competition
        match_comp = None
        for c in comps:
            names = [_norm(cc.get('athlete',{}).get('displayName') or cc.get('athlete',{}).get('fullName') or '')
                     for cc in c.get('competitors', [])]
            names_alpha = [_alpha(n) for n in names]
            if {fa_n, fb_n} & set(names) or {fa_a, fb_a} & set(names_alpha):
                # Verify BOTH fighters match
                if (fa_n in names or fa_a in names_alpha) and (fb_n in names or fb_a in names_alpha):
                    match_comp = c
                    break
        if not match_comp:
            print(f'  ✗ no ESPN match: {fa} vs {fb}')
            continue
        result = parse_fight_result(match_comp)
        if not result:
            continue   # not final yet
        # Which side won?
        wn = _norm(result['winner_name'] or '')
        wa = _alpha(result['winner_name'] or '')
        winner_side = None
        if wn == fa_n or wa == fa_a: winner_side = 'a'
        elif wn == fb_n or wa == fb_a: winner_side = 'b'
        elif not wn:  # draw / no contest
            if 'no contest' in result['detail']: winner_side = 'nc'
            elif 'draw' in result['detail']: winner_side = 'draw'
        if not winner_side:
            print(f'  ⚠ winner match failed: winner={result["winner_name"]!r} vs {fa}/{fb}')
            continue

        # pick_result relative to ev_recommended_side
        rec = pk.get('ev_recommended_side')
        pick_result = None
        if winner_side in ('draw','nc'): pick_result = 'PUSH'
        elif rec == winner_side: pick_result = 'W'
        elif rec in ('a','b'): pick_result = 'L'

        patch = {
            'winner_actual':   winner_side,
            'method_actual':   result['method'],
            'rounds_actual':   result['rounds'],
            'distance_actual': result['distance'],
            'pick_result':     pick_result,
            'graded_at':       now,
        }
        rec_name = pk['fighter_a'] if rec=='a' else (pk['fighter_b'] if rec=='b' else '-')
        wname = pk['fighter_a'] if winner_side=='a' else (pk['fighter_b'] if winner_side=='b' else winner_side)
        print(f'  ✓ {fa} vs {fb}: {wname} won by {result["method"]} R{result["rounds"]} · pick {rec_name} = {pick_result}')
        if dry_run: continue
        pr = requests.patch(f'{SB}/rest/v1/ufc_picks?id=eq.{pk["id"]}',
                            headers=H_W, json=patch, timeout=10)
        if pr.status_code in (200, 204): updated += 1
        else: print(f'    ⚠ patch {pr.status_code}: {pr.text[:150]}')
    print(f'  updated {updated}/{len(ungraded)} picks for {event_date}')
    return updated


def backfill_all(dry_run: bool = False, regrade: bool = False) -> int:
    """Grade every event_date that has ungraded picks."""
    r = requests.get(f'{SB}/rest/v1/ufc_picks', headers=H_R,
        params={'graded_at': 'is.null', 'select': 'event_date'}, timeout=15)
    rows = r.json() if isinstance(r.json(), list) else []
    dates = sorted({row['event_date'] for row in rows if row.get('event_date')})
    print(f'  {len(dates)} event dates with ungraded picks')
    total = 0
    for d in dates:
        total += grade_event(d, dry_run=dry_run, regrade=regrade)
    print(f'\n=== TOTAL: graded {total} picks across {len(dates)} events ===')
    return total


def summary():
    """Print current UFC grader stats."""
    r = requests.get(f'{SB}/rest/v1/ufc_picks', headers=H_R,
        params={'graded_at': 'not.is.null', 'ev_recommended_side': 'in.(a,b)',
                'select': 'event_date,ev_tier,pick_result',
                'order': 'event_date.desc', 'limit': '2000'}, timeout=15).json()
    if not isinstance(r, list): return
    by_tier = {}
    for row in r:
        t = row.get('ev_tier') or '?'
        by_tier.setdefault(t, []).append(row.get('pick_result'))
    print('\n=== UFC PICK TRACK RECORD (graded picks only) ===')
    for tier in ('PRIME','STRONG','LEAN','SKIP'):
        lst = by_tier.get(tier, [])
        if not lst: continue
        w = sum(1 for x in lst if x=='W')
        l = sum(1 for x in lst if x=='L')
        p = sum(1 for x in lst if x=='PUSH')
        tot = w + l
        print(f'  {tier:6s} W {w} · L {l} · P {p}  → {w}/{tot} = {round(100*w/max(tot,1),1)}%')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--event-date')
    ap.add_argument('--backfill-all', action='store_true')
    ap.add_argument('--summary', action='store_true')
    ap.add_argument('--regrade', action='store_true',
                    help='re-process rows that already have graded_at')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.event_date:
        grade_event(args.event_date, dry_run=args.dry_run, regrade=args.regrade)
    elif args.backfill_all:
        backfill_all(dry_run=args.dry_run, regrade=args.regrade)
    elif args.summary:
        summary()
    else:
        print('specify --event-date, --backfill-all, or --summary')

    # 2026-09-25: make a broken method parser loud. The old code defaulted an
    # unparseable detail to 'DEC', so a parser that stopped matching produced
    # 108 fabricated "decisions" instead of an error. Now it reports.
    if _UNPARSED_METHODS:
        from collections import Counter as _C
        print(f'\n🚨 method unparsed on {len(_UNPARSED_METHODS)} fight(s) — '
              f'method_actual/distance_actual left NULL rather than guessed.')
        for _d, _n in _C(_UNPARSED_METHODS).most_common(5):
            print(f'    {_n:3d}x  detail={_d!r}')
        print('    If this is every fight, the ESPN detail format changed and '
              'the tag list above needs updating.')


if __name__ == '__main__':
    main()
