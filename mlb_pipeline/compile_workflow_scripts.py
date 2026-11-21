"""compile_workflow_scripts — byte-compile what a workflow actually runs.

WHY THIS EXISTS, and why it is not `compileall` on the directory.

2026-11-20 I added this to daily_card.yml, to catch a 3.12-only f-string I
had shipped into generate_sweat_card.py:

    python -m compileall -q -x '_legacy|__pycache__' mlb_pipeline

The guard worked. It also took the entire product down for 21 hours.

grade_money_flow.py has carried a nested f-string illegal on 3.11 since
2026-10-20 (6f2d8aef). It is in NO workflow — nothing runs it, nothing
imports it — so the defect was inert for a month. The moment daily_card
began parsing every file in the directory, that one dormant line became a
hard dependency of the Sweat Card, POTD, the Sharp, the Dawg, the Ladder,
the Ledger, the receipts and the matview refresh. 13 consecutive runs died
at step 6 of 26 in 9 seconds, every later step skipped, and because the
end-heartbeat is `if: always()` the runs still posted start and end
([[feedback_an_always_end_heartbeat_hides_a_cancelled_run]]).

THE GUARD'S BLAST RADIUS EXCEEDED THE THING IT GUARDED. A gate should fail
on what the job cannot do without, and 969 files is not that: daily_card
runs 42 steps over ~30 scripts. The rest of the directory is one-off audits,
backfills and other sports' scripts, and an unparseable one of those is a
real bug worth reporting — but it is not a reason to withhold today's card.

So: BLOCKING on the scripts this workflow runs and everything they import,
ADVISORY on the remainder. Both sets are derived from the workflow file
itself, so adding a step to the yml extends the blocking set automatically
and the two cannot drift
([[feedback_a_detector_must_know_the_intentional_case]]).

CLI
  python compile_workflow_scripts.py --workflow .github/workflows/daily_card.yml
  python compile_workflow_scripts.py --workflow ... --advisory
  python compile_workflow_scripts.py --self-test
"""
from __future__ import annotations
import argparse
import ast
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                          # noqa: BLE001
    pass

#: `python foo.py`, `python -u foo.py`, `python foo.py --flag`. Also catches
#: the run_step.sh form, which puts the interpreter and script at the end of
#: a long line: `... run_step.sh --label "x" python foo.py --apply`.
_RUN = re.compile(r'python[0-9.]*\s+(?:-[A-Za-z]\s+)*([A-Za-z_][\w]*\.py)')


