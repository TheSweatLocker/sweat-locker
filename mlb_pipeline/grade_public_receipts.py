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

Two gaps in that join left 14 of 27 receipts ungraded on 2026-10-04 with
9 NFL games already final, and both are fixed here:

  1. TWO ID SPACES. `jerry_reads.game_id` is the Odds API event hash;
     the results tables key on `20261004_NYJ_CHI`. No bridge table
     exists, so a game_id that is *present but foreign* must fall through
     to the matchup join instead of being trusted.
  2. NFL SPELLS TEAMS TWICE. Reads say "New York Jets @ Chicago Bears",
     results say NYJ / CHI, so the matchup join missed every NFL game
     even when a matchup was present. Team keys now go through
     nfl_teams.canon, which returns None rather than guessing.

And `public_receipts.matchup` is NULL on every game_read row (the writer
left it for "downstream enrichment"), so when the receipt has no matchup
we read it from the source row's `input_snapshot.matchup`. That keeps
grading working on the ~1.4k already-written receipts without rewriting
a claim column.

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
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
try:
    from urllib3.util.retry import Retry
except ImportError:
    Retry = None

from nfl_teams import canon as nfl_canon

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

# 2026-09-26: hockey does not have a column called close_spread — its line
# is stored as close_puckline. The results SELECT below hardcoded
# close_spread for every sport, so the NHL fetch returned 42703 and the
# sport indexed ZERO result rows. PostgREST rejects the whole query on one
# unknown column, so this did not degrade to "spreads missing" — it took
# down the entire NHL branch, and today's run printed:
#
#   ⚠ fetch 400: column nhl_game_results.close_spread does not exist
#   NHL: 0 result rows indexed
#
# Confirmed against real receipts: four NHL game reads from 09-25 sat
# ungraded with finished games. NHL opens 10-08, so this would have
# silently voided grading for the entire first week.
#
# Aliased rather than renamed downstream: PostgREST returns the alias as
# the key, so res.get('close_spread') keeps working for every sport and
# the sign-handling logic below stays untouched.
SPREAD_COL = {'NHL': 'close_spread:close_puckline'}
DEFAULT_SPREAD_COL = 'close_spread'

# close_spread sign is NOT consistent across sports. Verified empirically
# 2026-09-20: NCAAF/MLB store NEGATIVE = home favored; NFL stores
# POSITIVE = home favored. Getting this backwards silently inverts every
# spread grade. See project_close_spread_sign_bug_914.
HOME_FAV_IS_NEGATIVE = {'MLB': True, 'NCAAF': True, 'NCAAB': True,
                        'NBA': True, 'NHL': True, 'NFL': False}

GAME_MARKETS = {'ml', 'rl', 'spread', 'total'}

# 2026-10-01: vocabulary that means "this label describes a PLAYER prop", used
# to refuse grading such a receipt as a game market. Kept deliberately broad
# and cross-sport — a false refusal leaves a row ungraded and visible, while a
# false grade enters the published record and is invisible.
_PROP_LABEL_RE = re.compile(
    r'\b(outs?|strikeouts?|ks|hits?|total\s+bases|rbis?|runs\s+scored|walks?|'
    r'earned\s+runs?|saves?|points?|rebounds?|assists?|pass(?:ing)?\s+yds?|'
    r'rush(?:ing)?\s+yds?|rec(?:eiving)?\s+yds?|receptions?|shots?|goals?|'
    r'sog|saves|blocks|steals|threes|3pt)\b', re.I)

# 2026-09-26: POTD / Dawg receipts store a SURFACE code in `market` rather
# than a real market -- deliberately, so rows already counted under those
# codes stay countable (see public_receipt._bet_market). The consequence was
# that they never passed the GAME_MARKETS filter in run(), so they were
# dropped BEFORE grade_one ever saw them and sat ungraded forever. Andy found
# a POTD (Chicago White Sox ML, conviction 88) ungraded while the identical
# pick graded WIN as a game_read on the same slate; the White Sox won 6-1.
#
# The real market is already recorded in audit.bet_market for exactly this
# purpose. Resolve it here so countability and gradeability stop conflicting.
SURFACE_MARKETS = {'potd', 'dawg', 'dotd'}


