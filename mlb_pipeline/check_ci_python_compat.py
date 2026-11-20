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


def scan_source(src, filename='<src>'):
    """Return a list of (lineno, kind, detail) findings.

    Walks tokens. In 3.12+ an f-string is FSTRING_START ... FSTRING_END with
    the replacement-field expression tokenised in between, which is what
    makes the three checks possible at all.
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

    depth = 0
    start_tok = None
    quote = ''
    triple = False
    for t in toks:
        if t.type == tokenize.FSTRING_START:
            depth += 1
            if depth == 1:
                start_tok = t
                s = t.string
                triple = s.endswith(('"""', "'''"))
                quote = s[-3:] if triple else s[-1:]
            continue
        if t.type == tokenize.FSTRING_END:
            depth -= 1
            if depth == 0 and start_tok is not None:
                if not triple and t.end[0] != start_tok.start[0]:
                    out.append((start_tok.start[0], 'MULTILINE_FSTRING_FIELD',
                                'replacement field spans lines '
                                f'{start_tok.start[0]}-{t.end[0]} in a '
                                'single-line f-string (legal from 3.12 only)'))
                start_tok = None
            continue
        if depth and start_tok is not None:
            # Inside an f-string: inspect the expression tokens.
            if t.type == tokenize.STRING and not triple:
                q = ('"""' if t.string.startswith('"""')
                     else "'''" if t.string.startswith("'''")
                     else t.string[:1])
                if q == quote:
                    out.append((t.start[0], 'NESTED_SAME_QUOTE',
                                f'nested string reuses the f-string quote '
                                f'{quote!r} (legal from 3.12 only)'))
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
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames
                           if d not in ('.git', '__pycache__', 'node_modules')]
            for f in sorted(filenames):
                if f.endswith('.py') and not f.startswith('_'):
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
    ]
    fails = 0
    for name, ok in cases:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            fails += 1
    print(f'\n  {len(cases) - fails}/{len(cases)} self-test cases passed')
    return 1 if fails else 0


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
