"""Vault Match pattern registry — nightly recompute.

Iterates PATTERN_CATALOG entries, evaluates each against graded
history in the pattern's lookback window, upserts hit rates into
sport_pattern_registry table.

A pattern is a (sport, key, label, description, matches_fn, outcome_fn)
tuple:
  matches_fn(game_ctx)   → bool     — does this pattern fire on this game?
  outcome_fn(game_ctx)   → str      — 'W'/'L'/'P' — did the pattern's
                                      implied side hit the result?

Both functions read from a merged dict of {game_context row} + {game_results
row} joined on game_id. matches_fn uses pre-game fields (splits, tier, etc);
outcome_fn uses post-game fields (winner, cover, total_hit).

Adding a new pattern = one entry in PATTERN_CATALOG. No schema change,
no migration. The badge fires when the pattern's rolling n_total >= 15
AND hit_pct >= 65% (thresholds enforced by context-builder attach step,
not by this script — this script computes and stores; consumers decide
what to badge).

USAGE:
    python compute_sport_patterns.py                        # all sports
    python compute_sport_patterns.py --sport MLB
    python compute_sport_patterns.py --dry-run
"""
import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
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


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


# ─── Pattern helpers (read from merged game_ctx dict) ────────────────

def _splits_triple_confirmed(game: dict) -> list:
    """Extract triple_confirmed list from splits_summary jsonb."""
    ss = game.get('splits_summary') or {}
    tc = ss.get('triple_confirmed') if isinstance(ss, dict) else []
    return tc if isinstance(tc, list) else []


def _primary_play(game: dict) -> dict:
    pp = game.get('primary_play') or {}
    if isinstance(pp, str):
        import json
        try: pp = json.loads(pp)
        except Exception: pp = {}
    return pp if isinstance(pp, dict) else {}


def _pp_result_outcome(game: dict) -> str:
    """Grade the primary_play against game result. Returns 'W'/'L'/'P'/None-safe.

    CORRECTED 2026-09-01 (guardrail pass): field names match the actual
    canonical grader in aggregate_daily_records.py (line 460+):
      pp.type   'ml' | 'rl' | 'spread' | 'total'  (NOT 'market')
      pp.side   'HOME' | 'AWAY' | 'OVER' | 'UNDER'
      home_win  bool
      spread_result  'home_covered' | 'away_covered' | 'push' (lowercase)
      total_result   'over' | 'under' | 'push'  (case-normalized via .lower())
    """
    pp = _primary_play(game)
    ptype = str(pp.get('type') or '').lower()
    side  = str(pp.get('side') or '').upper()
    # 2026-09-20: was `return 'P'`. A game with NO primary_play is not a
    # push — it is not evidence at all and must be EXCLUDED. Counting it
    # as a push inflated n_total (which the badge gate reads) while
    # leaving hit_pct computed off a tiny real sample: NFL
    # nfl_road_fav_7plus_fade scored 1-0-12 = "100% (n=13)" off ONE
    # decided game, because 12 of 13 matched ctx rows had no play.
    if not ptype or not side:
        return None
    if ptype == 'ml':
        hw = game.get('home_win')
        if hw is None: return None
        if side == 'HOME': return 'W' if hw else 'L'
        if side == 'AWAY': return 'L' if hw else 'W'
        return None
    if ptype in ('rl', 'spread', 'puckline', 'runline'):
        sr = str(game.get('spread_result') or '').lower()
        if sr == 'push': return 'P'          # a REAL push
        if sr == 'home_covered': return 'W' if side == 'HOME' else 'L'
        if sr == 'away_covered': return 'W' if side == 'AWAY' else 'L'
        return None                          # ungraded, not a push
    if ptype == 'total':
        tr = str(game.get('total_result') or '').lower()
        if tr == 'push': return 'P'          # a REAL push
        if tr == 'over':  return 'W' if side == 'OVER' else 'L'
        if tr == 'under': return 'W' if side == 'UNDER' else 'L'
        return None                          # ungraded, not a push
    return None


def _spread_outcome_home_covered(game: dict) -> str:
    """Standalone spread outcome for patterns that back HOME regardless
    of primary_play. Used by e.g. nfl_home_div_dog. Reads spread_result."""
    sr = str(game.get('spread_result') or '').lower()
    if not sr or sr == 'none': return None   # ungraded, not a push
    if sr == 'push': return 'P'
    if sr == 'home_covered': return 'W'
    if sr == 'away_covered': return 'L'
    return None


def _spread_outcome_away_covered(game: dict) -> str:
    sr = str(game.get('spread_result') or '').lower()
    if not sr or sr == 'none': return None   # ungraded, not a push
    if sr == 'push': return 'P'
    if sr == 'away_covered': return 'W'
    if sr == 'home_covered': return 'L'
    return None


def _pp_result_outcome_faded(game: dict) -> str:
    """FADE variant of _pp_result_outcome — returns W when the primary_play
    side LOST (i.e., the fade won). For FADE patterns whose implied bet
    is the opposite of pp.side. Push stays push."""
    oc = _pp_result_outcome(game)
    if oc == 'W': return 'L'
    if oc == 'L': return 'W'
    return oc  # P stays P


