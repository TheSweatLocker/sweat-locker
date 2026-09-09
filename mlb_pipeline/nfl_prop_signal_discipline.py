"""nfl_prop_signal_discipline — post-generation cleanup for NFL props.

Fixes two systemic bugs surfaced 2026-09-08 on Week 1 slate audit:

1. **Alt-line conflicts** — book posts multiple lines per (player, prop_type).
   Stevenson rush_yds OVER 44.5 (conv 93) AND UNDER 58.5 (conv 100) both
   shipped as STRONG because scorer processed each line independently.
   Reads as "model is broken" to users; is actually alt-line +EV on both.
   Fix: dedupe to ONE row per (player, prop_type) — keep highest conviction.

2. **Empty signal_sources calibration → all STRONG default** — signals field
   is populated with l4/season_avg/edge_pct but there's no per-signal
   hit-rate registry for NFL props, so every edge-detected prop defaults
   to STRONG. 515 Week 1 props: 416 STRONG (81%). Not remotely calibrated.
   Fix (interim): hard-cap NFL prop tier at LEAN pre-Week 4. Kills the
   "everything is STRONG" default while real calibration is built.

Run after nfl_generate_props.py. Idempotent.

USAGE:
    python nfl_prop_signal_discipline.py                    # today+7d window
    python nfl_prop_signal_discipline.py --days 14
    python nfl_prop_signal_discipline.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# 2026-09-08 v2: real multi-signal confluence gate. Replaces the flat
# LEAN cap. Uses signals already populated on the prop row (l4, l5, l10,
# season_avg, _stat_last10 hit rate) to grade tier by CONFLUENCE not just
# edge_pct. Every "STRONG" now has to earn it via multi-signal agreement.
TIER_ORDER = {'PRIME': 4, 'STRONG': 3, 'LEAN': 2, 'LIGHT': 1, 'COVERAGE': 0, 'SKIP': -1, 'PASS': -2}


def _agrees_with_pick(avg_val, line, direction) -> bool | None:
    """Does the historical average sit on the pick's side of the line?
    over pick + avg > line = agrees. under pick + avg < line = agrees.
    Returns None if we can't compute (missing data)."""
    if avg_val is None or line is None or direction not in ('over', 'under'):
        return None
    try:
        avg = float(avg_val); ln = float(line)
    except (TypeError, ValueError):
        return None
    if direction == 'over':  return avg > ln
    if direction == 'under': return avg < ln
    return None


def _player_l10_hit_pct(last10: list, line, direction) -> tuple[float | None, int]:
    """From _stat_last10 array of {value, opp, week, season}, count how
    many games the player's actual stat landed on the pick's side of the
    current line. Returns (hit_pct, n) or (None, 0) if unusable."""
    if not isinstance(last10, list) or not last10 or line is None:
        return (None, 0)
    if direction not in ('over', 'under'):
        return (None, 0)
    try: ln = float(line)
    except (TypeError, ValueError): return (None, 0)
    n = 0; hits = 0
    for g in last10:
        v = g.get('value') if isinstance(g, dict) else None
        if v is None: continue
        try: val = float(v)
        except (TypeError, ValueError): continue
        n += 1
        if direction == 'over' and val > ln: hits += 1
        elif direction == 'under' and val < ln: hits += 1
    if n == 0: return (None, 0)
    return (hits / n, n)


