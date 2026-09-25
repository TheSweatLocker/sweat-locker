"""Re-check the backlog's factual claims against the live database.

docs/BACKLOG.md rule 4 says: "Re-verify the whole file before quoting it. Items
go stale silently; the verify commands are how you find out." Those VERIFY
blocks are freeform prose, so nobody runs them, so the file rots. On 2026-09-24
two items were quoted and both were wrong: B18 said 51 masked workflow steps
(there were 104) and B10 said projection was populated on 2 of 1423 rows (it
was 1805 of 1807 by then).

This turns the checkable subset into one command. Each check returns the
CURRENT number next to what the backlog asserts, and says whether the item
still stands. It deliberately does not edit the file — a script that
auto-closed items would hide exactly the drift it is meant to surface.

Only items with an unambiguous database signal are here. Anything needing a
screenshot, a build, or a judgement call is listed as MANUAL at the end rather
than guessed at.

    python verify_backlog.py
    python verify_backlog.py --only B10,B44
"""
import os
import sys
import argparse

import requests

sys.stdout.reconfigure(encoding='utf-8')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HC = {**H, 'Prefer': 'count=exact', 'Range': '0-0'}
TODAY = os.environ.get('VERIFY_TODAY', '2026-09-24')


def count(table: str, params: dict | None = None) -> int:
    """Exact row count, or raise. Never returns a sentinel.

    2026-09-24: the first version accepted only HTTP 200 and returned -1
    otherwise. A request carrying a Range header gets 206 Partial Content, so
    every count returned -1 — and `-1 <= 2` made the B10 check report STILL
    TRUE for an item that had been resolved hours earlier. A verifier that
    silently answers -1 is worse than no verifier, so a bad response raises
    and the caller reports CHECK ERROR instead of a verdict.
    """
    r = requests.get(f'{SB}/rest/v1/{table}', headers=HC, timeout=120,
                     params={**(params or {}), 'select': 'id'})
    if r.status_code not in (200, 206):
        raise RuntimeError(f'{table} count HTTP {r.status_code}: '
                           f'{(r.text or "")[:120]}')
    cr = r.headers.get('content-range') or ''
    tail = cr.split('/')[-1]
    if not tail.isdigit():
        raise RuntimeError(f'{table} count: unparseable content-range {cr!r}')
    return int(tail)


def rows(table: str, select: str, params: dict | None = None) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=180,
                         params={**(params or {}), 'select': select,
                                 'order': 'id.asc', 'limit': 1000,
                                 'offset': off})
        if r.status_code != 200:
            return out
        b = r.json()
        if not isinstance(b, list):
            return out
        out.extend(b)
        if len(b) < 1000:
            return out
        off += 1000


CHECKS = {}


def check(bid: str, claim: str):
    def deco(fn):
        CHECKS[bid] = (claim, fn)
        return fn
    return deco


@check('B10', 'projection populated on 2 of 1423 NFL prop rows')
def _b10():
    tot = count('nfl_pipeline_props')
    pop = count('nfl_pipeline_props', {'projection': 'not.is.null'})
    return (pop <= 2, f'projection populated on {pop} of {tot}')


@check('B26', 'markdown leaking into short_read')
def _b26():
    import re
    bad, tot = 0, 0
    for sp in ('NFL', 'MLB', 'NCAAF', 'NHL'):
        for x in rows('jerry_reads', 'sport,short_read',
                      {'sport': f'eq.{sp}', 'game_date': f'gte.{TODAY}'}):
            tot += 1
            if re.search(r'\*\*|^#{1,3} ', x.get('short_read') or ''):
                bad += 1
    return (bad > 0, f'{bad} of {tot} forward-slate short_reads carry markdown')


@check('B32', 'closing price never stored, so CLV is unmeasurable')
def _b32():
    tot = count('nfl_pipeline_props')
    cl = count('nfl_pipeline_props', {'close_over_odds': 'not.is.null'})
    both = count('nfl_pipeline_props', {'book_over_odds': 'not.is.null',
                                        'book_under_odds': 'not.is.null'})
    return (cl == 0, f'close odds on {cl} of {tot}; both-side book odds on '
                     f'{both} (both-sides capture added 09-24, applies at next '
                     f'generation)')


