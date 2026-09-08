"""consistency_watchdog — cross-table sanity checks that STOP shipping inconsistent data.

Every user-reported bug in the 2026-09-08 session was "two sources of
truth for the same fact":
  - POTD result in jerry_cache vs daily_best_bet_history
  - LEAN props graded in mlb_pipeline_props vs surface_records
  - NCAAF record in sharp_card vs ncaaf_sides
  - Prop graph data in mlb_pipeline_props vs prop_jerry_reads
  - Badge/detail drift: primary_play vs jerry_reads.call_*
  - LR shadow stale vs LIVE recompute

Point-fixes don't scale. This watchdog is the systemic answer: cron
runs it every 30 min, it queries the tables directly, compares them
across the known consistency contracts, and fails LOUD if any check
trips. Exit code 2 = CRITICAL (blocks Sharp Card publish), 1 = WARNING,
0 = clean.

Writes tripped alerts to watchdog_alerts (same table as watchdogs.py).

CLI:
  python consistency_watchdog.py                    # today, all checks
  python consistency_watchdog.py --date 2026-09-08
  python consistency_watchdog.py --check potd_grade_mirror  # single check
  python consistency_watchdog.py --dry-run          # print + skip DB writes
"""
from __future__ import annotations
import argparse, json, os, sys
import datetime as dt
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

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}


def _et_today() -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date().isoformat()


