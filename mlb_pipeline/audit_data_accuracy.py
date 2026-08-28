"""_audit_data_accuracy.py — full-app data accuracy sweep (2026-08-27).

18 checks across 6 categories. Each check emits {check_id, category,
severity, status, detail}. Output is human-readable + machine-usable.

Scope: MLB, last 14 days unless the check is inherently lifetime.
Severity:
  HIGH   — wrong numbers visible in app
  MED    — internal drift / stale rollup
  LOW    — cosmetic / edge case

Run: python mlb_pipeline/_audit_data_accuracy.py
"""
from __future__ import annotations
import os, sys, datetime as dt
from collections import defaultdict
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests
SB  = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H   = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

TODAY  = dt.date.today()
D14    = (TODAY - dt.timedelta(days=14)).isoformat()
EPOCH  = '2026-08-20'
YEST   = (TODAY - dt.timedelta(days=1)).isoformat()

findings: list[dict] = []

def get(url, **kwargs):
    r = requests.get(f'{SB}/rest/v1/{url}', headers=H, timeout=30, **kwargs)
    r.raise_for_status()
    return r.json()

def emit(check_id, category, severity, status, detail):
    findings.append({'id': check_id, 'cat': category, 'sev': severity,
                     'status': status, 'detail': detail})

def _cls(r):
    r = (r or '').strip().lower()
    if r in ('win','w'): return 'win'
    if r in ('loss','l'): return 'loss'
    if r in ('push','p'): return 'push'
    return None

def _payout(american):
    try: o = int(american)
    except (TypeError, ValueError): return 0.909
    if o == 0: return 0.909
    return o/100.0 if o > 0 else 100.0/abs(o)


# ═══ A. RECORD AGGREGATORS ══════════════════════════════════════════════════

def check_A1_sharp():
    raw = get('jerry_reads?sport=eq.MLB&game_date=gte.' + EPOCH
              + '&conviction=gte.60&result=not.is.null'
              + '&select=result&limit=2000')
    w=l=p=0
    for r in raw:
        c = _cls(r.get('result'))
        if c=='win': w+=1
        elif c=='loss': l+=1
        elif c=='push': p+=1
    sr = get(f'surface_records?sport=eq.MLB&surface=eq.sharp&window_key=eq.epoch')
    if not sr:
        emit('A1', 'AGGREGATOR', 'HIGH', 'FAIL', 'surface_records missing sharp/MLB/epoch row')
        return
    row = sr[0]
    match = (row['wins']==w and row['losses']==l and row['pushes']==p)
    emit('A1', 'AGGREGATOR', 'HIGH' if not match else 'OK', 'OK' if match else 'FAIL',
         f'raw={w}-{l}-{p}  sr={row["wins"]}-{row["losses"]}-{row["pushes"]}')

def check_A2_prop():
    raw = get('mlb_pipeline_props?tier=in.(PRIME,STRONG)&game_date=gte.' + EPOCH
              + '&result=not.is.null&select=result,tier,conviction&limit=5000')
    w=l=p=0
    for r in raw:
        if (r.get('conviction') or 0) == 0: continue
        c = _cls(r.get('result'))
        if c=='win': w+=1
        elif c=='loss': l+=1
        elif c=='push': p+=1
    sr = get(f'surface_records?sport=eq.MLB&surface=eq.prop&window_key=eq.epoch')
    if not sr:
        emit('A2', 'AGGREGATOR', 'HIGH', 'FAIL', 'surface_records missing prop/MLB/epoch row')
        return
    row = sr[0]
    match = (row['wins']==w and row['losses']==l and row['pushes']==p)
    emit('A2', 'AGGREGATOR', 'HIGH' if not match else 'OK', 'OK' if match else 'FAIL',
         f'raw={w}-{l}-{p}  sr={row["wins"]}-{row["losses"]}-{row["pushes"]}')

