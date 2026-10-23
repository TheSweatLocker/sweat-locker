"""Project a starting QB's stat line from OUR data, the way Andy framed it.

ANDY 2026-10-22, the framework in his words:

    "take entire starting offense stats and use as average for our projected
    line then deduct or add points based on opponents tendencies to give up
    large numbers of that market or stat line. So if Saquon averages 87 yds
    per game but [the] Bucs who [they're] playing give up 100-110 with RB1
    typically getting most of that. and Saquon typically does well at home
    based on performances, and the total is low possibly indicating a slow
    wear and tear game bleeding clock, then maybe based on a certain
    calculation we project saquon to 98-104 yards. That would be the
    framework and then work that back to anytime touchdown and that branch of
    supporting data and eventually points which will lead to predicted score."

So: BASELINE x OPPONENT TENDENCY x HOME/AWAY x GAME ENVIRONMENT, with every
multiplier named and stored. QB ships first because he asked for it first;
the shape is position-agnostic so RB/WR/TE slot in without a rewrite.

╔══ WHY NOT project_nfl_stats.py, WHICH ALREADY EXISTS ═════════════════════╗
Because it is two seasons stale and cannot be fixed in place. It sources
nfl_data_py, and nfl_data_py 404s for BOTH 2025 and 2026 - the upstream only
carries through 2024. Measured today:

    project_qb('Caleb Williams') L5  =  2024 weeks 14-18
    project_qb('Drake Maye') pass_yds = 163.6   (2024 baseline)
    his actual 2026 L5                = 208/199/269/374/235  ~= 257

A ~94-yard error on a number we were about to put on a game card. Its
opponent-defence side was equally stale: `_completed_seasons(2026)` returns
[2024, 2023] from a hardcoded year-minus-2 rule written in August.

It reaches NO users today - its only caller is _legacy/generate_nfl_props.py
and it appears in zero workflows - so it is dead code, not a live defect.
This replaces it from tables that are current: nfl_player_stats holds 2026
weeks 1-6, nfl_positional_defense is all-2026 and refreshed nightly.
╚═══════════════════════════════════════════════════════════════════════════╝

╔══ A MISSING FACTOR REPORTS NULL, IT DOES NOT REPORT 1.0 ═════════════════╗
The stale module returned a 1.00 multiplier both for "mid-tier defence" and
for "I have no data for this team", distinguishable only by a note nobody
read. Every factor here returns (value, note, applied) and an unavailable
factor is stored as null with a reason. A projection that quietly skipped
half its inputs must not look identical to one that used them all.
╚═══════════════════════════════════════════════════════════════════════════╝

NOT A BETTING SURFACE. These are displayed to explain our number, and
`inputs` + `model_version` exist so they can be graded against actuals
before any of it is allowed to move a tier or a price. A projection is not
an edge until it has been measured against the price.

TABLE NAME. Writes `nfl_engine_player_projections`, not the obvious
`nfl_player_projections` - that name is TAKEN by a live third-party fantasy
feed (espn_fantasy + sleeper, 15,356 rows, refreshed nightly, keyed by
source/season/week with no game_id). Keeping both is deliberate: a vendor
projection sitting beside ours is the benchmark that says whether our number
is worth anything.

CLI
    python nfl_player_projection.py --date 2026-10-22 --dry-run
    python nfl_player_projection.py --date 2026-10-22
    python nfl_player_projection.py --days 7
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import statistics
import sys

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                          # noqa: BLE001
        pass

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}

MODEL_VERSION = 'nfl_player_proj_v1'

#: Recency weight. A QB's last five games carry more about his current form
#: than week 1 does, but a five-game window alone is noisy enough that one
#: 374-yard outlier moves it 20%. 60/40 toward L5 keeps form dominant without
#: letting a single game set the number.
W_L5 = 0.60
W_SEASON = 0.40

#: How hard a factor is allowed to pull. Real defensive spreads are large
#: (NFL TE yards allowed runs SEA 25.5 to DET 109.0, a 4.3x spread) but a
#: raw ratio would hand a 4x multiplier to one matchup, which is a scale
#: error dressed as a signal. Each factor is damped by an exponent and then
#: hard-clamped, so an extreme matchup moves the number meaningfully and
#: never absurdly.
DAMP_OPP = 0.60
CLAMP_OPP = (0.80, 1.25)
DAMP_ENV = 0.50
CLAMP_ENV = (0.88, 1.12)
CLAMP_HOME = (0.92, 1.08)

#: Home/away shrinkage. A player with three home games has a "home split"
#: that is mostly noise, so the observed ratio is pulled toward 1.0 by
#: n/(n+K). At K=6 a 3-game split keeps a third of its signal and a 10-game
#: split keeps most of it.
HOME_SHRINK_K = 6

#: Plausibility guard. A projection outside these bounds means an input was
#: wrong, not that the QB is about to have a historic night, and it must not
#: reach a card (feedback_model_output_needs_a_plausibility_guard).
BOUNDS = {
    'proj_pass_yds': (60.0, 450.0),
    'proj_pass_att': (10.0, 60.0),
    'proj_pass_cmp': (5.0, 45.0),
    'proj_pass_tds': (0.0, 5.0),
    'proj_ints': (0.0, 3.0),
    'proj_rush_yds': (-5.0, 120.0),
}

#: QB stat -> the column on nfl_positional_defense (position='QB') that says
#: what this defence gives up to the position. `_blended` where it exists:
#: those are shrunk toward the prior season, which is what makes a week-6
#: number usable at all.
OPP_COL = {
    'proj_pass_yds': 'pass_yds_pg_blended',
    'proj_pass_tds': 'pass_td_pg_blended',
    'proj_ints': 'pass_int_pg_blended',
    'proj_pass_att': 'pass_att_pg',
    'proj_pass_cmp': 'pass_cmp_pg',
    'proj_rush_yds': 'rush_yds_pg_blended',
}

#: Which raw per-game stat feeds each projection.
STAT_COL = {
    'proj_pass_yds': 'passing_yards',
    'proj_pass_tds': 'passing_tds',
    'proj_ints': 'interceptions',
    'proj_pass_att': 'attempts',
    'proj_pass_cmp': 'completions',
    'proj_rush_yds': 'rushing_yards',
}

#: Game environment moves volume stats, not efficiency ones. A low total is
#: Andy's "slow wear and tear game bleeding clock" - fewer snaps, fewer
#: attempts, fewer yards. Interceptions are left alone: nothing establishes
#: that a slow game changes a QB's pick rate, and inventing a coefficient for
#: it would be a number with no evidence behind it.
ENV_SENSITIVE = {'proj_pass_yds', 'proj_pass_att', 'proj_pass_cmp',
                 'proj_pass_tds'}

#: A team expected to trail throws more. Applied to volume only, scaled by
#: the spread, and deliberately small - this is a documented prior, not a
#: measured coefficient.
TRAIL_PER_POINT = 0.006
CLAMP_TRAIL = (0.95, 1.09)


def page(tbl, params, cap=200000):
    out, off = [], 0
    while off < cap:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, params=q,
                         timeout=180)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{tbl} {r.status_code} {r.text[:200]}')
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def et_today():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo('America/New_York')).date()
    except Exception:                                          # noqa: BLE001
        return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)).date()


def _damp_clamp(ratio, damp, clamp):
    """ratio ** damp, clamped. Keeps direction, kills the magnitude blowups."""
    if ratio is None or ratio <= 0:
        return None
    out = ratio ** damp
    return max(clamp[0], min(clamp[1], out))


# ─── name ↔ abbrev ──────────────────────────────────────────────────────────
def check_abbrev_vocabularies(ctx, stats, defrows):
    """NFL abbreviates in all three tables - so VERIFY they abbreviate alike.

    Unlike NBA/NHL, nfl_game_context.home_team is already 'CHI', not
    'Chicago Bears', so no name bridge is needed. That makes it tempting to
    assume the three vocabularies are identical. They are not guaranteed to
    be: nfl_player_stats spells the Rams 'LA', and 'LAR' is equally common
    upstream.

    A raw-string join across feeds that disagree by one team is exactly the
    STL defect (project_nhl_stl_name_mismatch_1013), where a silent
    `continue` cost one team all its odds and all its tendencies for weeks.
    So this reports the disagreement loudly instead of letting a team fall
    through to "no starter" and disappear.
    """
    c = {str(g.get(k)) for g in ctx for k in ('home_team', 'away_team')
         if g.get(k)}
    sset = {str(r.get('team')) for r in stats if r.get('team')}
    dset = {str(r.get('team')) for r in defrows if r.get('team')}
    problems = []
    for label, other in (('player_stats', sset), ('positional_defense', dset)):
        missing = sorted(t for t in c if t not in other)
        if missing:
            problems.append(f'{label} has no row spelled: {missing}')
    return problems


# ─── factors ────────────────────────────────────────────────────────────────
def baseline(games, col):
    """Recency-weighted per-game average, newest games first.

    Returns (value, note, applied). Fewer than two games is not a baseline,
    it is one game with extra steps, so it reports unavailable.
    """
    vals = [_f(g.get(col)) for g in games]
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return None, f'only {len(vals)} game(s) logged', False
    l5 = vals[:5]
    m_l5 = statistics.fmean(l5)
    m_all = statistics.fmean(vals)
    val = W_L5 * m_l5 + W_SEASON * m_all
    return (val,
            f'L{len(l5)} {m_l5:.1f} x{W_L5:g} + season {m_all:.1f} '
            f'x{W_SEASON:g} over {len(vals)} games', True)


def opp_factor(defrows, opp_abbrev, key):
    """What this defence gives up to QBs, against the league average.

    Andy's "Bucs give up 100-110" step. The league mean is computed from the
    same 32 rows, so the factor is relative to the population it came from
    rather than to a constant that would drift as the season does.
    """
    col = OPP_COL.get(key)
    if not col:
        return None, 'no opponent column for this stat', False
    vals = [_f(r.get(col)) for r in defrows]
    vals = [v for v in vals if v is not None]
    if len(vals) < 10:
        return None, f'league has only {len(vals)} rated defences', False
    league = statistics.fmean(vals)
    mine = next((_f(r.get(col)) for r in defrows
                 if str(r.get('team')) == str(opp_abbrev)), None)
    if mine is None:
        return None, f'no {opp_abbrev} defence row', False
    if league <= 0:
        return None, 'league mean is zero', False
    raw = mine / league
    fac = _damp_clamp(raw, DAMP_OPP, CLAMP_OPP)
    rank = sorted(vals, reverse=True).index(mine) + 1 if mine in vals else None
    return (fac,
            f'{opp_abbrev} allows {mine:.1f} vs league {league:.1f} '
            f'(raw {raw:.3f}' + (f', rank {rank}/{len(vals)} most allowed'
                                 if rank else '') + ')', True)


def home_factor(games, col, is_home):
    """The player's own home/away split, shrunk toward 1.0 by sample.

    Andy's "Saquon typically does well at home". With six games played the
    raw split is mostly noise, so n/(n+K) decides how much of it survives.
    """
    h = [_f(g.get(col)) for g in games if g.get('_home') is True]
    a = [_f(g.get(col)) for g in games if g.get('_home') is False]
    h = [v for v in h if v is not None]
    a = [v for v in a if v is not None]
    if len(h) < 2 or len(a) < 2:
        return None, (f'needs 2+ games each side, has {len(h)} home / '
                      f'{len(a)} away'), False
    mh, ma = statistics.fmean(h), statistics.fmean(a)
    if mh <= 0 or ma <= 0:
        return None, 'a split average is zero', False
    raw = (mh / ma) if is_home else (ma / mh)
    n = min(len(h), len(a))
    shrunk = 1.0 + (raw - 1.0) * (n / (n + HOME_SHRINK_K))
    fac = max(CLAMP_HOME[0], min(CLAMP_HOME[1], shrunk))
    return (fac,
            f'home {mh:.1f} ({len(h)}g) vs away {ma:.1f} ({len(a)}g), '
            f'raw {raw:.3f} shrunk to {shrunk:.3f} at n={n}', True)


def env_factor(close_total, league_total, key):
    """Andy's "the total is low ... bleeding clock" step.

    Only touches volume stats. An efficiency or turnover stat gets no
    environment adjustment, because nothing measured says it should.
    """
    if key not in ENV_SENSITIVE:
        return None, 'stat is not volume-driven', False
    t, lg = _f(close_total), _f(league_total)
    if t is None or lg is None or lg <= 0:
        return None, 'no posted total', False
    raw = t / lg
    fac = _damp_clamp(raw, DAMP_ENV, CLAMP_ENV)
    direction = 'higher-scoring' if raw > 1 else 'lower-scoring'
    return (fac, f'total {t:g} vs league {lg:.1f} - {direction} script '
                 f'(raw {raw:.3f})', True)


def trail_factor(spread_for_team, key):
    """A team expected to trail throws more. Volume only, small, documented.

    `spread_for_team` is positive when this team is the underdog.
    """
    if key not in {'proj_pass_att', 'proj_pass_cmp', 'proj_pass_yds'}:
        return None, 'stat is not game-script sensitive', False
    s = _f(spread_for_team)
    if s is None:
        return None, 'no posted spread', False
    fac = 1.0 + TRAIL_PER_POINT * s
    fac = max(CLAMP_TRAIL[0], min(CLAMP_TRAIL[1], fac))
    role = 'underdog' if s > 0 else ('favourite' if s < 0 else 'pick-em')
    return (fac, f'{abs(s):g}-point {role} '
                 f'({TRAIL_PER_POINT:g}/pt, clamped)', True)


# ─── one player ─────────────────────────────────────────────────────────────
def project_player(games, defrows, opp_abbrev, is_home, close_total,
                   league_total, spread_for_team):
    """Returns (values dict, inputs dict). Any stat whose baseline is
    unavailable is OMITTED rather than guessed."""
    values, inputs = {}, {}
    for key, col in STAT_COL.items():
        base, bnote, bok = baseline(games, col)
        if not bok:
            inputs[key] = {'skipped': bnote}
            continue
        facs, notes = [], {'baseline': f'{base:.2f} - {bnote}'}
        for label, (fac, note, ok) in (
                ('opponent', opp_factor(defrows, opp_abbrev, key)),
                ('home_away', home_factor(games, col, is_home)),
                ('environment', env_factor(close_total, league_total, key)),
                ('game_script', trail_factor(spread_for_team, key))):
            if ok and fac is not None:
                facs.append(fac)
                notes[label] = f'x{fac:.3f} - {note}'
            else:
                # Explicit null, never a silent 1.0.
                notes[label] = f'not applied - {note}'
        val = base
        for f in facs:
            val *= f
        lo, hi = BOUNDS.get(key, (None, None))
        if lo is not None and not (lo <= val <= hi):
            notes['PLAUSIBILITY'] = (f'{val:.1f} outside [{lo}, {hi}] - '
                                     f'dropped, an input is wrong')
            inputs[key] = notes
            continue
        values[key] = round(val, 2)
        notes['result'] = f'{val:.2f} from {len(facs)} applied factor(s)'
        inputs[key] = notes
    return values, inputs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    ap.add_argument('--days', type=int, default=1,
                    help='how many days forward from --date')
    ap.add_argument('--dry-run', action='store_true', dest='dry')
    a = ap.parse_args()

    start = (dt.date.fromisoformat(a.date) if a.date else et_today())
    end = start + dt.timedelta(days=max(0, a.days - 1))
    print(f'=== nfl_player_projection · {MODEL_VERSION} · '
          f'{start}..{end} ===')

    ctx = page('nfl_game_context',
               {'select': '*',
                'and': f'(game_date.gte.{start},game_date.lte.{end})'})
    if not ctx:
        print('  no NFL games in window')
        return 0
    season = max(int(c['season']) for c in ctx if c.get('season'))
    print(f'  {len(ctx)} game(s) · season {season}')

    defrows = [r for r in page('nfl_positional_defense',
                               {'select': '*', 'position': 'eq.QB'})
               if int(r.get('season') or 0) == season]
    print(f'  opponent defence: {len(defrows)} QB rows for {season}')

    stats = page('nfl_player_stats',
                 {'select': '*', 'season': f'eq.{season}',
                  'position': 'eq.QB'})
    print(f'  player logs: {len(stats)} QB rows for {season}')

    for msg in check_abbrev_vocabularies(ctx, stats, defrows):
        print(f'  ** TEAM SPELLING MISMATCH ** {msg}')

    starters = [s for s in page('nfl_starters',
                                {'select': '*', 'position': 'eq.QB',
                                 'season': f'eq.{season}'})]
    by_team_week = {}
    for s in starters:
        try:
            wk = int(s.get('week'))
        except (TypeError, ValueError):
            continue
        by_team_week.setdefault(str(s.get('team')), {})[wk] = s
    print(f'  starters: {len(starters)} QB rows across '
          f'{len(by_team_week)} teams')

    # Home/away per historical player-game, from the schedule.
    sched = {}
    for c in page('nfl_game_context', {'select': '*', 'season': f'eq.{season}'}):
        try:
            wk = int(c.get('week'))
        except (TypeError, ValueError):
            continue
        h, aw = str(c.get('home_team') or ''), str(c.get('away_team') or '')
        if h:
            sched[(wk, h)] = True
        if aw:
            sched[(wk, aw)] = False
    totals = [_f(c.get('close_total')) for c in ctx]
    all_tot = [_f(c.get('close_total')) for c in
               page('nfl_game_context', {'select': '*', 'season': f'eq.{season}'})]
    all_tot = [t for t in all_tot if t]
    league_total = statistics.fmean(all_tot) if all_tot else None
    print(f'  league average total: '
          f'{league_total:.1f}' if league_total else '  no totals')

    by_player = collections.defaultdict(list)
    for r in stats:
        nm = r.get('player_display_name') or r.get('player_name')
        if not nm:
            continue
        try:
            wk = int(r.get('week'))
        except (TypeError, ValueError):
            continue
        r['_home'] = sched.get((wk, str(r.get('team'))))
        r['_week'] = wk
        by_player[nm].append(r)
    for nm in by_player:
        by_player[nm].sort(key=lambda x: -x['_week'])

    payload, skipped = [], []
    for g in sorted(ctx, key=lambda x: str(x.get('game_date'))):
        try:
            wk = int(g.get('week'))
        except (TypeError, ValueError):
            wk = None
        home_ab = str(g.get('home_team') or '')
        away_ab = str(g.get('away_team') or '')
        spread = _f(g.get('close_spread'))
        for side, team_ab, opp_ab in (('HOME', home_ab, away_ab),
                                      ('AWAY', away_ab, home_ab)):
            if not team_ab or not opp_ab:
                skipped.append(f'{g.get("game_id")} {side}: no abbrev')
                continue
            wk_map = by_team_week.get(team_ab, {})
            st = wk_map.get(wk) or (wk_map[max(wk_map)] if wk_map else None)
            if not st:
                skipped.append(f'{g.get("game_id")} {team_ab}: no starter')
                continue
            nm = st.get('player_name')
            games = by_player.get(nm, [])
            if len(games) < 2:
                skipped.append(f'{g.get("game_id")} {team_ab} {nm}: '
                               f'{len(games)} game log(s)')
                continue
            # close_spread is for the HOME team; positive = home underdog.
            sp_for = spread if side == 'HOME' else (
                -spread if spread is not None else None)
            vals, inputs = project_player(
                games, defrows, opp_ab, side == 'HOME',
                _f(g.get('close_total')), league_total, sp_for)
            if not vals:
                skipped.append(f'{g.get("game_id")} {nm}: no stat survived')
                continue
            payload.append({
                'game_id': str(g.get('game_id')),
                'game_date': str(g.get('game_date'))[:10],
                'season': season, 'week': wk,
                'player_name': nm, 'player_id': st.get('player_id'),
                'team': team_ab, 'opp_team': opp_ab,
                'position': 'QB', 'home_away': side,
                **vals,
                'inputs': inputs,
                'model_version': MODEL_VERSION,
                'updated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            })

    print(f'\n  projected {len(payload)} QB line(s) · skipped {len(skipped)}')
    for s in skipped[:10]:
        print(f'     skip: {s}')
    for p in payload:
        print(f'\n  {p["player_name"]} ({p["team"]} {p["home_away"]}) '
              f'vs {p["opp_team"]}')
        print(f'     yds {p.get("proj_pass_yds")}  att {p.get("proj_pass_att")}'
              f'  cmp {p.get("proj_pass_cmp")}  td {p.get("proj_pass_tds")}'
              f'  int {p.get("proj_ints")}  rush {p.get("proj_rush_yds")}')
        yd = p['inputs'].get('proj_pass_yds', {})
        for k in ('baseline', 'opponent', 'home_away', 'environment',
                  'game_script'):
            if k in yd:
                print(f'        pass_yds {k:<12}{yd[k]}')

    if a.dry:
        print('\n  --dry-run: nothing written')
        return 0
    if not payload:
        return 0
    r = requests.post(f'{SB}/rest/v1/nfl_engine_player_projections',
                      headers={**H_W,
                               'Prefer': 'resolution=merge-duplicates,'
                                         'return=minimal'},
                      params={'on_conflict': 'game_id,player_name,position'},
                      json=payload, timeout=120)
    print(f'\n  write {r.status_code} {r.text[:200]}')
    if r.status_code not in (200, 201, 204):
        return 1
    # A 204 is not a write. Read it back.
    back = page('nfl_engine_player_projections',
                {'select': '*',
                 'and': f'(game_date.gte.{start},game_date.lte.{end})'})
    print(f'  read-back: {len(back)} row(s) present for {start}..{end}')
    return 0 if back else 1


if __name__ == '__main__':
    sys.exit(main())
