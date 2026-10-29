"""Execute a migration against the LIVE schema, then ROLLBACK. Prove it runs.

== 2026-10-29 - WHY THIS EXISTS ==
ANDY, after 20261029a came back 42P10 on its first paste:
  "if you give me broken migrations how can i trust you are doing the right
   thing in building an engine that actually works"

Fair. That migration's EXPRESSIONS were replayed through signal_expr against
live ctx rows before shipping; its DDL was only read. `ON CONFLICT
(signal_key)` names a key signal_sources does not have, and reading cannot
catch that - only the planner can, because the answer lives in pg_constraint,
not in the file. This is the same class as
feedback_swallowed_nameerror_is_a_false_condition: a check that passes on
inspection and fails on execution.

So: no migration is handed over again without running it here first.

HOW IT IS SAFE. The file's own BEGIN/COMMIT are stripped and the whole body
runs inside ONE transaction this script opens and then unconditionally
ROLLS BACK, in a finally. Nothing is ever committed. A migration that would
have failed fails HERE, with the real SQLSTATE, against the real schema -
including every constraint, trigger, type and existing row the file depends
on.

WHAT IT DOES NOT PROVE: that the migration is CORRECT, only that it RUNS.
Correctness still needs the measurement. A migration can execute perfectly
and seed a signal that fires on the wrong side.

NOTIFY is stripped too - it is a no-op inside an aborted transaction and its
absence changes nothing about whether the DDL parses.

TWO MODES, AND THE WEAKER ONE SAYS SO. Full validation needs a DB
password, which is NOT stored in this repo (not in .env, not in GH secrets -
only the pooler host is, in supabase/.temp/pooler-url). Without
SUPABASE_DB_URL the script falls back to --parse-only: a real Postgres
parser (sqlglot) checks every statement's SYNTAX, which catches typos,
unbalanced parens and bad clause order, and CANNOT catch anything that
depends on the catalog - a missing column, a wrong type, or exactly the
42P10 that started this. Parse-only prints that limitation on every run
instead of implying a clean bill of health.

USAGE
    python validate_migration.py ../supabase/migrations/20261029a_foo.sql
    python validate_migration.py <file> --parse-only
    python validate_migration.py <file> --show-statements
"""
from __future__ import annotations
import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote

import pg8000.dbapi

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

#: written by `supabase link`; untracked, and never printed by this script.
POOLER = Path(__file__).resolve().parent.parent / 'supabase' / '.temp' / 'pooler-url'


def _dsn() -> str:
    dsn = os.environ.get('SUPABASE_DB_URL') or ''
    if not dsn and POOLER.is_file():
        dsn = POOLER.read_text(encoding='utf-8').strip()
    if not dsn:
        raise SystemExit(f'no DSN: set SUPABASE_DB_URL or create {POOLER}')
    return dsn