def check_A3_ladder():
    raw = get('ladder_rung?sport=eq.MLB&result=not.is.null&select=result,conviction,tier&limit=500')
    w=l=p=0
    for r in raw:
        if (r.get('conviction') or 0)==0 or (r.get('tier') or '').upper()=='COVERAGE': continue
        c = _cls(r.get('result'))
        if c=='win': w+=1
        elif c=='loss': l+=1
        elif c=='push': p+=1
    sr = get(f'surface_records?sport=eq.MLB&surface=eq.ladder&window_key=eq.lifetime')
    if not sr:
        emit('A3', 'AGGREGATOR', 'MED', 'FAIL', 'surface_records missing ladder/MLB/lifetime row')
        return
    row = sr[0]
    match = (row['wins']==w and row['losses']==l and row['pushes']==p)
    emit('A3', 'AGGREGATOR', 'HIGH' if not match else 'OK', 'OK' if match else 'FAIL',
         f'raw={w}-{l}-{p}  sr={row["wins"]}-{row["losses"]}-{row["pushes"]}')

def check_A4_ledger():
    raw = get('ledger_suggestions?sport_scope=eq.MLB&result=not.is.null&select=result,combined_odds&limit=500')
    w=l=p=0
    for r in raw:
        c = _cls(r.get('result'))
        if c=='win': w+=1
        elif c=='loss': l+=1
        elif c=='push': p+=1
    sr = get(f'surface_records?sport=eq.MLB&surface=eq.ledger&window_key=eq.lifetime')
    if not sr:
        emit('A4', 'AGGREGATOR', 'MED', 'FAIL', 'surface_records missing ledger/MLB/lifetime row')
        return
    row = sr[0]
    match = (row['wins']==w and row['losses']==l and row['pushes']==p)
    emit('A4', 'AGGREGATOR', 'HIGH' if not match else 'OK', 'OK' if match else 'FAIL',
         f'raw={w}-{l}-{p}  sr={row["wins"]}-{row["losses"]}-{row["pushes"]}')

def check_A5_potd():
    raw = get('daily_best_bet_history?sport=eq.MLB&result=not.is.null&select=result,odds_american&limit=500')
    w=l=p=0; units=0.0; risk=0.0
    for r in raw:
        c = _cls(r.get('result'))
        payout = _payout(r.get('odds_american'))
        if c=='win': w+=1; units += payout; risk += 1
        elif c=='loss': l+=1; units -= 1.0; risk += 1
        elif c=='push': p+=1
    sr = get(f'surface_records?sport=eq.MLB&surface=eq.potd&window_key=eq.lifetime')
    if not sr:
        emit('A5', 'AGGREGATOR', 'MED', 'FAIL', 'surface_records missing potd/MLB/lifetime row')
        return
    row = sr[0]
    match = (row['wins']==w and row['losses']==l and row['pushes']==p)
    unit_ok = abs(float(row['units_net']) - units) < 0.05
    st = 'OK' if match and unit_ok else 'FAIL'
    emit('A5', 'AGGREGATOR', 'HIGH' if not match else 'OK' if unit_ok else 'MED', st,
         f'raw={w}-{l}-{p} {units:+.2f}u  sr={row["wins"]}-{row["losses"]}-{row["pushes"]} {float(row["units_net"]):+.2f}u')


# ═══ B. JERRY CONSISTENCY ═══════════════════════════════════════════════════

def check_B6_call_text_drift():
    raw = get(f'jerry_reads?sport=eq.MLB&game_date=gte.{D14}'
              '&select=id,game_id,game_date,call_text,call_market,call_side,call_line&limit=500')
    gids = list({r['game_id'] for r in raw})
    teams = {}
    for i in range(0, len(gids), 30):
        chunk = ','.join(f'"{g}"' for g in gids[i:i+30])
        for row in get(f'mlb_game_context?game_id=in.({chunk})&select=game_id,home_team,away_team'):
            teams[row['game_id']] = (row['home_team'], row['away_team'])
    drift = []
    for r in raw:
        if not r.get('call_text'): continue
        ha = teams.get(r['game_id'])
        if not ha: continue
        home, away = ha
        m = (r.get('call_market') or '').lower()
        s = (r.get('call_side') or '').upper()
        ct = (r['call_text'] or '').lower()
        if m == 'ml' and s in ('HOME','AWAY'):
            picked = home if s=='HOME' else away
            last = picked.lower().split()[-1]
            if last not in ct:
                drift.append((r['id'], r['game_date'], f'{away} @ {home}', s, r['call_text']))
        elif m == 'total' and s in ('OVER','UNDER'):
            if s.lower() not in ct:
                drift.append((r['id'], r['game_date'], f'{away} @ {home}', s, r['call_text']))
    if drift:
        emit('B6', 'JERRY', 'HIGH', 'FAIL',
             f'{len(drift)} rows with call_text drift; sample id={drift[0][0]} txt={drift[0][4]!r}')
    else:
        emit('B6', 'JERRY', 'OK', 'OK', f'0 call_text drift on {len(raw)} rows last 14d')