# ─── ATS form helpers (team_recent_games) ───────────────────────────
#
# Andy approved 2026-09-02: "X team covering spread playing against a
# team who doesn't cover away kind of deal — can we surface as badge?"
#
# Source is the `team_recent_games` matview: one row per team per game
# with a TEAM-RELATIVE spread_result ('won' / 'lost' / 'push'), is_home,
# and game_date. 30,654 NCAAF rows / 3,434 NFL / 8,772 MLB back to 2020.
# Team names join at 100% for NFL and MLB, 94% for NCAAF (the misses are
# FCS programmes carrying mascot suffixes).
#
# ⚠ LOOKAHEAD IS THE WHOLE RISK HERE. "Covered 3 straight" is only a
# signal if it is computed from games that finished BEFORE the game being
# graded. Including the game itself — or any later game — leaks the
# outcome into the predictor and every streak pattern backtests
# beautifully and fails live. _prior_games filters strictly `<
# before_date` and is the only entry point; nothing below reads the
# matview directly.
_TRG_CACHE: dict = {}


def _load_trg(sport: str) -> dict:
    """team -> [rows] sorted oldest→newest, graded spread rows only."""
    sport = str(sport).upper()
    if sport in _TRG_CACHE:
        return _TRG_CACHE[sport]
    by_team: dict = defaultdict(list)
    off = 0
    while off < 80000:
        r = requests.get(
            f'{SB}/rest/v1/team_recent_games?select=team,game_date,is_home,'
            f'spread_result,total_result,won&sport=eq.{sport}'
            f'&limit=1000&offset={off}', headers=H_READ, timeout=60)
        chunk = r.json() if r.status_code == 200 else []
        if not chunk:
            break
        for row in chunk:
            sr = str(row.get('spread_result') or '').lower()
            if sr in ('won', 'lost', 'push'):
                by_team[row.get('team')].append(row)
        if len(chunk) < 1000:
            break
        off += 1000
    for t in by_team:
        by_team[t].sort(key=lambda x: str(x.get('game_date') or ''))
    _TRG_CACHE[sport] = by_team
    return by_team


def _prior_games(sport: str, team: str, before_date, location=None,
                 limit: int = 10) -> list:
    """Most-recent-first graded games STRICTLY BEFORE `before_date`.

    location: 'home' | 'road' | None.
    """
    if not team or not before_date:
        return []
    rows = _load_trg(sport).get(team) or []
    bd = str(before_date)[:10]
    out = []
    for row in reversed(rows):                 # newest first
        if str(row.get('game_date') or '')[:10] >= bd:
            continue                           # the guard: never same-day or later
        if location == 'home' and not row.get('is_home'):
            continue
        if location == 'road' and row.get('is_home'):
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _ats_streak(sport, team, before_date, location=None) -> int:
    """Consecutive ATS covers immediately before `before_date`.
    Negative = consecutive non-covers. Pushes end the streak."""
    n = 0
    for row in _prior_games(sport, team, before_date, location, limit=20):
        sr = str(row.get('spread_result') or '').lower()
        if sr == 'push':
            break
        if n == 0:
            n = 1 if sr == 'won' else -1
        elif (n > 0) == (sr == 'won'):
            n += 1 if n > 0 else -1
        else:
            break
    return n


def _ats_cover_pct(sport, team, before_date, n_games, location=None):
    """(covers, decided) over the last `n_games` before `before_date`."""
    rows = _prior_games(sport, team, before_date, location, limit=n_games)
    w = sum(1 for r in rows if str(r.get('spread_result') or '').lower() == 'won')
    d = sum(1 for r in rows if str(r.get('spread_result') or '').lower() in ('won', 'lost'))
    return w, d


def _is_home_fav(g: dict, sport: str) -> Optional[bool]:
    cs = g.get('close_spread')
    if cs is None:
        return None
    try:
        cs = float(cs)
    except (ValueError, TypeError):
        return None
    if cs == 0:
        return None
    neg = _HOME_FAV_NEGATIVE.get(str(sport).upper(), False)
    return (cs < 0) if neg else (cs > 0)


# close_spread sign is NOT shared across sports. Verified empirically
# 2026-09-20 on 263 NCAAF games: home teams win 92.1% when close_spread
# is NEGATIVE, so NCAAF (like MLB) stores negative = home favored. NFL is
# the outlier and stores positive = home favored.
_HOME_FAV_NEGATIVE = {'MLB': True, 'NCAAF': True, 'NCAAB': True,
                      'NBA': True, 'NHL': True, 'NFL': False}


def _road_favorite_spread_fn(threshold: float, sport: str = 'NFL'):
    """Factory: fires when the ROAD team is favored by `threshold`+.

    2026-09-20 SIGN FIX. This used one hardcoded convention for every
    sport ("close_spread > 0 = home favored, so road fav = cs <= -thr"),
    which is right for NFL and backwards for NCAAF. On NCAAF it therefore
    selected HOME favourites and, paired with _spread_outcome_home_covered,
    measured "do home favourites of 10+ cover" — 99-64, 60.74%, n=163 —
    while labelling it "CFB Road Fav 10+ · FADE".

    That combination cleared every guardrail and attached to 4 upcoming
    games including Oklahoma @ Georgia and South Carolina @ Alabama. The
    badge would have told users to fade a road favourite that does not
    exist, on games where the HOME team is favoured — pointing at the
    wrong side entirely. Third instance of the close_spread sign family
    today; see project_close_spread_sign_bug_914.
    """
    home_fav_neg = _HOME_FAV_NEGATIVE.get(str(sport).upper(), False)

    def _fn(g: dict) -> bool:
        cs = g.get('close_spread')
        if cs is None:
            return False
        try:
            cs = float(cs)
        except (ValueError, TypeError):
            return False
        # Road favourite means the AWAY side is laying the points.
        return (cs >= threshold) if home_fav_neg else (cs <= -threshold)
    return _fn


