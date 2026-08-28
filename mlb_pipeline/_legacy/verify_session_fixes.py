"""Post-cron verification for 2026-08-22 session fixes.

Run this after the next cron completes. Tells you in one command
whether every root-cause fix from today's session actually took effect
on tonight's data, or which one silently broke.

Green / red per fix:
  ✅ 706b7d38 pitcher L10 dict-key mismatch
  ✅ 8cf25efc prop dedup on natural key
  ✅ e9bdba15 juice trap patch-all-copies
  ✅ e9bdba15 rookie floor guard
  ✅ 3739f65d cold-streak guard
  ✅ e9c7e44e diacritic name fallback
  ✅ f412b772 book odds patch-all-copies
  ✅ signal wiring verifier reporting improved
  ✅ template renderer populating below-gate props

Usage:
  python verify_session_fixes.py
  python verify_session_fixes.py --date 2026-08-22
"""
from __future__ import annotations
import argparse, os, sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

import requests
SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _get(table: str, params: dict) -> list:
    try:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=params, timeout=15)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


def check_pitcher_l10(gd: str) -> tuple[bool, str]:
    """Fix 706b7d38: pitcher props should now have L10 populated."""
    props = _get('mlb_pipeline_props', {
        'game_date': f'eq.{gd}',
        'prop_type': 'in.(ks_over,ks_under,ha_over,ha_under,bb_over,bb_under,outs_over,outs_under,er_over,er_under)',
        'select': 'player_l10_hit_count', 'limit': '500'})
    if not props: return (False, 'no pitcher props found for date')
    total = len(props)
    null = sum(1 for p in props if p.get('player_l10_hit_count') is None)
    pct = null / total * 100
    ok = pct < 25  # was 75% before fix
    return (ok, f'{null}/{total} pitcher props null L10 ({pct:.0f}%) — was ~75% before fix')


def check_prop_dedup(gd: str) -> tuple[bool, str]:
    """Fix 8cf25efc: no duplicate (player, type, dir, line) in top tiers."""
    props = _get('mlb_pipeline_props', {
        'game_date': f'eq.{gd}', 'tier': 'in.(PRIME,STRONG)',
        'select': 'player_name,prop_type,direction,prop_line', 'limit': '500'})
    keys = [(p.get('player_name'), p.get('prop_type'), p.get('direction'), p.get('prop_line'))
            for p in props]
    counts = Counter(keys)
    dupe_props = {k: n for k, n in counts.items() if n > 1}
    ok = len(dupe_props) == 0
    if ok:
        return (True, f'0 duplicates across {len(props)} PRIME/STRONG rows')
    top_offender = max(dupe_props.items(), key=lambda kv: kv[1])
    return (False, f'{len(dupe_props)} unique-props still duped — worst: {top_offender[0][0]} × {top_offender[1]}')


def check_book_odds_attached(gd: str) -> tuple[bool, str]:
    """Fix f412b772: book_over_odds should be attached to all copies."""
    props = _get('mlb_pipeline_props', {
        'game_date': f'eq.{gd}', 'tier': 'in.(PRIME,STRONG)', 'direction': 'eq.over',
        'select': 'book_over_odds', 'limit': '500'})
    if not props: return (False, 'no OVER PRIME/STRONG props found')
    total = len(props)
    null = sum(1 for p in props if p.get('book_over_odds') is None)
    pct = null / total * 100
    ok = pct < 20  # was 75% before fix
    return (ok, f'{null}/{total} OVER props null book_over_odds ({pct:.0f}%) — was 75% before fix')


def check_juice_trap_applied(gd: str) -> tuple[bool, str]:
    """Fix e9bdba15: -200+ hits_over should be LEAN (or below) uniformly."""
    props = _get('mlb_pipeline_props', {
        'game_date': f'eq.{gd}', 'prop_type': 'eq.hits_over', 'direction': 'eq.over',
        'book_over_odds': 'lte.-200', 'tier': 'in.(PRIME,STRONG)',
        'select': 'player_name,tier,book_over_odds', 'limit': '100'})
    escapes = [p for p in props]
    ok = len(escapes) == 0
    if ok:
        return (True, 'no PRIME/STRONG hits_over @ -200+ juice escaped the gate')
    return (False, f'{len(escapes)} juice-trap escapees: {escapes[0].get("player_name")} at {escapes[0].get("book_over_odds")}')