def check_B7_long_read_drift():
    raw = get(f'jerry_reads?sport=eq.MLB&game_date=gte.{D14}'
              '&long_read=not.is.null&select=id,game_date,call_market,call_side,long_read&limit=500')
    drift = []
    for r in raw:
        m = (r.get('call_market') or '').lower()
        s = (r.get('call_side') or '').upper()
        lr = (r.get('long_read') or '')
        if not lr or not m: continue
        tail = lr[-300:].lower()
        # If ML pick, long_read shouldn't conclude with OVER/UNDER lean
        if m == 'ml':
            if 'lean is over' in tail or 'lean is under' in tail or 'the pick: over' in tail or 'the pick: under' in tail:
                drift.append((r['id'], r['game_date'], m, s, tail[-120:]))
        # If total pick, long_read shouldn't conclude with ML team-name pick
        # (harder to detect deterministically; skip for now)
    if drift:
        emit('B7', 'JERRY', 'MED', 'FAIL',
             f'{len(drift)} rows: call_market vs long_read conclusion mismatch; sample id={drift[0][0]}')
    else:
        emit('B7', 'JERRY', 'OK', 'OK', f'0 long_read drift detected on {len(raw)} rows')

def check_B8_pp_vs_jerry():
    # For today, ctx.primary_play should match jerry_reads for the same game
    today = TODAY.isoformat()
    ctx = get(f'mlb_game_context?game_date=eq.{today}&primary_play=not.is.null'
              '&select=game_id,primary_play')
    jr  = get(f'jerry_reads?sport=eq.MLB&game_date=eq.{today}'
              '&select=game_id,call_market,call_side')
    jr_map = {}
    for j in jr: jr_map.setdefault(j['game_id'], []).append(j)
    drift = []
    for c in ctx:
        pp = c.get('primary_play') or {}
        ptype = (pp.get('type') or '').lower()
        pside = (pp.get('side') or '').upper()
        jrows = jr_map.get(c['game_id']) or []
        # Look for a jerry row on same market
        match = any((j.get('call_market','').lower()==ptype
                     and (j.get('call_side') or '').upper()==pside) for j in jrows)
        if jrows and not match:
            drift.append((c['game_id'], ptype, pside, [(j.get('call_market'), j.get('call_side')) for j in jrows]))
    if drift:
        emit('B8', 'JERRY', 'MED', 'FAIL',
             f'{len(drift)} games where primary_play doesn\'t match any jerry_reads')
    else:
        emit('B8', 'JERRY', 'OK', 'OK', 'primary_play aligns with jerry_reads today')


# ═══ C. EXTERNAL SOURCE RECORDS ═════════════════════════════════════════════