def split_statements(sql: str) -> list:
    """Split on top-level semicolons, respecting quoting.

    Handles '' escapes inside single quotes, "" inside quoted identifiers,
    $tag$ dollar-quoting (DO blocks), and both comment styles. A naive
    sql.split(';') corrupts any migration with a semicolon inside a string
    or a DO block, which is most of the ones in this repo.
    """
    out, buf, i, n = [], [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c == '-' and sql[i:i + 2] == '--':
            j = sql.find('\n', i)
            j = n if j < 0 else j + 1
            buf.append(sql[i:j]); i = j; continue
        if c == '/' and sql[i:i + 2] == '/*':
            j = sql.find('*/', i)
            j = n if j < 0 else j + 2
            buf.append(sql[i:j]); i = j; continue
        if c == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if sql[j:j + 2] == "''":
                        j += 2; continue
                    j += 1; break
                j += 1
            buf.append(sql[i:j]); i = j; continue
        if c == '"':
            j = i + 1
            while j < n:
                if sql[j] == '"':
                    if sql[j:j + 2] == '""':
                        j += 2; continue
                    j += 1; break
                j += 1
            buf.append(sql[i:j]); i = j; continue
        if c == '$':
            m = re.match(r'\$[A-Za-z_]*\$', sql[i:])
            if m:
                tag = m.group(0)
                j = sql.find(tag, i + len(tag))
                j = n if j < 0 else j + len(tag)
                buf.append(sql[i:j]); i = j; continue
        if c == ';':
            stmt = ''.join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []; i += 1; continue
        buf.append(c); i += 1
    tail = ''.join(buf).strip()
    if tail:
        out.append(tail)
    return out


def merge_adjacent_literals(sql: str) -> str:
    """Join Postgres adjacent string literals into one, for the parser only.

    Postgres (and the SQL standard) lexes

        'first part '
        'second part'

    as ONE literal when the parts are separated by whitespace containing a
    newline. Every signal in this repo is written that way, because the
    expressions are too long for one line. sqlglot does not implement it and
    reports the statement as malformed, so without this the validator would
    cry wolf on correct SQL - and a check that is wrong about the
    INTENTIONAL case is a check that gets ignored
    (feedback_a_detector_must_know_the_intentional_case).

    The merge is for the syntax check ONLY. The DB is always sent the
    original text.
    """
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c != "'":
            # step over comments so a quote inside one is not treated as code
            if c == '-' and sql[i:i + 2] == '--':
                j = sql.find('\n', i)
                j = n if j < 0 else j + 1
                out.append(sql[i:j]); i = j; continue
            if c == '/' and sql[i:i + 2] == '/*':
                j = sql.find('*/', i)
                j = n if j < 0 else j + 2
                out.append(sql[i:j]); i = j; continue
            out.append(c); i += 1; continue
        # a literal: collect its body, then look for continuations
        body, i = _lit_body(sql, i)
        while True:
            j = i
            while j < n and sql[j] in ' \t\r\n':
                j += 1
            # a -- comment may sit between the two parts
            if sql[j:j + 2] == '--':
                k = sql.find('\n', j)
                if k < 0:
                    break
                j = k + 1
                while j < n and sql[j] in ' \t\r\n':
                    j += 1
            # Postgres concatenates two literals only when the whitespace
            # between them contains a NEWLINE. Same-line 'a' 'b' is a syntax
            # error, so merging it would hide a real defect.
            if j < n and sql[j] == "'" and '\n' in sql[i:j]:
                more, i = _lit_body(sql, j)
                body += more
                continue
            break
        out.append("'" + body + "'")
    return ''.join(out)


def _lit_body(sql: str, i: int):
    """Read the literal starting at sql[i] == "'"; return (body, next_index).

    The body keeps '' doubled, so re-emitting it is lossless.
    """
    n = len(sql)
    j = i + 1
    body = []
    while j < n:
        if sql[j] == "'":
            if sql[j:j + 2] == "''":
                body.append("''"); j += 2; continue
            j += 1
            break
        body.append(sql[j]); j += 1
    return ''.join(body), j


def _is_noise(stmt: str):
    """Return a reason this statement is skipped, else None."""
    bare = re.sub(r'--[^\n]*', '', stmt)
    bare = re.sub(r'/\*.*?\*/', '', bare, flags=re.S).strip().rstrip(';').strip()
    if not bare:
        return 'comment only'
    low = ' '.join(bare.lower().split())
    if low in ('begin', 'commit', 'end', 'start transaction'):
        return "file's own transaction control (this script owns the txn)"
    if low.startswith('notify '):
        return 'NOTIFY (no-op in an aborted txn)'
    return None


def _err(e):
    """pg8000 wraps the server error as a dict in args[0]: C=sqlstate, M=msg."""
    a = getattr(e, 'args', None) or [None]
    if isinstance(a[0], dict):
        return a[0].get('C', '') or '', a[0].get('M', '') or str(e)
    return '', str(e)


def _parse_only(p: Path, stmts: list, show: bool) -> int:
    """Syntax-check every statement with a real Postgres parser.

    Weaker than executing: it reads the file, not the catalog, so it cannot
    see a missing column or an absent unique index. Stated on every run.
    """
    import sqlglot
    from sqlglot.errors import ParseError

    bar = '=' * 72
    print(f'{bar}\nPARSE-ONLY {p.name}\n  {len(stmts)} parsed statements . '
          f'sqlglot {sqlglot.__version__}, dialect=postgres\n{bar}')
    ok = skipped = 0
    bad = []
    for idx, stmt in enumerate(stmts, 1):
        why = _is_noise(stmt)
        head = ' '.join(re.sub(r'--[^\n]*', '', stmt).split())[:86]
        if why:
            skipped += 1
            print(f'  [{idx:2}] SKIP  {why}')
            continue
        try:
            sqlglot.parse_one(merge_adjacent_literals(stmt), dialect='postgres')
        except ParseError as e:
            print(f'  [{idx:2}] FAIL  {str(e).splitlines()[0][:200]}')
            print(f'        statement: {head}')
            bad.append(idx)
            continue
        ok += 1
        if show:
            print(f'  [{idx:2}] ok    {head}')
        else:
            print(f'  [{idx:2}] ok    {head[:70]}')
    print(f'\n  {ok} statements parse clean, {skipped} skipped, '
          f'{len(bad)} malformed.')
    print('  >> SYNTAX ONLY. This does NOT check the catalog, so it cannot')
    print('     catch a missing column, a wrong type, or a 42P10 conflict')
    print('     target. Set SUPABASE_DB_URL to validate by execution.')
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('--show-statements', action='store_true')
    ap.add_argument('--parse-only', action='store_true',
                    help='syntax check only; no DB connection (auto when '
                         'no DSN is available)')
    a = ap.parse_args()

    p = Path(a.path)
    if not p.is_file():
        raise SystemExit(f'not a file: {p}')
    stmts = split_statements(p.read_text(encoding='utf-8'))

    if a.parse_only:
        return _parse_only(p, stmts, a.show_statements)

    u = urlparse(_dsn())
    try:
        conn = pg8000.dbapi.connect(
            user=unquote(u.username or ''), password=unquote(u.password or ''),
            host=u.hostname, port=u.port or 5432,
            database=(u.path or '/postgres').lstrip('/') or 'postgres',
            ssl_context=True, timeout=30,
        )
    except Exception as e:                                # noqa: BLE001
        sqlstate, msg = _err(e)
        print(f'  ! cannot reach the DB ({sqlstate or "conn"}): {msg}')
        print('  ! falling back to PARSE-ONLY, which is strictly weaker.\n')
        return _parse_only(p, stmts, a.show_statements)
    conn.autocommit = False
    bar = '=' * 72
    print(f'{bar}\nVALIDATE {p.name}\n  {len(stmts)} parsed statements . '
          f'host {u.hostname}\n{bar}')
    ran = skipped = 0
    failed = None
    cur = conn.cursor()
    try:
        for idx, stmt in enumerate(stmts, 1):
            why = _is_noise(stmt)
            head = ' '.join(re.sub(r'--[^\n]*', '', stmt).split())[:86]
            if why:
                skipped += 1
                print(f'  [{idx:2}] SKIP  {why}')
                continue
            if a.show_statements:
                print(f'  [{idx:2}] ---- {head}')
            try:
                cur.execute(stmt)
            except Exception as e:                        # noqa: BLE001
                sqlstate, msg = _err(e)
                print(f'  [{idx:2}] FAIL  {sqlstate} {msg}')
                print(f'        statement: {head}')
                failed = (idx, sqlstate, msg)
                break
            ran += 1
            rc = cur.rowcount
            tail = f'   -> {rc} row(s)' if isinstance(rc, int) and rc >= 0 else ''
            print(f'  [{idx:2}] ok    {head}{tail}')
    finally:
        conn.rollback()
        conn.close()
        print('\n  ROLLED BACK - nothing was committed.')

    if failed:
        print(f'\n  >> WOULD FAIL at statement {failed[0]} ({failed[1]}). '
              'Do not hand it over.')
        return 1
    print(f'  {ran} statements executed clean, {skipped} skipped.')
    print('  >> RUNS against the live schema. (Runs is not the same as correct.)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