def compute_confluence_tier(prop: dict) -> tuple[str, int, dict]:
    """Multi-signal confluence gate WITH RECENCY WEIGHTING.

    Rolling week-by-week: every signal comes from windows that shift as
    new games play. `_stat_last10` rolls, l4/l5/l10 averages shift each
    week. Runs every workflow cron post-game so tier assignments always
    reflect the freshest data.

    Signal weights (favor recent form — a hot player in L4 matters more
    than season-long baseline for prop picking):
      L4 avg agree     → 1.5 pts   (recent form, weighted heaviest)
      L5 avg agree     → 1.25 pts  (rolling week window)
      L10 avg agree    → 1.0 pt    (medium-term stability)
      Season avg agree → 0.75 pts  (old data, dampened)
      L10 hit ≥60%     → 1.25 pts  (direct pick-side historical hit rate)
    Total possible: 5.75 pts

    Tier ladder (edge_pct floor + weighted-agree score):
      PRIME:  edge ≥15% AND score ≥4.25 AND L10 hit ≥65%
      STRONG: edge ≥10% AND score ≥3.0
      LEAN:   edge ≥7%  AND score ≥2.0
      LIGHT:  edge ≥5%  (bare edge, no confluence)
      SKIP:   below LIGHT threshold or insufficient valid signals
    """
    signals = prop.get('signals') or {}
    if not isinstance(signals, dict):
        return ('SKIP', 0, {'reason': 'no_signals_dict'})

    direction = (prop.get('direction') or '').lower()
    line = prop.get('book_line')
    edge_pct_raw = signals.get('edge_pct')
    try: edge_pct = abs(float(edge_pct_raw)) if edge_pct_raw is not None else 0.0
    except (TypeError, ValueError): edge_pct = 0.0

    l4     = signals.get('l4')
    l5     = signals.get('_stat_avg_l5')
    l10    = signals.get('_stat_avg_l10')
    season = signals.get('_stat_avg_season')
    last10 = signals.get('_stat_last10') or []

    a_l4 = _agrees_with_pick(l4, line, direction)
    a_l5 = _agrees_with_pick(l5, line, direction)
    a_l10 = _agrees_with_pick(l10, line, direction)
    a_season = _agrees_with_pick(season, line, direction)
    hit_pct, hit_n = _player_l10_hit_pct(last10, line, direction)
    a_hit = (hit_pct is not None and hit_pct >= 0.60 and hit_n >= 5)

    # Recency-weighted score. Higher weight on L4/L5 (freshest signal).
    weights = {'l4': 1.5, 'l5': 1.25, 'l10': 1.0, 'season': 0.75, 'hit': 1.25}
    checks = {'l4': a_l4, 'l5': a_l5, 'l10': a_l10, 'season': a_season, 'hit': a_hit}
    score = sum(weights[k] for k, v in checks.items() if v is True)
    max_score = sum(weights[k] for k, v in checks.items() if v is not None)
    valid_ct = sum(1 for v in checks.values() if v is not None)

    breakdown = {
        'edge_pct': edge_pct,
        'l4_agree': a_l4, 'l5_agree': a_l5, 'l10_agree': a_l10,
        'season_agree': a_season, 'hit_agree': a_hit,
        'l10_hit_pct': round(hit_pct, 3) if hit_pct is not None else None,
        'l10_hit_n': hit_n,
        'weighted_score': round(score, 2),
        'max_possible': round(max_score, 2),
        'valid_ct': valid_ct,
    }

    if valid_ct < 2:
        return ('SKIP', 0, {**breakdown, 'reason': f'insufficient_signal_valid={valid_ct}'})

    # 2026-09-08 EXTREME-EDGE GUARD. Edge_pct > 40 is almost always data
    # noise — small-sample player, role misclassification (backup rated
    # as bulk), team mis-attribution, unusually low line vs an outlier
    # projection. Cap at LEAN when edge is implausible, so downstream
    # never PRIME-promotes obvious garbage. Real +18% edges (which do
    # exist and are the model's bread and butter) still land PRIME.
    if edge_pct > 40:
        return ('LEAN', min(65, 40 + int(min(edge_pct, 60))),
                {**breakdown, 'reason': 'extreme_edge_data_noise_guard'})

    # 2026-09-08 team-assignment guard. If player_team is null, we can't
    # trust the pick belongs on this game. Downgrade to LEAN max.
    if not prop.get('player_team'):
        base_tier = 'LEAN' if edge_pct >= 7 else ('LIGHT' if edge_pct >= 5 else 'SKIP')
        return (base_tier, min(60, 40 + int(edge_pct)),
                {**breakdown, 'reason': 'null_player_team_guard'})

    # PRIME: extreme confluence — season-avg must confirm (season stability
    # anchors against recent-form noise), high edge, high hit rate, meaningful
    # sample. Deliberately conservative — Week 1 signals mostly draw from
    # LAST season's game logs; PRIME needs to survive that staleness.
    if (edge_pct >= 18 and score >= 4.5 and a_season is True
        and hit_pct is not None and hit_pct >= 0.70 and hit_n >= 7):
        return ('PRIME', min(95, 80 + int(edge_pct)),
                {**breakdown, 'reason': 'prime_gate'})
    # STRONG: solid edge + strong confluence
    if edge_pct >= 12 and score >= 3.25:
        return ('STRONG', min(85, 65 + int(edge_pct)),
                {**breakdown, 'reason': 'strong_gate'})
    # LEAN: moderate edge + some agreement
    if edge_pct >= 8 and score >= 2.25:
        return ('LEAN', min(75, 50 + int(edge_pct)),
                {**breakdown, 'reason': 'lean_gate'})
    # LIGHT: bare edge, no confluence
    if edge_pct >= 5:
        return ('LIGHT', min(60, 35 + int(edge_pct)),
                {**breakdown, 'reason': 'light_bare_edge'})
    return ('SKIP', 0, {**breakdown, 'reason': 'below_light_threshold'})