def check_C9_ext_grade_correctness():
    # Sample 20 recent graded external_picks and verify against game_results
    picks = get('external_picks?sport=eq.MLB&surface=in.(ml,total)&result=not.is.null'
                '&order=game_date.desc&select=id,game_id,surface,pick_side,pick_line,result&limit=20')
    gids = list({p['game_id'] for p in picks})
    ids = ','.join(f'"{g}"' for g in gids)
    res_map = {}
    for row in get(f'mlb_game_results?game_id=in.({ids})&select=game_id,away_score,home_score,close_total'):
        res_map[row['game_id']] = row
    wrong = []
    for p in picks:
        gr = res_map.get(p['game_id'])
        if not gr or gr.get('away_score') is None: continue
        a, h = gr['away_score'], gr['home_score']
        actual = p.get('pick_line') or gr.get('close_total')
        if p['surface'] == 'ml':
            side = (p.get('pick_side') or '').upper()
            picked_won = (side=='HOME' and h>a) or (side=='AWAY' and a>h)
            expected = 'W' if picked_won else 'L'
        elif p['surface'] == 'total' and actual is not None:
            total = a + h
            side = (p.get('pick_side') or '').upper()
            if total > float(actual): tr = 'over'
            elif total < float(actual): tr = 'under'
            else: tr = 'push'
            if tr == 'push': expected = 'P'
            else: expected = 'W' if (side=='OVER' and tr=='over') or (side=='UNDER' and tr=='under') else 'L'
        else:
            continue
        if expected != p['result']:
            wrong.append((p['id'], p['surface'], p['pick_side'], f'{a}-{h}', p['result'], expected))
    if wrong:
        emit('C9', 'EXTERNAL', 'HIGH', 'FAIL', f'{len(wrong)}/{len(picks)} misgraded; sample {wrong[0]}')
    else:
        emit('C9', 'EXTERNAL', 'OK', 'OK', f'{len(picks)} sampled grades all correct')

def check_C10_ext_rollup():
    # external_source_track_record vs external_picks raw
    ep = get('external_picks?sport=eq.MLB&result=not.is.null&select=source,surface,result&limit=5000')
    raw = defaultdict(lambda: {'W':0,'L':0,'P':0})
    for p in ep:
        c = _cls(p['result'])
        if not c: continue
        raw[(p['source'], p['surface'])][{'win':'W','loss':'L','push':'P'}[c]] += 1
    # rollup lifetime rows
    tr = get('external_source_track_record?sport=eq.MLB&window_days=eq.9999&select=source,surface,n_wins,n_losses,n_pushes')
    drift = []
    for row in tr:
        k = (row['source'], row['surface'])
        r = raw.get(k, {'W':0,'L':0,'P':0})
        if row.get('n_wins')!=r['W'] or row.get('n_losses')!=r['L']:
            drift.append((k, r, {'n_wins':row.get('n_wins'), 'n_losses':row.get('n_losses')}))
    if drift:
        emit('C10', 'EXTERNAL', 'MED', 'FAIL',
             f'{len(drift)} rollup rows disagree with raw; sample {drift[0]}')
    else:
        emit('C10', 'EXTERNAL', 'OK', 'OK', f'{len(tr)} rollup rows match raw')

def check_C11_ext_calibration():
    ep = get('external_picks?sport=eq.MLB&result=not.is.null&select=source,surface,result&limit=5000')
    raw = defaultdict(lambda: {'W':0,'L':0,'P':0})
    for p in ep:
        c = _cls(p['result'])
        if not c: continue
        raw[(p['source'], p['surface'])][{'win':'W','loss':'L','push':'P'}[c]] += 1
    cal = get('external_source_calibration?window_label=eq.lifetime&select=source,surface,wins,losses')
    drift = []
    for row in cal:
        k = (row['source'], row['surface'])
        r = raw.get(k, {'W':0,'L':0,'P':0})
        if row.get('wins')!=r['W'] or row.get('losses')!=r['L']:
            drift.append((k, r, {'wins':row.get('wins'),'losses':row.get('losses')}))
    if drift:
        emit('C11', 'EXTERNAL', 'MED', 'FAIL',
             f'{len(drift)} calibration rows disagree with raw; sample {drift[0]}')
    else:
        emit('C11', 'EXTERNAL', 'OK', 'OK', f'{len(cal)} calibration rows match raw')

def check_C12_sport_contamination():
    # MLB external_picks with non-MLB-looking game_id
    picks = get(f'external_picks?sport=eq.MLB&game_date=gte.{D14}&select=game_id,game_date,source&limit=1000')
    bad = [p for p in picks if any(kw in (p.get('game_id') or '').lower() for kw in ('ncaaf_','nfl_','nba_','nhl_','ncaab_','ufc_'))]
    if bad:
        emit('C12', 'EXTERNAL', 'HIGH', 'FAIL', f'{len(bad)} non-MLB game_ids stored as sport=MLB; sample {bad[0]}')
    else:
        emit('C12', 'EXTERNAL', 'OK', 'OK', f'{len(picks)} MLB picks all have MLB-shaped game_ids')


