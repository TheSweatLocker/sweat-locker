"""Grade public_receipts rows that carry no result from their source.

Most surfaces inherit a grade from the table they were reconstructed
from. Two do not:

  * sharp_card — its cached payload has no `result` field at all
    (410 receipts, 0 graded).
  * anything captured live at publish time, which by definition has no
    outcome yet.

So this grades game-level markets (ml / rl / total) directly against
<sport>_game_results. It is deliberately generic — surface-agnostic and
sport-agnostic — because Ladder and every future live-captured surface
lands in exactly the same state.

JOIN KEY
Only 16 of 410 sharp_card receipts carry a game_id; the field was added
to card items on 2026-09-17. All 410 carry a matchup. So the join is
(sport, game_date, matchup) with game_id used when present. Matchups are
normalised before comparison because the card writes "Away @ Home" and
the results table stores the teams separately.

TEAM MATCHING
Full-name containment, longest match wins. A substring match cost a real
grade once already: "Iowa" matched inside "Northern Iowa" and graded a
-38.5 favourite as a loss on a 55-0 win. A pick whose side cannot be
identified unambiguously is left UNGRADED, never guessed.

IDEMPOTENT: only touches rows where result IS NULL.

CLI
  python grade_public_receipts.py --dry-run
  python grade_public_receipts.py --surface sharp_card
  python grade_public_receipts.py --days 30
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _l in _env.read_text().split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY'))
if not (SB and KEY):
    sys.exit('SUPABASE_URL / SUPABASE_KEY not set')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

RESULTS_TABLE = {
    'MLB': 'mlb_game_results',
    'NFL': 'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'NBA': 'nba_game_results',
    'NHL': 'nhl_game_results',
    'NCAAB': 'ncaab_game_results',
}

# close_spread sign is NOT consistent across sports. Verified empirically
# 2026-09-20: NCAAF/MLB store NEGATIVE = home favored; NFL stores
# POSITIVE = home favored. Getting this backwards silently inverts every
# spread grade. See project_close_spread_sign_bug_914.
HOME_FAV_IS_NEGATIVE = {'MLB': True, 'NCAAF': True, 'NCAAB': True,
                        'NBA': True, 'NHL': True, 'NFL': False}

GAME_MARKETS = {'ml', 'rl', 'spread', 'total'}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm(s: str) -> str:
    return re.sub(r'[^a-z0-9 ]', '', str(s or '').lower()).strip()


def paged(url: str, page: int = 1000):
    off = 0
    while off < 60000:
        r = requests.get(url + f'&limit={page}&offset={off}', headers=H_READ, timeout=60)
        if r.status_code != 200:
            print(f'  ⚠ fetch {r.status_code}: {r.text[:200]}')
            return
        rows = r.json()
        if not rows:
            return
        for x in rows:
            if isinstance(x, dict):
                yield x
        if len(rows) < page:
            return
        off += page


def pick_side(label: str, home: str, away: str) -> str | None:
    """Which side the label names. Longest full-name match wins; ties or
    no match return None so the caller leaves it ungraded."""
    lab = _norm(label)
    h, a = _norm(home), _norm(away)
    hit_h = bool(h) and h in lab
    hit_a = bool(a) and a in lab
    if hit_h and hit_a:
        # Both appear (e.g. a label echoing the matchup) — prefer the
        # longer name, which is the more specific match.
        return 'HOME' if len(h) > len(a) else ('AWAY' if len(a) > len(h) else None)
    if hit_h:
        return 'HOME'
    if hit_a:
        return 'AWAY'
    # Nickname fallback: last word of the team name ("Guardians").
    hw, aw = h.split()[-1] if h else '', a.split()[-1] if a else ''
    hit_h = bool(hw) and re.search(rf'\b{re.escape(hw)}\b', lab) is not None
    hit_a = bool(aw) and re.search(rf'\b{re.escape(aw)}\b', lab) is not None
    if hit_h and not hit_a:
        return 'HOME'
    if hit_a and not hit_h:
        return 'AWAY'
    return None


def grade_one(rec: dict, res: dict, sport: str) -> tuple[str | None, str]:
    """-> (result, reason). result None means leave ungraded."""
    market = str(rec.get('market') or '').lower()
    hs, as_ = _f(res.get('home_score')), _f(res.get('away_score'))
    if hs is None or as_ is None:
        return None, 'no final score'
    home, away = res.get('home_team'), res.get('away_team')
    label = rec.get('pick_label') or ''

    if market == 'ml':
        side = pick_side(label, home, away)
        if not side:
            return None, 'side unidentifiable'
        if hs == as_:
            return 'PUSH', 'tie'
        won = (hs > as_) if side == 'HOME' else (as_ > hs)
        return ('WIN' if won else 'LOSS'), f'{side} ml'

    if market in ('rl', 'spread'):
        side = pick_side(label, home, away)
        if not side:
            return None, 'side unidentifiable'
        # The LABEL is authoritative for the sign, not pick_line.
        #
        # Verified on live data: receipt "Arizona Diamondbacks -1.5"
        # stores pick_line = +1.5. The stored magnitude is right and the
        # stored sign is not. Deriving the sign from the side instead
        # (-abs for HOME) happens to work for a home FAVOURITE and grades
        # a home UNDERDOG (+1.5) exactly backwards — and MLB run lines
        # are +1.5 on the dog roughly half the time.
        #
        # So parse the signed number the label actually shows and only
        # fall back to pick_line's magnitude when the label carries none.
        signed = None
        m_sign = re.search(r'([+-]\s*\d+(?:\.\d+)?)', str(label))
        if m_sign:
            signed = _f(m_sign.group(1).replace(' ', ''))
        line = _f(rec.get('pick_line'))
        cs = _f(res.get('close_spread'))
        if signed is None and line is None and cs is None:
            return None, 'no spread line'
        if signed is not None or line is not None:
            if signed is None:
                # No sign anywhere — refuse rather than assume a favourite.
                return None, 'spread sign unknown'
            # `signed` is the handicap applied to the PICKED side.
            picked_margin = (hs - as_) if side == 'HOME' else (as_ - hs)
            covered = picked_margin + signed
            if abs(covered) < 1e-9:
                return 'PUSH', 'push on the number'
            return ('WIN' if covered > 0 else 'LOSS'), f'{side} {signed:+g}'
        # Fall back to the book's closing spread.
        home_margin_needed = -cs if HOME_FAV_IS_NEGATIVE.get(sport, True) else cs
        margin = hs - as_
        diff = margin - home_margin_needed
        if abs(diff) < 1e-9:
            return 'PUSH', 'push on close'
        home_covered = diff > 0
        won = home_covered if side == 'HOME' else (not home_covered)
        return ('WIN' if won else 'LOSS'), f'{side} vs close'

    if market == 'total':
        line = _f(rec.get('pick_line')) or _f(res.get('close_total'))
        if line is None:
            return None, 'no total line'
        lab = _norm(label)
        want = 'OVER' if 'over' in lab else ('UNDER' if 'under' in lab else None)
        if want is None:
            return None, 'over/under unidentifiable'
        total = hs + as_
        if abs(total - line) < 1e-9:
            return 'PUSH', 'push on total'
        actual = 'OVER' if total > line else 'UNDER'
        return ('WIN' if actual == want else 'LOSS'), f'{want} {line:g}'

    return None, f'market {market!r} not gradeable here'


def run(surface: str | None, days: int, dry_run: bool) -> None:
    hi = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    lo = hi - timedelta(days=days)
    url = (f'{SB}/rest/v1/public_receipts?select=*&result=is.null'
           f'&game_date=gte.{lo}&game_date=lte.{hi}')
    if surface:
        url += f'&surface=eq.{surface}'
    recs = [r for r in paged(url)
            if str(r.get('market') or '').lower() in GAME_MARKETS]
    print(f'=== grade_public_receipts · {lo}..{hi} · '
          f'surface={surface or "ALL"} {"(DRY)" if dry_run else "(APPLY)"} ===')
    print(f'  ungraded game-market receipts: {len(recs)}')
    if not recs:
        return

    # Build a results index per sport.
    idx: dict = {}
    for sport in sorted({str(r.get('sport') or '').upper() for r in recs}):
        tbl = RESULTS_TABLE.get(sport)
        if not tbl:
            print(f'  ⚠ {sport}: no results table mapping — skipped')
            continue
        by_gid, by_match = {}, {}
        got = 0
        for row in paged(f'{SB}/rest/v1/{tbl}?select=game_id,game_date,home_team,'
                         f'away_team,home_score,away_score,close_spread,close_total'
                         f'&game_date=gte.{lo}&game_date=lte.{hi}'):
            got += 1
            if row.get('game_id'):
                by_gid[row['game_id']] = row
            key = (row.get('game_date'),
                   _norm(row.get('away_team')), _norm(row.get('home_team')))
            by_match[key] = row
        idx[sport] = (by_gid, by_match)
        print(f'  {sport}: {got} result rows indexed')

    out = Counter()
    reasons = Counter()
    patches = []
    for rec in recs:
        sport = str(rec.get('sport') or '').upper()
        if sport not in idx:
            out['no_table'] += 1
            continue
        by_gid, by_match = idx[sport]
        res = by_gid.get(rec.get('game_id')) if rec.get('game_id') else None
        if res is None:
            m = str(rec.get('matchup') or '')
            if '@' in m:
                a, h = m.split('@', 1)
                res = by_match.get((rec.get('game_date'), _norm(a), _norm(h)))
        if res is None:
            out['no_result_row'] += 1
            continue
        grade, why = grade_one(rec, res, sport)
        if grade is None:
            out['ungradeable'] += 1
            reasons[why] += 1
            continue
        out[grade] += 1
        patches.append((rec['id'], grade))

    print(f'\n  graded: {dict(out)}')
    if reasons:
        print(f'  ungradeable reasons: {dict(reasons)}')
    if dry_run or not patches:
        print(f'  [DRY] would patch {len(patches)} receipts' if dry_run else '')
        return
    now = datetime.now(timezone.utc).isoformat()
    ok = fail = 0
    for rid, grade in patches:
        r = requests.patch(f'{SB}/rest/v1/public_receipts?id=eq.{rid}',
                           headers=H_WRITE,
                           json={'result': grade, 'graded_at': now}, timeout=20)
        if r.status_code in (200, 204):
            ok += 1
        else:
            fail += 1
            if fail <= 3:
                print(f'  ⚠ patch {rid}: {r.status_code} {r.text[:120]}')
    print(f'  patched {ok}, failed {fail}')


# Source tables a prop receipt can inherit its grade from, as
# source_table -> (rest table, id column, result column).
# 2026-09-21: props were excluded from this grader entirely — run() filters
# to GAME_MARKETS — so 1,326 prop receipts sat at result=NULL even though
# their source table had already graded them (prop_jerry_reads was 690/728
# for 09-20). Nothing was broken upstream; the receipts just never asked.
PROP_INHERIT = {
    'prop_jerry_reads': ('prop_jerry_reads', 'id', 'result'),
}

# public_receipts.result already carries NO_ACTION as a real outcome (622
# rows), so a prop that never actioned — scratched starter, voided line —
# gets recorded rather than left NULL forever. Mapping these to PUSH would
# be worse than leaving them: a push is a tie that returns the stake, a void
# is a bet that never existed, and folding one into the other overstates the
# denominator on every surface record that counts pushes.
_RESULT_MAP = {
    'win': 'WIN', 'loss': 'LOSS', 'push': 'PUSH',
    'no_action': 'NO_ACTION', 'void': 'NO_ACTION', 'voided': 'NO_ACTION',
}

# "Shane Baz Under 17.5 OUTS" -> player / direction / line / stat
_SHARP_PROP_LABEL = re.compile(r'^(.+?)\s+(Over|Under)\s+([\d.]+)\s+(.+)$', re.I)
# Label suffix -> prop_type stem in <sport>_pipeline_props. All 266 sharp_card
# prop receipts parse against this; these are pitcher props, so KS maps to
# 'ks' and never 'batter_ks'.
_SHARP_PROP_STAT = {
    'OUTS': 'outs', 'HA': 'ha', 'KS': 'ks',
    'BB': 'bb', 'ER': 'er', 'HITS': 'hits',
}
PROPS_TABLE = {'MLB': 'mlb_pipeline_props', 'NFL': 'nfl_pipeline_props'}


def _grade_sharp_card_props(recs: list, patches: list, skipped: Counter) -> None:
    """Grade Sharp card prop receipts by matching back to the props table.

    These 266 receipts were captured without player_name / prop_type /
    pick_side — a capture defect in the card adapter — so they cannot
    inherit by id like prop_jerry_reads does. But pick_label survived intact
    ("Shane Baz Under 17.5 OUTS") and carries everything needed, so the
    identity is recoverable from the label plus (sport, game_date).

    Matches on the exact tuple the resolver itself keys on
    (player_name, prop_type, prop_line) and requires a UNIQUE hit — a
    player can hold two lines for the same stat, and grading the wrong one
    would put a fabricated result on a published receipt.
    """
    by_sport_date: dict = {}
    parsed = []
    for rec in recs:
        m = _SHARP_PROP_LABEL.match(str(rec.get('pick_label') or '').strip())
        if not m:
            skipped['label_unparseable'] += 1
            continue
        player, direction, line, stat = (m.group(1).strip(), m.group(2).lower(),
                                         m.group(3), m.group(4).strip().upper())
        stem = _SHARP_PROP_STAT.get(stat)
        if not stem:
            skipped[f'stat_unmapped:{stat[:10]}'] += 1
            continue
        sport = str(rec.get('sport') or 'MLB').upper()
        tbl = PROPS_TABLE.get(sport)
        if not tbl:
            skipped[f'no_props_table:{sport}'] += 1
            continue
        parsed.append((rec, sport, tbl, player, f'{stem}_{direction}', _f(line)))
        by_sport_date.setdefault((sport, tbl), set()).add(rec.get('game_date'))

    # Pull each sport/date slice once rather than per receipt.
    index: dict = {}
    for (sport, tbl), dates in by_sport_date.items():
        for d in sorted(x for x in dates if x):
            for row in paged(f'{SB}/rest/v1/{tbl}'
                             f'?select=player_name,prop_type,prop_line,result'
                             f'&game_date=eq.{d}'):
                key = (sport, d, str(row.get('player_name') or '').strip().lower(),
                       str(row.get('prop_type') or '').lower(), _f(row.get('prop_line')))
                index.setdefault(key, []).append(row.get('result'))

    for rec, sport, tbl, player, prop_type, line in parsed:
        key = (sport, rec.get('game_date'), player.lower(), prop_type, line)
        hits = [h for h in (index.get(key) or []) if h]
        if not hits:
            skipped['no_prop_row_or_ungraded'] += 1
            continue
        if len(set(hits)) > 1:
            skipped['ambiguous_prop_match'] += 1
            continue
        norm = _RESULT_MAP.get(str(hits[0]).strip().lower())
        if not norm:
            skipped[f'unmapped:{str(hits[0])[:12]}'] += 1
            continue
        patches.append((rec['id'], norm))


def grade_props(days: int, dry_run: bool) -> None:
    """Inherit prop receipt grades from the table they were captured from.

    Props cannot be graded the way game markets are — there is no
    (home_score, away_score) to compare against, the outcome lives in the
    prop row itself. But that row IS graded, so this is a join, not a
    regrade. Deliberately never recomputes an outcome: the source table is
    the authority, and a second opinion here would be a way to disagree
    with our own published record.
    """
    hi = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    lo = hi - timedelta(days=days)
    recs = [r for r in paged(f'{SB}/rest/v1/public_receipts?select=*&result=is.null'
                             f'&market=eq.prop'
                             f'&game_date=gte.{lo}&game_date=lte.{hi}')]
    print(f'\n=== grade_props · {lo}..{hi} {"(DRY)" if dry_run else "(APPLY)"} ===')
    print(f'  ungraded prop receipts: {len(recs)}')
    if not recs:
        return

    by_src = Counter(str(r.get('source_table') or '?') for r in recs)
    print(f'  by source_table: {dict(by_src)}')

    patches = []
    skipped = Counter()
    for src, (tbl, idcol, rescol) in PROP_INHERIT.items():
        mine = [r for r in recs if str(r.get('source_table') or '') == src
                and r.get('source_id')]
        if not mine:
            continue
        want = {str(r['source_id']) for r in mine}
        # Pull the source rows by id in batches rather than one at a time.
        grades: dict = {}
        ids = sorted(want)
        for i in range(0, len(ids), 80):
            batch = ids[i:i + 80]
            r = requests.get(f'{SB}/rest/v1/{tbl}',
                             params={'select': f'{idcol},{rescol}',
                                     idcol: f'in.({",".join(batch)})',
                                     'limit': 1000},
                             headers=H_READ, timeout=30)
            if r.status_code != 200:
                print(f'  ⚠ {tbl} read {r.status_code}: {r.text[:120]}')
                continue
            for row in (r.json() or []):
                val = row.get(rescol)
                if val:
                    grades[str(row[idcol])] = str(val)
        for rec in mine:
            g = grades.get(str(rec['source_id']))
            if not g:
                skipped['source_row_ungraded'] += 1
                continue
            norm = _RESULT_MAP.get(g.strip().lower())
            if not norm:
                skipped[f'unmapped:{g[:12]}'] += 1
                continue
            patches.append((rec['id'], norm))

    # Sharp card props have no id to inherit from — recovered from pick_label.
    sharp = [r for r in recs
             if str(r.get('source_table') or '') == 'jerry_cache.sharp_card']
    if sharp:
        _grade_sharp_card_props(sharp, patches, skipped)

    other = [r for r in recs if str(r.get('source_table') or '') not in PROP_INHERIT
             and str(r.get('source_table') or '') != 'jerry_cache.sharp_card']
    if other:
        skipped['no_inherit_path'] = len(other)

    print(f'  gradeable: {len(patches)}')
    if skipped:
        print(f'  skipped: {dict(skipped)}')
    if dry_run or not patches:
        if dry_run:
            print(f'  [DRY] would patch {len(patches)} prop receipts')
        return
    now = datetime.now(timezone.utc).isoformat()
    ok = fail = 0
    for rid, grade in patches:
        r = requests.patch(f'{SB}/rest/v1/public_receipts?id=eq.{rid}',
                           headers=H_WRITE,
                           json={'result': grade, 'graded_at': now}, timeout=20)
        if r.status_code in (200, 204):
            ok += 1
        else:
            fail += 1
            if fail <= 3:
                print(f'  ⚠ patch {rid}: {r.status_code} {r.text[:120]}')
    print(f'  patched {ok}, failed {fail}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--surface')
    p.add_argument('--days', type=int, default=45)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--skip-props', action='store_true',
                   help='game markets only (props inherit by default)')
    a = p.parse_args()
    run(a.surface, a.days, a.dry_run)
    if not a.skip_props:
        grade_props(a.days, a.dry_run)


if __name__ == '__main__':
    main()
