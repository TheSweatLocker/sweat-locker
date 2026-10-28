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
              blind_frac: Optional[float] = None) -> str:
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
    if blind_frac is None:
        if n >= 25 and hit_rate_frac <= 0.47:
            return 'ANTI_VALIDATED'
        if n >= 50 and hit_rate_frac >= 0.55:
            return 'VALIDATED'
        if n >= 20 and hit_rate_frac >= 0.55:
            return 'DISCOVERY'
        return 'UNVALIDATED'

    edge = hit_rate_frac - blind_frac
    se1 = 0.5 / (n ** 0.5)
    se2 = 2.0 * se1
    if n >= 25 and edge < -se2:
        return 'ANTI_VALIDATED'
    # Must clear the juice as well as the side — beating a 48% side at
    # 50% still loses money at -110.
    if hit_rate_frac < 0.5238:
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
                            market: str = '') -> float:
    """Coarse mapping to signal_registry.recommended_weight; ensemble
    scorer's edge_weight() is the runtime authority — this is a hint.

    `market` picks the breakeven. Omitting it keeps the -110 default, which
    is correct for rl/total and conservative-high for prop.
    """
    if tier == 'ANTI_VALIDATED':
        return 0.0
    import math
    edge = max(0.0, hit_rate_frac - breakeven_for(market))
    edge_c = min(edge / 0.12, 1.0)
    n_c = min(math.log1p(n) / math.log(101), 1.0)
    return round(edge_c * n_c, 4)


def collect_prop_firings(sport: str, days: int) -> dict[str, dict]:
    """Return {signal_key: {right, wrong}} from prop_playbook_decisions."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    r = requests.get(f'{SB}/rest/v1/prop_playbook_decisions',
                     headers={**H_READ, 'Range-Unit': 'items', 'Range': '0-9999'},
                     params={'sport': f'eq.{sport}',
                             'game_date': f'gte.{cutoff}',
                             'result': 'in.(W,L,Win,Loss)',
                             'select': 'result,playbook_side,playbook_sources'},
                     timeout=30)
    if r.status_code not in (200, 206):
        print(f'  ! prop query failed {r.status_code}: {r.text[:200]}')
        return {}
    rows = r.json() or []
    print(f'  [{sport}] prop decisions graded (last {days}d): {len(rows)}')

    firings: dict[str, dict] = defaultdict(lambda: {'right': 0, 'wrong': 0})
    for row in rows:
        result = str(row.get('result', '')).upper()
        won = result in ('W', 'WIN')
        pb_side = str(row.get('playbook_side', '')).upper()
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
            # Signal "agreed with pick" AND pick won → correct.
            # Signal "disagreed with pick" AND pick lost → also correct (they
            # were voting the winning side and the winning side did win).
            signal_agreed_with_pick = (src_side == pb_side)
            correct = (signal_agreed_with_pick == won)
            if correct: firings[base_key]['right'] += 1
            else:       firings[base_key]['wrong'] += 1
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
        lambda: {'right': 0, 'wrong': 0, 'blind_sum': 0.0, 'blind_n': 0})
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
                                 'select': 'signal_key,kind,result,'
                                           'pick_market'},
                         timeout=30)
        if r.status_code not in (200, 206): break
        page_rows = r.json() or []
        if not isinstance(page_rows, list): break
        all_rows.extend(page_rows)
        if len(page_rows) < 1000: break
    print(f'  [{sport}] signal_attribution graded rows (last {days}d): {len(all_rows)}')

    firings: dict[str, dict] = defaultdict(
        lambda: {'right': 0, 'wrong': 0, 'mkt': Counter()})
    for row in all_rows:
        key = row.get('signal_key')
        if not key: continue
        # 2026-10-27 F6: the market decides the breakeven, so tally which
        # market this signal actually fires on.
        _m = str(row.get('pick_market') or '').strip().lower()
        if _m:
            firings[key]['mkt'][_m] += 1
        kind = str(row.get('kind', '')).lower()
        result = str(row.get('result', '')).upper()
        won = (result == 'W')
        # ok+W or warn+L → the signal's voice was right; ok+L or warn+W → wrong.
        signal_was_right = (kind == 'ok' and won) or (kind == 'warn' and not won)
        if signal_was_right: firings[key]['right'] += 1
        else:                firings[key]['wrong'] += 1
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
                 'mkt': Counter()})
    for d in dicts:
        for k, v in d.items():
            out[k]['right'] += v.get('right', 0)
            out[k]['wrong'] += v.get('wrong', 0)
            out[k]['blind_sum'] += v.get('blind_sum', 0.0)
            out[k]['blind_n'] += v.get('blind_n', 0)
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
        tier = _tier_for(n, hr_frac, blind)
        # 2026-10-27 F6: weight against the breakeven of the market this
        # signal actually fires on, not a flat -110.
        _mkt_ct = ct.get('mkt') or {}
        _dom = (max(_mkt_ct.items(), key=lambda z: z[1])[0]
                if _mkt_ct else '')
        weight = _recommended_weight_for(n, hr_frac, tier, _dom)
        out.append({
            'signal_name': signal_key,
            'category': _infer_category(signal_key),
            'sport': sport,
            'hit_rate': round(hr_frac * 100, 2),
            'sample_n': n,
            'tier': tier,
            'recommended_weight': weight,
            'direction_hint': 'FADE' if tier == 'ANTI_VALIDATED' else 'FOLLOW',
            'origin': 'REFIT_SCRIPT',
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

    written = upsert_registry(rows, dry_run=dry_run)
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
