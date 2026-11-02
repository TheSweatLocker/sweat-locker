"""Which (prop_type, tier) cells may occupy a public card slot, per sport.

══ 2026-11-01 · WHY THIS EXISTS ═════════════════════════════════════════
ANDY, after the day audit: "If we cant surface nfl proops 8 weeks in we are
failing majorly, this is ridculous man ... WHY CANT WE SURFACE PROPS WHAT DO
YOU MEAN BY THAT."

The answer was three hardcoded strings, not a policy:

  * generate_sweat_card.fetch_top_props()  read "mlb_pipeline_props"
  * the prop pick construction stamped    "source_table": "mlb_pipeline_props"
  * generate_sharp_card                   read "mlb_pipeline_props"

So on a day with no MLB games the prop section was empty BY CONSTRUCTION,
and an NFL prop had never once been eligible for either card. Eight weeks of
NFL props scored nightly and discarded.

THE REASON A BARE TABLE SWAP WOULD HAVE MADE IT WORSE. That fetch orders by
`conviction.desc`. NFL prop tier is INVERTED, measured on 4,805 graded rows
that carry a real price:

    PRIME     116-123  48.5%  ROI  -8.4%  n=239
    STRONG    633-613  50.8%  ROI  -3.9%  n=1246
    LEAN      637-565  53.0%  ROI  -0.2%  n=1202
    LIGHT     568-526  51.9%  ROI  -1.3%  n=1094
    COVERAGE  122-313  28.0%  ROI -15.3%  n=435
    baseline  48.8%  ROI -4.0%  n=4805

Highest conviction is the WORST bucket. Ordering by it selects the worst
rows on the board, which is how a "fix" ships a loss.

So eligibility is a measured allowlist of (prop_type, tier) cells, and the
RANKER is the cell's own measured ROI — never conviction.

══ HOW A CELL EARNS ITS PLACE ═══════════════════════════════════════════
From _measure_prop_cells_1102.py, every cell must clear ALL THREE:

  1. n >= 30 graded rows                     (the house n gate)
  2. ROI > 0 priced at each row's OWN odds    (never flat -110 —
                                              feedback_flat_110_inflates_every_ml_figure)
  3. positive in BOTH date halves, >= 8 rows each half

Rule 3 is what matters. It rejected cells that look far better than the ones
that passed:

    reception_yds_under LIGHT  +11.8% n=125  -- early -10.5%, late +31.7%
    rush_yds_under      LEAN   +10.0% n=48   -- early  -2.0%, late +25.4%
    pass_tds_over       LEAN   +14.9% n=30   -- early +15.9%, late  +0.5%
    rush_attempts_over  LEAN   +13.3% n=33   -- early +18.3%, late  -2.3%

I was about to publish a reception_yds_under off its headline number. One
half of that sample is negative.

══ WHAT THE MEASUREMENT SAID, PER SPORT ════════════════════════════════
NHL gets NOTHING. Baseline -13.3% ROI on 45,361 graded rows, every row
tiered COVERAGE, and ZERO cells clear the three rules. 832 NHL props were
scored on 2026-11-01. None of them belongs on a card.

NBA gets two cells out of 15,679 graded rows (baseline -9.0%).

MLB keeps its own path and its own ban policy (prop_ban_policy.filter_mlb_props);
these entries are recorded for parity but MLB behaviour is unchanged.

Re-measure with _measure_prop_cells_1102.py before editing this table, and
move the numbers with it. A cell listed here without its record alongside is
[[feedback_comment_asserting_a_measurement]].
"""
from __future__ import annotations

