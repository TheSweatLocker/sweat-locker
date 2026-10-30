"""Refuse a source name or internal codename in anything a user can read.

══ 2026-10-30 · WHY A CHECK AND NOT JUST A FIND-AND-REPLACE ═══════════════
ANDY: "add to list of things to do to do a full app scrub of the word kenpom
as it shouldnt be mentioned."

The scrub itself was four edits. The reason this file exists is that the
same ask has already been made once — "Madden" had to become "Sweat Stats"
— and a one-time replace does not hold: the next prose builder, the next
signal template, the next state_message reintroduces it, and nobody notices
because it renders fine. The prop-family ban needed THREE layers before it
stuck (feedback_prop_family_ban_three_layer), and this is the same shape.

WHAT IS BANNED, AND WHAT IS DELIBERATELY NOT
Banned in anything a user can READ. Not banned in column names, table
names, cache keys, env vars, module names or comments — `home_adj_de`,
`kenpom_cache` and `mlb_pipeline/kenpom_pull.py` keep their lineage, because
renaming data plumbing to satisfy a copy rule is how a migration breaks a
pipeline. The metric is what the user needs; whose feed it came from is ours.

The 2026-10-30 sweep found four live exposures, all of which would have
started rendering when NCAAB opened on 11-03:
  * generate_ncaab_game_reads headline  "KenPom efficiency favors X by Y"
  * generate_ncaab_game_reads tier line "(KenPom + four-factors lens)"
  * sport_registry[NCAAB].state_message "probable starters, KenPom refresh"
  * signal_sources id=924 display_prose_template "(KenPom adj_de ...)"

USAGE
  python check_banned_vocabulary.py            # exits 2 if anything is found
  python check_banned_vocabulary.py --quiet
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

#: word -> what to say instead. Matched case-insensitively on word boundaries.
BANNED = {
    'kenpom': 'efficiency ratings / adj off eff / adj def eff',
    'madden': 'Sweat Stats',
}

#: (table, [user-readable columns]). Only fields a user can READ.
#: A column holding plumbing (cache_key, *_name lookups) is not listed.
TABLES = [
    ('sport_registry', ['sport', 'state_message']),
    ('signal_sources', ['id', 'sport', 'signal_key',
                        'display_prose_template', 'description']),
    # signal_registry.notes is DELIBERATELY NOT scanned. It holds internal
    # provenance ("2024-25 SU backfill; end-of-season KenPom snapshot bias")
    # and nothing in app/ reads it -- verified by grep, where the only app
    # reference in this family is a COMMENT in lib/sourcePersona.ts naming
    # signal_sources.display_prose_template. The first run of this check
    # flagged all 7 of those rows, and a guard that reports internal notes as
    # user-facing copy becomes the permanently-red step in
    # feedback_a_detector_must_know_the_intentional_case.
    ('signal_registry', ['id', 'sport', 'signal_name']),
]

#: jerry_reads and the per-sport contexts are large, so they are scanned on a
#: recent window rather than in full.
WINDOWED = [
    ('jerry_reads', ['sport', 'game_date', 'short_read', 'long_read',
                     'call_text'], 'game_date', 30),
]

#: Source files whose STRING LITERALS reach a user. Comments are ignored.
SRC_GLOBS = ['generate_*_game_reads.py', 'jerry_*.py', 'analyst_facts.py',
             '*_game_context.py', 'generate_sweat_card.py',
             'generate_sharp_card.py', 'generate_the_fade.py']


def _rx(word):
    return re.compile(rf'\b{re.escape(word)}\b', re.IGNORECASE)


def page(table, params):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['offset'] = off
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p,
                         timeout=120)
        if r.status_code != 200:
            print(f'  ! {table} HTTP {r.status_code} {r.text[:150]} '
                  f'— CANNOT VERIFY, treating as a failure')
            return None
        d = r.json()
        if not isinstance(d, list):
            return None
        out += d
        if len(d) < 1000:
            return out
        off += 1000


def scan_db(quiet=False) -> list:
    hits = []
    import datetime as dt
    for table, cols in TABLES:
        rows = page(table, {'select': ','.join(cols)})
        if rows is None:
            hits.append((table, '?', 'QUERY FAILED', ''))
            continue
        for r in rows:
            for word, alt in BANNED.items():
                for c in cols:
                    v = r.get(c)
                    if isinstance(v, str) and _rx(word).search(v):
                        hits.append((table, r.get('id') or r.get('sport'),
                                     f'{c} contains {word!r}', v[:120]))
    cutoff = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    for table, cols, datecol, _days in WINDOWED:
        rows = page(table, {'select': ','.join(cols),
                            datecol: f'gte.{cutoff}'})
        if rows is None:
            hits.append((table, '?', 'QUERY FAILED', ''))
            continue
        for r in rows:
            for word, alt in BANNED.items():
                for c in cols:
                    v = r.get(c)
                    if isinstance(v, str) and _rx(word).search(v):
                        hits.append((table, r.get(datecol),
                                     f'{c} contains {word!r}', v[:120]))
    return hits


_STR = re.compile(r"""(?:f?'([^'\n]*)'|f?"([^"\n]*)")""")


