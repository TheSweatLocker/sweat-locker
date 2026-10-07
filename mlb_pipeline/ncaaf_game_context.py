"""NCAAF game context pipeline — server-side analog of nfl_game_context.

Reads live NCAAF games from ncaaf_game_results (populated by
ncaaf_odds_pull), joins ncaaf_team_stats, computes SP+ and EPA-based
projections + confluence + sweat + primary_play, upserts to
ncaaf_game_context.

Model (v1 — Week 1 baseline, calibrates after Week 4):
  power_diff = (home_sp_overall - away_sp_overall)   OR fallback:
               (home_off_epa - home_def_epa) - (away_off_epa - away_def_epa)
  projected_spread = power_diff * K_PTS + HFA
  projected_total  = 52.0 baseline + roof/temp adjustments (CFB scores higher than NFL)

USAGE:
    python ncaaf_game_context.py           # today + next 7d
    python ncaaf_game_context.py --dry-run
"""
import argparse
import os
import sys
from datetime import datetime, date, timedelta, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


# Calibration constants — CFB has larger score variance than NFL.
# 2026-09-29 RECALIBRATED 0.85 → 0.94. This is the root-cause fix for the NCAAF
# dog bias and the inverted conviction curve.
#
# Measured on 171 leak-free graded 2026 games (ctx rows whose updated_at is
# strictly before kickoff_utc — 353 of 355 past rows qualify, so this is NOT
# the leak that blocked the earlier attempt in
# project_sp_plus_backtests_are_leaky_926: that one joined CURRENT sp_overall
# to past games, this one reads the projection the row actually carried
# pre-kickoff):
#
#   std(projected_spread) 14.85  vs  std(market) 16.54  vs  std(actual) 21.36
#   mean|model| 11.59            vs  mean|market| 16.49  vs  mean|actual| 19.24
#
# The model projects ~30% less margin than the market and ~40% less than
# reality. Compression is PROPORTIONAL, not saturating — the market/model
# magnitude ratio is 1.17-1.30 in every bucket from 3 to 38 points, and the
# model's range (-29 to +52) already spans the market's (-28 to +53). So a
# single multiplier is the right shape of fix.
#
# WHY THIS IS THE DOG BIAS. Compression can only ever under-project the
# FAVOURITE, so the model's disagreement with the market points at the dog
# almost by construction: |model| < |market| on 78-79% of games in BOTH
# directions. Dog-rate rises monotonically with disagreement size —
# 67.2% / 86.0% / 94.1% / 100% / 100% — and at |disagreement| >= 10 it is
# 100% dogs (n=43, going 21-22). Since sweat_score and conviction are driven
# by that same disagreement, THE ENGINE'S CONFIDENCE WAS ITS SCALE ERROR:
# conviction 70-80 ran 90.7% dogs and 48.8% ATS while conviction 0-60 ran
# 76.4% dogs and 56.4%. That is the inversion in
# project_football_engine_audit_929, explained.
#
# SIZING THE CORRECTION — and how uncertain it is.
# k = std(market)/std(model) on the SP+ path, estimated chronologically by
# game_date (NOT by season_week: that column is 0 on 62 of 181 rows, 34%
# unpopulated, and a first pass that split on it produced a fake clean
# convergence — the unlabeled rows are scattered across dates, not early ones):
#
#   through 09-07 (n=33)  k=1.090 → 0.93
#   through 09-14 (n=77)  k=0.991 → 0.84
#   through 09-21 (n=122) k=1.054 → 0.90
#   through 09-26 (n=171) k=1.114 → 0.95     <- full sample, the value taken
#
# So k WANDERS 0.99-1.11 and straddles 1.0. Chronological walk-forward gains
# are small and none is significant: 55.5%→57.9% (z +1.24→+1.78), 54.1%→54.7%,
# 47.7%→50.0%. Dog-rate fell in all three but only slightly.
#
# Sliced by stats_source the estimates DISAGREE materially — current-source
# rows give k=1.283 (K_PTS_SP 1.09, 1SE ±0.09) and prior_season_regressed give
# k=1.139 (0.97). Since every future row is 'current' after today's freshness
# fix, that slice is the forward-relevant one and argues for ~1.06-1.09. It is
# deliberately NOT taken: the slice is confounded with date (current-source rows
# cluster in specific weeks) and n=93.
#
# HONEST LIMIT. What is ROBUST is the DIRECTION and the mechanism: |model| <
# |market| on 78-79% of games in both directions, the market/model magnitude
# ratio is 1.17-1.30 in every bucket from 3 to 38 points, and dog-rate rises
# monotonically with disagreement. What is NOT well determined is the
# multiplier. No ATS result at any k clears the 2 SE bar. The justification for
# moving is the calibration defect, not a win-rate claim.
#
# 0.94 is therefore a deliberate PARTIAL step toward the full-sample estimate
# (0.95), chosen because it is small: on the live 67-game board it flips 3
# sides, all in games within ~1 point of the market, and moves the dog lean
# 61.2% → 56.7%. Re-measure after 2-3 weeks of de-compressed 'current'-source
# data before going further toward 1.06.
#
# DO NOT retune sweat_score/conviction thresholds in the same change — the
# deltas that feed them all shift here, so tiers must be re-measured against
# the new scale first.
K_PTS_SP = 0.94           # SP+ rating diff → spread points scaling
K_PTS_EPA = 5.5           # EPA diff → spread points (fallback)
HOME_FIELD_PTS = 2.8      # CFB HFA slightly higher than NFL
BASE_TOTAL = 52.0         # CFB avg total higher than NFL

# 2026-10-03 · sanity ceiling on |projected_spread + close_spread|. Above this
# the model-edge side covered 47.7% (n=44) vs 58.8% below it (n=160) on 2026
# graded games — a huge disagreement with the market means our projection is
# uninformed, not that we found value. See compute_sweat_score.
EDGE_SANITY_MAX_PTS = float(os.environ.get('EDGE_SANITY_MAX_PTS', '10'))


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(v) if v is not None else None
    except (TypeError, ValueError): return None