# 2026-10-04: the SAME play can be captured on two surfaces by two writers,
# and only one of them stamps audit.bet_market. Today's POTD was written
# twice — identical matchup, identical label "BUF RL -7 (Jerry 70/100)",
# identical source_id — but jerry_anchor_potd stamped bet_market='rl' while
# generate_sweat_card did not. So the potd-surface row graded LOSS and its
# sweat_card twin sat ungraded: one play, two surfaces, two different states
# on screen.
#
# Rather than depend on every writer remembering the stamp, infer the market
# from the label as a last resort. Deliberately narrow: the word has to be
# there, and a label that reads like a PLAYER prop is refused outright, since
# "Aaron Nola Over 11.5 Outs" would otherwise look like a game total. An
# unidentifiable label keeps the surface code and stays visibly ungraded.
_LABEL_MARKET = (
    (re.compile(r'\b(rl|ats|spread|puck\s*line|run\s*line)\b', re.I), 'rl'),
    (re.compile(r'\b(ml|moneyline)\b', re.I), 'ml'),
    (re.compile(r'^\s*(over|under)\b', re.I), 'total'),
)


def _market_from_label(label):
    s = str(label or '')
    if not s or _PROP_LABEL_RE.search(s):
        return None
    for rx, mkt in _LABEL_MARKET:
        if rx.search(s):
            return mkt
    return None


def _effective_market(rec):
    """Real market for grading; surface codes resolve via audit.bet_market."""
    m = str(rec.get('market') or '').lower()
    if m not in SURFACE_MARKETS:
        return m
    a = rec.get('audit')
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except Exception:
            a = None
    bm = (a or {}).get('bet_market') if isinstance(a, dict) else None
    if bm:
        return str(bm).lower()
    return _market_from_label(rec.get('pick_label')) or m

# 2026-09-21: grading the prop backlog issues ~1,000 sequential PATCHes and
# the host reset the connection partway through — and because each patch was
# its own new socket, the run died having written NOTHING while still exiting
# 0. Pool the connections and retry transient failures. (Same failure hit
# classify_line_moves on backfill; worth remembering that any loop doing
# hundreds of per-row writes needs this.)
_SESSION = requests.Session()
if Retry is not None:
    _retry = Retry(total=4, backoff_factor=0.5,
                   status_forcelist=(500, 502, 503, 504, 429),
                   allowed_methods=frozenset(['GET', 'PATCH']))
    _SESSION.mount('https://', HTTPAdapter(max_retries=_retry,
                                           pool_connections=8, pool_maxsize=8))


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm(s: str) -> str:
    return re.sub(r'[^a-z0-9 ]', '', str(s or '').lower()).strip()


def _team_key(sport: str, name: str) -> str:
    """Team key for the (date, away, home) join.

    NFL stores abbreviations in the results tables while reads carry full
    names, so NFL goes through the canonicaliser. `canon` returns None on
    anything it cannot resolve; falling back to _norm there keeps the key
    stable and simply fails to match, which is the safe outcome.
    """
    if str(sport or '').upper() == 'NFL':
        ab = nfl_canon(name)
        if ab:
            return ab
    return _norm(name)


def _match_key(sport: str, game_date, away: str, home: str) -> tuple:
    return (game_date, _team_key(sport, away), _team_key(sport, home))


def _src_key(rec: dict) -> tuple:
    return (str(rec.get('source_table') or ''), str(rec.get('source_id') or ''))


# Tables whose rows hold the matchup inside a JSON snapshot rather than in a
# column of their own. Value is the PostgREST path to pull it from.
_SNAPSHOT_MATCHUP = {'jerry_reads': 'input_snapshot->>matchup'}


