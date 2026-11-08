"""audit_context_column_fill — per-column fill scan of every live game_context.

══ 2026-11-07 · WHY THIS EXISTS ═════════════════════════════════════════
Andy, after I reported the UNC/UConn gaps: "Why didnt cadence catch this?"

Fair. The morning audit counted nulls on the SURFACES — unpriced reads,
null convictions — and never asked the one question that finds a dark
enrichment writer: for each column on each `*_game_context`, what fraction
of the forward slate actually carries a value? The standing rule already
said to do this ("scan per SPORT: a column live in one sport and 0% in
another is a wiring break", feedback_any_null_finding_must_be_investigated)
and it was being applied to reads and props but not to the context tables
the reads are built FROM.

Two different defects, and this reports them separately because they have
different fixes:

  SCHEMA DIVERGENCE — the column does not exist on this sport's table at
  all, while it does on others. This is the one that cost the most on
  11-07: `jerry_pred_total` exists on mlb_game_context ONLY, and
  collapse_sharp_fade_violations asked all six sports for it. A missing
  column 400s the WHOLE fetch, its "fallback-safe" retry dropped every
  optional column including close_total, and the sharp-fade gate could
  not fire at all outside MLB — 69 flips on MLB, 0 on five sports, step
  exiting 0 the whole time. One absent column, five sports dark.

  ZERO FILL — the column exists and is empty on every forward row. On
  11-07 NCAAF carried 13 of these across all 51 games: all six
  `consensus_fade_*`, `season_week`, `matched_patterns`,
  `primary_play_shadow_v2`, `shadow_v2_generated_at`, `sweat_tier_current`,
  `team_trends_updated_at`, and both `returning_production_def`.

The signal that separates "nobody populates this yet" from "this broke" is
the CROSS-SPORT CONTRAST, so every finding is reported with the sports where
the same column IS populated. A column at 0% here and 90% next door is a
wiring break; one at 0% everywhere is unbuilt, which is a backlog item, not
an incident.

Deliberately NOT a pass/fail on absolute fill: a forward slate legitimately
has empty closing lines and empty weather at T-10 days. The comparison is
against the other sports, not against 100%.

CLI:
  python audit_context_column_fill.py
  python audit_context_column_fill.py --sport NCAAF --verbose
  python audit_context_column_fill.py --json
  python audit_context_column_fill.py --days-back 1   # include yesterday

Exit: 2 = schema divergence or a contrast break found, 0 = nothing to action.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
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

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

#: Only sports with a live context table. MLB stays in: an offseason sport
#: reporting 0 rows is itself worth seeing, and the scan says so rather than
#: silently dropping it ([[feedback_zero_rows_is_a_wiring_error]]).
CTX_TABLES = {
    'MLB': 'mlb_game_context',
    'NFL': 'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NBA': 'nba_game_context',
    'NHL': 'nhl_game_context',
    'NCAAB': 'ncaab_game_context',
}

#: Columns whose emptiness carries no information — ids, stamps that only
#: exist once a later step runs, and free-text. Kept short on purpose: it is
#: a blocklist duplicating a lookup table that caused
#: [[feedback_a_blocklist_duplicating_a_lookup_table]], so anything added
#: here needs a reason beyond "it was noisy".
_IGNORE_SUFFIXES = ('_id',)
_IGNORE_EXACT = {'id', 'created_at', 'inserted_at'}

#: A column populated on at least this share of one sport's forward rows is
#: treated as "this sport populates it", for the contrast test.
_LIVE_AT = 0.20

#: ══ THE CROSS-SPORT CONTRACT ════════════════════════════════════════════
#: The first version of this scan diffed every column across every table and
#: reported 309 "divergences" — which was noise, because a sport-specific
#: column is not a defect. NHL is supposed to have `away_goalie` and NCAAB
#: is supposed to have `adj_em_gap`; nothing shared asks the others for them.
#:
#: What actually breaks is narrower and worth naming explicitly: a column
#: that SHARED, SPORT-PARAMETERISED code asks every sport for. That is the
#: vocabulary below, taken from the sport registries in the callers
#: themselves (collapse_sharp_fade_violations.SPORT_CONFIG,
#: compute_align_status's lens fields, refit_signal_registry._SPREAD_COL,
#: grade_jerry_reads.SPREAD_LINE_COL_BY_SPORT). If one of these is absent on
#: a sport whose siblings have it, some shared caller is getting a 400 and
#: therefore NOTHING for that sport.
#:
#: Each entry maps a logical field to the per-sport column names that
#: legitimately satisfy it, so a sport that spells it differently is NOT
#: reported. NHL's spread lives in `close_puckline`, not `close_spread`
#: ([[feedback_magnitude_cannot_express_a_side]] and the NHL registry in
#: grade_jerry_reads), and that is a naming difference, not a gap.
#:
#: Adding a row here is a claim that shared code depends on the field. Keep
#: it honest: this is a contract, not a wishlist.
CROSS_SPORT_CONTRACT = {
    'spread line':   ('close_spread', 'close_puckline', 'close_puckline_home'),
    'total line':    ('close_total',),
    'home moneyline': ('close_home_ml',),
    'away moneyline': ('close_away_ml',),
    'the pick':      ('primary_play',),
    'projected spread': ('projected_spread',),
    'projected total':  ('projected_total',),
    'monte carlo':   ('mc_probabilities',),
    'public splits': ('oddscrowd_snapshot',),
    'teams':         ('home_team',),
    'slate date':    ('game_date',),
}


def _et_today() -> dt.date:
    """ET calendar date. Returns a DATE, so callers keep their own tz logic
    ([[feedback_hardcoded_et_offset_breaks_in_est]] — this runs mid-morning,
    well clear of the 04:00-04:59 UTC window where a fixed -4 disagrees with
    true ET, but the offset is named rather than buried)."""
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)).date()


def _fetch(table: str, since: str, limit: int) -> tuple[list, str | None]:
    """Forward rows for one context table. Returns (rows, error)."""
    try:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=60,
                         params={'select': '*', 'game_date': f'gte.{since}',
                                 'order': 'game_date.asc',
                                 'limit': str(limit)})
    except Exception as exc:                     # network, not schema
        return [], f'{type(exc).__name__}: {exc}'
    if r.status_code not in (200, 206):
        return [], f'HTTP {r.status_code} {str(r.text)[:160]}'
    body = r.json()
    if not isinstance(body, list):
        return [], f'unexpected shape {type(body).__name__}'
    return body, None


def _interesting(col: str) -> bool:
    if col in _IGNORE_EXACT:
        return False
    return not col.endswith(_IGNORE_SUFFIXES)


def scan(days_back: int = 0, limit: int = 400) -> dict:
    """Per-sport, per-column fill over the forward slate."""
    since = (_et_today() - dt.timedelta(days=days_back)).isoformat()
    out = {'since': since, 'sports': {}, 'errors': {}}
    for sport, table in CTX_TABLES.items():
        rows, err = _fetch(table, since, limit)
        if err:
            out['errors'][sport] = err
            continue
        cols = collections.OrderedDict()
        for row in rows:
            if not isinstance(row, dict):
                continue
            for k, v in row.items():
                slot = cols.setdefault(k, [0, 0])
                slot[1] += 1
                # '' and [] and {} are as empty as None for a data column.
                if v is not None and v != '' and v != [] and v != {}:
                    slot[0] += 1
        out['sports'][sport] = {
            'table': table,
            'rows': len(rows),
            'fill': {k: {'n': v[0], 'of': v[1],
                         'pct': round(100.0 * v[0] / v[1], 1) if v[1] else 0.0}
                     for k, v in cols.items()},
        }
    return out


def findings(scanned: dict) -> dict:
    """Split the scan into schema divergence, contrast breaks and unbuilt."""
    sports = scanned['sports']
    # Which columns does each sport's table HAVE, and which does it populate?
    present = {sp: set(d['fill']) for sp, d in sports.items() if d['rows']}
    live = {sp: {c for c, f in d['fill'].items()
                 if f['of'] and f['n'] / f['of'] >= _LIVE_AT}
            for sp, d in sports.items() if d['rows']}
    all_cols = set().union(*present.values()) if present else set()

    # ── 1. the cross-sport contract: a shared caller asks every sport ──
    #
    # 2026-11-07, caught on this scan's own first run: an earlier version
    # bucketed any sport below _LIVE_AT as "present but 0%" and printed that
    # as fact. NFL mc_probabilities is 29 of 150 — thin, at 19.3%, just
    # under the threshold — and the scan asserted zero. A detector that
    # misstates its own number is worse than no detector
    # ([[feedback_comment_asserting_a_measurement]],
    # [[feedback_sample_size_with_pct]]), so ABSENT / ZERO / THIN are now
    # three distinct verdicts and every one of them carries its n.
    divergence = []
    for field, aliases in CROSS_SPORT_CONTRACT.items():
        live_on, absent_on, zero_on, thin_on = [], [], [], []
        for sp in sorted(present):
            hit = next((c for c in aliases if c in present[sp]), None)
            if hit is None:
                absent_on.append(sp)
                continue
            # best-filled alias wins: NHL satisfies 'spread line' through
            # close_puckline even with close_spread sitting empty.
            best = max((sports[sp]['fill'][c] for c in aliases
                        if c in sports[sp]['fill']),
                       key=lambda f: f['pct'], default=None)
            if best is None:
                absent_on.append(sp)
            elif best['n'] == 0:
                zero_on.append((sp, best))
            elif best['pct'] / 100.0 >= _LIVE_AT:
                live_on.append((sp, best))
            else:
                thin_on.append((sp, best))
        # Only a report if someone genuinely HAS it — otherwise the field is
        # not built yet and there is nothing to contrast against.
        if live_on and (absent_on or zero_on or thin_on):
            divergence.append({'field': field, 'aliases': list(aliases),
                               'absent_on': absent_on,
                               'zero_on': [(sp, f['of']) for sp, f in zero_on],
                               'thin_on': [(sp, f['n'], f['of'], f['pct'])
                                           for sp, f in thin_on],
                               'live_on': [(sp, f['n'], f['of'], f['pct'])
                                           for sp, f in live_on]})

    # ── 2. zero fill with a live sibling, on columns of the SAME NAME ──
    # Unchanged and much quieter than the schema diff: a column present on
    # two sports, carrying data on one and empty on every forward row of
    # the other, is the NBA-props-carry-no-opp_abbrev shape
    # ([[feedback_any_null_finding_must_be_investigated]]).
    contrast, unbuilt = [], []
    for col in sorted(all_cols):
        if not _interesting(col):
            continue
        has = {sp for sp in present if col in present[sp]}
        if len(has) < 2:
            continue          # single-sport column: nothing to compare to
        populated = sorted(sp for sp in has if col in live.get(sp, set()))
        zero = sorted(sp for sp in has
                      if sports[sp]['fill'][col]['of']
                      and sports[sp]['fill'][col]['n'] == 0)
        if zero and populated:
            contrast.append({'column': col, 'empty_on': zero,
                             'populated_on': populated})
        elif zero and not populated and len(zero) == len(has):
            unbuilt.append({'column': col, 'empty_on': zero})
    return {'divergence': divergence, 'contrast': contrast, 'unbuilt': unbuilt}


def report(days_back: int = 0, limit: int = 400, verbose: bool = False,
           only_sport: str | None = None, contrast_limit: int = 12) -> int:
    scanned = scan(days_back=days_back, limit=limit)
    f = findings(scanned)
    sports = scanned['sports']

    print(f'=== context column fill · forward from {scanned["since"]} ===')
    for sport, d in sports.items():
        if only_sport and sport != only_sport:
            continue
        cols = len(d['fill'])
        print(f'  {sport:<6} {d["table"]:<20} rows={d["rows"]:<5} '
              f'columns={cols}')
        if d['rows'] == 0:
            print(f'         no forward rows — if this sport is in season '
                  f'that is a WIRING ERROR, not an empty slate')
    for sport, err in (scanned.get('errors') or {}).items():
        print(f'  {sport:<6} FETCH FAILED: {err}')

    if f['divergence']:
        print(f'\n  🚨 CROSS-SPORT CONTRACT — {len(f["divergence"])} field(s) '
              f'that shared code asks every sport for are not satisfied '
              f'everywhere.')
        print('     A missing column 400s the WHOLE fetch, so a caller that '
              'asks every sport for one of these gets NOTHING back for the '
              'sports that lack it — and may exit 0 doing it.')
        for d in f['divergence']:
            _live = ', '.join(f'{sp} {n}/{of}' for sp, n, of, _ in d['live_on'])
            if d['absent_on']:
                print(f'     · {d["field"]:<18} COLUMN ABSENT on '
                      f'{",".join(d["absent_on"]):<20} | live: {_live}')
            for sp, of in d['zero_on']:
                print(f'     · {d["field"]:<18} ZERO on {sp} '
                      f'(0 of {of} forward rows){"":<4} | live: {_live}')
            for sp, n, of, pct in d['thin_on']:
                print(f'     · {d["field"]:<18} THIN on {sp} '
                      f'({n} of {of}, {pct}%){"":<6} | live: {_live}')
            print(f'       {"":18} (columns checked: '
                  f'{", ".join(d["aliases"])})')

    if f['contrast']:
        print(f'\n  🚨 ZERO FILL WITH A LIVE SIBLING — {len(f["contrast"])} '
              f'column(s) empty on every forward row of one sport while '
              f'populated on another.')
        # The count is always stated in full; the LIST is capped so this can
        # sit inside the morning audit without burying the contract breaks
        # above it. Run with --contrast-limit 0 for all of them.
        _shown = f['contrast'] if contrast_limit <= 0 \
            else f['contrast'][:contrast_limit]
        for d in _shown:
            print(f'     · {d["column"]:<34} 0% on '
                  f'{",".join(d["empty_on"]):<22} live on '
                  f'{",".join(d["populated_on"])}')
        if len(f['contrast']) > len(_shown):
            _rest = collections.Counter()
            for d in f['contrast'][len(_shown):]:
                for sp in d['empty_on']:
                    _rest[sp] += 1
            print(f'     · … and {len(f["contrast"]) - len(_shown)} more '
                  f'({", ".join(f"{sp} {n}" for sp, n in _rest.most_common())})'
                  f' — run audit_context_column_fill.py --contrast-limit 0')

    if f['unbuilt']:
        print(f'\n  · unbuilt everywhere ({len(f["unbuilt"])}): '
              f'{", ".join(x["column"] for x in f["unbuilt"][:14])}'
              + (' …' if len(f['unbuilt']) > 14 else ''))
        print('    0% on every sport = not wired up yet. Backlog, not an '
              'incident.')

    if verbose and only_sport and only_sport in sports:
        print(f'\n  full fill for {only_sport}:')
        for col, fl in sorted(sports[only_sport]['fill'].items(),
                              key=lambda kv: kv[1]['pct']):
            print(f'     {fl["pct"]:>5.1f}%  {fl["n"]:>4}/{fl["of"]:<4} {col}')

    _hard = [d for d in f['divergence'] if d['absent_on'] or d['zero_on']]
    if not (f['divergence'] or f['contrast']):
        print('\n  ✅ contract satisfied everywhere, and no '
              'zero-fill-with-a-live-sibling')
    return 2 if (_hard or f['contrast']) else (1 if f['divergence'] else 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days-back', type=int, default=0,
                    help='include this many days before today (default 0)')
    ap.add_argument('--limit', type=int, default=400,
                    help='max forward rows per sport')
    ap.add_argument('--sport', default=None, choices=sorted(CTX_TABLES))
    ap.add_argument('--verbose', action='store_true',
                    help='with --sport, print every column fill %%')
    ap.add_argument('--contrast-limit', type=int, default=12,
                    help='cap the zero-fill list (0 = no cap); the COUNT is '
                         'always printed in full')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    if a.json:
        scanned = scan(days_back=a.days_back, limit=a.limit)
        print(json.dumps({'scan': scanned, 'findings': findings(scanned)},
                         indent=2, default=str))
        return 0
    return report(days_back=a.days_back, limit=a.limit, verbose=a.verbose,
                  only_sport=a.sport, contrast_limit=a.contrast_limit)


if __name__ == '__main__':
    sys.exit(main())
