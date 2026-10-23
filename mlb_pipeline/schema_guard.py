#!/usr/bin/env python3
"""schema_guard — refuse to issue a request for a column that does not exist.

╔══ WHY THIS EXISTS ════════════════════════════════════════════════════════╗
2026-10-23: the app went down for paying subscribers. Root cause was a micro
Supabase instance taking ~214,000 requests/day, and a large share of those
requests COULD NEVER HAVE SUCCEEDED:

    nfl_player_stats.anytime_td        column does not exist  (hundreds/run)
    nfl_game_context.home_qb/away_qb   column does not exist
    ncaaf_pipeline_props               TABLE does not exist   (8 scripts)
    mlb_game_context.kickoff_utc       NFL-only column
    nhl_game_context.close_locked_at   MLB/NFL-only column
    close_spread_home_price            no price cols on ANY results table

Not one of them was visible, because every call site swallowed the failure
with the same line:

    if r.status_code != 200: return []

A 400 became an empty list, the caller carried on with no data, and the step
exited GREEN. That is the fifth occurrence of
feedback_green_step_that_writes_nothing, and the reason ATD conviction came
out inverted: it had been computing from an empty list the whole time.

So this module does two things, and the second matters more than the first:

  1. VALIDATE a select list against the live schema before spending a request.
  2. BE LOUD. A dead column is a bug in our code, not a quiet day.
╚═══════════════════════════════════════════════════════════════════════════╝

Library use — drop-in replacement for the swallow pattern:

    from schema_guard import sg_get
    rows = sg_get('nfl_player_stats',
                  {'player_id': 'eq.X', 'select': 'season,week,rushing_tds'})
    # returns [] on failure, but PRINTS status + URL, and refuses to send the
    # request at all if a column is unknown.

Repo audit — finds every dead query without running any pipeline:

    python schema_guard.py --audit

This is the cheap version of the guard: one OpenAPI request describes every
table, so validating costs nothing per call after the first.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _line in _env.read_text(encoding='utf-8', errors='replace').split('\n'):
        if '=' in _line and not _line.strip().startswith('#'):
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
_H = {'apikey': KEY or '', 'Authorization': f'Bearer {KEY or ""}'}

#: table -> frozenset(columns). Populated once from the OpenAPI spec.
_SCHEMA: dict = {}
#: tables whose select we refuse to validate (RPC, views we cannot introspect)
_SKIP_PREFIX = ('rpc/',)
#: PostgREST aggregates / pseudo-columns — valid in a select, not real columns
_PSEUDO_COLS = frozenset({'count', 'sum', 'avg', 'min', 'max'})


def schema(refresh: bool = False) -> dict:
    """{table: frozenset(cols)} for the whole exposed schema. ONE request."""
    global _SCHEMA
    if _SCHEMA and not refresh:
        return _SCHEMA
    if not SB or not KEY:
        print('  schema_guard: SUPABASE env missing — validation disabled')
        return {}
    req = urllib.request.Request(
        f'{SB}/rest/v1/',
        headers={**_H, 'Accept': 'application/openapi+json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            spec = json.loads(r.read().decode('utf-8'))
    except Exception as e:                                   # noqa: BLE001
        print(f'  schema_guard: could not read schema ({type(e).__name__}) '
              f'— validation disabled this run')
        return {}
    out = {}
    for name, defn in (spec.get('definitions') or {}).items():
        props = (defn or {}).get('properties') or {}
        if props:
            out[name] = frozenset(props)
    _SCHEMA = out
    return _SCHEMA


def missing(table: str, select: str) -> list:
    """Columns named in `select` that the table does not have.

    Returns [] when the table is unknown to us (a view we cannot introspect,
    an RPC) — this guard never blocks on its own ignorance, only on a
    positive contradiction.
    """
    sch = schema()
    cols = sch.get(str(table))
    if not cols or not select:
        return []
    bad = []
    for raw in str(select).split(','):
        c = raw.strip()
        if not c or c == '*':
            continue
        # PostgREST aggregates / pseudo-columns are not table columns.
        # `select=count` is the legacy row-count form and is legitimate.
        if c in _PSEUDO_COLS:
            continue
        # embedded resource (fk!inner(...)), aliases, casts, json paths
        if any(ch in c for ch in '()!:->'):
            continue
        c = c.split('.', 1)[0]
        if c and c not in cols:
            bad.append(c)
    return bad


def table_exists(table: str) -> bool | None:
    """True / False, or None when the schema could not be read."""
    sch = schema()
    if not sch:
        return None
    return str(table) in sch


def sg_get(table: str, params: dict | None = None, timeout: int = 30,
           label: str = '') -> list:
    """GET rows, validating first and NEVER failing silently.

    Returns [] on any failure, exactly like the pattern it replaces — but it
    says why, every time.
    """
    params = dict(params or {})
    sel = params.get('select')
    tag = label or table

    if table_exists(table) is False:
        print(f'  *** schema_guard: TABLE {table!r} DOES NOT EXIST — '
              f'request not sent ({tag}) ***')
        return []
    bad = missing(table, sel) if sel else []
    if bad:
        print(f'  *** schema_guard: {table} has no column(s) '
              f'{", ".join(bad)} — request not sent ({tag}) ***')
        return []

    url = f'{SB}/rest/v1/{table}'
    if params:
        url += '?' + urllib.parse.urlencode(params, safe='.,()*:')
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=_H), timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8')) or []
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read().decode('utf-8')[:200]
        except Exception:
            pass
        # LOUD. This is the whole point of the module.
        print(f'  ⚠ {tag}: HTTP {e.code} on {table} — {body}')
        return []
    except Exception as e:                                   # noqa: BLE001
        print(f'  ⚠ {tag}: {type(e).__name__} on {table}')
        return []


# ════════════════════════════════════════════════════════════════════════════
# REPO AUDIT
# ════════════════════════════════════════════════════════════════════════════

# f'{SB}/rest/v1/<table>?...select=a,b,c...'  — URL form
_URL_RE = re.compile(
    r'rest/v1/([a-z0-9_]+)[^\'"\n]*?[?&]select=([A-Za-z0-9_,.*]+)')
# params={'select': 'a,b,c'} near a rest/v1/<table> — params form
_TBL_RE = re.compile(r'rest/v1/([a-z0-9_]+)')
_SEL_RE = re.compile(r'[\'"]select[\'"]\s*:\s*[\'"]([A-Za-z0-9_,.*]+)[\'"]')


def audit(paths: list) -> int:
    """Scan source for select lists and report columns that do not exist."""
    sch = schema()
    if not sch:
        print('  cannot audit without a schema')
        return 1
    print(f'  schema: {len(sch)} tables')
    findings, scanned = [], 0
    for p in paths:
        try:
            src = Path(p).read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        scanned += 1
        lines = src.split('\n')

        # URL form — table and select in the same expression
        for i, line in enumerate(lines, 1):
            for tbl, sel in _URL_RE.findall(line):
                for c in missing(tbl, sel):
                    findings.append((p, i, tbl, c))
                if table_exists(tbl) is False:
                    findings.append((p, i, tbl, '<TABLE MISSING>'))

        # params form — nearest preceding rest/v1/<table> within 12 lines
        for i, line in enumerate(lines, 1):
            m = _SEL_RE.search(line)
            if not m:
                continue
            sel = m.group(1)
            tbl = None
            for j in range(i - 1, max(0, i - 13), -1):
                t = _TBL_RE.search(lines[j - 1])
                if t:
                    tbl = t.group(1)
                    break
            if not tbl:
                continue
            for c in missing(tbl, sel):
                findings.append((p, i, tbl, c))

    seen, uniq = set(), []
    for f in findings:
        k = (f[0], f[2], f[3])
        if k not in seen:
            seen.add(k)
            uniq.append(f)

    print(f'  scanned {scanned} file(s)\n')
    if not uniq:
        print('  ✓ no dead columns or tables found')
        return 0
    print(f'  *** {len(uniq)} DEAD REFERENCE(S) — each is a guaranteed-fail '
          f'request ***\n')
    by_col: dict = {}
    for path, ln, tbl, col in uniq:
        by_col.setdefault((tbl, col), []).append(f'{Path(path).name}:{ln}')
    for (tbl, col), where in sorted(by_col.items(),
                                    key=lambda kv: -len(kv[1])):
        sch_cols = schema().get(tbl)
        hint = ''
        if col != '<TABLE MISSING>' and sch_cols:
            near = [c for c in sch_cols
                    if col.split('_')[0] and col.split('_')[0] in c][:3]
            if near:
                hint = f'   did you mean: {", ".join(sorted(near))}'
        print(f'  {tbl}.{col}')
        print(f'    {len(where)} site(s): {", ".join(where[:6])}'
              + (' …' if len(where) > 6 else ''))
        if hint:
            print(f'  {hint}')
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--audit', action='store_true',
                    help='scan source for dead columns/tables')
    ap.add_argument('--glob', default='*.py',
                    help='files to scan (default *.py in this dir)')
    args = ap.parse_args()
    if not args.audit:
        sch = schema()
        print(f'schema_guard: {len(sch)} tables visible')
        return 0
    here = Path(__file__).parent
    paths = [str(p) for p in sorted(here.glob(args.glob))
             if not p.name.startswith('_') and p.name != 'schema_guard.py']
    print(f'=== schema_guard --audit · {len(paths)} file(s) ===')
    return audit(paths)


if __name__ == '__main__':
    raise SystemExit(main())