def _rest_edge_underdog_fn(threshold_days: int):
    """Factory: matches_fn that fires when the underdog has at least
    `threshold_days` more rest than the favorite. NFL convention:
    days_rest_home / days_rest_away in ctx; close_spread > 0 = home fav."""
    def _fn(g: dict) -> bool:
        cs = g.get('close_spread')
        rh = g.get('days_rest_home')
        ra = g.get('days_rest_away')
        if cs is None or rh is None or ra is None:
            return False
        try:
            cs = float(cs); rh = int(rh); ra = int(ra)
        except (ValueError, TypeError):
            return False
        # Home favored (cs > 0) → dog is away → back away if ra - rh >= threshold
        if cs > 0 and (ra - rh) >= threshold_days:
            return True
        # Away favored (cs < 0) → dog is home → back home if rh - ra >= threshold
        if cs < 0 and (rh - ra) >= threshold_days:
            return True
        return False
    return _fn


def _rest_edge_underdog_outcome(g: dict) -> str:
    """W when the underdog with rest edge covered. Determines which side
    from close_spread sign, then reads spread_result."""
    cs = g.get('close_spread')
    if cs is None: return None               # ungraded, not a push
    try:
        cs = float(cs)
    except (ValueError, TypeError):
        return None
    if cs > 0:  # dog is away
        return _spread_outcome_away_covered(g)
    if cs < 0:  # dog is home
        return _spread_outcome_home_covered(g)
    return 'P'


def _public_overload_pp_fn(threshold_pct: float):
    """Factory: matches_fn — public betting >= threshold on primary_play
    side. Reads splits_summary.per_market for the pp market/side, checks
    public bet% or money%. If splits_summary shape doesn't expose this,
    returns False (silent skip)."""
    def _fn(g: dict) -> bool:
        pp = _primary_play(g)
        pp_side = str(pp.get('side') or '').upper()
        pp_type = str(pp.get('type') or '').lower()
        if not pp_side or not pp_type:
            return False
        ss = g.get('splits_summary') or {}
        if not isinstance(ss, dict): return False
        pm = ss.get('per_market') or {}
        # Map pp.type to splits key
        market_key = {'ml': 'ml', 'rl': 'rl', 'spread': 'rl', 'total': 'total'}.get(pp_type)
        if not market_key: return False
        market = pm.get(market_key) or {}
        # For side-based markets (ml/rl), splits store per-side pct
        side_key = pp_side.lower()  # 'home'/'away'/'over'/'under'
        side_data = market.get(side_key) or {}
        # Try common field names — bet_pct, public_pct, tickets_pct
        for k in ('public_pct', 'bet_pct', 'tickets_pct', 'public_bets_pct'):
            v = side_data.get(k)
            if v is not None:
                try:
                    return float(v) >= threshold_pct
                except (ValueError, TypeError):
                    pass
        return False
    return _fn


def _confluence_backs_pp_fn(min_abs_net: int):
    """Factory: matches_fn — signal_confluence_net has absolute value
    >= min_abs_net AND agrees direction with primary_play side."""
    def _fn(g: dict) -> bool:
        net = g.get('signal_confluence_net')
        if net is None: return False
        try:
            net = int(net)
        except (ValueError, TypeError):
            return False
        if abs(net) < min_abs_net: return False
        pp = _primary_play(g)
        pp_side = str(pp.get('side') or '').upper()
        # net > 0 = home, net < 0 = away
        if net > 0 and pp_side == 'HOME': return True
        if net < 0 and pp_side == 'AWAY': return True
        return False
    return _fn


def _sp_plus_underdog_fn(g: dict) -> bool:
    """NCAAF: SP+ predicts the market underdog. Fires when the sign of
    SP+ implied margin disagrees with the sign of close_spread."""
    sp_h = g.get('sp_plus_pred_home_pts')
    sp_a = g.get('sp_plus_pred_away_pts')
    cs = g.get('close_spread')
    if sp_h is None or sp_a is None or cs is None: return False
    try:
        sp_margin = float(sp_h) - float(sp_a)  # positive = home favored
        cs = float(cs)  # positive = home favored (nflverse convention)
    except (ValueError, TypeError):
        return False
    # SP+ favors home (sp_margin > 0) but market favors away (cs < 0) → back home dog
    if sp_margin > 3.0 and cs < -3.0: return True
    # SP+ favors away (sp_margin < 0) but market favors home (cs > 0) → back away dog
    if sp_margin < -3.0 and cs > 3.0: return True
    return False


def _f_safe(v):
    """Safe float coerce — returns None on any failure."""
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _sp_plus_underdog_outcome(g: dict) -> str:
    """Grade: did the SP+-favored (market underdog) side cover?"""
    sp_h = g.get('sp_plus_pred_home_pts')
    sp_a = g.get('sp_plus_pred_away_pts')
    if sp_h is None or sp_a is None: return 'P'
    try:
        sp_margin = float(sp_h) - float(sp_a)
    except (ValueError, TypeError):
        return 'P'
    if sp_margin > 0:  # SP+ favors home
        return _spread_outcome_home_covered(g)
    if sp_margin < 0:  # SP+ favors away
        return _spread_outcome_away_covered(g)
    return 'P'


