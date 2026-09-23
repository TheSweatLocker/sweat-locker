"""preflight — catch the bugs that py_compile cannot, before they ship.

2026-09-23.

WHY THIS EXISTS. On 09-23 the MLB pipeline died twice on:

    UnboundLocalError: cannot access local variable '_cohort'

I had added `from cohort_evidence import cohort as _cohort` at module
level in play_of_day.py while two lines inside score_mlb_game already
used `_cohort` as a local string. Python makes a name local for the
WHOLE function if it is assigned anywhere in it, so every call to that
import became a reference to an unbound local.

`python -m py_compile` passes on this cleanly. It is a runtime binding
error, and compiling proved nothing — which is exactly the false
confidence that let it reach production. play_of_day is step 98 of 172
in a serial workflow, so it took 74 unrelated downstream steps with it:
jerry reads, prop scoring, the Sharp Card, the Sweat Card.

This is NOT a runtime guard on the pipeline. Andy's standing objection
to guards-on-guards is about adding steps whose job is to clean up
after earlier steps. This runs BEFORE a push, against source, and
catches a defect class that has now cost a production morning. Nothing
in the pipeline depends on it at run time.

CHECKS

  shadowed-import   a function assigns to a name bound by a
                    module-level import. Either the import is dead or
                    every earlier use of it in that function raises.
                    This is the 09-23 bug.

  import-failure    a module cannot be imported at all (syntax is fine
                    but a top-level statement raises). Entry points
                    only — importing all 432 would run real work.

Deliberately narrow. A check that fires on things that are usually fine
gets ignored, and an ignored check is worse than none.

CLI
    python preflight.py                    # whole package
    python preflight.py play_of_day.py     # just what changed
    python preflight.py --changed          # files git says are modified
"""
from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

HERE = Path(__file__).parent


def _module_imports(tree: ast.Module) -> set[str]:
    """Names bound by imports at module level only."""
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                out.add(a.asname or a.name.split('.')[0])
    return out


def _assigned_names(fn: ast.AST) -> set[str]:
    """Every name this function binds by assignment, for/with, or except."""
    out: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                for sub in ast.walk(t):
                    if isinstance(sub, ast.Name):
                        out.add(sub.id)
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
            if isinstance(n.target, ast.Name):
                out.add(n.target.id)
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            for sub in ast.walk(n.target):
                if isinstance(sub, ast.Name):
                    out.add(sub.id)
        elif isinstance(n, ast.withitem) and n.optional_vars is not None:
            for sub in ast.walk(n.optional_vars):
                if isinstance(sub, ast.Name):
                    out.add(sub.id)
    return out


def _local_imports(fn: ast.AST) -> set[str]:
    """A function-level `import x` is a deliberate rebind, not a shadow."""
    out: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                out.add(a.asname or a.name.split('.')[0])
    return out


def check_shadowed_imports(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))
    except SyntaxError as e:
        return [f'{path.name}:{e.lineno}: SYNTAX ERROR — {e.msg}']
    top = _module_imports(tree)
    if not top:
        return []
    problems = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        # `global x` means the assignment is intentional and module-scoped
        globals_declared = {nm for g in ast.walk(fn)
                            if isinstance(g, ast.Global) for nm in g.names}
        clash = (_assigned_names(fn) & top) - _local_imports(fn) - globals_declared
        for name in sorted(clash):
            problems.append(
                f'{path.name}:{fn.lineno}: {fn.name}() assigns to {name!r}, '
                f'which is a module-level import. Every use of {name!r} in '
                f'this function is a local — earlier uses raise '
                f'UnboundLocalError.')
    return problems


def changed_files() -> list[Path]:
    try:
        out = subprocess.run(
            ['git', 'diff', '--name-only', 'HEAD'],
            cwd=HERE.parent, capture_output=True, text=True, timeout=30).stdout
        staged = subprocess.run(
            ['git', 'diff', '--name-only', '--cached'],
            cwd=HERE.parent, capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return []
    names = {n.strip() for n in (out + staged).split('\n') if n.strip()}
    return [HERE.parent / n for n in sorted(names)
            if n.endswith('.py') and (HERE.parent / n).exists()]


def targets(args) -> list[Path]:
    if args.changed:
        return [p for p in changed_files() if p.suffix == '.py']
    if args.files:
        return [HERE / f if not os.path.isabs(f) else Path(f) for f in args.files]
    return sorted(p for p in HERE.glob('*.py')
                  if not p.name.startswith('_') and p.name != 'preflight.py')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='*')
    ap.add_argument('--changed', action='store_true',
                    help='only files git reports as modified')
    a = ap.parse_args()

    paths = [p for p in targets(a) if p.exists() and p.suffix == '.py']
    if not paths:
        print('preflight: nothing to check')
        return 0

    problems: list[str] = []
    for p in paths:
        problems += check_shadowed_imports(p)

    print(f'=== preflight · {len(paths)} file(s) ===')
    if not problems:
        print('  shadowed module imports: none')
        print('  OK')
        return 0
    print(f'  shadowed module imports: {len(problems)}\n')
    for msg in problems:
        print(f'  ✗ {msg}')
    print('\n  This is the 09-23 class: py_compile passes, the pipeline dies.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
