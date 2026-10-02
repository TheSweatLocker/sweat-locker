"""Enrich nfl_game_context with Madden ratings + Top 100 fields (2026-08-24).

Joins nfl_madden_ratings + nfl_madden_player_ratings + nfl_top100_snapshot
into nfl_game_context so shadow signals can read via ctx.field.

Runs AFTER seed_nfl_madden_launch.py + AFTER nfl_game_context builder.
Idempotent — uses PATCH targeting existing rows (same pattern as
enrich_ctx_roster_physicality after 8/24 bug fix).

USAGE
─────
    python enrich_ctx_nfl_madden.py                  # current week, current season
    python enrich_ctx_nfl_madden.py --week 1
    python enrich_ctx_nfl_madden.py --season 2026
    python enrich_ctx_nfl_madden.py --days-ahead 14

FIELDS WRITTEN
──────────────
  home_madden_ovr / away_madden_ovr / home_madden_off / away_madden_off /
  home_madden_def / away_madden_def
  madden_ovr_gap_home = home_ovr - away_ovr
  madden_off_gap_home = home_off - away_def  (home offense vs opp defense)
  madden_off_gap_away = away_off - home_def
  home_qb_madden_ovr / away_qb_madden_ovr / madden_qb_delta_home
  home_top100_count / away_top100_count
  home_qb_top10_flag / away_qb_top10_flag
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}


def _get_latest_week(season: int) -> int:
    """Return the most recent week_snapshot with data (fallback to 0 = launch)."""
    r = urllib.request.Request(
        f'{SB}/rest/v1/nfl_madden_ratings?season=eq.{season}'
        f'&select=week_snapshot&order=week_snapshot.desc&limit=1',
        headers=H_READ)
    try:
        rows = json.loads(urllib.request.urlopen(r, timeout=10).read())
        return rows[0]['week_snapshot'] if rows else 0
    except Exception:
        return 0


def load_team_ratings(season: int, week: int) -> dict:
    r = urllib.request.Request(
        f'{SB}/rest/v1/nfl_madden_ratings'
        f'?season=eq.{season}&week_snapshot=eq.{week}'
        f'&select=team,ovr,off_rating,def_rating,ovr_rank',
        headers=H_READ)
    try:
        rows = json.loads(urllib.request.urlopen(r, timeout=15).read())
        return {row['team']: row for row in rows}
    except Exception as e:
        print(f'[err] team load: {e}', flush=True)
        return {}


def load_qb_ratings(season: int, week: int) -> dict:
    """Return {team: [{name, ovr}, ...]} ordered best-rated first.

    ══ 2026-10-01 · "HIGHEST OVR = STARTER" IS WRONG WHEN IT MATTERS ══
    This used to keep only the top-rated QB per team and call him the starter.
    That definition fails in exactly the situation it most needs to be right,
    because the injured star IS the highest-rated QB: with Caleb Williams
    listed Doubtful, it still returned Williams at 90, so
    madden_qb_delta_home credited Chicago an eight-point quarterback edge
    belonging to a player who was not dressing. nfl_qb_injury_gate already
    named this leak in its docstring on 09-28; it capped the TIER and left
    the INPUT alone.

    It matters because these ratings are written at nfl_pipeline step 304 —
    AFTER the injury gate at step 243 — and are then consumed by
    nfl_goat_composite (792) and recompute_nfl_primary_play (834). The cap
    lands before the contaminated number is even written, so the cap cannot
    protect the lenses downstream of it.

    Keeping every QB lets the caller walk down the list to the best one who
    is actually available. Caller must do the availability check — this
    function stays a pure ratings load.
    """
    r = urllib.request.Request(
        f'{SB}/rest/v1/nfl_madden_player_ratings'
        f'?season=eq.{season}&week_snapshot=eq.{week}&position=eq.QB'
        f'&select=team,player_name,ovr&order=ovr.desc',
        headers=H_READ)
    try:
        rows = json.loads(urllib.request.urlopen(r, timeout=15).read())
    except Exception as e:
        print(f'[err] qb load: {e}', flush=True)
        return {}
    # ══ DROP INITIALS ALIASES — THEY BYPASS THE AVAILABILITY CHECK ══
    # nfl_madden_player_ratings carries an initials row beside many real
    # players: ('CHI','QB',90) is BOTH 'Caleb Williams' and 'CW'. Measured
    # 2026-10-01: 100 of 275 rows (36%) are such duplicates, e.g.
    # ('KC','QB',93) -> ['PM','Patrick Mahomes'], ('NE','QB',92) ->
    # ['DM','Drake Maye'].
    #
    # They defeat any name-keyed gate: nfl_injuries has Caleb Williams
    # Doubtful but nothing for 'CW', so after correctly skipping Williams the
    # alias sailed through as an available QB at the same 90 rating and CHI
    # kept its contaminated delta. Verified: NYG and TB nulled correctly while
    # CHI did not, and this was the only difference.
    #
    # Rule is deliberately narrow — drop a no-space name ONLY when a longer
    # name exists for the same (team, position, ovr). A genuine one-word name
    # therefore survives, and nothing is dropped on the strength of its shape
    # alone. Fixing the table itself is the real cure; this stops the bypass
    # at the point of use.
    best: dict = {}
    for row in rows:
        k = (row.get('team'), row.get('position'), row.get('ovr'))
        nm = row.get('player_name') or ''
        prev = best.get(k)
        if prev is None or len(nm) > len(prev.get('player_name') or ''):
            best[k] = row
    kept = list(best.values())
    if len(kept) < len(rows):
        print(f'[qb] dropped {len(rows) - len(kept)} alias/duplicate QB row(s)',
              flush=True)

    qbs: dict = {}
    for row in sorted(kept, key=lambda r: -(r.get('ovr') or 0)):
        t = row['team']
        qbs.setdefault(t, []).append({
            'name': row['player_name'],
            'ovr': float(row['ovr']) if row.get('ovr') is not None else None,
        })
    return qbs


def available_qb(team, qb_list, max_week=None, as_of=None):
    """Best-rated QB on `team` who is not ruled out, or None.

    Returns (qb_dict, note). qb_dict is None when every rated QB on the roster
    is ruled out or the roster has no rated QB — and None is the RIGHT answer
    there, because the caller then writes no QB rating at all. Measured
    2026-10-01: Tyson Bagent and Jalon Daniels, the men actually starting for
    CHI and TB, have NO Madden rating, so "substitute the backup's number" is
    frequently impossible. A missing feature is honest; a feature that credits
    an absent player is a wrong number the models will act on.

    'Questionable' is NOT treated as out — questionable QBs usually play, and
    the gate already handles that case as a softer caveat. Only the hard
    statuses (out / doubtful / IR / PUP / suspended) demote a QB here, so this
    stays consistent with nfl_qb_injury_gate rather than inventing a second,
    stricter rule.

    Leak guards are passed straight through to latest_status: without
    max_week / as_of a backtest reads injury news from after kickoff, which is
    the defect that file documents catching on its first regression run.
    """
    if not qb_list:
        return None, 'no rated QB on roster'
    try:
        from nfl_qb_injury_gate import OUT_STATUSES, latest_status
    except ImportError:
        # Gate unavailable — fail OPEN to the old behaviour rather than
        # silently stripping every QB rating in the league.
        return qb_list[0], 'injury check unavailable — using top-rated QB'
    skipped = []
    for qb in qb_list:
        st = None
        try:
            st = latest_status(SB, H_READ, qb['name'], team=team,
                               max_week=max_week, as_of=as_of)
        except Exception:
            st = None            # lookup failure => treat as playing
        status = str((st or {}).get('injury_status') or '').strip().lower()
        if status in OUT_STATUSES:
            skipped.append(f"{qb['name']} ({status})")
            continue
        note = (f"using {qb['name']}; ruled out: {', '.join(skipped)}"
                if skipped else None)
        return qb, note
    return None, f"every rated QB ruled out: {', '.join(skipped)}"


def _norm_name(name: str) -> str:
    """Lowercase, strip punctuation and suffixes, for cross-source name joins."""
    s = ''.join(ch for ch in (name or '').lower() if ch.isalnum() or ch == ' ')
    for suf in (' jr', ' sr', ' ii', ' iii', ' iv', ' v'):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return ' '.join(s.split())


def load_starters(season: int) -> dict:
    """{(team, week): player_name} — the QB nfl_starters says is starting.

    ══ 2026-10-01 · WHO IS STARTING IS NOT A MADDEN QUESTION ══
    This file used to answer "who starts" with "the highest-rated available QB
    on the Madden roster". That is a talent ranking, not a depth chart, and the
    team key it ranks within is unreliable in both Madden sources. Measured
    today, the two failures it produced:

      ATL  rated list held only Tua Tagovailoa 74, so ATL was credited 74 —
           while Michael Penix Jr. (82) started every week 1-4. Penix exists
           in nfl_madden_player_ratings but keyed 'Atlanta Falcons' instead of
           'ATL', in a second key space this file never reads.
      MIN  Kyler Murray 75 and J.J. McCarthy 80 are both nominally Vikings in
           Madden, so "highest rated" picks McCarthy — who is now the NYG
           starter. Normalizing the team key WITHOUT this change would have
           made that worse, not better.

    nfl_starters answers who; Madden answers how good. Keeping them separate is
    what makes the unreliable team key stop mattering: the rating is looked up
    by NAME (see load_qb_ratings_by_name), so a player filed under the wrong
    franchise spelling is still found.

    Keyed by week because starters change weekly — using one global map would
    reintroduce the staleness this is meant to remove.
    """
    r = urllib.request.Request(
        f'{SB}/rest/v1/nfl_starters'
        f'?season=eq.{season}&position=eq.QB&is_starter=is.true'
        f'&select=team,week,player_name,source',
        headers=H_READ)
    try:
        rows = json.loads(urllib.request.urlopen(r, timeout=15).read())
    except Exception as e:
        print(f'[warn] starters load: {e} — falling back to Madden ranking',
              flush=True)
        return {}
    # {team: [(week, name), ...]} newest first, so a game in a week the starter
    # puller has not reached yet carries the most recent starter forward instead
    # of falling back to the Madden talent ranking. The horizon is 14 days, so
    # next week's games are always in it while nfl_starters only covers through
    # the current week — without this, every one of those games silently reverts
    # to the old behaviour and the fix appears to work while covering half the
    # board.
    by_team: dict = {}
    for x in rows:
        if not x.get('team') or x.get('week') is None:
            continue
        by_team.setdefault(x['team'], []).append((x['week'], x['player_name']))
    for v in by_team.values():
        v.sort(reverse=True)
    return by_team


def load_qb_ratings_by_name(season: int) -> dict:
    """{normalized_name: {'name','ovr','team'}} across every team key and week.

    Deliberately team-agnostic. A Madden rating is a property of the player, and
    the team column is the one field in this table that cannot be trusted — it
    carries two spellings for every franchise ('ATL' and 'Atlanta Falcons') and
    the launch snapshot's rosters are two months stale. Looking up by name
    sidesteps both problems.

    Most recent snapshot wins (week_snapshot, then fetched_at).
    """
    r = urllib.request.Request(
        f'{SB}/rest/v1/nfl_madden_player_ratings'
        f'?season=eq.{season}&position=eq.QB&select=*',
        headers=H_READ)
    try:
        rows = json.loads(urllib.request.urlopen(r, timeout=15).read())
    except Exception as e:
        print(f'[err] qb-by-name load: {e}', flush=True)
        return {}
    best: dict = {}
    for row in rows:
        nm = row.get('player_name') or ''
        key = _norm_name(nm)
        if not key or row.get('ovr') is None:
            continue
        rank = (row.get('week_snapshot') or 0, str(row.get('fetched_at') or ''))
        prev = best.get(key)
        if prev is None or rank > prev['_rank']:
            best[key] = {'name': nm, 'ovr': float(row['ovr']),
                         'team': row.get('team'), '_rank': rank}
    return best


def starter_qb(team: str, week, starters: dict, by_name: dict,
               max_week=None, as_of=None):
    """(qb_dict, note) for the listed starter, or (None, reason).

    Honours the same injury gate as available_qb: a listed starter who has since
    been ruled out is not used, and the caller falls back to the Madden ranking.
    """
    hist = starters.get(team) or []
    name = None
    for wk, nm in hist:                  # newest first
        if week is None or wk <= week:
            name = nm
            carried = (week is not None and wk < week)
            break
    else:
        carried = False
    if not name:
        return None, None
    try:
        from nfl_qb_injury_gate import OUT_STATUSES, latest_status
        st = latest_status(SB, H_READ, name, team=team,
                           max_week=max_week, as_of=as_of)
        if str((st or {}).get('injury_status') or '').strip().lower() in OUT_STATUSES:
            return None, f'listed starter {name} is ruled out'
    except ImportError:
        pass
    except Exception:
        pass                       # lookup failure => treat as playing
    m = by_name.get(_norm_name(name))
    if not m:
        # Honest blank. Journeyman starters are routinely unrated: measured
        # 2026-10-01, Case Keenum (CHI), Jameis Winston (NYG) and Marcus
        # Mariota (WAS) have no Madden rating under any team key, so there is
        # no number to write for them.
        return None, f'starter {name} has no Madden rating'
    return ({'name': m['name'], 'ovr': m['ovr']},
            f"starter {m['name']} ({m['ovr']:.0f})"
            + (' [carried forward]' if carried else ''))


def load_top100(season: int) -> dict:
    """Return {team: [(rank, position), ...]} for aggregate counts + QB Top 10 check."""
    r = urllib.request.Request(
        f'{SB}/rest/v1/nfl_top100_snapshot'
        f'?season=eq.{season}&select=team,rank,position,player_name',
        headers=H_READ)
    try:
        rows = json.loads(urllib.request.urlopen(r, timeout=15).read())
    except Exception:
        return {}
    by_team = {}
    for row in rows:
        t = row.get('team')
        if not t: continue
        by_team.setdefault(t, []).append(row)
    return by_team


def load_upcoming(days_ahead: int) -> list[dict]:
    end = (date.today() + timedelta(days=days_ahead)).isoformat()
    start = date.today().isoformat()
    r = urllib.request.Request(
        f'{SB}/rest/v1/nfl_game_context'
        f'?game_date=gte.{start}&game_date=lte.{end}'
        f'&select=game_id,home_team,away_team,game_date,week',
        headers=H_READ)
    try:
        return json.loads(urllib.request.urlopen(r, timeout=15).read())
    except Exception as e:
        print(f'[err] ctx load: {e}', flush=True)
        return []


def compute_fields(home: str, away: str,
                   team_ratings: dict, qb_ratings: dict,
                   top100_by_team: dict,
                   max_week: int | None = None,
                   as_of: str | None = None,
                   week=None,
                   starters: dict | None = None,
                   qb_by_name: dict | None = None) -> dict:
    """Compute all ctx fields for one game. Returns dict of only non-None values.

    max_week / as_of are the injury leak guards, passed to available_qb so a
    backtest cannot resolve a QB using news published after kickoff. Both
    default to None for callers that genuinely want "now".

    week / starters / qb_by_name drive the starter-list path. All three default
    to None so existing callers keep the old Madden-ranking behaviour rather
    than crashing — this function has several call sites.
    """
    starters = starters or {}
    qb_by_name = qb_by_name or {}
    h = team_ratings.get(home) or {}
    a = team_ratings.get(away) or {}
    h_ovr = h.get('ovr'); a_ovr = a.get('ovr')
    h_off = h.get('off_rating'); a_off = a.get('off_rating')
    h_def = h.get('def_rating'); a_def = a.get('def_rating')

    out = {}
    if h_ovr is not None: out['home_madden_ovr'] = float(h_ovr)
    if a_ovr is not None: out['away_madden_ovr'] = float(a_ovr)
    if h_off is not None: out['home_madden_off'] = float(h_off)
    if a_off is not None: out['away_madden_off'] = float(a_off)
    if h_def is not None: out['home_madden_def'] = float(h_def)
    if a_def is not None: out['away_madden_def'] = float(a_def)
    if h_ovr is not None and a_ovr is not None:
        out['madden_ovr_gap_home'] = float(h_ovr) - float(a_ovr)
    if h_off is not None and a_def is not None:
        out['madden_off_gap_home'] = float(h_off) - float(a_def)
    if a_off is not None and h_def is not None:
        out['madden_off_gap_away'] = float(a_off) - float(h_def)

    # QB — best-rated AVAILABLE arm, not simply best-rated. See
    # load_qb_ratings / available_qb. When nobody rated is available we write
    # NOTHING rather than the injured star's number: downstream
    # (nfl_goat_composite, recompute_nfl_primary_play) treats a missing OVR as
    # "no QB signal", which is correct, whereas a stale OVR is a wrong number
    # it will act on.
    #
    # Starter list first (see load_starters): it answers who is playing, which
    # a talent ranking cannot. Falls back to the Madden ranking only when
    # nfl_starters has no row for this (team, week) — never when it has a row
    # naming an unrated player, because "unrated" is a real answer there and
    # silently substituting the backup's number would undo the whole point.
    h_qb, h_note = starter_qb(home, week, starters, qb_by_name,
                              max_week=max_week, as_of=as_of)
    a_qb, a_note = starter_qb(away, week, starters, qb_by_name,
                              max_week=max_week, as_of=as_of)
    if h_qb is None and h_note is None:
        h_qb, h_note = available_qb(home, qb_ratings.get(home) or [],
                                    max_week=max_week, as_of=as_of)
    if a_qb is None and a_note is None:
        a_qb, a_note = available_qb(away, qb_ratings.get(away) or [],
                                    max_week=max_week, as_of=as_of)
    h_qb_ovr = (h_qb or {}).get('ovr')
    a_qb_ovr = (a_qb or {}).get('ovr')
    # ══ OMITTING A FIELD DOES NOT CLEAR IT ══
    # This function's contract is "only non-None values" and patch_ctx sends a
    # PATCH, so leaving these keys out preserves whatever is already in the
    # row — which is the injured starter's rating. The first cut of this fix
    # was therefore a no-op on precisely the games it existed for: NYG would
    # have kept Dart's 77 and TB Mayfield's 83. Write an explicit None so the
    # PATCH nulls them. Same family as the DEFAULT-now()-plus-upsert timestamp
    # that lied: absence of a write is not a write of absence.
    out['home_qb_madden_ovr'] = h_qb_ovr
    out['away_qb_madden_ovr'] = a_qb_ovr
    out['madden_qb_delta_home'] = (
        h_qb_ovr - a_qb_ovr
        if (h_qb_ovr is not None and a_qb_ovr is not None) else None)
    # Say so in the log when a substitution or a blank happened, so a league
    # that suddenly loses QB deltas is explainable instead of mysterious.
    for _side, _nm, _note in (('home', home, h_note), ('away', away, a_note)):
        if _note:
            print(f'  [qb] {_nm} ({_side}): {_note}', flush=True)

    # Top 100
    h_t100 = top100_by_team.get(home) or []
    a_t100 = top100_by_team.get(away) or []
    out['home_top100_count'] = len(h_t100)
    out['away_top100_count'] = len(a_t100)
    # QB Top 10 flag (any QB from this team ranked 1-10)
    out['home_qb_top10_flag'] = any(r.get('position') == 'QB' and r.get('rank', 999) <= 10 for r in h_t100)
    out['away_qb_top10_flag'] = any(r.get('position') == 'QB' and r.get('rank', 999) <= 10 for r in a_t100)

    return out


def patch_ctx(game_id: str, fields: dict) -> bool:
    if not fields: return False
    qgid = urllib.parse.quote(game_id, safe='')
    url = f'{SB}/rest/v1/nfl_game_context?game_id=eq.{qgid}'
    body = json.dumps(fields).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers=H_WRITE, method='PATCH')
    try:
        urllib.request.urlopen(req, timeout=15).read()
        return True
    except Exception as e:
        print(f'[warn] patch failed for {game_id}: {str(e)[:120]}', flush=True)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=datetime.now().year)
    ap.add_argument('--week', type=int, default=None,
                    help='week_snapshot to read. Default = latest available.')
    ap.add_argument('--days-ahead', type=int, default=14)
    args = ap.parse_args()

    season = args.season
    week = args.week if args.week is not None else _get_latest_week(season)

    print(f'\n[start] NFL ctx enrichment season={season} week={week} horizon={args.days_ahead}d',
          flush=True)

    team = load_team_ratings(season, week)
    qbs = load_qb_ratings(season, week)
    t100 = load_top100(season)
    starters = load_starters(season)
    qb_by_name = load_qb_ratings_by_name(season)
    if not team:
        print(f'[abort] no team ratings for season {season} week {week}. '
              f'Run seed_nfl_madden_launch.py first.', flush=True)
        return
    print(f'[data] teams={len(team)} qbs={len(qbs)} top100_teams={len(t100)}',
          flush=True)

    upcoming = load_upcoming(args.days_ahead)
    print(f'[games] {len(upcoming)} in horizon', flush=True)

    ok, no_data = 0, 0
    for g in upcoming:
        # ══ DO NOT PASS THE MADDEN WEEK AS THE INJURY WEEK ══
        # First cut passed max_week=week here. `week` is the Madden
        # week_snapshot — a ratings-RELEASE week — not the NFL game week, and
        # latest_status bounds nfl_injuries by game week. Feeding one into the
        # other produced BOTH failure modes at once, verified on this slate:
        #   * NYG / CHI / TB week-5 'Out' rows fell outside the bound, so Dart,
        #     Williams and Mayfield were all treated as playing and kept their
        #     ratings — the fix silently did nothing for the only games it was
        #     written for.
        #   * ATL resolved to Tua's week-2 'Doubtful' instead of his week-4
        #     'Full', inventing an injury for a healthy player and nulling a
        #     good rating.
        # Two week spaces that happen to share a name is a trap; the units
        # have to match, not just the type.
        #
        # as_of alone is the right guard here. Per nfl_qb_injury_gate,
        # report_date is authoritative wherever it exists and the week bound
        # only covers the ~38 rows where it is NULL. And this script enriches
        # FORWARD games only (load_upcoming spans today..+14d), so there is no
        # leak to guard against — today's news is all anyone has.
        fields = compute_fields(g['home_team'], g['away_team'], team, qbs, t100,
                                max_week=None, as_of=g.get('game_date'),
                                week=g.get('week'), starters=starters,
                                qb_by_name=qb_by_name)
        if not fields:
            no_data += 1
            continue
        if patch_ctx(g['game_id'], fields):
            ok += 1

    print(f'\n[done] enriched={ok} no_data={no_data}', flush=True)


if __name__ == '__main__':
    main()