# ─── PATTERN CATALOG ─────────────────────────────────────────────────
# Add new patterns here. Each is (sport, key, label, description,
# lookback_days, matches_fn, outcome_fn).

PATTERN_CATALOG = [
    # ─── MLB ─────────────────────────────────────────────────────────
    {
        'sport': 'MLB',
        'key': 'mlb_sharp_confirmed_prime',
        'label': 'Sharp $ + PRIME',
        'direction': 'BACK',
        'description': 'MLB PRIMEs with 2+ sources confirming the sharp side. Backs the model when the market agrees.',
        'lookback_days': 30,
        'matches': lambda g: (
            len(_splits_triple_confirmed(g)) > 0
            and str(_primary_play(g).get('tier') or '').upper() == 'PRIME'
        ),
        'outcome': _pp_result_outcome,
    },
    {
        'sport': 'MLB',
        'key': 'mlb_sharp_confirmed_strong',
        'label': 'Sharp $ + STRONG',
        'direction': 'BACK',
        'description': 'MLB STRONGs with 2+ sources confirming the sharp side.',
        'lookback_days': 30,
        'matches': lambda g: (
            len(_splits_triple_confirmed(g)) > 0
            and str(_primary_play(g).get('tier') or '').upper() == 'STRONG'
        ),
        'outcome': _pp_result_outcome,
    },
    {
        'sport': 'MLB',
        'key': 'mlb_confluence_backs_prime',
        'label': 'Confluence + PRIME',
        'direction': 'BACK',
        'description': 'MLB PRIMEs where signal_confluence_net has |value| ≥ 3 and agrees direction with our pick. Deep multi-signal agreement.',
        'lookback_days': 60,
        'matches': lambda g: (
            str(_primary_play(g).get('tier') or '').upper() == 'PRIME'
            and _confluence_backs_pp_fn(3)(g)
        ),
        'outcome': _pp_result_outcome,
    },
    {
        'sport': 'MLB',
        'key': 'mlb_public_overload_fade',
        'label': 'Public Trap',
        'direction': 'FADE',
        'description': 'MLB pick that Vegas public has piled 70%+ onto. Historically the public-heavy side underperforms — fade the pick.',
        'lookback_days': 90,
        'matches': _public_overload_pp_fn(70.0),
        'outcome': _pp_result_outcome_faded,  # W when pp side LOSES
    },

    # 2026-09-02: INVERSE VARIANTS — starter "sharp confirmed = win" thesis
    # audits at 48-51% (below coin flip). Testing whether the INVERSE holds
    # (i.e., sharp confirmation is a fade signal, not a back signal). If
    # these validate at 65%+, we've found the real edge — was inverted
    # from day one. Shadow mode protects users while we test.
    {
        'sport': 'MLB',
        'key': 'mlb_sharp_confirmed_prime_fade',
        'label': 'Fade Sharp+PRIME',
        'direction': 'FADE',
        'description': 'MLB PRIMEs with 2+ sources confirming sharp side — but empirically these lose. Fade the pick.',
        'lookback_days': 30,
        'matches': lambda g: (
            len(_splits_triple_confirmed(g)) > 0
            and str(_primary_play(g).get('tier') or '').upper() == 'PRIME'
        ),
        'outcome': _pp_result_outcome_faded,
    },
    {
        'sport': 'MLB',
        'key': 'mlb_sharp_confirmed_strong_fade',
        'label': 'Fade Sharp+STRONG',
        'direction': 'FADE',
        'description': 'MLB STRONGs with 2+ sources confirming sharp side — but empirically these lose. Fade the pick.',
        'lookback_days': 30,
        'matches': lambda g: (
            len(_splits_triple_confirmed(g)) > 0
            and str(_primary_play(g).get('tier') or '').upper() == 'STRONG'
        ),
        'outcome': _pp_result_outcome_faded,
    },

    # ─── Memory-based patterns (from tracked findings) ─────────────
    # 2026-09-02: patterns derived from documented user memories.
    # If any validate at 65%+, they graduate from memory → live badge.
    {
        'sport': 'MLB',
        'key': 'mlb_heavy_fav_ml_trap',
        'label': 'Heavy Fav Trap',
        'direction': 'FADE',
        'description': 'MLB ML pick on team priced at -200 or heavier juice. Per feedback_heavy_fav_ml_trap_803 — heavy ML favorites underperform their implied win rate; fade the ML.',
        'lookback_days': 90,
        'matches': lambda g: (
            str(_primary_play(g).get('type') or '').lower() == 'ml'
            and _primary_play(g).get('conviction') is not None
            and (
                # Home fav @ -200+ juice
                (str(_primary_play(g).get('side') or '').upper() == 'HOME'
                 and g.get('home_ml') is not None
                 and _f_safe(g.get('home_ml')) is not None
                 and _f_safe(g.get('home_ml')) <= -200)
                or
                # Away fav @ -200+ juice
                (str(_primary_play(g).get('side') or '').upper() == 'AWAY'
                 and g.get('away_ml') is not None
                 and _f_safe(g.get('away_ml')) is not None
                 and _f_safe(g.get('away_ml')) <= -200)
            )
        ),
        'outcome': _pp_result_outcome_faded,
    },
    {
        'sport': 'MLB',
        'key': 'mlb_mc_hard_dissent_fade',
        'label': 'MC Hard Dissent',
        'direction': 'FADE',
        'description': 'MLB pick where MC simulation gives our side <40% win probability. Per defensive_gates — MC hard dissent flags catastrophic model divergence; fade the pick.',
        'lookback_days': 60,
        'matches': lambda g: (
            _primary_play(g).get('_mc_dissent') is not None
            and _primary_play(g).get('_mc_dissent', {}).get('mc_pick_win_pct') is not None
            and float(_primary_play(g).get('_mc_dissent', {}).get('mc_pick_win_pct') or 100) < 40
        ),
        'outcome': _pp_result_outcome_faded,
    },

    # ─── NFL ─────────────────────────────────────────────────────────
    {
        'sport': 'NFL',
        'key': 'nfl_home_div_dog',
        'label': 'Home Div Dog',
        'direction': 'BACK',
        'description': 'NFL home team is a divisional underdog. Historically hits the spread at an above-market rate.',
        'lookback_days': 365,
        'matches': lambda g: (
            g.get('div_game') is True
            and g.get('close_spread') is not None
            and float(g.get('close_spread', 0)) < 0
        ),
        'outcome': _spread_outcome_home_covered,
    },
    {
        'sport': 'NFL',
        'key': 'nfl_road_fav_7plus_fade',
        'label': 'Road Fav 7+',
        'direction': 'FADE',
        'description': 'NFL road favorites of 7+ points. Classic angle — travel + inflated public perception. Fade the road favorite.',
        'lookback_days': 730,
        'matches': _road_favorite_spread_fn(7.0, 'NFL'),
        # FADE the road fav means backing the home dog to cover
        'outcome': _spread_outcome_home_covered,
    },
    {
        'sport': 'NFL',
        'key': 'nfl_rest_edge_dog_back',
        'label': 'Rest Edge Dog',
        'direction': 'BACK',
        'description': 'NFL underdog with a 3+ day rest advantage (short-week fav vs bye-week dog, TNF fav vs SNF dog, etc). Back the rested dog.',
        'lookback_days': 730,
        'matches': _rest_edge_underdog_fn(3),
        'outcome': _rest_edge_underdog_outcome,
    },

    # ─── External-source dissent (2026-09-02) ────────────────────────
    # From project_dissent_audit_822: MAJ_when_CZ_dissents +16pp winner,
    # 3_of_3_AGREE -11pp fade. Data source: split_dissent_snapshots
    # populated nightly by compute_split_dissent_rollup.py. Guardrails
    # (n>=15, hit>=65%, Wilson) still apply — patterns only render
    # when they clear the threshold based on live-graded outcomes.
    {
        'sport': 'MLB',
        'key': 'mlb_maj_when_cz_dissents_ml',
        'label': 'MAJ · CZ Dissents',
        'direction': 'BACK',
        'description': 'MLB ML: 2 sources agree on sharp side, CZ (Split 2) dissents. Per dissent audit — the majority sharp side hits +16pp above baseline.',
        'lookback_days': 90,
        'matches': lambda g: (
            (g.get('_dissent') or {}).get('ml', {}).get('agreement') == 'MAJ_2/3'
            and (g.get('_dissent') or {}).get('ml', {}).get('dissenter') == 'cz'
        ),
        # Outcome: did the majority sharp side win?
        'outcome': lambda g: _spread_outcome_home_covered(g) if (g.get('_dissent') or {}).get('ml', {}).get('majority_side') == 'HOME'
                             else _spread_outcome_away_covered(g) if (g.get('_dissent') or {}).get('ml', {}).get('majority_side') == 'AWAY'
                             else 'P',
    },
    {
        'sport': 'MLB',
        'key': 'mlb_maj_when_oc_dissents_ml',
        'label': 'MAJ · OC Dissents',
        'direction': 'BACK',
        'description': 'MLB ML: 2 sources agree on sharp side, OC (Split 3) dissents. Per per_source_tracker_moat_818 — 77% hit rate.',
        'lookback_days': 90,
        'matches': lambda g: (
            (g.get('_dissent') or {}).get('ml', {}).get('agreement') == 'MAJ_2/3'
            and (g.get('_dissent') or {}).get('ml', {}).get('dissenter') == 'oc'
        ),
        'outcome': lambda g: _spread_outcome_home_covered(g) if (g.get('_dissent') or {}).get('ml', {}).get('majority_side') == 'HOME'
                             else _spread_outcome_away_covered(g) if (g.get('_dissent') or {}).get('ml', {}).get('majority_side') == 'AWAY'
                             else 'P',
    },
    {
        'sport': 'MLB',
        'key': 'mlb_all_3_agree_fade_ml',
        'label': 'All-3 Agree FADE',
        'direction': 'FADE',
        'description': 'MLB ML: all 3+ external sources agree on sharp side. Per dissent audit — counter-intuitively, when everyone agrees they LOSE at -11pp (fade signal).',
        'lookback_days': 90,
        'matches': lambda g: (
            (g.get('_dissent') or {}).get('ml', {}).get('agreement') == 'TRIPLE'
        ),
        # FADE: outcome = W when the majority (consensus) side LOST
        'outcome': lambda g: (
            'W' if (
                ((g.get('_dissent') or {}).get('ml', {}).get('majority_side') == 'HOME'
                 and _spread_outcome_home_covered(g) == 'L')
                or
                ((g.get('_dissent') or {}).get('ml', {}).get('majority_side') == 'AWAY'
                 and _spread_outcome_away_covered(g) == 'L')
            ) else 'L' if (
                ((g.get('_dissent') or {}).get('ml', {}).get('majority_side') == 'HOME'
                 and _spread_outcome_home_covered(g) == 'W')
                or
                ((g.get('_dissent') or {}).get('ml', {}).get('majority_side') == 'AWAY'
                 and _spread_outcome_away_covered(g) == 'W')
            ) else 'P'
        ),
    },

    # ─── NCAAF ───────────────────────────────────────────────────────
    {
        'sport': 'NCAAF',
        'key': 'ncaaf_rz_finisher_back',
        'label': 'RZ Finisher',
        'direction': 'BACK',
        'description': 'NCAAF team with elite red-zone TD rate (>=70%) as favorite. Finishers cover more consistently — market often undervalues drive-completion.',
        'lookback_days': 730,
        'matches': lambda g: (
            (_f_safe(g.get('home_rz_td_rate')) is not None
             and _f_safe(g.get('home_rz_td_rate')) >= 0.70
             and _f_safe(g.get('close_spread')) is not None
             and _f_safe(g.get('close_spread')) > 0)
            or
            (_f_safe(g.get('away_rz_td_rate')) is not None
             and _f_safe(g.get('away_rz_td_rate')) >= 0.70
             and _f_safe(g.get('close_spread')) is not None
             and _f_safe(g.get('close_spread')) < 0)
        ),
        # BACK the RZ-elite favorite (home if home fav w/ RZ, away if away fav w/ RZ)
        'outcome': lambda g: (
            _spread_outcome_home_covered(g) if (_f_safe(g.get('home_rz_td_rate')) or 0) >= 0.70 and (_f_safe(g.get('close_spread')) or 0) > 0
            else _spread_outcome_away_covered(g) if (_f_safe(g.get('away_rz_td_rate')) or 0) >= 0.70 and (_f_safe(g.get('close_spread')) or 0) < 0
            else 'P'
        ),
    },
    {
        'sport': 'NCAAF',
        'key': 'ncaaf_to_lucky_fade',
        'label': 'TO Luck FADE',
        'direction': 'FADE',
        'description': 'NCAAF team with TO margin >= +8 for the season. Turnover luck regresses toward mean — fade the hot-margin team, back the opponent.',
        'lookback_days': 730,
        'matches': lambda g: (
            (_f_safe(g.get('home_to_margin')) is not None and _f_safe(g.get('home_to_margin')) >= 8)
            or
            (_f_safe(g.get('away_to_margin')) is not None and _f_safe(g.get('away_to_margin')) >= 8)
        ),
        # Fade the TO-lucky team = back the OTHER team
        'outcome': lambda g: (
            _spread_outcome_away_covered(g) if (_f_safe(g.get('home_to_margin')) or 0) >= 8
            else _spread_outcome_home_covered(g) if (_f_safe(g.get('away_to_margin')) or 0) >= 8
            else 'P'
        ),
    },
    {
        'sport': 'NCAAF',
        'key': 'ncaaf_top10_trap_fade',
        'label': 'Top-10 Trap',
        'direction': 'FADE',
        'description': 'NCAAF top-10 team laying 17+ points vs unranked opponent. Classic look-ahead / lookaround trap — historically fade the favorite (they fail to cover).',
        'lookback_days': 730,
        'matches': lambda g: (
            # One team ranked top-10, other unranked (or >25)
            (
                (_f_safe(g.get('home_ap_rank')) is not None
                 and _f_safe(g.get('home_ap_rank')) <= 10
                 and (_f_safe(g.get('away_ap_rank')) is None or _f_safe(g.get('away_ap_rank')) > 25)
                 and _f_safe(g.get('close_spread')) is not None
                 and _f_safe(g.get('close_spread')) >= 17)
                or
                (_f_safe(g.get('away_ap_rank')) is not None
                 and _f_safe(g.get('away_ap_rank')) <= 10
                 and (_f_safe(g.get('home_ap_rank')) is None or _f_safe(g.get('home_ap_rank')) > 25)
                 and _f_safe(g.get('close_spread')) is not None
                 and _f_safe(g.get('close_spread')) <= -17)
            )
        ),
        # FADE the favorite = back the underdog covering
        'outcome': lambda g: (
            # Favorite is home (close_spread >= 17 means home fav) → back away
            _spread_outcome_away_covered(g) if (_f_safe(g.get('close_spread')) or 0) >= 17
            # Favorite is away (close_spread <= -17 means away fav) → back home
            else _spread_outcome_home_covered(g) if (_f_safe(g.get('close_spread')) or 0) <= -17
            else 'P'
        ),
    },
    {
        'sport': 'NCAAF',
        'key': 'ncaaf_road_fav_10plus_fade',
        'label': 'CFB Road Fav 10+',
        'direction': 'FADE',
        'description': 'NCAAF road favorites of 10+ points. Road environments are punishing (crowd, travel, altitude). Fade the road favorite.',
        'lookback_days': 730,
        'matches': _road_favorite_spread_fn(10.0, 'NCAAF'),
        'outcome': _spread_outcome_home_covered,
    },
    {
        'sport': 'NCAAF',
        'key': 'ncaaf_sp_underdog_edge',
        'label': 'SP+ Dog Edge',
        'direction': 'BACK',
        'description': 'NCAAF game where SP+ efficiency rating favors the market underdog by 3+ points. Model disagrees with market — back the SP+ pick.',
        'lookback_days': 730,
        'matches': _sp_plus_underdog_fn,
        'outcome': _sp_plus_underdog_outcome,
    },

    # ─── NBA / NCAAB / NHL — extend once season data thickens ──────
    # NBA opens 10/22; NCAAB 11/3; NHL 10/7. Add patterns once we have
    # enough graded games (30+) to compute meaningful hit rates.
]


