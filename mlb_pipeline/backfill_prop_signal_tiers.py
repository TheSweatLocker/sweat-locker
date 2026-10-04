"""Backfill signal_registry tiers for PROP signals (2026-08-18).

Sister to backfill_signal_tiers.py — but iterates *props* instead of games.
Prop signals live in signal_sources with class LIKE 'prop_%' and reference
`p` (the prop row) in their condition_expr. The game-level backfill can't
evaluate them because it binds `ctx` to a game_context row; prop signals
need the prop row with its `signals` JSON column populated.

Grading model:
  For each resolved prop (result IN Win/Loss/Push):
    - Evaluate condition_expr with {'ctx': None, 'p': prop_dict}
    - If fires, evaluate side_expr → 'BACK' or 'FADE'
    - BACK: side agrees with the prop's direction (endorsed the pick)
        → W if prop.result == 'Win', L if 'Loss', P if 'Push'
    - FADE: side opposes the prop's direction (would have flipped it)
        → W if prop.result == 'Loss', L if 'Win', P if 'Push'
  Then n = W + L; hit_rate = W/n; tier assignment matches game backfill.

Same tier rules + weight mapping as backfill_signal_tiers.py so the
playbook ensemble consumes both from a uniform signal_registry.

CLI:
  python backfill_prop_signal_tiers.py                      # MLB, 60 days
  python backfill_prop_signal_tiers.py --sport MLB --days 90
  python backfill_prop_signal_tiers.py --dry-run
  python backfill_prop_signal_tiers.py --signal-key pitcher_l5_confirm
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

import requests

# Same ctx access semantics as live scoring — the module docstring commits to
# "evaluation semantics are identical between live scoring and backfill
# grading", so import the proxy rather than reimplement it and let the two
# drift.
from prop_ensemble_scorer import _CtxProxy
from prop_ctx_resolve import build_ctx_index, resolve_ctx, derive_side_fields

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


PROP_TABLES = {
    'MLB':   'mlb_pipeline_props',
    'NFL':   'nfl_pipeline_props',
    'NCAAF': 'ncaaf_pipeline_props',
    'NBA':   'nba_pipeline_props',
    'NHL':   'nhl_pipeline_props',
}


def fetch_prop_signals(sport: str) -> list[dict]:
    r = requests.get(f'{SB}/rest/v1/signal_sources',
                     headers=H_READ,
                     params={'select': '*', 'sport': f'eq.{sport}',
                             'class': 'like.prop_%', 'enabled': 'eq.true'},
                     timeout=15)
    return r.json() if r.status_code == 200 else []


def fetch_resolved_props(sport: str, days: int) -> list[dict]:
    """Pull graded props from the last N days. Only rows with result set."""
    table = PROP_TABLES.get(sport)
    if not table: return []
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    rows = []
    for off in range(0, 50000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/{table}'
            f'?game_date=gte.{cutoff}&game_date=lte.{yesterday}'
            f'&result=in.(Win,Loss,Push)'
            f'&select=*&limit=1000&offset={off}',
            headers=H_READ, timeout=45)
        chunk = r.json() if r.status_code == 200 else []
        rows += chunk
        if len(chunk) < 1000: break
    return rows


CTX_TABLES = {
    'MLB': 'mlb_game_context',
    'NFL': 'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NHL': 'nhl_game_context',
    'NBA': 'nba_game_context',
}


def fetch_context_window(sport: str, days: int) -> list[dict]:
    """Game-context rows covering the grading window.

    Returns [] on any failure rather than raising: a missing context table
    must degrade grading to "ctx-scoped signals ungradable", never kill the
    whole run for the form/model signals that need no context.
    """
    table = CTX_TABLES.get(sport)
    if not table:
        return []
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = []
    for off in range(0, 50000, 1000):
        try:
            r = requests.get(
                f'{SB}/rest/v1/{table}'
                f'?game_date=gte.{cutoff}&select=*&limit=1000&offset={off}',
                headers=H_READ, timeout=45)
        except Exception:
            break
        if r.status_code != 200:
            print(f'    ! {table} read {r.status_code} — ctx-scoped signals '
                  f'will be ungradable')
            break
        chunk = r.json()
        if not isinstance(chunk, list) or not chunk:
            break
        rows += chunk
        if len(chunk) < 1000:
            break
    return rows


def _coerce_signals(p: dict) -> dict:
    """PostgREST may return jsonb as string on some paths — parse it once."""
    s = p.get('signals')
    if isinstance(s, str):
        try: p['signals'] = json.loads(s)
        except Exception: p['signals'] = {}
    return p


def _matches_market(source_row: dict, prop: dict) -> bool:
    """Mirror prop_ensemble_scorer._matches_market so we grade the same
    subset the live scorer would evaluate."""
    scope = (source_row.get('market_scope') or '').lower()
    if not scope or scope == '*': return True
    ptype = (prop.get('prop_type') or '').lower()
    if scope == 'pitcher':
        return any(ptype.startswith(x) for x in ('bb_', 'ha_', 'ks_', 'outs_', 'er_'))
    if scope == 'hits':
        return ptype.startswith('hits_')
    if scope == ptype: return True
    if ptype.startswith(scope + '_'): return True
    return False


_SAFE_BUILTINS = {'min': min, 'max': max, 'abs': abs, 'int': int, 'float': float,
                   'str': str, 'sum': sum, 'len': len, 'any': any, 'all': all,
                   'round': round, 'None': None, 'True': True, 'False': False,
                   'isinstance': isinstance, 'dict': dict, 'list': list, 'bool': bool}


def _safe_eval(expr: str, env: dict):
    """Same sandbox as prop_ensemble_scorer._safe_eval so evaluation semantics
    are identical between live scoring and backfill grading."""
    if not expr: return None
    locs = {**_SAFE_BUILTINS, **env}
    # Env goes into GLOBALS as well as locals. In eval(code, globs, locs) a
    # generator expression's body executes in its own frame that can see globs
    # but NOT locs, so `any(w in str(p['x']) for w in (...))` raises
    # NameError on `str` and `p` while the same expression without a genexp
    # works. That silently broke every ctx/matchup signal written with `any(`
    # — they reported "did not fire" on 5,889 NHL props.
    globs = {'__builtins__': {}, **locs}
    try:
        return eval(compile(expr, '<prop_signal_expr>', 'eval'), globs, locs)
    except Exception:
        return None


def grade_prop_signal(side: str, prop_result: str) -> str | None:
    """BACK endorses direction → W on prop Win, L on prop Loss.
    FADE opposes → W on prop Loss (correctly faded), L on prop Win."""
    if prop_result == 'Push': return 'P'
    if side == 'BACK':
        if prop_result == 'Win': return 'W'
        if prop_result == 'Loss': return 'L'
    elif side == 'FADE':
        if prop_result == 'Loss': return 'W'
        if prop_result == 'Win': return 'L'
    return None


def _eval_condition(expr: str, env: dict):
    """-> (matched: bool, errored: bool).

    `_safe_eval` returns None both when a condition is FALSE and when it
    RAISED. For a ctx-dependent condition against an empty context,
    `ctx.away_goalie_sv_pct` is None and `float(None)` raises — which then
    reads as "the signal did not fire". That is how four NHL prop signals came
    to sit at sample_n=0 on 5,889 graded props and be mistaken for evidence.
    Separating the two is the whole point.
    """
    if not expr:
        return (False, False)
    locs = {**_SAFE_BUILTINS, **env}
    globs = {'__builtins__': {}, **locs}   # genexp scoping — see _safe_eval
    try:
        return (bool(eval(compile(expr, '<prop_signal_expr>', 'eval'), globs, locs)),
                False)
    except Exception:
        return (False, True)


def backfill_prop_signal(source: dict, props: list[dict],
                         ctx_index: dict | None = None) -> dict:
    condition = source.get('condition_expr') or ''
    side_expr = source.get('side_expr') or ''
    if not condition or not side_expr:
        return {'skipped': True, 'reason': 'missing expr'}

    w = l = p = 0
    fires = 0
    # ══ 2026-10-03 · THE DIRECTION-ONLY CONTROL ══
    # Grade a free alternative on exactly the same fired rows: "BACK every
    # under, FADE every over". Prop families are wildly asymmetric — graded NHL
    # props run OVER 23.9% vs UNDER 69.3% — so a signal whose side_expr is
    # keyed on direction inherits that base rate and validates on it alone.
    #
    #   nhl_prop_l5_cold   side_expr: "'BACK' if direction == 'under' else 'FADE'"
    #                      hit 79.1% (n=1,216) · direction-only 79.1% · lift +0.0pp
    #
    # That signal was VALIDATED at weight 1.0 while carrying literally zero
    # information beyond the direction already on the row. Measuring lift over
    # this control is what separates a form signal from a relabelled coin.
    dw = dl = 0
    errored = ctx_miss = 0
    for prop in props:
        if not _matches_market(source, prop): continue
        prop = _coerce_signals(prop)
        # Pass a REAL context. This used to be `{'ctx': None}` literally, so
        # any ctx-dependent condition raised and was counted as "did not fire".
        ctx_row, how = (resolve_ctx(ctx_index, prop) if ctx_index else ({}, 'miss'))
        if how == 'miss':
            ctx_miss += 1
        # own_*/opp_* so a condition never has to re-derive the player's side
        # from mismatched team vocabularies. Does not overwrite real prop
        # fields — the prop row wins on any key collision.
        _p = {**derive_side_fields(ctx_row, prop), **prop}
        env = {'ctx': _CtxProxy(ctx_row), 'p': _p}
        matched, err = _eval_condition(condition, env)
        if err:
            errored += 1
            continue
        if not matched: continue
        fires += 1
        side_raw = _safe_eval(side_expr, env)
        side = str(side_raw).upper() if side_raw else ''
        if side not in ('BACK', 'FADE'): continue
        result = grade_prop_signal(side, prop.get('result'))
        if result == 'W': w += 1
        elif result == 'L': l += 1
        # control: direction alone, same row, same grading function
        _d = str(prop.get('direction') or '').lower()
        if _d in ('over', 'under'):
            _ctl = grade_prop_signal('BACK' if _d == 'under' else 'FADE',
                                     prop.get('result'))
            if _ctl == 'W': dw += 1
            elif _ctl == 'L': dl += 1
        elif result == 'P': p += 1

    n_dec = w + l
    hit_rate = round(100 * w / n_dec, 1) if n_dec else None
    edge_pp = round(hit_rate - 52.4, 1) if hit_rate is not None else None

    # Direction-only control on the same fired rows (see the loop above).
    n_ctl = dw + dl
    base_hit = round(100 * dw / n_ctl, 1) if n_ctl else None
    lift = (round(hit_rate - base_hit, 1)
            if hit_rate is not None and base_hit is not None else None)

    # Tier assignment — now requires LIFT over the direction-only control, not
    # just a hit rate over a flat 52.4%. nhl_backfill_signal_tiers.py already
    # took this lesson for the rl scope ("a signal can no longer false-validate
    # against a league average"); the prop grader never received it.
    #
    # A signal that merely matches or trails the control is UNVALIDATED, NOT
    # anti-validated: underperforming a baseline does not imply that inverting
    # the signal is profitable. ANTI_VALIDATED flips direction_hint to FADE and
    # is an assertion that the opposite side wins, so it stays reserved for a
    # genuinely bad raw hit rate.
    if n_dec < 15:
        tier = 'UNVALIDATED'
    elif (hit_rate is not None and n_dec >= 25 and hit_rate <= 48.0
          and (lift is None or lift <= -5.0)):
        tier = 'ANTI_VALIDATED'
    elif lift is not None and lift <= -2.0:
        # No information beyond the direction already on the row.
        tier = 'UNVALIDATED'
    elif (hit_rate is not None and n_dec >= 50 and hit_rate >= 55.0
          and lift is not None and lift >= 4.0):
        tier = 'VALIDATED'
    elif (hit_rate is not None and hit_rate >= 52.4
          and lift is not None and lift >= 2.0):
        tier = 'DISCOVERY'
    else:
        tier = 'UNVALIDATED'

    weight = {'VALIDATED': 1.0, 'DISCOVERY': 0.5,
              'UNVALIDATED': 0.3, 'ANTI_VALIDATED': 0.0}[tier]

    return {
        'fires': fires, 'w': w, 'l': l, 'p': p, 'n_dec': n_dec,
        'hit_rate': hit_rate, 'edge_pp': edge_pp, 'tier': tier,
        'recommended_weight': weight,
        'base_hit': base_hit, 'lift': lift, 'n_ctl': n_ctl,
        'errored': errored, 'ctx_miss': ctx_miss,
    }


def write_registry(source: dict, stats: dict, dry_run: bool = False) -> bool:
    if dry_run: return True
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        'signal_name': source['signal_key'],
        'sport': source['sport'],
        'market_scope': source.get('market_scope', 'prop'),
        'category': source['class'],
        'description': source.get('description') or source.get('display_prose_template') or '',
        'hit_rate': stats['hit_rate'],
        'sample_n': stats['n_dec'],
        'edge_pp': stats['edge_pp'],
        'tier': stats['tier'],
        'recommended_weight': stats['recommended_weight'],
        'direction_hint': 'FADE' if stats['tier'] == 'ANTI_VALIDATED' else 'FOLLOW',
        'origin': f'PROP_BACKFILL_{date.today().isoformat()}',
        'last_computed_at': now_iso,
        'updated_at': now_iso,
    }
    pr = requests.post(
        f'{SB}/rest/v1/signal_registry?on_conflict=signal_name,sport,market_scope',
        headers=H_WRITE, json=[payload], timeout=15)
    if pr.status_code not in (200, 201, 204):
        print(f'    x write failed: {pr.status_code} {pr.text[:150]}')
        return False
    return True


def run(days: int = 60, dry_run: bool = False,
         signal_key_filter: str | None = None, sport: str = 'MLB'):
    print(f'=== prop signal backfill-to-tier · {sport} · last {days} days ===')
    signals = fetch_prop_signals(sport)
    if signal_key_filter:
        signals = [s for s in signals if s['signal_key'] == signal_key_filter]
    print(f'  {len(signals)} prop signal_sources rows to evaluate')

    props = fetch_resolved_props(sport, days)
    print(f'  {len(props)} resolved props in window\n')
    if not props:
        print('  no resolved props — abort')
        return

    # Load the context window so ctx-dependent conditions can actually be
    # evaluated. Context is a working set, not an archive, so coverage over a
    # 60-day grading window is partial by nature — report it rather than let a
    # thin join masquerade as a thin signal.
    ctx_index = build_ctx_index(fetch_context_window(sport, days))
    _n_ctx = len(ctx_index.get('by_id') or {})
    _resolved = sum(1 for p in props if resolve_ctx(ctx_index, p)[1] != 'miss')
    print(f'  {_n_ctx} context rows loaded · '
          f'{_resolved}/{len(props)} props resolve to a context row '
          f'({100.0 * _resolved / max(1, len(props)):.1f}%)')
    if _resolved < len(props):
        print(f'  NOTE: {len(props) - _resolved} props have no context in this '
              f'window — ctx-scoped signals cannot be graded on those rows.\n')
    else:
        print()

    tier_counts = defaultdict(int)
    written = 0
    for source in signals:
        cls = source.get('class', '')
        key = source['signal_key']
        stats = backfill_prop_signal(source, props, ctx_index=ctx_index)
        if stats.get('skipped'):
            print(f'  {key:<40} [{cls:<18}] SKIP ({stats["reason"]})')
            continue
        hr = stats['hit_rate']; n = stats['n_dec']
        fires = stats['fires']; tier = stats['tier']; edge = stats['edge_pp']
        hr_str = f'{hr}%' if hr is not None else '--'
        edge_str = f'{edge:+.1f}pp' if edge is not None else ''
        # Show the direction-only control and the lift over it. edge_pp alone
        # (hit - 52.4) reads as +26.7pp next to a demotion and invites exactly
        # the misreading this fix exists to stop.
        base = stats.get('base_hit'); lift = stats.get('lift')
        ctl_str = (f'dir={base}% lift={lift:+.1f}pp'
                   if base is not None and lift is not None else '')
        # A signal that never fired because its condition RAISED is a plumbing
        # fact, not evidence about the sport. Say so on the line.
        _err = stats.get('errored') or 0
        if _err and fires == 0:
            ctl_str = f'UNGRADABLE: {_err} rows raised'
        elif _err:
            ctl_str = (ctl_str + f' ({_err} raised)').strip()
        print(f'  {key:<40} [{cls:<18}] fires={fires:>4} n={n:>3} '
              f'{stats["w"]}-{stats["l"]}-{stats["p"]}  HR={hr_str:<7} '
              f'{edge_str:<8} {ctl_str:<26} tier={tier}')
        tier_counts[tier] += 1
        if write_registry(source, stats, dry_run=dry_run):
            written += 1

    print(f'\n--- summary ---')
    for t in ('VALIDATED', 'DISCOVERY', 'UNVALIDATED', 'ANTI_VALIDATED'):
        if tier_counts.get(t):
            print(f'  {t:<16} {tier_counts[t]}')
    print(f'\n  {"[DRY] " if dry_run else ""}wrote {written} signal_registry rows')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=60)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--signal-key')
    p.add_argument('--sport', default='MLB',
                   choices=list(PROP_TABLES.keys()) + ['ALL'])
    args = p.parse_args()
    if args.sport == 'ALL':
        for s in PROP_TABLES:
            run(days=args.days, dry_run=args.dry_run,
                signal_key_filter=args.signal_key, sport=s)
    else:
        run(days=args.days, dry_run=args.dry_run,
            signal_key_filter=args.signal_key, sport=args.sport)


if __name__ == '__main__':
    main()