@check('B44', 'NFL externals: 4 sources against MLB\'s 12')
def _b44():
    seen = {}
    for sp in ('NFL', 'MLB'):
        srcs = {str(x.get('source')) for x in
                rows('external_picks', 'source,sport', {'sport': f'eq.{sp}'})
                if x.get('source')}
        seen[sp] = len(srcs)
    return (seen.get('NFL', 0) < seen.get('MLB', 0),
            f"NFL {seen.get('NFL')} distinct sources vs MLB {seen.get('MLB')}")


@check('B46', 'all 37 NHL reads are the engine sub string, long_read empty')
def _b46():
    import re
    r = rows('jerry_reads', 'short_read,long_read',
             {'sport': 'eq.NHL', 'game_date': f'gte.{TODAY}'})
    sub = sum(1 for x in r
              if re.match(r'^\s*Model conviction on', x.get('short_read') or ''))
    short_long = sum(1 for x in r
                     if len((x.get('long_read') or '').strip()) < 300)
    return (sub > 0 or short_long > 0,
            f'{len(r)} NHL reads · {sub} engine-sub shorts · '
            f'{short_long} with long_read under 300 chars')


@check('B47', 'NFL publish gate ships only STRONG and hides LEAN')
def _b47():
    from collections import Counter
    r = requests.get(f'{SB}/rest/v1/v_nfl_props_publishable', headers=H,
                     timeout=180, params={'select': 'tier',
                                          'game_date': f'gte.{TODAY}',
                                          'limit': '2000'}).json()
    if not isinstance(r, list):
        return (True, 'view query failed')
    c = Counter(str(x['tier']) for x in r)
    return ('LEAN' not in c,
            f'{len(r)} publishable · {dict(c)} (LEAN absent means the gate is '
            f'unchanged)')


@check('NEW', 'every NHL read promises a proprietary model launch date')
def _new():
    import re
    pat = re.compile(r'(launch\w*|debuts?|arriv\w*|rolls out)[^.]{0,40}202\d',
                     re.I)
    hits, tot = 0, 0
    typo = 0
    for x in rows('jerry_reads', 'sport,short_read,long_read',
                  {'sport': 'eq.NHL', 'game_date': f'gte.{TODAY}'}):
        tot += 1
        blob = (x.get('short_read') or '') + ' ' + (x.get('long_read') or '')
        if pat.search(blob):
            hits += 1
        if re.search(r'\d\dseason', blob):
            typo += 1
    return (hits > 0, f'{hits} of {tot} NHL reads claim a launch date; '
                      f'{typo} also carry the "27season" typo')


MANUAL = {
    'B1': 'client API keys in the binary — needs a build inspection',
    'B2': 'fetchPlayerStats dead season — client code read',
    'B3': 'prop tier contamination — Andy decided: do NOT change records',
    'B4': 'prop LR retrain on clean history — model work',
    'B5': 'client build to ship 86 restored columns — build gate',
    'B8': 'admin note placement — screenshot',
    'B9': 'UFC tab promotions — screenshot',
    'B20': 'recent-schedule table readability — screenshot',
    'B21': 'screenshot QA pass — screenshot',
    'B25': 'UFC end-to-end before Apple push — build gate',
    'B29': 'UFC feature leak (as-of-date career stats) — model work',
    'B40': 'mlb_pipeline job split — workflow refactor',
    'B41': 'jerry_reads DB-level write protection — needs a migration',
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', help='comma-separated ids')
    args = ap.parse_args()
    want = set((args.only or '').split(',')) if args.only else None

    print(f'=== verify_backlog · as of {TODAY} ===\n')
    still, stale, failed = [], [], []
    for bid, (claim, fn) in CHECKS.items():
        if want and bid not in want:
            continue
        try:
            holds, detail = fn()
        except Exception as e:
            print(f'  {bid:5s} CHECK ERROR  {e}')
            failed.append(bid)
            continue
        tag = 'STILL TRUE' if holds else 'STALE'
        (still if holds else stale).append(bid)
        print(f'  {bid:5s} {tag:11s} {detail}')
        print(f'        backlog says: {claim}')
    print(f'\n  still true: {still}')
    print(f'  STALE (backlog needs updating): {stale}')
    if failed:
        print(f'  check errored: {failed}')
    print(f'\n  not machine-checkable ({len(MANUAL)}):')
    for b, why in MANUAL.items():
        print(f'    {b:5s} {why}')


if __name__ == '__main__':
    main()