# ─── ATS form patterns — TESTED AND REJECTED 2026-09-21 ─────────────
#
# Andy approved this idea 2026-09-02 ("X team covering spread playing
# against a team who doesn't cover away"). Built, then power-tested
# against team_recent_games BEFORE shipping a badge. It does not work.
#
# The catalog backtest is limited to games present in *_game_context
# (239 graded NCAAF), which is far too thin to judge a pattern. The
# matview holds 12,544 NCAAF / 3,434 NFL / 4,090 MLB team-games back to
# 2020. Tested there, with every streak computed strictly from prior
# games:
#
#   NEXT-COVER RATE BY PRIOR ATS STREAK (base is exactly 50.00% — every
#   game has one covering side, so across team-games it must be)
#     NCAAF  cold 3+  50.29%  [47.6, 52.9]  n=1362
#     NCAAF  hot  3+  49.28%  [46.6, 51.9]  n=1380
#     NFL    cold 3+  50.40%  [45.4, 55.4]  n=379
#     MLB    cold 3+  53.51%  [49.1, 57.9]  n=484
#   Not one bucket's Wilson 95% lower bound clears the base rate.
#
#   ATS CLASH (home covering 60%+ at home vs road team covering <=40%)
#     NCAAF  596-598 = 49.92%  [47.1, 52.7]  n=1194
#     NFL    177-196 = 47.45%  [42.4, 52.5]  n=373
#     MLB    257-256 = 50.10%  [45.8, 54.4]  n=513
#
# The catalog version briefly looked great — ncaaf_ats_cold3_home_back
# scored 21-10-1 = 67.74% on n=31 — but its MIRROR (fade the hot home
# team) also beat the base rate, which is the tell that the streak was
# doing no work and the small sample was doing all of it.
#
# Patterns removed rather than left in to fail the guardrails on every
# cron. The helpers above (_prior_games / _ats_streak / _ats_cover_pct)
# are KEPT: they are correct, lookahead-safe, and the next pattern idea
# that needs team form should use them instead of rebuilding them.