def _fetch_props(date_from: str, date_to: str) -> list:
    url = (f'{SB}/rest/v1/nfl_pipeline_props'
           f'?game_date=gte.{date_from}&game_date=lte.{date_to}'
           f'&select=id,game_id,player_name,prop_type,direction,tier,'
           f'conviction,book_line,player_team,opp_team,signals')
    r = requests.get(url, headers=H_R, timeout=30)
    return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []


def _fetch_game_teams(game_ids: set) -> dict:
    """Return {game_id: (home_team, away_team)} for validating player_team."""
    if not game_ids: return {}
    ids = ','.join(f'"{g}"' for g in game_ids)
    r = requests.get(
        f'{SB}/rest/v1/nfl_game_context', headers=H_R,
        params={'game_id': f'in.({ids})',
                'select': 'game_id,home_team,away_team'},
        timeout=30,
    )
    if r.status_code != 200 or not isinstance(r.json(), list): return {}
    return {g['game_id']: (g.get('home_team'), g.get('away_team')) for g in r.json()}


def find_cross_team_leaks(props: list) -> list:
    """Props where player_team is neither the home nor away team of the
    game they're attached to. Legacy rows written before the source-guard
    landed in nfl_generate_props.py (2026-09-08)."""
    gids = {p.get('game_id') for p in props if p.get('game_id')}
    team_map = _fetch_game_teams(gids)
    leaks = []
    for p in props:
        gid = p.get('game_id')
        team = p.get('player_team')
        if not team: continue  # null-team rows are broken but a separate class
        game = team_map.get(gid)
        if not game: continue
        home, away = game
        if team != home and team != away:
            leaks.append(p)
    return leaks


def _patch(row_id: int, updates: dict) -> bool:
    r = requests.patch(
        f'{SB}/rest/v1/nfl_pipeline_props', headers=H_W,
        params={'id': f'eq.{row_id}'},
        json=updates, timeout=15,
    )
    return r.status_code < 300


