"""NCAAB post-game resolver — pull ESPN scoreboard, refresh scores,
compute outcomes, then kick the sport-agnostic external picks grader.

Runs nightly during CBB season on the NCAAB workflow cron. Uses ESPN's
free public scoreboard endpoint (no auth) as the score source of truth
— CFB has CFBD, MLB has stats.mlb, CBB has no equivalent free feed so
ESPN it is.

Flow:
  1. Iterate a window of dates (default: yesterday + today, catches
     west-coast late finals + any pending scores).
  2. For each date, pull ESPN /scoreboard?dates=YYYYMMDD.
  3. Match each game's teams to canonical names via ncaab_team_aliases
     (espn_id primary, location fallback).
  4. Compute game_id = ncaab_YYYYMMDD_AWAY_HOME to match odds_pull.
  5. PATCH ncaab_game_results with home_score/away_score/spread_result/
     total_result/home_win.
  6. Kick resolve_externals.py --sport NCAAB to grade any external picks.

Idempotent — only writes when scores changed.

USAGE:
    python resolve_ncaab_results.py                 # yesterday + today
    python resolve_ncaab_results.py --days-back 7   # last 7 days
    python resolve_ncaab_results.py --date 20250301 # specific date
    python resolve_ncaab_results.py --dry-run
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional
import requests
from dotenv import load_dotenv
from sweat_dates import et_and_utc_stamps  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
SB_KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

ESPN_BASE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball'
GAP_LOG_PATH = Path(__file__).parent / 'ncaab_alias_gaps.log'


def _f(v):
    try: return float(v) if v not in (None, '') else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(float(v)) if v not in (None, '') else None
    except (TypeError, ValueError): return None


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


# ─── alias loading ────────────────────────────────────────────────

def load_alias_lookups() -> tuple[dict, dict]:
    """Returns (by_espn_id, by_name) lookup dicts.
      by_espn_id: {espn_id_str: canonical_name}  — primary matcher (bulletproof)
      by_name:    {name.lower(): canonical_name} — fallback for teams
                  not yet enriched with espn_id (e.g., recent D1 arrivals
                  that get added via scoreboard pull first)
    """
    r = requests.get(
        f'{SB}/rest/v1/ncaab_team_aliases'
        '?select=canonical_name,odds_api_name,alt_names,espn_id',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200:
        print(f'  ⚠ alias fetch failed: {r.status_code}')
        return {}, {}
    by_espn, by_name = {}, {}
    for row in r.json():
        canon = row['canonical_name']
        if row.get('espn_id'):
            by_espn[str(row['espn_id'])] = canon
        by_name[canon.lower()] = canon
        if row.get('odds_api_name'):
            by_name[row['odds_api_name'].lower()] = canon
        for alt in (row.get('alt_names') or []):
            if alt: by_name[alt.lower()] = canon
    return by_espn, by_name


def resolve_team(espn_team: dict, by_espn: dict, by_name: dict) -> Optional[str]:
    """espn_id primary, then location/displayName/shortDisplayName fallbacks."""
    eid = str(espn_team.get('id')) if espn_team.get('id') else None
    if eid and eid in by_espn:
        return by_espn[eid]
    for k in ('location', 'displayName', 'shortDisplayName', 'name'):
        v = espn_team.get(k)
        if v and v.lower() in by_name:
            return by_name[v.lower()]
    return None


def _log_gap(entry: dict) -> None:
    """Append an unresolved-team record to the gap audit for manual review."""
    try:
        with GAP_LOG_PATH.open('a', encoding='utf-8') as f:
            f.write(f'{datetime.now(timezone.utc).isoformat()} RESOLVE_GAP '
                    f'{json.dumps(entry)}\n')
    except Exception:
        pass


# ─── ESPN scoreboard ──────────────────────────────────────────────

def fetch_espn_scoreboard(date_str: str) -> list:
    """Returns list of ESPN 'events' for a given YYYYMMDD."""
    # groups=50 = full D1 coverage (Phase 1b fix — was missing ~85% of games).
    url = f'{ESPN_BASE}/scoreboard?dates={date_str}&groups=50&limit=500'
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
        r.raise_for_status()
        return r.json().get('events', []) or []
    except Exception as e:
        print(f'  ⚠ ESPN scoreboard {date_str}: {e}')
        return []


def event_to_outcome(event: dict, by_espn: dict, by_name: dict) -> Optional[dict]:
    """Extract (game_id, home_team, away_team, scores, status) from an ESPN event.
    Returns None if either team can't map or game is not yet final."""
    comp = (event.get('competitions') or [{}])[0]
    competitors = comp.get('competitors') or []
    if len(competitors) != 2:
        return None

    # ESPN: homeAway="home" | "away"
    home_c = next((c for c in competitors if c.get('homeAway') == 'home'), None)
    away_c = next((c for c in competitors if c.get('homeAway') == 'away'), None)
    if not (home_c and away_c):
        return None

    home_team = resolve_team(home_c.get('team', {}), by_espn, by_name)
    away_team = resolve_team(away_c.get('team', {}), by_espn, by_name)
    if not home_team or not away_team:
        # Log which side(s) failed so gaps are actionable
        _log_gap({
            'espn_event_id': event.get('id'),
            'date': event.get('date'),
            'home': home_c.get('team', {}).get('displayName'),
            'home_id': home_c.get('team', {}).get('id'),
            'home_resolved': home_team,
            'away': away_c.get('team', {}).get('displayName'),
            'away_id': away_c.get('team', {}).get('id'),
            'away_resolved': away_team,
        })
        return None

    home_score = _i(home_c.get('score'))
    away_score = _i(away_c.get('score'))
    _st = ((event.get('status') or {}).get('type') or {})
    status = _st.get('state')
    # ══ 2026-11-14 · ESPN USES 'post' FOR A POSTPONED GAME ════════════
    # `state == 'post'` is NOT "played". ESPN returns, verbatim, for three
    # games on 2026-11-14:
    #
    #   state=post  name=STATUS_POSTPONED  completed=False  scores=['0','0']
    #
    # So a postponed game arrived here as a FINAL with a 0-0 score, and
    # compute_outcome_patch below does not care how implausible a score is:
    # it wrote home_win=False, spread_result='away_covered' and
    # total_result='under' for games that were never played. Any pick on
    # one grades as a loss on a fabricated result.
    #
    # `type.completed` is the field ESPN actually means it with, so that is
    # what is read. The 0-0 clause is belt-and-braces: no basketball game
    # ends 0-0, so a 0-0 "final" is a status we have misread, not a result
    # ([[feedback_model_output_needs_a_plausibility_guard]]).
    #
    # Three rows had already been written this way and are repaired by
    # repair_postponed_zero_finals.py. The same `== 'post'` test is in
    # ncaab_backfill_results.py, ncaaf_results_espn.py and ufc_grader.py —
    # fixed together, because one writer patched is not a fix
    # ([[feedback_fix_every_writer_to_the_table]]).
    completed = bool(_st.get('completed')) and status == 'post'
    if completed and home_score == 0 and away_score == 0:
        completed = False

    commence = event.get('date', '')
    # ══ 2026-11-06 · R166: THE DATE MUST BE ET, NOT UTC ═══════════
    # A 7pm ET tip is 00:00 UTC the NEXT day, so `dt.date()` filed most
    # of the NCAAB slate a day late in BOTH game_date and the date
    # inside game_id. grade_public_receipts bridges a receipt to its
    # result on (game_date, away_team, home_team) -- the date is IN the
    # join key -- so every such receipt was ungradeable. Measured:
    # 2026-11-02 had 119 games in context and 30 rows here; 11-03 had 19
    # and 144.
    #
    # `legacy_game_id` is the old UTC-keyed id, carried so the
    # existing-row lookup can PATCH the row already stored under it
    # rather than insert a duplicate for the same game. Derivation is
    # shared in sweat_dates because three scrapers held three copies of
    # it ([[feedback_each_scraper_invented_its_own_season]]).
    game_date, _et, _utc = et_and_utc_stamps(commence)
    if not game_date:
        return None
    game_id = f'ncaab_{_et}_{away_team}_{home_team}'.replace(' ', '_')
    legacy_game_id = f'ncaab_{_utc}_{away_team}_{home_team}'.replace(' ', '_')

    return {
        'game_id': game_id,
        'legacy_game_id': legacy_game_id,
        'game_date': game_date,
        'home_team': home_team,
        'away_team': away_team,
        'home_score': home_score if completed else None,
        'away_score': away_score if completed else None,
        'completed': completed,
    }


