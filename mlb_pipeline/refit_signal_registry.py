"""refit_signal_registry — compute per-signal hit_rate from graded history.

2026-08-21 companion to the ramp-up prior shipped same day. Fresh signals
get RAMP_UP_PRIOR=0.50 for their first 30 days. After that, they need a
real registry entry — otherwise they drop to the 0.15/0.20 floor and get
silently ignored again.

This script walks recent graded outcomes and rebuilds signal_registry
rows for every signal that fired ≥ min_n times. Two data sources:

  * prop_playbook_decisions (result in W/L/Push) — prop signals
  * mlb_game_context.primary_play (grade against home/away score,
    close_spread, close_total) — game-ensemble signals

Grading rule (identical for both):
  A signal contributed to a specific side. If that side won the outcome,
  the signal was "right." If it lost, wrong. Push is excluded from the
  sample (no signal on push).

Tier assignment:
  VALIDATED     — n ≥ 50 AND hit_rate ≥ 55%   (proven edge, high sample)
  DISCOVERY     — n ≥ 20 AND hit_rate ≥ 55%   (promising, small sample)
  ANTI_VALIDATED — n ≥ 25 AND hit_rate ≤ 47%   (real fade edge)
  UNVALIDATED   — otherwise                     (noise or too thin)

Run weekly (or nightly). Idempotent: full recompute + upsert each run.

CLI:
  python refit_signal_registry.py                      # last 90d, all sports
  python refit_signal_registry.py --days 60 --sport MLB
  python refit_signal_registry.py --dry-run
  python refit_signal_registry.py --min-n 10           # dev: include thin samples
"""
from __future__ import annotations
import argparse, os, sys
from collections import defaultdict, Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# Sport → (ctx table for live picks, resolved-results table with final scores).
# game_context is where fresh primary_play lives BEFORE the game. results tables
# store the graded final state including primary_play snapshot at write time.
SPORTS_WITH_CTX = {
    'MLB':   ('mlb_game_context',   'mlb_game_results'),
    'NFL':   ('nfl_game_context',   'nfl_game_results'),
    'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results'),
    'NBA':   ('nba_game_context',   'nba_game_results'),
    'NHL':   ('nhl_game_context',   'nhl_game_results'),
}


def _tier_for(n: int, hit_rate_frac: float,
              blind_frac: Optional[float] = None,
              be_frac: Optional[float] = None) -> str:
    """Tier a signal. With `blind_frac`, judge it against ITS OWN SIDE.

    ══ 2026-10-11 · THE FLAT THRESHOLDS WERE SIDE-BLIND ══
    >=55% earned VALIDATED and <=47% earned ANTI regardless of which side
    the signal backs. In NCAAF home wins 58.9% of moneylines, so a
    home-ML signal hitting 56% was being stamped VALIDATED for performing
    WORSE than a blind bet on home — and VALIDATED is exactly what the
    ensemble's credibility floor reads to decide a pick may reach a card.
    Same defect as the edge_weight benchmark fixed in fe806500; fixing one
    and not the other would just move it.

    `blind_frac` is the rate a blind bet on the same side/market mix would
    have returned over the same firings. When present, a signal must beat
    it by more than its own 2SE — so a thin sample cannot earn VALIDATED
    on a lucky run, which the flat rule allowed at n=20.

    Falls back to the flat rule when no baseline is known (prop and
    attribution paths, sports with no measured side rate), so nothing that
    worked before changes.
    """
    # ══ 2026-10-29 · THE FLAT >=55% FALLBACK PROMOTED A BREAK-EVEN SIGNAL ══
    # With no side baseline this branch used a flat >=55%. On a market whose
    # breakeven is 65.8% (the NHL dog puckline, priced ~-190) that bar is
    # ten points BELOW water, so away_covers_as_dog at 65.3% on n=98 earned
    # VALIDATED and weight 1.00 while returning ROI -0.25%. It then became
    # the loudest voice in the NHL engine. When the real breakeven is known
    # from the prices the signal fired at, it is the bar.
    se1 = 0.5 / (n ** 0.5)
    se2 = 2.0 * se1
    if blind_frac is None:
        if be_frac is None:
            if n >= 25 and hit_rate_frac <= 0.47:
                return 'ANTI_VALIDATED'
            if n >= 50 and hit_rate_frac >= 0.55:
                return 'VALIDATED'
            if n >= 20 and hit_rate_frac >= 0.55:
                return 'DISCOVERY'
            return 'UNVALIDATED'
        edge = hit_rate_frac - be_frac
        if n >= 25 and edge < -se2:
            return 'ANTI_VALIDATED'
        if n >= 50 and edge > se2:
            return 'VALIDATED'
        if n >= 20 and edge > se1:
            return 'DISCOVERY'
        return 'UNVALIDATED'

    edge = hit_rate_frac - blind_frac
    if n >= 25 and edge < -se2:
        return 'ANTI_VALIDATED'
    # Must clear the juice as well as the side — beating a 48% side at 50%
    # still loses money. The bar is the breakeven of the prices this signal
    # actually fired at, not a flat -110: 0.5238 is right for rl and total
    # but ~6 points light on a moneyline and ~13 light on an NHL dog
    # puckline, which is the whole of the favourite-bias mechanism.
    _juice = be_frac if be_frac is not None else 0.5238
    if hit_rate_frac < _juice:
        return 'UNVALIDATED'
    # The bar matches the strength of the claim the label makes.
    # VALIDATED says "proven", so it pays the full 2SE. DISCOVERY says
    # "promising, thin sample" — 1SE, explicitly provisional.
    #
    # A single 2SE bar for both sounded rigorous and was useless: at
    # n=60, 2SE is ±12.9pp, so a real 11pp edge still failed and NOTHING
    # could ever promote. A ladder that can only demote is not a ladder,
    # and the thin-sample tier exists precisely to carry claims this
    # sample size cannot settle.
    if n >= 50 and edge > se2:
        return 'VALIDATED'
    if n >= 20 and edge > se1:
        return 'DISCOVERY'
    return 'UNVALIDATED'