def load_upcoming(days_ahead: int = 21) -> list:
    """Pull upcoming ncaaf_game_results within horizon.
    2026-08-23: extended 10→21 days so 'Next Week' tab renders. Week 1
    opener (Aug 30) was inside the 10d horizon but Week 2 (Sep 4-6) fell
    outside, leaving Next Week tab empty for a week straight."""
    today = _et_now().date().isoformat()
    horizon = (_et_now() + timedelta(days=days_ahead)).date().isoformat()
    # 2026-08-28: paginate — 21d × ~60 CFB games/wk (busy Sat) can
    # exceed 200; prior fixed limit silently dropped later games from
    # "Next Week" tab in-season. Chunk in 1000s.
    out = []
    for off in range(0, 5000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/ncaaf_game_results?'
            f'game_date=gte.{today}&game_date=lte.{horizon}'
            f'&select=*&order=game_date.asc&limit=1000&offset={off}',
            headers=H_READ, timeout=15,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not isinstance(chunk, list): break
        out.extend(chunk)
        if len(chunk) < 1000: break
    return out


def load_returning_production(season: int) -> dict:
    """2026-08-09: fetch {team → {returning_offense_pct, returning_defense_pct}}
    from ncaaf_returning_production. Falls back silently if table doesn't
    exist yet (migration pending) or season not backfilled."""
    r = requests.get(
        f'{SB}/rest/v1/ncaaf_returning_production?season=eq.{season}&select=*',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200: return {}
    rows = r.json() if isinstance(r.json(), list) else []
    return {row['team']: row for row in rows}


def _load_ncaaf_team_aliases() -> dict:
    """Return {ctx_name: cfbd_canonical} from ncaaf_team_name_aliases.
    Silent-empty on error so a missing table doesn't kill the builder.
    2026-09-10: fixes 4+ FBS teams (App State/Hawai'i/San José State/
    Virginia Tech) that were dropping SP+ due to CFBD naming variants."""
    try:
        r = requests.get(f'{SB}/rest/v1/ncaaf_team_name_aliases',
            headers=H_READ, params={'select': 'ctx_name,cfbd_canonical'},
            timeout=8)
        if r.status_code == 200:
            return {row['ctx_name']: row['cfbd_canonical'] for row in r.json()
                    if row.get('ctx_name') and row.get('cfbd_canonical')}
    except Exception:
        pass
    return {}


def _norm_team(name: str) -> str:
    """Fold a team name to a comparison key.

    Strips diacritics/punctuation, lowercases, and expands the St/St.
    abbreviation to 'state' so 'Youngstown St' and 'Youngstown State'
    collapse together. Comparison only -- never stored.
    """
    import re, unicodedata
    s = unicodedata.normalize('NFKD', str(name or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9&\s]", ' ', s).lower()
    toks = ['state' if t in ('st', 'sts') else t for t in s.split()]
    return ' '.join(toks)


# 2026-09-26: every normalised team name seen in any season or alias row.
# The suffix-strip guard in _resolve_team_key consults this in addition to
# the index it is searching, so a school that exists in one season cannot
# be eaten as a mascot suffix while resolving against another season that
# happens not to list it. Populated by load_team_stats.
_KNOWN_TEAM_KEYS: set = set()


def _register_known_teams(names) -> None:
    for n in names:
        k = _norm_team(n)
        if k:
            _KNOWN_TEAM_KEYS.add(k)


def _resolve_team_key(name: str, index: dict) -> str | None:
    """Map an odds-pipe team name onto a CFBD canonical key.

    `index` is {normalized_name: canonical_name}. Resolution order:
      1. exact normalized match
      2. progressive suffix strip -- drop trailing words one at a time,
         because the odds feed appends mascots ('Virginia Tech Hokies',
         'Youngstown St Penguins', 'Sacramento State Hornets').

    THE AMBIGUITY GUARD IS THE WHOLE POINT. A strip is accepted only
    when the shortened form is an exact key AND no OTHER CFBD team name
    extends it. Without that check the strip silently eats meaningful
    qualifiers rather than mascots:

        'Houston Baptist Huskies' -> 'Houston'   (+11.6 SP+, Big 12)
                                     but the school is FCS Houston Christian
        'Louisiana Monroe'        -> 'Louisiana' (-6.6 SP+)
                                     but ULM's real SP+ is -29.3

    Both were caught in testing before shipping. 'Houston Christian'
    extends 'houston' and 'Louisiana Tech' extends 'louisiana', so the
    guard now refuses both and they fall to the alias table instead.

    Returns None when nothing resolves -- deliberately. A blank tile is
    recoverable; another team's rating presented as this team's is not.
    Callers record the Nones (see _TeamStats.unresolved) so gaps get
    reported rather than silently rendering a game with NULL stats.
    """
    key = _norm_team(name)
    if key in index:
        return index[key]
    toks = key.split()
    for cut in range(len(toks) - 1, 0, -1):
        short = ' '.join(toks[:cut])
        if short not in index:
            continue
        pre = short + ' '
        # 2026-09-26 · THE GUARD IS ONLY AS GOOD AS THE INDEX IT CHECKS.
        #
        # This refusal worked perfectly against the CURRENT-season index,
        # where 'Houston Christian' is itself a key, so stripping to
        # 'Houston' was correctly rejected. But load_team_stats resolves
        # against the PRIOR season too, and the prior index contains only
        # teams that had rows that year — FCS schools mostly do not. With
        # nothing extending 'houston' in that index, the strip was
        # accepted and the FCS school inherited the FBS school's rating:
        #
        #     Houston Christian      -> Houston 2025        SP+  +7.4
        #     North Carolina Central -> North Carolina 2025 SP+  -6.6
        #
        # Consequence on the live board: the model had Houston Christian
        # (+7.4) BETTER than North Texas (-9.2) and projected them to win
        # by 11.3, against a market line of +38.5. That is not a
        # compressed projection, it is the wrong team's rating, and it is
        # the mechanism behind the fabricated dog edges on FBS-vs-FCS
        # games. The docstring above already named this exact failure as
        # the reason the guard exists — it just was not consulted widely
        # enough.
        #
        # _KNOWN_TEAM_KEYS accumulates every team name seen in ANY season
        # and in the alias table, so a name that is a real school
        # somewhere can never be swallowed as a mascot suffix here.
        if any(k != short and k.startswith(pre) for k in index):
            return None         # ambiguous truncation -- refuse outright
        if any(k != short and k.startswith(pre) for k in _KNOWN_TEAM_KEYS):
            return None         # known elsewhere -- still ambiguous
        return index[short]
    return None


class _TeamStats(dict):
    """{team: stats_row} that also resolves odds-pipe name variants.

    Exact keys behave like a plain dict. A miss falls through to
    _resolve_team_key (mascot strip / St->State / diacritics) and, on a
    hit, CACHES the row under the queried name so repeated lookups stay
    O(1). Misses are recorded in `.unresolved` so the builder can report
    which teams silently lost their stats instead of just emitting a
    game with every away_* column NULL.

    Resolution is lazy on purpose: the previous eager-alias approach
    inserted extra keys up front, which is what inflated the dict and
    dragged _populated_pct under the 60% floor (see the 2026-08-29 note
    below).
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._index: dict = {}
        self.unresolved: set = set()

    def build_index(self) -> None:
        self._index = {}
        for k in list(self.keys()):
            self._index.setdefault(_norm_team(k), k)

    def _resolve(self, name):
        if not self._index:
            self.build_index()
        canon = _resolve_team_key(name, self._index)
        if canon is None:
            self.unresolved.add(str(name))
            return None
        row = dict.get(self, canon)
        if row is not None:
            self[name] = row          # memoize
        return row

    def __missing__(self, name):
        row = self._resolve(name)
        if row is None:
            raise KeyError(name)
        return row

    def get(self, name, default=None):
        row = dict.get(self, name, None)
        if row is not None:
            return row
        row = self._resolve(name)
        return default if row is None else row

    def __contains__(self, name):
        return dict.__contains__(self, name) or self._resolve(name) is not None


def load_team_stats(season: int) -> dict:
    """Return {team: stats_row} for the season, merged with defense stats.

    2026-08-29: PRIOR-SEASON FALLBACK for volumetric fields. Current
    season (e.g. 2026) has sp_overall populated (preseason SP+) but
    pass_yards/penalties/def_ppg/etc are all NULL until games are
    played. Previously the "current has sp_overall → use current"
    logic wiped every volumetric field. Now we merge: fetch BOTH
    current and prior season stats, then for each team fill any
    NULL current field from prior. This means Alabama's 2025 defense
    numbers (242 pass_ypg allowed) render on their 2026 preview cards
    until enough 2026 data accumulates.

    2026-09-10: NCAAF TEAM-NAME ALIASING. ncaaf_team_stats stores CFBD
    canonical names ("App State", "Hawai'i") while ncaaf_game_context
    stores odds-pipe names ("Appalachian State", "Hawaii"). Add alias
    lookups to the returned dict so the ctx-side name resolves the row
    just as reliably as the canonical. Alias table:
    ncaaf_team_name_aliases (migration 20260910e).
    """
    def _fetch(s: int) -> dict:
        r = requests.get(
            f'{SB}/rest/v1/ncaaf_team_stats?season=eq.{s}&season_type=eq.regular&select=*',
            headers=H_READ, timeout=15,
        )
        base = {row['team']: row for row in r.json()} if r.status_code == 200 else {}
        # Register BEFORE any resolution happens, so the guard already
        # knows about this season's schools when another season is walked.
        _register_known_teams(base.keys())
        out = _TeamStats(base)
        # Genuine synonyms an algorithm cannot derive: FIU ->
        # Florida International, UConn, UL Monroe, Southern Miss.
        _aliases = _load_ncaaf_team_aliases()
        _register_known_teams(_aliases.keys())
        for ctx_name, canon in _aliases.items():
            if canon in base and ctx_name not in base:
                out[ctx_name] = base[canon]
        # Mechanical variants (mascot suffix, St/State, diacritics) are
        # resolved on demand by _TeamStats -- see _resolve_team_key.
        out.build_index()
        # 2026-08-29: ONLY enrich existing D1 teams with defense fields.
        # ncaaf_team_defense_stats may include D2/D3 rows w/ 0.0000 EPA
        # (they play FBS teams occasionally). setdefault previously added
        # them as new team entries — inflated dict to ~700 teams, drove
        # `_populated_pct` below 60%, triggered "source=none" fallback.
        try:
            d = requests.get(
                f'{SB}/rest/v1/ncaaf_team_defense_stats?season=eq.{s}&season_type=eq.regular&select=*',
                headers=H_READ, timeout=15)
            if d.status_code == 200:
                for row in (d.json() or []):
                    team = row.get('team')
                    if not team or team not in out: continue
                    out[team].update({
                        'def_ppg':                  row.get('def_ppg'),
                        'def_pass_ypg':             row.get('def_pass_ypg'),
                        'def_rush_ypg':             row.get('def_rush_ypg'),
                        'def_pass_epa_allowed':     row.get('def_pass_epa_allowed'),
                        'def_rush_epa_allowed':     row.get('def_rush_epa_allowed'),
                        'def_success_rate_allowed': row.get('def_success_rate_allowed'),
                        'def_explosiveness_allowed': row.get('def_explosiveness_allowed'),
                    })
        except Exception:
            pass
        return out

    # Both seasons are fetched before either is resolved against, so the
    # guard sees the union of school names rather than one season's view.
    current = _fetch(season)
    prior = _fetch(season - 1)

    # 2026-09-02 VOLUMETRIC-BLOCK FIX: volumetric aggregates (pass_yards,
    # rush_yards, penalties, def_sacks, etc.) MUST come as a coherent
    # block from the same season with `games` from the same season. The
    # prior "fill NULLs" merge would take Stanford 2026 pass_yards=310
    # (1 game played), fall through NULL games=None with prior's games=12,
    # then compute pass_yds_pg = 310/12 = 25.8 — grossly wrong. Now:
    # if current has volumetric data but NO games count, WIPE current's
    # volumetric fields so the fallback fills the coherent prior-season
    # block instead. Rates (sp_overall, off_epa_per_play) are per-play,
    # not per-game, so they stay from current if populated.
    #
    # 2026-09-09 BLEND: snapshot raw current volumetric BEFORE wiping so
    # _blend_pg can use them (paired with real cur_games from
    # load_team_games_played). Merged/wiped fields kept for consumers
    # that haven't been ported to the blend yet.
    VOLUMETRIC_FIELDS = {
        'games', 'pass_yards', 'pass_tds', 'pass_completions', 'pass_attempts',
        'pass_ints', 'rush_yards', 'rush_tds', 'rush_attempts', 'first_downs',
        'third_down_conv', 'third_downs', 'fourth_down_conv', 'fourth_downs',
        'penalties', 'penalty_yards', 'turnovers', 'fumbles_lost',
        'possession_time_sec', 'def_sacks', 'def_ints', 'def_fumbles_rec',
        'def_ppg', 'def_pass_ypg', 'def_rush_ypg', 'def_pass_epa_allowed',
        'def_rush_epa_allowed', 'def_success_rate_allowed',
        'def_explosiveness_allowed',
    }
    # 2026-09-09 BLEND SUPPORT: also stash raw current + prior values under
    # `_cur_*` / `_pri_*` keys so build_context_row can do a weighted per-game
    # blend for weeks 1-3 (see _blend_pg helper). Existing merged fields kept
    # for backward compat with everything downstream that hasn't been ported
    # to the blend yet.
    _BLEND_TRACKED = (
        # volumetric season totals (blended by division by games)
        'pass_yards', 'pass_tds', 'rush_yards', 'rush_tds',
        'def_sacks', 'def_ints', 'penalties', 'penalty_yards',
        'turnovers', 'possession_time_sec',
        # pre-averaged defense stats (blended straight, not per-game)
        'def_ppg', 'def_pass_ypg', 'def_rush_ypg',
        'def_pass_epa_allowed', 'def_rush_epa_allowed',
    )
    # Snapshot current volumetric for blend BEFORE the wipe below zeroes them.
    _cur_snapshot = {}
    for team, row in current.items():
        _cur_snapshot[team] = {f: row.get(f) for f in _BLEND_TRACKED}
    for team, row in current.items():
        if row.get('games') is None:
            # Volumetric fields without a games count are unusable — wipe
            # them so the merge below fills the full prior-season block.
            for f in VOLUMETRIC_FIELDS:
                if f != 'games' and row.get(f) is not None:
                    row[f] = None

    # Merge: for each team seen in either, fill NULL current fields from prior.
    # 2026-09-09: authoritative current-season games count from
    # ncaaf_game_results — CFBD's stats.games is None early season.
    games_played = load_team_games_played(season)

    # _TeamStats, not {} — a plain dict here would throw away the
    # odds-pipe name resolution that load_team_stats just set up, which
    # is how 'FIU' and 'Sacramento State Hornets' ended up with every
    # stat NULL even though CFBD had the rows.
    merged = _TeamStats()
    all_teams = set(current.keys()) | set(prior.keys())
    for team in all_teams:
        cur_row = current.get(team) or {}
        pri_row = prior.get(team) or {}
        merged_row = dict(cur_row)
        for k, v in pri_row.items():
            if merged_row.get(k) is None:
                merged_row[k] = v
        # Real current-season games (from game_results) — falls back to CFBD's
        # stats.games if game_results has 0 (D2/D3 not in results table).
        merged_row['_cur_games'] = games_played.get(team, cur_row.get('games') or 0)
        merged_row['_pri_games'] = pri_row.get('games') or 0
        merged_row['_cur_season'] = season
        merged_row['_pri_season'] = season - 1
        # Pull cur values from PRE-WIPE snapshot so blend sees real data
        _cur_snap = _cur_snapshot.get(team, {})
        for f in _BLEND_TRACKED:
            merged_row[f'_cur_{f}'] = _cur_snap.get(f)
            merged_row[f'_pri_{f}'] = pri_row.get(f)
        merged[team] = merged_row
    merged.build_index()
    return merged


# 2026-09-09: Weeks 1-3 blend cutover. Games 0-2 = weighted blend prior+current,
# Game 3+ = 100% current. User request 9/9 to stop being called out on stats
# not matching ESPN (ESPN shows current-only; ours showed prior-only pre-fix).
BLEND_UNTIL_GAMES = 3


def load_team_games_played(season: int) -> dict:
    """Return {team: games_completed} for current season from ncaaf_game_results.

    Why: CFBD's ncaaf_team_stats.games field is None until the CFBD season
    aggregation cron runs (usually mid-October, after enough sample lands).
    Meanwhile ncaaf_game_results is populated per-game as scores come in, so
    counting completed games there gives the true early-season sample size —
    which drives the _blend_pg weighting for Weeks 1-3.
    """
    # 2026-09-13 PAGINATION FIX. Previously `limit=5000` was silently
    # capped by PostgREST at 1000, and 2025 has 3,829 game_results rows
    # (bowls + playoff + FCS-vs-FBS). Result: only the first 1,000 rows
    # (early-season weeks) were tallied, so Texas ended up at 3 games,
    # Tennessee at 4, Ohio State at 3 for the 2025 prior-season fallback.
    # _blend_pg then computed pass_yds_pg = pass_yards_TOTAL / 3, which
    # rendered Texas averaging 1,086 pass yds/game on the 9/12 Ohio State
    # card — obvious nonsense. Range-header pagination guarantees every
    # game is counted regardless of season size (bowls, FCS opponents,
    # future 16-team CFP).
    out: dict = {}
    for page in range(10):  # 10*1000 = 10k safety cap; a season is ~4k rows
        lo = page * 1000
        r = requests.get(
            f'{SB}/rest/v1/ncaaf_game_results',
            headers={**H_READ, 'Range': f'{lo}-{lo+999}', 'Range-Unit': 'items'},
            params={'season': f'eq.{season}',
                    'home_score': 'not.is.null',
                    'select': 'home_team,away_team'},
            timeout=15,
        )
        if r.status_code not in (200, 206): break
        chunk = r.json() if isinstance(r.json(), list) else []
        for row in chunk:
            if not isinstance(row, dict): continue
            for k in ('home_team', 'away_team'):
                t = row.get(k)
                if t: out[t] = out.get(t, 0) + 1
        if len(chunk) < 1000: break
    return out


def load_team_rolling_form(season: int, window: int = 4) -> dict:
    """Return {team: {'ppg', 'pa', 'total_avg', 'over_rate'}} rolled over the
    team's last-N completed games (this season). Used by build_context_row
    to stamp home_l4_/away_l4_ features onto ncaaf_game_context — same
    features the ncaaf_total_logreg trainer computes in _compute_rolling.

    2026-09-15 v1.06: adds real non-market signal to the LR total model.
    Trainer had these features already but omitted them because ctx didn't
    persist them (would resolve to imputer median at inference = no signal).
    This helper closes that loop.

    Bulk-loads the season's games once + walks per-team in chronological
    order so N teams × N games is one paginated read, not N × 4 queries.
    """
    from collections import defaultdict, deque
    hist: dict = defaultdict(lambda: deque(maxlen=window))
    all_games: list = []
    for page in range(10):  # 10k safety cap; a season is ~4k rows
        lo = page * 1000
        r = requests.get(
            f'{SB}/rest/v1/ncaaf_game_results',
            headers={**H_READ, 'Range': f'{lo}-{lo+999}', 'Range-Unit': 'items'},
            params={'season': f'eq.{season}',
                    'home_score': 'not.is.null', 'away_score': 'not.is.null',
                    'close_total': 'not.is.null',
                    'select': 'game_date,game_id,home_team,away_team,'
                              'home_score,away_score,close_total',
                    'order': 'game_date.asc'},
            timeout=20,
        )
        if r.status_code not in (200, 206): break
        chunk = r.json() if isinstance(r.json(), list) else []
        all_games.extend(chunk)
        if len(chunk) < 1000: break

    all_games.sort(key=lambda g: (g.get('game_date') or '', g.get('game_id') or ''))
    for g in all_games:
        home = g.get('home_team'); away = g.get('away_team')
        hs = _f(g.get('home_score')); as_ = _f(g.get('away_score'))
        ct = _f(g.get('close_total'))
        if not home or not away or hs is None or as_ is None:
            continue
        actual_tot = hs + as_
        over_hit = None if ct is None else (1.0 if actual_tot > ct else 0.0)
        hist[home].append({'pf': hs, 'pa': as_, 'tot_line': ct, 'over_hit': over_hit})
        hist[away].append({'pf': as_, 'pa': hs, 'tot_line': ct, 'over_hit': over_hit})

    # Snapshot the final rolling window per team (last N completed games).
    out: dict = {}
    for team, dq in hist.items():
        if not dq: continue
        pfs = [g['pf'] for g in dq]
        pas = [g['pa'] for g in dq]
        tots = [g['tot_line'] for g in dq if g['tot_line'] is not None]
        overs = [g['over_hit'] for g in dq if g['over_hit'] is not None]
        out[team] = {
            'ppg':       sum(pfs) / len(pfs) if pfs else None,
            'pa':        sum(pas) / len(pas) if pas else None,
            'total_avg': sum(tots) / len(tots) if tots else None,
            'over_rate': sum(overs) / len(overs) if overs else None,
        }
    return out


def _blend_pg(stats: dict, field: str, per_game: bool = True) -> Optional[float]:
    """Blend a volumetric field between current + prior season by team's
    current-season games played. weight_current = min(1.0, cur_games / 3).

    per_game=True: field is a season total (pass_yards etc) — divide by games
                    for each side, then weighted average.
    per_game=False: field is already a per-game rate (def_ppg etc) — weighted
                    average directly.

    Returns None if neither side has usable data.

    2026-09-09 DATA-GAP GUARD: CFBD reports 0.0 for defense stats
    (def_pass_ypg / def_rush_ypg) even after games are played until its
    aggregation cron runs. Verified against Kansas 2026: def_pass_ypg=0.0
    with games=2 — real teams don't allow literally 0 passing yards.
    Treat cur_val==0 as unpopulated when field is a defense stat OR when
    the yardage is implausibly zero across ≥1 game. Falls through to
    prior-only for those cases.
    """
    cur_games = stats.get('_cur_games') or 0
    pri_games = stats.get('_pri_games') or 0
    cur_val = stats.get(f'_cur_{field}')
    pri_val = stats.get(f'_pri_{field}')

    # Treat exact-zero as unpopulated for stats that shouldn't ever be
    # actually zero across ≥1 game played (all yardage, all PPG). Only
    # counters like def_ints / turnovers can legitimately be 0.
    _NEVER_ZERO_FIELDS = {
        'pass_yards', 'rush_yards', 'penalty_yards', 'possession_time_sec',
        'def_ppg', 'def_pass_ypg', 'def_rush_ypg',
    }
    if cur_val == 0 and field in _NEVER_ZERO_FIELDS:
        cur_val = None

    if per_game:
        cur_pg = (cur_val / cur_games) if (cur_games and cur_val is not None) else None
        pri_pg = (pri_val / pri_games) if (pri_games and pri_val is not None) else None
    else:
        cur_pg = float(cur_val) if cur_val is not None else None
        pri_pg = float(pri_val) if pri_val is not None else None

    if cur_pg is None and pri_pg is None: return None
    if cur_pg is None:  result = round(pri_pg, 2)
    elif pri_pg is None:  result = round(cur_pg, 2)
    elif cur_games >= BLEND_UNTIL_GAMES:
        result = round(cur_pg, 2)
    else:
        w = cur_games / float(BLEND_UNTIL_GAMES)
        result = round(w * cur_pg + (1 - w) * pri_pg, 2)

    # 2026-09-12 SANITY CLAMP. Andy caught Jerry writing "Texas averages
    # 36 penalties per game" in the Ohio State @ Texas read — impossible
    # (real NCAAF penalty rate is 5-8/game). Root cause: 101 games in
    # ncaaf_game_context had penalty values >15/pg, either the CFBD
    # source mis-labeling season totals as per-game, or a games-played
    # denominator missing when the volumetric field was already pre-
    # divided upstream. Rather than trust the ingest to be perfect,
    # cap known-per-game stats at physical limits — any value above is
    # a data bug, return None so downstream signal composers + Jerry
    # prompts don't see garbage. Better nothing than nonsense in prose.
    _PG_CAPS = {
        'penalties': 15,          # real NCAAF max ~12/game
        'penalty_yards': 200,     # real NCAAF max ~150/game
        'turnovers': 8,           # real max ~5/game
        # 2026-09-13: added after games_played pagination bug produced
        # pass_yds_pg=1086 for Texas (season total / 3 games). Even with
        # the fix landed, cap out-of-band values so any future upstream
        # bug can't render as user-visible garbage. Real CFB extremes:
        # ~450 pass yds/gm, ~350 rush yds/gm, ~7 TDs/gm.
        'pass_yards': 550,
        'rush_yards': 400,
        'pass_tds': 8,
        'rush_tds': 8,
        'def_sacks': 8,
        'def_ints': 5,
        'possession_time_sec': 2400,   # 40 minutes ceiling
    }
    _cap = _PG_CAPS.get(field)
    if _cap is not None and result is not None and result > _cap:
        return None
    return result


def _blend_label(stats: dict) -> str:
    """Per-team label describing which season(s) the displayed stats reflect.

    - "prior season" — 0 games this season (before Wk 1 played)
    - "blended · N game(s) this season + prior" — 1-2 games (weeks 1-3)
    - "current season · N games" — 3+ games (Wk 4+)
    """
    cur = stats.get('_cur_games') or 0
    cur_season = stats.get('_cur_season')
    pri_season = stats.get('_pri_season')
    if cur == 0:
        return f'{pri_season} season'
    if cur >= BLEND_UNTIL_GAMES:
        return f'{cur_season} season · {cur} games'
    plural = 's' if cur != 1 else ''
    return f'blended · {cur} game{plural} this season + {pri_season} season'


# Weeks 1-3 discipline (mirrors NFL Sept-4 fallback).
# CFB regular season = ~12 games per team; 3 games/team avg ≈ Week 4.
# Shrink=0.5 (heavier than NFL's 0.4) because CFB has higher year-over-year
# roster turnover — portal transfers + coaching changes shift team quality
# more than NFL free agency does. Cutting prior-year edge in half is honest.
SHRINK = 0.5
# CFBD's ncaaf_team_stats populates SP+/EPA fields for teams once meaningful
# game data exists but leaves games=None. Use "% of teams with sp_overall
# populated" as the freshness signal instead. 60% = ~80 of 133 teams have
# real SP+ this season → considered mature enough to use as 'current'.
MIN_POPULATED_PCT = 0.60


def _rated_team_count(stats_dict: dict) -> int:
    """How many teams actually carry an SP+ rating."""
    if not stats_dict: return 0
    return sum(1 for row in stats_dict.values() if row.get('sp_overall') is not None)


def _populated_pct(stats_dict: dict) -> float:
    """DEPRECATED 2026-09-29. Divided SP+ coverage by EVERY team in the dict,
    and that denominator is not comparable across seasons: SP+ rates FBS only
    (~139 teams) while the 2026 pull ingests FCS/D2 too.

        2026   139 of 267 rated = 52.1%  -> FAILED the 60% gate
        2025   137 of 137 rated = 100%   -> passed

    Measured 2026-09-29: four weeks into the season, all 67 upcoming NCAAF
    games projected off '2025 season 12-16 games' and ZERO used 2026 stats,
    because the gate rejected the current season for containing MORE teams.
    The fallback gained nothing — 2025 holds the same ~137 rated FBS teams.

    Use _rated_team_count and compare like for like.
    """
    if not stats_dict: return 0.0
    return _rated_team_count(stats_dict) / len(stats_dict)


def _league_mean_stats(stats_dict: dict) -> dict:
    """Compute league-mean SP+/EPA rates for regression-to-mean blending."""
    if not stats_dict: return {}
    keys = ('sp_overall', 'off_epa_per_play', 'def_epa_per_play',
            'off_success_rate', 'off_explosiveness')
    totals = {k: 0.0 for k in keys}
    counts = {k: 0 for k in keys}
    for row in stats_dict.values():
        for k in keys:
            v = row.get(k)
            if v is not None:
                totals[k] += float(v)
                counts[k] += 1
    return {k: (totals[k] / counts[k]) if counts[k] > 0 else 0.0 for k in keys}


def _regress_to_mean(stats_dict: dict, shrink: float = SHRINK) -> dict:
    """Blend prior-season stats toward league mean.
    shrink=0.5 → 50% prior signal + 50% league mean. Handles portal
    turnover + coaching changes without discarding prior-year signal.
    """
    if not stats_dict: return {}
    mean = _league_mean_stats(stats_dict)
    keys = ('sp_overall', 'off_epa_per_play', 'def_epa_per_play',
            'off_success_rate', 'off_explosiveness')
    out = _TeamStats()          # preserve odds-pipe name resolution
    for team, row in stats_dict.items():
        new = dict(row)
        for k in keys:
            v = row.get(k)
            if v is not None:
                new[k] = (1 - shrink) * float(v) + shrink * mean.get(k, 0.0)
        out[team] = new
    out.build_index()
    return out


def load_team_stats_with_fallback(current_season: int) -> tuple:
    """Return (stats_dict, source_label). Falls back to prior-season
    regressed-to-mean when current-year sample is too thin (Aug-Sep).

    Uses "% of teams with SP+ populated" as the freshness gate — CFBD
    writes SP+/EPA to ncaaf_team_stats but leaves games=None, so we
    can't use a games/team average. 60%+ populated = current season
    has enough games to trust.
    """
    current = load_team_stats(current_season)
    prior = load_team_stats(current_season - 1)

    # 2026-09-29 - COMPARE RATED-TEAM COUNTS, NOT A RATIO.
    # The old gate was _populated_pct(current) >= 0.60, i.e. SP+ coverage over
    # EVERY team in the dict. That denominator is not comparable across
    # seasons: SP+ rates FBS only, and the 2026 pull ingests FCS/D2 as well.
    #     2026   139 of 267 teams rated = 52.1%  -> FAILED the 60% gate
    #     2025   137 of 137 teams rated = 100%   -> passed
    # So the current season was rejected for containing MORE teams and every
    # NCAAF projection fell back to 2025 - measured four weeks in, 0 of 67
    # upcoming games used 2026 stats. The fallback gained nothing, because
    # 2025 holds the same ~137 rated FBS teams and none of the rest.
    #
    # The question the gate should ask is whether the current season rates as
    # many teams as the season we would fall back to. 2026 rates 139 against
    # 2025's 137, so the answer has been yes since the SP+ pull started.
    # 0.9 rather than 1.0 tolerates a few teams CFBD has not rated yet early
    # in a season without flipping the whole sport back to last year.
    _cur_rated = _rated_team_count(current)
    _pri_rated = _rated_team_count(prior)
    if _cur_rated and (_pri_rated == 0 or _cur_rated >= 0.9 * _pri_rated):
        print('  stats source: CURRENT season %s (%d rated vs %d in %s)'
              % (current_season, _cur_rated, _pri_rated, current_season - 1))
        return current, 'current'
    if _pri_rated:
        print('  stats source: %s regressed - current rates only %d vs %d'
              % (current_season - 1, _cur_rated, _pri_rated))
        return _regress_to_mean(prior, shrink=SHRINK), 'prior_season_regressed'
    return current or prior, 'none'


def compute_projections(home_stats: dict, away_stats: dict,
                        neutral_site: bool = False) -> dict:
    """EPA/SP+ based projected spread + total."""
    out = {
        'home_off_epa_pp': None, 'away_off_epa_pp': None,
        'home_def_epa_pp': None, 'away_def_epa_pp': None,
        'home_sp_overall': None, 'away_sp_overall': None,
        # 2026-08-22 (silent-bug audit finding #13): expose per-team SP+ under
        # the names the signal_sources rows actually read. sp_overall was the
        # internal name; ncaaf_sp_plus_edge_home/_away expects home_sp_plus.
        # Same value, aliased so signals can read + trend calls can too.
        'home_sp_plus': None, 'away_sp_plus': None,
        'sp_plus_matchup_total': None,  # sum for over/under signal
        'sp_gap': None,
        'projected_spread': None, 'projected_total': None,
        'model_pred_home_points': None, 'model_pred_away_points': None,
    }
    h_sp = _f(home_stats.get('sp_overall'))
    a_sp = _f(away_stats.get('sp_overall'))
    h_off_epa = _f(home_stats.get('off_epa_per_play'))
    a_off_epa = _f(away_stats.get('off_epa_per_play'))
    h_def_epa = _f(home_stats.get('def_epa_per_play'))
    a_def_epa = _f(away_stats.get('def_epa_per_play'))
    out['home_off_epa_pp'] = h_off_epa
    out['away_off_epa_pp'] = a_off_epa
    out['home_def_epa_pp'] = h_def_epa
    out['away_def_epa_pp'] = a_def_epa
    out['home_sp_overall'] = h_sp
    out['away_sp_overall'] = a_sp
    # 2026-08-22: aliased for signal_sources readers (finding #13)
    out['home_sp_plus'] = h_sp
    out['away_sp_plus'] = a_sp
    if h_sp is not None and a_sp is not None:
        out['sp_plus_matchup_total'] = round(float(h_sp) + float(a_sp), 2)

    hfa = 0 if neutral_site else HOME_FIELD_PTS
    # Prefer SP+ when both teams have it
    if h_sp is not None and a_sp is not None:
        sp_gap = h_sp - a_sp
        out['sp_gap'] = round(sp_gap, 2)
        projected_spread = round(sp_gap * K_PTS_SP + hfa, 2)
    elif h_off_epa is not None and a_off_epa is not None:
        # EPA fallback: net_epa = off_epa - def_epa (higher = better team)
        #
        # ⚠ 2026-09-29 THIS PATH IS BROKEN, NOT MERELY LESS ACCURATE, and it is
        # the real hallucination risk in this file. Measured on the 10 leak-free
        # graded 2026 games that used it:
        #
        #     std(projected_spread) = 1.35   vs  std(market) = 9.27
        #
        # It emits a near-CONSTANT projection — every game lands within a couple
        # of points of the HFA. Variance matching would want K_PTS_EPA 5.5 →
        # 37.8, which is not a calibration on n=10, it is a coin flip. The same
        # compression shows up in epa_pred_spread below (Indiana@Rutgers -0.18
        # against a market of 24.5) and is consistent with
        # project_sp_plus_compression_927: rolling EPA is not opponent-adjusted.
        #
        # Live exposure TODAY is ZERO — after the stats-freshness fix, 0 of 67
        # upcoming games hit this branch (62 of 67 teams are SP+-rated). It was
        # 10 of 171 graded games, concentrated in weeks 1-3 and FCS opponents,
        # so it comes back whenever an unrated team appears.
        #
        # DELIBERATELY NOT "fixed" here: multiplying by a factor fitted on n=10
        # would be exactly the unmeasured adjustment this project keeps getting
        # burned by. The right fix is that an unrated matchup should not yield a
        # confident pick at all — a tier cap keyed on
        # projected_spread_source == 'epa'. That is left for when the path is
        # live again and can be measured, rather than shipping an untested cap
        # on a dormant branch. projected_spread_source is written below so the
        # cap has something to key on when it is built.
        h_net = h_off_epa - (h_def_epa or 0)
        a_net = a_off_epa - (a_def_epa or 0)
        projected_spread = round((h_net - a_net) * K_PTS_EPA + hfa, 2)
    else:
        return out
    out['projected_spread'] = projected_spread

    # 2026-08-09 Phase 2: matchup-adjusted total using SP+ off/def or EPA
    # per-play. Previously flat BASE_TOTAL for every game; now varies by
    # each team's offense × opp defense strength. Same pattern as
    # NFL nfl_game_context.compute_projections after 8/9 upgrade.
    h_sp_off = _f(home_stats.get('sp_offense'))
    h_sp_def = _f(home_stats.get('sp_defense'))
    a_sp_off = _f(away_stats.get('sp_offense'))
    a_sp_def = _f(away_stats.get('sp_defense'))
    LEAGUE_TEAM_AVG = BASE_TOTAL / 2  # 26 points per team
    # SP+ off/def are rated in points-per-game above/below avg. A team with
    # sp_offense=+5 scores 5 more than average against average defense.
    if all(v is not None for v in (h_sp_off, h_sp_def, a_sp_off, a_sp_def)):
        # 2026-08-25 REAL FIX (was: broken formula nulling every game).
        # SP+ Offense = expected points scored vs an AVERAGE opponent.
        # SP+ Defense = expected points allowed vs an AVERAGE opponent.
        # Both are already in the same "points" unit — no LEAGUE_TEAM_AVG
        # baseline to add. The correct matchup projection is the average of
        # "how many pts I usually score" (my off) and "how many pts you
        # usually allow" (your def):
        #     expected_home_pts = (h_sp_offense + a_sp_defense) / 2
        # The prior formula ADDED 26 baseline on top, then averaged with
        # baseline again, producing 40-50+ per team → 90+ totals capped to
        # null. Real CFBD math is simpler and produces reasonable 40-70 totals.
        h_pts = (h_sp_off + a_sp_def) / 2
        a_pts = (a_sp_off + h_sp_def) / 2
        # Small home-field bump (~2 pts total shift toward home).
        if not neutral_site:
            h_pts += HOME_FIELD_PTS * 0.4
            a_pts -= HOME_FIELD_PTS * 0.2
        total = h_pts + a_pts
        # Sanity gate: reject if the math still produces bogus results
        # (bad team_stats row, missing data on one side). Real CFBD totals
        # cluster in the [35, 80] band.
        if total < 35 or total > 80:
            out['projected_total'] = None
            out['model_pred_home_points'] = None
            out['model_pred_away_points'] = None
        else:
            out['projected_total'] = round(total, 2)
            out['model_pred_home_points'] = round(h_pts, 1)
            out['model_pred_away_points'] = round(a_pts, 1)
            # 2026-09-16: also populate sp_plus_pred_home_pts / away_pts / total.
            # Columns already exist in schema but were never written — every
            # NCAAF card's Numbers panel SP+ row showed a margin but null
            # per-team points. Same SP+ math, same values, just plumbed to
            # the SP+-labeled columns so the NumbersPanel can render the
            # full row (margin + total + AWAY pts + HOME pts).
            #
            # ⚠ 2026-09-20 — READ THIS BEFORE USING sp_plus_pred_total.
            # These three are DISPLAY ALIASES, not a second model.
            # `projected_total` above is already the SP+ matchup total, so
            # sp_plus_pred_total is byte-identical to it by construction
            # (verified 72/72, mean |diff| 0.00). It must NEVER be counted
            # as an independent lens: doing so double-counts one model.
            #
            # It had already gone wrong twice —
            #   * ncaaf_sharp_fade_rules.rule_models_oppose_sharp built a
            #     2-model totals consensus from projected_total +
            #     sp_plus_pred_total, so the "both models agree" guard was
            #     one model counted twice. Now uses Monte Carlo.
            #   * GameDetailV2's Model Consensus rendered v3/v4/SP+ totals
            #     as three lenses showing one number.
            # For a genuinely independent total use
            # mc_probabilities.mc_expected_total (0/68 identical, mean
            # |diff| 1.13).
            #
            # ⚠ 2026-09-29 CORRECTION. This note used to read: "The SPREAD
            # columns are NOT aliases — sp_plus_pred_spread is computed
            # separately below and genuinely differs from projected_spread
            # (mean |diff| ~7 pts). Only the TOTAL is a duplicate."
            # That was WRONG and it cost nine days. The ~7 pts is the diff
            # between the EPA spread and SP+ — a value the code computed and
            # then discarded. What actually got written to
            # sp_plus_pred_spread was projected_spread itself: 67/67
            # identical, measured 2026-09-29. The SPREAD IS AN ALIAS TOO.
            # Acting on this note, the 09-20 cleanup fixed only the total
            # branch of ncaaf_sharp_fade_rules and left the spread branch
            # building fake corroboration. See the second-lens block at the
            # end of this function and migration 20260929b.
            out['sp_plus_pred_home_pts'] = round(h_pts, 1)
            out['sp_plus_pred_away_pts'] = round(a_pts, 1)
            out['sp_plus_pred_total']    = round(total, 2)
    else:
        # Fallback: static base + split via spread
        total = BASE_TOTAL
        if projected_spread >= 0:
            home_share = 0.50 + min(0.10, projected_spread * 0.008)
        else:
            home_share = 0.50 + max(-0.10, projected_spread * 0.008)
        out['projected_total'] = round(total, 2)
        out['model_pred_home_points'] = round(total * home_share, 1)
        out['model_pred_away_points'] = round(total * (1 - home_share), 1)

    # ── second spread lens ──────────────────────────────────────────────
    # 2026-09-29 · THE SPREAD IS AN ALIAS TOO. Correcting the 09-20 note above.
    #
    # This block used to compute `epa_spread` and then never read it — the next
    # line copied the PRIMARY projection into sp_plus_pred_spread, and two
    # trailing comments described what the code was supposed to do instead of
    # what it did ("Store EPA as second lens", "Cleaner: add explicit
    # epa_pred_spread field"). So sp_plus_pred_spread was a byte-identical copy
    # of projected_spread: measured 67/67 on 2026-09-29, exactly like
    # sp_plus_pred_total.
    #
    # The 09-20 note's "mean |diff| ~7 pts" was not wrong, it was measuring the
    # WRONG VALUE — the diff between the EPA spread and SP+ (re-measured today:
    # mean 7.98, median 6.83, 0/67 identical). It described the intended write.
    # Because that note said the spread columns were safe, the 09-20
    # duplicate-lens cleanup fixed only the TOTAL branch of
    # ncaaf_sharp_fade_rules.rule_models_oppose_sharp and left the SPREAD
    # branch counting one model twice for nine more days. Both are fixed now.
    #
    # sp_plus_pred_spread stays a documented alias rather than being nulled:
    # when SP+ drives the primary it genuinely IS the SP+ spread, and
    # GameDetailV2's Model Consensus drops its SP+ tile only when the two
    # values MATCH (`_spDupe`) — nulling it makes that guard fail open and
    # renders an empty tile.
    #
    # epa_pred_spread is the real second lens, under a name that says so
    # (migration 20260929b). It is written and graded ONLY. It is visibly
    # compressed — Indiana@Rutgers EPA -0.18 against a market of 24.5 — which
    # is project_sp_plus_compression_927 showing up again (rolling EPA is not
    # opponent-adjusted). Nothing that selects or tiers a pick may read it
    # until it has been graded against results.
    out['projected_spread_source'] = 'sp_plus' if (h_sp is not None and a_sp is not None) else 'epa'
    if h_off_epa is not None and a_off_epa is not None:
        h_net_epa = h_off_epa - (h_def_epa or 0)
        a_net_epa = a_off_epa - (a_def_epa or 0)
        out['epa_pred_spread'] = round((h_net_epa - a_net_epa) * K_PTS_EPA + hfa, 2)
        out['sp_plus_pred_spread'] = round(projected_spread, 2)  # alias, see above
    return out


def compute_confluence(home: dict, away: dict) -> tuple:
    breakdown = {}
    # SP+ overall
    h_sp = _f(home.get('sp_overall')); a_sp = _f(away.get('sp_overall'))
    if h_sp is not None and a_sp is not None:
        if h_sp - a_sp >= 3: breakdown['sp_plus'] = 'home'
        elif a_sp - h_sp >= 3: breakdown['sp_plus'] = 'away'
    # Off EPA per play
    h_off = _f(home.get('off_epa_per_play')); a_off = _f(away.get('off_epa_per_play'))
    if h_off is not None and a_off is not None:
        if h_off - a_off >= 0.10: breakdown['off_epa'] = 'home'
        elif a_off - h_off >= 0.10: breakdown['off_epa'] = 'away'
    # Def EPA (lower = better defense)
    h_def = _f(home.get('def_epa_per_play')); a_def = _f(away.get('def_epa_per_play'))
    if h_def is not None and a_def is not None:
        if a_def - h_def >= 0.10: breakdown['def_epa'] = 'home'  # home has better def
        elif h_def - a_def >= 0.10: breakdown['def_epa'] = 'away'
    # Success rate
    h_sr = _f(home.get('off_success_rate')); a_sr = _f(away.get('off_success_rate'))
    if h_sr is not None and a_sr is not None:
        if h_sr - a_sr >= 0.03: breakdown['success_rate'] = 'home'
        elif a_sr - h_sr >= 0.03: breakdown['success_rate'] = 'away'
    # Explosiveness
    h_ex = _f(home.get('off_explosiveness')); a_ex = _f(away.get('off_explosiveness'))
    if h_ex is not None and a_ex is not None:
        if h_ex - a_ex >= 0.10: breakdown['explosiveness'] = 'home'
        elif a_ex - h_ex >= 0.10: breakdown['explosiveness'] = 'away'
    # HFA default
    breakdown['hfa'] = 'home'
    h = sum(1 for v in breakdown.values() if v == 'home')
    a = sum(1 for v in breakdown.values() if v == 'away')
    return h - a, breakdown


def sweat_tier(score):
    if score >= 80: return 'PRIME'
    if score >= 65: return 'STRONG'
    if score >= 50: return 'LIGHT_LEAN'
    return 'PASS'


def compute_sweat_score(proj_spread, close_spread, conf_net, proj_total, close_total,
                        proj_spread_source=None):
    score = 45
    # ── 2026-10-03 · THE 'epa' EDGE BONUS IS NOT AN EDGE ──────────────────
    # The deferred fix from the 2026-09-29 note on the EPA fallback above.
    # That note said the path was dormant (0 of 67 games) and capped its own
    # scope: "left for when the path is live again and can be measured."
    # It went live again TODAY and nothing tripped — 3 of 54 games on 10-03
    # used it, and all 3 landed STRONG or PRIME on the strength of the very
    # compression the note documented:
    #
    #   McNeese @ LSU        market -53.5  proj +4.8  "edge" -48.6  ss=83 PRIME
    #   Texas Southern @ FAU market -44.5  proj +2.9  "edge" -41.6  ss=78 STRONG
    #   Samford @ UAB        market -30.5  proj +3.2  "edge" -27.3  ss=75 STRONG
    #
    # mean sweat_score 78.7 on those 3 vs 62.2 on the 51 sp_plus games.
    #
    # The projection is not wrong-signed, it is STARVED: rolling EPA is not
    # opponent-adjusted and an FCS opponent has no data, so h_net - a_net
    # collapses and the output is just the home-field constant (std 1.35 vs
    # market 9.27). A near-constant projection against a real spread
    # manufactures a huge "disagreement" on every unrated matchup, and the
    # edge ladder below paid +25 for it.
    #
    # So: an unrated matchup earns NO edge bonus. Confluence and total still
    # score normally — this removes a fabricated input, it does not suppress
    # the game. Deliberately NOT a K_PTS_EPA recalibration: fitting a factor
    # on n=10 is the unmeasured adjustment the 09-29 note refused, correctly.
    #
    # ── THE GENERAL GUARD: A HUGE EDGE IS MODEL ERROR, NOT OPPORTUNITY ────
    # The 'epa' key alone is not enough. The two worst days on record carried
    # projected_spread_source = NULL, so a source-keyed cap would have missed
    # them entirely: 2026-09-12 max |edge| 74.8, 2026-09-26 max |edge| 49.9.
    # Whatever produces an absurd projection next will have a different name.
    #
    # So gate on the MAGNITUDE, which is measurable regardless of source.
    # Model-edge side cover rate on the 204 graded+modelled 2026 games:
    #
    #     |edge| <  10 pts  ->  58.8%  (n=160)
    #     |edge| >= 10 pts  ->  47.7%  (n= 44)   below the 52.4% breakeven
    #
    # Large disagreements carry NEGATIVE information, and the ladder below
    # paid its MAXIMUM +25 for them. The mechanism is the same starvation as
    # the EPA note: the model disagrees violently with the market precisely
    # when it lacks the data to rate a team, so magnitude is a proxy for
    # "this projection is uninformed" rather than "the market is wrong".
    #
    # NOT retuning the rest of the ladder here, on purpose. The 0.0-1.5 pt
    # bucket covers 71.1% (n=38) and is paid nothing, so the ladder looks
    # close to inverted end-to-end — but the header warning at the top of
    # this file is explicit: "DO NOT retune sweat_score/conviction thresholds
    # in the same change." One guard now; the ladder shape is its own
    # measured change with its own before/after.
    _absurd_edge = (proj_spread is not None and close_spread is not None
                    and abs(proj_spread + close_spread) >= EDGE_SANITY_MAX_PTS)
    _epa_starved = (proj_spread_source == 'epa')
    if (proj_spread is not None and close_spread is not None
            and not _epa_starved and not _absurd_edge):
        # 2026-09-16 SIGN CONVENTION FIX (Andy MIA@WF audit).
        # NCAAF close_spread uses pos=away favored; projected_spread uses
        # pos=home favored. Prior code subtracted raw values, inflating
        # every edge by ~2x (MIA@WF: |-16.64 - 21| = 37.64 vs true edge
        # of |-16.64 + 21| = 4.36). Every NCAAF game with a real spread
        # got a sweat_score boost it didn't earn → tier inflation.
        # Mirror the ncaab pattern: ADD to match opposite conventions.
        edge = abs(proj_spread + close_spread)
        if edge >= 7:   score += 25
        elif edge >= 5: score += 18
        elif edge >= 3: score += 12
        elif edge >= 1.5: score += 6
    if conf_net is not None:
        a = abs(conf_net)
        if a >= 4: score += 15
        elif a >= 3: score += 10
        elif a >= 2: score += 5
    if proj_total is not None and close_total is not None:
        te = abs(proj_total - close_total)
        if te >= 7: score += 8
        elif te >= 4: score += 5
        elif te >= 2: score += 3
    return min(100, max(0, score))


def compute_primary_play(ctx):
    """NCAAF primary_play. Early-season discipline (Aug-Sep):
    when stats_source='prior_season_regressed', all tiers cap at LEAN.
    Cohort-based plays exempt (none audit-validated yet for NCAAF —
    see project_ncaaf_phase1_audit_baselines for future additions).
    """
    stats_source = ctx.get('stats_source') or 'current'
    stats_stale = stats_source != 'current'

    conf = ctx.get('signal_confluence_net') or 0
    proj_spread = ctx.get('projected_spread')
    close_spread = ctx.get('close_spread')
    home_team = ctx.get('home_team') or 'Home'
    away_team = ctx.get('away_team') or 'Away'
    proj_total = ctx.get('projected_total')
    close_total = ctx.get('close_total')

    # 2026-09-26 · NO SP+ ON ONE SIDE MEANS NO MARGIN OPINION.
    #
    # Andy asked whether the LR-warn gate would produce "a whole bunch of
    # passes". Measuring that turned up a worse problem underneath it.
    #
    # projected_spread comes from `sp_gap * K_PTS_SP + hfa`, but SP+ only
    # exists for FBS teams. In an FBS-vs-FCS game the FCS side is NULL, so
    # the code drops to the EPA fallback — computed from ncaaf_team_stats
    # rows whose `games` count we already know is wrong for exactly these
    # teams (see recompute_ncaaf_per_game_stats.py). The result is a
    # near-zero projected margin against a 30-40 point market line:
    #
    #     Western Kentucky  market -40.5   model projected  1.19
    #     FIU               market -37.5   model projected  4.65
    #     Texas State       market -30.5   model projected  0.82
    #     William & Mary    market -43.5   model projected  None  ← still picked
    #
    # spread_edge then reads as a 30-40 point edge, which is not an edge,
    # it is the absence of information. That is the mechanism behind the
    # -23.7 pt mean divergence measured on the 21+ band, and it is why
    # NCAAF dog picks have gone 8-22 (26.7%) while favourite picks went
    # 50-40 (55.6%) on the same board.
    #
    # Gated at the SOURCE rather than in the scorer, per
    # feedback_source_gate_pattern: a pick that should never exist should
    # not be created and then filtered. Totals are unaffected — they come
    # from projected_total, which does not depend on SP+.
    #
    # Already-published picks are protected by the database pick lock
    # (20260926b); this only changes what future runs produce.
    _sp_missing = (ctx.get('home_sp_overall') is None
                   or ctx.get('away_sp_overall') is None)
    if _sp_missing:
        proj_spread = None

    spread_edge = None
    if proj_spread is not None and close_spread is not None:
        # 2026-09-16 SIGN CONVENTION FIX (Andy MIA@WF audit).
        # NCAAF close_spread uses pos=away favored; projected_spread uses
        # pos=home favored. Prior subtract inflated edges ~2x → over-
        # tiering. Correct is to add (matches ncaab / MLB pattern).
        # Sign of the sum tells model direction relative to market:
        # positive → model likes HOME more than market does; negative
        # → model likes AWAY more.
        spread_edge = round(float(proj_spread) + float(close_spread), 2)
    abs_edge = abs(spread_edge) if spread_edge is not None else 0.0
    fav = home_team if (proj_spread is not None and float(proj_spread) > 0) else away_team

    total_edge = None
    if proj_total is not None and close_total is not None:
        total_edge = round(float(proj_total) - float(close_total), 2)

    stale_note = ' · prior-season regressed, LEAN cap' if stats_stale else ''

    # 2026-09-26 · SAY WHO, NOT JUST HOW MUCH.
    # Andy: "Model projects 4.42 vs market -10.00 — same direction,
    # opposite signs. Reads as if the model has Vandy winning by 4.4."
    #
    # Both numbers meant Auburn. projected_spread is HOME-POSITIVE
    # (+4.42 = home by 4.4) and close_spread here is HOME-NEGATIVE
    # (-10.0 = home by 10), so printing them side by side showed a "+"
    # and a "-" for two statements that agree. Naming the team drops the
    # sign convention out of the user-facing string entirely — which
    # matters more than usual because the convention is not even
    # consistent across sports (project_close_spread_sign_bug_914: NFL
    # stores away-perspective, everyone else home-perspective).
    def _spread_sentence() -> str:
        try:
            p, c = float(proj_spread), float(close_spread)
        except (TypeError, ValueError):
            return ''
        m_side, m_mag = (home_team, p) if p > 0 else (away_team, -p)
        k_side, k_mag = (home_team, -c) if c < 0 else (away_team, c)
        base = (f'Model has {m_side} by {m_mag:.1f}; '
                f'market has {k_side} by {k_mag:.1f}')
        if spread_edge is not None:
            # spread_edge < 0 means the model likes AWAY more than the
            # market does (see the derivation just above).
            lean = away_team if spread_edge < 0 else home_team
            base += f' — {abs_edge:.1f} pts of value on {lean}'
        return base

    # 2026-09-10 humanize spread labels: "GB spread cover" → "GB -3.5"
    # + populate side/line so downstream badge alignment works.
    _spread_side = 'HOME' if (proj_spread is not None and float(proj_spread) > 0) else 'AWAY'
    _fav_line = None
    if close_spread is not None:
        _fav_line = -float(close_spread) if _spread_side == 'HOME' else float(close_spread)
    _spread_label = f'{fav} {_fav_line:+g}' if _fav_line is not None else f'{fav} spread'

    # PRIME spread — big edge + confluence agreement
    if abs_edge >= 6.0 and abs(conf) >= 3:
        tier = 'LEAN' if stats_stale else 'PRIME'
        floor = 60 if stats_stale else 85
        return {'type': 'spread', 'tier': tier,
                'side': _spread_side, 'line': _fav_line,
                'label': _spread_label,
                'sub': f'{_spread_sentence()} (conf {conf:+d}){stale_note}',
                'signal_floor': floor}
    # STRONG spread — meaningful edge
    if abs_edge >= 4.0 and abs(conf) >= 2:
        tier = 'LEAN' if stats_stale else 'STRONG'
        floor = 58 if stats_stale else 72
        return {'type': 'spread', 'tier': tier,
                'side': _spread_side, 'line': _fav_line,
                'label': _spread_label,
                'sub': f'{_spread_sentence()}{stale_note}',
                'signal_floor': floor}
    # STRONG total
    if total_edge is not None and abs(total_edge) >= 5.0:
        side = 'Over' if total_edge > 0 else 'Under'
        tier = 'LEAN' if stats_stale else 'STRONG'
        floor = 58 if stats_stale else 70
        return {'type': 'total', 'tier': tier,
                'side': side.upper(), 'line': float(close_total),
                'label': f'{side} {close_total}',
                'sub': f'Model projects {proj_total:.1f} vs market {close_total} ({total_edge:+.1f}){stale_note}',
                'signal_floor': floor}
    # LIGHT spread — cap at LEAN when stale (same rationale as NFL:
    # a weaker signal shouldn't sneak into lock_of_week when the
    # stronger STRONG-tier signal on the same data got capped out)
    if abs_edge >= 3.0:
        tier = 'LEAN' if stats_stale else 'LIGHT'
        return {'type': 'spread', 'tier': tier,
                'side': _spread_side, 'line': _fav_line,
                'label': _spread_label,
                'sub': f'Edge {abs_edge:.1f}{stale_note}',
                'signal_floor': 60}
    return None


_NCAAF_RANK_CACHE: dict = {}

def _ncaaf_rank_by(field: str, team_stats: dict, per_game: bool = True,
                   higher_is_better: bool = True) -> dict:
    """Return {team → 1-based rank} across all FBS teams for the field.
    Cached in-process per (field, higher_is_better) so we sort 130 teams
    once per run, not per game."""
    key = (field, higher_is_better)
    if key in _NCAAF_RANK_CACHE: return _NCAAF_RANK_CACHE[key]
    scored = []
    for team, s in team_stats.items():
        if not s: continue
        raw = s.get(field)
        if raw is None: continue
        try:
            v = float(raw)
            if per_game:
                g = float(s.get('games') or 0)
                if g <= 0: continue
                v = v / g
            scored.append((team, v))
        except (TypeError, ValueError): continue
    scored.sort(key=lambda x: -x[1] if higher_is_better else x[1])
    ranks = {team: i + 1 for i, (team, _) in enumerate(scored)}
    _NCAAF_RANK_CACHE[key] = ranks
    return ranks


def _build_ncaaf_team_summary(team: str, stats: dict, all_stats: dict) -> Optional[dict]:
    """Casual-friendly NCAAF summary blob for game-card render.

    Uses ncaaf_team_stats (offense fields) + attached def_ppg/def_*_ypg
    from ncaaf_team_defense_stats (merged into stats dict via
    load_team_stats). Ranks are FBS-wide (~130 teams). Lower rank = better.

    Blob:
      { pts_pg, pts_allowed_pg,
        pass_yds_pg, pass_yds_allowed_pg,
        rush_yds_pg, rush_yds_allowed_pg,
        sacks_pg, turnovers_forced_pg, turnover_diff_pg,
        rank_scoring_off, rank_scoring_def,
        rank_pass_off, rank_pass_def,
        rank_rush_off, rank_rush_def,
        rank_sp_overall, rank_sp_off, rank_sp_def,
        season_source, games_sample }
    """
    # 2026-09-09 BLEND: previously bailed if games=None (meaning current-season
    # aggregation hadn't run). Now falls through to prior-season if _pri_games
    # exists — early-season cards still get real numbers, labeled honestly.
    games = stats.get('games') or 0
    _cur = stats.get('_cur_games') or 0
    _pri = stats.get('_pri_games') or 0
    if not games and not _pri:
        return None

    total_tds = ((stats.get('pass_tds') or 0) + (stats.get('rush_tds') or 0))
    # CFBD ncaaf_team_stats doesn't publish season fg_made — approximate
    # scoring at 6.9 pts/TD (touchdown + XP assumed made) + a fixed FG
    # rate contribution (~1.5 fg × 3 = 4.5 pts/game). Reasonable proxy.
    pts_pg = round((total_tds * 6.9) / games + 4.5, 1) if games and total_tds else None

    # Use blended per-game values so early-season display matches what the
    # rest of ctx now uses (avoids drift between summary blob and top-level
    # ctx fields).
    tf_blend = (_blend_pg(stats, 'def_ints') or 0)  # def_fumbles_rec not tracked yet
    to_blend = _blend_pg(stats, 'turnovers') or 0

    summary = {
        'pts_pg':              pts_pg,
        'pts_allowed_pg':      _blend_pg(stats, 'def_ppg', per_game=False),
        'pass_yds_pg':         _blend_pg(stats, 'pass_yards'),
        'pass_yds_allowed_pg': _blend_pg(stats, 'def_pass_ypg', per_game=False),
        'rush_yds_pg':         _blend_pg(stats, 'rush_yards'),
        'rush_yds_allowed_pg': _blend_pg(stats, 'def_rush_ypg', per_game=False),
        'sacks_pg':            _blend_pg(stats, 'def_sacks'),
        'turnovers_forced_pg': round(tf_blend, 1),
        'turnover_diff_pg':    round(tf_blend - to_blend, 1),
        'season_source':       stats.get('season'),
        'games_sample':        games,
        # 2026-09-09: user-facing label so app can render "blended · 1 game
        # this season + 2025 season" caption on the team-stats section
        'blend_label':         _blend_label(stats),
        'games_current':       _cur,
    }

    def _rank(field, per_game=True, higher_is_better=True):
        ranks = _ncaaf_rank_by(field, all_stats,
                               per_game=per_game, higher_is_better=higher_is_better)
        return ranks.get(team)

    r = _rank('pass_yards');       summary['rank_pass_off']    = r
    r = _rank('rush_yards');       summary['rank_rush_off']    = r
    r = _rank('pass_tds');         summary['rank_scoring_off'] = r
    r = _rank('def_pass_ypg', per_game=False, higher_is_better=False)
    if r: summary['rank_pass_def'] = r
    r = _rank('def_rush_ypg', per_game=False, higher_is_better=False)
    if r: summary['rank_rush_def'] = r
    r = _rank('def_ppg', per_game=False, higher_is_better=False)
    if r: summary['rank_scoring_def'] = r
    # SP+ ranks (efficiency composite) — advanced-metric context
    r = _rank('sp_overall', per_game=False, higher_is_better=True)
    if r: summary['rank_sp_overall'] = r
    r = _rank('sp_offense', per_game=False, higher_is_better=True)
    if r: summary['rank_sp_off'] = r
    r = _rank('sp_defense', per_game=False, higher_is_better=False)  # lower SP+ def = better
    if r: summary['rank_sp_def'] = r
    return summary


def build_context_row(g: dict, team_stats: dict, stats_source: str = 'current',
                       returning_prod: Optional[dict] = None,
                       rolling_form: Optional[dict] = None) -> Optional[dict]:
    home = g.get('home_team'); away = g.get('away_team')
    if not home or not away:
        return None
    home_stats = team_stats.get(home) or {}
    away_stats = team_stats.get(away) or {}

    proj = compute_projections(home_stats, away_stats,
                               neutral_site=bool(g.get('neutral_site')))
    conf_net, breakdown = compute_confluence(home_stats, away_stats)

    # 2026-08-09 Phase 2: returning production % (offense-only from CFBD).
    # Critical Weeks 1-3 signal when EPA/SP+ are thin. Attached to row for
    # downstream primary_play resolver + Jerry synthesis.
    rp = returning_prod or {}
    hrp = rp.get(home) or {}
    arp = rp.get(away) or {}
    # 2026-08-22 (silent-bug audit finding #12): derive combined field so the
    # ncaaf_returning_prod_home / _away signals can read ctx.home_returning_production
    # (previously read from a field that never existed). Blended off+def average.
    # 2026-08-23: kept the blended writes; upsert() strip-on-400 handles it if
    # DB migration hasn't landed. Migration 20260823d_ncaaf_ctx_missing_columns
    # adds the 5 columns permanently.
    def _blend(off, deff):
        vals = [v for v in (off, deff) if v is not None]
        return sum(vals) / len(vals) if vals else None
    ret_fields = {
        'home_returning_production_off': hrp.get('returning_offense_pct'),
        'home_returning_production_def': hrp.get('returning_defense_pct'),
        'away_returning_production_off': arp.get('returning_offense_pct'),
        'away_returning_production_def': arp.get('returning_defense_pct'),
        'home_returning_production': _blend(
            hrp.get('returning_offense_pct'), hrp.get('returning_defense_pct')),
        'away_returning_production': _blend(
            arp.get('returning_offense_pct'), arp.get('returning_defense_pct')),
    }

    # 2026-08-28: pull defense stats attached to team_stats dict (see
    # load_team_stats merge). Fuels ncaaf_def_* matchup signals.
    # 2026-09-09 BLEND: def_* fields are already per-game rates from
    # ncaaf_team_defense_stats — use _blend_pg(per_game=False) so weeks
    # 1-3 weight current + prior, 4+ = current only.
    def_fields = {
        'home_def_ppg':                  _blend_pg(home_stats, 'def_ppg', per_game=False),
        'home_def_pass_ypg':             _blend_pg(home_stats, 'def_pass_ypg', per_game=False),
        'home_def_rush_ypg':             _blend_pg(home_stats, 'def_rush_ypg', per_game=False),
        'home_def_pass_epa_allowed':     _blend_pg(home_stats, 'def_pass_epa_allowed', per_game=False),
        'home_def_rush_epa_allowed':     _blend_pg(home_stats, 'def_rush_epa_allowed', per_game=False),
        'home_def_success_rate_allowed': home_stats.get('def_success_rate_allowed'),
        'home_def_explosiveness_allowed': home_stats.get('def_explosiveness_allowed'),
        'away_def_ppg':                  _blend_pg(away_stats, 'def_ppg', per_game=False),
        'away_def_pass_ypg':             _blend_pg(away_stats, 'def_pass_ypg', per_game=False),
        'away_def_rush_ypg':             _blend_pg(away_stats, 'def_rush_ypg', per_game=False),
        'away_def_pass_epa_allowed':     _blend_pg(away_stats, 'def_pass_epa_allowed', per_game=False),
        'away_def_rush_epa_allowed':     _blend_pg(away_stats, 'def_rush_epa_allowed', per_game=False),
        'away_def_success_rate_allowed': away_stats.get('def_success_rate_allowed'),
        'away_def_explosiveness_allowed': away_stats.get('def_explosiveness_allowed'),
    }

    # 2026-08-28: volumetric + discipline stats from ncaaf_team_stats
    # (populated by CFBD /stats/season pull). Per-game averages using
    # games count on the stat row.
    # 2026-09-09 BLEND: use _blend_pg (per_game=True — season totals divided
    # by games per side, weighted). Wks 1-3 blend, Wk 4+ pure current.
    vol_fields = {
        # Penalty tendencies
        'home_penalties_pg':     _blend_pg(home_stats, 'penalties'),
        'home_penalty_yds_pg':   _blend_pg(home_stats, 'penalty_yards'),
        'away_penalties_pg':     _blend_pg(away_stats, 'penalties'),
        'away_penalty_yds_pg':   _blend_pg(away_stats, 'penalty_yards'),
        # Offense volume
        'home_pass_yds_pg':      _blend_pg(home_stats, 'pass_yards'),
        'home_rush_yds_pg':      _blend_pg(home_stats, 'rush_yards'),
        'home_pass_tds_pg':      _blend_pg(home_stats, 'pass_tds'),
        'home_rush_tds_pg':      _blend_pg(home_stats, 'rush_tds'),
        'away_pass_yds_pg':      _blend_pg(away_stats, 'pass_yards'),
        'away_rush_yds_pg':      _blend_pg(away_stats, 'rush_yards'),
        'away_pass_tds_pg':      _blend_pg(away_stats, 'pass_tds'),
        'away_rush_tds_pg':      _blend_pg(away_stats, 'rush_tds'),
        # Situational efficiency — third-down conv is a ratio, kept as-is
        'home_third_down_pct':   (round(100 * (home_stats.get('third_down_conv') or 0) / home_stats['third_downs'], 1)
                                   if home_stats.get('third_downs') else None),
        'away_third_down_pct':   (round(100 * (away_stats.get('third_down_conv') or 0) / away_stats['third_downs'], 1)
                                   if away_stats.get('third_downs') else None),
        # Ball security
        'home_turnovers_pg':     _blend_pg(home_stats, 'turnovers'),
        'away_turnovers_pg':     _blend_pg(away_stats, 'turnovers'),
        # Time of possession (minutes/game) — divide blended seconds by 60
        'home_top_min':          (round(_blend_pg(home_stats, 'possession_time_sec') / 60, 1)
                                   if _blend_pg(home_stats, 'possession_time_sec') else None),
        'away_top_min':          (round(_blend_pg(away_stats, 'possession_time_sec') / 60, 1)
                                   if _blend_pg(away_stats, 'possession_time_sec') else None),
        # Defensive events (own team's D)
        'home_def_sacks_pg':     _blend_pg(home_stats, 'def_sacks'),
        'home_def_ints_pg':      _blend_pg(home_stats, 'def_ints'),
        'away_def_sacks_pg':     _blend_pg(away_stats, 'def_sacks'),
        'away_def_ints_pg':      _blend_pg(away_stats, 'def_ints'),
        # 2026-09-09: per-team blend label so app can render "blended · 2 games
        # this season + 2025 season" caption instead of stats appearing to
        # not match ESPN's current-season-only display.
        'home_stats_blend_label': _blend_label(home_stats),
        'away_stats_blend_label': _blend_label(away_stats),
        'home_games_played':      home_stats.get('_cur_games') or 0,
        'away_games_played':      away_stats.get('_cur_games') or 0,
    }

    # 2026-08-31: Casual-friendly team-stats summary blob for game-card
    # render. Mirrors NFL pattern (nfl_game_context._build_team_summary)
    # but ranks over ALL FBS teams (~130) instead of NFL's 32. Emits pts,
    # yds/g both directions, sacks, turnovers forced, SP+ overall/off/def
    # ranks. App renders "Iowa State averages 189 rush yds/g (#14)" style.
    home_summary = _build_ncaaf_team_summary(home, home_stats, team_stats)
    away_summary = _build_ncaaf_team_summary(away, away_stats, team_stats)

    # 2026-09-15 v1.06: stamp rolling L4 team form onto ctx. Feeds
    # ncaaf_total_logreg — trainer computes these same features from
    # ncaaf_game_results, but at inference time _lr_predict_total reads
    # from ctx. Silent no-op if migration hasn't landed (upsert() strips
    # unknown cols on 400 per the existing convention below).
    rf = (rolling_form or {}).get(home) or {}
    ra = (rolling_form or {}).get(away) or {}
    roll_fields = {
        'home_l4_ppg':        rf.get('ppg'),
        'home_l4_pa':         rf.get('pa'),
        'home_l4_total_avg':  rf.get('total_avg'),
        'home_l4_over_rate':  rf.get('over_rate'),
        'away_l4_ppg':        ra.get('ppg'),
        'away_l4_pa':         ra.get('pa'),
        'away_l4_total_avg':  ra.get('total_avg'),
        'away_l4_over_rate':  ra.get('over_rate'),
    }

    row = {
        'game_id': g['game_id'],
        'game_date': g['game_date'],
        'season': g.get('season'),
        'season_type': g.get('season_type') or 'regular',
        'week': g.get('week'),
        'home_team': home,
        'away_team': away,
        'kickoff_utc': g.get('kickoff_utc'),
        'close_spread': g.get('close_spread'),
        'open_spread': g.get('open_spread'),
        'close_total': g.get('close_total'),
        'open_total': g.get('open_total'),
        'close_home_ml': g.get('close_home_ml'),
        'close_away_ml': g.get('close_away_ml'),
        # 2026-09-16: mirror close→open on first pull so Line Movement
        # box has an anchor. upsert layer preserves existing DB open on
        # subsequent runs (see upsert helper). Columns added by
        # 20260916e migration.
        'open_home_ml': g.get('open_home_ml') or g.get('close_home_ml'),
        'open_away_ml': g.get('open_away_ml') or g.get('close_away_ml'),
        'neutral_site': g.get('neutral_site'),
        'conference_game': g.get('conference_game'),
        'stats_source': stats_source,
        'home_team_stats_summary': home_summary,
        'away_team_stats_summary': away_summary,
        **proj,
        **ret_fields,
        **def_fields,
        **vol_fields,
        **roll_fields,
        'signal_confluence_net': conf_net,
        'signal_confluence_breakdown': breakdown,
    }
    # 2026-09-13 Market-anchor pass. When stats_source='prior_season_regressed'
    # (Wks 1-3), thin sample can produce hallucinated magnitudes vs market
    # (Andy audit: ARI +2 vs LAC when market has LAC -9.5). Blend the raw
    # model projection toward market by a tiered weight so pick DIRECTION is
    # preserved but magnitude stays reasonable. Full contract in
    # projection_anchor.py. `projected_spread_raw` retains the unblended
    # number for post-week grading + tier-tuning.
    try:
        from projection_anchor import anchor_projected_spread
        _raw = row.get('projected_spread')
        # 2026-09-16 pass sport so anchor can normalize close_spread's
        # sign convention (NCAAF pos=away fav) into projected_spread's
        # convention (pos=home fav) before blending. Prior version fed
        # both raw → MIA@WF anchored to +14.86 (WF favored) despite
        # market MIA -21 and raw model MIA -3.6 both being MIA-favored.
        _anchored, _w, _reason = anchor_projected_spread(
            row.get('close_spread'), _raw, stats_source, sport='NCAAF',
        )
        row['projected_spread_raw']    = _raw
        row['spread_anchor_weight']    = _w
        row['spread_anchor_reason']    = _reason
        row['projected_spread']        = _anchored
    except Exception as _e:
        # Non-fatal: leave raw projection in place if anchor helper errors.
        row['spread_anchor_reason']    = f'anchor_error:{type(_e).__name__}'
    score = compute_sweat_score(
        row.get('projected_spread'), row.get('close_spread'), conf_net,
        row.get('projected_total'), row.get('close_total'),
        proj_spread_source=row.get('projected_spread_source'),
    )
    row['sweat_score'] = score
    row['sweat_tier'] = sweat_tier(score)

    # 2026-09-06 cohort_tags — mirror nfl_game_context.compute_cohort_tags
    # so NCAAF games get situational badges rendered on the app
    # (compute_cohort_tags fields → chip renderer in GameDetailV2). Yesterday-
    # audit (9/5) found 0/86 NCAAF games had cohort_tags populated even
    # though splits + confluence were fine — bare tags is why users saw
    # zero situational badges. NCAAF equivalents of NFL's tag set:
    tags = []
    _sp = row.get('close_spread'); _tot = row.get('close_total')
    try: _sp = float(_sp) if _sp is not None else None
    except (TypeError, ValueError): _sp = None
    try: _tot = float(_tot) if _tot is not None else None
    except (TypeError, ValueError): _tot = None
    # Home dog getting big points (universal chalk-fade situation)
    if _sp is not None and _sp >= 7.0:
        tags.append('ncaaf_heavy_home_dog')
    # Heavy home favorite (FBS-vs-FCS chalk pattern from Week 1 audit)
    if _sp is not None and _sp <= -20.0:
        tags.append('ncaaf_heavy_home_fav')
    # Home favorite (any margin)
    if _sp is not None and _sp < 0:
        tags.append('ncaaf_home_fav')
    # Big total game (over 60 is a shootout on the CFB scale)
    if _tot is not None and _tot >= 60:
        tags.append('ncaaf_shootout')
    # Low total game
    if _tot is not None and _tot <= 42:
        tags.append('ncaaf_grinder')
    row['cohort_tags'] = tags

    # 2026-08-16 CUTOVER: ensemble_scorer v2 authority (NCAAF).
    ensemble_pp = None
    try:
        from ensemble_scorer import score_game as _ensemble_score
        from game_context import _compose_ensemble_sub
        from defensive_gates import apply_all_defensive_gates, reroute_ml_if_trapped
        decision = _ensemble_score('NCAAF', row)
        if decision is not None:
            # 2026-09-01: juice-trap ML reroute BEFORE picking top. When
            # ML side is priced at -300+ (heavy fav) or +400+ (long dog),
            # swap top_market to spread or total if either has a real
            # score. Motivation: NCAAF Week 1 Missouri-tier games price
            # ML at -3000 — surfacing that as a "take" is nonsense.
            decision = reroute_ml_if_trapped(decision, row, sport='NCAAF')
            top = decision.top()
            if top.pick is not None:
                # 2026-08-31: recommended_stake for unified sizing across sports.
                from game_context import compute_recommended_stake as _rs
                _rec_stake = _rs(top, mc_dissented=False)
                _reroute = getattr(top, '_ml_reroute', None)
                _audit = (f'ensemble_scorer v2 · NCAAF · {len(top.contributions)} sources · '
                          f'score={top.score:.2f} margin={top.margin:+.2f}')
                if _reroute:
                    _audit += f' · ML-reroute({_reroute["orig_market"]}→{top.market} @ {_reroute["orig_ml_price"]})'
                ensemble_pp = {
                    'type': top.market, 'tier': top.tier, 'label': top.display_label,
                    'side': top.side, 'line': top.line, 'conviction': top.conviction,
                    'score': round(top.score, 2), 'sub': _compose_ensemble_sub(top),
                    'recommended_stake': _rec_stake,
                    'audit_note': _audit,
                    '_engine': 'ensemble_v2',
                    '_ensemble_sources': [
                        {'signal_key': c.signal_key, 'class': c.signal_class,
                         'side': c.side, 'weight': round(c.weight, 2),
                         'n': c.n, 'contribution': round(c.contribution, 2),
                         'hit_rate': (round(c.hit_rate, 3) if c.hit_rate is not None else None),
                         'prose': c.display_prose}
                        for c in top.contributions[:8]
                    ],
                    # 2026-09-08 mirror NFL/NHL/NBA: capture all three
                    # market picks so retrospective "what if we'd played
                    # spread instead of ML" backtest is possible. Loss of
                    # this field on NCAAF made cross-market pattern
                    # discovery blind for the sport.
                    '_ensemble_all_markets': {
                        'ml':    {'pick': decision.ml.pick, 'label': decision.ml.display_label,
                                  'tier': decision.ml.tier, 'conviction': decision.ml.conviction},
                        'rl':    {'pick': decision.rl.pick, 'label': decision.rl.display_label,
                                  'tier': decision.rl.tier, 'conviction': decision.rl.conviction},
                        'total': {'pick': decision.total.pick, 'label': decision.total.display_label,
                                  'tier': decision.total.tier, 'conviction': decision.total.conviction},
                    },
                }
                if _reroute:
                    ensemble_pp['_ml_reroute'] = _reroute
                # 2026-09-01: apply defensive gates (OC flip → MC dissent
                # → juice-trap demote → publish gate). Juice-trap floor
                # is -300 for NCAAF. Reroute already handled the ML→spread
                # swap; the gate here catches leftover cases where reroute
                # couldn't find a clearing alt and ML still shipped.
                ensemble_pp = apply_all_defensive_gates(ensemble_pp, row, sport='NCAAF')
    except Exception:
        pass

    if ensemble_pp is not None:
        row['primary_play'] = ensemble_pp
    else:
        row['primary_play'] = compute_primary_play(row)
        if isinstance(row['primary_play'], dict):
            row['primary_play']['_engine'] = 'legacy_ncaaf_compute_primary_play'

    # 2026-09-27 · edge ceiling, after both branches so the legacy path is
    # covered too. One definition in model_edge.py — see the NHL call site.
    try:
        from model_edge import apply_to_pick
        row['primary_play'] = apply_to_pick(row.get('primary_play'), row)
    except ImportError:
        pass
    return row


def upsert(rows: list, dry_run: bool = False) -> int:
    if not rows: return 0
    if dry_run:
        for r in rows:
            pp = r.get('primary_play') or {}
            print(f"  [DRY] {r['game_id']}  {r['away_team']} @ {r['home_team']}  "
                  f"sp={r.get('close_spread')} proj={r.get('projected_spread')}  "
                  f"conf={r.get('signal_confluence_net'):+d}  ss={r['sweat_score']} {r['sweat_tier']}"
                  + (f"  → {pp.get('tier')} {pp.get('label')}" if pp else ''))
        return len(rows)

    # 2026-09-16: preserve existing OPEN values so subsequent pulls don't
    # overwrite the true week-open. See nfl_game_context.upsert_context
    # for identical pattern + rationale.
    _gids = [r['game_id'] for r in rows if r.get('game_id')]
    _existing_opens: dict = {}
    if _gids:
        try:
            import requests as _req
            _ids_csv = ','.join(f'"{g}"' for g in _gids)
            _resp = _req.get(
                f'{SB}/rest/v1/ncaaf_game_context',
                params={'game_id': f'in.({_ids_csv})',
                        'select': 'game_id,open_spread,open_total,open_home_ml,open_away_ml'},
                headers=H_READ, timeout=15)
            if _resp.status_code == 200:
                for _e in _resp.json():
                    _existing_opens[_e['game_id']] = _e
        except Exception as _e:
            print(f'  ⚠ NCAAF open-preserve lookup failed ({_e}) — proceeding')
    for _row in rows:
        _prev = _existing_opens.get(_row.get('game_id'))
        if not _prev: continue
        for _ok in ('open_spread', 'open_total', 'open_home_ml', 'open_away_ml'):
            if _prev.get(_ok) is not None:
                _row[_ok] = _prev[_ok]

    # 2026-08-29: DYNAMIC strip-on-400. Prior version had a hardcoded
    # STRIP_CANDIDATES list and every time we added a new ctx field
    # (like def_pass_ypg / def_rush_ypg / _explosiveness_allowed today),
    # the whole upsert 400'd until someone updated the list. Now we
    # regex the column name straight out of the PGRST204 error and
    # strip it, so new fields self-heal.
    import re as _re
    _COL_RE = _re.compile(r"Could not find the '([^']+)' column of '[^']+' in the schema cache")
    # 2026-08-29: normalize batch keys BEFORE first attempt. Per-team
    # merge fallback produces variable key sets across rows, and
    # PostgREST throws PGRST102 "All object keys must match" on
    # heterogeneous batches. Union keys, fill missing with None so
    # every row has the same shape.
    _all_keys = set()
    for _row in rows: _all_keys.update(_row.keys())
    for _row in rows:
        for _k in _all_keys:
            if _k not in _row: _row[_k] = None
    # ── 2026-09-21 PUBLISH LOCK ──────────────────────────────────────
    # NCAAF had NO write lock. MLB and NFL each got one on Andy's
    # directive ("whatever comes out in the morning stays" / "make sure
    # aren't overwritten"), and the other four sports were never covered —
    # so every later cron run silently rewrote picks that had already been
    # published, while the receipt kept the original. The card and the
    # receipt would then disagree with no record of the change.
    #
    # Gated at the WRITE because that is the one path every caller shares.
    # Preserves the published primary_play only; every other column still
    # refreshes. A game with no published pick is untouched — a gap is not
    # a change.
    try:
        from pick_lock import preserve_published
        _locked = 0
        for _row in rows:
            if preserve_published('NCAAF', 'ncaaf_game_context', _row, SB, H_WRITE):
                _locked += 1
        if _locked:
            print(f'  🔒 NCAAF publish lock: preserved {{_locked}} published pick(s)')
    except Exception as _e:
        print(f'  ⚠ NCAAF publish lock unavailable ({{type(_e).__name__}}) — writing unlocked')

    # 2026-10-07 · Stamp when these features were computed. Same fix as
    # nfl_game_context: nothing here ever wrote updated_at, so it held the
    # INSERT time and row freshness was unknowable. Reading it as staleness
    # produced a false CRITICAL on nine NFL games whose content was current.
    # Making it mean what its name says turns the next freshness question
    # into a lookup instead of forensics.
    _now_iso = datetime.now(timezone.utc).isoformat()
    for _row in rows:
        _row['updated_at'] = _now_iso

    r = requests.post(
        f'{SB}/rest/v1/ncaaf_game_context?on_conflict=game_id',
        headers=H_WRITE, json=rows, timeout=30,
    )
    stripped_total = []
    retry_rounds = 0
    while r.status_code == 400 and retry_rounds < 100:
        m = _COL_RE.search(r.text)
        if not m: break
        col = m.group(1)
        for row in rows: row.pop(col, None)
        stripped_total.append(col)
        r = requests.post(
            f'{SB}/rest/v1/ncaaf_game_context?on_conflict=game_id',
            headers=H_WRITE, json=rows, timeout=30,
        )
        retry_rounds += 1
    if stripped_total and r.status_code in (200, 201, 204):
        print(f'  ⚠ ncaaf ctx stripped {len(stripped_total)} unknown cols: {stripped_total} — add ALTER TABLE for these')
    if r.status_code == 409:
        # 2026-09-23. One row can no longer cost the other eighty-nine.
        #
        # ncaaf_game_results holds the same fixture twice for neutral-site
        # and UTC-boundary games — ncaaf_20261010_Oklahoma_Texas alongside
        # ncaaf_20261010_Texas_Oklahoma, and Georgia_Alabama on both 10-10
        # and 10-11 with OPPOSITE spread signs. The table's unique index on
        # (game_date, LEAST(home,away), GREATEST(home,away)) is right to
        # reject them. What was wrong is that a batch POST is atomic, so
        # the single conflicting pair aborted all 90 writes and returned 0.
        #
        # The workflow wraps this call in `|| echo "game_context failed"`,
        # so it printed one line and carried on. Every NCAAF context row
        # sat frozen from 2026-09-05 while team form kept refreshing around
        # it — which is how we came to publish "USC -2.5" against a market
        # of USC +3.0.
        #
        # Fall back to one request per row: good rows land, and the
        # conflicting fixtures are named instead of being inferred from a
        # truncated Postgres error.
        ok, clashed = 0, []
        for row in rows:
            rr = requests.post(
                f'{SB}/rest/v1/ncaaf_game_context?on_conflict=game_id',
                headers=H_WRITE, json=[row], timeout=30,
            )
            if rr.status_code in (200, 201, 204):
                ok += 1
            elif rr.status_code == 409:
                clashed.append(row.get('game_id'))
            else:
                print(f"  ⚠ {row.get('game_id')}: {rr.status_code} {rr.text[:120]}")
        if clashed:
            print(f'  ⚠ {len(clashed)} row(s) rejected as duplicate fixtures '
                  f'(same matchup already present under another game_id): '
                  f'{clashed[:6]}')
        print(f'  ↻ batch conflicted; wrote {ok}/{len(rows)} row-by-row')
        return ok
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    # 2026-08-23 Wave 1b multi-sport: snapshot primary_play per publish.
    try:
        from snapshot_writer import write_primary_play_snapshot
        for row in rows:
            write_primary_play_snapshot(SB, H_WRITE, 'NCAAF', row)
    except Exception:
        pass
    return len(rows)


def run(dry_run: bool = False) -> None:
    print(f'=== NCAAF game context · {_et_now().date()} ===')
    games = load_upcoming()
    print(f'  upcoming games (10d): {len(games)}')
    if not games:
        return
    season = games[0].get('season') or 2026
    team_stats, stats_source = load_team_stats_with_fallback(season)
    if stats_source == 'prior_season_regressed':
        print(f'  ⚠ current season {season} thin — falling back to {season-1} regressed to mean (LEAN cap on non-cohort plays)')
    elif stats_source == 'none':
        print(f'  ⚠ neither {season} nor {season-1} has usable team stats — cohort/market signal only')
    print(f'  team_stats: {len(team_stats)} teams  source={stats_source}')

    # 2026-08-09 Phase 2: load returning production for early-season variance
    # reduction. Silent no-op if migration not applied yet.
    returning_prod = load_returning_production(season)
    print(f'  returning_production: {len(returning_prod)} teams')

    # 2026-09-15 v1.06: preload rolling L4 team form (PPG/PA/total-avg/
    # over-rate) so build_context_row can stamp home_l4_*/away_l4_* onto
    # each ctx row. Feeds ncaaf_total_logreg — trainer already computes
    # these but had no ctx path to read them at inference. See the
    # helper's docstring for the full loop.
    rolling_form = load_team_rolling_form(season, window=4)
    print(f'  rolling_l4_form: {len(rolling_form)} teams')

    rows = [build_context_row(g, team_stats, stats_source=stats_source,
                                returning_prod=returning_prod,
                                rolling_form=rolling_form) for g in games]
    rows = [r for r in rows if r]
    written = upsert(rows, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}wrote {written} rows to ncaaf_game_context')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    # 2026-08-22: season gate. NCAAF is Aug-Jan. Off-season top-exit
    # rather than fetching empty Odds API responses + iterating no games.
    try:
        from season_gate import season_gate_or_exit
        season_gate_or_exit('NCAAF')
    except ImportError:
        pass
    main()