# ─── Data fetch ──────────────────────────────────────────────────────

def _fetch_dissent_snapshots(sport: str, game_ids: list) -> dict:
    """Return {game_id: {market: {agreement, dissenter, majority_side, n_sources}}}.
    2026-09-02: powers dissent-based Vault Match patterns."""
    if not game_ids: return {}
    out = {}
    for i in range(0, len(game_ids), 200):
        batch = game_ids[i:i+200]
        r = requests.get(
            f'{SB}/rest/v1/split_dissent_snapshots?'
            f'select=game_id,market,agreement,dissenter,majority_side,n_sources'
            f'&sport=eq.{sport}'
            f'&game_id=in.({",".join(str(g) for g in batch)})',
            headers=H_READ, timeout=30,
        )
        if r.status_code == 200:
            for row in r.json():
                gid = str(row.get('game_id'))
                mkt = row.get('market')
                if gid and mkt:
                    out.setdefault(gid, {})[mkt] = row
    return out


def fetch_games(sport: str, lookback_days: int) -> list:
    """Fetch graded game_context rows joined with results for a sport.
    Returns merged dicts with pre-game ctx + post-game result fields.
    2026-09-02: also merges split_dissent_snapshots per game/market."""
    cutoff = (_et_now().date() - timedelta(days=lookback_days)).isoformat()
    ctx_table = f'{sport.lower()}_game_context'
    res_table = f'{sport.lower()}_game_results'

    # Fetch context rows in the window
    ctx_rows = []
    off = 0
    while True:
        r = requests.get(
            f'{SB}/rest/v1/{ctx_table}?'
            f'select=*&game_date=gte.{cutoff}&limit=1000&offset={off}',
            headers=H_READ, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not chunk:
            break
        ctx_rows.extend(chunk)
        if len(chunk) < 1000:
            break
        off += 1000

    if not ctx_rows:
        return []

    # Fetch matching results
    game_ids = [str(g.get('game_id')) for g in ctx_rows if g.get('game_id')]
    if not game_ids:
        return []

    # Chunk in batches of 200 (PostgREST IN clause limit)
    res_by_gid = {}
    for i in range(0, len(game_ids), 200):
        batch = game_ids[i:i+200]
        r = requests.get(
            f'{SB}/rest/v1/{res_table}?'
            f'select=*&game_id=in.({",".join(batch)})',
            headers=H_READ, timeout=30,
        )
        if r.status_code == 200:
            for row in r.json():
                res_by_gid[str(row.get('game_id'))] = row

    # 2026-09-20 NFL JOIN FIX — the game_id join is BROKEN for NFL and has
    # been since this shipped. nfl_game_context stores MD5 hashes
    # ('171aba127aaad3dcbe32a02fc194eb32') while nfl_game_results stores
    # readable keys ('20261227_LAC_MIA'). Measured overlap: 0 of 316 ctx
    # ids. So fetch_games returned "0 graded games" for NFL on every run,
    # all three NFL patterns never got a registry row, and NFL Vault Match
    # rendered nothing from day one. MLB (185/187) and NCAAF (266/302)
    # join fine, which is why this stayed invisible.
    #
    # Third site of project_nfl_game_id_mismatch_911. Same remedy as the
    # receipts grader: fall back to the (date, away, home) tuple, which
    # identifies a game regardless of which id scheme a table chose.
    unmatched = [g for g in ctx_rows if str(g.get('game_id')) not in res_by_gid]
    if unmatched:
        def _k(d, away, home):
            def n(s):
                return re.sub(r'[^a-z0-9]', '', str(s or '').lower())
            return (str(d or '')[:10], n(away), n(home))

        res_by_tuple = {}
        off2 = 0
        while off2 < 40000:
            rr = requests.get(
                f'{SB}/rest/v1/{res_table}?select=*'
                f'&game_date=gte.{cutoff}&limit=1000&offset={off2}',
                headers=H_READ, timeout=60,
            )
            chunk = rr.json() if rr.status_code == 200 else []
            if not chunk:
                break
            for row in chunk:
                res_by_tuple[_k(row.get('game_date'), row.get('away_team'),
                                row.get('home_team'))] = row
            if len(chunk) < 1000:
                break
            off2 += 1000
        recovered = 0
        for g in unmatched:
            row = res_by_tuple.get(_k(g.get('game_date'), g.get('away_team'),
                                      g.get('home_team')))
            if row:
                res_by_gid[str(g.get('game_id'))] = row
                recovered += 1
        if recovered:
            print(f'    → recovered {recovered}/{len(unmatched)} results via '
                  f'(date,away,home) tuple — game_id join missed them')

    # 2026-09-02: also fetch dissent snapshots for these games
    dissent_by_gid = _fetch_dissent_snapshots(sport, game_ids)

    # Merge: ctx + result + dissent snapshot. Only keep games with a result.
    merged = []
    for g in ctx_rows:
        gid = str(g.get('game_id'))
        res = res_by_gid.get(gid)
        if not res:
            continue
        # Attach dissent snapshot dict keyed by market
        # (e.g. game['_dissent']['ml'] = {'agreement':'MAJ_2/3', 'dissenter':'cz', ...})
        merged.append({**g, **res, '_dissent': dissent_by_gid.get(gid, {})})

    return merged


# ─── Compute + upsert ────────────────────────────────────────────────

def compute_pattern(games: list, pattern: dict) -> Optional[dict]:
    """Evaluate one pattern against a list of merged games."""
    matches_fn = pattern['matches']
    outcome_fn = pattern['outcome']
    w = l = p = 0
    for g in games:
        try:
            if not matches_fn(g):
                continue
            oc = outcome_fn(g)
        except Exception:
            continue
        if oc == 'W': w += 1
        elif oc == 'L': l += 1
        elif oc == 'P': p += 1

    n = w + l
    if n == 0 and p == 0:
        return None
    hit_pct = round(100.0 * w / n, 2) if n > 0 else None
    return {
        'sport': pattern['sport'],
        'pattern_key': pattern['key'],
        'pattern_label': pattern['label'],
        'pattern_description': pattern.get('description') or '',
        'direction': pattern.get('direction', 'BACK'),
        'lookback_days': pattern.get('lookback_days', 30),
        'n_wins': w,
        'n_losses': l,
        'n_pushes': p,
        'n_total': n,
        'hit_pct': hit_pct,
        'last_computed_at': _et_now().isoformat(),
    }


def upsert_patterns(records: list, dry_run: bool = False) -> int:
    if not records:
        return 0
    if dry_run:
        for r in records:
            print(f"  [DRY] {r['sport']:5} {r['pattern_key']:32} "
                  f"{r['n_wins']}-{r['n_losses']}-{r['n_pushes']} = "
                  f"{r['hit_pct']}% (n={r['n_total']}) — {r['pattern_label']}")
        return len(records)

    r = requests.post(
        f'{SB}/rest/v1/sport_pattern_registry?'
        f'on_conflict=sport,pattern_key',
        headers=H_WRITE, json=records, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(records)


# ─── Entry ───────────────────────────────────────────────────────────

def run(sport_filter: Optional[str] = None, dry_run: bool = False) -> None:
    print('=== Vault Match pattern recompute ===')
    catalog = [p for p in PATTERN_CATALOG
               if not sport_filter or p['sport'] == sport_filter.upper()]
    if not catalog:
        print(f'  no patterns matched filter {sport_filter!r}')
        return

    # Group by (sport, lookback_days) so we fetch each window once
    fetch_keys = defaultdict(list)
    for p in catalog:
        fetch_keys[(p['sport'], p['lookback_days'])].append(p)

    all_records = []
    for (sport, days), patterns in fetch_keys.items():
        print(f'\n  fetching {sport} games (last {days}d)...')
        games = fetch_games(sport, days)
        print(f'    → {len(games)} graded games')
        if not games:
            continue
        for p in patterns:
            rec = compute_pattern(games, p)
            if rec:
                all_records.append(rec)

    if not all_records:
        print('\n  no pattern records to write')
        return

    n = upsert_patterns(all_records, dry_run)
    verb = '[DRY]' if dry_run else 'wrote'
    print(f'\n  {verb} {n} pattern rows')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', help='Only recompute this sport (MLB/NFL/NCAAF/etc)')
    ap.add_argument('--dry-run', action='store_true', help='Print without writing')
    args = ap.parse_args()
    run(sport_filter=args.sport, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
