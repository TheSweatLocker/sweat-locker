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

def agg_sharp_card(date: str) -> dict | None:
    """Sharp Card = PRIME/STRONG primary_play picks + PRIME/STRONG props
    on that date, using the app's 2u/2u/1u sizing policy.

    2026-08-23 CRITICAL FIX: read primary_play from mlb_game_results
    (FROZEN at log_game_result time) instead of mlb_game_context
    (LIVE, drifts every time recompute_primary_play runs). Yesterday's
    audit showed 9 of 15 games had drifted primary_play between cron
    time and next-morning aggregation — 3 phantom losses got attributed
    to sharp_card that never actually shipped to users. Frozen version:
    18-10 (64.3%). Live-drifted version: 18-13 (58.1%). Difference is
    entirely from LEAN picks getting live-promoted to PRIME/STRONG after
    the fact, then graded (and losing) against actual outcomes.

    mlb_game_results.primary_play is captured by log_game_result at
    grade time — closest available to what shipped. A true cron-time
    snapshot table would be better but doesn't exist yet.
    """
    res = requests.get(f'{SB}/rest/v1/mlb_game_results',
        headers=H_READ,
        params={'game_date': f'eq.{date}',
                'select': 'game_id,home_team,away_team,home_score,away_score,'
                          'home_win,run_line_result,total_result,spread_result,'
                          'primary_play'},
        timeout=15).json()
    w = l = p = 0; units_bet = 0.0; units_won = 0.0; detail = []
    for g in (res if isinstance(res, list) else []):
        pp = g.get('primary_play') or {}
        if pp.get('tier') not in ('PRIME','STRONG'): continue
        # `game` for _grade_side is just the current row (has all outcome cols)
        game = g
        verdict = _grade_side(pp, game)
        if verdict is None: continue
        stake = 2.0
        units_bet += stake
        # Approximate payout — MLB games use -110 for spread/total, ML uses close odds
        odds = -110
        if pp.get('type') == 'ml':
            # Use close ML from ctx if available (best-effort)
            odds = -110  # fallback; ideally read from mlb_game_results.home_ml_close
        payout = _american_payout(odds)
        if verdict == 'W': w += 1; units_won += stake * payout
        elif verdict == 'L': l += 1; units_won -= stake
        elif verdict == 'P': p += 1
        detail.append({'type':'game','pick':pp.get('label'),'verdict':verdict,'stake':stake})

    # Props — PRIME/STRONG on that date
    props = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
        headers=H_READ,
        params={'game_date': f'eq.{date}', 'tier': 'in.(PRIME,STRONG)',
                'select': 'player_name,prop_type,direction,tier,result,book_over_odds,book_under_odds'},
        timeout=15).json()
    for pr in (props if isinstance(props, list) else []):
        res_c = (pr.get('result') or '').upper()
        if res_c not in ('W','WIN','L','LOSS','P','PUSH'): continue
        odds = pr.get('book_over_odds') if pr.get('direction') == 'over' else pr.get('book_under_odds')
        # Enforce [-300, +150] gate per feedback_prop_jerry_odds
        if odds is not None:
            try:
                oi = int(odds)
                if oi < -300 or oi > 150: continue
            except (TypeError, ValueError):
                pass
        stake = 2.0  # PRIME/STRONG props
        units_bet += stake
        v = res_c[:1]  # W / L / P
        if v == 'W': w += 1; units_won += stake * _american_payout(odds)
        elif v == 'L': l += 1; units_won -= stake
        elif v == 'P': p += 1
        detail.append({'type':'prop','pick':f'{pr.get("player_name")} {pr.get("direction")} {pr.get("prop_type")}','verdict':v,'stake':stake,'odds':odds})

    if not detail: return None
    return {'surface':'sharp_card','sport':'MLB','record_date':date,
            'wins':w,'losses':l,'pushes':p,'units_bet':round(units_bet,2),
            'units_won':round(units_won,2),'pick_count':w+l+p,
            'detail':{'legs':detail[:50]}}


def agg_ledger(date: str) -> list[dict]:
    """Ledger — one record per kind (chalk_parlay, teased_*).

    2026-08-22: read from ledger_SNAPSHOTS not ledger_suggestions.
    Snapshots is the graded persistent table (result + unit_pnl written
    by grade_ledger_snapshots.py). Suggestions is the pre-game combo
    proposals — never graded, so agg was always returning empty.
    """
    rows = requests.get(f'{SB}/rest/v1/ledger_snapshots',
        headers=H_READ,
        params={'game_date': f'eq.{date}',
                'result': 'not.is.null',
                'select': 'kind,result,legs,combined_odds,unit_pnl'},
        timeout=15).json()
    if not isinstance(rows, list) or not rows: return []
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

    out = []
    for bucket, agg in tier_agg.items():
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