#: (prop_type, tier) -> (hit_pct, roi_pct, n, early_roi, late_roi)
#: Measured 2026-11-01. ROI at each row's own price. All clear n>=30,
#: ROI>0, and positive in both date halves.
ALLOWLIST: dict[str, dict[tuple[str, str], tuple[float, float, int, float, float]]] = {
    'NFL': {
        ('pass_attempts_under', 'LIGHT'):  (62.5, +18.4,  48, +29.5, +14.3),
        ('rush_attempts_under', 'STRONG'): (61.4, +17.3,  57,  +4.7, +32.4),
        ('receptions_under',    'STRONG'): (57.1,  +7.9, 196,  +3.1, +15.8),
        ('rush_yds_under',      'STRONG'): (57.1,  +7.6,  63,  +1.1, +12.7),
        ('reception_yds_over',  'LEAN'):   (55.8,  +5.2, 355,  +5.1,  +5.3),
    },
    'NBA': {
        ('ast_under', 'LEAN'): (60.0, +7.2, 538, +10.8, +2.5),
        ('pra_under', 'LEAN'): (55.0, +3.0, 733,  +4.8, +0.2),
    },
    #: deliberately empty — see the docstring. Do not add an NHL cell
    #: without a fresh measurement that clears all three rules.
    'NHL': {},
    'NCAAF': {},
    'NCAAB': {},
}

#: the prop table per sport. The hardcoded literal this replaces is the
#: whole reason NFL props were invisible.
PROP_TABLE = {
    'MLB': 'mlb_pipeline_props',
    'NFL': 'nfl_pipeline_props',
    'NBA': 'nba_pipeline_props',
    'NHL': 'nhl_pipeline_props',
}

#: Andy's standing prop odds band. Outside it, SKIP.
ODDS_MIN, ODDS_MAX = -300.0, 150.0

#: hard cap on non-MLB props per card, so a newly-opened path cannot flood
#: a surface that has never carried them.
MAX_PER_SPORT = 2
MAX_NON_MLB = 3


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cell_for(sport, prop_type, tier):
    """The measured record for this cell, or None if it is not allowlisted."""
    return ALLOWLIST.get(str(sport).upper(), {}).get(
        (str(prop_type or ''), str(tier or '').upper()))


def row_odds(row):
    """The price of the SIDE THIS ROW TAKES. Under rows take the under price.

    Returns None when absent. A priceless prop is never eligible: a graded
    play with no price is priced at a guess (receipt_invariants refuses it
    downstream anyway).
    """
    key = ('book_under_odds' if str(row.get('direction')) == 'under'
           else 'book_over_odds')
    return _f(row.get(key))


def eligible(sport, rows, max_per_sport=MAX_PER_SPORT):
    """Allowlisted, priced, in-band props for one sport, best cell first.

    Ranked by the CELL's measured ROI, then by n -- never by conviction,
    which is measured inverted on this surface. Deduped to one prop per
    player so a single name cannot take two slots.
    """
    out = []
    for r in rows or []:
        cell = cell_for(sport, r.get('prop_type'), r.get('tier'))
        if cell is None:
            continue
        o = row_odds(r)
        if o is None or not (ODDS_MIN <= o <= ODDS_MAX):
            continue
        hit, roi, n, _e, _l = cell
        out.append((roi, n, o, r))
    out.sort(key=lambda z: (-z[0], -z[1]))
    picked, seen = [], set()
    for roi, n, o, r in out:
        nm = str(r.get('player_name') or '').strip().lower()
        if nm in seen:
            continue
        seen.add(nm)
        r = dict(r)
        r['_cell_roi'] = roi
        r['_cell_n'] = n
        r['_resolved_odds'] = o
        r['_source_table'] = PROP_TABLE.get(str(sport).upper())
        picked.append(r)
        if len(picked) >= max_per_sport:
            break
    return picked



# ══ 2026-11-02 · THE SIDE WE PUBLISH, NOT THE SIDE THE ROW DESCRIBES ════
# Added for the prop_jerry gate. `prop_type` carries its own direction in
# these tables -- verified, 0 of 3169 NFL rows since 10-01 have a prop_type
# that does not end in '_' + direction -- so 'receptions_under' IS the under.
#
# A FADE verdict backs the OPPOSITE side. So a FADE on 'receptions_under'
# publishes the OVER, and checking it against the receptions_under cell
# would gate the play on the record of the bet we did NOT make. That is the
# same inversion that made 72 graded FADE receipts display the under while
# carrying the over's result, and the same one that left the prop playbook
# unable to match a single NHL or NBA row
# (project_prop_playbook_grading_was_mlb_only_1028,
#  feedback_grade_the_side_we_published).
#
# The cells in ALLOWLIST above were measured on each row's OWN direction,
# priced at that direction's own book odds. So the rule is: resolve the
# published side FIRST, then look that up.
_FLIP = {'over': 'under', 'under': 'over'}


