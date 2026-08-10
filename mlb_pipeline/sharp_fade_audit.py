"""Sharp fade audit trail writer + self-calibration engine (2026-08-09).

Two responsibilities:

  1. WRITE per-game audit rows to sharp_fade_audit_trail table.
     Called by sharp_pattern_dashboard at pick-time AND by nightly
     backfill for outcomes.

  2. SELF-CALIBRATE rule thresholds.
     Reads the audit trail, computes empirical hit rates per rule,
     updates jerry_cache['sharp_fade_rules_stats'] with fresh numbers
     that sharp_fade_rules._get_rule_mode() reads at runtime.

     Baseline calibration:
       - n_min: max(10, 30) — enough for 95% CI to distinguish 40% from 50%
       - life_ceiling: aligned-bucket hit% - 5pp (fade cap must beat baseline)
       - kill_switch: aligned-bucket hit% + 5pp
     These auto-adjust as market baseline shifts.

CLI:
    python sharp_fade_audit.py --write-today [--date YYYY-MM-DD] [--sport MLB]
    python sharp_fade_audit.py --backfill-results [--date YYYY-MM-DD]
    python sharp_fade_audit.py --calibrate
    python sharp_fade_audit.py --backfill-historical [--sport MLB]  # replay all snapshots
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

sys.path.insert(0, str(Path(__file__).parent))
from sharp_fade_rules import compute_fade_context as _compute_fade_context_mlb, ALL_RULES, RULE_META
from sharp_fade_flag import compute_sharp_fade_flag
try:
    from nfl_sharp_fade_rules import compute_fade_context as _compute_fade_context_nfl
except ImportError:
    _compute_fade_context_nfl = None
try:
    from ncaaf_sharp_fade_rules import compute_fade_context as _compute_fade_context_ncaaf
except ImportError:
    _compute_fade_context_ncaaf = None


def compute_fade_context(ctx, market, side, sport='MLB'):
    """Sport-router for fade rule engines."""
    if sport == 'NFL' and _compute_fade_context_nfl:
        return _compute_fade_context_nfl(ctx, market, side)
    if sport == 'NCAAF' and _compute_fade_context_ncaaf:
        return _compute_fade_context_ncaaf(ctx, market, side)
    return _compute_fade_context_mlb(ctx, market, side)


def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


# ==================================================================
# WRITE: audit rows for today's picks
# ==================================================================

def _upsert_audit(rows):
    """Idempotent upsert into sharp_fade_audit_trail using UNIQUE(sport, game_id)."""
    if not rows: return 0
    written = 0
    for row in rows:
        # Try INSERT first; if 23505 (duplicate), do PATCH
        wr = requests.post(f'{SB}/rest/v1/sharp_fade_audit_trail',
                           headers=H_W, data=json.dumps(row, default=str), timeout=15)
        if wr.status_code in (200, 201, 204):
            written += 1
        elif wr.status_code == 409:
            # duplicate — patch existing row (except id)
            filt = f'sport=eq.{row["sport"]}&game_id=eq.{row["game_id"]}'
            patch = {k: v for k, v in row.items() if k != 'id'}
            pr = requests.patch(f'{SB}/rest/v1/sharp_fade_audit_trail?{filt}',
                                headers=H_W, data=json.dumps(patch, default=str), timeout=15)
            if pr.status_code in (200, 204): written += 1
            else: print(f'  ⚠ patch {pr.status_code}: {pr.text[:150]}')
        else:
            print(f'  ⚠ insert {wr.status_code}: {wr.text[:150]}')
    return written


def write_today_mlb(date: str):
    ctx = requests.get(f'{SB}/rest/v1/mlb_game_context', headers=H,
        params={'game_date': f'eq.{date}', 'select': '*'}, timeout=15).json()
    if not isinstance(ctx, list): return 0
    jr = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H,
        params={'game_date': f'eq.{date}', 'sport': 'eq.MLB',
                'select': 'game_id,call_market,call_side,call_line,conviction'}, timeout=15).json()
    jr_map = {j['game_id']: j for j in (jr if isinstance(jr, list) else [])}
    now = datetime.now(timezone.utc).isoformat()

    rows = []
    for g in ctx:
        gid = g.get('game_id')
        if not gid: continue
        j = jr_map.get(gid, {})
        pick_market = (j.get('call_market') or '').lower()
        pick_side   = (j.get('call_side') or '').upper() or None
        if pick_market not in ('ml','total') or not pick_side: continue

        # Rules + bucket
        fade_ctx = compute_fade_context(g, pick_market, pick_side)
        oc = g.get('oddscrowd_snapshot')
        if isinstance(oc, str):
            try: oc = json.loads(oc)
            except: oc = {}
        oc = oc or {}
        bucket_flag = compute_sharp_fade_flag(oc, pick_market, pick_side)

        # Determine cap (rules take priority over bucket)
        cap = fade_ctx.get('cap_directive')
        if not cap and bucket_flag and bucket_flag.get('cap_directive'):
            cap = bucket_flag['cap_directive']
        conv_pre = j.get('conviction')
        conv_after = 55 if cap == 'CAP_TO_LEAN_55' else (49 if cap == 'CAP_TO_READ_49' else conv_pre)
        if conv_pre is not None and conv_after is not None and conv_after > conv_pre:
            conv_after = conv_pre

        ml_b = oc.get('ml') or {}
        total_b = oc.get('total') or {}
        row = {
            'sport': 'MLB', 'game_id': gid, 'game_date': date,
            'matchup': f'{g["away_team"]} @ {g["home_team"]}',
            'computed_at': now,
            'pick_source': 'jerry',
            'pick_market': pick_market, 'pick_side': pick_side,
            'pick_line': j.get('call_line'),
            'pick_conviction_pre_cap': conv_pre,
            'pick_conviction_after_cap': conv_after,
            'sharp_ml_pick':   ml_b.get('pick'),
            'sharp_ml_div':    ml_b.get('div'),
            'sharp_ml_money':  ml_b.get('money'),
            'sharp_ml_bets':   ml_b.get('bets'),
            'sharp_total_pick':  total_b.get('pick'),
            'sharp_total_div':   total_b.get('div'),
            'sharp_total_money': total_b.get('money'),
            'sharp_total_bets':  total_b.get('bets'),
            'rules_triggered':  fade_ctx.get('triggers', []),
            'active_rules_count': fade_ctx.get('active_count', 0),
            'cap_directive': cap,
            'bucket_flag': bucket_flag,
        }
        rows.append(row)
    n = _upsert_audit(rows)
    print(f'  wrote {n} audit rows for {date} MLB')
    return n


def backfill_results(date: str = None):
    """Fill actual outcomes onto audit rows for games that finished."""
    params = [('resolved_at', 'is.null'), ('select', '*')]
    if date: params.append(('game_date', f'eq.{date}'))
    r = requests.get(f'{SB}/rest/v1/sharp_fade_audit_trail', headers=H,
                     params=params, timeout=30)
    audit_rows = r.json() if isinstance(r.json(), list) else []
    if not audit_rows:
        print('  no unresolved audit rows'); return 0

    # 2026-08-09 Phase 2: route sport → results table
    results_map = {}
    for sport, table in (('MLB','mlb_game_results'), ('NFL','nfl_game_results'),
                          ('NCAAF','ncaaf_game_results')):
        ids = [row['game_id'] for row in audit_rows if row['sport'] == sport]
        for i in range(0, len(ids), 100):
            chunk = ids[i:i+100]
            rr = requests.get(f'{SB}/rest/v1/{table}', headers=H,
                params=[('game_id', f'in.({",".join(chunk)})'),
                        ('select','game_id,home_score,away_score,total_result')], timeout=15)
            for row in (rr.json() if isinstance(rr.json(),list) else []):
                h, a = row.get('home_score'), row.get('away_score')
                if h is None or a is None: continue
                row['ml_winner'] = 'HOME' if h > a else ('AWAY' if a > h else 'PUSH')
                results_map[row['game_id']] = row

    updated = 0
    now = datetime.now(timezone.utc).isoformat()
    for arow in audit_rows:
        res = results_map.get(arow['game_id'])
        if not res: continue
        actual_ml = res.get('ml_winner')
        actual_tot = (res.get('total_result') or '').upper()
        # Determine pick_won
        pm = arow.get('pick_market'); ps = arow.get('pick_side')
        pick_won = None
        if pm == 'ml' and actual_ml and actual_ml != 'PUSH':
            pick_won = (ps == actual_ml)
        elif pm == 'total' and actual_tot in ('OVER','UNDER'):
            pick_won = (ps == actual_tot)
        # Was the cap correct?
        cap_correct = None
        if arow.get('cap_directive') and pick_won is not None:
            # Cap was right if pick lost (we softened a losing bet)
            cap_correct = not pick_won
        # Sharp win checks
        ml_sw = None; tot_sw = None
        if arow.get('sharp_ml_pick') and actual_ml and actual_ml != 'PUSH':
            ml_sw = (arow['sharp_ml_pick'] == actual_ml)
        if arow.get('sharp_total_pick') and actual_tot in ('OVER','UNDER'):
            tot_sw = (arow['sharp_total_pick'] == actual_tot)
        patch = {
            'actual_ml_winner': actual_ml,
            'actual_total_result': actual_tot or None,
            'actual_home_score': res.get('home_score'),
            'actual_away_score': res.get('away_score'),
            'ml_sharp_won': ml_sw,
            'total_sharp_won': tot_sw,
            'pick_won': pick_won,
            'cap_was_correct': cap_correct,
            'resolved_at': now,
        }
        pr = requests.patch(f'{SB}/rest/v1/sharp_fade_audit_trail?id=eq.{arow["id"]}',
                            headers=H_W, data=json.dumps(patch, default=str), timeout=10)
        if pr.status_code in (200, 204): updated += 1
    print(f'  backfilled {updated}/{len(audit_rows)} audit rows with outcomes')
    return updated


# ==================================================================
# SELF-CALIBRATION
# ==================================================================

def calibrate():
    """Auto-tune rule THRESHOLDS from audit trail baseline.

    Rule STRENGTH stats (per-rule n and hit%) stay authoritative from
    sharp_fade_rules_stats.py (which replays every snapshot regardless
    of whether Jerry picked). That's the correct broader sample for
    "how strong is this pattern?"

    Calibrate() only updates the THRESHOLDS applied to those stats:
      - LIFE_CEILING = baseline - 5pp  (fade cap must beat baseline)
      - KILL_SWITCH  = baseline + 5pp  (rule auto-DISABLED if sharp
                                         wins above this)
      - Baseline is our own aggregate pick hit rate from audit trail
        (currently 61.8% MLB — Jerry win% overall)

    Then merges calibrated thresholds into the per-rule payload written
    by sharp_fade_rules_stats.py, so runtime lookup gets both.
    """
    # 1) Baseline from audit trail
    r = requests.get(f'{SB}/rest/v1/sharp_fade_audit_trail', headers=H,
        params=[('resolved_at', 'not.is.null'), ('pick_won','not.is.null'),
                ('select','pick_won,sport'), ('limit','5000')], timeout=30)
    rows = r.json() if isinstance(r.json(), list) else []
    if not rows:
        print('  no resolved audit rows — using default thresholds (baseline=52.4%)')
        market_baseline_pct = 52.4  # -110 breakeven default
    else:
        hits = [x['pick_won'] for x in rows]
        market_baseline_pct = round(100 * sum(hits) / max(len(hits), 1), 1)
        print(f'  market baseline from audit ({len(rows)} rows): {market_baseline_pct}%')

    LIFE_CEILING = round(market_baseline_pct - 5, 1)
    KILL_SWITCH = round(market_baseline_pct + 5, 1)
    print(f'  → life_ceiling={LIFE_CEILING}% · kill_switch={KILL_SWITCH}%')

    # 2) Read authoritative per-rule stats from sharp_fade_rules_stats output
    stats_r = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H,
        params={'cache_key': 'eq.sharp_fade_rules_stats',
                'game_id': 'eq.GLOBAL_RULES',
                'select': 'data'}, timeout=10)
    stats_rows = stats_r.json() if isinstance(stats_r.json(), list) else []
    rule_stats = stats_rows[0]['data'] if stats_rows else {}

    # 3) Merge: keep rule stats + inject calibrated mode per rule
    calibrated = {}
    for rname, s in rule_stats.items():
        n = s.get('n', 0)
        pct = s.get('lifetime_hit_pct', 100.0)
        recent_pct = s.get('recent_hit_pct')
        meta = RULE_META.get(rname, {})
        min_n = meta.get('n_min', 10)
        default_mode = meta.get('default_mode', 'LOG')

        # Determine mode based on calibrated thresholds
        if n < min_n:
            mode, why = 'LOG', f'n<{min_n}'
        elif recent_pct is not None and recent_pct >= KILL_SWITCH:
            mode, why = 'DISABLED', f'recent {recent_pct}% ≥ kill_switch {KILL_SWITCH}% (fade dead)'
        elif pct >= KILL_SWITCH:
            mode, why = 'DISABLED', f'lifetime {pct}% ≥ kill_switch {KILL_SWITCH}%'
        elif pct < LIFE_CEILING:
            mode, why = default_mode, f'fade edge ({pct}% < {LIFE_CEILING}%)'
        else:
            mode, why = 'LOG', f'in neutral zone ({pct}% between {LIFE_CEILING} and {KILL_SWITCH})'

        calibrated[rname] = {
            **s,
            'auto_calibrated_mode': mode,
            'auto_calibrated_why': why,
            'thresholds_used': {'life_ceiling': LIFE_CEILING,
                                 'kill_switch': KILL_SWITCH, 'n_min': min_n},
            'market_baseline_pct': market_baseline_pct,
        }

    print(f'\n=== RULE MODES (calibrated against baseline {market_baseline_pct}%) ===')
    print(f'{"RULE":30s} {"N":>4s} {"HIT%":>6s} {"MODE":>10s}  {"WHY":s}')
    print('-' * 100)
    for rname in sorted(calibrated.keys()):
        s = calibrated[rname]
        print(f'  {rname:28s} {s.get("n",0):>4d} {s.get("lifetime_hit_pct",0):>5.1f}%  '
              f'{s["auto_calibrated_mode"]:>10s}  {s["auto_calibrated_why"]}')

    # Write merged payload back to jerry_cache
    requests.delete(f'{SB}/rest/v1/jerry_cache?cache_key=eq.sharp_fade_rules_stats&game_id=eq.GLOBAL_RULES',
                    headers=H_W, timeout=10)
    wr = requests.post(f'{SB}/rest/v1/jerry_cache', headers=H_W,
        data=json.dumps({'cache_key': 'sharp_fade_rules_stats',
                          'game_id': 'GLOBAL_RULES', 'sport': 'MLB',
                          'narrative': f'Auto-calibrated (baseline {market_baseline_pct}%, {len(calibrated)} rules, {len(rows)} audit rows)',
                          'data': calibrated,
                          'fetched_at': datetime.now(timezone.utc).isoformat()},
                         default=str), timeout=15)
    if wr.status_code in (200, 201, 204):
        print(f'\n✓ calibrated stats written to jerry_cache["sharp_fade_rules_stats"]')
    else:
        print(f'\n⚠ write failed {wr.status_code}: {wr.text[:200]}')


# ==================================================================
# BACKFILL: replay historical snapshots
# ==================================================================

def backfill_historical_mlb(sport='MLB', limit_days=30):
    """Replay every historical snapshot × jerry_read into audit table."""
    since = (datetime.now(timezone.utc) - timedelta(days=limit_days)).strftime('%Y-%m-%d')
    r = requests.get(f'{SB}/rest/v1/mlb_game_context_snapshots', headers=H,
        params=[('snapshot_date', f'gte.{since}'), ('select','*'),
                ('limit','5000')], timeout=30)
    snaps = r.json() if isinstance(r.json(),list) else []
    # Dedup by game_id (latest snap per game)
    by_gid = {}
    for s in snaps:
        prev = by_gid.get(s['game_id'])
        if not prev or s['snapshot_date'] > prev['snapshot_date']:
            by_gid[s['game_id']] = s
    snaps = list(by_gid.values())
    print(f'  {len(snaps)} unique games to replay from {since}')

    # Pull Jerry reads for the same date range
    jr = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H,
        params=[('game_date', f'gte.{since}'), ('sport', 'eq.MLB'),
                ('select','game_id,game_date,call_market,call_side,call_line,conviction')], timeout=30).json()
    jr_map = {j['game_id']: j for j in (jr if isinstance(jr, list) else [])}

    # Results
    gids = list(by_gid.keys())
    results = {}
    for i in range(0,len(gids),100):
        chunk = gids[i:i+100]
        rr = requests.get(f'{SB}/rest/v1/mlb_game_results', headers=H,
            params=[('game_id',f'in.({",".join(chunk)})'),
                    ('select','game_id,game_date,home_team,away_team,home_score,away_score,total_result')],
            timeout=15)
        for row in (rr.json() if isinstance(rr.json(),list) else []):
            h,a = row.get('home_score'), row.get('away_score')
            if h is None or a is None: continue
            row['ml_winner'] = 'HOME' if h > a else ('AWAY' if a > h else 'PUSH')
            results[row['game_id']] = row

    rows_to_write = []
    for s in snaps:
        gid = s['game_id']
        j = jr_map.get(gid, {})
        pick_market = (j.get('call_market') or '').lower()
        pick_side   = (j.get('call_side') or '').upper() or None
        if pick_market not in ('ml','total') or not pick_side: continue
        res = results.get(gid)
        if not res: continue

        # Build ctx from snap
        ctx = dict(s)
        ctx['home_team'] = res.get('home_team')
        ctx['away_team'] = res.get('away_team')

        oc = s.get('oddscrowd_snapshot')
        if isinstance(oc, str):
            try: oc = json.loads(oc)
            except: oc = {}
        ctx['oddscrowd_snapshot'] = oc or {}

        fade_ctx = compute_fade_context(ctx, pick_market, pick_side)
        bucket_flag = compute_sharp_fade_flag(oc or {}, pick_market, pick_side)
        cap = fade_ctx.get('cap_directive')
        if not cap and bucket_flag and bucket_flag.get('cap_directive'):
            cap = bucket_flag['cap_directive']
        conv_pre = j.get('conviction')
        conv_after = 55 if cap == 'CAP_TO_LEAN_55' else (49 if cap == 'CAP_TO_READ_49' else conv_pre)
        if conv_pre is not None and conv_after is not None and conv_after > conv_pre:
            conv_after = conv_pre

        # Outcome
        actual_ml = res.get('ml_winner')
        actual_tot = (res.get('total_result') or '').upper()
        pick_won = None
        if pick_market == 'ml' and actual_ml and actual_ml != 'PUSH':
            pick_won = (pick_side == actual_ml)
        elif pick_market == 'total' and actual_tot in ('OVER','UNDER'):
            pick_won = (pick_side == actual_tot)
        cap_correct = None
        if cap and pick_won is not None: cap_correct = not pick_won

        ml_b = (oc or {}).get('ml') or {}
        total_b = (oc or {}).get('total') or {}
        rows_to_write.append({
            'sport': 'MLB', 'game_id': gid, 'game_date': res.get('game_date') or j.get('game_date'),
            'matchup': f'{res["away_team"]} @ {res["home_team"]}',
            'computed_at': datetime.now(timezone.utc).isoformat(),
            'pick_source': 'jerry',
            'pick_market': pick_market, 'pick_side': pick_side,
            'pick_line': j.get('call_line'),
            'pick_conviction_pre_cap': conv_pre,
            'pick_conviction_after_cap': conv_after,
            'sharp_ml_pick': ml_b.get('pick'), 'sharp_ml_div': ml_b.get('div'),
            'sharp_ml_money': ml_b.get('money'), 'sharp_ml_bets': ml_b.get('bets'),
            'sharp_total_pick': total_b.get('pick'), 'sharp_total_div': total_b.get('div'),
            'sharp_total_money': total_b.get('money'), 'sharp_total_bets': total_b.get('bets'),
            'rules_triggered': fade_ctx.get('triggers', []),
            'active_rules_count': fade_ctx.get('active_count', 0),
            'cap_directive': cap,
            'bucket_flag': bucket_flag,
            'actual_ml_winner': actual_ml,
            'actual_total_result': actual_tot or None,
            'actual_home_score': res.get('home_score'),
            'actual_away_score': res.get('away_score'),
            'pick_won': pick_won,
            'cap_was_correct': cap_correct,
            'resolved_at': datetime.now(timezone.utc).isoformat(),
        })
    n = _upsert_audit(rows_to_write)
    print(f'  wrote {n} historical audit rows')
    return n


def write_today_ncaaf(date: str):
    """Same pattern as MLB/NFL — read ncaaf_game_context + jerry_reads sport=NCAAF
    and emit audit rows per pick."""
    ctx = requests.get(f'{SB}/rest/v1/ncaaf_game_context', headers=H_READ,
        params={'game_date': f'eq.{date}', 'select': '*'}, timeout=15).json()
    if not isinstance(ctx, list): return 0
    jr = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H_READ,
        params={'game_date': f'eq.{date}', 'sport': 'eq.NCAAF',
                'select': 'game_id,call_market,call_side,call_line,conviction'}, timeout=15).json()
    jr_map = {j['game_id']: j for j in (jr if isinstance(jr, list) else [])}
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for g in ctx:
        gid = g.get('game_id')
        if not gid: continue
        j = jr_map.get(gid, {})
        pick_market = (j.get('call_market') or '').lower()
        pick_side = (j.get('call_side') or '').upper() or None
        if pick_market not in ('ml','total','spread') or not pick_side: continue
        fade_ctx = compute_fade_context(g, pick_market, pick_side, sport='NCAAF')
        oc = g.get('oddscrowd_snapshot')
        if isinstance(oc, str):
            try: oc = json.loads(oc)
            except: oc = {}
        oc = oc or {}
        try:
            bucket_flag = compute_sharp_fade_flag(oc, pick_market, pick_side)
        except Exception:
            bucket_flag = None
        cap = fade_ctx.get('cap_directive')
        if not cap and bucket_flag and bucket_flag.get('cap_directive'):
            cap = bucket_flag['cap_directive']
        conv_pre = j.get('conviction')
        conv_after = 55 if cap == 'CAP_TO_LEAN_55' else (49 if cap == 'CAP_TO_READ_49' else conv_pre)
        if conv_pre is not None and conv_after is not None and conv_after > conv_pre:
            conv_after = conv_pre
        ml_b = oc.get('ml') or {}
        total_b = oc.get('total') or {}
        row = {
            'sport': 'NCAAF', 'game_id': gid, 'game_date': date,
            'matchup': f'{g.get("away_team","?")} @ {g.get("home_team","?")}',
            'computed_at': now,
            'pick_source': 'jerry',
            'pick_market': pick_market, 'pick_side': pick_side,
            'pick_line': j.get('call_line'),
            'pick_conviction_pre_cap': conv_pre,
            'pick_conviction_after_cap': conv_after,
            'sharp_ml_pick': ml_b.get('pick'), 'sharp_ml_div': ml_b.get('div'),
            'sharp_ml_money': ml_b.get('money'), 'sharp_ml_bets': ml_b.get('bets'),
            'sharp_total_pick': total_b.get('pick'), 'sharp_total_div': total_b.get('div'),
            'sharp_total_money': total_b.get('money'), 'sharp_total_bets': total_b.get('bets'),
            'rules_triggered': fade_ctx.get('triggers', []),
            'active_rules_count': fade_ctx.get('active_count', 0),
            'cap_directive': cap,
            'bucket_flag': bucket_flag,
        }
        rows.append(row)
    n = _upsert_audit(rows)
    print(f'  wrote {n} NCAAF audit rows for {date}')
    return n


def write_today_nfl(date: str):
    """2026-08-09: analog of write_today_mlb for NFL. Reads nfl_game_context
    (has oddscrowd_snapshot + models + primary_play) + jerry_reads (sport=NFL
    written by generate_nfl_game_reads.py). Emits one audit row per NFL pick."""
    ctx = requests.get(f'{SB}/rest/v1/nfl_game_context', headers=H_READ,
        params={'game_date': f'eq.{date}', 'select': '*'}, timeout=15).json()
    if not isinstance(ctx, list): return 0
    jr = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H_READ,
        params={'game_date': f'eq.{date}', 'sport': 'eq.NFL',
                'select': 'game_id,call_market,call_side,call_line,conviction'}, timeout=15).json()
    jr_map = {j['game_id']: j for j in (jr if isinstance(jr, list) else [])}
    now = datetime.now(timezone.utc).isoformat()

    rows = []
    for g in ctx:
        gid = g.get('game_id')
        if not gid: continue
        j = jr_map.get(gid, {})
        pick_market = (j.get('call_market') or '').lower()
        pick_side = (j.get('call_side') or '').upper() or None
        if pick_market not in ('ml', 'total', 'spread') or not pick_side: continue

        fade_ctx = compute_fade_context(g, pick_market, pick_side, sport='NFL')
        oc = g.get('oddscrowd_snapshot')
        if isinstance(oc, str):
            try: oc = json.loads(oc)
            except: oc = {}
        oc = oc or {}
        # Bucket flag (MLB helper works generically on any oddscrowd shape)
        try:
            bucket_flag = compute_sharp_fade_flag(oc, pick_market, pick_side)
        except Exception:
            bucket_flag = None

        cap = fade_ctx.get('cap_directive')
        if not cap and bucket_flag and bucket_flag.get('cap_directive'):
            cap = bucket_flag['cap_directive']
        conv_pre = j.get('conviction')
        conv_after = 55 if cap == 'CAP_TO_LEAN_55' else (49 if cap == 'CAP_TO_READ_49' else conv_pre)
        if conv_pre is not None and conv_after is not None and conv_after > conv_pre:
            conv_after = conv_pre

        ml_b = oc.get('ml') or {}
        total_b = oc.get('total') or {}
        row = {
            'sport': 'NFL', 'game_id': gid, 'game_date': date,
            'matchup': f'{g.get("away_team","?")} @ {g.get("home_team","?")}',
            'computed_at': now,
            'pick_source': 'jerry',
            'pick_market': pick_market, 'pick_side': pick_side,
            'pick_line': j.get('call_line'),
            'pick_conviction_pre_cap': conv_pre,
            'pick_conviction_after_cap': conv_after,
            'sharp_ml_pick': ml_b.get('pick'), 'sharp_ml_div': ml_b.get('div'),
            'sharp_ml_money': ml_b.get('money'), 'sharp_ml_bets': ml_b.get('bets'),
            'sharp_total_pick': total_b.get('pick'), 'sharp_total_div': total_b.get('div'),
            'sharp_total_money': total_b.get('money'), 'sharp_total_bets': total_b.get('bets'),
            'rules_triggered': fade_ctx.get('triggers', []),
            'active_rules_count': fade_ctx.get('active_count', 0),
            'cap_directive': cap,
            'bucket_flag': bucket_flag,
        }
        rows.append(row)
    n = _upsert_audit(rows)
    print(f'  wrote {n} NFL audit rows for {date}')
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write-today', action='store_true')
    ap.add_argument('--backfill-results', action='store_true')
    ap.add_argument('--calibrate', action='store_true')
    ap.add_argument('--backfill-historical', action='store_true')
    ap.add_argument('--date', default=None)
    ap.add_argument('--sport', default='MLB')
    ap.add_argument('--days', type=int, default=30)
    args = ap.parse_args()

    date = args.date or today_et()
    if args.write_today:
        if args.sport == 'MLB':
            write_today_mlb(date)
        elif args.sport == 'NFL':
            write_today_nfl(date)
        elif args.sport == 'NCAAF':
            write_today_ncaaf(date)
        else:
            print(f'  sport {args.sport} not yet supported')
    elif args.backfill_results:
        backfill_results(date=args.date)
    elif args.calibrate:
        calibrate()
    elif args.backfill_historical:
        backfill_historical_mlb(sport=args.sport, limit_days=args.days)
    else:
        print('specify --write-today, --backfill-results, --calibrate, or --backfill-historical')


if __name__ == '__main__':
    main()
