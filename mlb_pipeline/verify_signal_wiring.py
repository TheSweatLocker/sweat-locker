"""Signal wiring verifier (2026-08-22).

Preventive health check for the class of silent bugs documented in
[[project_pipeline_audit_822]]: signal_sources rows reference `ctx.X`
column names that the game_context writer never actually populates.
When that happens, `_CtxProxy.__getattr__` returns None on miss and the
signal evaluates to a truthiness fail — no error, no log, no fire. The
silent-bug audit (agent 8/22) found 19 signals broken this way.

This script:
  1. Loads every enabled signal_sources row per sport
  2. Extracts every `ctx.X` reference from condition_expr / side_expr /
     strength_expr via regex
  3. Fetches a recent {sport}_game_context row and checks which ctx.X
     fields actually exist
  4. Reports every reference that will always resolve to None

Non-fatal: this is a diagnostic. Print output + optional exit code.

Usage:
  python verify_signal_wiring.py                    # every sport
  python verify_signal_wiring.py --sport MLB        # single sport
  python verify_signal_wiring.py --exit-nonzero-on-issues   # for CI
"""
from __future__ import annotations
import argparse, os, re, sys
from collections import defaultdict
from pathlib import Path

import requests
from dotenv import load_dotenv

# Force UTF-8 stdout so ✗/⚠ chars work on Windows cp1252 terminals
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv()
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

# Sport → its game_context table name
CTX_TABLES = {
    'MLB':   'mlb_game_context',
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NBA':   'nba_game_context',
    'NHL':   'nhl_game_context',
    'NCAAB': 'ncaab_game_context',
}

# Regex captures `ctx.foo_bar` — Python identifier chars only
CTX_REF_RE = re.compile(r'\bctx\.([A-Za-z_][A-Za-z0-9_]*)')


def load_signals(sport: str) -> list[dict]:
    r = requests.get(f'{SB}/rest/v1/signal_sources', headers=H_READ,
                     params={'sport': f'eq.{sport}', 'enabled': 'eq.true',
                             'select': 'signal_key,condition_expr,side_expr,strength_expr,subject_scope,class'},
                     timeout=15)
    return r.json() if r.status_code == 200 else []


def load_ctx_columns(sport: str) -> set:
    """Return the set of columns present on a recent game_context row."""
    tbl = CTX_TABLES.get(sport)
    if not tbl:
        return set()
    r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_READ,
                     params={'select': '*', 'order': 'updated_at.desc.nullslast', 'limit': '1'},
                     timeout=15)
    rows = r.json() if r.status_code == 200 else []
    if not rows:
        return set()
    return set(rows[0].keys())


def extract_ctx_refs(row: dict) -> set:
    refs = set()
    for field in ('condition_expr', 'side_expr', 'strength_expr'):
        text = row.get(field) or ''
        for match in CTX_REF_RE.finditer(text):
            refs.add(match.group(1))
    return refs


def check_sport(sport: str) -> tuple[int, int, list]:
    """Return (dead_signal_count, total_signal_count, findings)."""
    signals = load_signals(sport)
    ctx_cols = load_ctx_columns(sport)
    if not ctx_cols:
        print(f'  ⚠ {sport}: no ctx row available to compare (off-season empty table?)')
        return (0, len(signals), [])

    dead = 0
    findings = []
    # Group by which missing column so we can report cleanly
    by_missing = defaultdict(list)
    for sig in signals:
        refs = extract_ctx_refs(sig)
        missing = refs - ctx_cols
        if missing:
            dead += 1
            for m in missing:
                by_missing[m].append(sig['signal_key'])

    if by_missing:
        for col, sig_keys in sorted(by_missing.items(), key=lambda kv: -len(kv[1])):
            note = f'  ✗ ctx.{col} referenced by {len(sig_keys)} signal(s) but NOT populated on {CTX_TABLES[sport]}'
            print(note)
            for sk in sorted(set(sig_keys))[:5]:
                print(f'      · {sk}')
            if len(set(sig_keys)) > 5:
                print(f'      · ...and {len(set(sig_keys)) - 5} more')
            findings.append({'sport': sport, 'column': col, 'signals': sorted(set(sig_keys))})
    return (dead, len(signals), findings)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', help='Single sport to check (else: all)')
    ap.add_argument('--exit-nonzero-on-issues', action='store_true',
                    help='exit code 1 if any dead signal reference found (for CI)')
    args = ap.parse_args()

    sports = [args.sport] if args.sport else list(CTX_TABLES.keys())
    total_dead = 0
    total_all = 0
    all_findings = []
    for sport in sports:
        print(f'\n=== {sport} ===')
        dead, all_n, findings = check_sport(sport)
        total_dead += dead
        total_all += all_n
        all_findings.extend(findings)
        print(f'  → {dead}/{all_n} enabled signals reference missing ctx columns')

    print(f'\n{"=" * 50}')
    print(f'TOTAL: {total_dead}/{total_all} enabled signals across all sports have dead ctx references')
    if all_findings:
        print(f'unique columns silently missing: {len(all_findings)}')

    if args.exit_nonzero_on_issues and total_dead > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