# ═══ D. YESTERDAY RECAP ═════════════════════════════════════════════════════

def check_D13_yesterday_sharp():
    jr = get(f'jerry_reads?sport=eq.MLB&game_date=eq.{YEST}&conviction=gte.60&result=not.is.null&select=result')
    props = get(f'mlb_pipeline_props?game_date=eq.{YEST}&tier=in.(PRIME,STRONG)&result=not.is.null&select=result')
    w=l=p=0
    for r in jr+props:
        c = _cls(r['result'])
        if c=='win': w+=1
        elif c=='loss': l+=1
        elif c=='push': p+=1
    emit('D13', 'RECAP', 'OK', 'INFO',
         f'yesterday ({YEST}) combined = {w}-{l}-{p}')

def check_D14_yesterday_potd():
    p = get(f'daily_best_bet_history?bet_date=eq.{YEST}&sport=eq.MLB&select=game,lean,result,odds_american')
    if not p:
        emit('D14', 'RECAP', 'MED', 'INFO', 'no POTD row for yesterday')
    else:
        row = p[0]
        emit('D14', 'RECAP', 'OK', 'INFO',
             f'POTD {row["game"]} · {row["lean"][:40]} · result={row["result"]} · odds={row.get("odds_american")}')


# ═══ E. CROSS-TABLE ═════════════════════════════════════════════════════════

def check_E15_potd_leandisplay_drift():
    # daily_best_bet_history.lean vs jerry_cache.data.leanDisplay for last 14 days
    hist = get(f'daily_best_bet_history?sport=eq.MLB&bet_date=gte.{D14}&select=bet_date,lean')
    drift = []
    for h in hist:
        bd = h['bet_date']
        jc = get(f'jerry_cache?game_id=eq.best_bet_{bd}&select=data')
        if not jc: continue
        jld = ((jc[0].get('data') or {}).get('leanDisplay') or '')
        if not jld or not h.get('lean'): continue
        # compare after normalize
        h_norm = h['lean'].lower().replace(' ','')[:20]
        j_norm = jld.lower().replace(' ','')[:20]
        if h_norm != j_norm:
            drift.append((bd, h['lean'], jld))
    if drift:
        emit('E15', 'CROSSTAB', 'MED', 'FAIL',
             f'{len(drift)} POTD rows where history.lean disagrees with cache.leanDisplay; sample {drift[0]}')
    else:
        emit('E15', 'CROSSTAB', 'OK', 'OK', f'{len(hist)} POTD rows consistent across history + cache')

def check_E16_potd_odds_drift():
    # odds_american came from primary_play backfill; check when primary_play type/side
    # disagreed with actual POTD lean text
    rows = get(f'daily_best_bet_history?sport=eq.MLB&bet_date=gte.{D14}&odds_american=not.is.null&select=bet_date,game,lean,odds_american')
    drift = []
    for r in rows:
        lean = (r.get('lean') or '').lower()
        odds = r.get('odds_american')
        # If lean is Over/Under/RL/spread but odds is captured from ML, that's drift
        is_ml = 'ml' in lean
        # ML rows with odds captured — verify magnitude reasonable
        if not is_ml:
            drift.append((r['bet_date'], r['game'][:40], r['lean'][:40], odds))
    if drift:
        emit('E16', 'CROSSTAB', 'MED', 'FAIL',
             f'{len(drift)} POTD rows have odds_american but non-ML lean text; sample {drift[0]}')
    else:
        emit('E16', 'CROSSTAB', 'OK', 'OK', 'POTD odds only captured on ML picks')