# ══ 2026-10-27 · F6 · BREAKEVEN IS NOT 52.38% ON EVERY MARKET ════════════
# This was a single hardcoded 0.5238 (-110 juice) applied to every market,
# so a moneyline signal was credited with ~6 points of edge it does not
# have and its weight came out roughly 4x too high.
#
# MEASURED from stored prices on graded receipts since 2026-09-19, using the
# MEDIAN payout -- the mean is the wrong statistic here, because American
# odds are skewed and a few longshots drag it (that error is what made NHL
# overs read as CLEARING at BE 7.0% while their ROI was -20.00%):
#
#   market         n   med payout   BREAKEVEN
#   prop        3852        0.893      52.83%
#   ml           541        0.714      58.33%   <- 5.95 pts above -110
#   rl           355        0.909      52.38%
#   total        171        0.909      52.38%
#   per sport:  MLB ml 57.81%   NHL ml 57.45%   NCAAF ml 58.68%
#
# So rl and total were always right; ml was the one being inflated.
# 2026-10-27 · the spread column is NOT named the same in every sport.
# nhl_game_results has NO `close_spread` -- its spread is `close_puckline`.
# A select naming close_spread 400s and _page returns [], which printed
# "game rows joined ctx->results: 0 of 0 results" and read as "NHL has no
# settled games" rather than a wiring error. The grader had the identical
# bug; this is the same fix in the second file.
_SPREAD_COL = {'NHL': 'close_puckline'}


BREAKEVEN_BY_MARKET = {
    'ml': 0.5833,
    'moneyline': 0.5833,
    'prop': 0.5283,
    'rl': 0.5238,
    'spread': 0.5238,
    'total': 0.5238,
}
BREAKEVEN_DEFAULT = 0.5238          # -110, the right answer for rl/total


def breakeven_for(market: str) -> float:
    return BREAKEVEN_BY_MARKET.get(
        str(market or '').strip().lower(), BREAKEVEN_DEFAULT)


def _recommended_weight_for(n: int, hit_rate_frac: float, tier: str,
                            market: str = '',
                            blind_frac: Optional[float] = None) -> float:
    """Coarse mapping to signal_registry.recommended_weight; ensemble
    scorer's edge_weight() is the runtime authority — this is a hint.

    `market` picks the breakeven. Omitting it keeps the -110 default, which
    is correct for rl/total and conservative-high for prop.

    ══ 2026-10-28 · THE BAR MUST BE THE SIDE, NOT JUST THE PRICE ══
    This measured edge against the PRICE breakeven only, so a signal that
    points at the home/favourite side was paid for beating 52.4% while a
    blind bet on that same side already returns 53.5-57.6% by sport.
    Observed: nba_elo_home_ml_strong — the chip behind the NBA favourite
    lock — was correctly demoted to UNVALIDATED by _tier_for's control and
    STILL carried weight 0.3215 from here, because the two functions used
    different bars. Half a fix is the dangerous state: the tier says
    "unproven" and the weight says "use me".

    Same shape as edge_weight: a signal must clear the harder of the two
    bars. `blind_frac` is None wherever nothing is measured (NHL puckline,
    prop paths), and then this is byte-identical to before.
    """
    if tier == 'ANTI_VALIDATED':
        return 0.0
    _bar = breakeven_for(market)
    if blind_frac is not None:
        _bar = max(_bar, blind_frac)
    edge = max(0.0, hit_rate_frac - _bar)
    edge_c = min(edge / 0.12, 1.0)
    # ══ 2026-10-27 · LINEAR TO 100, NOT LOGARITHMIC ══════════════════════
    # Was min(log1p(n)/log(101), 1.0), which is far too generous at low n:
    # it hands 0.520 of full weight to a 10-firing signal and 0.706 to a
    # 25-firing one. That is how a 64%-on-n=25 candidate came out at 0.684,
    # nearly the weight of a VALIDATED signal with 100+ firings.
    #
    # Linear is identical at n>=100 and strictly stricter below it:
    #     n      log    linear
    #    10    0.520     0.100
    #    25    0.706     0.250
    #    50    0.852     0.500
    #   100    1.000     1.000
    #
    # Measured across all 976 evidenced registry rows it ZEROES NOTHING NEW
    # (485 rows at weight 0 before and after, so no signal is lost) and only
    # removes over-confidence: rows above 0.5 fall 176 -> 56, mean weight
    # 0.207 -> 0.105. Well-evidenced signals are untouched --
    # sp_plus_edge_home (n=106) and prop:slow_start (n=159) do not move at
    # all; away_covers_as_dog (n=99) moves 0.008.
    #
    # This is what lets a brand-new signal be USED immediately at a weight
    # that reflects its evidence, instead of either over-trusted or
    # excluded until it clears a floor.
    n_c = min(max(int(n or 0), 0) / 100.0, 1.0)
    return round(edge_c * n_c, 4)