def _source_matchups(recs: list) -> dict:
    """Recover "Away @ Home" from source rows for receipts missing a matchup.

    Reads only — this does not write back to public_receipts. The matchup is a
    claim column and backfilling it is a separate, auditable job; grading must
    not depend on having run it.
    """
    want = defaultdict(set)
    for rec in recs:
        if '@' in str(rec.get('matchup') or ''):
            continue
        tbl = str(rec.get('source_table') or '')
        sid = str(rec.get('source_id') or '')
        if tbl in _SNAPSHOT_MATCHUP and sid:
            want[tbl].add(sid)
    found = {}
    for tbl, ids in want.items():
        path = _SNAPSHOT_MATCHUP[tbl]
        ids = sorted(ids)
        for i in range(0, len(ids), 200):
            chunk = ','.join(ids[i:i + 200])
            r = _SESSION.get(f'{SB}/rest/v1/{tbl}', headers=H_READ, timeout=60,
                             params={'select': f'id,matchup:{path}',
                                     'id': f'in.({chunk})', 'limit': 1000})
            if r.status_code != 200:
                print(f'  ⚠ matchup recovery from {tbl} failed: '
                      f'{r.status_code} {r.text[:120]}')
                continue
            for row in r.json():
                m = row.get('matchup')
                if m and '@' in str(m):
                    found[(tbl, str(row['id']))] = str(m)
    return found


def paged(url: str, page: int = 1000):
    off = 0
    while off < 60000:
        r = _SESSION.get(url + f'&limit={page}&offset={off}', headers=H_READ, timeout=60)
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
    market = _effective_market(rec)
    # ══ 2026-09-26 · SURFACE CODES ARE NOT MARKETS ══
    # POTD and Dawg receipts carry market='potd' / 'dawg' — a SURFACE code,
    # deliberately, so the rows already counted under those codes stay
    # countable (see public_receipt._bet_market). But grade_one only knows
    # ml / rl / total, so every one of them fell through all three branches
    # and stayed ungraded forever. Andy found a POTD (Chicago White Sox ML,
    # conviction 88) sitting ungraded while the SAME pick graded WIN as a
    # game_read on the same slate.
    #
    # public_receipt already records the real market in audit.bet_market
    # precisely for this. Use it for grading and leave the `market` column
    # alone, so countability and gradeability stop being in conflict.
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
        # ══ 2026-10-01 · A PLAYER PROP IS NOT A GAME TOTAL ══
        # `line = pick_line or res.close_total` silently substituted the GAME's
        # closing run total whenever the receipt had no line of its own. POTD
        # receipts written by jerry_anchor_potd carry bet_market='total' with
        # player_name / prop_type / pick_line ALL NULL, so a pitcher-outs POTD
        # was graded against runs scored.
        #
        # Two published POTDs were FALSE WINS because of it, both verified
        # against the box score:
        #   9/22 JR Ritchie Under 14.5 Outs  -> threw 15 outs = LOSS, read WIN
        #   9/30 Hunter Brown Over 14.5 Outs -> threw  2 outs = LOSS, read WIN
        # Two others graded correctly only by coincidence, because the game
        # total happened to agree with the prop outcome.
        #
        # Substituting the line is not recoverable by picking a better number:
        # outs and runs are different units, so NO line makes this comparison
        # valid. Refuse instead. These rows then stay ungraded, which is
        # honest and visible, rather than graded wrong, which is neither.
        # Real fix belongs upstream in jerry_anchor_potd, which should record
        # the prop's market, player, type and line (feedback_fix_at_root).
        if _PROP_LABEL_RE.search(str(label or '')):
            return None, 'prop label on a game market — refusing to grade as a total'
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


