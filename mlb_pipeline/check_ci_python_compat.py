#!/usr/bin/env python3
"""Catch syntax that MY interpreter accepts and the CI runner cannot parse.

WHY THIS EXISTS
---------------
2026-11-20. Andy: "ALl theGH woekflow look liek they are fialing" — and
"somehting has always been up wiht workflows i ahve brought it up 100s of
times".

Reading `workflow_heartbeat` for `event = 'step_failed'`, two steps had been
dying in 0 SECONDS on every `daily_card` run since the previous afternoon:

    generate_sweat_card.py      rc 1  duration_s 0
    refresh_yesterday_recap.py  rc 1  duration_s 0   (it imports the above)

    File ".../generate_sweat_card.py", line 5560
      f'{_why} run {_hit}% on n={_n},{_bar or " measured below "
      ^
    SyntaxError: unterminated string literal

The file compiled clean on my machine, was byte-identical to HEAD, and ran
to completion locally. The cause:

    local  Python 3.14.3
    CI     python-version: '3.11'

The offending line put a replacement field across TWO LINES with same-type
nested quotes. PEP 701 legalised that in **3.12**. On 3.11 it is a hard
SyntaxError AT IMPORT, so the step could never start — hence 0 seconds.

**`python -m py_compile` cannot catch this, and neither can
`ast.parse(..., feature_version=(3, 11))`** — I tested both; the 3.14
tokenizer accepts the construct whatever feature_version is passed. So a
local syntax check gives a false all-clear, which is exactly how this
shipped. The only reliable local signal is to look at the TOKENS.

WHAT IT CHECKS (the three things PEP 701 relaxed)
  1. a replacement field spanning more than one line, in a single- or
     double-quoted f-string
  2. a nested string inside a replacement field using the SAME quote
     character as the f-string itself
  3. a backslash anywhere inside a replacement field

All three are legal from 3.12 and fatal on 3.11.

MY INTERPRETER WILL ALWAYS BE NEWER THAN THE RUNNER'S, so this class of
defect is permanent and invisible unless something looks for it. Same shape
as feedback_hermes_date_reparse_is_v8_only: a construct valid in one runtime
and fatal in another, where the authoring environment is the forgiving one.

CLI
    python check_ci_python_compat.py                # scan mlb_pipeline/
    python check_ci_python_compat.py --path app     # scan elsewhere
    python check_ci_python_compat.py --self-test
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import tokenize

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                          # noqa: BLE001
    pass

#: The version CI pins. Read from the workflows so the two cannot drift.
CI_TARGET = (3, 11)


def ci_python_version(root='.'):
    """The python-version the workflows pin, so this check tracks it."""
    import re
    found = set()
    wf = os.path.join(root, '.github', 'workflows')
    if os.path.isdir(wf):
        for f in os.listdir(wf):
            if not f.endswith(('.yml', '.yaml')):
                continue
            try:
                txt = open(os.path.join(wf, f), encoding='utf-8').read()
            except Exception:                              # noqa: BLE001
                continue
            for m in re.findall(r"python-version:\s*'?\"?(\d+\.\d+)", txt):
                found.add(tuple(int(x) for x in m.split('.')))
    return found


def _git_tracked_py(path, root='.'):
    """Tracked .py files under `path`, or None if git cannot answer.

    None means "fall back to the directory walk" — never "no files", which
    would turn this check into a silent no-op
    ([[feedback_zero_rows_is_a_wiring_error]]).
    """
    import subprocess
    try:
        r = subprocess.run(['git', '-C', root, 'ls-files', '-z', '--', path],
                           capture_output=True, timeout=60)
        if r.returncode != 0:
            return None
        out = r.stdout.decode('utf-8', 'replace')
    except Exception:                                      # noqa: BLE001
        return None
    files = []
    for rel in out.split('\0'):
        if not rel.endswith('.py'):
            continue
        parts = rel.replace('\\', '/').split('/')
        if '_legacy' in parts or '__pycache__' in parts:
            continue
        files.append(os.path.normpath(os.path.join(root, rel)))
    return sorted(files) or None


def scan_source(src, filename='<src>'):
    """Return a list of (lineno, kind, detail) findings.

    Walks tokens. In 3.12+ an f-string is FSTRING_START ... FSTRING_END with
    the replacement-field expression tokenised in between, which is what
    makes the three checks possible at all.

    == 2026-11-21 - A STACK, NOT A SINGLE QUOTE ==
    The first version kept ONE `quote`, captured at depth 1, and compared
    every nested string against it. That is correct only while f-strings do
    not nest. grade_money_flow.py:471 was an outer f-string delimited with
    an apostrophe holding an INNER f-string delimited with a double quote,
    whose dict keys then reused that double quote. It is the inner
    delimiter the keys collide with; measured against the outer apostrophe
    all three looked legal, so this checker called the file clean while
    3.11 rejected it outright.

    Each f-string carries its own delimiter, so a nested string has to be
    compared against every OPEN one -- the innermost usually, but a collision
    with an outer frame is just as fatal.

    That miss is why the Sweat Card went dark for a day: the file is in no
    workflow, nothing imported it and nothing complained, until
    daily_card.yml began byte-compiling the directory and a dormant line
    became a hard dependency of every surface in the product.
    """
    out = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, SyntaxError) as e:
        return [(getattr(e, 'lineno', 0) or 0, 'UNPARSEABLE', str(e))]

    if not hasattr(tokenize, 'FSTRING_START'):
        # Running on <3.12: the interpreter itself would have rejected the
        # bad construct, so there is nothing this check can add.
        return []

    def _delims(tok_string):
        """(quote, is_triple) for an FSTRING_START or a STRING token.

        Strips the prefix rather than slicing a fixed offset. A nested
        rb'x' / r"x" / f"x" carries one or two prefix letters, so
        tok_string[:1] reads the PREFIX as the delimiter and then matches
        nothing -- the second way this check could report a clean file.
        """
        body = tok_string.lstrip('rRbBuUfF')
        if body[:3] in ('"""', "'''"):
            return body[:3], True
        return body[:1], False

    # One frame per OPEN f-string, innermost last. A nested string is checked
    # against all of them: the innermost is the usual collision, but reusing
    # an OUTER frame's delimiter is equally fatal on 3.11.
    stack = []
    for t in toks:
        if t.type == tokenize.FSTRING_START:
            q, triple = _delims(t.string)
            stack.append((t, q, triple))
            continue
        if t.type == tokenize.FSTRING_END:
            if stack:
                start_tok, _q, triple = stack.pop()
                if not triple and t.end[0] != start_tok.start[0]:
                    out.append((start_tok.start[0], 'MULTILINE_FSTRING_FIELD',
                                'replacement field spans lines '
                                f'{start_tok.start[0]}-{t.end[0]} in a '
                                'single-line f-string (legal from 3.12 only)'))
            continue
        if not stack:
            continue
        # Inside an f-string: inspect the expression tokens.
        if t.type == tokenize.STRING:
            q, _tri = _delims(t.string)
            for i, (start_tok, fq, triple) in enumerate(stack):
                if triple or q != fq:
                    continue
                out.append((t.start[0], 'NESTED_SAME_QUOTE',
                            f'nested string reuses the delimiter {fq!r} of '
                            f'the f-string opened on line '
                            f'{start_tok.start[0]} (nesting depth {i + 1} '
                            f'of {len(stack)}) (legal from 3.12 only)'))
                break
        if t.type in (tokenize.STRING, tokenize.NAME, tokenize.OP) \
                and '\\' in t.string:
            out.append((t.start[0], 'BACKSLASH_IN_FIELD',
                        'backslash inside a replacement field '
                        '(legal from 3.12 only)'))
    return out