def collect_prop_firings(sport: str, days: int) -> dict[str, dict]:
    """Return {signal_key: {right, wrong}} from prop_playbook_decisions.

    == 2026-10-29 · IT WAS MEASURING A TRUNCATED, UNORDERED SUBSET ==
    `Range: 0-9999` does NOT lift the cap: PostgREST enforces its own
    db-max-rows and returns 1000. The run of 10-29 printed exactly

        [MLB] prop decisions graded (last 90d): 1000
        [NBA] prop decisions graded (last 90d): 1000
        [NHL] prop decisions graded (last 90d): 1000

    — three sports landing on precisely the cap, which is the signature,
    not a coincidence. And there was no `order=`, so which 1000 arrived was
    arbitrary and could differ between runs. Every prop signal's hit rate
    was therefore computed on a random slice of its history
    (feedback_never_measure_from_truncated_output,
    feedback_unordered_data_zero_picks_a_vintage, and the same family as
    the prop scorer seeing 22% of an NHL board).

    Now paged with an explicit order, so the denominator is the whole
    window and is reproducible.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows: list = []
    off = 0
    while True:
        r = requests.get(f'{SB}/rest/v1/prop_playbook_decisions',
                         headers=H_READ,
                         params={'sport': f'eq.{sport}',
                                 'game_date': f'gte.{cutoff}',
                                 'result': 'in.(W,L,Win,Loss)',
                                 'select': 'result,playbook_side,direction,'
                                           'playbook_sources',
                                 'order': 'id.asc',
                                 'limit': '1000', 'offset': str(off)},
                         timeout=60)
        if r.status_code not in (200, 206):
            print(f'  ! prop query failed {r.status_code}: {r.text[:200]}')
            return {}
        batch = r.json() or []
        if not isinstance(batch, list):
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        off += 1000
        if off > 200000:                       # hard stop, never silent
            print(f'  ! [{sport}] prop paging exceeded 200k rows — stopping')
            break
    print(f'  [{sport}] prop decisions graded (last {days}d): {len(rows)}')

    # ══ 2026-10-29 · GIVE THE PROP PATH A DIRECTION CONTROL ══════════════
    # Until today this path returned right/wrong only, so blind_n stayed 0
    # and _tier_for fell back to the FLAT >=55% rule for every prop signal.
    # 48 MLB prop signals were carrying w>0 under it, several at w=1.0.
    #
    # The blind rate is per SPORT and it is not near 50%: a prop signal
    # voting OVER has to beat 48.42% in MLB but 57.07% in NHL. The flat rule
    # scores those identically, which is how an inflated prop record is
    # manufactured.
    try:
        from ensemble_scorer import prop_side_baseline as _psb
    except Exception:                                      # noqa: BLE001
        _psb = lambda *_a, **_k: None                       # noqa: E731
    # == 2026-10-29 . THE PROP PATH WAS MEASURING OUR PICK ==================
    # `playbook_sources` stores the contributions that DROVE the decision,
    # so a source almost always votes the side the decision took:
    #     MLB 93.96% of 18,514 firings agreed with their own pick
    #     NHL 67.23% of 1,007        NBA 62.27% of 2,693
    # With `correct = (agreed == won)` that makes a signal's "hit rate"
    # ~= the pick's hit rate. Measured on the same rows, signal-blind:
    #     pitcher_vs_team_recent_hit_hard  65.07%  vs pick control 63.76%
    #     pitcher_vs_team_owned            70.25%  vs           69.42%
    #     nhl_prop_b2b_fade                63.31%  vs           63.91%
    # - inside ~1pp every time. Those are the rows that were about to earn
    # w=1.00. Same defect as the game path
    # (project_signal_hit_rate_was_our_pick_rate_1028), never fixed here.
    #
    # pick_* is the signal-blind control: did OUR BET win on the rows this
    # signal fired on. dis_n is how often the signal voted AGAINST the
    # decision - the only non-circular evidence it carries. Both are
    # carried to build_rows, which refuses VALIDATED without them.
    firings: dict[str, dict] = defaultdict(
        lambda: {'right': 0, 'wrong': 0, 'blind_sum': 0.0, 'blind_n': 0,
                 'pick_right': 0, 'pick_n': 0, 'dis_n': 0, 'dis_right': 0})
    for row in rows:
        result = str(row.get('result', '')).upper()
        pb_side = str(row.get('playbook_side', '')).upper()
        # ══ 2026-10-29 · `result` GRADES THE LISTED SIDE, NOT OUR BET ════
        # prop_playbook_decisions.result is computed from actual_value vs
        # prop_line against the prop's OWN direction (verified: 32,508 rows,
        # 0 disagreements with a recomputation from actual_value). On a FADE
        # we back the OPPOSITE direction, so result='W' means our bet LOST.
        #
        # This function already knew that — pb_eff below flips the side for
        # the baseline — but `won` was read raw, so every signal's
        # right/wrong was INVERTED on every FADE row:
        #     NHL 503 of 2,610 graded (19.3%)
        #     NBA 818 of 4,540 (18.0%)
        #     MLB 2,144 of 25,321 (8.5%)
        # Same family as feedback_grade_the_side_we_published, and it is why
        # the FADE cell read +37.6pp "edge" on NHL — the stored price is on
        # the backed side while the stored result was on the listed one.
        won_listed = result in ('W', 'WIN')
        won = (not won_listed) if pb_side == 'FADE' else won_listed
        # The decision's EFFECTIVE side: a FADE backs the opposite of the
        # prop's own direction (feedback_grade_the_side_we_published).
        _dir = str(row.get('direction', '')).strip().lower()
        if _dir in ('over', 'under') and pb_side in ('BACK', 'FADE'):
            _flip = (pb_side == 'FADE')
            pb_eff = ('UNDER' if _flip else 'OVER') if _dir == 'over' else \
                     ('OVER' if _flip else 'UNDER')
        else:
            pb_eff = None
        srcs = row.get('playbook_sources') or []
        if isinstance(srcs, str):
            try:
                import json as _j; srcs = _j.loads(srcs)
            except Exception:
                srcs = []
        for s in srcs:
            if not isinstance(s, dict): continue
            key = s.get('signal_key')
            if not key: continue
            # Strip __fade suffix so fade opinions count under parent
            base_key = str(key).replace('__fade', '')
            src_side = str(s.get('side', '')).upper()
            if src_side not in ('BACK', 'FADE'):
                continue
            # == 2026-10-29 . IT GRADED THE SIGNAL AGAINST THE PICK, AND
            #                 THE PICK WAS USUALLY `PASS` ================
            # The old rule was `correct = (src_side == pb_side) == won`:
            # right for a BACK/FADE decision, but PASS is a decision value
            # too, and PASS is nearly all of the table --
            #     MLB 19,501 of 25,321 graded (77.0%)
            #     NHL  1,870 of  2,610 (71.6%)
            #     NBA  2,954 of  4,540 (65.1%)
            # On a PASS row `src_side == pb_side` is False for EVERY source,
            # so a signal that voted BACK on a prop whose listed direction
            # WON was scored WRONG -- inverted on three quarters of the
            # evidence. FADE voters came out right by accident, which is
            # why the two families looked so different.
            #
            # A signal's record does not depend on what we did with it. It
            # advocates a side; either that side won or it did not:
            #     BACK -> the prop's listed direction
            #     FADE -> the opposite direction
            # Graded that way the pick drops out of the metric entirely,
            # which removes the circularity the pick control was added to
            # bound, and lets PASS rows carry their real information.
            sig_backs_listed = (src_side == 'BACK')
            correct = won_listed if sig_backs_listed else (not won_listed)
            if correct: firings[base_key]['right'] += 1
            else:       firings[base_key]['wrong'] += 1
            # The side this signal actually advocated, for its baseline.
            if _dir in ('over', 'under'):
                sig_eff = (_dir.upper() if sig_backs_listed
                           else ('UNDER' if _dir == 'over' else 'OVER'))
                _b = _psb(sport, sig_eff)
                if _b is not None:
                    firings[base_key]['blind_sum'] += _b
                    firings[base_key]['blind_n'] += 1
            # Does the signal add anything OVER THE PICK? Only meaningful
            # where a pick exists, so PASS rows are excluded here even
            # though they count above. `won` is our bet's outcome.
            if pb_side in ('BACK', 'FADE'):
                firings[base_key]['pick_n'] += 1
                if won: firings[base_key]['pick_right'] += 1
                if src_side != pb_side:
                    firings[base_key]['dis_n'] += 1
                    if not won: firings[base_key]['dis_right'] += 1
    return dict(firings)


def _game_market_result(market: str, ctx_row: dict) -> Optional[str]:
    """Return the winning candidate for a market on a completed game.

    market: 'ml' | 'rl' | 'total'
    Returns one of HOME_ML/AWAY_ML/HOME_RL/AWAY_RL/OVER/UNDER or None if
    push / cannot resolve.
    """
    hs, as_ = ctx_row.get('home_score'), ctx_row.get('away_score')
    if hs is None or as_ is None: return None
    if market == 'ml':
        if hs > as_: return 'HOME_ML'
        if as_ > hs: return 'AWAY_ML'
        return None
    if market == 'rl':
        cs = ctx_row.get('close_spread')
        if cs is None: return None
        try: cs = float(cs)
        except (TypeError, ValueError): return None
        margin = hs - as_
        if margin + cs > 0.0001:  return 'HOME_RL'
        if margin + cs < -0.0001: return 'AWAY_RL'
        return None
    if market == 'total':
        ct = ctx_row.get('close_total')
        if ct is None: return None
        try: ct = float(ct)
        except (TypeError, ValueError): return None
        total = hs + as_
        if total > ct + 0.0001: return 'OVER'
        if total < ct - 0.0001: return 'UNDER'
        return None
    return None


def _market_of_side(side: str) -> Optional[str]:
    """HOME_ML → ml; HOME_RL → rl; OVER/UNDER → total."""
    side = str(side).upper()
    if side in ('HOME_ML', 'AWAY_ML'): return 'ml'
    if side in ('HOME_RL', 'AWAY_RL'): return 'rl'
    if side in ('OVER', 'UNDER'):      return 'total'
    return None


def collect_game_firings(sport: str, days: int) -> dict[str, dict]:
    """Walk primary_play._ensemble_sources on completed games, grade each
    source's side against the actual game outcome. Same right/wrong tally.

    Reads from the results table (mlb_game_results etc.) which carries the
    primary_play snapshot at grade time PLUS home_score/away_score. This is
    the historical audit trail — ctx tables get overwritten on recompute
    so they can't be trusted for backward-looking analysis."""
    tables = SPORTS_WITH_CTX.get(sport)
    if not tables: return {}
    ctx_table, results_table = tables
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    # ══ 2026-10-11 · primary_play IS NOT ON THE RESULTS TABLE ══
    # This selected primary_play FROM THE RESULTS TABLE. It lives on the
    # CONTEXT table. PostgREST 400s on the missing column, the handler
    # below caught it, and printed "game results schema lacks
    # primary_play — game grading skipped", which reads like a known
    # limitation of the sport. It is not: the data exists, one table over.
    # `_ctx_table` was unpacked and never used — the underscore was the
    # tell.
    #
    # Consequence: the game-ensemble grading path has been dead for every
    # sport whose results table lacks the column, which is all of
    # NFL/NCAAF/NBA/NHL. MLB "worked" only because mlb_game_results
    # happens to carry primary_play. That is why 67 NCAAF ensemble signals
    # and 123 NFL ones all sit at sample_n=0, and why _resolve_weight
    # returns (None, 0, None) for every one of them so they fall back to
    # RAMP_UP_PRIOR — a signal that loses weighted the same as one that
    # wins.
    #
    # Joined on (game_date, home_team, away_team) rather than game_id,
    # because NFL context and results use different id schemes
    # ([[project_nfl_game_id_mismatch_911]]).
    def _page(table, params):
        out, off = [], 0
        while True:
            p = dict(params); p['limit'] = 1000; p['offset'] = off
            rr = requests.get(f'{SB}/rest/v1/{table}', headers=H_READ,
                              params=p, timeout=60)
            if rr.status_code not in (200, 206):
                print(f'  ! {table} {rr.status_code}: {rr.text[:160]}')
                return out
            b = rr.json() or []
            out += b
            if len(b) < 1000: return out
            off += 1000

    # PREFER primary_play ON THE RESULTS TABLE where it exists (MLB).
    # That column is the historical SNAPSHOT of what was published; the
    # context table is overwritten on every recompute, so it cannot be
    # trusted looking backwards (see this function's own docstring).
    # Joining context unconditionally cost MLB 254 game rows -> 25 and
    # swapped a frozen audit trail for a mutable one. Only fall back to
    # the join for sports whose results table lacks the column.
    direct = requests.get(
        f'{SB}/rest/v1/{results_table}',
        headers={**H_READ, 'Range-Unit': 'items', 'Range': '0-4999'},
        params={'game_date': f'gte.{cutoff}', 'home_score': 'not.is.null',
                'select': 'game_id,home_score,away_score,close_spread,'
                          'close_total,primary_play'},
        timeout=30)
    if direct.status_code in (200, 206):
        rows = direct.json() or []
        print(f'  [{sport}] game rows from results.primary_play '
              f'(last {days}d): {len(rows)}')
        return _tally_game_firings(rows, sport)

    _sc = _SPREAD_COL.get(sport, 'close_spread')
    res_rows = _page(results_table, {
        'game_date': f'gte.{cutoff}', 'home_score': 'not.is.null',
        # alias the sport's own spread column to close_spread so the
        # grading below reads one name
        'select': 'game_date,home_team,away_team,home_score,away_score,'
                  f'close_spread:{_sc},close_total'})
    if not res_rows:
        print(f'  [{sport}] WARNING: results fetch returned 0 rows from '
              f'{results_table} (spread col {_sc!r}) — if that table has '
              f'settled games this is a WIRING ERROR, not an empty slate')
    ctx_rows = _page(ctx_table, {
        'game_date': f'gte.{cutoff}',
        'select': 'game_date,home_team,away_team,primary_play'})
    pp_by_key = {}
    for c in ctx_rows:
        pp = c.get('primary_play')
        if isinstance(pp, str):
            try:
                import json as _json
                pp = _json.loads(pp)
            except Exception:
                pp = None
        if isinstance(pp, dict):
            pp_by_key[(str(c.get('game_date'))[:10],
                       str(c.get('home_team')).lower(),
                       str(c.get('away_team')).lower())] = pp
    rows = []
    for r0 in res_rows:
        k = (str(r0.get('game_date'))[:10],
             str(r0.get('home_team')).lower(),
             str(r0.get('away_team')).lower())
        pp = pp_by_key.get(k)
        if pp is None:
            continue
        rows.append({**r0, 'primary_play': pp})
    print(f'  [{sport}] game rows joined ctx->results (last {days}d): '
          f'{len(rows)} of {len(res_rows)} results, '
          f'{len(ctx_rows)} context rows')

    return _tally_game_firings(rows, sport)


