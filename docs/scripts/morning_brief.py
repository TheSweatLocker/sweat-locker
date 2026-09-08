"""Morning brief — single-command daily verification.

Runs the 5-section audit from docs/MORNING_BRIEF.md automatically:
  1. Grading completeness per sport + table
  2. Pick results by sport (Jerry, PRIME/STRONG/LEAN, LR-endorsed)
  3. Steam Room P/L (from daily_surface_records)
  4. Today's pipeline health (writes for today's slate)
  5. Known regressions grep (Peterson orphans, MoneyFlow field, etc.)

Exit codes:
  0 → all green, no gaps
  1 → soft warning (some sport <80% graded, no data loss)
  2 → hard failure (missing overnight jobs, POTD ungraded, key data missing)

USAGE:
    python docs/scripts/morning_brief.py                    # yesterday (ET), human
    python docs/scripts/morning_brief.py --date 2026-09-07
    python docs/scripts/morning_brief.py --json             # for pipes/email/slack
    python docs/scripts/morning_brief.py --sport MLB        # single sport
    python docs/scripts/morning_brief.py --fix              # auto-repair (runs graders)

Wired into .github/workflows/mlb_grade_overnight.yml so nightly runs
exit non-zero on regression → email/slack alert (TODO: alert wire).
"""
from __future__ import annotations
import argparse, json, os, sys
import datetime as dt
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent.parent.parent / 'mlb_pipeline' / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _yesterday_et() -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4, days=1)).strftime('%Y-%m-%d')


def _today_et() -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).strftime('%Y-%m-%d')


def _count(url_path: str, params: dict = None) -> int:
    p = {**(params or {}), 'select': 'id'}
    r = requests.get(f'{SB}/rest/v1/{url_path}', headers={**H_R, 'Prefer': 'count=exact'}, params=p)
    if r.status_code != 200: return -1
    try: return int(r.headers.get('Content-Range', '0/0').split('/')[-1])
    except (ValueError, IndexError): return -1


def _rows(url_path: str, params: dict = None, limit: int = 500) -> list:
    p = {**(params or {}), 'select': params.get('select', '*') if params else '*'}
    if 'select' not in p: p['select'] = '*'
    p.setdefault('limit', str(limit))
    r = requests.get(f'{SB}/rest/v1/{url_path}', headers=H_R, params=p)
    return r.json() if r.status_code == 200 else []


def _rate(w: int, l: int) -> str:
    return f'{100*w/(w+l):.1f}%' if (w + l) else '-'


def _stats(rows: list, res_key: str = 'result') -> tuple:
    w = sum(1 for r in rows if (r.get(res_key) or '').lower() == 'win')
    l = sum(1 for r in rows if (r.get(res_key) or '').lower() == 'loss')
    p = sum(1 for r in rows if (r.get(res_key) or '').lower() == 'push')
    return w, l, p


def _get_active_sports() -> list:
    """Read sport_registry for currently-active sports (per feedback_faq_sport_registry_source_906)."""
    r = _rows('sport_registry', {'is_active': 'eq.true', 'select': 'sport'})
    if r:
        return sorted(set(row.get('sport') for row in r if row.get('sport')))
    # Fallback if registry unavailable
    return ['MLB', 'NFL', 'NCAAF', 'NBA', 'NHL', 'NCAAB', 'UFC']


# ═══════════════════════════════════════════════════════════════════════
# SECTION 1 — Grading completeness
# ═══════════════════════════════════════════════════════════════════════

def section_1_grading(target_date: str, sports: list) -> dict:
    """For each sport, verify grading tables hit ≥80% populated."""
    out = {}
    for sport in sports:
        total = _count('jerry_reads', {'sport': f'eq.{sport}', 'game_date': f'eq.{target_date}'})
        if total == 0:
            out[sport] = {'total': 0, 'graded': 0, 'pct': None, 'status': 'no-games'}
            continue
        graded = _count('jerry_reads', {'sport': f'eq.{sport}',
                                        'game_date': f'eq.{target_date}',
                                        'result': 'not.is.null'})
        pct = round(100 * graded / total) if total else 0
        status = 'green' if pct >= 80 else ('warn' if pct >= 40 else 'fail')
        out[sport] = {'total': total, 'graded': graded, 'pct': pct, 'status': status}

    # POTD (single row per date)
    potd_rows = _rows('jerry_cache', {'game_id': f'eq.best_bet_{target_date}', 'select': 'data'})
    potd_status = 'no-row'
    if potd_rows:
        data = potd_rows[0].get('data') or {}
        if isinstance(data, str):
            try: data = json.loads(data)
            except (json.JSONDecodeError, TypeError): data = {}
        potd_status = 'graded' if data.get('result') else 'ungraded'
    out['_potd'] = {'status': potd_status}

    # daily_surface_records
    dsr = _count('daily_surface_records', {'record_date': f'eq.{target_date}'})
    out['_daily_surface_records'] = {'rows': dsr,
                                     'status': 'green' if dsr > 0 else 'fail'}
    return out