def effective_cell_key(prop_type, direction, verdict):
    """(prop_type_as_published, side_as_published) after a FADE flip.

    Returns (None, None) when the shape cannot be resolved, which callers
    must treat as ineligible -- never as "no flip needed".
    """
    pt = str(prop_type or '')
    d = str(direction or '').lower()
    v = str(verdict or '').upper()
    if not pt or d not in _FLIP:
        return None, None
    if not pt.endswith('_' + d):
        # prop_type and direction disagree. Do not guess which is stale.
        return None, None
    if v != 'FADE':
        return pt, d
    flipped = _FLIP[d]
    return pt[: -len(d)] + flipped, flipped


def publishable(sport, prop_type, direction, tier, verdict,
                over_odds, under_odds):
    """May this read be published as a PLAY? -> (ok, reason).

    TIER and VERDICT are different things and both are needed:

      * `tier` is the props table's own tier ('STRONG', 'LEAN', ...). The
        ALLOWLIST cells were measured against THAT column, so it is what
        the cell is looked up by.
      * `verdict` only decides whether the published side is flipped,
        because a FADE backs the opposite of the prop's own direction.

    They are easy to conflate here because prop_jerry_reads stores tier
    NAMES in `call_verdict` for football (measured 2026-11-02: NFL reads
    since 09-01 carry call_verdict STRONG 1363, PRIME 255, LEAN 1341,
    PASS 1866, and only 18 rows that actually say BACK or FADE). Using the
    verdict as the tier would therefore appear to work on most NFL rows and
    be wrong on every BACK/FADE one.

    `reason` is always a sentence, so a refusal is legible in a log rather
    than being a silent drop.
    """
    su = str(sport).upper()
    if su == 'MLB':
        return True, 'MLB keeps its own path (prop_ban_policy)'
    if su not in ALLOWLIST:
        return False, f'{su} has no measured allowlist'
    pt, side = effective_cell_key(prop_type, direction, verdict)
    if pt is None:
        return False, (f'cannot resolve the published side from '
                       f'prop_type={prop_type!r} direction={direction!r}')
    cell = cell_for(su, pt, tier)
    if cell is None:
        return False, (f'{su} {pt} / {str(tier).upper()} is not an '
                       f'allowlisted cell')
    o = _f(over_odds if side == 'over' else under_odds)
    if o is None:
        return False, f'no book price on the {side} side'
    if not (ODDS_MIN <= o <= ODDS_MAX):
        return False, (f'{o:+g} is outside the {ODDS_MIN:+g}..{ODDS_MAX:+g} '
                       f'band')
    return True, (f'allowlisted cell {pt} / {str(tier).upper()}, '
                  f'{o:+g} in band, measured ROI {cell[1]:+.1f}% n={cell[2]}')