def scan_source(quiet=False) -> list:
    """Banned words inside STRING LITERALS only — comments are lineage."""
    hits = []
    here = Path(__file__).parent
    seen = set()
    for g in SRC_GLOBS:
        for fp in sorted(here.glob(g)):
            if fp.name == Path(__file__).name or fp in seen:
                continue
            seen.add(fp)
            try:
                text = fp.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue
            in_doc, doc_q = False, None
            for i, line in enumerate(text.split('\n'), 1):
                st = line.strip()
                # Skip triple-quoted blocks. A DOCSTRING is lineage, not
                # copy -- the first run flagged ncaab_game_context.py:90,
                # "All NCAAB teams' KenPom snapshot for the current season",
                # which is a function docstring and exempt by this module's
                # own stated rule. Without this the guard contradicts its
                # own policy, and a guard that cries wolf gets muted.
                if in_doc:
                    if doc_q and doc_q in st:
                        in_doc, doc_q = False, None
                    continue
                _opened = False
                for q in ('"""', "'''"):
                    if st.startswith(q):
                        if st.count(q) < 2:
                            in_doc, doc_q = True, q
                        _opened = True
                        break
                if _opened or not st or st.startswith('#') or st.startswith('*'):
                    continue
                for m in _STR.finditer(line):
                    lit = m.group(1) or m.group(2) or ''
                    for word in BANNED:
                        if _rx(word).search(lit):
                            # a plumbing identifier is allowed
                            if re.search(rf'{word}_\w+|\w+_{word}', lit,
                                         re.IGNORECASE):
                                continue
                            hits.append((fp.name, i,
                                         f'string literal contains {word!r}',
                                         lit[:110]))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()
    print('=== check_banned_vocabulary ===')
    print(f'banned: {", ".join(BANNED)}  '
          f'(column/table/cache/module names are exempt by design)')
    db = scan_db(a.quiet)
    src = scan_source(a.quiet)
    print(f'\nLIVE DATA: {len(db)} hit(s)')
    for t, key, what, val in db[:30]:
        print(f'  {t} [{key}] {what}')
        if val:
            print(f'      {val}')
    print(f'\nSOURCE STRING LITERALS: {len(src)} hit(s)')
    for f, ln, what, val in src[:30]:
        print(f'  {f}:{ln} {what}')
        print(f'      {val}')
    total = len(db) + len(src)
    if total:
        print(f'\n*** {total} user-facing mention(s). Say the METRIC, not the '
              f'source: ' + '; '.join(f'{w} -> {alt}'
                                      for w, alt in BANNED.items()) + ' ***')
        raise SystemExit(2)
    print('\nclean — no banned vocabulary in any user-readable field.')
    raise SystemExit(0)


if __name__ == '__main__':
    main()
