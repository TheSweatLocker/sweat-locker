"""Validate every PostgREST column list in the app against the live schema.

WHY THIS EXISTS
---------------
PostgREST rejects an ENTIRE select when ONE named column is missing
(42703 -> HTTP 400). There is no partial success. So a single typo or a
column that was renamed server-side silently nulls a whole screen, and
because the app treats `data == null` the same as "no games yet", it
looks like an empty slate rather than an error.

This has now happened four times:
  d91173d2  2026-09-07  NFL  ctx select naming non-existent columns
  8b1a48b6  2026-09-07  NCAAF badges (inverse: columns missing FROM select)
  dc9335c5  2026-09-19  NCAAF MC/SP+ tiles (inverse, same class)
  ef697869  2026-09-13  MLB  supplementary_play/home_era/away_era
                             -> found 2026-09-19 after SIX DAYS live,
                                by screenshot, in a shipped build

The MLB one is the case for automating this: every MLB game detail
rendered blank for six days and nothing anywhere reported a problem.

WHAT IT CHECKS
--------------
Extracts `.from('<table>').select(<cols>)` pairs from the app source,
resolves shared column constants (e.g. MLB_CTX_COLUMNS), then asks
PostgREST for each column individually and reports the ones that do not
exist. Exits non-zero if any select would 400, so it can gate CI or a
pre-build hook.

    python verify_app_select_columns.py
    python verify_app_select_columns.py --json
"""
from __future__ import annotations
import argparse, json, os, re, sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8')
_ROOT = Path(__file__).resolve().parent.parent
for _line in (Path(__file__).parent / '.env').read_text().split('\n'):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

SOURCES = ['app/index.tsx', 'app/track-record.tsx']

# Selects we cannot statically resolve (built at runtime from variables).
# Listed so the report says "skipped" rather than silently ignoring them.
_UNRESOLVABLE = '<dynamic>'


def _string_literal_parts(expr: str) -> str | None:
    """Join a concatenation of quoted string literals into one value.

    Returns None when the expression contains anything that is not a
    literal or a `+` (i.e. a template literal or a variable), because
    then we cannot know the real column list.
    """
    stripped = re.sub(r"'[^']*'|\"[^\"]*\"", '', expr)
    if stripped.strip(" \t\n+"):
        return None
    return ''.join(re.findall(r"'([^']*)'|\"([^\"]*)\"", expr) and
                   [a or b for a, b in re.findall(r"'([^']*)'|\"([^\"]*)\"", expr)])


def _const_map(src: str) -> dict:
    """Resolve `const NAME = 'a,b,' + 'c'` column constants."""
    out = {}
    for m in re.finditer(r"const\s+([A-Z][A-Z0-9_]*)\s*=\s*((?:\s*(?:'[^']*'|\"[^\"]*\")\s*\+?)+);",
                         src):
        val = _string_literal_parts(m.group(2))
        if val and ',' in val:
            out[m.group(1)] = val
    return out


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", '', src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", '', src)


def extract(path: Path) -> list[tuple[str, str, int]]:
    """Return [(table, columns_or_marker, line_no)]."""
    raw = path.read_text(encoding='utf-8')
    src = _strip_comments(raw)
    consts = _const_map(src)
    found = []
    for m in re.finditer(r"\.from\(\s*'([a-z0-9_]+)'\s*\)", src):
        table = m.group(1)
        window = src[m.end(): m.end() + 2500]
        # Bind the select to THIS from() only. Without this the search ran
        # past the end of the chain into a later query and reported its
        # columns against the wrong table (false-positived mlb_pitcher_stats
        # as prop_jerry_cache on the first run).
        nxt = window.find('.from(')
        if nxt != -1:
            window = window[:nxt]
        sm = re.search(r"\.select\(\s*(.*?)\s*\)\s*(?:\.|;|,|\n)", window, re.S)
        if not sm:
            continue
        expr = sm.group(1).strip()
        line = src[: m.start()].count('\n') + 1
        if expr in consts:
            found.append((table, consts[expr], line)); continue
        if expr.startswith('*') or expr in ("'*'", '"*"'):
            continue
        val = _string_literal_parts(expr)
        found.append((table, val if val else _UNRESOLVABLE, line))
    return found


_COL_CACHE: dict[str, set] = {}


def table_columns(table: str) -> set | None:
    """Live column set, or None if the table is unreachable."""
    if table not in _COL_CACHE:
        r = requests.get(f'{SB}/rest/v1/{table}?select=*&limit=1',
                         headers=H, timeout=20)
        if r.status_code != 200:
            _COL_CACHE[table] = None
        else:
            rows = r.json()
            # An empty table cannot tell us its columns via this route.
            _COL_CACHE[table] = set(rows[0].keys()) if rows else None
    return _COL_CACHE[table]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    problems, skipped, checked = [], [], 0
    for rel in SOURCES:
        p = _ROOT / rel
        if not p.exists():
            continue
        for table, cols, line in extract(p):
            if cols == _UNRESOLVABLE:
                skipped.append((rel, line, table, 'dynamic select'))
                continue
            live = table_columns(table)
            if live is None:
                skipped.append((rel, line, table, 'table empty or unreadable'))
                continue
            wanted = [c.strip() for c in cols.split(',') if c.strip()]
            # embedded resources like `foo(bar)` are not plain columns
            bad = [c for c in wanted
                   if '(' not in c and ':' not in c and c not in live]
            checked += 1
            if bad:
                problems.append({'file': rel, 'line': line, 'table': table,
                                 'missing': bad})

    if args.json:
        print(json.dumps({'problems': problems, 'skipped': skipped}, indent=2))
    else:
        print(f'=== app SELECT column audit ===')
        print(f'{checked} resolvable selects checked across {len(SOURCES)} files\n')
        if problems:
            for p in problems:
                print(f"  BROKEN  {p['file']}:{p['line']}  ({p['table']})")
                for c in p['missing']:
                    print(f"            -> '{c}' does not exist on {p['table']}")
                print('          PostgREST returns 400 for the WHOLE select; '
                      'every consumer of this fetch renders empty.')
        else:
            print('  All resolvable selects reference columns that exist.')
        if skipped:
            print(f'\n  {len(skipped)} not statically checkable:')
            for rel, line, table, why in skipped[:15]:
                print(f'    {rel}:{line}  {table}  ({why})')
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