def run(surface: str | None, days: int, dry_run: bool,
        verbose: bool = False) -> None:
    hi = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    lo = hi - timedelta(days=days)
    url = (f'{SB}/rest/v1/public_receipts?select=*&result=is.null'
           f'&game_date=gte.{lo}&game_date=lte.{hi}')
    if surface:
        url += f'&surface=eq.{surface}'
    recs = [r for r in paged(url)
            if _effective_market(r) in GAME_MARKETS]
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
        spread_col = SPREAD_COL.get(sport, DEFAULT_SPREAD_COL)
        for row in paged(f'{SB}/rest/v1/{tbl}?select=game_id,game_date,home_team,'
                         f'away_team,home_score,away_score,{spread_col},close_total'
                         f'&game_date=gte.{lo}&game_date=lte.{hi}'):
            got += 1
            if row.get('game_id'):
                by_gid[row['game_id']] = row
            by_match[_match_key(sport, row.get('game_date'),
                                row.get('away_team'),
                                row.get('home_team'))] = row
        idx[sport] = (by_gid, by_match)
        print(f'  {sport}: {got} result rows indexed')

    # public_receipts.matchup is NULL on every game_read row — the writer left
    # it for "downstream enrichment" that never ran. The source row carries it
    # inside input_snapshot, so fetch it for exactly the receipts that need it.
    src_matchup = _source_matchups(recs)
    if src_matchup:
        print(f'  matchups recovered from source rows: {len(src_matchup)}')

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
            # A game_id can be present but belong to a different ID space
            # (Odds API hash vs nflverse key), so always try the team join.
            m = str(rec.get('matchup') or '')
            if '@' not in m:
                m = src_matchup.get(_src_key(rec)) or ''
            if '@' in m:
                a, h = m.split('@', 1)
                res = by_match.get(_match_key(sport, rec.get('game_date'), a, h))
        if res is None:
            out['no_result_row'] += 1
            continue
        grade, why = grade_one(rec, res, sport)
        if grade is None:
            out['ungradeable'] += 1
            reasons[why] += 1
            if verbose and why != 'no final score':
                print(f'      SKIP {rec.get("sport"):<4} {rec.get("surface"):<11} '
                      f'{str(rec.get("pick_label"))[:28]:<28} {why}')
            continue
        out[grade] += 1
        if verbose:
            # Every grade printed with the score it was derived from, so a
            # human can audit the day without re-querying anything.
            print(f'      {grade:<5} {rec.get("game_date")} {rec.get("sport"):<4} '
                  f'{rec.get("surface"):<11} {rec.get("market"):<6} '
                  f'{str(rec.get("pick_label"))[:26]:<26} '
                  f'{res.get("away_team")} {res.get("away_score")}-'
                  f'{res.get("home_score")} {res.get("home_team")}')
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
        r = _SESSION.patch(f'{SB}/rest/v1/public_receipts?id=eq.{rid}',
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


def _grade_sweat_card_props(recs: list, patches: list, skipped: Counter) -> None:
    """Grade Sweat card prop receipts by parsing their packed source_id.

    2026-10-04. Andy: the Sweat Card recap never resolved Ohtani and Kim, and
    both had WON — Ohtani hits UNDER 1.5 finished on 1, Kim UNDER 0.5 on 0.

    These rows carry source_table='mlb_pipeline_props', so grade_props tries to
    inherit by id — but their source_id is not an id, it is the packed string
    'prop:Shohei Ohtani|hits_under|1.5'. The lookup misses and every one lands
    in source_row_ungraded, silently. Their player_name / prop_type / pick_line
    / pick_side are all NULL, so nothing else can match them either.

    THE WRITER IS ALSO FIXED (public_receipt.sweat_card_rows now populates
    those columns), but that only reaches NEW receipts: capture() upserts with
    resolution=ignore-duplicates and the table REFUSES updates to claim
    columns — verified, a PATCH returns 200 with the row unchanged. Both are
    correct: a receipt is a claim and a re-run must not rewrite it. So the
    already-written rows can only ever be graded by reading what they already
    carry, which is what this does.

    Same discipline as _grade_sharp_card_props: match the exact tuple the
    resolver keys on and require a UNIQUE result, because a player can hold
    two lines for one stat and grading the wrong one puts a fabricated result
    on a published receipt.
    """
    by_sport_date: dict = {}
    parsed = []
    for rec in recs:
        sid = str(rec.get('source_id') or '')
        if not sid.startswith('prop:'):
            skipped['sweat_sid_not_prop'] += 1
            continue
        parts = sid[len('prop:'):].split('|')
        if len(parts) != 3:
            skipped['sweat_sid_unparseable'] += 1
            continue
        player, prop_type, raw_line = (p.strip() for p in parts)
        line = _f(raw_line)
        if not player or not prop_type or line is None:
            skipped['sweat_sid_incomplete'] += 1
            continue
        # ══ THE LABEL AND THE SOURCE MUST AGREE ON THE NUMBER ══
        # 10-03 carried "Tarik Skubal Over 7.5 Ks" pointing at
        # source_id prop:Tarik Skubal|ks_over|6.5. He threw 7 — a WIN at one
        # line and a LOSS at the other. Grading from either side would assert
        # something nobody can verify, on a PUBLISHED receipt, and would do it
        # silently. The label is what the user saw; the source_id is what we
        # linked. When they disagree the receipt itself is the defect, and a
        # human has to say which number was actually published.
        _m = re.search(r'(\d+(?:\.\d+)?)', str(rec.get('pick_label') or ''))
        if _m:
            _shown = _f(_m.group(1))
            if _shown is not None and abs(_shown - line) > 1e-9:
                skipped['sweat_label_line_mismatch'] += 1
                print(f'    ⚠ {str(rec.get("pick_label"))[:44]!r}: label says '
                      f'{_shown}, source_id says {line} — NOT graded')
                continue
        sport = str(rec.get('sport') or 'MLB').upper()
        tbl = PROPS_TABLE.get(sport)
        if not tbl:
            skipped[f'no_props_table:{sport}'] += 1
            continue
        parsed.append((rec, sport, tbl, player, prop_type.lower(), line))
        by_sport_date.setdefault((sport, tbl), set()).add(rec.get('game_date'))

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
            skipped['sweat_no_prop_row_or_ungraded'] += 1
            continue
        if len(set(hits)) > 1:
            skipped['sweat_ambiguous_prop_match'] += 1
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
            r = _SESSION.get(f'{SB}/rest/v1/{tbl}',
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

    # Sweat card props claim source_table='mlb_pipeline_props' but their
    # source_id is the packed 'prop:player|type|line', not an id — so the
    # inherit above silently misses every one. Recovered from that string.
    _sw_done = {r['id'] for r in sharp}
    sweat = [r for r in recs
             if r['id'] not in _sw_done
             and str(r.get('source_id') or '').startswith('prop:')]
    if sweat:
        _grade_sweat_card_props(sweat, patches, skipped)
    _sw_done |= {r['id'] for r in sweat}

    other = [r for r in recs
             if r['id'] not in _sw_done
             and str(r.get('source_table') or '') not in PROP_INHERIT]
    if other:
        skipped['no_inherit_path'] = len(other)

    # 2026-09-25: BOX-SCORE FALLBACK. Everything above inherits a result from
    # the source row, which is exactly the design that stranded 618 receipts —
    # prop_jerry_reads and mlb_pipeline_props are pruned, and an immutable
    # ledger cannot depend on a mutable one. Anything the inherit could not
    # resolve now settles from the MLB box score using only what the receipt
    # itself carries, so a pruned source is no longer fatal.
    #
    # One implementation, one scheduled job: settle_prop_receipts owns the
    # box-score logic and the FADE guard, and is imported here rather than
    # duplicated. It stays runnable standalone for backfills and for its
    # --validate mode.
    done_ids = {rid for rid, _ in patches}
    remaining = [r for r in recs if r['id'] not in done_ids]
    if remaining:
        try:
            from settle_prop_receipts import (
                settle as _settle, fadeable_families as _fadeable,
                source_verdicts as _verdicts,
            )
            sports = {str(r.get('sport') or 'MLB') for r in remaining}
            for sp in sports:
                mine = [r for r in remaining if str(r.get('sport') or 'MLB') == sp]
                fam = _fadeable(sp, (lo - timedelta(days=120)).isoformat())
                vmap = _verdicts(mine)
                for r in mine:
                    res, _actual, why = _settle(r, fam, vmap)
                    if res:
                        patches.append((r['id'], res))
                        skipped['settled_from_boxscore'] += 1
                    else:
                        skipped[f'boxscore:{why}'] += 1
        except Exception as e:
            # Never let the fallback take down the inherit pass that works.
            print(f'  ⚠ box-score fallback unavailable: {type(e).__name__}: {e}')

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
        r = _SESSION.patch(f'{SB}/rest/v1/public_receipts?id=eq.{rid}',
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
    p.add_argument('--verbose', '-v', action='store_true',
                   help='print every grade with the score it came from')
    a = p.parse_args()
    run(a.surface, a.days, a.dry_run, a.verbose)
    if not a.skip_props:
        grade_props(a.days, a.dry_run)


if __name__ == '__main__':
    main()
