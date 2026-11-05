"""Which scripts WRITE to the database and are in no workflow?

══ 2026-10-23 · B157 · THE PATTERN THAT KEEPS COSTING US ══
Eight instances found by accident in three days, each one dark for days to
weeks, each one found because Andy opened a screen and saw something wrong:

  nba_player_log_ingest.py        no NBA prop could ever be graded
  split_sharp_card_record.py      Sharp breakdown frozen a month, in front of users
  refresh_calibration_notes.py    same, caught 10-21
  cross_sport_lines.yml           workflow_dispatch only - the Fade's only input
  refresh_nfl_context_qb.py       NYG carried a released QB on 11 forward rows
  audit_qb_staleness.py           the detector for that, also never run
  recompute_ncaaf_per_game_stats  1,469 of 5,260 NCAAF stat rows withheld
  recompute_nfl_epa_units.py      same class, NFL side

Finding these one at a time, by noticing a wrong number downstream, is not a
strategy. This is the scheduling equivalent of `schema_guard --audit`: it
reads every script, decides whether it WRITES, and checks whether any
workflow references it.

A write is what makes absence dangerous. A read-only script that never runs
costs nothing; a WRITER that never runs leaves a table frozen while every
consumer keeps quoting it as current, which is exactly
feedback_green_step_that_writes_nothing seen from the other end.

Exit code is 1 when an unscheduled writer exists, so this can gate a nightly.

Usage:
  python audit_unscheduled_writers.py
  python audit_unscheduled_writers.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# A docstring line carrying an arrow killed this script mid-report on a
# cp1252 console (UnicodeEncodeError, partway through the alphabet), so a
# local run never reached anything after ncaaf_alias_sync.py. Harmless on the
# UTF-8 runner and fatal to anyone triaging the list by hand — which is the
# only way this list ever gets triaged.
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
WF = REPO / '.github' / 'workflows'

#: Signatures of a DB write. PostgREST writes go out as POST/PATCH/PUT/DELETE
#: against /rest/v1/, and the repo also wraps them in helpers.
WRITE_RE = re.compile(
    r'requests\.(post|patch|put|delete)\s*\(|'
    r'\.(post|patch|put|delete)\s*\(\s*f?["\'][^"\']*rest/v1|'
    r'\bupsert\s*\(|\b_upsert\b|\bsb_write\b|\bwrite_rows\b|'
    # ══ 2026-11-04 · A urllib WRITER WAS NOT COUNTED AS A WRITER ══
    # Everything above assumes `requests`. reconcile_stale_side_receipts.py
    # writes public_receipts through urllib.request.Request(url, data=...,
    # method='POST') via its own req() wrapper, so it matched NONE of these
    # and was absent from the report entirely — not "scheduled", not
    # "one-shot by name", invisible. It is a writer of record and it had
    # been unscheduled since the day it was written (B144). A detector that
    # cannot see a whole transport has a hole the size of that transport.
    r'urllib\.request\.Request\s*\(|'
    r'\bmethod\s*=\s*["\'](?:POST|PATCH|PUT|DELETE)["\']|'
    r'\breq\s*\(\s*["\'](?:POST|PATCH|PUT|DELETE)["\']',
    re.I)

#: A script that only writes to jerry_cache for a dry run, or only ever writes
#: when passed --apply, still counts: the point is that nothing invokes it.

#: Scratch convention - mlb_pipeline/_*.py is gitignored, not production.
def is_scratch(p: Path) -> bool:
    return p.name.startswith('_')


def workflow_text() -> str:
    """Everything that can INVOKE a script: the workflow YAML, and the shell
    scripts those workflows call.

    ══ 2026-11-04 · THE SCHEDULE DOES NOT ALL LIVE IN YAML ══
    This read only .github/workflows/*.yml. On 2026-09-29 the entire
    cross-sport nightly was deliberately moved OUT of the YAML into
    .github/scripts/nightly_cross_sport.sh — one definition, two callers —
    and this audit never learned to look there, so every step scheduled in
    that file read as "reachable from nothing".

    Proof it was wrong rather than merely incomplete:
    reconcile_card_recap_to_receipts.py runs in that script every night and
    was reported here as an unscheduled writer.

    Any .sh under .github counts, because a workflow that calls one shell
    script may call another.
    """
    if not WF.exists():
        return ''
    out = []
    files = sorted(WF.glob('*.yml')) + sorted(WF.glob('*.yaml'))
    files += sorted((REPO / '.github').rglob('*.sh'))
    for f in files:
        try:
            out.append(f.read_text(encoding='utf-8', errors='replace'))
        except OSError:
            pass
    return '\n'.join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    wf = workflow_text()
    if not wf:
        print('no workflow files found - cannot judge scheduling')
        return 2

    # ── reachability: a module IMPORTED by a scheduled script is covered ──
    # Checking workflow text alone overcounts badly. price_picks.py appears in
    # no .yml yet runs nightly because a scheduled script imports it, and the
    # shared libraries (snapshot_writer, prop_publish_lock, rule_registry) are
    # never invoked directly at all. Counting those as dark buries the handful
    # that genuinely are, and a report nobody can triage is the same as no
    # report.
    import_re = re.compile(r'^[ \t]*(?:from|import)[ \t]+([A-Za-z_]\w*)', re.M)
    imports: dict = {}
    for q in HERE.glob('*.py'):
        if is_scratch(q):
            continue
        try:
            imports[q.stem] = set(import_re.findall(
                q.read_text(encoding='utf-8', errors='replace')))
        except OSError:
            imports[q.stem] = set()
    reachable = {k for k in imports if k in wf}
    frontier = set(reachable)
    while frontier:
        nxt = set()
        for m in frontier:
            for dep in imports.get(m, ()):
                if dep in imports and dep not in reachable:
                    reachable.add(dep)
                    nxt.add(dep)
        frontier = nxt

    #: One-shot by design: a repair, backfill or one-time reconcile is
    #: SUPPOSED to be unscheduled. Reported separately so they do not drown
    #: the real finding.
    ONE_SHOT = ('repair_', 'regrade_', 'backfill_', 'purge_', 'reconcile_',
                'void_', 'resync_', 'fix_', 'seed_', 'migrate_',
                'totals_cohort_', 'correct_', 'enrich_receipts_',
                'set_manual_')

    writers, unscheduled, skipped = [], [], 0
    for p in sorted(HERE.glob('*.py')):
        if is_scratch(p) or p.name == Path(__file__).name:
            skipped += 1
            continue
        try:
            src = p.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        if not WRITE_RE.search(src):
            continue
        stem = p.stem
        writers.append(stem)
        if stem in reachable:
            continue
        m = re.search(r'"""(.*?)(?:\n|""")', src, re.S)
        what = (m.group(1).strip() if m else '')[:84]
        unscheduled.append({'script': p.name, 'what': what,
                            'one_shot': stem.startswith(ONE_SHOT)})

    if a.json:
        print(json.dumps({'writers': len(writers),
                          'unscheduled': unscheduled}, indent=2))
    else:
        recurring = [u for u in unscheduled if not u['one_shot']]
        oneshot = [u for u in unscheduled if u['one_shot']]
        print(f'== unscheduled DB writers · {len(writers)} writers scanned '
              f'({skipped} scratch/self skipped) ==')
        print(f'   {len(reachable)} reachable from a workflow — named in a '
              f'.yml, or imported by something that is')
        print()
        print(f'-- NEEDS A DECISION: {len(recurring)} writer(s) reachable '
              f'from nothing, and not one-shot by name --')
        for u in sorted(recurring, key=lambda x: x['script']):
            print(f"  {u['script']}")
            if u['what']:
                print(f"      {u['what']}")
        print()
        # ══ 2026-11-04 · "ONE-SHOT BY NAME" IS A GUESS, AND IT HAS COST US ══
        # This bucket was headed "Unscheduled is CORRECT" and printed 12 of
        # its 52 names. Both halves were wrong.
        #
        # backfill_nfl_result_weather.py sits in it. It is the ONLY writer of
        # nfl_game_results.wind, that column has been NULL on every game
        # since 2026-10-11, and wind is the only validated edge in the
        # system (B205). The audit DID see the script — it exempted it for
        # starting with 'backfill_'. B205 asked why the audit missed it and
        # guessed reachability; this is the answer, and it is the name.
        #
        # A prefix describes intent, never cadence: a 'backfill_' that owns a
        # column no scheduled writer fills is load-bearing, and a
        # 'reconcile_' can be a nightly loop —
        # reconcile_card_recap_to_receipts is exactly that. So the bucket
        # stays separate and still does not set the exit code, but it prints
        # in FULL and says what it is: a guess to be triaged, not a
        # clearance.
        print(f'-- one-shot BY NAME ({len(oneshot)}) — prefix says '
              f'repair/backfill/regrade/reconcile/void/purge --')
        print('   The prefix is a GUESS about cadence, not a clearance. It '
              'was wrong about')
        print('   backfill_nfl_result_weather.py — sole writer of '
              'nfl_game_results.wind, dark')
        print('   three weeks (B205). Triage this list; do not skim it.')
        for u in sorted(oneshot, key=lambda x: x['script']):
            print(f"  {u['script']}")
            if u['what']:
                print(f"      {u['what']}")
        print()
        print('  A writer that never runs leaves its table frozen while every')
        print('  consumer keeps quoting it as current.')
        return 1 if recurring else 0
    return 1 if unscheduled else 0


if __name__ == '__main__':
    sys.exit(main())
