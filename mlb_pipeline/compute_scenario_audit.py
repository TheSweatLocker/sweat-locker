"""Cross-sport scenario audit (2026-08-10 · queue item from user).

Answers "does public/sharp/house win when scenario X happens?" across
every sport. Nightly recompute of `scenario_audit` table.

## How it works

For each graded historical game, project it into N pre-defined scenarios
(canonical dim-tuples). Bucket outcomes per (sport, market, scenario, window).
Compute hit_rate + ROI + jerry_hint.

## Scenarios (v1)

**MLB-specific** (deeper — where we have most data):
  - public_pct band × market (ml/spread/total) × home/away × fav/dog
  - sharp_pct band × market × side
  - money-vs-bets divergence (whale) × market
  - bullpen taxed + market
  - line-move direction × sharp side (reverse-line-move)
  - confluence net band × primary_play tier
  - pitcher L3 ERA gap band × ML side

**Sport-universal** (starts sparse, fills as sports mature):
  - public_pct heavy on home/away side × outcome
  - sharp_pct heavy on side × outcome (baseline sharp-fade metric)

## Extensibility

New scenarios: add an entry to `SCENARIO_DEFS` with a canonical
scenario_key generator function. Schema doesn't change.

New sports: register in `RESULTS_TABLE`. Sport-agnostic scenarios apply
automatically. Sport-specific scenarios need per-sport def function.

## Usage

    python compute_scenario_audit.py [--sport MLB|ALL] [--window lifetime|90d|30d]
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Any

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

RESULTS_TABLE = {
    'MLB':   ('mlb_game_context', 'mlb_game_results'),
    'NFL':   ('nfl_game_context', 'nfl_game_results'),
    'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results'),
    'NHL':   ('nhl_game_context', 'nhl_game_results'),
    'NCAAB': ('ncaab_game_context', 'ncaab_game_results'),
    # NBA/UFC add when Jerry synth ships for each
}

# 2026-08-11: Sports whose RESULTS table is the primary source. Iterate results
# directly (skip ctx-fetch entirely). Needed for sports with sparse or empty
# ctx tables but rich historical results — NCAAF has 15k+ games in results
# but ctx is only populated on gameday going forward. Same eventually for NHL.
RESULTS_PRIMARY = {'NCAAF', 'NHL', 'NCAAB'}


def _parse_snap(s):
    if not s: return {}
    if isinstance(s, str):
        try: return json.loads(s)
        except: return {}
    return s


def _bucket_pct(pct: float | None) -> str | None:
    """Bucket a percentage into named bands: 50-55, 55-65, 65-75, 75+."""
    if pct is None: return None
    if pct >= 75: return '75+'
    if pct >= 65: return '65-74'
    if pct >= 55: return '55-64'
    if pct >= 50: return '50-54'
    return '<50'


def _bucket_ml(ml: int | None) -> str | None:
    """American odds bucket: heavy_fav / fav / pickem / dog / heavy_dog."""
    if ml is None: return None
    if ml <= -200: return 'heavy_fav'
    if ml <= -130: return 'fav'
    if ml <= 130: return 'pickem'
    if ml <= 200: return 'dog'
    return 'heavy_dog'


def _grade_ml(pick_side: str, hs: int, as_: int) -> str | None:
    if hs == as_: return 'push'
    won = (pick_side == 'HOME' and hs > as_) or (pick_side == 'AWAY' and as_ > hs)
    return 'win' if won else 'loss'


def _grade_total(pick_side: str, line: float | None, hs: int, as_: int) -> str | None:
    if line is None: return None
    total = hs + as_
    if total == line: return 'push'
    if pick_side == 'OVER': return 'win' if total > line else 'loss'
    if pick_side == 'UNDER': return 'win' if total < line else 'loss'
    return None


def build_scenarios_mlb(row: dict) -> list[tuple[str, str, str, str]]:
    """MLB scenario extraction. Returns list of (market, scenario_key,
    scenario_label, actual_side_or_outcome_bucket) tuples.

    Each element represents: this game contributed one data point to
    scenario S with observed side/outcome O. We then grade at aggregate.
    """
    scenarios = []
    snap = _parse_snap(row.get('oddscrowd_snapshot'))
    home_ml = row.get('home_ml_close')
    away_ml = row.get('away_ml_close')
    close_total = row.get('close_total')
    close_spread = row.get('close_spread')
    conf_net = row.get('signal_confluence_net')

    # --- ML scenarios ---
    ml_seg = snap.get('ml') or {}
    ml_pick = (ml_seg.get('pick') or '').upper()
    ml_money = ml_seg.get('money')
    ml_bets = ml_seg.get('bets')
    ml_div = ml_seg.get('div', 0)
    if ml_pick in ('HOME', 'AWAY'):
        # public_pct bucket × ml_pick side
        band = _bucket_pct(ml_money)
        if band:
            side_ml = home_ml if ml_pick == 'HOME' else away_ml
            fav_dog = _bucket_ml(side_ml)
            if fav_dog:
                key = f'public_money={band}&side={ml_pick.lower()}&ml_bucket={fav_dog}'
                label = f'Public {band}% $ on {ml_pick} {fav_dog}'
                scenarios.append(('ml', key, label, ml_pick))
        # Whale divergence — money >= bets + 15
        if ml_money is not None and ml_bets is not None:
            if ml_money - ml_bets >= 15:
                key = f'whale_signal&side={ml_pick.lower()}'
                label = f'Whale ($≥bets+15) on {ml_pick} ML'
                scenarios.append(('ml', key, label, ml_pick))
            elif ml_bets - ml_money >= 15:
                key = f'square_signal&side={ml_pick.lower()}'
                label = f'Square (bets≥$+15) on {ml_pick} ML'
                scenarios.append(('ml', key, label, ml_pick))

    # --- Total scenarios ---
    tot_seg = snap.get('total') or {}
    tot_pick = (tot_seg.get('pick') or '').upper()
    tot_money = tot_seg.get('money')
    tot_bets = tot_seg.get('bets')
    if tot_pick in ('OVER', 'UNDER') and close_total is not None:
        band = _bucket_pct(tot_money)
        if band:
            # Total line bucket: low <8, mid 8-9, high >9
            if close_total < 8: line_bucket = 'low<8'
            elif close_total < 9.5: line_bucket = 'mid8-9.5'
            else: line_bucket = 'high>=9.5'
            key = f'public_money={band}&side={tot_pick.lower()}&line={line_bucket}'
            label = f'Public {band}% $ on {tot_pick} · total {line_bucket}'
            scenarios.append(('total', key, label, tot_pick))
        # Whale
        if tot_money is not None and tot_bets is not None:
            if tot_money - tot_bets >= 15:
                key = f'whale_signal&side={tot_pick.lower()}'
                label = f'Whale ($≥bets+15) on {tot_pick}'
                scenarios.append(('total', key, label, tot_pick))

    # --- Square-signal scenarios (2026-08-11 · post-BAL loss) ---
    # When public bet% is HIGHER than money% (bets - money >= 5) on the ML
    # pick, it's a "square-heavy" spot: retail is loaded but sharp isn't
    # matching. Historically these underperform vs headline. Same for total.
    if ml_pick in ('HOME', 'AWAY') and ml_money is not None and ml_bets is not None:
        square_gap = ml_bets - ml_money
        if square_gap >= 5:
            side_ml = home_ml if ml_pick == 'HOME' else away_ml
            fav_dog = _bucket_ml(side_ml)
            if fav_dog:
                key = f'square_signal_bets_over_money&side={ml_pick.lower()}&ml_bucket={fav_dog}'
                label = f'Square ML (bets≥$+{square_gap}) on {ml_pick} {fav_dog}'
                scenarios.append(('ml', key, label, ml_pick))
    if tot_pick in ('OVER', 'UNDER') and tot_money is not None and tot_bets is not None:
        square_gap_t = tot_bets - tot_money
        if square_gap_t >= 5:
            key = f'square_signal_bets_over_money&side={tot_pick.lower()}'
            label = f'Square total (bets≥$+{square_gap_t}) on {tot_pick}'
            scenarios.append(('total', key, label, tot_pick))

    # --- Road-favorite scenarios (2026-08-11 · post-BAL loss) ---
    # BAL @ MIN yesterday was a road fav at -108 that lost. Historically
    # road favorites at thin juice underperform (a "trap" bucket per
    # sportsbook heuristics). Track separately from public-money scenarios.
    if away_ml is not None and home_ml is not None:
        try:
            away_ml_i = int(away_ml); home_ml_i = int(home_ml)
            # Road team is favored if their ML is more negative than home's
            if away_ml_i < 0 and away_ml_i < home_ml_i:
                juice_bucket = ('thin' if away_ml_i >= -125 else
                                'mid' if away_ml_i >= -170 else 'heavy')
                key = f'road_fav_juice={juice_bucket}'
                label = f'Road favorite (juice {juice_bucket})'
                scenarios.append(('ml', key, label, 'AWAY'))
        except (TypeError, ValueError): pass

    # --- Reverse-line-move outcome tracking (2026-08-11) ---
    # detect_reverse_line_move.py writes primary_play.rlm_flag when
    # public 70%+ on side A and line moved AGAINST A → sharp on side B.
    # This scenario tracks whether the SHARP side (opposite of public)
    # actually wins. High hit rate here validates the RLM signal.
    pp_check = row.get('primary_play') or {}
    if isinstance(pp_check, dict):
        rlm = pp_check.get('rlm_flag')
        if isinstance(rlm, dict) and rlm.get('public_side') and rlm.get('market'):
            rlm_market = rlm['market']
            public = rlm['public_side'].upper()
            # Sharp/suggested side is opposite of public
            if public == 'HOME': sharp_side = 'AWAY'
            elif public == 'AWAY': sharp_side = 'HOME'
            elif public == 'OVER': sharp_side = 'UNDER'
            elif public == 'UNDER': sharp_side = 'OVER'
            else: sharp_side = None
            if sharp_side:
                conf = rlm.get('confidence', 'moderate')
                key = f'reverse_line_move&market={rlm_market}&confidence={conf}'
                label = f'Reverse line move · {rlm_market} · sharp {sharp_side} (fade public {public})'
                # Grade against the SHARP side winning
                scenarios.append((rlm_market, key, label, sharp_side))

    # --- Confluence-x-primary_play scenarios ---
    pp = row.get('primary_play') or {}
    if isinstance(pp, dict) and pp.get('tier') and conf_net is not None:
        tier = pp.get('tier')
        market = pp.get('type', 'unknown')
        conf_band = 'high+3' if conf_net >= 3 else 'mid+1-2' if conf_net >= 1 else \
                    'neutral' if abs(conf_net) < 1 else 'mid-1-2' if conf_net >= -2 else 'low<-2'
        key = f'tier={tier}&confluence={conf_band}'
        label = f'{tier} primary_play at confluence {conf_band}'
        # For grading we need the pick side — encode into scenario tuple
        side = pp.get('side') or pp.get('label', '').split()[0].upper()
        if side in ('HOME','AWAY','OVER','UNDER'):
            scenarios.append((market, key, label, side))

    return scenarios


def grade_scenario(sport: str, market: str, scenario_side: str,
                   game_row: dict, results_row: dict) -> str | None:
    """Return 'win' / 'loss' / 'push' for scenario_side given actual outcome.

    Prefer pre-computed grade columns when available on the results row
    (NCAAF: spread_result / total_result / home_win). Falls back to
    score-based grading (MLB).
    """
    # --- Pre-computed grade path (NCAAF/NHL/NCAAB) ---
    # NCAAF convention: spread_result = 'home_covered'|'away_covered'|'push'
    # (lowercase). total_result = 'over'|'under'|'push'. home_win = bool.
    if market == 'spread' and results_row.get('spread_result') is not None:
        r = str(results_row['spread_result']).lower()
        if r == 'push': return 'push'
        if r in ('home_covered', 'home'):
            return 'win' if scenario_side == 'HOME' else 'loss'
        if r in ('away_covered', 'away'):
            return 'win' if scenario_side == 'AWAY' else 'loss'
    if market == 'total' and results_row.get('total_result') is not None:
        r = str(results_row['total_result']).lower()
        if r == 'push': return 'push'
        if r == 'over':
            return 'win' if scenario_side == 'OVER' else 'loss'
        if r == 'under':
            return 'win' if scenario_side == 'UNDER' else 'loss'
    if market == 'ml' and results_row.get('home_win') is not None:
        winner = 'HOME' if results_row['home_win'] else 'AWAY'
        return 'win' if winner == scenario_side else 'loss'

    # --- Score-based path (MLB fallback) ---
    hs = results_row.get('home_score')
    as_ = results_row.get('away_score')
    if hs is None or as_ is None: return None
    try: hs = int(hs); as_ = int(as_)
    except: return None

    if market == 'ml':
        if scenario_side in ('HOME', 'AWAY'):
            return _grade_ml(scenario_side, hs, as_)
    elif market == 'total':
        line = game_row.get('close_total')
        return _grade_total(scenario_side, line, hs, as_)
    elif market in ('over', 'under'):
        line = game_row.get('close_total')
        return _grade_total(scenario_side, line, hs, as_)
    return None


def build_scenarios_ncaaf(row: dict) -> list[tuple[str, str, str, str]]:
    """NCAAF scenario extraction (2026-08-11).

    NCAAF results table carries close_spread / close_total / close_X_ml
    directly (rich historical dataset, 15k+ games). Sharp/public data
    (oddscrowd) not yet integrated for CFB — sharp scenarios will
    populate once NCAAF oddscrowd puller ships.

    Initial scenarios (from results only):
      * Home dog ATS by spread bucket
      * Road favorite ML by juice band
      * Total OVER/UNDER by line bucket
      * Conference game × home_ml_bucket
      * Public-money scenarios — added once oddscrowd_snapshot is populated
    """
    scenarios = []
    close_spread = row.get('close_spread')
    close_total = row.get('close_total')
    home_ml = row.get('close_home_ml')
    away_ml = row.get('close_away_ml')
    conf_game = row.get('conference_game')

    # --- Home dog ATS scenarios ---
    if close_spread is not None:
        try:
            spr = float(close_spread)
            # NCAAF convention: positive close_spread = HOME is underdog
            if spr > 0:
                if spr >= 14: dog_bucket = 'heavy_dog_14+'
                elif spr >= 7: dog_bucket = 'mid_dog_7-13'
                else: dog_bucket = 'small_dog<7'
                key = f'home_dog_ats&spread={dog_bucket}'
                label = f'Home dog ATS ({dog_bucket})'
                scenarios.append(('spread', key, label, 'HOME'))
            elif spr < 0:
                if spr <= -14: fav_bucket = 'heavy_fav_-14+'
                elif spr <= -7: fav_bucket = 'mid_fav_-7to-13'
                else: fav_bucket = 'small_fav>-7'
                key = f'home_fav_ats&spread={fav_bucket}'
                label = f'Home favorite ATS ({fav_bucket})'
                scenarios.append(('spread', key, label, 'HOME'))
        except (TypeError, ValueError): pass

    # --- Road favorite ML by juice ---
    if away_ml is not None and home_ml is not None:
        try:
            aml = int(away_ml); hml = int(home_ml)
            if aml < 0 and aml < hml:
                juice = ('thin' if aml >= -150 else 'mid' if aml >= -250 else 'heavy')
                key = f'road_fav_ml&juice={juice}'
                label = f'CFB road favorite (juice {juice})'
                scenarios.append(('ml', key, label, 'AWAY'))
        except (TypeError, ValueError): pass

    # --- Total bucket ---
    if close_total is not None:
        try:
            tot = float(close_total)
            if tot < 45: line_bucket = 'low<45'
            elif tot < 55: line_bucket = 'mid_45-54'
            elif tot < 65: line_bucket = 'high_55-64'
            else: line_bucket = 'shootout_65+'
            # We don't know which side to bet without sharp data — track BOTH sides
            # as historical hit rates for informational purposes
            for side in ('OVER', 'UNDER'):
                key = f'total_line_bucket_{side.lower()}&line={line_bucket}'
                label = f'Total {side} · line {line_bucket}'
                scenarios.append(('total', key, label, side))
        except (TypeError, ValueError): pass

    # --- Conference vs non-conf ---
    if conf_game is not None:
        conf_str = 'conf' if conf_game else 'nonconf'
        if home_ml is not None and away_ml is not None:
            try:
                hml_i = int(home_ml)
                if hml_i < 0:  # home favored
                    key = f'{conf_str}_game_home_fav'
                    label = f'{conf_str.upper()} game · home favorite ML'
                    scenarios.append(('ml', key, label, 'HOME'))
            except (TypeError, ValueError): pass

    return scenarios


def build_scenarios_nhl(row: dict) -> list[tuple[str, str, str, str]]:
    """NHL scenario extractor stub (2026-08-11).

    Fills in once NHL data lands (Oct 8 season). Same pattern as MLB/NCAAF:
      * Public money bands × side × ML bucket (once oddscrowd covers NHL)
      * Puck-line scenarios (spread analog)
      * Total OVER/UNDER at 5.5 / 6.0 / 6.5 buckets (hockey-specific)
      * Goalie form scenarios once nhl_goalie_stats populated
    """
    return []  # empty until NHL data flows


def build_scenarios_ncaab(row: dict) -> list[tuple[str, str, str, str]]:
    """NCAAB scenario extractor stub (2026-08-11).

    Fills in once NCAAB data lands (Nov 3 season). Scenarios to add:
      * KenPom rank gap × side (favored by X spots historically hits Y%)
      * Public heavy on home fav × conf/nonconf
      * Total scenarios at basketball line ranges (135 / 145 / 155)
      * Q4 comeback / late-game scenarios (basketball-specific)
    """
    return []  # empty until NCAAB data flows


def american_to_decimal(o) -> float | None:
    if o is None: return None
    try: o = int(o)
    except: return None
    return 1 + (100 / (-o)) if o < 0 else 1 + (o / 100)


def compute_jerry_hint(hit_rate: float, roi: float | None, n: int) -> tuple[str, int]:
    """Standard jerry_hint mapping matching prop_bucket_roi convention."""
    if n < 20: return ('PASS', 30)
    if hit_rate >= 60 and (roi is None or roi > 5): return ('BACK', min(90, 50 + int(hit_rate - 50)))
    if hit_rate <= 42 or (roi is not None and roi < -10): return ('FADE', min(85, 50 + int(50 - hit_rate)))
    if hit_rate >= 55: return ('LEAN', 55)
    return ('PASS', 40)


def _run_results_primary(sport: str, res_table: str, window: str,
                          date_filter: str | None) -> int:
    """Results-primary variant of run() for sports whose ctx is thin/empty
    but whose results table carries close_spread/total/ml + graded outcomes.

    Iterates results directly. Passes the result row into build_scenarios_X
    as both `ctx` (for scenario extraction) and `res` (for grading).
    Historical scenarios only — no sharp/public/whale bands (those need ctx).
    """
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()
    all_res_rows = []; offset = 0
    while True:
        params = {'select': '*', 'order': 'game_date.desc',
                  'game_date': f'lt.{today}'}
        if date_filter:
            params['and'] = f'(game_date.gte.{date_filter},game_date.lt.{today})'
        r = requests.get(f'{SB}/rest/v1/{res_table}',
            headers={**H_READ, 'Range': f'{offset}-{offset+999}',
                     'Range-Unit': 'items'},
            params=params, timeout=30)
        if r.status_code != 200: break
        rows = r.json()
        if not isinstance(rows, list) or not rows: break
        all_res_rows += rows
        if len(rows) < 1000: break
        offset += 1000
        if offset > 20000: break
    print(f'  {len(all_res_rows)} result rows (results-primary path)')

    accum = defaultdict(list)
    scenario_labels = {}
    dispatch = {'NCAAF': build_scenarios_ncaaf, 'NHL': build_scenarios_nhl,
                'NCAAB': build_scenarios_ncaab}
    builder = dispatch.get(sport)
    if not builder:
        print(f'  no builder for {sport}'); return 0

    for res in all_res_rows:
        scenarios = builder(res)
        for market, key, label, side in scenarios:
            outcome = grade_scenario(sport, market, side, res, res)
            if outcome:
                accum[(market, key)].append((outcome,
                    res.get('close_home_ml'), res.get('close_away_ml'),
                    res.get('close_total'), side))
                scenario_labels[(market, key)] = label

    return _finalize_and_upsert(sport, window, accum, scenario_labels)


def run(sport: str, window: str = 'lifetime') -> int:
    ctx_table, res_table = RESULTS_TABLE.get(sport, (None, None))
    if not ctx_table:
        print(f'  [{sport}] no tables registered — skip'); return 0

    print(f'=== scenario_audit · sport={sport} · window={window} ===')
    date_filter = None
    if window == '90d': date_filter = (datetime.now(timezone.utc) - timedelta(days=90)).date().isoformat()
    elif window == '30d': date_filter = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()

    # 2026-08-11: results-primary path for sports whose ctx table is empty/thin
    # but whose results table carries the rich betting-line data directly.
    if sport in RESULTS_PRIMARY:
        return _run_results_primary(sport, res_table, window, date_filter)

    # Paginate ctx via Range headers — Supabase default row limit is 1000
    # but `limit` param isn't enough here (jsonb hydration may cap response
    # smaller). Range headers are the reliable path.
    # 2026-08-10 fix: only pull PAST games (game_date < today). Today's + future
    # games are in ctx but not yet in results — including them here inflates
    # the row count and produces 0 matches when we join.
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()
    all_ctx = []; offset = 0
    while True:
        params = {'select': '*', 'order': 'game_date.desc',
                  'game_date': f'lt.{today}'}
        if date_filter: params['and'] = f'(game_date.gte.{date_filter},game_date.lt.{today})'
        r = requests.get(f'{SB}/rest/v1/{ctx_table}',
            headers={**H_READ, 'Range': f'{offset}-{offset+999}',
                     'Range-Unit': 'items'},
            params=params, timeout=30).json()
        if not isinstance(r, list) or not r: break
        all_ctx += r
        if len(r) < 1000: break
        offset += 1000
        if offset > 20000: break  # safety cap
    print(f'  {len(all_ctx)} game_context rows (past-only)')

    # Pull results — only need game_id, home_score, away_score.
    # 2026-08-10: cap chunk at 80 game_ids per URL (game_id is 32 chars,
    # avoids URL-length limits that returned empty results silently).
    gids = [c['game_id'] for c in all_ctx if c.get('game_id')]
    all_res = {}
    # 2026-08-10: use unquoted comma-list; game_ids are hex, no special
    # chars that require PostgREST double-quoting. Prior quoted variant
    # was URL-encoding the quotes and returning empty results silently.
    for i in range(0, len(gids), 80):
        chunk = gids[i:i+80]
        gid_in = ','.join(chunk)
        # 2026-08-10: build URL inline instead of via params dict — requests
        # url-encodes commas + parens which breaks PostgREST in.() filter.
        url = (f'{SB}/rest/v1/{res_table}?game_id=in.({gid_in})'
               f'&select=game_id,home_score,away_score')
        rr = requests.get(url, headers=H_READ, timeout=30)
        for row in (rr.json() if rr.status_code == 200 else []):
            all_res[row['game_id']] = row

    print(f'  {len(all_res)} results matched')

    # Build scenario map: {(market, key, side): [outcomes]}
    accum = defaultdict(list)
    scenario_labels = {}  # (market, key) -> label
    for ctx in all_ctx:
        res = all_res.get(ctx.get('game_id'))
        if not res or res.get('home_score') is None: continue
        # 2026-08-11: sport-dispatch scenarios. For sports where the rich
        # betting-line data lives on RESULTS (NCAAF has close_spread/total/
        # ml on results, ctx is thin), merge res into ctx before dispatch.
        merged = dict(ctx)
        for k in ('close_spread', 'close_total', 'close_home_ml', 'close_away_ml',
                  'conference_game', 'spread_result', 'total_result'):
            if k in res and merged.get(k) is None:
                merged[k] = res[k]
        if sport == 'MLB':
            scenarios = build_scenarios_mlb(merged)
        elif sport == 'NCAAF':
            scenarios = build_scenarios_ncaaf(merged)
        elif sport == 'NHL':
            scenarios = build_scenarios_nhl(merged)
        elif sport == 'NCAAB':
            scenarios = build_scenarios_ncaab(merged)
        else:
            scenarios = []
        for market, key, label, side in scenarios:
            outcome = grade_scenario(sport, market, side, ctx, res)
            if outcome:
                accum[(market, key)].append((outcome, ctx.get('home_ml_close'),
                                              ctx.get('away_ml_close'),
                                              ctx.get('close_total'), side))
                scenario_labels[(market, key)] = label

    return _finalize_and_upsert(sport, window, accum, scenario_labels)


def _finalize_and_upsert(sport: str, window: str, accum: dict,
                          scenario_labels: dict) -> int:
    """Aggregate accumulated (market, key) → outcomes, compute hit/ROI,
    and upsert into scenario_audit. Extracted 2026-08-11 so both the
    ctx-primary run() and the results-primary _run_results_primary()
    paths share this write logic verbatim."""
    written = 0
    print(f'\n{"market":<8} {"scenario":<50} {"n":>4} {"hit%":>6} {"ROI%":>7} hint')
    for (market, key), rows in sorted(accum.items(), key=lambda x: -len(x[1])):
        wins = sum(1 for r in rows if r[0] == 'win')
        losses = sum(1 for r in rows if r[0] == 'loss')
        pushes = sum(1 for r in rows if r[0] == 'push')
        n = wins + losses + pushes
        if n < 10: continue
        graded = wins + losses
        if graded == 0: continue
        hit = round(100 * wins / graded, 2)
        avg_dec = None
        if market == 'ml':
            odds_list = []
            for _, hml, aml, _, side in rows:
                o = hml if side == 'HOME' else aml
                d = american_to_decimal(o)
                if d: odds_list.append(d)
            if odds_list: avg_dec = round(sum(odds_list)/len(odds_list), 3)
        else:
            avg_dec = 1.91  # -110 assumption for totals/spreads
        roi = None
        if avg_dec:
            p_win = wins / graded
            roi = round(100 * (p_win * (avg_dec - 1) - (1 - p_win)), 2)
        hint, conf = compute_jerry_hint(hit, roi, n)
        payload = {
            'sport': sport, 'market': market, 'scenario_key': key,
            'scenario_label': scenario_labels.get((market, key)),
            'wins': wins, 'losses': losses, 'pushes': pushes, 'total_n': n,
            'hit_rate': hit, 'avg_dec_odds': avg_dec, 'roi_pct': roi,
            'jerry_hint': hint, 'hint_confidence': conf,
            'scenario_window': window,
            'computed_at': datetime.now(timezone.utc).isoformat(),
        }
        pr = requests.post(
            f'{SB}/rest/v1/scenario_audit?on_conflict=sport,market,scenario_key,scenario_window',
            headers=H_WRITE, json=payload, timeout=15)
        if pr.status_code in (200, 201, 204):
            written += 1
            roi_s = f'{roi:+.1f}%' if roi is not None else '   -   '
            print(f'  {market:<8} {key[:48]:<50} {n:>4} {hit:>5.1f}% {roi_s:<7} {hint}')
        else:
            print(f'  UPSERT FAILED {pr.status_code} for {market}/{key}: {pr.text[:150]}')
    print(f'\n  wrote {written} scenario_audit rows for {sport}/{window}')
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='ALL',
                    help='MLB / NFL / NCAAF / NHL / NCAAB / ALL')
    ap.add_argument('--window', default='lifetime',
                    choices=['lifetime', '90d', '30d'])
    args = ap.parse_args()
    sports = list(RESULTS_TABLE.keys()) if args.sport == 'ALL' else [args.sport]
    for s in sports:
        run(sport=s, window=args.window)


if __name__ == '__main__':
    main()