def dedupe_alt_lines(props: list) -> list:
    """Group by (game_id, player_name, prop_type_family) — across BOTH
    directions and BOTH alt lines. Keep ONE winner (highest conviction).
    Everything else demoted to SKIP.

    Why cross-direction dedupe: Stevenson case — rush_yds OVER 44.5
    (STRONG conv 93) AND UNDER 58.5 (STRONG conv 100) both technically
    +EV (projection is between the lines), but shipping both reads as
    the model is broken. Winner-take-all surfaces the strongest edge and
    hides alt-line noise. Alt-line stacking can come back post-launch
    with proper "range" display UX.
    """
    to_demote = []  # rows losing the alt-line contest
    by_group = defaultdict(list)
    for p in props:
        pt = (p.get('prop_type') or '')
        if pt.endswith('_over'):    fam = pt[:-5]
        elif pt.endswith('_under'): fam = pt[:-6]
        else:                       fam = pt
        # 2026-09-08: no direction in key — one winner per player+family
        # regardless of over/under. Prevents Stevenson-class conflicts.
        key = (p.get('game_id'), p.get('player_name'), fam)
        by_group[key].append(p)
    for key, rows in by_group.items():
        if len(rows) <= 1: continue
        # Winner = highest conviction; tiebreak on tier rank; tiebreak on lowest id
        rows_sorted = sorted(
            rows,
            key=lambda r: (-(r.get('conviction') or 0),
                           -TIER_ORDER.get(r.get('tier') or '', 0),
                           r.get('id') or 0)
        )
        keeper = rows_sorted[0]
        for loser in rows_sorted[1:]:
            to_demote.append((loser, keeper))
    return to_demote


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=8)
    ap.add_argument('--date', type=str)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    et = datetime.now(timezone.utc) - timedelta(hours=4)
    if args.date:
        date_from = date_to = args.date
    else:
        date_from = et.date().isoformat()
        date_to = (et.date() + timedelta(days=args.days)).isoformat()

    print(f'== nfl_prop_signal_discipline == window={date_from}→{date_to} dry_run={args.dry_run}')
    props = _fetch_props(date_from, date_to)
    print(f'  fetched {len(props)} NFL props in window')
    if not props:
        return 0

    # ── Fix 0: cross-team leaks ─────────────────────────
    leaks = find_cross_team_leaks(props)
    leak_ids = {p['id'] for p in leaks}
    # Filter these out of downstream so we don't dedupe/cap them separately
    props = [p for p in props if p['id'] not in leak_ids]

    # ── Fix 1: confluence-gate tier reassignment ─────────
    # Reassign every prop's tier + conviction via multi-signal confluence
    # (recency-weighted). Uses signals already on the row — no external
    # fetch. Fully rolling: as new games play, L4/L5/L10/_stat_last10
    # shift automatically so tier assignments track player form.
    from collections import Counter
    new_tiers = Counter(); orig_tiers = Counter()
    reassigned = 0
    for p in props:
        orig = p.get('tier') or ''
        orig_tiers[orig] += 1
        new_tier, new_conv, breakdown = compute_confluence_tier(p)
        new_tiers[new_tier] += 1
        if new_tier != orig or (p.get('conviction') or 0) != new_conv:
            p['_new_tier'] = new_tier
            p['_new_conviction'] = new_conv
            p['_confluence_breakdown'] = breakdown
            reassigned += 1

    # ── Fix 2: dedupe alt-line conflicts ────────────────
    demotions = dedupe_alt_lines(props)
    print(f'  cross-team leaks to SKIP: {len(leaks)}')
    print(f'  tier reassignments (confluence gate): {reassigned}')
    print(f'  before: {dict(orig_tiers)}')
    print(f'  after:  {dict(new_tiers)}')
    print(f'  alt-line duplicates to demote to SKIP: {len(demotions)}')

    # ── Write ─────────────────────────────────────────────
    if args.dry_run:
        # print a few examples
        for p in props[:5]:
            if p.get('_new_tier'):
                print(f'    would cap: {p["player_name"]:20} {p["prop_type"]:22} {p["tier"]}→{p["_new_tier"]}')
        for loser, keeper in demotions[:5]:
            print(f'    would demote: {loser["player_name"]:20} {loser["prop_type"]:22} '
                  f'line={loser.get("book_line")} conv={loser.get("conviction")} → SKIP  '
                  f'(keeper line={keeper.get("book_line")} conv={keeper.get("conviction")})')
        return 0

    written = 0
    fails = 0
    # Cross-team leaks first — demote to SKIP (safer than delete; preserves audit trail)
    for p in leaks:
        if _patch(p['id'], {'tier': 'SKIP', 'conviction': 0}): written += 1
        else: fails += 1
    # Apply confluence-gate reassignments (tier + conviction)
    for p in props:
        if p.get('_new_tier'):
            if _patch(p['id'], {'tier': p['_new_tier'],
                                'conviction': p['_new_conviction']}):
                written += 1
            else: fails += 1
    # Apply alt-line demotions (loser rows already patched above but tier may
    # need to drop further to SKIP so composer never surfaces them)
    for loser, keeper in demotions:
        if _patch(loser['id'], {'tier': 'SKIP', 'conviction': 0}):
            written += 1
        else: fails += 1

    print(f'\n== TOTAL == patched={written} failed={fails}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