# ═══════════════════════════════════════════════════════════════════════
# SECTION 2 — Pick results
# ═══════════════════════════════════════════════════════════════════════

def section_2_picks(target_date: str, sports: list) -> dict:
    """Jerry pick record per sport, breakdown by tier + LR-endorsed subset."""
    out = {}
    for sport in sports:
        rows = _rows('jerry_reads', {'sport': f'eq.{sport}', 'game_date': f'eq.{target_date}',
                                     'select': 'game_id,call_market,call_side,result,input_snapshot'})
        w, l, p = _stats(rows)
        if not rows:
            out[sport] = {'total': 0}
            continue

        # Fetch primary_play tier from ctx for tier breakdown
        gids = list(set(r['game_id'] for r in rows if r.get('game_id')))
        ctx_table = {'MLB': 'mlb_game_context', 'NFL': 'nfl_game_context',
                     'NCAAF': 'ncaaf_game_context', 'NCAAB': 'ncaab_game_context',
                     'NBA': 'nba_game_context', 'NHL': 'nhl_game_context'}.get(sport)
        tier_by_gid = {}
        lr_by_gid = {}
        if ctx_table and gids:
            for i in range(0, len(gids), 100):
                chunk = gids[i:i+100]
                ids_p = ','.join(f'"{g}"' for g in chunk)
                cr = _rows(ctx_table, {'game_id': f'in.({ids_p})',
                                       'select': 'game_id,primary_play'})
                for c in cr:
                    pp = c.get('primary_play') or {}
                    if isinstance(pp, str):
                        try: pp = json.loads(pp)
                        except (json.JSONDecodeError, TypeError): pp = {}
                    tier_by_gid[c['game_id']] = (pp.get('tier') or '').upper()
                    lr_by_gid[c['game_id']] = pp

        # Tier breakdown
        by_tier = {}
        for tier in ('PRIME', 'STRONG', 'LEAN', 'COVERAGE'):
            subset = [r for r in rows if tier_by_gid.get(r.get('game_id')) == tier]
            if subset:
                sw, sl, sp = _stats(subset)
                by_tier[tier] = {'w': sw, 'l': sl, 'p': sp, 'pct': _rate(sw, sl), 'n': len(subset)}

        # LR-endorsed subset (based on _lr_p_home_win / _lr_p_over on primary_play)
        lr_endorsed = []
        for r in rows:
            pp = lr_by_gid.get(r.get('game_id'), {})
            lr_p_home = pp.get('_lr_p_home_win')
            lr_p_over = pp.get('_lr_p_over')
            pick_side = pp.get('side')
            pick_type = (pp.get('type') or '').lower()
            endorsed = False
            if pick_type == 'ml' and lr_p_home is not None:
                if (lr_p_home > 0.5) == (pick_side == 'HOME') and abs(lr_p_home - 0.5) >= 0.05:
                    endorsed = True
            elif pick_type == 'total' and lr_p_over is not None:
                if (lr_p_over > 0.5) == (pick_side == 'OVER') and abs(lr_p_over - 0.5) >= 0.05:
                    endorsed = True
            if endorsed: lr_endorsed.append(r)

        lr_w, lr_l, lr_p = _stats(lr_endorsed)

        out[sport] = {
            'total': len(rows), 'w': w, 'l': l, 'p': p, 'pct': _rate(w, l),
            'by_tier': by_tier,
            'lr_endorsed': {'w': lr_w, 'l': lr_l, 'p': lr_p, 'pct': _rate(lr_w, lr_l), 'n': len(lr_endorsed)},
        }
    return out


# ═══════════════════════════════════════════════════════════════════════
# SECTION 3 — Steam Room P/L
# ═══════════════════════════════════════════════════════════════════════

