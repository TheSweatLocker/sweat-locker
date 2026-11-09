#!/usr/bin/env python3
"""Reject a workflow file GitHub would reject -- strictly.

WHY THIS EXISTS (2026-11-08)
---------------------------
`yaml.safe_load` ACCEPTS duplicate mapping keys and keeps the last one. So a
step carrying two `continue-on-error`/`env`/`run` keys parses clean in Python
and is REFUSED by GitHub, which de-registers the entire workflow: it drops out
of the Actions sidebar (listed by raw path instead of its `name:`), every push
logs an instant "Invalid workflow file" run, and NO scheduled run fires.

nba_pipeline.yml and nhl_pipeline.yml sat in exactly that state from 11-06 to
11-08 -- three days, two sports, nothing ran. 8 of 12 NHL reads for 11-07 went
ungraded because NHL's grading step lives in that file. A morning check that
used yaml.safe_load printed "PARSE OK" for both.

Checks: duplicate keys anywhere; a step with both `uses` and `run`; a step with
neither; a missing `name:` or `on:`; no jobs; no trigger.

Usage:  python .github/scripts/validate_workflows.py [path ...]
Exit 0 = every file is one GitHub will register. Exit 1 = at least one is not.
"""
import glob
import os
import sys

import yaml


class StrictLoader(yaml.SafeLoader):
    """SafeLoader that RAISES on a duplicate mapping key, as GitHub does."""


def _no_duplicate_keys(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None,
                f'duplicate key {key!r} -- GitHub refuses the whole file',
                key_node.start_mark)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicate_keys)


def check(path):
    """Return a list of human-readable problems with one workflow file."""
    try:
        with open(path, encoding='utf-8') as fh:
            doc = yaml.load(fh, Loader=StrictLoader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, 'problem_mark', None) or getattr(
            exc, 'context_mark', None)
        where = f' at line {mark.line + 1}' if mark else ''
        return [f'{getattr(exc, "problem", exc)}{where}']

    problems = []
    if not isinstance(doc, dict):
        return ['top level is not a mapping']
    # PyYAML resolves a bare `on:` to the boolean True, not the string 'on'.
    if doc.get(True) is None and doc.get('on') is None:
        problems.append('no `on:` trigger block')
    if not doc.get('name'):
        problems.append('no `name:` -- the Actions sidebar will show the path')
    jobs = doc.get('jobs') or {}
    if not jobs:
        problems.append('no jobs')
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            problems.append(f'job {job_name}: not a mapping')
            continue
        for i, step in enumerate(job.get('steps') or []):
            if not isinstance(step, dict):
                problems.append(f'job {job_name} step #{i}: not a mapping')
                continue
            label = step.get('name') or step.get('uses') or '(unnamed)'
            has_uses, has_run = bool(step.get('uses')), bool(step.get('run'))
            if has_uses and has_run:
                problems.append(
                    f'job {job_name} step #{i} {label!r}: has BOTH `uses` '
                    'and `run`')
            elif not has_uses and not has_run:
                problems.append(
                    f'job {job_name} step #{i} {label!r}: has NEITHER `uses` '
                    'nor `run` -- its body was probably absorbed by the next '
                    'step')
    return problems


def main(argv):
    paths = argv[1:] or sorted(glob.glob('.github/workflows/*.y*ml'))
    bad = 0
    for path in paths:
        problems = check(path)
        name = os.path.basename(path)
        if problems:
            bad += 1
            print(f'FAIL  {name}')
            for problem in problems:
                print(f'        {problem}')
        else:
            print(f'ok    {name}')
    print()
    print(f'{len(paths) - bad} of {len(paths)} workflow files are valid '
          'for GitHub')
    if bad:
        print(f'{bad} file(s) GitHub would REFUSE TO REGISTER -- they will '
              'not appear in the Actions sidebar and no cron will fire.')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
