"""Which external sources are DEFINED but produce nothing? — per sport.

Andy 2026-10-10, after the NCAAF Saturday-refresh fix: "yes" to checking
whether NFL/MLB/NHL have the same dead-source-in-a-subset problem.

THE BUG CLASS THIS EXISTS TO CATCH. pull_externals_ncaaf.py's Saturday
refresh ran
    sources = ['action', 'vsin', 'bettingpros']
on the biggest slate of the week. Measured over every NCAAF row ever written,
vsin and bettingpros had produced ZERO — so the refresh fetched ONE working
source and had done all season. Nothing errored, nothing alerted. A dead
source inside a hardcoded subset is indistinguishable from a live source that
happened to return nothing, which is precisely why it hid.

The same three-name subset appears in pull_externals_nfl.py and
pull_externals_ncaab.py, and a four-name variant in pull_externals_mlb.py.
Whether those are dead is a per-sport question: five sources are documented as
MLB-ONLY (vsin, docsports, bettingpros, betfirm, tonyspicks), so vsin being
dead for NCAAF says nothing about vsin for MLB. This measures it rather than
assuming either way.

WHAT IT REPORTS, per sport:
  * every source DEFINED in that sport's registry / fetcher map
  * rows each has ever written, and when it last wrote one
  * DARK sources: defined, zero rows, ever
  * STALE sources: have rows but nothing recent
  * and for each hardcoded subset, how many of its members are actually live

Source names are read from the puller files rather than hardcoded here, so a
newly added source appears without touching this script.

READ ONLY. Writes nothing.

CLI
    python audit_external_source_coverage.py
    python audit_external_source_coverage.py --stale-days 14
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
HERE = Path(__file__).parent

#: sport -> puller file. NHL/NBA delegate to externals_pro_core, whose
#: fetchers() registry is read instead of a local SOURCE_REGISTRY.
PULLERS = {
    'MLB': 'pull_externals_mlb.py',
    'NFL': 'pull_externals_nfl.py',
    'NCAAF': 'pull_externals_ncaaf.py',
    'NCAAB': 'pull_externals_ncaab.py',
    'NHL': 'externals_pro_core.py',
    'NBA': 'externals_pro_core.py',
    'UFC': 'pull_externals_ufc.py',
}


def _page(t, p, cap=400000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:140]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def defined_sources(path: Path) -> set:
    """Source names a puller declares, read from the file itself."""
    if not path.exists():
        return set()
    src = path.read_text(encoding='utf-8', errors='replace')
    names: set = set()
    # SOURCE_REGISTRY = { 'name': fetch_name, ... }
    for m in re.finditer(r'SOURCE_REGISTRY\s*=\s*\{(.*?)\n\}', src, re.S):
        names |= set(re.findall(r"^\s*'([a-z_]+)'\s*:", m.group(1), re.M))
    # externals_pro_core: def fetchers(self) -> dict: return {'name': ...}
    for m in re.finditer(r'def fetchers\(self\).*?return \{(.*?)\}',
                         src, re.S):
        names |= set(re.findall(r"'([a-z_]+)'\s*:", m.group(1)))
    # SOURCES = {...} style maps used by some pullers
    for m in re.finditer(r'^SOURCES\s*=\s*\{(.*?)\n\}', src, re.S | re.M):
        names |= set(re.findall(r"^\s*'([a-z_]+)'\s*:", m.group(1), re.M))
    # Drop config keys that live inside the same dicts rather than being
    # source names. These are field names, not sources.
    return names - {'base_url', 'label', 'ttl_hours', 'fade_flag',
                    'surface', 'enabled', 'url', 'slug'}


def subsets(path: Path) -> list:
    """Hardcoded `sources = [...]` lists — the thing that hid the NCAAF bug."""
    if not path.exists():
        return []
    src = path.read_text(encoding='utf-8', errors='replace')
    out = []
    for m in re.finditer(r"sources\s*=\s*\[([^\]]*?)\]", src, re.S):
        names = re.findall(r"'([a-z_]+)'", m.group(1))
        if len(names) >= 2:          # a single [args.source] is not a subset
            line = src[:m.start()].count('\n') + 1
            out.append((line, names))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stale-days', type=int, default=14, dest='stale')
    a = ap.parse_args()
    today = dt.date.today()

    rows = _page('external_picks', {'select': 'sport,source,pulled_at,'
                                              'game_date'})
    print(f'=== {len(rows)} external_picks rows total\n')
    live = collections.defaultdict(collections.Counter)
    last = collections.defaultdict(dict)
    for x in rows:
        sp, s = str(x.get('sport')), str(x.get('source'))
        live[sp][s] += 1
        d = str(x.get('pulled_at') or x.get('game_date') or '')[:10]
        if d and d > last[sp].get(s, ''):
            last[sp][s] = d

    dark_total = stale_total = 0
    broken_subsets = []
    for sport in ('MLB', 'NFL', 'NCAAF', 'NCAAB', 'NHL', 'NBA', 'UFC'):
        path = HERE / PULLERS[sport]
        dfn = defined_sources(path)
        got = live.get(sport, collections.Counter())
        print(f'── {sport}  ({PULLERS[sport]})')
        if not dfn:
            print(f'     could not read a source registry — skipping')
            continue
        dark, stale, ok = [], [], []
        for s in sorted(dfn):
            n = got.get(s, 0)
            if n == 0:
                dark.append(s)
                continue
            ld = last[sport].get(s, '')
            age = ((today - dt.date.fromisoformat(ld)).days
                   if re.fullmatch(r'\d{4}-\d\d-\d\d', ld) else 999)
            (stale if age > a.stale else ok).append((s, n, ld, age))
        print(f'     {len(dfn)} defined · {len(ok)} live · '
              f'{len(stale)} stale · {len(dark)} DARK')
        for s, n, ld, age in sorted(ok, key=lambda z: -z[1]):
            print(f'       ok    {s:<16}{n:>6} rows · last {ld}')
        for s, n, ld, age in sorted(stale, key=lambda z: -z[1]):
            print(f'       STALE {s:<16}{n:>6} rows · last {ld} '
                  f'({age}d ago)')
        for s in dark:
            print(f'       DARK  {s:<16}     0 rows · NEVER produced for '
                  f'{sport}')
        dark_total += len(dark)
        stale_total += len(stale)

        # the subset check — the actual bug class
        for line, names in subsets(path):
            livec = [s for s in names if got.get(s, 0) > 0]
            deadc = [s for s in names if got.get(s, 0) == 0]
            if deadc:
                broken_subsets.append((sport, line, names, livec, deadc))
                print(f'     ⚠ SUBSET at {PULLERS[sport]}:{line} '
                      f'{names}')
                print(f'       -> {len(livec)} live, {len(deadc)} DEAD '
                      f'({deadc}) — this subset fetches '
                      f'{len(livec)} working source(s)')
        print()

    print('=' * 74)
    print(f'  {dark_total} dark source(s) · {stale_total} stale · '
          f'{len(broken_subsets)} hardcoded subset(s) containing a dead '
          f'source')
    if broken_subsets:
        print('\n  SUBSETS TO FIX — each one silently runs fewer sources than')
        print('  it appears to, and a dead member looks identical to a live')
        print('  one that returned nothing:')
        for sport, line, names, livec, deadc in broken_subsets:
            print(f'    {sport:<6} {PULLERS[sport]}:{line}')
            print(f'           dead: {deadc}   live: {livec}')
    print('\n  A DARK source is not automatically a bug: five sources are')
    print('  documented MLB-ONLY (vsin, docsports, bettingpros, betfirm,')
    print('  tonyspicks), so being dark for NCAAF is expected for those. The')
    print('  bug is a dark source sitting inside a SUBSET that is relied on.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
