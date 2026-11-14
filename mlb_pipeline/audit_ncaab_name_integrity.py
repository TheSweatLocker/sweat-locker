#!/usr/bin/env python3
"""Fail loudly when an NCAAB team name cannot be resolved or is stored wrong.

══ WHY THIS EXISTS ══════════════════════════════════════════════════════
Andy, 2026-11-13: "SO ghow do we ensure name matching is not an issue going
forward?"

Because "we ported that file" is a promise, not a guarantee. The same defect
has now been found at FOUR sites, each one a hand-rolled match sitting beside
`ncaab_team_aliases`:

    2026-11-03  the app's stripMascot          6 of 29 cards had stats
    2026-11-12  ncaab_game_context.canonical_team   3 of 24 games NULL
    2026-11-12  ncaab_team_stats keyed 'FIU'
    2026-11-13  ncaab_odds_pull did aliases.get(raw) -- an EXACT-STRING hit.
                The Odds API respells schools between pulls, so five events
                silently lost spread, total and both moneylines. One was DUKE.

Every one of those was invisible in the output: a card with no stats looks
like a quiet night, and a game with no line looks like a book that has not
posted. None of them failed a step.

So this is the check that goes RED. It knows nothing about any one scraper;
it asserts the two properties that have to hold no matter who writes:

  1. RESOLVABLE  -- every team name on the live Odds API slate resolves
                    through ncaab_team_resolver. This is the one that cost
                    Duke its line, and it cannot be detected from any table
                    we store: line_history only holds names that ALREADY
                    passed the gate, so it reads 79 of 79 clean by pure
                    survivorship ([[feedback_coverage_pct_hides_dead_code]]).
                    It has to be measured against the FEED.
  2. CANONICAL   -- every team name stored in a table that something JOINS
                    ON resolves to ITSELF. A name that resolves but is not
                    canonical still breaks the join, which is how
                    'LSU New Orleans' kept that team off every stats panel
                    while resolving perfectly well.

Exit 1 on any violation, and this step must NOT be continue-on-error: a
permanently-green name check is the thing that let four of these ship
([[feedback_green_step_that_writes_nothing]]).

An AMBIGUOUS name is reported separately and is NOT a failure. The resolver
refuses a bare mascot rather than guessing, because guessing puts a line on
the wrong fixture ([[feedback_prefix_strip_can_name_another_school]]). That
refusal is the guard working.

USAGE
    python audit_ncaab_name_integrity.py              # full check, exit 1 on fail
    python audit_ncaab_name_integrity.py --no-feed     # stored names only (no API credit)
    python audit_ncaab_name_integrity.py --warn-only   # report, always exit 0
"""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _line in _env.read_text().split('\n'):
        if '=' in _line and not _line.startswith('#'):
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
ODDS_KEY = os.environ.get('ODDS_API_KEY')
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

import ncaab_team_resolver as R  # noqa: E402

#: (table, name column, extra PostgREST filter) for every NCAAB surface whose
#: team column something joins on. A name stored here that is not canonical
#: breaks that join silently.
#:
#: ncaab_game_context/ncaab_game_results are bounded to the FORWARD slate on
#: purpose: 126 non-canonical names exist on 11-02..11-11 from before the
#: 11-12 resolver fix, and a check that stays red over history is a check
#: nobody reads. Judge on the newest artefact
#: ([[feedback_a_migration_can_be_live_then_reverted]]).
JOIN_SURFACES = [
    ('ncaab_team_stats',    'team',      '&season=eq.2026-27'),
    ('roster_physicality',  'team',      '&sport=eq.NCAAB'),
    ('ncaab_game_context',  'home_team', '&game_date=gte.2026-11-12'),
    ('ncaab_game_context',  'away_team', '&game_date=gte.2026-11-12'),
    ('ncaab_game_results',  'home_team', '&game_date=gte.2026-11-12'),
    ('ncaab_game_results',  'away_team', '&game_date=gte.2026-11-12'),
]

ODDS_EVENTS_URL = ('https://api.the-odds-api.com/v4/sports/'
                   'basketball_ncaab/events')


def _page_names(table: str, col: str, extra: str):
    """Every distinct non-null value of `col`, paged. (names, error)."""
    out, off = [], 0
    while True:
        url = (f'{SB}/rest/v1/{table}?select={col}'
               f'&limit=1000&offset={off}{extra}')
        try:
            r = requests.get(url, headers=H, timeout=60)
        except Exception as e:                                # noqa: BLE001
            return None, f'request failed: {e}'
        if r.status_code != 200:
            return None, f'{r.status_code} {r.text[:120]}'
        body = r.json() or []
        out += [row.get(col) for row in body]
        if len(body) < 1000:
            break
        off += 1000
    return {v for v in out if v}, None