# ══ 2026-10-29 · THE JUICE BAR WAS A FLAT 52.38% ON EVERY MARKET ════════
# _tier_for checked `hit_rate_frac < 0.5238` as its "must clear the juice"
# floor. That number is -110. It is not the breakeven of most things we bet:
#
#   NHL dog puckline      prices ~-190  ->  breakeven 65.8%
#   moneylines (median)                 ->  breakeven 58.33%
#   rl / total (median)                 ->  breakeven 52.38%
#
# So on the NHL puckline a signal hitting 65.3% cleared a 52.38% bar by 13
# points while LOSING money, and `side_baseline(NHL,'rl')` is None, so it
# also took the no-baseline branch whose bar is a flat >=55%. That is how
# away_covers_as_dog -- 65.3% on n=98 against a 65.8% breakeven, edge
# -0.54pp, ROI -0.25% -- was stamped VALIDATED with weight 1.00 and became
# the engine's single loudest voice. The favourite-side ML signals in every
# sport where favourites win 60%+ are promoted by the same mechanism.
#
# The fix is to use the breakeven of the PRICES THE SIGNAL ACTUALLY FIRED
# AT, averaged over its own firings, and to fall back to the measured
# per-market median only when no price is stored.
_MARKET_BE_FALLBACK = {'ml': 0.5833, 'rl': 0.5238, 'total': 0.5238,
                       'prop': 0.5283}

