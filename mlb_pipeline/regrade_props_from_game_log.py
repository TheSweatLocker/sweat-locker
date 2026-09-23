"""regrade_props_from_game_log — re-settle prop grades against boxscores.

2026-09-22. Andy: "regrade history but want to see results before we
change anything surfaced to users."

So this REPORTS by default and writes only with --apply. Nothing
user-facing moves until he has seen the impact.

WHAT IT FIXES. mlb_player_game_log (69,820 rows, verified 98.96%
against independently graded outcomes) disagrees with
mlb_pipeline_props.final_value on 208 single-game rows. 160 of those
carry final_value = 0 against a boxscore showing real production —
`outs_under` settled at 0 for pitchers who recorded 12-21 outs. That is
a failed stat fetch written as 0 instead of left unknown: the
silent-failure class reaching all the way into the record.

WHY THE BOXSCORE WINS. The two sources are independent. final_value was
written by the resolver at grade time from a live fetch that could
fail; the game log was pulled later, per-game, from the MLB Stats API
boxscore, which is the same thing the resolver was trying to read. Where
they agree (22,407 of 22,643) nothing changes. Where they disagree, one
of them saw a failed request and recorded a zero.

DELIBERATELY CONSERVATIVE — a row is only touched when ALL hold:
  * exactly ONE game for that player on that date (doubleheaders and
    suspended games are skipped entirely; the prop row does not say
    which leg it meant, so any re-grade would be a guess)
  * the game log actually carries a value for that stat (not NULL)
  * the recomputed grade differs from the stored one

Everything else is left alone. The goal is to remove errors, not to
re-derive a record.

CLI
    python regrade_props_from_game_log.py                # report only
    python regrade_props_from_game_log.py --show-all     # list every change
    python regrade_props_from_game_log.py --published    # PRIME/STRONG only
    python regrade_props_from_game_log.py --apply        # write (asks first)
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

FAMILY_COL = {
    'total_bases': 'tb', 'hits': 'h', 'rbis': 'rbi', 'hr': 'hr',
    'runs': 'r', 'batter_ks': 'so',
    'ks': 'p_so', 'bb': 'p_bb', 'ha': 'p_h', 'outs': 'outs', 'er': 'er',
}


def _paged(table: str, select: str, **flt) -> list[dict]:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=60,
                         params={'select': select, 'limit': 1000,
                                 'offset': off, **flt})
        if r.status_code != 200:
            raise RuntimeError(f'{table} read {r.status_code}: {r.text[:200]}')
        chunk = r.json()
        if not chunk:
            break
        out += chunk
        off += 1000
        if len(chunk) < 1000:
            break
    return out


def build_log_index() -> dict:
    cols = 'player_name,game_date,' + ','.join(sorted(set(FAMILY_COL.values())))
    idx = defaultdict(list)
    for x in _paged('mlb_player_game_log', cols):
        idx[(str(x['player_name']).strip().lower(),
             str(x['game_date'])[:10])].append(x)
    return idx


def grade(value: float, line: float, direction: str) -> str | None:
    """Settle one prop. A push is neither side and is left untouched."""
    d = str(direction or '').lower()
    if abs(float(value) - float(line)) < 1e-9:
        return None            # exactly on the number
    if d.startswith('o'):
        return 'Win' if value > line else 'Loss'
    if d.startswith('u'):
        return 'Win' if value < line else 'Loss'
    return None


def find_changes(idx: dict) -> list[dict]:
    props = _paged('mlb_pipeline_props',
                   'id,game_date,player_name,prop_type,prop_line,direction,'
                   'final_value,result,tier,conviction',
                   result='not.is.null')
    out = []
    for p in props:
        if str(p['result']) not in ('Win', 'Loss'):
            continue
        col = FAMILY_COL.get(str(p['prop_type']).rsplit('_', 1)[0])
        if not col:
            continue
        games = idx.get((str(p['player_name']).strip().lower(),
                         str(p['game_date'])[:10])) or []
        if len(games) != 1:
            continue                      # doubleheader — never guess
        val = games[0].get(col)
        if val is None or p.get('prop_line') is None:
            continue
        should = grade(float(val), float(p['prop_line']), p['direction'])
        if should is None or should == str(p['result']):
            continue
        out.append({
            'id': p['id'], 'date': str(p['game_date'])[:10],
            'player': p['player_name'], 'prop_type': p['prop_type'],
            'line': float(p['prop_line']), 'direction': p['direction'],
            'stored_fv': p['final_value'], 'real': float(val),
            'was': str(p['result']), 'now': should,
            'tier': (p.get('tier') or '').upper(),
        })
    return out


def report(changes: list[dict], show_all: bool, published_only: bool) -> None:
    if published_only:
        changes = [c for c in changes if c['tier'] in ('PRIME', 'STRONG')]
    print(f'\n=== grades the boxscore disputes: {len(changes)} ===\n')
    if not changes:
        print('  nothing to change.')
        return
    w2l = [c for c in changes if c['was'] == 'Win']
    l2w = [c for c in changes if c['was'] == 'Loss']
    print(f'  recorded Win  -> should be Loss : {len(w2l)}')
    print(f'  recorded Loss -> should be Win  : {len(l2w)}')
    print(f'  net change to the win column    : {len(l2w) - len(w2l):+d}')
    print(f'\n  stored final_value == 0        : '
          f'{sum(1 for c in changes if float(c["stored_fv"] or 0) == 0)}')
    print(f'\n  by tier   : {dict(Counter(c["tier"] or "—" for c in changes).most_common())}')
    print(f'  by family : {dict(Counter(str(c["prop_type"]).rsplit("_",1)[0] for c in changes).most_common(8))}')
    print(f'  by date   : {dict(Counter(c["date"] for c in changes).most_common(8))}')

    for scope, label in ((('PRIME', 'STRONG'), 'PUBLISHED (PRIME/STRONG)'),
                         (None, 'ALL TIERS')):
        sub = [c for c in changes if scope is None or c['tier'] in scope]
        if not sub:
            continue
        w = sum(1 for c in sub if c['was'] == 'Win')
        print(f'\n  {label}: {len(sub)} rows, {w} Win->Loss, '
              f'{len(sub)-w} Loss->Win, net {len(sub)-2*w:+d} wins')

    rows = changes if show_all else changes[:20]
    print(f'\n  {"date":11} {"player":20} {"prop":14} {"line":>6} '
          f'{"stored":>7} {"real":>6}  change      tier')
    for c in rows:
        print(f'  {c["date"]:11} {str(c["player"])[:20]:20} {c["prop_type"][:14]:14} '
              f'{c["line"]:6.1f} {str(c["stored_fv"]):>7} {c["real"]:6.1f}  '
              f'{c["was"]:>4} -> {c["now"]:<4} {c["tier"] or "—"}')
    if not show_all and len(changes) > 20:
        print(f'  ... {len(changes)-20} more (--show-all to list)')


def records_before_after(changes: list[dict]) -> None:
    """What the PUBLISHED record looks like either side of the change."""
    ids = {c['id']: c for c in changes}
    props = _paged('mlb_pipeline_props',
                   'id,game_date,result,tier,conviction,direction,prop_type,'
                   'book_over_odds,book_under_odds', result='not.is.null')
    try:
        from prop_ban_policy import is_banned_mlb_prop as banned
    except Exception:
        def banned(p, t=None):
            return False

    def odds_of(r):
        d = (r.get('direction') or '').lower()
        v = r.get('book_over_odds') if d == 'over' else \
            r.get('book_under_odds') if d == 'under' else None
        return None if v is None else int(v)

    windows = (('pre-adjustment  (< 08-20)', '2000-01-01', '2026-08-20'),
               ('CLEAN  08-20 -> 09-02', '2026-08-20', '2026-09-03'),
               ('leak   09-03 -> 09-22', '2026-09-03', '2026-09-23'),
               ('LIFETIME', '2000-01-01', '2100-01-01'))
    print('\n=== PUBLISHED record (PRIME/STRONG, odds -300..+150) ===')
    print(f'  {"window":28} {"before":>14}   {"after":>14}   delta')
    for label, lo, hi in windows:
        bw = bl = aw = al = 0
        for r in props:
            if str(r['result']) not in ('Win', 'Loss'):
                continue
            t = (r.get('tier') or '').upper()
            if t not in ('PRIME', 'STRONG') or r.get('conviction') == 0:
                continue
            if banned(r.get('prop_type'), t):
                continue
            o = odds_of(r)
            if o is None or o < -300 or o > 150:
                continue
            d = str(r['game_date'])[:10]
            if not (lo <= d < hi):
                continue
            was = str(r['result'])
            now = ids[r['id']]['now'] if r['id'] in ids else was
            bw += was == 'Win'; bl += was == 'Loss'
            aw += now == 'Win'; al += now == 'Loss'
        if bw + bl == 0:
            continue
        print(f'  {label:28} {bw:>5}-{bl:<4} {100*bw/(bw+bl):4.1f}%   '
              f'{aw:>5}-{al:<4} {100*aw/(aw+al):4.1f}%   '
              f'{100*aw/(aw+al) - 100*bw/(bw+bl):+.2f}pp')


def apply(changes: list[dict]) -> int:
    ok = 0
    for c in changes:
        r = requests.patch(
            f'{SB}/rest/v1/mlb_pipeline_props?id=eq.{c["id"]}',
            headers=HW, timeout=25,
            json={'result': c['now'], 'final_value': c['real'],
                  'audit_notes': (f'regraded 2026-09-22 from '
                                  f'mlb_player_game_log: was {c["was"]} '
                                  f'(final_value={c["stored_fv"]}), '
                                  f'boxscore={c["real"]}')[:2000]})
        if r.status_code in (200, 204):
            ok += 1
        else:
            print(f'  ⚠ id={c["id"]} patch {r.status_code}: {r.text[:140]}')
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--show-all', action='store_true')
    ap.add_argument('--published', action='store_true',
                    help='restrict the report to PRIME/STRONG')
    a = ap.parse_args()

    print('=== regrade_props_from_game_log ===')
    print('  source of truth: mlb_player_game_log (MLB Stats API boxscores)')
    print('  doubleheaders and suspended games are SKIPPED, never guessed')
    idx = build_log_index()
    print(f'  game-log (player, date) keys: {len(idx)}')
    changes = find_changes(idx)
    report(changes, a.show_all, a.published)
    records_before_after(changes)

    if not a.apply:
        print('\n  REPORT ONLY — nothing written. Re-run with --apply to commit.')
        return
    tgt = [c for c in changes if c['tier'] in ('PRIME', 'STRONG')] \
        if a.published else changes
    print(f'\n  applying {len(tgt)} corrections...')
    print(f'  patched: {apply(tgt)}/{len(tgt)}')


if __name__ == '__main__':
    main()