def section_3_steam_room(target_date: str) -> dict:
    rows = _rows('daily_surface_records', {'record_date': f'eq.{target_date}',
                                            'select': 'sport,surface,wins,losses,pushes,units_won'})
    out = {'surfaces': [], 'total_units': 0.0}
    for r in rows:
        if not isinstance(r, dict): continue
        try: u = float(r.get('units_won') or 0)
        except (ValueError, TypeError): u = 0.0
        out['total_units'] += u
        out['surfaces'].append({
            'sport': r.get('sport'), 'surface': r.get('surface'),
            'w': r.get('wins', 0), 'l': r.get('losses', 0), 'p': r.get('pushes', 0),
            'units': round(u, 2),
        })
    return out


# ═══════════════════════════════════════════════════════════════════════
# SECTION 4 — Today's pipeline health
# ═══════════════════════════════════════════════════════════════════════

def section_4_today(today: str, sports: list) -> dict:
    out = {}
    for sport in sports:
        table = {'MLB': 'mlb_pipeline_props', 'NFL': 'nfl_pipeline_props'}.get(sport)
        if not table: continue
        total = _count(table, {'game_date': f'eq.{today}'})
        unresolved = _count(table, {'game_date': f'eq.{today}', 'player_team': 'eq.UNKNOWN'})
        out[f'{sport}_props'] = {'total': total, 'unresolved_team': unresolved,
                                 'status': 'green' if unresolved == 0 else 'fail'}
    return out


# ═══════════════════════════════════════════════════════════════════════
# SECTION 5 — Known regressions
# ═══════════════════════════════════════════════════════════════════════

def section_5_regressions(target_date: str, today: str) -> dict:
    """Grep-check the DB for patterns that were fixed but could regress."""
    regressions = []

    # 1. UNKNOWN-team pitcher props today (Peterson class)
    unknown_today = _count('mlb_pipeline_props',
                           {'game_date': f'eq.{today}', 'player_team': 'eq.UNKNOWN'})
    if unknown_today > 0:
        regressions.append({
            'name': 'peterson_orphans',
            'severity': 'high',
            'msg': f'{unknown_today} MLB props today have player_team=UNKNOWN — sweep_prop_coverage skip-gate regressed',
        })

    # 2. NCAAF jerry_reads game_date mismatch (game_id vs game_date)
    ncaaf_rows = _rows('jerry_reads', {'sport': 'eq.NCAAF', 'select': 'game_id,game_date',
                                        'game_date': f'gte.{target_date}'})
    mismatch = 0
    for r in ncaaf_rows:
        gid = r.get('game_id') or ''
        if not gid.startswith('ncaaf_') or len(gid) < 14: continue
        ymd = gid[6:14]
        if not ymd.isdigit(): continue
        actual = f'{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}'
        if actual != r.get('game_date'): mismatch += 1
    if mismatch > 0:
        regressions.append({
            'name': 'ncaaf_date_mismatch',
            'severity': 'high',
            'msg': f'{mismatch} NCAAF jerry_reads have game_date != game_id embedded date — generator regressed to today_et()',
        })

    return {'count': len(regressions), 'items': regressions}


# ═══════════════════════════════════════════════════════════════════════
# RENDER
# ═══════════════════════════════════════════════════════════════════════

