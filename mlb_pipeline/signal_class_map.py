"""Give every ATTRIBUTED signal a family, so weights can be fit per family.

══ 2026-10-30 · THE CLASS-LEVEL REFIT HAD NO CLASSES TO FIT ═══════════════
ANDY: "flip then refit ... the quicker we have that the quicker we can tune
across app and look at data."

The refit he has asked for repeatedly is a per-sport weight on each FAMILY
of evidence -- money flow, model consensus, ATS situational, team-stat
mismatch, external handicappers weighted by their own record. Per-SIGNAL
weight was already tried and is not the lever: a weighted vote's winner is
invariant to uniform scaling, so `scale` and `cap` were inert knobs and the
fit lost out-of-sample (53.55% vs the live weights' 55.92%). Family weight
is different -- the RELATIVE weight between two families does move the
argmax when they disagree.

Measured blocker: of 3,584 signal_attribution rows carrying
`signal_was_right`, 2,118 (59.1%) have a `signal_key` that is not in
signal_sources AT ALL. Not a null class -- 0 of 859 registry rows have a
null class -- the keys were never registered. So the family a row belongs to
is unknown for most of the evidence, and a family fit on the remaining 41%
would be a fit on NCAAF's registered signals and almost nothing else.

Two causes, both handled here.

1. 479 rows are `<base>__fade` variants, and 478 of them have a base key
   that IS registered. Stripping the suffix recovers them (team_form 138,
   roster_physicality 106, team_form_season 87,
   roster_physicality_compound 77, model 29, ...).

   They are kept DISTINGUISHABLE rather than silently merged. A fade inverts
   what "right" means (feedback_grade_the_side_we_published: 641 of 692 fade
   receipts named the side we did not take), and I could not test the
   orientation here -- a base row and its __fade row never share a game, 0
   of 3,584, so there is no inversion to measure. Same family, separate
   weight, and the fit can discover whether the orientation matters instead
   of me assuming it.

2. 1,639 rows across just 18 keys are genuinely unregistered, and they are
   not miscellany -- they are four of the families in the standard, with
   their measured accuracy on the signal's OWN outcome:

     model (LR shadow)        MLB  LR_SHADOW         64.3% n=112
                              MLB  LR_TOTAL_SHADOW   53.4% n=88
                              NFL  LR_SHADOW         43.4% n=83   <- inverted
     situational (cohorts)    NCAAF HOME_FAV         54.6% n=317
                              NCAAF HEAVY_HOME_DOG   60.0% n=70
                              NFL   HOME_FAV         41.2% n=68   <- inverted
                              NFL   DIV_HOME_COVER   33.3% n=30   <- inverted
     team_stat_mismatch       NFL  OFF_RATING_GAP    42.6% n=94   <- inverted
     external                 NCAAF covers           57.4% n=47
                              NCAAF action           68.8% n=16
                              NCAAF dimers           33.3% n=15

   The `external` family is the one Andy specified as "external
   handicappers weighted by their own record". It has never been weighted by
   record because it was never registered, so no refit could see it.

WHY A MODULE AND NOT ROWS IN signal_sources
A registry row carries `enabled`, and the scorer reads enabled rows. Adding
18 rows to get a label risks turning on scoring that was never backtested --
feedback_unmeasured_signal_must_not_outweigh_measured, where a 0.3 prior on
n=0 outweighed every measured sibling. This file only labels. It cannot
change what the engine scores.

HOW TO READ THE ACCURACIES ABOVE
They are the signal's OWN outcome (`signal_was_right`), never the pick's
(`result`), which is stamped identically on every signal row for a pick and
punishes a signal for DISAGREEING -- that bug made 21.9% of the weekly
report's rows wrong. Several n are under the n>=30 gate, so a fit over them
has to shrink hard toward the family prior rather than trust the cell.

USAGE
  from signal_class_map import resolve
  cls, is_fade = resolve('COHORT_NFL_HOME_FAV', 'NFL', registry_class=None)

  python signal_class_map.py          # coverage self-test against live data
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

#: Suffix the engine appends when it publishes the INVERSE of a signal.
FADE_SUFFIX = '__fade'

#: A second suffix seen once in the data (external:covers__fadeflag).
_EXTRA_FADE_SUFFIXES = ('__fadeflag',)

#: The families. These are the names Andy's selection standard uses; the
#: registry's own `class` values are kept as-is where they already exist, so
#: this only has to name the families the registry never covered.
CLASS_MODEL = 'model'
CLASS_SITUATIONAL = 'situational'
CLASS_TEAM_STAT_MISMATCH = 'team_stat_mismatch'
CLASS_EXTERNAL = 'external'

#: Exact keys that signal_attribution emits but signal_sources never defined.
#: Every one was read off live data on 2026-10-30, not guessed -- the count
#: beside it is the row count carrying `signal_was_right`.
UNREGISTERED_CLASS_BY_KEY = {
    'LR_SHADOW':                 CLASS_MODEL,               # 206 rows, 4 sports
    'LR_TOTAL_SHADOW':           CLASS_MODEL,               # 95
    'OFF_RATING_GAP':            CLASS_TEAM_STAT_MISMATCH,  # 96
    'EPA_GAP':                   CLASS_TEAM_STAT_MISMATCH,  # 1
    'COHORT_NCAAF_HOME_FAV':       CLASS_SITUATIONAL,       # 317
    'COHORT_NCAAF_HEAVY_HOME_FAV': CLASS_SITUATIONAL,       # 110
    'COHORT_NCAAF_HEAVY_HOME_DOG': CLASS_SITUATIONAL,       # 70
    'COHORT_NFL_HOME_FAV':         CLASS_SITUATIONAL,       # 68
    'COHORT_NFL_DIV_HOME_COVER':   CLASS_SITUATIONAL,       # 30
    'COHORT_NFL_HEAVY_HOME_DOG':   CLASS_SITUATIONAL,       # 12
}

#: Anything matching these is that family, so a NEW cohort or handicapper
#: does not silently fall back to UNMAPPED the day it is added. The exact
#: table above stays as documentation of what was measured; these catch the
#: next one. A prefix rule is why this file will not need editing weekly.
_PATTERNS = (
    (re.compile(r'^COHORT_', re.I),            CLASS_SITUATIONAL),
    (re.compile(r'^external:', re.I),          CLASS_EXTERNAL),
    (re.compile(r'^LR_[A-Z_]*SHADOW$', re.I),  CLASS_MODEL),
    (re.compile(r'^(OFF|DEF|EPA|NET)_[A-Z_]*GAP$', re.I),
     CLASS_TEAM_STAT_MISMATCH),
)

#: What `resolve` returns when it genuinely cannot place a key. Callers must
#: treat this as "exclude from the family fit", never as a family of its own
#: -- pooling unlike evidence under one weight is
#: feedback_pooled_slope_across_scales_is_fake.
UNMAPPED = 'UNMAPPED'


def strip_fade(signal_key: str) -> Tuple[str, bool]:
    """('home_ats_cold_at_home__fade') -> ('home_ats_cold_at_home', True)."""
    k = str(signal_key or '')
    for suf in (FADE_SUFFIX,) + _EXTRA_FADE_SUFFIXES:
        if k.endswith(suf):
            return k[:-len(suf)], True
    return k, False


def resolve(signal_key: str,
            sport: Optional[str] = None,
            registry_class: Optional[str] = None) -> Tuple[str, bool]:
    """Return (family, is_fade) for an attributed signal key.

    `registry_class` is signal_sources.class for the BASE key when the
    caller already has it; it WINS, because the registry is the owner of the
    taxonomy and this module exists only to fill what it never covered.
    """
    base, is_fade = strip_fade(signal_key)
    if registry_class:
        return str(registry_class), is_fade
    exact = UNREGISTERED_CLASS_BY_KEY.get(base)
    if exact:
        return exact, is_fade
    for rx, cls in _PATTERNS:
        if rx.match(base):
            return cls, is_fade
    return UNMAPPED, is_fade


def _self_test() -> int:
    """Measure coverage against live attribution. Exits non-zero on a gap.

    A mapping that quietly stops covering the data is the failure this is
    meant to prevent, so it is checked against the rows, not asserted.
    """
    import collections
    import os
    from pathlib import Path

    import requests

    env = Path(__file__).parent / '.env'
    if env.exists():
        for line in env.read_text().split('\n'):
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())
    sb, key = os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY']
    h = {'apikey': key, 'Authorization': f'Bearer {key}'}

    def page(table, params):
        out, off = [], 0
        while True:
            p = dict(params)
            p['limit'] = 1000
            p['offset'] = off
            r = requests.get(f'{sb}/rest/v1/{table}', headers=h, params=p,
                             timeout=120)
            if r.status_code != 200:
                print(f'  ! {table} HTTP {r.status_code} {r.text[:140]}')
                return None
            d = r.json()
            out += d
            if len(d) < 1000:
                return out
            off += 1000

    rows = page('signal_attribution',
                {'select': 'sport,signal_key,signal_was_right',
                 'signal_was_right': 'not.is.null'})
    src = page('signal_sources', {'select': 'signal_key,class'})
    if rows is None or src is None:
        print('CANNOT VERIFY -- treating as a failure')
        return 2
    reg = {s['signal_key']: s.get('class') for s in src}

    fam = collections.Counter()
    unmapped = collections.Counter()
    for r in rows:
        base, _ = strip_fade(r.get('signal_key'))
        cls, is_fade = resolve(r.get('signal_key'), r.get('sport'),
                               reg.get(base))
        fam[cls] += 1
        if cls == UNMAPPED:
            unmapped[(str(r.get('sport')).upper(), r.get('signal_key'))] += 1

    total = sum(fam.values())
    placed = total - fam[UNMAPPED]
    print(f'=== signal_class_map coverage: {placed}/{total} '
          f'({100.0 * placed / total:.1f}%) of attributed rows placed ===')
    for cls, n in fam.most_common():
        print(f'  {cls:22s} {n:5d}')
    if unmapped:
        print(f'\n{sum(unmapped.values())} row(s) still UNMAPPED:')
        for (sp, k), n in unmapped.most_common(15):
            print(f'  {sp:6s} {str(k)[:46]:46s} n={n}')
        print('\nAdd them above, or to _PATTERNS if they are a new family.')
        return 1
    print('\nevery attributed signal has a family.')
    return 0


if __name__ == '__main__':
    raise SystemExit(_self_test())