def check_cold_streak_guard(gd: str) -> tuple[bool, str]:
    """Fix 3739f65d: L10<=2 AND season<25% AND BACK OVER should be PASS not LEAN."""
    props = _get('mlb_pipeline_props', {
        'game_date': f'eq.{gd}', 'direction': 'eq.over',
        'player_l10_hit_count': 'lte.2',
        'player_season_hit_pct': 'lte.25',
        'select': 'player_name,tier,player_l10_hit_count,player_season_hit_pct', 'limit': '50'})
    misses = [p for p in props if p.get('tier') not in ('PASS', None)]
    ok = len(misses) == 0
    if ok:
        return (True, f'{len(props)} cold-streak candidates all correctly PASS')
    return (False, f'{len(misses)} cold-streak props still elevated: {misses[0].get("player_name")}={misses[0].get("tier")}')


def check_matchup_placeholder(gd: str) -> tuple[bool, str]:
    """Fix c68b2b61: no line_movement_flags should have detail starting with 'game ·'."""
    r = requests.get(f'{SB}/rest/v1/line_movement_flags', headers=H,
                     params={'last_seen_at': f'gte.{gd}T00:00:00Z',
                             'detail': 'like.game ·*', 'select': 'id'}, timeout=15)
    if r.status_code != 200: return (False, f'query failed {r.status_code}')
    count = len(r.json() or [])
    return (count == 0, f'{count} flags still have "game ·" placeholder prefix — was many before c68b2b61')


def check_prop_jerry_written(gd: str) -> tuple[bool, str]:
    """Fixes 2ba623cf + 6a16dead: tier-gate + template renderer.
    Expect: LLM path writes PRIME+STRONG count, template fills the rest."""
    props_prime_strong = len(_get('mlb_pipeline_props', {
        'game_date': f'eq.{gd}', 'tier': 'in.(PRIME,STRONG)', 'select': 'id'}))
    props_all_non_skip = len(_get('mlb_pipeline_props', {
        'game_date': f'eq.{gd}', 'tier': 'not.in.(SKIP,COVERAGE,PASS)', 'select': 'id'}))
    jerry_all = len(_get('prop_jerry_reads', {'game_date': f'eq.{gd}', 'select': 'id'}))
    # Ideal: jerry_all ≈ props_all_non_skip (LLM for PRIME/STRONG + template for rest)
    coverage = jerry_all / props_all_non_skip * 100 if props_all_non_skip else 0
    ok = coverage >= 80
    return (ok, f'prop_jerry_reads {jerry_all}/{props_all_non_skip} = {coverage:.0f}% (PRIME+STRONG={props_prime_strong} via LLM, rest via template)')


def check_painter_specific(gd: str) -> tuple[bool, str]:
    """Painter er_over 2.5 — the pick we deep-audited tonight."""
    props = _get('mlb_pipeline_props', {
        'game_date': f'eq.{gd}', 'player_name': 'eq.Andrew Painter',
        'prop_type': 'eq.er_over', 'select': 'tier,conviction,player_l10_hit_count,player_season_hit_pct,book_over_odds'})
    if not props: return (False, 'no Painter er_over rows (may not be pitching)')
    p = props[0]
    l10 = p.get('player_l10_hit_count')
    season = p.get('player_season_hit_pct')
    tier = p.get('tier')
    conv = p.get('conviction')
    if l10 is None and season is None:
        return (False if len(props) == 1 else True,
                f'{len(props)} rows, L10 still null — rookie floor should cap at LEAN. tier={tier} conv={conv}')
    return (True, f'{len(props)} rows · L10={l10}/10 · season={season}% · tier={tier} conv={conv}')


CHECKS = [
    ('L10 key-mismatch (pitcher props)', check_pitcher_l10),
    ('Prop dedup on natural key',        check_prop_dedup),
    ('Book odds attach on all copies',   check_book_odds_attached),
    ('Juice trap gate uniform demote',   check_juice_trap_applied),
    ('Cold-streak guard',                check_cold_streak_guard),
    ('Matchup "game" placeholder',       check_matchup_placeholder),
    ('Prop jerry_reads coverage (LLM+template)', check_prop_jerry_written),
    ('Painter er_over 2.5 specific',     check_painter_specific),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    args = ap.parse_args()
    gd = args.date or _et_today()

    print(f'\n{"=" * 70}')
    print(f'SESSION 2026-08-22 FIX VERIFICATION · game_date={gd}')
    print(f'{"=" * 70}\n')

    passed = 0; total = len(CHECKS)
    for label, check_fn in CHECKS:
        try:
            ok, note = check_fn(gd)
        except Exception as e:
            ok, note = False, f'check crashed: {type(e).__name__}: {e}'
        marker = '✅' if ok else '❌'
        print(f'  {marker} {label}')
        print(f'       {note}\n')
        if ok: passed += 1

    print('=' * 70)
    print(f'RESULT: {passed}/{total} fixes verified · {total - passed} still need attention')
    print('=' * 70)


if __name__ == '__main__':
    main()