def check_E17_props_l10_gate():
    # Post L10-gate: no PRIME/STRONG hits_over should have L10<10
    rows = get('mlb_pipeline_props?prop_type=eq.hits_over&direction=eq.over'
               '&tier=in.(PRIME,STRONG)&select=id,player_name,game_date,tier,player_l10_hit_count&limit=500')
    bad = [r for r in rows if r.get('player_l10_hit_count') is None or int(r.get('player_l10_hit_count', 0)) < 10]
    if bad:
        emit('E17', 'CROSSTAB', 'HIGH', 'FAIL',
             f'{len(bad)}/{len(rows)} PRIME/STRONG hits_over rows violate L10=10 gate; sample id={bad[0]["id"]} L10={bad[0].get("player_l10_hit_count")}')
    else:
        emit('E17', 'CROSSTAB', 'OK', 'OK', f'all {len(rows)} PRIME/STRONG hits_over rows have L10=10')


# ═══ F. SCHEMA ══════════════════════════════════════════════════════════════

def check_F18_result_schema():
    tables = {
        'jerry_reads': "result=not.is.null&select=result&limit=100",
        'mlb_pipeline_props': "result=not.is.null&select=result&limit=100",
        'ladder_rung': "result=not.is.null&select=result&limit=100",
        'ledger_suggestions': "result=not.is.null&select=result&limit=100",
        'daily_best_bet_history': "result=not.is.null&select=result&limit=100",
        'external_picks': "result=not.is.null&select=result&limit=100",
    }
    schemas = {}
    for tbl, params in tables.items():
        rows = get(f'{tbl}?{params}')
        schemas[tbl] = sorted(set(r['result'] for r in rows if r.get('result')))
    # W/Loss/Push (title) vs W/L/P (single char) is the ongoing schema drift
    inconsistent = []
    for tbl, vals in schemas.items():
        v = set(vals)
        if v & {'W','L','P'} and v & {'Win','Loss','Push'}:
            inconsistent.append((tbl, list(v)))
    if inconsistent:
        emit('F18', 'SCHEMA', 'MED', 'FAIL',
             f'mixed result value schemas within table: {inconsistent}')
    else:
        # Just report the per-table format
        emit('F18', 'SCHEMA', 'OK', 'INFO',
             '  '.join(f'{t}={vals}' for t,vals in schemas.items()))


# ═══ RUN ════════════════════════════════════════════════════════════════════

CHECKS = [
    check_A1_sharp, check_A2_prop, check_A3_ladder, check_A4_ledger, check_A5_potd,
    check_B6_call_text_drift, check_B7_long_read_drift, check_B8_pp_vs_jerry,
    check_C9_ext_grade_correctness, check_C10_ext_rollup, check_C11_ext_calibration, check_C12_sport_contamination,
    check_D13_yesterday_sharp, check_D14_yesterday_potd,
    check_E15_potd_leandisplay_drift, check_E16_potd_odds_drift, check_E17_props_l10_gate,
    check_F18_result_schema,
]

def main():
    print(f'audit_data_accuracy @ {dt.datetime.now(dt.timezone.utc).isoformat()}')
    print(f'  scope: MLB, last 14 days (epoch={EPOCH})')
    print()
    for chk in CHECKS:
        try:
            chk()
        except Exception as e:
            emit(chk.__name__, 'RUNTIME', 'HIGH', 'ERROR', f'{type(e).__name__}: {e}')
    # Print grouped by severity
    order = {'HIGH':0, 'MED':1, 'OK':2}
    fails = [f for f in findings if f['status']!='OK' and f['status']!='INFO']
    infos = [f for f in findings if f['status'] in ('OK','INFO')]
    print('═' * 60)
    print(f'FAILURES: {len(fails)}')
    print('═' * 60)
    for f in sorted(fails, key=lambda x: order.get(x['sev'], 9)):
        print(f'[{f["sev"]}] {f["id"]:5s} ({f["cat"]:9s}) {f["status"]:5s}  {f["detail"]}')
    print()
    print('═' * 60)
    print(f'OK / INFO: {len(infos)}')
    print('═' * 60)
    for f in infos:
        print(f'[{f["sev"]:3s}] {f["id"]:5s} ({f["cat"]:9s}) {f["status"]:5s}  {f["detail"]}')

if __name__ == '__main__':
    main()
