#!/usr/bin/env python3
"""Audit every PUBLISHED prop grade against the box score. Read-only.

WHY (2026-10-06)
----------------
settle_prop_receipts --validate found that our published record contains
grades that contradict the box score:

    NFL  13 of 1,337 compared   (99.0% agree)
    MLB   7 of   154 compared   (95.5% agree)

Aaron Rodgers is a LOSS on OVER 31.5 pass attempts in a game he threw 40.
Alec Burleson is a LOSS on OVER 0.5 hits in a game he had 2.

Those numbers are not comparable and neither is a rate. The MLB figure came
from a --limit 400 run ordered game_date.asc, so it sampled one day. The NFL
run covered the whole season and found that eleven of its thirteen sit on a
single date, 09-13, written by resolve_nfl_props.py before it was disabled on
09-19 for grading off `m.iloc[-1]` — the player's latest row of the season
rather than the game being graded. All seven MLB ones sit on 09-11.

So the shape of the problem is "bad grading RUNS on particular dates", not a
uniform error rate, and the only way to size it is to check every row. That is
what this does.

WHY NOT JUST RUN --validate
settle_prop_receipts resolves MLB actuals through the MLB stats API, one
request per receipt. A full history pass takes hours, which is why it had only
ever been run on a slice. This reads mlb_player_game_log instead — a table the
settler never touches — so a full pass takes seconds AND the verdict is
independent of the code being audited. When two sources that share no code
agree that a stored grade is wrong, it is wrong.

WHAT IT REFUSES TO JUDGE
A disagreement is only reported when the backed side is unambiguous:

  * PASS reads were never a bet (graded NO_ACTION) — skipped.
  * A FADE read backs the OPPOSITE side of the one the receipt displays, so
    the expected result is flipped. Where the source read survives, its
    verdict decides. Where it is GONE and the family has ever been faded, the
    backed side is genuinely unknowable and the row is counted undecidable,
    never wrong. (Reading the verdict off the receipt's own audit blob is not
    an option: on exactly that population it is wrong 48 of 48 times in NFL
    and 87% of the time in MLB.)
  * bb_* and ks_* mean different columns for a pitcher than for a batter
    (walks allowed vs walks drawn). The role comes from the log row itself —
    innings/batters-faced present means pitcher. A row whose role cannot be
    established is undecidable, not guessed.
  * No log row, or a NULL stat, is undecidable.

Nothing here writes. Use --dates to feed a correction run once Andy approves
one.

    python audit_prop_grades.py                      # MLB, full history
    python audit_prop_grades.py --sport NFL
    python audit_prop_grades.py --since 2026-09-01 --show 40
"""
from __future__ import annotations
import argparse
import collections
import os
import sys
import unicodedata as ud
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def page(table: str, params: dict) -> list:
    """Every row, 1000 at a time. A bare select caps at 1000 silently.

    Retries on transport errors, and RAISES if a page can't be fetched. A
    12,000-row audit does ~30 requests and one of them reset the connection
    mid-run; swallowing that would have returned a short list and quietly
    changed the denominator of every percentage printed below.
    """
    import time
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        chunk = None
        for attempt in range(4):
            try:
                r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=q,
                                 timeout=120)
            except requests.RequestException as e:
                if attempt == 3:
                    raise SystemExit(f'{table} offset={off}: {e}')
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code not in (200, 206):
                raise SystemExit(f'{table} {r.status_code}: {r.text[:300]}')
            chunk = r.json()
            if not isinstance(chunk, list):
                raise SystemExit(f'{table}: {chunk}')
            break
        out += chunk
        if len(chunk) < 1000:
            return out
        off += 1000


def norm(name: str) -> str:
    import re
    n = ud.normalize('NFKD', name or '').encode('ascii', 'ignore').decode()
    n = re.sub(r'\b(jr|sr|ii|iii|iv|v)\b\.?', '', n.lower())
    n = re.sub(r'[^a-z0-9\s]', '', n)
    return re.sub(r'\s+', ' ', n).strip()


# family stem -> (pitcher column, batter column). None = not valid for that role.
MLB_COL = {
    'hits':  (None,   'h'),      # batter hits
    'ha':    ('p_h',  None),     # hits ALLOWED
    'er':    ('er',   None),
    'outs':  ('outs', None),
    'ks':    ('p_so', 'so'),     # strikeouts thrown vs struck out
    'bb':    ('p_bb', 'bb'),     # walks allowed vs walks drawn
    'tb':    (None,   'tb'),
    'rbi':   (None,   'rbi'),
    'runs':  (None,   'r'),
    'hr':    (None,   'hr'),
    'sb':    (None,   'sb'),
}
NFL_COL = {
    'reception_yds': 'receiving_yards', 'receptions': 'receptions',
    'targets': 'targets', 'reception_tds': 'receiving_tds',
    'rush_yds': 'rushing_yards', 'rush_attempts': 'carries',
    'rush_tds': 'rushing_tds', 'pass_yds': 'passing_yards',
    'pass_attempts': 'attempts', 'pass_completions': 'completions',
    'pass_tds': 'passing_tds', 'pass_interceptions': 'interceptions',
}
NFL_SEASON_START = '2026-09-09'   # preseason reuses week numbers