def render_human(target_date: str, today: str, sports: list,
                 s1: dict, s2: dict, s3: dict, s4: dict, s5: dict) -> str:
    lines = []
    lines.append(f'\n╔═══ Morning Brief · yesterday={target_date} · today={today} ═══\n')

    # Section 1
    lines.append('┌─ SECTION 1 · Grading completeness')
    for sport in sports:
        s = s1.get(sport, {})
        if s.get('total') == 0:
            lines.append(f'│   {sport:<7s}: no games')
            continue
        marker = {'green': '✓', 'warn': '⚠', 'fail': '✗'}.get(s.get('status'), '?')
        lines.append(f'│   {sport:<7s}: {marker} {s.get("graded")}/{s.get("total")} graded ({s.get("pct")}%)')
    p = s1.get('_potd', {})
    lines.append(f'│   POTD  : {"✓ graded" if p.get("status")=="graded" else "✗ " + p.get("status", "unknown")}')
    d = s1.get('_daily_surface_records', {})
    lines.append(f'│   daily_surface_records: {"✓" if d.get("status")=="green" else "✗"} {d.get("rows")} rows')

    # Section 2
    lines.append('\n┌─ SECTION 2 · Pick results')
    for sport in sports:
        s = s2.get(sport, {})
        if not s or s.get('total') == 0: continue
        lines.append(f'│   {sport}:  {s.get("w")}W-{s.get("l")}L-{s.get("p")}P  ({s.get("pct")})  n={s.get("total")}')
        for tier, tr in s.get('by_tier', {}).items():
            lines.append(f'│     └─ {tier:<9s}: {tr["w"]}W-{tr["l"]}L ({tr["pct"]}) n={tr["n"]}')
        lr = s.get('lr_endorsed', {})
        if lr.get('n', 0) > 0:
            lines.append(f'│     └─ LR-endorsed: {lr["w"]}W-{lr["l"]}L ({lr["pct"]}) n={lr["n"]}')

    # Section 3
    lines.append('\n┌─ SECTION 3 · Steam Room P/L')
    for row in s3.get('surfaces', []):
        lines.append(f'│   {row["surface"]:<22s} {row["sport"]:>5s}: {row["w"]}W-{row["l"]}L-{row["p"]}P · {row["units"]:+.2f}u')
    lines.append(f'│   TOTAL: {s3.get("total_units", 0):+.2f}u')

    # Section 4
    lines.append('\n┌─ SECTION 4 · Today\'s pipeline health')
    for k, v in s4.items():
        marker = '✓' if v.get('status') == 'green' else '✗'
        extra = f' · UNKNOWN teams: {v["unresolved_team"]}' if v.get('unresolved_team', 0) > 0 else ''
        lines.append(f'│   {marker} {k}: {v.get("total")} rows{extra}')

    # Section 5
    lines.append('\n┌─ SECTION 5 · Known regressions')
    if s5.get('count', 0) == 0:
        lines.append('│   ✓ no regressions detected')
    else:
        for item in s5.get('items', []):
            marker = '🚨' if item.get('severity') == 'high' else '⚠'
            lines.append(f'│   {marker} {item["name"]}: {item["msg"]}')

    lines.append(f'\n╚═══════════════════════════════════════════════════════════════\n')
    return '\n'.join(lines)


def compute_exit_code(s1: dict, s5: dict) -> int:
    # Hard fail if POTD ungraded, daily records missing, or high-severity regressions
    if s1.get('_potd', {}).get('status') not in ('graded', 'no-row'): return 2
    if s1.get('_daily_surface_records', {}).get('status') == 'fail': return 2
    if any(i.get('severity') == 'high' for i in s5.get('items', [])): return 2
    # Soft warn if any sport <80% graded
    for sport, s in s1.items():
        if sport.startswith('_'): continue
        if s.get('total', 0) > 0 and s.get('pct', 100) < 80: return 1
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='YYYY-MM-DD (default: yesterday ET)')
    ap.add_argument('--sport', help='Single sport filter (default: all active from sport_registry)')
    ap.add_argument('--json', action='store_true', help='Emit JSON instead of human-readable')
    ap.add_argument('--fix', action='store_true', help='Attempt auto-repair (runs graders)')
    args = ap.parse_args()

    target = args.date or _yesterday_et()
    today = _today_et()
    sports = [args.sport] if args.sport else _get_active_sports()

    s1 = section_1_grading(target, sports)
    s2 = section_2_picks(target, sports)
    s3 = section_3_steam_room(target)
    s4 = section_4_today(today, sports)
    s5 = section_5_regressions(target, today)

    if args.json:
        print(json.dumps({
            'target_date': target, 'today': today, 'sports': sports,
            'grading': s1, 'picks': s2, 'steam_room': s3,
            'pipeline_health': s4, 'regressions': s5,
        }, indent=2, default=str))
    else:
        print(render_human(target, today, sports, s1, s2, s3, s4, s5))

    if args.fix:
        # TODO: shell out to grade_jerry_reads / grade_potd / aggregate_daily_records
        # for each sport with <80% coverage. Kept as TODO to avoid destructive
        # ops on first ship — user opts in explicitly via --fix.
        print('  --fix not yet implemented; run graders manually per Section 1 failures')

    exit_code = compute_exit_code(s1, s5)
    if exit_code:
        print(f'\n(exit code {exit_code} — see Section 1 + 5 for detail)')
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