def scripts_in_workflow(text):
    """Script basenames a workflow's shell lines invoke, in file order."""
    seen, out = set(), []
    for name in _RUN.findall(text):
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def local_imports(path, available):
    """Module names imported by `path` that exist as files in `available`.

    Parsed, not grepped: a name inside a string or a comment is not an
    import, and `import x.y` is not the local module `x.y`.
    """
    try:
        tree = ast.parse(open(path, encoding='utf-8').read(), path)
    except SyntaxError:
        # The caller is about to report this file as a failure; it has no
        # readable import list, and returning () must not hide that.
        return ()
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.add(a.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` has no module; level>0 is a package-relative
            # import, which this flat directory does not use.
            if node.module and node.level == 0:
                found.add(node.module.split('.')[0])
    return tuple(sorted(m for m in found if m + '.py' in available))


def closure(entries, directory):
    """Entry scripts plus every local module reachable from them."""
    available = {f for f in os.listdir(directory) if f.endswith('.py')}
    out, queue = set(), [e for e in entries if e in available]
    missing = [e for e in entries if e not in available]
    while queue:
        name = queue.pop()
        if name in out:
            continue
        out.add(name)
        for mod in local_imports(os.path.join(directory, name), available):
            if mod + '.py' not in out:
                queue.append(mod + '.py')
    return sorted(out), sorted(missing), available


def compile_files(directory, names):
    """[(name, lineno, msg)] for each file that will not parse here."""
    bad = []
    for n in sorted(names):
        p = os.path.join(directory, n)
        try:
            compile(open(p, encoding='utf-8').read(), p, 'exec')
        except SyntaxError as e:
            bad.append((n, e.lineno, f'{e.msg}'))
        except Exception as e:                             # noqa: BLE001
            bad.append((n, None, f'{type(e).__name__}: {e}'))
    return bad


def main(workflow, directory, advisory):
    wf = os.path.basename(workflow)
    try:
        text = open(workflow, encoding='utf-8').read()
    except Exception as e:                                 # noqa: BLE001
        print(f'  !! cannot read {workflow}: {e}')
        # A guard that cannot read its own input is broken, not satisfied
        # ([[feedback_zero_rows_is_a_wiring_error]]).
        return 2

    entries = scripts_in_workflow(text)
    if not entries:
        print(f'  !! no `python <script>.py` lines found in {wf} — this '
              f'guard is reading the wrong file or the wrong format, which '
              f'is a wiring error, not a clean result')
        return 2

    needed, missing, available = closure(entries, directory)
    others = sorted(available - set(needed))

    print(f'== compile_workflow_scripts · {wf} · python '
          f'{sys.version_info[0]}.{sys.version_info[1]} ==')
    print(f'   steps invoke      : {len(entries)} script(s)')
    print(f'   + local imports   : {len(needed)} file(s) this job needs')
    print(f'   not in this job   : {len(others)} other file(s) in {directory}')
    if missing:
        print(f'   ⚠ invoked but absent from {directory}: '
              f'{", ".join(missing)}')

    bad = compile_files(directory, needed)
    if bad:
        print(f'\n   ❌ {len(bad)} file(s) this job RUNS will not parse on '
              f'python {sys.version_info[0]}.{sys.version_info[1]}:')
        for n, ln, msg in bad:
            print(f'      {n}:{ln}  {msg}')
        print('\n   A SyntaxError kills the step at import, in 0 seconds,'
              '\n   with the reason only in a step log. Failing here instead.')
        return 1
    print(f'\n   ✓ all {len(needed)} file(s) this job runs parse clean')

    if advisory:
        other_bad = compile_files(directory, others)
        if other_bad:
            print(f'\n   ⚠ ADVISORY — {len(other_bad)} file(s) this job does '
                  f'NOT run will not parse here. Real bugs, but no reason to'
                  f'\n     withhold this job\'s output:')
            for n, ln, msg in other_bad:
                print(f'      {n}:{ln}  {msg}')
        else:
            print(f'   ✓ the other {len(others)} file(s) parse clean too')
    return 0


def _self_test():
    import tempfile
    import textwrap
    cases = []

    # ── extraction ──
    WF = textwrap.dedent('''
        steps:
          - name: One
            run: |
              python jerry_anchor_potd.py --apply
          - name: Two
            run: |
              bash run_step.sh --label "Sweat Card" python generate_sweat_card.py
          - name: Repeat
            run: python jerry_anchor_potd.py
          - name: Flagged
            run: python -u snapshot_ledger.py
    ''')
    got = scripts_in_workflow(WF)
    cases.append(('every invoked script is found, deduped, in order',
                  got == ['jerry_anchor_potd.py', 'generate_sweat_card.py',
                          'snapshot_ledger.py']))
    cases.append(('a yml with no python lines yields nothing (so main can '
                  'call it a wiring error)', scripts_in_workflow('a: b') == []))

    d = tempfile.mkdtemp()
    w = lambda n, s: open(os.path.join(d, n), 'w', encoding='utf-8').write(s)
    w('entry.py', 'import helper\nimport requests\nfrom deep import thing\n')
    w('helper.py', 'x = 1\n')
    w('deep.py', 'import deeper\n')
    w('deeper.py', 'y = 2\n')
    w('unrelated.py', 'z = 3\n')
    w('broken_unrelated.py', 'def f(:\n')
    w('mentions.py', '# import helper\ns = "import helper"\n')

    needed, missing, avail = closure(['entry.py'], d)
    cases.append(('the closure follows local imports transitively',
                  needed == ['deep.py', 'deeper.py', 'entry.py', 'helper.py']))
    cases.append(('a third-party import is not treated as a local file',
                  'requests.py' not in needed))
    cases.append(('an unrelated file is NOT in the closure',
                  'unrelated.py' not in needed))
    cases.append(('an invoked script that does not exist is reported, not '
                  'silently dropped', closure(['ghost.py'], d)[1] == ['ghost.py']))
    cases.append(('a module named only in a comment or a string is not an '
                  'import', local_imports(os.path.join(d, 'mentions.py'),
                                          avail) == ()))

    # ── the actual regression: an unparseable file OUTSIDE the closure ──
    bad_needed = compile_files(d, needed)
    cases.append(('a clean closure compiles clean', bad_needed == []))
    bad_other = compile_files(d, ['broken_unrelated.py'])
    cases.append(('the unparseable file outside the closure IS detectable',
                  len(bad_other) == 1))
    cases.append(('...and does NOT appear in the blocking set',
                  'broken_unrelated.py' not in needed))

    # ── and one INSIDE the closure must block ──
    w('helper.py', 'def g(:\n')
    bad_needed2 = compile_files(d, needed)
    cases.append(('an unparseable file the job RUNS does block',
                  [n for n, _, _ in bad_needed2] == ['helper.py']))
    # A file that will not parse has no import list; returning () must not be
    # mistaken for "imports nothing, all clear".
    cases.append(('an unparseable file yields no imports but is still '
                  'reported by compile_files',
                  local_imports(os.path.join(d, 'helper.py'), avail) == ()
                  and len(bad_needed2) == 1))

    fails = 0
    for name, ok in cases:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            fails += 1
    print(f'\n  {len(cases) - fails}/{len(cases)} self-test cases passed')
    return 1 if fails else 0


if __name__ == '__main__':
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument('--workflow')
    ap.add_argument('--dir', default=here)
    ap.add_argument('--advisory', action='store_true',
                    help='also report files this job does NOT run, without '
                         'failing on them')
    ap.add_argument('--self-test', action='store_true')
    a = ap.parse_args()
    if a.self_test:
        raise SystemExit(_self_test())
    if not a.workflow:
        ap.error('--workflow is required')
    raise SystemExit(main(a.workflow, a.dir, a.advisory))