def main(path, root='.'):
    vers = ci_python_version(root)
    older = sorted(v for v in vers if v < sys.version_info[:2])
    print(f'== check_ci_python_compat ==')
    print(f'   this interpreter : {sys.version_info[0]}.{sys.version_info[1]}')
    print(f'   workflows pin    : '
          f'{sorted(".".join(map(str, v)) for v in vers) or "none found"}')
    if not older:
        print('   nothing older than this interpreter is pinned — this check '
              'only matters when CI runs an OLDER Python than the author.')
    target = older[0] if older else CI_TARGET
    print(f'   checking against : {target[0]}.{target[1]}\n')

    files = []
    if os.path.isfile(path):
        files = [path]
    else:
        # ══ 2026-11-21 · SCAN WHAT CI COMPILES, NOT WHAT LOOKS LIKE SCRATCH ══
        # This skipped every `_*.py` on the CLAUDE.md note that they are
        # gitignored scratch. 133 of them are COMMITTED (the ignore rule
        # post-dates them, and tracked beats ignored), so CI checks them out
        # and `compileall` parses them — while this checker, the author-side
        # counterpart meant to agree with it, did not look at a single one.
        # A checker whose file set is narrower than the gate's file set
        # cannot clear the gate.
        #
        # `_legacy` is excluded to match daily_card.yml's `-x _legacy`: it
        # holds a file unparseable on EVERY version, so reporting it is a
        # standing false alarm against a gate that does not read it. If that
        # `-x` ever goes, this must go with it.
        #
        # TRACKED FILES, because that is what the gate compiles. The gate
        # runs on a CI checkout, which is exactly the tracked tree. The walk
        # below sees 2080 files here against CI's 969 — the extra 1100 are
        # local scratch, and scanning them produced 2 findings in an
        # untracked, gitignored file that can never reach CI. A file set
        # WIDER than the gate's cries wolf; a set NARROWER than it (the
        # `_*.py` skip above) misses the real thing. Neither clears the gate,
        # so the criterion is tracked-or-not: not the filename, and not the
        # ignore rule — 133 `_*.py` files match .gitignore:91 and are
        # committed anyway, and tracked beats ignored.
        tracked = _git_tracked_py(path, root)
        if tracked is not None:
            files = tracked
        for dirpath, dirnames, filenames in ([] if tracked is not None
                                             else os.walk(path)):
            dirnames[:] = [d for d in dirnames
                           if d not in ('.git', '__pycache__', 'node_modules',
                                        '_legacy')]
            for f in sorted(filenames):
                if f.endswith('.py'):
                    files.append(os.path.join(dirpath, f))

    bad = 0
    for f in files:
        try:
            src = open(f, encoding='utf-8').read()
        except Exception as e:                             # noqa: BLE001
            print(f'  ! unreadable {f}: {e}')
            continue
        for lineno, kind, detail in scan_source(src, f):
            bad += 1
            rel = os.path.relpath(f, root)
            print(f'  FAIL {rel}:{lineno}  {kind}')
            print(f'       {detail}')
    print(f'\n  {len(files)} file(s) scanned · {bad} finding(s)')
    if bad:
        print('  These parse HERE and are a SyntaxError on the pinned CI '
              'Python. A SyntaxError at import kills the step in 0 seconds.')
    return 1 if bad else 0