# Only NHL stores a spread/total price; MLB stores no price at all.
_PRICE_COLS = {
    'ml':    ('close_home_ml', 'close_away_ml'),
    'rl':    ('close_puckline_home_price', 'close_puckline_away_price'),
    'total': ('close_total_over_price', 'close_total_under_price'),
}


def _american_to_be(odds) -> Optional[float]:
    """Breakeven win rate implied by American odds, as a fraction."""
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def _firing_breakeven(market: str, src_side: str, row: dict) -> Optional[float]:
    """Breakeven of the side this signal actually took on this game."""
    cols = _PRICE_COLS.get(market)
    if not cols:
        return None
    first_side = {'ml': 'HOME', 'rl': 'HOME', 'total': 'OVER'}[market]
    col = cols[0] if src_side.startswith(first_side) else cols[1]
    return _american_to_be(row.get(col))


def _tally_game_firings(rows: list, sport: Optional[str] = None) -> dict:
    """{signal_key: {right, wrong, blind_sum}} from rows with primary_play.

    Shared by both fetch paths (results.primary_play for MLB, the
    ctx->results join for football) so the two cannot grade differently.

    `blind_sum` accumulates what a BLIND bet on the same side would have
    been expected to return on each firing, so _tier_for can judge the
    signal against its own side rather than a flat threshold. Stays 0 when
    the sport has no measured side rate, and the caller then falls back to
    the flat rule rather than dividing by a baseline it does not have.
    """
    try:
        from ensemble_scorer import side_baseline
    except Exception:                                      # noqa: BLE001
        side_baseline = lambda *_a, **_k: None             # noqa: E731
    firings: dict[str, dict] = defaultdict(
        lambda: {'right': 0, 'wrong': 0, 'blind_sum': 0.0, 'blind_n': 0,
                 'be_sum': 0.0, 'be_n': 0, 'mkt': Counter()})
    for row in rows:
        pp = row.get('primary_play') or {}
        if isinstance(pp, str):
            try:
                import json as _json
                pp = _json.loads(pp)
            except Exception:
                continue
        if not isinstance(pp, dict): continue
        # Prefer the exhaustive per-market sources if present; fall back to
        # winning-side-only sources.
        srcs = pp.get('_ensemble_sources') or []
        if not isinstance(srcs, list): continue
        # Cache per-market outcomes so we don't recompute for every source.
        market_winner_cache: dict[str, Optional[str]] = {}
        for s in srcs:
            if not isinstance(s, dict): continue
            key = s.get('signal_key')
            if not key: continue
            base_key = str(key).replace('__fade', '')
            src_side = str(s.get('side', '')).upper()
            market = _market_of_side(src_side)
            if market is None: continue
            if market not in market_winner_cache:
                market_winner_cache[market] = _game_market_result(market, row)
            winner = market_winner_cache[market]
            if winner is None:
                # Push / unresolved — skip for this signal on this game.
                continue
            correct = (src_side == winner)
            if correct: firings[base_key]['right'] += 1
            else:       firings[base_key]['wrong'] += 1
            _b = side_baseline(sport, market, src_side)
            if _b is not None:
                firings[base_key]['blind_sum'] += _b
                firings[base_key]['blind_n'] += 1
            # The breakeven of the price this signal took, per firing.
            _be = _firing_breakeven(market, src_side, row)
            if _be is not None:
                firings[base_key]['be_sum'] += _be
                firings[base_key]['be_n'] += 1
            firings[base_key]['mkt'][market] += 1
    return dict(firings)