# ─── DB layer ─────────────────────────────────────────────────────

#: PostgREST `in.(...)` is sent in the URL, and this stack silently
#: TRUNCATES a long one. Measured 2026-11-06 on real ids: 25 ids / 1,028
#: chars returned 200, 50 ids / 2,022 chars returned PGRST100 "unexpected
#: end of input expecting \",\" or \")\"". So a 100-id chunk NEVER parsed.
_ID_CHUNK = 20


def fetch_existing(game_ids: list) -> dict:
    """Stored rows for these game_ids, keyed by game_id.

    ══ 2026-11-06 · THIS COULD NEVER MATCH ANYTHING ═══════════════
    Two defects, and the second hid the first:

      1. it chunked at 100 ids, which builds a ~4KB URL that arrives
         TRUNCATED and 400s with PGRST100; and
      2. `if r.status_code != 200: continue` swallowed that 400, so the
         function returned {} and every game looked NEW.

    The resolver therefore never PATCHED a row in its life -- it only ever
    inserted, which is why 2026-11-03 holds 144 rows and a dry run on
    11-02 reported "existing rows matched: 0/165" while a direct probe for
    the very first id returned the row. A silent `continue` on a non-200
    turns a wiring error into a finding
    ([[feedback_zero_rows_is_a_wiring_error]]), and 0 rows read is never
    the answer.

    Now chunked small and LOUD on failure: a lookup that fails must not
    read as "nothing is stored", because that sends every game down the
    INSERT path.
    """
    if not game_ids: return {}
    ids_u = sorted({str(g) for g in game_ids if g})
    out, failed = {}, 0
    for i in range(0, len(ids_u), _ID_CHUNK):
        chunk = ids_u[i:i + _ID_CHUNK]
        ids = ','.join(f'"{gid}"' for gid in chunk)
        r = requests.get(
            f'{SB}/rest/v1/ncaab_game_results',
            params={'game_id': f'in.({ids})',
                    'select': 'game_id,game_date,home_score,away_score,'
                              'close_spread,close_total'},
            headers=H_READ, timeout=30,
        )
        if r.status_code != 200:
            failed += len(chunk)
            print(f'  ! fetch_existing HTTP {r.status_code} on {len(chunk)} '
                  f'id(s): {r.text[:160]}')
            continue
        for row in r.json():
            out[row['game_id']] = row
    if failed:
        raise RuntimeError(
            f'fetch_existing could not read {failed} of {len(ids_u)} ids. '
            f'Refusing to continue: an unreadable lookup looks identical to '
            f'"not stored yet" and would INSERT a duplicate row for every '
            f'one of them.')
    return out