def stem_and_side(prop_type: str):
    """('hits_over') -> ('hits', 'over'). Suffix is informational only; the
    backed side comes from pick_side, which is what users were shown."""
    pt = (prop_type or '').lower()
    for suf in ('_over', '_under'):
        if pt.endswith(suf):
            return pt[:-len(suf)], suf[1:]
    return pt, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='MLB')
    ap.add_argument('--since', default='2026-01-01')
    ap.add_argument('--show', type=int, default=25)
    ap.add_argument('--dates', action='store_true',
                    help='print only the affected game_dates, one per line')
    args = ap.parse_args()
    sport = args.sport.upper()

    recs = page('public_receipts', {
        'select': 'id,game_date,player_name,prop_type,pick_side,pick_line,'
                  'pick_odds,result,source_id,surface,capture_mode',
        'market': 'eq.prop', 'sport': f'eq.{sport}', 'result': 'not.is.null',
        'game_date': f'gte.{args.since}', 'order': 'game_date.asc'})
    if not args.dates:
        print(f'=== audit_prop_grades · {sport} · since {args.since} ===')
        print(f'  {len(recs)} graded prop receipt(s)')

    # Verdicts, for the FADE question only. Source-gone stays unknowable.
    # Only numeric source_ids address prop_jerry_reads. Card surfaces store a
    # composite text key there ("prop:AJ Blubaugh|outs_under|5.5"), and feeding
    # one to an id=in.() filter is a 400 on the whole batch, not a skipped row.
    sids = sorted({str(r['source_id']) for r in recs
                   if r.get('source_id') and str(r['source_id']).isdigit()})
    verdict = {}
    for i in range(0, len(sids), 150):
        csv = ','.join(f'"{x}"' for x in sids[i:i + 150])
        for z in page('prop_jerry_reads',
                      {'select': 'id,call_verdict', 'id': f'in.({csv})'}):
            verdict[str(z['id'])] = (z.get('call_verdict') or '').upper()
    # "Could a missing verdict have been a FADE?" — answered from the verdicts
    # that survive, exactly as settle_prop_receipts does it, rather than from a
    # hardcoded list that would go stale the day a family becomes fadeable.
    faded_families = set()
    for r in recs:
        # prop_type is nullable on some reconstructed receipts; a None in this
        # set makes sorted() raise, and more importantly it would match the
        # `in faded_families` test for every other None-typed row.
        if (verdict.get(str(r.get('source_id'))) == 'FADE'
                and r.get('prop_type')):
            faded_families.add(r['prop_type'])
    if not args.dates:
        print(f'  source reads surviving: {len(verdict)}/{len(sids)}')
        print(f'  families ever faded: {sorted(faded_families) or "none"}')

    # Box scores, keyed by (normalised name, date) for MLB / (name, week) NFL.
    log = {}
    if sport == 'MLB':
        # DOUBLEHEADERS. Keyed by (player, date) a second game silently
        # overwrites the first, and the audit would then compare a prop from
        # game 1 against game 2's box score and call a correct grade wrong.
        # Keep every row per key and refuse to judge when there is more than
        # one, since the receipt carries no game_pk to disambiguate with.
        for row in page('mlb_player_game_log',
                        {'select': 'player_name,game_date,game_pk,h,er,p_so,'
                                   'p_h,p_bb,outs,bb,so,tb,rbi,r,hr,sb,ip,bf',
                         'game_date': f'gte.{args.since}'}):
            log.setdefault(
                (norm(row['player_name']), str(row['game_date'])[:10]),
                []).append(row)
    else:
        wk = {}
        for row in page('nfl_game_context',
                        {'select': 'game_date,week,season',
                         'game_date': f'gte.{NFL_SEASON_START}'}):
            if row.get('week') is not None:
                wk[str(row['game_date'])[:10]] = (row.get('season'), row['week'])
        for row in page('nfl_player_stats', {'select': '*', 'season': 'eq.2026'}):
            log[(norm(row.get('player_name')), row.get('week'))] = row
        log['__wk__'] = wk

    wrong, undec = [], collections.Counter()
    for r in recs:
        stored = str(r.get('result') or '').upper()
        if stored not in ('WIN', 'LOSS', 'PUSH'):
            undec['stored is not a W/L/P outcome'] += 1
            continue
        v = verdict.get(str(r.get('source_id')))
        if v == 'PASS':
            undec['PASS read (no bet)'] += 1
            continue
        stem, _suf = stem_and_side(r['prop_type'])
        side = (r.get('pick_side') or '').strip().lower()
        if side not in ('over', 'under'):
            undec['no usable pick_side'] += 1
            continue
        if v == 'FADE':
            side = 'under' if side == 'over' else 'over'
        elif v is None and r['prop_type'] in faded_families:
            undec['fadeable family, source read gone'] += 1
            continue

        if sport == 'MLB':
            rows = log.get((norm(r['player_name']), str(r['game_date'])[:10]))
            if not rows:
                undec['no box-score row'] += 1
                continue
            if len(rows) > 1:
                undec['doubleheader: which game is unknowable'] += 1
                continue
            row = rows[0]
            cols = MLB_COL.get(stem)
            if not cols:
                undec[f'family not mapped: {stem}'] += 1
                continue
            is_pitcher = (row.get('ip') is not None or row.get('bf') is not None)
            col = cols[0] if is_pitcher else cols[1]
            if col is None:
                undec[f'{stem} invalid for role '
                      f'{"pitcher" if is_pitcher else "batter"}'] += 1
                continue
        else:
            season_week = log['__wk__'].get(str(r['game_date'])[:10])
            if not season_week:
                undec['date outside the NFL week map'] += 1
                continue
            row = log.get((norm(r['player_name']), season_week[1]))
            if not row:
                undec['no box-score row'] += 1
                continue
            col = NFL_COL.get(stem)
            if not col:
                undec[f'family not mapped: {stem}'] += 1
                continue

        raw, line = row.get(col), r.get('pick_line')
        if raw is None or line is None:
            undec['stat or line is NULL'] += 1
            continue
        actual, line = float(raw), float(line)
        truth = ('Push' if actual == line else
                 'Win' if ((actual > line) if side == 'over' else (actual < line))
                 else 'Loss')
        if truth.upper() != stored:
            wrong.append((r, truth, actual, col, v))

    if args.dates:
        for d in sorted({r['game_date'] for r, *_ in wrong}):
            print(d)
        return 0

    judged = len(wrong) + 0
    total_judged = len(recs) - sum(undec.values())
    print(f'\n  judged: {total_judged}   WRONG: {len(wrong)}'
          + (f'   ({len(wrong)/total_judged*100:.2f}%)' if total_judged else ''))
    print(f'  undecidable: {sum(undec.values())}')
    for k, n in undec.most_common():
        print(f'     {n:6d}  {k}')

    if not wrong:
        print('\n  ✓ every judgeable published grade matches the box score.')
        return 0

    print(f'\n  --- wrong grades by game_date ---')
    bydate = collections.Counter(r['game_date'] for r, *_ in wrong)
    perdate_total = collections.Counter(r['game_date'] for r in recs)
    for d, n in sorted(bydate.items()):
        print(f'     {d}  {n:4d} wrong of {perdate_total[d]:4d} receipts'
              f'   ({n/perdate_total[d]*100:5.1f}%)')
    print(f'  --- by family ---')
    for k, n in collections.Counter(r['prop_type'] for r, *_ in wrong).most_common(12):
        print(f'     {k:24s} {n}')
    print(f'  --- by stored value ---')
    print('    ', dict(collections.Counter(str(r['result']) for r, *_ in wrong)))

    du = 0.0
    priced = 0
    for r, truth, *_ in wrong:
        o = r.get('pick_odds')
        if o is None:
            continue
        o = float(o)

        def u(res):
            if res.upper() == 'PUSH':
                return 0.0
            if res.upper() == 'WIN':
                return (o / 100.0) if o > 0 else (100.0 / abs(o))
            return -1.0
        du += u(truth) - u(str(r['result']))
        priced += 1
    print(f'\n  unit impact of correcting all of them: {du:+.3f}u '
          f'across {priced} priced row(s)')
    print(f'  (positive means the PUBLISHED record is currently worse than the truth)')

    print(f'\n  --- first {args.show} ---')
    for r, truth, actual, col, v in wrong[:args.show]:
        print(f'    id={r["id"]:<8} {r["game_date"]} {r["player_name"][:20]:20s} '
              f'{r["prop_type"][:20]:20s} {(r.get("pick_side") or "")[:5]:5s} '
              f'{str(r.get("pick_line")):>6s}  {col}={actual:<7g} '
              f'stored={str(r["result"]):<5s} -> TRUE={truth:<5s} '
              f'verdict={v or "-"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