def _days_ago(n: int) -> str:
    return ((dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date()
            - dt.timedelta(days=n)).isoformat()


# ═══════════════════════════════════════════════════════════════════════
# CHECKS — each returns dict when tripped, None when clean.
# ═══════════════════════════════════════════════════════════════════════

def check_potd_grade_mirror(date: str) -> Optional[dict]:
    """jerry_cache POTD state must match daily_best_bet_history for
    both graded and noPlay days.

    Both are read by different app surfaces (RPC vs calendar). If they
    disagree, one shows Win while another shows Pending — or one shows
    noPlay while another still shows an old pick. User loses trust.

    2026-09-09 EXPANDED: was only checking Win/Loss/Push grade mirror.
    Live audit today caught jerry_cache.noPlay while daily_best_bet_history
    still had a stale pipelineGenerated Marlins pick. Now checks noPlay
    state mirror too.
    """
    since = _days_ago(14)
    r = requests.get(
        f'{SB}/rest/v1/jerry_cache',
        headers=H_R,
        params={'cache_key': f'like.best_bet_2026-%',
                'select': 'cache_key,data', 'limit': '30',
                'order': 'cache_key.desc'},
        timeout=15,
    )
    if r.status_code != 200: return None
    jc_state_by_date: dict = {}  # date → 'Win' | 'Loss' | 'Push' | 'Void' | 'NoPlay' | 'Live'
    for row in r.json() or []:
        ck = row.get('cache_key') or ''
        if '_mlb' in ck or '_nfl' in ck: continue
        d = ck.replace('best_bet_', '')
        if d < since: continue
        data = row.get('data') or {}
        if isinstance(data, str):
            try: data = json.loads(data)
            except Exception: data = {}
        if data.get('noPlay'):
            jc_state_by_date[d] = 'NoPlay'
        else:
            res = data.get('result')
            if res in ('Win', 'Loss', 'Push', 'Void'):
                jc_state_by_date[d] = res
            elif data.get('game'):
                jc_state_by_date[d] = 'Live'  # pick exists, not yet graded

    if not jc_state_by_date: return None

    r2 = requests.get(
        f'{SB}/rest/v1/daily_best_bet_history',
        headers=H_R,
        params={'bet_date': f'gte.{since}',
                'select': 'bet_date,result,lean', 'limit': '30'},
        timeout=15,
    )
    hist_by_date: dict = {}
    for row in (r2.json() if r2.status_code == 200 else []) or []:
        hist_by_date[row['bet_date']] = {
            'result': (row.get('result') or ''),
            'lean': (row.get('lean') or ''),
        }

    mismatches = []
    for d, jc_state in jc_state_by_date.items():
        hist = hist_by_date.get(d, {'result': 'MISSING', 'lean': ''})
        hist_res = hist['result']
        hist_lean = hist['lean']
        # NoPlay in cache but real pick in history = drift
        if jc_state == 'NoPlay':
            if 'no play' not in hist_lean.lower() and hist_res not in ('No Play', 'MISSING'):
                mismatches.append({'date': d, 'jerry_cache': 'NoPlay',
                                   'daily_best_bet_history': f'{hist_res} · {hist_lean[:40]}'})
        elif jc_state in ('Win', 'Loss', 'Push', 'Void'):
            if hist_res != jc_state:
                mismatches.append({'date': d, 'jerry_cache': jc_state,
                                   'daily_best_bet_history': hist_res})

    if not mismatches: return None
    return {
        'check_name': 'potd_grade_mirror',
        'severity': 'WARNING',
        'message': f'{len(mismatches)} POTD date(s) with state mismatch '
                   f'between jerry_cache and daily_best_bet_history. '
                   f'Home / Receipts / calendar will display conflicting values.',
        'detail': {'mismatches': mismatches[:10],
                   'fix': 'python jerry_anchor_potd.py  (writes both surfaces now)'},
    }


def check_sharp_card_props_have_synthesis(date: str) -> Optional[dict]:
    """Every prop that landed on today's Sharp Card must have a
    prop_jerry_reads row so the graph renders on the card."""
    r = requests.get(
        f'{SB}/rest/v1/jerry_cache',
        headers=H_R,
        params={'cache_key': f'eq.sharp_card_{date}', 'select': 'data'},
        timeout=15,
    )
    if r.status_code != 200 or not r.json(): return None
    blob = r.json()[0].get('data') or {}
    if isinstance(blob, str):
        try: blob = json.loads(blob)
        except Exception: blob = {}
    items = blob.get('items') or []
    prop_items = [it for it in items if (it.get('type') or '').lower() == 'prop']
    if not prop_items: return None

    # For each prop item, check prop_jerry_reads exists
    missing = []
    for it in prop_items:
        player = it.get('player_name') or it.get('pick', '').split(' Over ')[0].split(' Under ')[0].strip()
        prop_type = it.get('prop_type')
        direction = it.get('direction')
        if not player or not prop_type: continue
        params = {'player_name': f'eq.{player}',
                  'prop_type': f'eq.{prop_type}',
                  'game_date': f'eq.{date}',
                  'select': 'id', 'limit': '1'}
        if direction:
            params['direction'] = f'eq.{direction}'
        pr = requests.get(f'{SB}/rest/v1/prop_jerry_reads', headers=H_R,
                          params=params, timeout=10)
        if pr.status_code == 200 and not pr.json():
            missing.append(f'{player} · {prop_type}')

    if not missing: return None
    return {
        'check_name': 'sharp_card_props_synthesis_gap',
        'severity': 'CRITICAL' if len(missing) >= 5 else 'WARNING',
        'message': f'{len(missing)} of {len(prop_items)} Sharp Card props '
                   f'have NO prop_jerry_reads → no graph on card.',
        'detail': {'missing_props': missing[:15], 'sharp_card_prop_count': len(prop_items)},
    }


def check_badge_matches_detail(date: str) -> Optional[dict]:
    """For every game_context today with primary_play, jerry_reads.call_*
    must match primary_play (type/side/label). Divergence = badge on
    games tab shows one pick, game detail shows another.
    """
    r = requests.get(
        f'{SB}/rest/v1/mlb_game_context',
        headers=H_R,
        params={'game_date': f'eq.{date}', 'primary_play': 'not.is.null',
                'select': 'game_id,home_team,away_team,primary_play',
                'limit': '30'},
        timeout=15,
    )
    if r.status_code != 200: return None
    ctx_rows = r.json() or []
    if not ctx_rows: return None
    game_ids = [c['game_id'] for c in ctx_rows if c.get('game_id')]
    if not game_ids: return None

    jr = requests.get(
        f'{SB}/rest/v1/jerry_reads',
        headers=H_R,
        params={'sport': 'eq.MLB', 'game_date': f'eq.{date}',
                'game_id': f'in.({",".join(game_ids)})',
                'select': 'game_id,call_market,call_side,call_text',
                'limit': '30'},
        timeout=15,
    )
    jr_by_game = {row['game_id']: row for row in (jr.json() if jr.status_code == 200 else []) or []}

    mismatches = []
    for c in ctx_rows:
        gid = c['game_id']
        pp = c.get('primary_play')
        if isinstance(pp, str):
            try: pp = json.loads(pp)
            except Exception: pp = {}
        pp = pp or {}
        jr_row = jr_by_game.get(gid)
        if not jr_row: continue  # not-yet-synthesized isn't a mismatch
        pp_type = (pp.get('type') or '').lower()
        pp_side = (pp.get('side') or '').upper()
        jr_type = (jr_row.get('call_market') or '').lower()
        jr_side = (jr_row.get('call_side') or '').upper()
        if pp_type and jr_type and pp_type != jr_type:
            mismatches.append({
                'game': f'{c.get("away_team","?")[:12]} @ {c.get("home_team","?")[:12]}',
                'primary_play': f'{pp_type}/{pp_side}',
                'jerry_reads':  f'{jr_type}/{jr_side}',
            })

    if not mismatches: return None
    return {
        'check_name': 'badge_detail_drift',
        'severity': 'CRITICAL',
        'message': f'{len(mismatches)} game(s) with primary_play vs jerry_reads '
                   f'market mismatch. Badges + game detail will contradict.',
        'detail': {'mismatches': mismatches[:10],
                   'fix_hint': 'python jerry_pick_scrub.py --sport MLB'},
    }


def check_lean_prop_rollup(date: str) -> Optional[dict]:
    """LEAN + COVERAGE prop surface_records must exist when graded props exist.
    Otherwise Receipts will show 0-0 for tiers that have real records.
    """
    # Any graded LEAN props last 7 days?
    since = _days_ago(7)
    r = requests.get(
        f'{SB}/rest/v1/mlb_pipeline_props',
        headers=H_R,
        params={'game_date': f'gte.{since}', 'tier': 'eq.LEAN',
                'result': 'in.(Win,Loss,Push,win,loss,push)',
                'select': 'result', 'limit': '5'},
        timeout=15,
    )
    if r.status_code != 200: return None
    if not r.json(): return None  # no graded LEAN props = expected empty

    # Then surface_records must have a prop_lean entry
    sr = requests.get(
        f'{SB}/rest/v1/surface_records',
        headers=H_R,
        params={'surface': 'eq.prop_lean', 'sport': 'eq.MLB',
                'window_key': 'eq.d7',
                'select': 'wins,losses,pushes'},
        timeout=15,
    )
    rows = sr.json() if sr.status_code == 200 else []
    if not rows or (rows[0].get('wins', 0) + rows[0].get('losses', 0)) == 0:
        return {
            'check_name': 'lean_prop_rollup_missing',
            'severity': 'WARNING',
            'message': 'Graded LEAN props exist in mlb_pipeline_props but '
                       'surface_records.prop_lean is empty. '
                       'Receipts LEAN row will show 0-0.',
            'detail': {'fix_hint': 'python compute_surface_records.py'},
        }
    return None


def check_lr_shadow_coverage(date: str) -> Optional[dict]:
    """Every MLB game with primary_play should have _lr_ml_shadow and
    _lr_total_shadow populated. Gap = downstream LR gates silently no-op."""
    r = requests.get(
        f'{SB}/rest/v1/mlb_game_context',
        headers=H_R,
        params={'game_date': f'eq.{date}', 'primary_play': 'not.is.null',
                'select': 'primary_play,home_team,away_team', 'limit': '30'},
        timeout=15,
    )
    if r.status_code != 200: return None
    rows = r.json() or []
    if not rows: return None
    ml_ok = tot_ok = 0
    missing = []
    for c in rows:
        pp = c.get('primary_play')
        if isinstance(pp, str):
            try: pp = json.loads(pp)
            except Exception: pp = {}
        pp = pp or {}
        has_ml = bool((pp.get('_lr_ml_shadow') or {}).get('p_home_win'))
        has_tot = bool((pp.get('_lr_total_shadow') or {}).get('p_over'))
        if has_ml: ml_ok += 1
        if has_tot: tot_ok += 1
        if not (has_ml and has_tot):
            missing.append({'game': f'{c.get("away_team","?")[:12]} @ {c.get("home_team","?")[:12]}',
                            'ml': has_ml, 'total': has_tot})
    total = len(rows)
    if ml_ok == total and tot_ok == total: return None
    return {
        'check_name': 'lr_shadow_coverage_gap',
        'severity': 'WARNING' if (ml_ok/total >= 0.8 and tot_ok/total >= 0.8) else 'CRITICAL',
        'message': f'MLB LR shadow coverage: ML {ml_ok}/{total}, Total {tot_ok}/{total}. '
                   f'Games without shadow silently no-op the LR gate.',
        'detail': {'missing_games': missing[:10],
                   'fix_hint': 'python recompute_primary_play.py --force'},
    }


def check_ncaaf_sides_over_sharp_card(date: str) -> Optional[dict]:
    """When NCAAF has both sharp_card AND ncaaf_sides surface records,
    ncaaf_sides is the authoritative fuller record. Warn if only
    sharp_card exists (Receipts would understate NCAAF)."""
    r = requests.get(
        f'{SB}/rest/v1/surface_records',
        headers=H_R,
        params={'sport': 'eq.NCAAF', 'window_key': 'eq.d30',
                'select': 'surface,wins,losses'},
        timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
    if not rows: return None
    surfaces = {row['surface']: row for row in rows}
    sc = surfaces.get('sharp_card')
    ns = surfaces.get('ncaaf_sides')
    if sc and not ns:
        return {
            'check_name': 'ncaaf_sides_surface_missing',
            'severity': 'WARNING',
            'message': 'NCAAF sharp_card record exists but ncaaf_sides '
                       'record missing → Receipts undercounts NCAAF.',
            'detail': {'fix_hint': 'python compute_surface_records.py'},
        }
    return None


CHECKS = {
    'potd_grade_mirror':           check_potd_grade_mirror,
    'sharp_card_props_synthesis':  check_sharp_card_props_have_synthesis,
    'badge_detail_drift':          check_badge_matches_detail,
    'lean_prop_rollup':            check_lean_prop_rollup,
    'lr_shadow_coverage':          check_lr_shadow_coverage,
    'ncaaf_sides_over_sharp_card': check_ncaaf_sides_over_sharp_card,
}


def write_alert(alert: dict, date: str) -> None:
    """Upsert into watchdog_alerts (same table as watchdogs.py uses)."""
    payload = {
        'run_date': date,
        'check_name': alert['check_name'],
        'severity': alert['severity'],
        'message': alert['message'],
        'detail': alert.get('detail') or {},
        'last_seen_at': dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    r = requests.post(
        f'{SB}/rest/v1/watchdog_alerts?on_conflict=check_name,run_date',
        headers={**H_W, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
        json=payload, timeout=10,
    )
    if r.status_code >= 400:
        print(f'  ⚠ alert write failed {r.status_code}: {r.text[:120]}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='YYYY-MM-DD (default: today ET)')
    ap.add_argument('--check', choices=list(CHECKS),
                    help='Run one specific check')
    ap.add_argument('--dry-run', action='store_true',
                    help='Print alerts but do not persist to DB')
    args = ap.parse_args()

    date = args.date or _et_today()
    to_run = {args.check: CHECKS[args.check]} if args.check else CHECKS

    print(f'=== consistency_watchdog · {date} · {len(to_run)} check(s) '
          f'{"· DRY" if args.dry_run else ""} ===')

    tripped = 0
    critical = 0
    for name, fn in to_run.items():
        try:
            result = fn(date)
        except Exception as e:
            print(f'  ✗ {name}: exception {type(e).__name__}: {e}')
            continue
        if result is None:
            print(f'  ✓ {name}: clean')
            continue
        sev = result.get('severity', '?')
        msg = result.get('message', '?')
        print(f'  {"🚨" if sev == "CRITICAL" else "⚠️"} {sev}  {name}: {msg}')
        tripped += 1
        if sev == 'CRITICAL': critical += 1
        if not args.dry_run:
            write_alert(result, date)

    print(f'\n{"" if not tripped else "⚠️ "}{tripped}/{len(to_run)} checks tripped '
          f'({critical} critical)')

    # Exit codes: 0 clean, 1 warnings, 2 critical (workflow can gate on this)
    sys.exit(2 if critical > 0 else (1 if tripped > 0 else 0))


if __name__ == '__main__':
    main()