def fetch_fixture_index(finals: list) -> dict:
    """Stored rows across the resolve window, indexed by UNORDERED team pair.

    ══ 2026-11-14 · ORIENTATION IS NOT A FACT AT A NEUTRAL SITE ═══════
    `fetch_existing` can only find a row whose game_id matches, and a
    game_id embeds the orientation (`ncaab_<date>_<away>_<home>`). At a
    neutral site there IS no home team, so ESPN and the odds feed pick
    opposite ones — and then NEITHER id matches and the game goes down the
    INSERT path. Measured season-to-date: **14 fixtures stored TWICE, 28
    rows for 14 games**, and the split is the damage:

        ours (odds feed)   close_spread 7.5    home_score NULL
        ESPN's (inserted)  close_spread NULL   74-72

    So the line sits on a row that never gets a score and the score sits on
    a row that has no line. Neither can grade, which is why 13 of those
    fixtures survived a full `--days-back 14` pass still unscored, and why
    four chalk_parlay snapshots could never settle. Every one of the 13 has
    `is_neutral_site = True` in ncaab_game_context — 13 of 13.

    The dates disagree too (ctx 11-02 vs results 11-03 on UCLA/Arizona), so
    the match is widened to +-1 day. Letting the existing `_owed_date` rule
    then correct the stored date is what REPAIRS the receipt join, because
    ESPN's ET date is the one grade_public_receipts shares with context.
    """
    if not finals:
        return {}
    days = sorted({str(o['game_date'])[:10] for o in finals if o.get('game_date')})
    if not days:
        return {}
    lo = (date.fromisoformat(days[0]) - timedelta(days=1)).isoformat()
    hi = (date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat()
    idx: dict = {}
    off = 0
    while True:
        r = requests.get(
            f'{SB}/rest/v1/ncaab_game_results',
            params={'select': 'game_id,game_date,home_team,away_team,'
                              'home_score,away_score,close_spread,close_total,'
                              'spread_result,total_result',
                    'and': f'(game_date.gte.{lo},game_date.lte.{hi})',
                    'limit': 1000, 'offset': off},
            headers=H_READ, timeout=60,
        )
        if r.status_code != 200:
            # Loud, never swallowed: an unreadable lookup is identical to
            # "no twin stored" and would insert the duplicate all over again
            # ([[feedback_zero_rows_is_a_wiring_error]]).
            raise RuntimeError(f'fetch_fixture_index HTTP {r.status_code}: '
                               f'{r.text[:160]}')
        body = r.json() or []
        for row in body:
            key = frozenset((row.get('home_team'), row.get('away_team')))
            idx.setdefault(key, []).append(row)
        if len(body) < 1000:
            break
        off += 1000
    return idx


def remap_onto_swapped_fixture(finals: list, existing: dict,
                               idx: dict) -> int:
    """Point an unmatched final at its orientation-swapped stored row.

    SCORES ARE REASSIGNED BY TEAM NAME, NEVER BY POSITION. Swapping on
    position would silently hand the home side the away team's points and
    grade the OPPOSITE SIDE of what we published
    ([[feedback_grade_the_side_we_published]],
    [[feedback_magnitude_cannot_express_a_side]]). The name test has to hold
    exactly in both directions or the game is left alone.

    Refuses on ambiguity: 0 candidates means the game really is new, and >1
    means two stored rows claim the fixture, which a resolver must not pick
    between ([[feedback_prefix_strip_can_name_another_school]]).
    """
    swapped = 0
    for o in finals:
        if o['game_id'] in existing:
            continue
        key = frozenset((o['home_team'], o['away_team']))
        if len(key) != 2:
            continue
        gd = date.fromisoformat(str(o['game_date'])[:10])
        cands = []
        for row in idx.get(key, []):
            try:
                rd = date.fromisoformat(str(row['game_date'])[:10])
            except Exception:
                continue
            if abs((rd - gd).days) > 1:
                continue
            # Only an OPPOSITE orientation is of interest here; a matching
            # one would already have been found by game_id.
            if (row.get('home_team') == o['away_team']
                    and row.get('away_team') == o['home_team']):
                cands.append(row)
        if len(cands) != 1:
            if len(cands) > 1:
                print(f'  ! {o["game_id"]}: {len(cands)} stored rows claim '
                      f'this fixture — refusing to guess')
            continue
        row = cands[0]
        o['home_score'], o['away_score'] = o['away_score'], o['home_score']
        o['home_team'], o['away_team'] = row['home_team'], row['away_team']
        o['game_id'] = row['game_id']
        existing[row['game_id']] = row
        swapped += 1
        print(f'  ~ neutral-site orientation: {o["game_id"]} '
              f'({row["away_team"]} @ {row["home_team"]}) '
              f'{o["away_score"]}-{o["home_score"]}')
    return swapped


def compute_outcome_patch(new: dict, existing: dict) -> Optional[dict]:
    """Build PATCH payload. Returns None if unchanged / not final."""
    hs = new.get('home_score')
    as_ = new.get('away_score')
    if hs is None or as_ is None:
        return None

    # == 2026-11-03 . "SCORES UNCHANGED" IS NOT "NOTHING TO DO" ===========
    # This returned None the moment the score matched, so a game scored
    # BEFORE its closing line landed never got spread_result or
    # total_result -- the odds backfill fills close_spread/close_total
    # afterwards and nothing ever revisits the row.
    #
    # Measured this season: 73 games have a final score AND a close_spread
    # AND a close_total, and **72 of them have spread_result and
    # total_result NULL**. Every one is computable from columns already in
    # the row; all 72 share result_logged_at=2026-11-03T18:17:06, i.e. one
    # scoring pass that ran before the lines arrived.
    #
    # That is not cosmetic. It is why NO NCAAB spread or total signal can be
    # measured: the backtest grades 221 joined games and finds 2 with a
    # gradeable spread outcome. And NCAAB favourite ML is already measured
    # dead (negative in every price band, n=4,981; and every ML signal with
    # n>=30 came out -0.70% to -8.47% ROI), so spreads and totals are the
    # only markets left to look for an edge in.
    #
    # So: still skip when there is genuinely nothing to add, but treat a
    # MISSING DERIVED OUTCOME as work to do.
    _same_score = (_i(existing.get('home_score')) == hs
                   and _i(existing.get('away_score')) == as_)
    _owed_spread = (_f(existing.get('close_spread')) is not None
                    and not existing.get('spread_result'))
    _owed_total = (_f(existing.get('close_total')) is not None
                   and not existing.get('total_result'))
    # ══ 2026-11-06 · R166: A WRONG game_date IS WORK TO DO ══════════
    # Every row written before today carries the UTC date, which for a
    # post-7pm-ET tip is a day late, and grade_public_receipts joins ON
    # that date. Without this the short-circuit above would return None on
    # a fully-scored row and the bad date would never be repaired -- the
    # resolver would look healthy while the receipts stayed ungradeable
    # ([[feedback_green_step_that_writes_nothing]]).
    _owed_date = (str(existing.get('game_date') or '')
                  != str(new.get('game_date') or '')
                  and bool(new.get('game_date')))
    if _same_score and not _owed_spread and not _owed_total and not _owed_date:
        return None

    payload = {
        'home_score': hs,
        'away_score': as_,
        'total_points': hs + as_,
        'home_win': hs > as_,
        'result_logged_at': datetime.now(timezone.utc).isoformat(),
    }
    if _owed_date:
        payload['game_date'] = new['game_date']

    # NCAAB convention: close_spread negative = home favored.
    # spread_result semantics: home_covered if home_margin > -close_spread.
    cs = _f(existing.get('close_spread'))
    if cs is not None:
        margin = hs - as_
        if margin > -cs:   payload['spread_result'] = 'home_covered'
        elif margin < -cs: payload['spread_result'] = 'away_covered'
        else:              payload['spread_result'] = 'push'

    ct = _f(existing.get('close_total'))
    if ct is not None:
        tot = hs + as_
        if tot > ct:   payload['total_result'] = 'over'
        elif tot < ct: payload['total_result'] = 'under'
        else:          payload['total_result'] = 'push'

    return payload


def upsert_new_games(new_rows: list, dry_run: bool = False) -> int:
    """For games from ESPN not yet in ncaab_game_results (no odds row),
    insert a minimal row so the resolver has something to update.
    This catches unlined games (scrimmages, exhibition, MTE tournaments)."""
    if not new_rows: return 0
    payload = [{
        'game_id':   n['game_id'],
        'game_date': n['game_date'],
        'home_team': n['home_team'],
        'away_team': n['away_team'],
        'home_score': n.get('home_score'),
        'away_score': n.get('away_score'),
        'total_points': (n.get('home_score') or 0) + (n.get('away_score') or 0)
                        if n.get('home_score') is not None and n.get('away_score') is not None
                        else None,
        'home_win': n.get('home_score') > n.get('away_score')
                    if n.get('home_score') is not None and n.get('away_score') is not None
                    else None,
        'result_logged_at': datetime.now(timezone.utc).isoformat(),
    } for n in new_rows]
    if dry_run:
        print(f'  [DRY] would INSERT {len(payload)} unlined games')
        return len(payload)
    r = requests.post(
        f'{SB}/rest/v1/ncaab_game_results?on_conflict=game_id',
        headers={**H_WRITE, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
        json=payload, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ new-game insert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(payload)


def apply_patches(patches: list, dry_run: bool = False) -> int:
    if not patches: return 0
    if dry_run:
        for gid, payload in patches[:10]:
            print(f'  [DRY] {gid}: {payload.get("home_score")}-{payload.get("away_score")} '
                  f'sp={payload.get("spread_result")} tot={payload.get("total_result")}')
        if len(patches) > 10:
            print(f'  [DRY] ... {len(patches)-10} more')
        return len(patches)
    updated = 0
    for gid, payload in patches:
        r = requests.patch(
            f'{SB}/rest/v1/ncaab_game_results?game_id=eq.{requests.utils.quote(str(gid), safe="")}',
            headers=H_WRITE, json=payload, timeout=15,
        )
        if r.status_code in (200, 201, 204):
            updated += 1
        else:
            print(f'  ⚠ patch {gid} failed {r.status_code}: {r.text[:120]}')
    return updated


def kick_external_resolver() -> None:
    """Fire sport-agnostic external picks resolver for NCAAB."""
    print(f'\n=== Kicking resolve_externals --sport NCAAB ===')
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resolve_externals.py')
    if not os.path.exists(script):
        print(f'  (resolve_externals.py not found — skip)'); return
    try:
        result = subprocess.run(
            [sys.executable, script, '--sport', 'NCAAB', '--days', '14'],
            capture_output=True, text=True, timeout=120,
        )
        if result.stdout:
            print('\n'.join(result.stdout.splitlines()[-8:]))
    except Exception as e:
        print(f'  ⚠ external resolver kickoff failed: {e}')


# ─── main ─────────────────────────────────────────────────────────

def run(dates: list, dry_run: bool = False, skip_external: bool = False) -> None:
    print(f'=== NCAAB resolver · dates={dates} ===')
    by_espn, by_name = load_alias_lookups()
    if not by_espn and not by_name:
        print('  ✗ alias table empty — run ncaab_seed_aliases.py then ncaab_enrich_aliases.py')
        return
    print(f'  aliases: {len(by_espn)} by ESPN id, {len(by_name)} by name variant')

    all_outcomes = []
    total_events = 0
    for date_str in dates:
        events = fetch_espn_scoreboard(date_str)
        total_events += len(events)
        for e in events:
            o = event_to_outcome(e, by_espn, by_name)
            if o: all_outcomes.append(o)
        time.sleep(0.4)  # polite pacing on ESPN
    print(f'  ESPN events pulled: {total_events} (resolved {len(all_outcomes)})')

    finals = [o for o in all_outcomes if o['completed']]
    print(f'  finals in window: {len(finals)}')
    if not finals:
        print('  (no completed games)'); return

    # ══ 2026-11-06 · R166: LOOK THE LEGACY ID UP TOO ══════════════
    # game_id now carries the ET date, but every row already stored is
    # keyed by the UTC one. Looking up only the new id would find nothing
    # and send the game down the INSERT path, putting a second row in the
    # table that grades the record -- 165 duplicates on one date when this
    # was first dry-run. So both spaces are fetched and a game that is
    # already stored under its legacy id is REMAPPED onto that row, which
    # then gets its game_date corrected by the patch
    # ([[feedback_merge_duplicates_needs_on_conflict]]: a write that can
    # only insert is not an upsert).
    existing = fetch_existing([o['game_id'] for o in finals]
                              + [o['legacy_game_id'] for o in finals])
    _remapped = 0
    for o in finals:
        if o['game_id'] not in existing and o['legacy_game_id'] in existing:
            o['game_id'] = o['legacy_game_id']
            _remapped += 1
    if _remapped:
        print(f'  {_remapped} game(s) remapped onto their legacy UTC-keyed '
              f'row so the date is PATCHED, not duplicated')

    # Third and last resolution step, for the neutral-site games whose two
    # feeds disagree about which team is home. See fetch_fixture_index.
    _swapped = remap_onto_swapped_fixture(
        finals, existing, fetch_fixture_index(finals))
    if _swapped:
        print(f'  {_swapped} neutral-site game(s) matched to their stored row '
              f'in the opposite orientation (scores reassigned BY TEAM NAME)')

    print(f'  existing rows matched: '
          f'{sum(1 for o in finals if o["game_id"] in existing)}/{len(finals)}')

    patches = []
    new_games = []
    for o in finals:
        ex = existing.get(o['game_id'])
        if not ex:
            new_games.append(o)  # unlined game, insert minimal row
            continue
        payload = compute_outcome_patch(o, ex)
        if payload:
            patches.append((o['game_id'], payload))

    updated = apply_patches(patches, dry_run=dry_run)
    inserted = upsert_new_games(new_games, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'{prefix}patched {updated}, inserted {inserted} unlined')

    if not skip_external and not dry_run:
        kick_external_resolver()


def _date_range(days_back: int) -> list:
    today = _et_now().date()
    return [(today - timedelta(days=d)).strftime('%Y%m%d') for d in range(days_back + 1)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='Single YYYYMMDD (e.g., 20250301)')
    ap.add_argument('--days-back', type=int, default=1,
                    help='How many days back from today to resolve (default: 1 = yesterday + today)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--skip-external', action='store_true')
    args = ap.parse_args()

    if args.date:
        dates = [args.date]
    else:
        dates = _date_range(args.days_back)

    run(dates, dry_run=args.dry_run, skip_external=args.skip_external)


if __name__ == '__main__':
    main()