def _self_test():
    # The exact line that broke generate_sweat_card.py, and its fix.
    BROKEN_MULTILINE = (
        "x = f'{_why} run {_hit}% on n={_n},{_bar or \" measured below \"\n"
        "      \"breakeven at its own price\"}'\n")
    FIXED = ("t = _bar or ' measured below breakeven at its own price'\n"
             "x = f'{_why} run {_hit}% on n={_n},{t}'\n")
    NESTED_SAME_QUOTE = "x = f'{d['k']}'\n"
    OK_DIFF_QUOTE = "x = f'{d[\"k\"]}'\n"
    OK_TRIPLE = "x = f'''\n{a}\n{b}\n'''\n"
    OK_PLAIN = "x = f'{a} and {b:.2f}'\n"

    # ── 2026-11-21 · the defect this checker reported CLEAN ──
    # grade_money_flow.py:471, verbatim. An f-string nested inside another
    # f-string: outer delimiter ', inner ", dict keys reusing the inner ".
    # The single-quote version of this scanner compared the keys against the
    # OUTER ' and passed the file, while 3.11.18 rejected it with
    # "f-string: f-string: unmatched '[" -- the doubled prefix is the
    # tokeniser saying the collision is one level down.
    NESTED_FSTRING = (
        "x = (f'{f\"{best[\"w\"]}-{best[\"l\"]}-{best[\"p\"]}\":>12}')\n")
    # Its fix: hoist the inner f-string into a name.
    NESTED_FIXED = ("r = f'{best[\"w\"]}-{best[\"l\"]}-{best[\"p\"]}'\n"
                    "x = (f'{r:>12}')\n")
    # Nesting with DISTINCT delimiters at every level is legal on 3.11.
    OK_NESTED_DIFF = "x = f'{f\"{a}\"}'\n"
    # A prefixed nested string: t.string[:1] read 'r' as the delimiter and
    # matched nothing, so this collision was invisible too.
    PREFIXED_SAME_QUOTE = "x = f'{d[r'k']}'\n"

    cases = [
        ('the real broken line is caught',
         any(k == 'MULTILINE_FSTRING_FIELD'
             for _, k, _ in scan_source(BROKEN_MULTILINE))),
        ('the fix is clean', scan_source(FIXED) == []),
        ('nested same-type quote is caught',
         any(k == 'NESTED_SAME_QUOTE'
             for _, k, _ in scan_source(NESTED_SAME_QUOTE))),
        ('nested DIFFERENT quote is allowed', scan_source(OK_DIFF_QUOTE) == []),
        ('a triple-quoted f-string may span lines',
         scan_source(OK_TRIPLE) == []),
        ('an ordinary f-string is clean', scan_source(OK_PLAIN) == []),
        # The two checks that gave a false all-clear and let this ship.
        ('py_compile/ast alone would NOT catch it',
         _ast_accepts(BROKEN_MULTILINE)),
        # ── the 11-21 regressions ──
        ('a NESTED f-string reusing its OWN delimiter is caught',
         any(k == 'NESTED_SAME_QUOTE'
             for _, k, _ in scan_source(NESTED_FSTRING))),
        ('its fix is clean', scan_source(NESTED_FIXED) == []),
        ('nesting with distinct delimiters is allowed',
         scan_source(OK_NESTED_DIFF) == []),
        ('a PREFIXED nested string reusing the delimiter is caught',
         any(k == 'NESTED_SAME_QUOTE'
             for _, k, _ in scan_source(PREFIXED_SAME_QUOTE))),
        # The positive control for the whole exercise: 3.11 must actually
        # reject the fixture, or the checker is agreeing with nothing.
        ('3.11 really rejects the nested fixture',
         not _parses_on(NESTED_FSTRING, (3, 11))),
        ('3.11 really accepts its fix', _parses_on(NESTED_FIXED, (3, 11))),
    ]
    fails = skipped = 0
    for name, ok in cases:
        # None = could not be determined on THIS interpreter. Printed SKIP,
        # never PASS: a control that reports the expected answer when it did
        # not run is the exact failure mode this file exists to prevent
        # ([[feedback_a_synthesized_failure_carries_no_tail]]).
        if ok is None:
            skipped += 1
            print(f'  SKIP  {name}')
            continue
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            fails += 1
    ran = len(cases) - skipped
    print(f'\n  {ran - fails}/{ran} self-test cases passed'
          + (f' · {skipped} skipped (need a {CI_TARGET[0]}.{CI_TARGET[1]} '
             f'interpreter; this is {sys.version_info[0]}.'
             f'{sys.version_info[1]})' if skipped else ''))
    return 1 if fails else 0


