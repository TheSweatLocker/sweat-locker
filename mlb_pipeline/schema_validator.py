"""Pre-flight schema validator.

Source of truth: `supabase/migrations/*.sql`. Parses every migration file and
extracts the canonical set of columns that SHOULD exist per table. Compares
against the live Supabase schema (via PostgREST's OpenAPI introspection) and
returns the set of missing columns per table.

Returns:
    {table_name: [missing_column_1, ...], ...} — empty dict when everything aligns.

Used by:
    check_pipeline_health.py — fails the workflow loudly when a migration
    is sitting in the repo but hasn't landed in the live DB. Prevents the
    pattern where the pipeline writes into stripped columns for days
    before anyone notices.

Why not rely on the strip-and-warn fallback in game_context.py: because
warnings get ignored. Pre-flight failure is loud, blocks the run, and
points at the exact column that needs to land.
"""
import json
import os
import re
import urllib.request
from pathlib import Path


# ALTER TABLE statements span multiple lines; capture the whole block then
# pull `ADD COLUMN IF NOT EXISTS <name>` from it. Handles the conventional
# Supabase migration shape: ALTER TABLE x ADD COLUMN IF NOT EXISTS a TYPE,
# ADD COLUMN IF NOT EXISTS b TYPE;
_ALTER_BLOCK = re.compile(
    r'ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?([a-z_][a-z0-9_]*)\s+(.+?);',
    re.IGNORECASE | re.DOTALL,
)
_ADD_COLUMN = re.compile(
    r'ADD\s+COLUMN(?:\s+IF\s+NOT\s+EXISTS)?\s+([a-z_][a-z0-9_]*)',
    re.IGNORECASE,
)
# CREATE TABLE statements declare the initial column set
_CREATE_BLOCK = re.compile(
    r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)\s*\((.+?)\);',
    re.IGNORECASE | re.DOTALL,
)
# Column declarations inside CREATE TABLE: `col_name TYPE [...],` at line start
_CREATE_COLUMN = re.compile(
    r'^\s*([a-z_][a-z0-9_]*)\s+(?:[A-Z]+)',
    re.IGNORECASE | re.MULTILINE,
)
# Reserved tokens that look like column names but aren't (constraints, etc.)
_RESERVED = {
    'primary', 'unique', 'check', 'foreign', 'constraint', 'index',
    'references', 'like', 'inherits',
}


def parse_migrations(migrations_dir):
    """Walk every .sql migration and build {table: set(expected_columns)}.

    Drops are not modeled — if a future migration DROPs a column we previously
    added, the expected set would falsely include it. Acceptable for now since
    the codebase rarely drops columns; revisit if it becomes a source of false
    positives.
    """
    expected = {}
    migrations_dir = Path(migrations_dir)
    if not migrations_dir.exists():
        return expected

    for sql_file in sorted(migrations_dir.glob('*.sql')):
        text = sql_file.read_text(encoding='utf-8')
        # Strip line comments to reduce regex noise
        text = re.sub(r'--[^\n]*', '', text)

        # CREATE TABLE — initial column set
        for create_match in _CREATE_BLOCK.finditer(text):
            table = create_match.group(1).lower()
            body = create_match.group(2)
            cols = expected.setdefault(table, set())
            for col_match in _CREATE_COLUMN.finditer(body):
                col = col_match.group(1).lower()
                if col in _RESERVED:
                    continue
                cols.add(col)

        # ALTER TABLE ... ADD COLUMN — additive changes
        for alter_match in _ALTER_BLOCK.finditer(text):
            table = alter_match.group(1).lower()
            body = alter_match.group(2)
            cols = expected.setdefault(table, set())
            for col_match in _ADD_COLUMN.finditer(body):
                cols.add(col_match.group(1).lower())

    return expected


def _probe_columns(supabase_url, supabase_key, table, columns):
    """Verify a set of columns exists on `table` via PostgREST.

    Strategy: attempt a single bulk SELECT listing every expected column. If
    PostgREST returns 200 the entire set is present. On 400, fall back to
    probing each column individually so we can name the specific missing
    ones in the failure message.

    Returns sorted list of missing column names (empty when everything OK).
    PostgREST's anon-key SELECT permission is required; the schema validator
    is read-only and matches what the rest of the pipeline already has.
    """
    headers = {'apikey': supabase_key, 'Authorization': f'Bearer {supabase_key}'}

    def _select_ok(cols):
        select_clause = ','.join(sorted(cols))
        url = f"{supabase_url}/rest/v1/{table}?select={select_clause}&limit=0"
        req = urllib.request.Request(url, headers=headers)
        try:
            urllib.request.urlopen(req, timeout=15).read()
            return True, None
        except urllib.error.HTTPError as e:
            body = (e.read() or b'').decode('utf-8', errors='replace')
            return False, (e.code, body)
        except Exception as e:
            return False, (None, str(e))

    ok, err = _select_ok(columns)
    if ok:
        return []
    code, body = err if err else (None, '')
    if code == 404 or '"42P01"' in (body or '') or 'relation' in (body or '').lower() and 'does not exist' in (body or '').lower():
        return ['<entire table missing>']

    # Bulk select failed — probe one-by-one to identify the missing column(s)
    missing = []
    for col in sorted(columns):
        ok, err = _select_ok([col])
        if not ok:
            missing.append(col)
    return missing


def fetch_actual_schema(supabase_url, supabase_key, expected):
    """Returns {table: missing_columns_list} by probing each declared column
    against the live PostgREST surface. Anon-key compatible; no OpenAPI /
    service-role access needed.

    Signature differs from a pure-introspection fetch because PostgREST's
    OpenAPI is service-role only on Supabase. This probe approach gives the
    same coverage for an additive-only validator.
    """
    drift = {}
    for table, cols in expected.items():
        if not cols:
            continue
        missing = _probe_columns(supabase_url, supabase_key, table, cols)
        if missing:
            drift[table] = missing
    return drift


def validate(migrations_dir, supabase_url, supabase_key, *, tables_to_check=None):
    """Returns {table: sorted([missing_columns])} for any drift.

    tables_to_check: optional whitelist. When None, validates every table the
    migrations folder declares. Passing a whitelist scopes the check (useful
    when some tables are managed outside migrations).
    """
    expected = parse_migrations(migrations_dir)
    if tables_to_check is not None:
        expected = {t: cols for t, cols in expected.items() if t in tables_to_check}
    return fetch_actual_schema(supabase_url, supabase_key, expected)


if __name__ == '__main__':
    import sys, io
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    from dotenv import load_dotenv
    load_dotenv()
    repo_root = Path(__file__).resolve().parent.parent
    drift = validate(
        repo_root / 'supabase' / 'migrations',
        os.environ['SUPABASE_URL'],
        os.environ['SUPABASE_KEY'],
    )
    if not drift:
        print('✅ Schema in sync with migrations.')
        sys.exit(0)
    print('❌ SCHEMA DRIFT DETECTED — migrations not applied:')
    for table, cols in sorted(drift.items()):
        print(f'  {table}:')
        for c in cols:
            print(f'    - {c}')
    sys.exit(1)
