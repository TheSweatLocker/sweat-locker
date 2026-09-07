"""Aggregate daily surface records (2026-08-20).

Nightly job. For each surface (sharp_card, sweat_card, ledger_*, ladder,
dawg_of_day, daily_degen, potd), compute yesterday's:
  - wins / losses / pushes
  - units_bet / units_won (real BFO/book odds where available)
  - pick count

Writes one row per (surface, sport, date) to daily_surface_records.
User caught on 8/20 audit that Sharp Card had NO daily record persisted
anywhere — this closes the gap.

CLI:
  python aggregate_daily_records.py                 # yesterday ET
  python aggregate_daily_records.py --date YYYY-MM-DD
  python aggregate_daily_records.py --backfill 21   # last 21 days
  python aggregate_daily_records.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

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


def _et_yesterday() -> str:
    return ((datetime.now(timezone.utc) - timedelta(hours=4)).date()
            - timedelta(days=1)).isoformat()


def _american_payout(odds) -> float:
    """1u win payout for American odds. -110 → 0.909, +130 → 1.30."""
    if odds is None: return 0.909
    try: o = int(odds)
    except (TypeError, ValueError): return 0.909
    return (o / 100.0) if o >= 100 else (100.0 / abs(o))


def _grade_side(pp: dict, game: dict) -> str | None:
    """Grade an ML/RL/total pick. Returns W/L/P/None."""
    hs = game.get('home_score'); as_ = game.get('away_score')
    if hs is None or as_ is None: return None
    home = (game.get('home_team') or '').lower()
    away = (game.get('away_team') or '').lower()
    m = (pp.get('type') or '').lower()
    label = (pp.get('label') or '').lower()
    picked_home = home in label; picked_away = away in label
    if m == 'ml':
        if picked_home: return 'W' if hs > as_ else 'L' if hs < as_ else 'P'
        if picked_away: return 'W' if as_ > hs else 'L' if as_ < hs else 'P'
    elif m == 'rl':
        rl = (game.get('run_line_result') or '').lower()
        if picked_home and '+1.5' in label: return 'W' if rl != 'home' else 'L'
        if picked_home: return 'W' if rl == 'home' else 'L'
        if picked_away and '+1.5' in label: return 'W' if rl != 'away' else 'L'
        if picked_away: return 'W' if rl == 'away' else 'L'
    elif m in ('total','over','under'):
        tr = (game.get('total_result') or '').lower()
        if 'over' in label: return 'W' if tr == 'over' else 'L' if tr == 'under' else 'P'
        if 'under' in label: return 'W' if tr == 'under' else 'L' if tr == 'over' else 'P'
    return None


# ─────────────────────────────────────────────────────────────
# SURFACE AGGREGATORS
# ─────────────────────────────────────────────────────────────

# 2026-08-31: Sharp Card sizing cutover date.
# BEFORE this date: use the legacy hardcoded 2u/PRIME + 2u/STRONG map
#   so historical records match what was actually recommended (per user:
#   "historical should stay where it is").
# ON/AFTER this date: use primary_play.recommended_stake (1u default,
#   2u LOCK gate — see game_context.compute_recommended_stake). Uniform
#   sizing that reflects the true published play.
SHARP_STAKE_CUTOVER = '2026-08-31'


def _load_game_results_by_sport(date: str, sport: str) -> dict:
    """Load game_results rows for one sport → matchup-keyed dict for
    downstream Sharp Card grading. Normalizes spread column so grading
    is sport-agnostic downstream."""
    _RESULTS_TABLE = {
        'MLB': 'mlb_game_results', 'NFL': 'nfl_game_results',
        'NCAAF': 'ncaaf_game_results', 'NCAAB': 'ncaab_game_results',
        'NBA': 'nba_game_results',
    }
    _SPREAD_COL = {
        'MLB': 'run_line_result,total_result,spread_result',
        'NFL': 'spread_result,total_result',
        'NCAAF': 'spread_result,total_result',
        'NCAAB': 'spread_result,total_result',
        'NBA': 'spread_result,total_result',
    }
    table = _RESULTS_TABLE.get(sport)
    if not table: return {}
    cols = _SPREAD_COL.get(sport, 'spread_result,total_result')
    r = requests.get(f'{SB}/rest/v1/{table}',
        headers=H_READ,
        params={'game_date': f'eq.{date}',
                'select': f'game_id,home_team,away_team,home_score,away_score,'
                          f'home_win,{cols}'},
        timeout=15)
    if r.status_code != 200:
        print(f'  [agg_sharp_card] {sport} results fetch failed: {r.status_code} {str(r.text)[:120]}')
        return {}
    by_matchup = {}
    for g in (r.json() if isinstance(r.json(), list) else []):
        # Normalize: alias spread_result → run_line_result so _grade_side
        # works uniformly for both baseball and other sports.
        if sport != 'MLB' and g.get('spread_result') is not None:
            sp = str(g.get('spread_result','')).lower()
            if 'home' in sp and 'covered' in sp:  g['run_line_result'] = 'home'
            elif 'away' in sp and 'covered' in sp: g['run_line_result'] = 'away'
            elif 'push' in sp: g['run_line_result'] = 'push'
        key = f"{(g.get('away_team') or '').lower()} @ {(g.get('home_team') or '').lower()}"
        by_matchup[key] = g
    return by_matchup


def _load_props_by_sport(date: str, sport: str) -> dict:
    """Load prop rows for one sport → composite-key dict for Sharp Card
    prop grading."""
    _PROPS_TABLE = {
        'MLB': 'mlb_pipeline_props', 'NFL': 'nfl_pipeline_props',
    }
    table = _PROPS_TABLE.get(sport)
    if not table: return {}
    r = requests.get(f'{SB}/rest/v1/{table}',
        headers=H_READ,
        params={'game_date': f'eq.{date}',
                'select': 'player_name,prop_type,direction,tier,result,book_over_odds,book_under_odds'},
        timeout=15)
    if r.status_code != 200:
        print(f'  [agg_sharp_card] {sport} props fetch failed: {r.status_code}')
        return {}
    by_key = {}
    for p in (r.json() if isinstance(r.json(), list) else []):
        pkey = (str(p.get('player_name') or '').lower(),
                str(p.get('prop_type') or '').lower(),
                str(p.get('direction') or '').lower())
        by_key[pkey] = p
    return by_key


def agg_sharp_card(date: str) -> list[dict] | None:
    """Sharp Card = items that SHIPPED to users on jerry_cache.sharp_card_YYYY-MM-DD.

    2026-09-05 ROOT-CAUSE FIX (launch-day credibility bug): prior version
    scanned mlb_game_results.primary_play + mlb_pipeline_props BROADLY for
    tier IN PRIME/STRONG. That over-counted by 27% (9/4: 75 graded picks
    vs 59 items actually on the card).

    The jerry_cache.sharp_card_{date} row written by generate_sharp_card.py
    IS the source of truth for what the user saw. Grade only those items.

    2026-09-07 MULTI-SPORT extension: prior version was MLB-only. Once
    NFL Week 1 hit the card (9/10+), NFL items would silently fail to
    grade — Sharp Card record would erode as NFL wins vanished.
    Now dispatches by item.sport, loads results/props per sport, and
    returns MULTIPLE daily_surface_records rows (per-sport + combined ALL).

    Item shape (see generate_sharp_card.py):
      {tier, type ('ml'/'total'/'rl'/'prop'), sport, pick (label),
       odds, units (stake), matchup, player_name?, prop_type?, direction?, line?}
    """
    # 1. Load the cached items — source of truth
    card = requests.get(f'{SB}/rest/v1/jerry_cache',
        headers=H_READ,
        params={'cache_key': f'eq.sharp_card_{date}', 'select': 'data'},
        timeout=15).json()
    if not (isinstance(card, list) and card):
        return None
    blob = card[0].get('data') or {}
    items = blob.get('items') or []
    if not items:
        return None

    # 2. Group items by sport so we only load results tables we need
    sports_in_card = set()
    for it in items:
        if isinstance(it, dict):
            sport = (it.get('sport') or 'MLB').upper()
            sports_in_card.add(sport)

    # 3. Preload per-sport grading sources
    games_by_sport = {}
    props_by_sport = {}
    for sport in sports_in_card:
        games_by_sport[sport] = _load_game_results_by_sport(date, sport)
        props_by_sport[sport] = _load_props_by_sport(date, sport)

    # Legacy dict names for the original grading loop (default to MLB
    # dicts so the loop below reads them cleanly for backward compat).
    games_by_matchup = games_by_sport.get('MLB', {})
    props_by_key = props_by_sport.get('MLB', {})

    # 4. Grade each item on the card — dispatch by item.sport so NFL/
    # NCAAF items look up in the correct results tables (multi-sport fix).
    # Accumulate per-sport tallies so we can emit one daily_surface_records
    # row per sport plus a combined ALL row for the app hero display.
    per_sport = {}   # sport → {w, l, p_ct, units_bet, units_won, detail}
    def _bucket(s):
        return per_sport.setdefault(s, {'w':0,'l':0,'p_ct':0,'units_bet':0.0,
                                         'units_won':0.0,'detail':[]})

    for it in items:
        if not isinstance(it, dict): continue
        item_sport = (it.get('sport') or 'MLB').upper()
        item_type = (it.get('type') or '').lower()
        stake = float(it.get('units') or 1.0)
        odds = it.get('odds')
        verdict = None
        pick_label = it.get('pick') or it.get('pick_label') or '?'

        if item_type in ('ml', 'rl', 'total'):
            matchup = (it.get('matchup') or '').lower()
            games_dict = games_by_sport.get(item_sport, {})
            g = games_dict.get(matchup)
            if not g: continue
            fake_pp = {'type': item_type, 'label': pick_label}
            verdict = _grade_side(fake_pp, g)
        elif item_type == 'prop':
            player = it.get('player_name')
            ptype = it.get('prop_type')
            direction = it.get('direction')
            if not (player and ptype and direction):
                # Fallback — parse label like "Luis Castillo Under 15.5 OUTS"
                lbl = pick_label
                for ptype_upper, ptype_lc in [('KS','ks'), ('OUTS','outs'), ('ER','er'),
                                               ('HA','ha'), ('BB','bb'), ('HITS','hits')]:
                    if lbl.upper().endswith(f' {ptype_upper}'):
                        parts = lbl.rsplit(' ', 3)
                        if len(parts) >= 4:
                            player = ' '.join(parts[:-3])
                            direction = parts[-3].lower()
                            ptype = f'{ptype_lc}_{direction}'
                            break
            if not (player and ptype and direction):
                continue
            pkey = (str(player).lower(), str(ptype).lower(), str(direction).lower())
            props_dict = props_by_sport.get(item_sport, {})
            pr = props_dict.get(pkey)
            if not pr: continue
            res_c = (pr.get('result') or '').upper()
            if res_c in ('WIN', 'W'): verdict = 'W'
            elif res_c in ('LOSS', 'L'): verdict = 'L'
            elif res_c in ('PUSH', 'P'): verdict = 'P'

        if verdict is None: continue
        b = _bucket(item_sport)
        b['units_bet'] += stake
        payout = _american_payout(odds)
        if verdict == 'W': b['w'] += 1; b['units_won'] += stake * payout
        elif verdict == 'L': b['l'] += 1; b['units_won'] -= stake
        elif verdict == 'P': b['p_ct'] += 1
        b['detail'].append({'type': item_type, 'pick': pick_label[:80],
                            'verdict': verdict, 'stake': stake, 'odds': odds,
                            'sport': item_sport})

    if not per_sport: return None

    # 5. Emit one row per sport with data, plus a combined ALL row so the
    # app can show a single unified Sharp Card record OR filter per sport.
    rows = []
    all_w = all_l = all_p = 0
    all_bet = all_won = 0.0
    all_detail = []
    for sport, b in per_sport.items():
        if b['w'] + b['l'] + b['p_ct'] == 0: continue
        rows.append({
            'surface':'sharp_card','sport':sport,'record_date':date,
            'wins':b['w'],'losses':b['l'],'pushes':b['p_ct'],
            'units_bet':round(b['units_bet'],2),
            'units_won':round(b['units_won'],2),
            'pick_count':b['w']+b['l']+b['p_ct'],
            'detail':{'legs':b['detail'][:50], 'source':'jerry_cache.sharp_card'},
        })
        all_w += b['w']; all_l += b['l']; all_p += b['p_ct']
        all_bet += b['units_bet']; all_won += b['units_won']
        all_detail.extend(b['detail'])

    # Combined ALL row (only if multi-sport). App reads MLB row today but
    # can flip to ALL for the true combined figure once NFL/NCAAF ship.
    if len(per_sport) > 1:
        rows.append({
            'surface':'sharp_card','sport':'ALL','record_date':date,
            'wins':all_w,'losses':all_l,'pushes':all_p,
            'units_bet':round(all_bet,2),
            'units_won':round(all_won,2),
            'pick_count':all_w+all_l+all_p,
            'detail':{'legs':all_detail[:50], 'source':'jerry_cache.sharp_card',
                       'sports_included': sorted(per_sport.keys())},
        })
    return rows


def agg_ledger(date: str) -> list[dict]:
    """Ledger — one record per kind (chalk_parlay, teased_*).

    2026-08-22: read from ledger_SNAPSHOTS not ledger_suggestions.
    Snapshots is the graded persistent table (result + unit_pnl written
    by grade_ledger_snapshots.py). Suggestions is the pre-game combo
    proposals — never graded, so agg was always returning empty.

    2026-09-05 ROOT-CAUSE FIX: multiple ledger_snapshots rows exist per
    (date, kind) — one per cron cycle (morning, afternoon, imminent).
    Prior version summed ALL snapshots per kind → 4 chalk_parlay
    snapshots became 4-0 record (n=4). User only ever saw ONE
    chalk_parlay on the app (rank=1 from ledger_suggestions). Real
    record was 1-0 (n=1), not 4-0. Same pattern for teasers.

    Fix: dedup by (kind) keeping only the LATEST snapshotted_at row per
    kind — that mirrors what shipped to users on the ledger tab (which
    reads the latest ledger_suggestions). One snapshot per kind per date.
    """
    rows = requests.get(f'{SB}/rest/v1/ledger_snapshots',
        headers=H_READ,
        params={'game_date': f'eq.{date}',
                'result': 'not.is.null',
                'select': 'kind,result,legs,combined_odds,unit_pnl,snapshotted_at'},
        timeout=15).json()
    if not isinstance(rows, list) or not rows: return []
    # Dedup by kind — keep the LATEST snapshotted_at per kind
    latest_by_kind: dict = {}
    for r in rows:
        k = (r.get('kind') or 'unknown').replace('_combo', '')
        cur = latest_by_kind.get(k)
        ts = r.get('snapshotted_at') or ''
        if cur is None or (ts and ts > (cur.get('snapshotted_at') or '')):
            latest_by_kind[k] = r
    rows = list(latest_by_kind.values())
    # 2026-08-22: collapse by kind so we get ONE record per ledger surface
    # per date (chalk_parlay, teased_spreads, teased_totals, etc), even
    # when multiple snapshots exist for the same kind on one day.
    from collections import defaultdict as _dd
    by_kind = _dd(lambda: {'w': 0, 'l': 0, 'p': 0, 'units_bet': 0.0,
                            'units_won': 0.0, 'legs_all': []})
    for r in rows:
        v = (r.get('result') or '').upper()
        if v not in ('W','L','P'): continue
        stake = 1.0
        odds = r.get('combined_odds')
        pnl = r.get('unit_pnl')
        if pnl is None:
            pnl = stake * _american_payout(odds) if v == 'W' else -stake if v == 'L' else 0
        kind = r.get('kind','unknown').replace('_combo','')
        agg = by_kind[kind]
        agg['units_bet'] += stake
        agg['units_won'] += float(pnl)
        if v == 'W': agg['w'] += 1
        elif v == 'L': agg['l'] += 1
        else: agg['p'] += 1
        agg['legs_all'].append({'legs': r.get('legs') or [], 'odds': odds, 'result': v})

    out = []
    for kind, agg in by_kind.items():
        out.append({
            'surface': f'ledger_{kind}', 'sport': 'MLB', 'record_date': date,
            'wins': agg['w'], 'losses': agg['l'], 'pushes': agg['p'],
            'units_bet': round(agg['units_bet'], 2),
            'units_won': round(agg['units_won'], 2),
            'pick_count': agg['w'] + agg['l'] + agg['p'],
            'detail': {'combos': agg['legs_all']},
        })
    return out


def agg_ladder(date: str) -> dict | None:
    """Ladder — one rung per day."""
    rows = requests.get(f'{SB}/rest/v1/ladder_rung',
        headers=H_READ,
        params={'game_date': f'eq.{date}',
                'select': 'sport,result,pick_side,odds_american,market'},
        timeout=15).json()
    if not isinstance(rows, list) or not rows: return None
    total = defaultdict(lambda: {'w':0,'l':0,'p':0,'units_bet':0.0,'units_won':0.0,'detail':[]})
    for r in rows:
        v = (r.get('result') or '').upper()
        if v not in ('W','L','P','WIN','LOSS','PUSH'): continue
        v = v[:1]
        sport = r.get('sport') or 'MLB'
        stake = 1.0
        odds = r.get('odds_american')
        total[sport]['units_bet'] += stake
        if v == 'W': total[sport]['w']+=1; total[sport]['units_won'] += stake * _american_payout(odds)
        elif v == 'L': total[sport]['l']+=1; total[sport]['units_won'] -= stake
        elif v == 'P': total[sport]['p']+=1
        total[sport]['detail'].append({'pick':r.get('pick_side'),'market':r.get('market'),'verdict':v,'odds':odds})
    if not total: return None
    # Combine into one 'ALL' sport summary + emit
    all_w = sum(s['w'] for s in total.values())
    all_l = sum(s['l'] for s in total.values())
    all_p = sum(s['p'] for s in total.values())
    all_bet = sum(s['units_bet'] for s in total.values())
    all_won = sum(s['units_won'] for s in total.values())
    return {'surface':'ladder','sport':'ALL','record_date':date,
            'wins':all_w,'losses':all_l,'pushes':all_p,
            'units_bet':round(all_bet,2),'units_won':round(all_won,2),
            'pick_count':all_w+all_l+all_p,
            'detail':{sp: v['detail'] for sp, v in total.items()}}


def agg_potd(date: str) -> dict | None:
    # 2026-08-23 fix: POTD data lives in daily_best_bet_history, NOT the
    # long-deprecated play_of_the_day table (404). This agg had been
    # silently returning None for months — POTD never appeared in
    # daily_surface_records. Now reads the correct table + immutable
    # snapshot pattern (has created_at + resolved_at, no updated_at).
    r = requests.get(f'{SB}/rest/v1/daily_best_bet_history',
        headers=H_READ,
        params={'bet_date': f'eq.{date}', 'select': '*',
                'order': 'created_at.desc', 'limit': '1'},
        timeout=10)
    if r.status_code != 200: return None
    rows = r.json()
    if not rows: return None
    row = rows[0]
    v = (row.get('result') or '').upper()[:1]
    if v not in ('W','L','P'): return None
    stake = 2.0
    odds = row.get('odds_american') or row.get('odds')
    units_won = stake * _american_payout(odds) if v == 'W' else -stake if v == 'L' else 0
    return {'surface':'potd','sport':row.get('sport') or 'MLB','record_date':date,
            'wins':1 if v=='W' else 0,'losses':1 if v=='L' else 0,'pushes':1 if v=='P' else 0,
            'units_bet':stake,'units_won':round(units_won,2),'pick_count':1,
            'detail':{'pick':row.get('lean') or row.get('game'),'tier':row.get('sweat_score'),'odds':odds}}


def agg_dawg_of_day(date: str) -> dict | None:
    r = requests.get(f'{SB}/rest/v1/daily_dawg',
        headers=H_READ,
        params={'game_date': f'eq.{date}', 'select': '*', 'limit': '1'},
        timeout=10)
    if r.status_code != 200: return None
    rows = r.json()
    if not rows: return None
    row = rows[0]
    v = (row.get('result') or '').upper()[:1]
    if v not in ('W','L','P'): return None
    stake = 1.0
    odds = row.get('odds')  # dawg has +100 to +250 range
    units_won = stake * _american_payout(odds) if v == 'W' else -stake if v == 'L' else 0
    return {'surface':'dawg_of_day','sport':'MLB','record_date':date,
            'wins':1 if v=='W' else 0,'losses':1 if v=='L' else 0,'pushes':1 if v=='P' else 0,
            'units_bet':stake,'units_won':round(units_won,2),'pick_count':1,
            'detail':{'team':row.get('team'),'odds':odds}}


def agg_daily_degen(date: str) -> dict | None:
    r = requests.get(f'{SB}/rest/v1/daily_degen',
        headers=H_READ,
        params={'game_date': f'eq.{date}', 'select': '*', 'limit': '1'},
        timeout=10)
    if r.status_code != 200: return None
    rows = r.json()
    if not rows: return None
    row = rows[0]
    v = (row.get('result') or '').upper()[:1]
    if v not in ('W','L','P'): return None
    stake = 1.0
    # Combined parlay odds — approximate from legs if not stored
    payout_est = 3.5  # 3-leg parlay typical combined payout
    units_won = stake * payout_est if v == 'W' else -stake if v == 'L' else 0
    return {'surface':'daily_degen','sport':'MULTI','record_date':date,
            'wins':1 if v=='W' else 0,'losses':1 if v=='L' else 0,'pushes':1 if v=='P' else 0,
            'units_bet':stake,'units_won':round(units_won,2),'pick_count':1,
            'detail':{'legs':row.get('legs') or [],'combined_est_payout':payout_est}}


def agg_split(date: str) -> list[dict]:
    """Split — grades sharp-signal flags against game outcomes.

    2026-08-22: builds a historical record for the Split sub-tab so users
    can see if triple-confirmed / confirmed / lean signals actually cash
    when surfaced. Prior: Split showed live signals but no track record —
    users had no way to gauge signal reliability.

    Grades each SHARP_MOVE_* flag by comparing the sharp side against
    the actual market winner (ml/rl/total from mlb_game_results). One
    record per tier level: split_sharp_triple, split_sharp_confirmed,
    split_sharp_lean. -110 assumed vig.
    """
    # 2026-08-22: filter by first_seen_at instead of classified_at.
    # classified_at gets refreshed whenever the classifier reruns, so an
    # 8/21 flag re-classified on 8/22 would be counted on 8/22 not 8/21.
    # first_seen_at is when the movement was detected — the honest date.
    # Multiple filters on same column require PostgREST's and=(...) syntax
    # since dict keys can't repeat.
    and_filter = (f'(classification.like.SHARP_MOVE_*,'
                  f'first_seen_at.gte.{date}T00:00:00,'
                  f'first_seen_at.lt.{date}T23:59:59)')
    r = requests.get(f'{SB}/rest/v1/line_movement_flags',
        headers=H_READ,
        params={'select': 'game_id,sport,market,side,classification',
                'and': and_filter},
        timeout=15).json()
    if not isinstance(r, list) or not r: return []

    # Pull results for these games (MLB only for now; extend as other sports
    # accumulate line-movement history).
    mlb_gids = list({f['game_id'] for f in r if f.get('sport') == 'MLB'})
    if not mlb_gids: return []
    ids_csv = ','.join(f'"{g}"' for g in mlb_gids)
    res = requests.get(f'{SB}/rest/v1/mlb_game_results',
        headers=H_READ,
        params={'game_id': f'in.({ids_csv})',
                'select': 'game_id,home_score,away_score,close_spread,close_total'},
        timeout=15).json()
    res_by_gid = {row['game_id']: row for row in (res if isinstance(res, list) else [])}

    def _sharp_side_won(flag: dict) -> str | None:
        """Return 'W' if the sharp side won its market, 'L' if lost, None if push/unresolved."""
        g = res_by_gid.get(flag['game_id'])
        if not g: return None
        hs, as_ = g.get('home_score'), g.get('away_score')
        if hs is None or as_ is None: return None
        market = str(flag.get('market') or '').lower()
        side = str(flag.get('side') or '').lower()
        if market == 'ml':
            if hs > as_: winner = 'home'
            elif as_ > hs: winner = 'away'
            else: return None
            return 'W' if side == winner else 'L'
        if market == 'rl':
            cs = g.get('close_spread')
            if cs is None: return None
            try: cs = float(cs)
            except: return None
            margin = hs - as_ + cs
            if abs(margin) < 0.01: return None
            home_covers = margin > 0
            return 'W' if (side == 'home' and home_covers) or (side == 'away' and not home_covers) else 'L'
        if market == 'total':
            ct = g.get('close_total')
            if ct is None: return None
            try: ct = float(ct)
            except: return None
            total = hs + as_
            if abs(total - ct) < 0.01: return None
            went_over = total > ct
            return 'W' if (side == 'over' and went_over) or (side == 'under' and not went_over) else 'L'
        return None

    # Bucket by classification tier
    tier_agg = defaultdict(lambda: {'w': 0, 'l': 0})
    for flag in r:
        result = _sharp_side_won(flag)
        if result not in ('W', 'L'): continue
        cls = str(flag.get('classification') or '')
        if 'TRIPLE_CONFIRMED' in cls: bucket = 'triple'
        elif '_CONFIRMED' in cls:      bucket = 'confirmed'
        elif '_LEAN' in cls:           bucket = 'lean'
        else: continue
        tier_agg[bucket][result.lower()] += 1

    # 2026-08-31: quarantine LEAN + CONFIRMED tier buckets. 30d audit
    # showed both hit below breakeven (LEAN 34%, CONFIRMED 42%) and the
    # ensemble + app already stopped surfacing them. Aggregator was
    # still writing daily rows though — keeps polluting the records
    # ledger with signals users no longer see. Only TRIPLE aggregates.
    _PUBLISHED_BUCKETS = {'triple'}

    out = []
    for bucket, agg in tier_agg.items():
        if bucket not in _PUBLISHED_BUCKETS:
            continue
        w, l = agg['w'], agg['l']
        stake = 1.0
        # -110 standard vig (Split signals are always +/-110 range for
        # ML/RL/Total sharp side; approximation for units math)
        units_won = round((w * (100/110)) - l, 2) if (w or l) else 0
        out.append({
            'surface': f'split_sharp_{bucket}', 'sport': 'MLB', 'record_date': date,
            'wins': w, 'losses': l, 'pushes': 0,
            'units_bet': float(w + l), 'units_won': units_won,
            'pick_count': w + l,
            'detail': {'tier': bucket, 'assumed_odds': -110},
        })
    return out


def agg_ncaaf_card(date: str) -> dict | None:
    """NCAAF picks graded (2026-08-30). Reads ncaaf_game_context.primary_play
    + ncaaf_game_results outcomes. Only PRIME/STRONG/LEAN counted.
    Flat -110 payout since NCAAF ctx doesn't store per-side ML close yet.
    """
    ctx = requests.get(f'{SB}/rest/v1/ncaaf_game_context',
        headers=H_READ,
        params={'game_date': f'eq.{date}', 'primary_play': 'not.is.null',
                'select': 'game_id,primary_play'}, timeout=15).json()
    if not (isinstance(ctx, list) and ctx): return None
    gids = ",".join(g['game_id'] for g in ctx if g.get('game_id'))
    if not gids: return None
    res = requests.get(f'{SB}/rest/v1/ncaaf_game_results',
        headers=H_READ,
        params={'game_id': f'in.({gids})',
                'select': 'game_id,home_win,spread_result,total_result'}, timeout=15).json()
    res_map = {r['game_id']: r for r in (res if isinstance(res, list) else []) if r.get('game_id')}

    w = l = p = 0; units_bet = 0.0; units_won = 0.0; detail = []
    for c in ctx:
        pp = c.get('primary_play') or {}
        tier = (pp.get('tier') or '').upper()
        if tier not in ('PRIME', 'STRONG', 'LEAN'): continue
        r = res_map.get(c['game_id'])
        if not r: continue
        ptype = (pp.get('type') or '').lower()
        side  = (pp.get('side') or '').upper()
        v = None
        if ptype == 'ml':
            hw = r.get('home_win')
            if hw is None: continue
            v = 'W' if ((side == 'HOME' and hw) or (side == 'AWAY' and not hw)) else 'L'
        elif ptype in ('rl', 'spread'):
            sr = (r.get('spread_result') or '').lower()
            if sr == 'push': v = 'P'
            elif sr == 'home_covered': v = 'W' if side == 'HOME' else 'L'
            elif sr == 'away_covered': v = 'W' if side == 'AWAY' else 'L'
            else: continue
        elif ptype == 'total':
            tr = (r.get('total_result') or '').lower()
            if tr == 'push': v = 'P'
            elif tr == 'over':  v = 'W' if side == 'OVER' else 'L'
            elif tr == 'under': v = 'W' if side == 'UNDER' else 'L'
            else: continue
        else: continue
        stake = 2.0 if tier in ('PRIME', 'STRONG') else 1.0
        units_bet += stake
        if v == 'W': w += 1; units_won += stake * _american_payout(-110)
        elif v == 'L': l += 1; units_won -= stake
        elif v == 'P': p += 1
        detail.append({'pick': pp.get('label'), 'tier': tier, 'verdict': v, 'stake': stake})

    if not detail: return None
    return {'surface': 'ncaaf_card', 'sport': 'NCAAF', 'record_date': date,
            'wins': w, 'losses': l, 'pushes': p,
            'units_bet': round(units_bet, 2), 'units_won': round(units_won, 2),
            'pick_count': w + l + p,
            'detail': {'legs': detail[:50]}}


AGGREGATORS = [
    ('sharp_card', agg_sharp_card),
    ('ledger', agg_ledger),        # returns LIST
    ('ladder', agg_ladder),
    ('potd', agg_potd),
    ('dawg_of_day', agg_dawg_of_day),
    ('daily_degen', agg_daily_degen),
    ('split', agg_split),          # returns LIST — 2026-08-22
    ('ncaaf_card', agg_ncaaf_card), # 2026-08-30 — NCAAF picks graded
]


def write_record(rec: dict, dry_run: bool = False) -> None:
    if dry_run:
        icon = '✓' if rec['units_won'] > 0 else '✗' if rec['units_won'] < 0 else '='
        print(f'  {icon} {rec["surface"]:22s} {rec["sport"]:5s} {rec["wins"]}-{rec["losses"]}-{rec["pushes"]} units {rec["units_won"]:+.2f}u [DRY]')
        return
    # 2026-08-22: delete-then-insert to keep this idempotent. Table has no
    # unique constraint on (surface, sport, record_date) so repeated cron
    # runs would otherwise stack duplicate rows and inflate the record.
    # Delete existing row for the key, then insert fresh — simpler than
    # a PATCH+POST dance and keeps computed_at reflecting the latest run.
    try:
        del_url = (f'{SB}/rest/v1/daily_surface_records'
                   f'?surface=eq.{rec["surface"]}'
                   f'&sport=eq.{rec["sport"]}'
                   f'&record_date=eq.{rec["record_date"]}')
        requests.delete(del_url, headers=H_WRITE, timeout=10)
    except Exception:
        pass  # best-effort — insert will still work, may just create dup
    payload = {**rec, 'computed_at': datetime.now(timezone.utc).isoformat()}
    r = requests.post(f'{SB}/rest/v1/daily_surface_records',
        headers=H_WRITE, json=payload, timeout=10)
    if r.status_code not in (200, 201, 204):
        print(f'    ✗ write failed {r.status_code}: {r.text[:120]}')
        return
    icon = '✓' if rec['units_won'] > 0 else '✗' if rec['units_won'] < 0 else '='
    print(f'  {icon} {rec["surface"]:22s} {rec["sport"]:5s} {rec["wins"]}-{rec["losses"]}-{rec["pushes"]} units {rec["units_won"]:+.2f}u')


def run_date(date: str, dry_run: bool = False) -> int:
    print(f'\n=== aggregate_daily_records · {date} · dry={dry_run} ===')
    total = 0
    for name, fn in AGGREGATORS:
        try:
            result = fn(date)
        except Exception as e:
            print(f'  ⚠ {name}: {type(e).__name__}: {e}')
            continue
        if result is None: continue
        if isinstance(result, list):
            for r in result:
                write_record(r, dry_run=dry_run); total += 1
        else:
            write_record(result, dry_run=dry_run); total += 1
    return total


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: yesterday ET)')
    p.add_argument('--backfill', type=int, help='Backfill last N days')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    if args.backfill:
        end = datetime.strptime(args.date, '%Y-%m-%d').date() if args.date else \
              (datetime.now(timezone.utc) - timedelta(hours=4)).date() - timedelta(days=1)
        total = 0
        for i in range(args.backfill):
            d = (end - timedelta(days=i)).isoformat()
            total += run_date(d, dry_run=args.dry_run)
        print(f'\ntotal records written: {total}')
    else:
        d = args.date or _et_yesterday()
        n = run_date(d, dry_run=args.dry_run)
        print(f'\n{n} records written')


if __name__ == '__main__':
    main()