def _parses_on(src, ver):
    """True/False if `src` parses on Python `ver`; None if undecidable here.

    Only a real interpreter of that version answers this. ast.parse's
    feature_version does NOT -- proven by the case above it -- so when this
    runs on anything other than `ver` and no matching interpreter is on PATH,
    the honest answer is None. CI pins 3.11, so on CI these controls DO run,
    which is where they matter most.
    """
    import shutil
    import subprocess
    if sys.version_info[:2] == tuple(ver):
        try:
            compile(src, '<control>', 'exec')
            return True
        except SyntaxError:
            return False
    exe = shutil.which(f'python{ver[0]}.{ver[1]}')
    if not exe:
        cand = os.path.expanduser(
            os.path.join('~', 'AppData', 'Roaming', 'uv', 'python',
                         f'cpython-{ver[0]}.{ver[1]}-windows-x86_64-none',
                         'python.exe'))
        exe = cand if os.path.exists(cand) else None
    if not exe:
        return None
    try:
        r = subprocess.run([exe, '-c',
                            'import sys;compile(sys.stdin.read(),"c","exec")'],
                           input=src, capture_output=True, text=True,
                           timeout=30)
        return r.returncode == 0
    except Exception:                                      # noqa: BLE001
        return None


def _ast_accepts(src):
    """True if ast.parse accepts it even at feature_version 3.11 — i.e. the
    reason a local syntax check cannot be trusted for this."""
    import ast
    try:
        ast.parse(src, feature_version=(3, 11))
        return True
    except SyntaxError:
        return False


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--path', default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument('--root', default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..'))
    ap.add_argument('--self-test', action='store_true')
    a = ap.parse_args()
    if a.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(main(a.path, a.root))