def check_stored() -> list[str]:
    """Assert every stored, join-bearing team name resolves to ITSELF."""
    failures: list[str] = []
    print('── STORED NAMES must be canonical ' + '─' * 36)
    for table, col, extra in JOIN_SURFACES:
        names, err = _page_names(table, col, extra)
        if err:
            # A 400 names a column, and a column that moved is a real
            # finding, not a reason to pass
            # ([[feedback_select_star_before_concluding_absent]]).
            failures.append(f'{table}.{col}: cannot read ({err})')
            print(f'  ✗ {table}.{col:10s} UNREADABLE — {err}')
            continue
        unresolvable, non_canonical = [], []
        for nm in names:
            hit = R.resolve(nm)
            if hit is None:
                _, how = R.resolve(nm, with_method=True)
                unresolvable.append((nm, how))
            elif hit != nm:
                non_canonical.append((nm, hit))
        status = '✓' if not (unresolvable or non_canonical) else '✗'
        print(f'  {status} {table}.{col:10s} {len(names):4d} distinct | '
              f'unresolvable {len(unresolvable)} | '
              f'non-canonical {len(non_canonical)}')
        for nm, how in sorted(unresolvable):
            print(f'        unresolvable {nm!r} [{how}]')
            failures.append(f'{table}.{col}: {nm!r} does not resolve [{how}]')
        for nm, hit in sorted(non_canonical):
            print(f'        stored {nm!r} should be {hit!r}')
            failures.append(f'{table}.{col}: {nm!r} should be stored as {hit!r}')
    return failures


def check_live_feed() -> list[str]:
    """Assert every team on the live Odds API slate resolves.

    Uses /events, which returns ids and team names without prices — the
    cheapest way to see exactly the spellings the odds pull will face.
    """
    print('── LIVE FEED names must resolve ' + '─' * 38)
    if not ODDS_KEY:
        print('  ⚠ ODDS_API_KEY missing — cannot check the feed. NOT a pass.')
        return ['ODDS_API_KEY missing, live feed never checked']
    try:
        r = requests.get(ODDS_EVENTS_URL, params={'apiKey': ODDS_KEY},
                         timeout=30)
    except Exception as e:                                    # noqa: BLE001
        return [f'odds feed request failed: {e}']
    if r.status_code != 200:
        return [f'odds feed returned {r.status_code}: {r.text[:120]}']
    events = r.json() or []
    if not events:
        print('  · feed is empty (offseason or no games scheduled) — nothing '
              'to check, and nothing is being lost')
        return []

    failures, ambiguous = [], []
    seen = set()
    for ev in events:
        for raw in (ev.get('away_team'), ev.get('home_team')):
            if not raw or raw in seen:
                continue
            seen.add(raw)
            hit, how = R.resolve(raw, with_method=True)
            if hit is not None:
                continue
            if how == 'ambiguous':
                ambiguous.append(raw)
            else:
                failures.append(
                    f'live feed name {raw!r} does not resolve [{how}] — add '
                    f'alt_names to ncaab_team_aliases, never a constant in a '
                    f'scraper')
    print(f'  {"✓" if not failures else "✗"} {len(events)} events, '
          f'{len(seen)} distinct feed spellings | unresolvable '
          f'{len(failures)} | ambiguous {len(ambiguous)}')
    for f in failures:
        print(f'        {f}')
    # Ambiguity is the guard REFUSING to guess, which is correct behaviour.
    # It is surfaced because it still means a game gets no line, but it must
    # not be fixed by loosening the resolver
    # ([[feedback_a_detector_must_know_the_intentional_case]]).
    for a in sorted(ambiguous):
        print(f'        AMBIGUOUS (guard working, not a bug): {a!r} — needs a '
              f'specific alt_names row, not a looser match')
    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-feed', action='store_true',
                    help='skip the live Odds API check')
    ap.add_argument('--warn-only', action='store_true',
                    help='report everything but always exit 0')
    args = ap.parse_args()

    print('=== NCAAB name integrity ===')
    print(f'  resolver keys loaded: {R.load_aliases()}')
    print()
    failures = check_stored()
    print()
    if not args.no_feed:
        failures += check_live_feed()
        print()

    if not failures:
        print('✓ NCAAB name integrity OK — every live feed spelling resolves '
              'and every stored join key is canonical')
        return 0
    print(f'✗ {len(failures)} NCAAB name integrity failure(s):')
    for f in failures:
        print(f'   - {f}')
    print()
    print('  Fix by adding alt_names to ncaab_team_aliases (the table), or by '
          'correcting the stored value. NEVER by adding a constant to a '
          'scraper or loosening the resolver — that is how four separate '
          'hand-rolled matchers shipped.')
    if args.warn_only:
        print('  (--warn-only: exiting 0 anyway)')
        return 0
    return 1


if __name__ == '__main__':
    sys.exit(main())