if __name__ == '__main__':
    print('prop_cell_allowlist self-test')
    assert cell_for('NFL', 'rush_yds_under', 'STRONG') is not None
    assert cell_for('NFL', 'rush_yds_under', 'PRIME') is None, 'PRIME is not allowlisted'
    assert cell_for('NHL', 'sog_under', 'COVERAGE') is None, 'NHL must be empty'
    assert cell_for('nfl', 'receptions_under', 'strong') is not None, 'case-insensitive'
    rows = [
        {'player_name': 'A', 'prop_type': 'rush_yds_under', 'tier': 'STRONG',
         'direction': 'under', 'book_under_odds': -110},            # keep
        {'player_name': 'B', 'prop_type': 'receptions_under', 'tier': 'STRONG',
         'direction': 'under', 'book_under_odds': -110},            # keep (lower ROI)
        {'player_name': 'C', 'prop_type': 'anytime_td_over', 'tier': 'PRIME',
         'direction': 'over', 'book_over_odds': 120},               # not allowlisted
        {'player_name': 'D', 'prop_type': 'rush_yds_under', 'tier': 'STRONG',
         'direction': 'under', 'book_under_odds': -400},            # out of band
        {'player_name': 'E', 'prop_type': 'rush_yds_under', 'tier': 'STRONG',
         'direction': 'under', 'book_under_odds': None},            # priceless
        {'player_name': 'A', 'prop_type': 'rush_attempts_under', 'tier': 'STRONG',
         'direction': 'under', 'book_under_odds': -110},            # dupe player
    ]
    got = eligible('NFL', rows, max_per_sport=5)
    names = [r['player_name'] for r in got]
    print('  eligible ->', names)
    assert names == ['A', 'B'], names
    assert got[0]['_source_table'] == 'nfl_pipeline_props'
    assert eligible('NHL', [{'player_name': 'X', 'prop_type': 'sog_under',
                             'tier': 'COVERAGE', 'direction': 'under',
                             'book_under_odds': -110}]) == []
    print('  NHL yields nothing, as measured')

    # ---- the FADE inversion ----
    assert effective_cell_key('receptions_under', 'under', 'STRONG') == ('receptions_under', 'under')
    assert effective_cell_key('receptions_under', 'under', 'FADE') == ('receptions_over', 'over')
    assert effective_cell_key('reception_yds_over', 'over', 'FADE') == ('reception_yds_under', 'under')
    assert effective_cell_key('receptions_under', 'over', 'BACK') == (None, None), \
        'prop_type and direction disagree -- must refuse, not guess'
    assert effective_cell_key('anytime_td_over', '', 'BACK') == (None, None)
    assert effective_cell_key(None, 'over', 'BACK') == (None, None)
    print('  FADE flips the published side; a mismatched pair is refused  ok')

    # ---- publishable(sport, prop_type, direction, tier, verdict, o, u) ----
    ok, why = publishable('NFL', 'receptions_under', 'under', 'STRONG',
                          'STRONG', -120, -110)
    assert ok, why
    # the same row FADED publishes receptions_OVER, which is NOT allowlisted
    ok2, why2 = publishable('NFL', 'receptions_under', 'under', 'STRONG',
                            'FADE', -120, -110)
    assert not ok2, why2
    assert 'receptions_over' in why2, why2
    # a FADE is priced on the side we TAKE: over here, so -400 over is out
    ok2b, why2b = publishable('NFL', 'reception_yds_over', 'over', 'STRONG',
                              'FADE', -110, -400)
    assert not ok2b, why2b
    # out of band on the side we would actually take
    ok3, _ = publishable('NFL', 'receptions_under', 'under', 'STRONG',
                         'STRONG', -120, -400)
    assert not ok3
    # priceless on our side
    ok4, _ = publishable('NFL', 'receptions_under', 'under', 'STRONG',
                         'STRONG', -120, None)
    assert not ok4
    # wrong tier -- and note the VERDICT still says STRONG, so a function
    # that read the verdict as the tier would wrongly pass this
    ok5, why5 = publishable('NFL', 'receptions_under', 'under', 'PRIME',
                            'STRONG', -120, -110)
    assert not ok5, why5
    assert 'PRIME' in why5, why5
    # NHL has no cells at all
    ok6, _ = publishable('NHL', 'sog_under', 'under', 'STRONG', 'STRONG',
                         -110, -110)
    assert not ok6
    # MLB is untouched by this gate
    ok7, why7 = publishable('MLB', 'hits_under', 'under', 'PRIME', 'PRIME',
                            -110, -110)
    assert ok7 and 'own path' in why7
    print('  publishable(): tier and verdict kept separate; FADE refused; '
          'MLB exempt  ok')
    print('self-test PASSED')