def collect_attribution_firings(sport: str, days: int) -> dict[str, dict]:
    """Return {signal_key: {right, wrong}} from signal_attribution table.

    2026-09-13: closes the loop for signals surfaced on the game card
    SignalsRow (LR_SHADOW, GOAT, ANCHOR, EPA_GAP, COHORT_*, DIV_GAME, etc.)
    that don't necessarily vote in _ensemble_sources. Grading semantics:
      kind='ok'   → signal agrees with pick; W = right, L = wrong
      kind='warn' → signal disagrees; W = wrong (fade voice was correct
                    to distrust the pick? no — pick WON so warn was wrong),
                    L = right (pick lost, warn voice was correct)
      kind='neutral' → informational, excluded from tally
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    # Paginate — attribution rows can exceed the 1000 PostgREST cap
    # over long windows on a busy sport.
    all_rows: list = []
    for page in range(20):
        r = requests.get(f'{SB}/rest/v1/signal_attribution',
                         headers={**H_READ, 'Range-Unit': 'items',
                                  'Range': f'{page*1000}-{(page+1)*1000-1}'},
                         params={'sport': f'eq.{sport}',
                                 'game_date': f'gte.{cutoff}',
                                 'result': 'in.(W,L)',
                                 'kind': 'in.(ok,warn)',
                                 # signal_side + the outcome numbers, so the
                                 # tally can grade the side the signal named
                                 # rather than inheriting our pick's result.
                                 'select': 'signal_key,kind,result,sport,'
                                           'pick_market,pick_side,signal_side,'
                                           'actual_margin,actual_total,'
                                           'close_line'},
                         timeout=30)
        if r.status_code not in (200, 206): break
        page_rows = r.json() or []
        if not isinstance(page_rows, list): break
        all_rows.extend(page_rows)
        if len(page_rows) < 1000: break
    print(f'  [{sport}] signal_attribution graded rows (last {days}d): {len(all_rows)}')

    firings: dict[str, dict] = defaultdict(
        lambda: {'right': 0, 'wrong': 0, 'mkt': Counter(),
                 'blind_sum': 0.0, 'blind_n': 0})
    # == 2026-10-28 . THE CONTROL WAS MISSING ON EXACTLY THIS PATH ========
    # _tier_for already judges a signal against a blind bet on its own side,
    # and falls back to a FLAT rule (>=55% earns VALIDATED) when blind_frac
    # is None. blind_sum was accumulated only on the primary_play path, so
    # every signal measured through ATTRIBUTION got the flat rule -- and the
    # flat rule is where the favourite bias is manufactured, because a blind
    # bet on the home side already returns 53.5-57.6% by sport.
    #
    # Observed before this fix: nba_elo_home_ml_strong, the chip behind the
    # NBA favourite lock, came out DISCOVERY w=0.3215 on hr=60.42% n=48.
    # Against NBA's measured home-ML baseline of 0.550 its edge is +5.4pp
    # against a 1SE bar of 7.2pp -- it should not promote at all.
    #
    # So the de-biasing is not a new rule, it is supplying the control to the
    # path that had none (feedback_guard_belongs_at_the_chokepoint, again).
    # side_baseline returns None where nothing is measured (NHL puckline),
    # and the coverage gate below still refuses a partial baseline.
    try:
        from ensemble_scorer import side_baseline as _sb
    except Exception:                                          # noqa: BLE001
        _sb = lambda *_a, **_k: None                            # noqa: E731
    # ══ 2026-10-28 · kind='ok' IS A PLACEHOLDER ON COHORT ROWS ══
    # The kind rule below measured OUR PICK's result on games where a signal
    # fired, for every row the snapshot writer stamps kind='ok' without
    # comparing sides. That is every COHORT_* row: the writer records
    # signal_side as the cohort's SUBJECT (HOME for any home-referenced tag)
    # and says so explicitly --
    #
    #   "Recording the subject is deliberately NOT the same as recording a
    #    recommendation: whether to follow or fade the subject is then a
    #    calibration OUTPUT instead of an assumption baked into the writer."
    #
    # -- and it was fixed on 10-24 to record that side. This consumer was
    # never updated to use it, so the calibration output the writer set up
    # was still being replaced by a measurement of our own selection. The
    # exact thing its comment warns about, one file downstream.
    #
    # Measured difference on n>=25 signals (kind rule -> side-graded):
    #   NCAAF COHORT_NCAAF_HEAVY_HOME_FAV   61.7% -> 50.9%  n=110
    #   NCAAF COHORT_NCAAF_HEAVY_HOME_DOG   43.8% -> 60.0%  n= 70
    #   NCAAF LR_SHADOW                     57.1% -> 51.4%  n=422
    #   NFL   COHORT_NFL_HOME_FAV           48.6% -> 40.3%  n= 67
    # HEAVY_HOME_FAV carries weight 0.78 as VALIDATED on the 61.7%; its own
    # subject covered 50.9%. HEAVY_HOME_DOG sits at 0.0 ANTI_VALIDATED on the
    # 43.8%; its subject covered 60.0%.
    #
    # signal_outcome.signal_was_right grades signal_side against the stored
    # margin and close line, with the per-sport sign convention, and returns
    # None rather than a guess for a row it cannot settle -- a NEUTRAL
    # subject, a side signal on a total pick, a push, or a total market (the
    # closing total is not stored on these rows at all: close_line is the
    # SPREAD even on total rows). It falls back to the kind rule only when
    # the numbers are missing, so nothing countable before stops counting.
    from signal_outcome import signal_was_right as _swr
    _skipped = 0
    for row in all_rows:
        key = row.get('signal_key')
        if not key: continue
        # 2026-10-27 F6: the market decides the breakeven, so tally which
        # market this signal actually fires on.
        _m = str(row.get('pick_market') or '').strip().lower()
        if _m:
            firings[key]['mkt'][_m] += 1
        verdict = _swr(row, sport)
        if verdict is None:
            _skipped += 1
            continue
        if verdict: firings[key]['right'] += 1
        else:       firings[key]['wrong'] += 1
        # the blind rate for the side THIS signal named, same row
        _bl = _sb(sport, _m, row.get('signal_side'))
        if _bl is not None:
            firings[key]['blind_sum'] += _bl
            firings[key]['blind_n'] += 1
    if _skipped:
        print(f'  [{sport}] {_skipped} attribution row(s) not countable '
              f'(neutral subject / cross-axis / push) — skipped, not defaulted')
    return dict(firings)


def merge_firings(*dicts) -> dict[str, dict]:
    """Combine multiple {signal_key → {right,wrong,…}} dicts, summing counts.

    2026-10-11: blind_sum / blind_n must be carried through. They were
    dropped here, so the side-aware tiering added to _tier_for never saw a
    baseline and silently fell back to the flat rule — the tier
    distribution came out byte-identical, which is what made it obvious.
    A merge that quietly discards the keys its callers just started
    producing is the same silent-no-op class as the rest of today.
    """
    out: dict[str, dict] = defaultdict(
        lambda: {'right': 0, 'wrong': 0, 'blind_sum': 0.0, 'blind_n': 0,
                 'pick_right': 0, 'pick_n': 0, 'dis_n': 0, 'dis_right': 0,
                 'be_sum': 0.0, 'be_n': 0, 'mkt': Counter()})
    for d in dicts:
        for k, v in d.items():
            out[k]['right'] += v.get('right', 0)
            out[k]['wrong'] += v.get('wrong', 0)
            out[k]['blind_sum'] += v.get('blind_sum', 0.0)
            out[k]['blind_n'] += v.get('blind_n', 0)
            # 2026-10-29: the pick control, per this docstring's own
            # warning about dropping keys the callers just started making.
            for _k in ('pick_right', 'pick_n', 'dis_n', 'dis_right', 'be_n'):
                out[k][_k] += v.get(_k, 0)
            out[k]['be_sum'] += v.get('be_sum', 0.0)
            # 2026-10-27 F6: carry the market tally, per this docstring's
            # own warning about silently dropping new keys.
            _m = v.get('mkt')
            if _m:
                out[k]['mkt'].update(_m)
    return dict(out)


def upsert_registry(rows: list[dict], dry_run: bool = False) -> int:
    """PATCH existing rows by (signal_name, sport), POST new. Two-step
    because signal_registry lacks a (signal_name, sport) unique constraint —
    can't use PostgREST's on_conflict. Not ideal (2 round-trips per row)
    but the row count is tiny (dozens, not thousands) so latency is fine."""
    if dry_run or not rows:
        print(f'  [dry] would upsert {len(rows)} rows to signal_registry')
        return len(rows)

    # Pull existing rows to build (signal_name, sport) → id map
    sports = sorted({r['sport'] for r in rows})
    existing_ids: dict[tuple[str, str], int] = {}
    for sp in sports:
        rr = requests.get(f'{SB}/rest/v1/signal_registry', headers=H_READ,
                          params={'sport': f'eq.{sp}',
                                  'select': 'id,signal_name,sport'}, timeout=15)
        for row in (rr.json() or []):
            existing_ids[(row['signal_name'], row['sport'])] = row['id']

    total = 0
    for row in rows:
        # 2026-10-29: build_rows carries audit-only fields prefixed with
        # '_' (pick control, discrimination). They are NOT columns, and
        # PostgREST 400s on an unknown key, so strip them here rather than
        # relying on every caller to remember.
        row = {k: v for k, v in row.items() if not k.startswith('_')}
        key = (row['signal_name'], row['sport'])
        if key in existing_ids:
            rid = existing_ids[key]
            payload = {k: v for k, v in row.items() if k not in ('signal_name', 'sport')}
            resp = requests.patch(f'{SB}/rest/v1/signal_registry?id=eq.{rid}',
                                  headers=H_WRITE, json=payload, timeout=15)
            if resp.status_code in (200, 204): total += 1
            else: print(f'  ! patch {row["signal_name"]} failed {resp.status_code}: {resp.text[:150]}')
        else:
            resp = requests.post(f'{SB}/rest/v1/signal_registry',
                                 headers=H_WRITE, json=[row], timeout=15)
            if resp.status_code in (201, 204): total += 1
            else: print(f'  ! insert {row["signal_name"]} failed {resp.status_code}: {resp.text[:150]}')
    return total


_PICK_COLS = ('pick_control_pct', 'discrimination_pp', 'disagree_n')
_PICK_COLS_PRESENT: Optional[bool] = None


def _have_pick_control_cols() -> bool:
    """True once 20261029c has been applied. Probed once per process.

    A select on a missing column returns 42703 with a 400 and NO ROWS, so
    guessing is the one thing not to do here
    (feedback_select_star_before_concluding_absent). On any doubt this
    returns False and the run simply prints the numbers as before -- a
    failed probe must not cost us the refit.
    """
    global _PICK_COLS_PRESENT
    if _PICK_COLS_PRESENT is None:
        try:
            r = requests.get(f'{SB}/rest/v1/signal_registry', headers=H_READ,
                             params={'select': ','.join(_PICK_COLS),
                                     'limit': '1'}, timeout=10)
            _PICK_COLS_PRESENT = (r.status_code == 200)
            if not _PICK_COLS_PRESENT:
                print('  [registry] pick-control columns absent '
                      '(20261029c not applied) - printing only')
        except Exception as e:                                 # noqa: BLE001
            print(f'  [registry] pick-control column probe failed: {e}')
            _PICK_COLS_PRESENT = False
    return _PICK_COLS_PRESENT


def build_rows(sport: str, firings: dict[str, dict], min_n: int) -> list[dict]:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    # 2026-09-09: category is NOT NULL on signal_registry. Existing rows
    # PATCH fine (already have category) but new INSERTs (external:*,
    # sharp_scenario_*, new refit-discovered signals) failed 23502 all
    # session. Infer category from signal_key prefix so INSERTS succeed.
    def _infer_category(key: str) -> str:
        k = str(key).lower()
        if k.startswith('external:'):        return 'external_pick'
        if k.startswith('sharp_'):           return 'split'
        if k.startswith('prop:') or 'prop_' in k[:6]: return 'prop'
        if k.startswith('mc_') or k.startswith('v4_') or k.startswith('v3_'): return 'model'
        if 'pitcher' in k or 'sp_' in k:     return 'pitcher'
        if 'bullpen' in k or 'bp_' in k:     return 'bullpen'
        if 'wrc' in k or 'ops' in k or 'platoon' in k or 'offense' in k: return 'offense'
        if 'confluence' in k or 'cohort' in k: return 'cohort'
        if 'refit' in k:                     return 'refit'
        if 'ats' in k or 'ml_' in k or 'rl_' in k: return 'team_form'
        if 'nrfi' in k or 'yrfi' in k:       return 'situational'
        return 'other'

    out = []
    dropped = 0
    for signal_key, ct in firings.items():
        n = ct['right'] + ct['wrong']
        if n < min_n:
            dropped += 1
            continue
        hr_frac = ct['right'] / n if n > 0 else 0.0
        # Judge against the side the signal actually backs where we have a
        # measured rate for it; otherwise the flat rule (see _tier_for).
        _bn = ct.get('blind_n') or 0
        blind = (ct['blind_sum'] / _bn) if _bn >= max(10, int(n * 0.8)) else None
        # 2026-10-29: the breakeven of the prices this signal fired at.
        # Requires the same 80% coverage as the side baseline -- a bar
        # computed from a fifth of the firings is not this signal's bar.
        # Falls back to the measured per-market median, which is still far
        # closer than a flat -110 on a moneyline.
        _en = ct.get('be_n') or 0
        if _en >= max(10, int(n * 0.8)):
            be_frac = ct['be_sum'] / _en
        else:
            _mk = ct.get('mkt') or {}
            _dommk = max(_mk.items(), key=lambda z: z[1])[0] if _mk else ''
            be_frac = _MARKET_BE_FALLBACK.get(_dommk)
        tier = _tier_for(n, hr_frac, blind, be_frac)
        # == 2026-10-29 . THE PICK CONTROL, AS A GUARD ======================
        # right/wrong above no longer references the pick at all, so the
        # raw hit rate is non-circular on its own. This stays as a guard
        # for one specific failure: a signal that, on the rows where it
        # disagreed with a pick we actually made, was reliably the worse
        # side. That is evidence no side-baseline can supply.
        #
        # Deliberately conservative - it can only DEMOTE, needs 30
        # disagreements, and needs a 10pp gap, because the disagreement
        # cell is small and a sharp rule on a small cell is how the 0.3
        # prior got justified. feedback_signal_needs_a_direction_control.
        _pick_n = ct.get('pick_n') or 0
        _dis_n = ct.get('dis_n') or 0
        pick_ctl = ((ct.get('pick_right') or 0) / _pick_n
                    if _pick_n >= 30 else None)
        disc_pp = None
        if pick_ctl is not None and _dis_n >= 20:
            _na = _pick_n - _dis_n
            _kd = _dis_n - (ct.get('dis_right') or 0)
            if _na >= 30:
                _ka = (ct.get('pick_right') or 0) - _kd
                disc_pp = round(100.0 * _ka / _na - 100.0 * _kd / _dis_n, 2)
        if disc_pp is not None and disc_pp <= -10.0 and _dis_n >= 30:
            if tier in ('VALIDATED', 'DISCOVERY'):
                tier = 'UNVALIDATED'
        # 2026-10-27 F6: weight against the breakeven of the market this
        # signal actually fires on, not a flat -110.
        _mkt_ct = ct.get('mkt') or {}
        _dom = (max(_mkt_ct.items(), key=lambda z: z[1])[0]
                if _mkt_ct else '')
        weight = _recommended_weight_for(n, hr_frac, tier, _dom, blind)
        out.append({
            'signal_name': signal_key,
            'category': _infer_category(signal_key),
            'sport': sport,
            'hit_rate': round(hr_frac * 100, 2),
            'sample_n': n,
            # Stripped before the upsert (see upsert_registry); carried
            # only so run() can print the audit table.
            '_pick_ctl': (round(pick_ctl * 100, 2)
                          if pick_ctl is not None else None),
            '_disc_pp': disc_pp,
            '_dis_n': _dis_n,
            **({'pick_control_pct': (round(pick_ctl * 100, 2)
                                     if pick_ctl is not None else None),
                'discrimination_pp': disc_pp,
                'disagree_n': _dis_n}
               if pick_ctl is not None and _have_pick_control_cols() else {}),
            'tier': tier,
            'recommended_weight': weight,
            'direction_hint': 'FADE' if tier == 'ANTI_VALIDATED' else 'FOLLOW',
            # ══ 2026-10-29 · THE CONTROL WAS COMPUTED AND THROWN AWAY ══
            # `blind` decides the tier and the weight above, and was then
            # not persisted: baseline_pct was NULL on all 1,282 registry
            # rows across every sport. So nothing downstream, and no audit,
            # could tell a signal that BEAT its own side from one that was
            # promoted by the flat >=55% fallback — which is where the
            # favourite bias is manufactured, because a blind home bet
            # already returns 53.5-57.6% by sport.
            #
            # Storing it makes the distinction visible: baseline_pct NULL
            # now MEANS "judged by the flat rule, no control available",
            # and edge_pp is the margin over the signal's own side.
            # A number that decides a weight has to be auditable
            # (feedback_signal_needs_a_direction_control).
            'baseline_pct': (round(blind * 100, 2)
                             if blind is not None else None),
            'edge_pp': (round((hr_frac - blind) * 100, 2)
                        if blind is not None else None),
            'origin': 'REFIT_SCRIPT',
            # NOT written to `notes`: 479 registry rows carry hand-written
            # annotations there and a PATCH would erase them. 20261029c adds
            # dedicated columns; _have_pick_control_cols() probes for them so
            # this starts persisting the moment the migration lands, with no
            # second deploy. Until then the numbers are printed only.
            'last_computed_at': now,
            'updated_at': now,
        })
    print(f'  [{sport}] {len(out)} signals meet min_n={min_n} threshold ({dropped} dropped)')
    return out


def run(sport: str, days: int, dry_run: bool = False, min_n: int = 20) -> None:
    print(f'\n=== refit_signal_registry · {sport} · last {days}d ===')
    prop = collect_prop_firings(sport, days)
    game = collect_game_firings(sport, days)
    attr = collect_attribution_firings(sport, days)
    merged = merge_firings(prop, game, attr)
    print(f'  [{sport}] unique signals with firings: {len(merged)} '
          f'(prop={len(prop)}, game={len(game)}, attr={len(attr)})')
    rows = build_rows(sport, merged, min_n)

    # Tier distribution summary before write
    from collections import Counter
    tier_dist = Counter(r['tier'] for r in rows)
    print(f'  [{sport}] tier dist: {dict(tier_dist)}')

    # Show a few examples
    if rows:
        print(f'  [{sport}] examples:')
        for r in sorted(rows, key=lambda x: -x['sample_n'])[:5]:
            print(f'    {r["signal_name"]:45s} tier={r["tier"]:14s} '
                  f'hr={r["hit_rate"]}% n={r["sample_n"]} w={r["recommended_weight"]}')

    # == 2026-10-29 . THE PICK CONTROL, PRINTED ==============================
    # A prop signal is a component of the decision it is graded on, so its
    # raw hit rate is mostly that decision's record. This table is the only
    # honest read: pick_ctl is the signal-blind win rate on the same rows,
    # disc is agree% - disagree%, and dis_n is how much evidence exists.
    _audit = [r for r in rows if r.get('_pick_ctl') is not None]
    if _audit:
        print(f'  [{sport}] PICK CONTROL (prop path) - '
              f'{len(_audit)} signals graded on decisions they helped make:')
        print(f'    {"signal":42s} {"hit":>7s} {"pick_ctl":>9s} '
              f'{"vs pick":>8s} {"disc":>8s} {"dis_n":>6s}  tier')
        for r in sorted(_audit, key=lambda x: -(x['_disc_pp']
                                                if x['_disc_pp'] is not None
                                                else -999)):
            d = ('untestable' if r['_disc_pp'] is None
                 else f'{r["_disc_pp"]:+.2f}')
            print(f'    {r["signal_name"][:42]:42s} {r["hit_rate"]:6.2f}% '
                  f'{r["_pick_ctl"]:8.2f}% '
                  f'{r["hit_rate"] - r["_pick_ctl"]:+7.2f}pp {d:>8s} '
                  f'{r["_dis_n"]:6d}  {r["tier"]}')
    written = upsert_registry(rows, dry_run=dry_run)
    # ══ 2026-10-28 · A DRY RUN THAT SAYS "wrote" ══
    # upsert_registry returns len(rows) in dry mode, so this line printed
    # "✓ wrote 10 registry rows" on a --dry-run. I read that today and had
    # to query the table to find out whether the weights had actually
    # changed. A message that cannot be told apart from the real thing is
    # worse than no message.
    if dry_run:
        print(f'  [{sport}] DRY RUN — computed {written} rows, WROTE NOTHING')
    else:
        print(f'  [{sport}] ✓ wrote {written} registry rows')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', help='specific sport (default: all)')
    p.add_argument('--days', type=int, default=90)
    # 2026-10-27: floor lowered 20 -> 10. At min_n=20 NFL weighted only 9
    # of 72 firing signals and NHL 3 of 28, so the other 63 and 25 fell back
    # to an UNMEASURED prior -- "a signal that loses weighted the same as one
    # that wins", per the note at the results-join fix above. The F4
    # shrinkage now bounds the risk that the old floor was standing in for:
    # log1p(n)/log(101) caps a 10-firing signal at 0.520 and a 15-firing one
    # at 0.601, both earned only on a full 12pp edge over their own
    # breakeven. A measured low weight beats an unmeasured 0.3 default.
    # Raising it back is one number.
    p.add_argument('--min-n', type=int, default=10,
                   help='minimum firings before writing a row (default 20)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    sports = [args.sport] if args.sport else list(SPORTS_WITH_CTX.keys())
    for s in sports:
        run(s, days=args.days, dry_run=args.dry_run, min_n=args.min_n)


if __name__ == '__main__':
    main()
