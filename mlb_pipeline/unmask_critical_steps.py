"""Strip `|| echo ...` from correctness-critical workflow steps.

WHY
Audit on 2026-09-20: of 55 invocations of scripts whose failure directly
corrupts or hides user-facing truth (graders, resolvers, the pick scrub,
record aggregation, card composers), 49 were wrapped in `|| echo "...
non-fatal"`. That forces exit 0, so a crash and a clean run are
indistinguishable in the Actions UI.

That is not a theoretical risk. jerry_pick_scrub.py raised TypeError on
the first `pass` game of EVERY MLB run for days, checking almost nothing,
and the step showed green every time. Same for the NCAAF variant, which
would have crashed on its first scheduled Sunday.

WHAT THIS CHANGES — AND WHAT IT DOES NOT
Only steps that ALREADY carry `continue-on-error: true` are touched. For
those, GitHub continues the job on a non-zero exit anyway, so removing
`|| echo` changes NOTHING about execution: the same steps still run, the
pipeline still completes. The only difference is the step goes red
instead of green, and the exit code reaches the logs.

Steps WITHOUT continue-on-error are left alone and reported — removing
the mask there would turn a soft failure into a hard stop, which is a
behaviour change and needs a human decision per step.

    python unmask_critical_steps.py --dry-run
    python unmask_critical_steps.py --apply
"""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
WF = Path(__file__).resolve().parent.parent / '.github' / 'workflows'

CRITICAL = (
    'jerry_pick_scrub', 'grade_jerry_reads', 'grade_props',
    'grade_prop_jerry_reads', 'resolve_game_results', 'resolve_ladder_results',
    'resolve_ncaaf_results', 'resolve_nfl_props_espn', 'aggregate_daily_records',
    'compute_surface_records', 'grade_potd', 'grade_ledger_snapshots',
    'reconcile_resolution', 'generate_sweat_card', 'generate_sharp_card',
    'jerry_anchor_potd', 'recompute_primary_play', 'nhl_resolve_results',
    'nba_resolve_results', 'grade_daily_card',
)

_ECHO = re.compile(r'\s*\|\|\s*echo\s+"[^"]*"\s*$')


def step_has_continue_on_error(lines: list[str], idx: int) -> bool:
    """Walk back to this step's `- name:` and look for continue-on-error."""
    for i in range(idx, -1, -1):
        if re.match(r'\s*- name:', lines[i]):
            for j in range(i, min(idx + 1, len(lines))):
                if re.match(r'\s*continue-on-error:\s*true', lines[j]):
                    return True
            return False
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    dry = args.dry_run

    unmasked = skipped = 0
    for f in sorted(WF.glob('*.yml')):
        lines = f.read_text(encoding='utf-8').split('\n')
        changed = False
        for i, line in enumerate(lines):
            m = re.search(r'python\s+([A-Za-z0-9_./-]+\.py)', line)
            if not m or Path(m.group(1)).stem not in CRITICAL:
                continue
            if not _ECHO.search(line):
                continue
            if not step_has_continue_on_error(lines, i):
                print(f'  SKIP  {f.name}:{i+1}  {Path(m.group(1)).stem} '
                      f'— no continue-on-error; removing the mask would '
                      f'halt the job. Left alone.')
                skipped += 1
                continue
            new = _ECHO.sub('', line)
            print(f'  FIX   {f.name}:{i+1}  {Path(m.group(1)).stem}')
            if not dry:
                lines[i] = new
                changed = True
            unmasked += 1
        if changed and not dry:
            f.write_text('\n'.join(lines), encoding='utf-8')

    print(f'\n  unmasked: {unmasked}   left alone (would change behaviour): {skipped}')
    if dry:
        print('  --apply to write.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
