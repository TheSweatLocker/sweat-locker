"""Server-side Sharp Card item composer.

User directive (2026-09-03): "The idea was to minimize client side: hard
notes, things like pick generation. In theory not every user will have
the same sharp plays in sharp tab if done client side, right?"

CORRECT. Sharp Card item composition was client-side in fetchSharpTab
(app/index.tsx:8869-9133). This generator moves ALL of that logic
server-side: reads every sport context + MLB props + prop_playbook +
UFC jerry_reads, applies the same unit-sizing / juice-cap / odds-gate
rules, writes ONE cached output to jerry_cache.cache_key='sharp_card_{today}'.

App becomes a dumb renderer: fetch one row, .items[], done.

Every user sees the SAME Sharp Card because they read the SAME row.
Threshold changes = single Python commit, no App Store update.

CACHE SHAPE:
  {
    "items": [
      {sport, matchup, tier, pick, type, reason, odds, line, units,
       playbook_lifted?, _raw_prop_key?},
      ...
    ],
    "generated_at": ISO,
    "count": N,
    "config_version": "2026-09-03",
  }

USAGE:
  python generate_sharp_card.py           # publish for today
  python generate_sharp_card.py --dry-run # print, don't write
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
K = os.environ['SUPABASE_KEY']
H_READ = {'apikey': K, 'Authorization': f'Bearer {K}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


# ═══════════════════════════════════════════════════════════════════════
# CONFIG — must stay in sync with prior app-side logic
# ═══════════════════════════════════════════════════════════════════════

CONFIG_VERSION = '2026-09-05'

# Feature flag: prop playbook tier lift. Kept false until 14d shadow validates.
PROP_PLAYBOOK_ENABLED = False

# ─── 2026-09-05 SHARP CARD DISCIPLINE FLAGS ────────────────────────────
# User directive 9/5: Sharp Card at 100+ items dilutes the "sharp" brand.
# Handicapper industry standard is 3-15 picks/day. Trim to that with a
# 3-flag combo. Each flag has a REVERT sha noted for rollback if the
# trimmed card underperforms the volume-heavy card in W-L / units.
#
# REVERT: to restore pre-9/5 volume-heavy behavior, flip these 3 flags:
#   FOOTBALL_INCLUDE_LEAN = True
#   FOOTBALL_CHALK_SPREAD_MAX_MAGNITUDE = None  (or set very high)
#   SHARP_CARD_ITEM_CAP = None  (uncap)
# Or revert at git: pre-discipline SHA was `e2e2dced`.

# (1) Drop LEAN-tier picks from football (NCAAF/NFL) sections. MLB
# still includes LEAN props (historically profitable). Football LEAN is
# noisier + more visible on Sat slate.
FOOTBALL_INCLUDE_LEAN = False

# (2) Fade the "chalky STRONG" trap — football STRONG picks where spread
# magnitude > this AND ML worse than the juice cap. Common Alabama -32
# / BYU -51 body-bag pattern where public loves the chalk but sharp
# fades. Set to None to disable.
FOOTBALL_CHALK_SPREAD_MAX_MAGNITUDE = 20   # points
FOOTBALL_CHALK_ML_JUICE_MAX = -1500        # if picked ML <= this, cap it

# (3) Total items cap. Uses PER-SPORT quotas so NCAAF picks don't get
# fully evicted by MLB PRIME abundance on Sat slates. Overflow beyond
# the quota drops by tier priority (LEAN first, then STRONG).
# Historical volume was 100+; new cap ~50 balances "sharp discipline"
# with "cross-sport coverage on Sat when 4 sports are live."
SHARP_CARD_ITEM_CAP = 50
SHARP_CARD_PER_SPORT_MAX = {
    'MLB':   25,   # daily bread — sides + props
    'NCAAF': 12,   # Sat slate is huge, top 12 STRONGs
    'NFL':   10,   # Sun slate
    'NCAAB': 10,
    'NBA':    8,
    'NHL':    8,
    'UFC':    5,   # per-card
}

_SPORT_PRIORITY = {'MLB': 0, 'NCAAF': 1, 'NFL': 2, 'NCAAB': 3, 'NBA': 4, 'NHL': 5, 'UFC': 6}
_TIER_PRIORITY = {'PRIME': 0, 'STRONG': 1, 'LEAN': 2}


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _is_ps(tier: str | None) -> bool:
    return tier in ('PRIME', 'STRONG')


def _is_any_tier(tier: str | None) -> bool:
    return tier in ('PRIME', 'STRONG', 'LEAN')


def _resolve_tier(legacy: str | None, playbook: str | None,
                  playbook_side: str | None) -> str | None:
    """Match app resolveTier(). BACK-side lift only; FADE ignored."""
    if not PROP_PLAYBOOK_ENABLED:
        return legacy
    side = (playbook_side or 'BACK').upper()
    if side == 'FADE':
        return legacy
    rank = {'PRIME': 3, 'STRONG': 2, 'LEAN': 1}
    return playbook if rank.get(playbook or '', 0) > rank.get(legacy or '', 0) else legacy


def _units_for_tier(tier: str | None) -> float:
    return 2.0 if tier in ('PRIME', 'STRONG') else 1.0


def _units_for_pick(tier: str | None, type_: str | None, odds: Any,
                    side_price_american: Any = None,
                    prop_type: str | None = None) -> float:
    """Mirror app unitsForPick(). Returns unit stake or 0 for filtered picks."""
    base = _units_for_tier(tier)
    if base == 0: return 0.0
    o = odds
    # Prop with no captured odds → skip
    is_prop_ctx = type_ == 'prop' or (type_ is None and prop_type)
    if o is None and is_prop_ctx: return 0.0
    # Prop hard gate: outside [-300, +150]
    if is_prop_ctx and o is not None:
        try:
            if float(o) < -300 or float(o) > 150: return 0.0
        except (TypeError, ValueError): return 0.0
    stake = base
    # Juice traps (any pick type)
    if o is not None:
        try:
            f = float(o)
            if f <= -250 or f >= 250: stake = stake / 2
        except (TypeError, ValueError): pass
    # ML regime traps
    if type_ == 'ml' and side_price_american is not None:
        try:
            ml = float(side_price_american)
            if ml <= -180 or ml >= 150: stake = stake / 2
        except (TypeError, ValueError): pass
    # Hits_over -200+ trap
    if prop_type and prop_type.lower() == 'hits_over' and o is not None:
        try:
            if float(o) <= -200: stake = stake / 2
        except (TypeError, ValueError): pass
    stake = max(0.0, min(base, round(stake * 2) / 2))
    return stake


def _prop_team_matches(player_team: str | None, matchup: str | None) -> bool:
    """Mirror app propTeamMatches(). Player_team must appear in matchup."""
    team = (player_team or '').strip().lower()
    if not team or team == 'unknown': return True
    m = (matchup or '').lower()
    if not m: return True
    short = team.split(' ')[-1] if team else ''
    if short == 'sox':  # Sox collision — require full name
        return team in m
    return short in m or team in m


# ═══════════════════════════════════════════════════════════════════════
# FETCH LAYER
# ═══════════════════════════════════════════════════════════════════════

def _get(url: str, params: dict = None, timeout: int = 30) -> list:
    try:
        r = requests.get(url, params=params, headers=H_READ, timeout=timeout)
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f'  ⚠ fetch failed: {url[:80]} — {e}')
        return []


def _fetch_all(today: str) -> dict:
    """One-shot fetch of every source, keyed for downstream composition."""
    out = {}
    out['mlb_ctx']   = _get(f'{SB}/rest/v1/mlb_game_context',
                            params={'select': 'game_id,home_team,away_team,primary_play,'
                                    'home_ml_close,away_ml_close,home_ml_odds,away_ml_odds,'
                                    'close_spread,close_total',
                                    'game_date': f'eq.{today}'})
    out['mlb_props'] = _get(f'{SB}/rest/v1/mlb_pipeline_props',
                            params={'select': 'player_name,player_team,matchup,prop_type,prop_line,'
                                    'direction,tier,conviction,refit_conviction,book_line,'
                                    'book_over_odds,book_under_odds,game_id',
                                    'game_date': f'eq.{today}',
                                    'tier': 'in.(PRIME,STRONG,LEAN)'})
    # 2026-09-05 FIX: NCAAF/NFL use `close_home_ml`/`close_away_ml`; MLB
    # uses `home_ml_close`/`away_ml_close`. Prior version requested MLB
    # column names for every sport → PostgREST 400 → silent empty list →
    # zero NCAAF/NFL picks on Sharp Card despite PRIME/STRONG picks existing
    # in game_context. Sport-aware select fixes it. `_compose_other_sport_sides`
    # already reads either alias (`home_ml_close or home_ml_odds`).
    for sport, tbl in [('nfl','nfl_game_context'), ('ncaaf','ncaaf_game_context'),
                       ('ncaab','ncaab_game_context'), ('nba','nba_game_context'),
                       ('nhl','nhl_game_context')]:
        if sport in ('nfl', 'ncaaf'):
            cols = ('game_id,home_team,away_team,primary_play,'
                    'close_home_ml,close_away_ml')
        else:
            cols = ('game_id,home_team,away_team,primary_play,'
                    'home_ml_close,away_ml_close,home_ml_odds,away_ml_odds')
        out[f'{sport}_ctx'] = _get(f'{SB}/rest/v1/{tbl}',
                                    params={'select': cols, 'game_date': f'eq.{today}'})
    out['ufc_reads'] = _get(f'{SB}/rest/v1/jerry_reads',
                             params={'select': 'game_id,call_side,conviction,input_snapshot',
                                     'sport': 'eq.UFC',
                                     'game_date': f'eq.{today}',
                                     'call_market': 'eq.fight',
                                     'conviction': 'gte.55'})
    out['playbook']  = _get(f'{SB}/rest/v1/prop_playbook_decisions',
                             params={'select': 'player_name,prop_type,direction,prop_line,'
                                     'playbook_tier,playbook_side',
                                     'sport': 'eq.MLB',
                                     'game_date': f'eq.{today}'})
    return out


# ═══════════════════════════════════════════════════════════════════════
# COMPOSITION LAYER
# ═══════════════════════════════════════════════════════════════════════

def _compose_mlb_sides(mlb_ctx: list) -> list[dict]:
    picks = []
    for g in mlb_ctx:
        pp = g.get('primary_play') or {}
        if not isinstance(pp, dict) or not _is_any_tier(pp.get('tier')): continue
        home_ml = g.get('home_ml_close') or g.get('home_ml_odds')
        away_ml = g.get('away_ml_close') or g.get('away_ml_odds')
        side = pp.get('side')
        side_ml = home_ml if side == 'HOME' else away_ml if side == 'AWAY' else None
        picks.append({
            'sport': 'MLB',
            'matchup': f"{g.get('away_team')} @ {g.get('home_team')}",
            'tier': pp.get('tier'),
            'pick': pp.get('label') or '—',
            'type': pp.get('type') or 'ml',
            'reason': pp.get('sub') or '',
            'odds': side_ml if pp.get('type') == 'ml' else -110,
            'line': pp.get('line') or g.get('close_spread') or g.get('close_total'),
            'units': _units_for_pick(pp.get('tier'), pp.get('type') or 'ml',
                                     side_ml if pp.get('type') == 'ml' else -110,
                                     side_price_american=side_ml),
        })
    return [p for p in picks if p['units'] > 0]


def _compose_mlb_props(mlb_props: list, playbook: list) -> list[dict]:
    playbook_by_key = {}
    for d in playbook:
        k = f"{d.get('player_name')}|{d.get('prop_type')}|{d.get('direction')}|{d.get('prop_line')}"
        playbook_by_key[k] = d
    picks = []
    for p in mlb_props:
        pb_key = f"{p.get('player_name')}|{p.get('prop_type')}|{p.get('direction')}|{p.get('prop_line')}"
        pb = playbook_by_key.get(pb_key)
        effective_tier = _resolve_tier(p.get('tier'),
                                        pb.get('playbook_tier') if pb else None,
                                        pb.get('playbook_side') if pb else None)
        if not _is_ps(effective_tier): continue
        if not _prop_team_matches(p.get('player_team'), p.get('matchup')): continue
        prop_odds = p.get('book_over_odds') if p.get('direction') == 'over' else p.get('book_under_odds')
        units = _units_for_pick(effective_tier, 'prop', prop_odds,
                                 prop_type=p.get('prop_type'))
        if units <= 0: continue
        prop_short = (p.get('prop_type') or '').split('_')[0].upper()
        picks.append({
            'sport': 'MLB',
            'matchup': p.get('matchup') or '—',
            'tier': effective_tier,
            'pick': f"{p.get('player_name')} {'Over' if p.get('direction')=='over' else 'Under'} "
                    f"{p.get('prop_line')} {prop_short}",
            'type': 'prop',
            'reason': f"conv={p.get('refit_conviction') or p.get('conviction')}",
            'odds': prop_odds,
            'line': p.get('prop_line'),
            'units': units,
            'playbook_lifted': PROP_PLAYBOOK_ENABLED and pb and pb.get('playbook_tier')
                                and effective_tier != p.get('tier'),
        })
    return picks


def _compose_other_sport_sides(rows: list, sport: str) -> list[dict]:
    is_football = sport in ('NCAAF', 'NFL')
    tier_gate = _is_ps if (is_football and not FOOTBALL_INCLUDE_LEAN) else _is_any_tier
    dropped_lean = dropped_chalk = 0
    picks = []
    for g in rows:
        pp = g.get('primary_play') or {}
        if not isinstance(pp, dict): continue
        tier = pp.get('tier')
        # (1) LEAN gate for football
        if not tier_gate(tier):
            if is_football and tier == 'LEAN': dropped_lean += 1
            continue
        # Sport-aware column aliases: MLB uses home_ml_close, NCAAF/NFL use close_home_ml
        home_ml = g.get('home_ml_close') or g.get('home_ml_odds') or g.get('close_home_ml')
        away_ml = g.get('away_ml_close') or g.get('away_ml_odds') or g.get('close_away_ml')
        side = pp.get('side')
        # (2) Chalky-STRONG fade — football only, big-spread + heavy ML juice
        if is_football and tier == 'STRONG' and FOOTBALL_CHALK_SPREAD_MAX_MAGNITUDE is not None:
            spread = g.get('close_spread')
            picked_ml = home_ml if side == 'HOME' else away_ml if side == 'AWAY' else None
            try:
                if (spread is not None and abs(float(spread)) > FOOTBALL_CHALK_SPREAD_MAX_MAGNITUDE
                    and picked_ml is not None and float(picked_ml) <= FOOTBALL_CHALK_ML_JUICE_MAX):
                    dropped_chalk += 1
                    continue
            except (TypeError, ValueError):
                pass
        side_ml = home_ml if side == 'HOME' else away_ml if side == 'AWAY' else None
        units = _units_for_pick(pp.get('tier'), pp.get('type') or 'ml',
                                 side_ml if pp.get('type') == 'ml' else None,
                                 side_price_american=side_ml)
        if units <= 0: continue
        picks.append({
            'sport': sport,
            'matchup': f"{g.get('away_team')} @ {g.get('home_team')}",
            'tier': pp.get('tier'),
            'pick': pp.get('label') or '—',
            'type': pp.get('type') or 'ml',
            'reason': pp.get('sub') or '',
            'odds': side_ml if pp.get('type') == 'ml' else None,
            'line': pp.get('line'),
            'units': units,
        })
    if is_football and (dropped_lean or dropped_chalk):
        print(f'  {sport} discipline drops: LEAN={dropped_lean}  chalky-STRONG={dropped_chalk}')
    return picks


def _compose_ufc(ufc_reads: list) -> list[dict]:
    picks = []
    for r in ufc_reads:
        inp = r.get('input_snapshot') or {}
        if not isinstance(inp, dict): continue
        side = r.get('call_side')
        picked = inp.get('fighter_a') if side == 'A' else inp.get('fighter_b')
        odds = inp.get('odds_picked_side_median')
        base = _units_for_tier(inp.get('ev_tier'))
        halve = 0.5 if (odds is not None and odds <= -180) else 1.0
        units = base * halve
        if units <= 0: continue
        odds_str = f'  ({"+" if odds and odds > 0 else ""}{odds})' if odds else ''
        picks.append({
            'sport': 'UFC',
            'matchup': f"{inp.get('fighter_a')} vs {inp.get('fighter_b')}",
            'tier': inp.get('ev_tier') or '—',
            'pick': f'{picked} ML{odds_str}',
            'type': 'ml',
            'reason': f"Win prob {inp.get('win_probability_pct')}% @ {odds}",
            'odds': odds,
            'line': None,
            'units': units,
        })
    return picks


# ═══════════════════════════════════════════════════════════════════════
# WRITE LAYER
# ═══════════════════════════════════════════════════════════════════════

def _publish(today: str, items: list[dict], dry_run: bool):
    payload = {
        'items': items,
        'count': len(items),
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'config_version': CONFIG_VERSION,
    }
    if dry_run:
        print(f'  [DRY] would write {len(items)} items to jerry_cache.sharp_card_{today}')
        by_sport = {}
        for p in items:
            by_sport.setdefault(p['sport'], 0)
            by_sport[p['sport']] += 1
        print(f'         breakdown: {by_sport}')
        return
    row = {
        'game_id': f'sharp_card_{today}',
        'cache_key': f'sharp_card_{today}',
        'sport': 'ALL',
        'data': payload,
        'narrative': f'Sharp Card · {today} · {len(items)} plays',
        'fetched_at': datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(f'{SB}/rest/v1/jerry_cache?on_conflict=cache_key',
                      headers=H_WRITE, json=row, timeout=30)
    if r.status_code in (200, 201, 204):
        print(f'  ✓ published {len(items)} items → jerry_cache.sharp_card_{today}')
    else:
        print(f'  ✗ publish failed {r.status_code}: {r.text[:200]}')


def run(dry_run: bool = False):
    today = _today_et()
    print(f'== Generate Sharp Card · {today} ==')

    sources = _fetch_all(today)
    print(f'  fetched: MLB ctx={len(sources["mlb_ctx"])} props={len(sources["mlb_props"])} '
          f'NFL={len(sources["nfl_ctx"])} NCAAF={len(sources["ncaaf_ctx"])} '
          f'NCAAB={len(sources["ncaab_ctx"])} NBA={len(sources["nba_ctx"])} '
          f'NHL={len(sources["nhl_ctx"])} UFC={len(sources["ufc_reads"])} '
          f'playbook={len(sources["playbook"])}')

    mlb_sides = _compose_mlb_sides(sources['mlb_ctx'])
    mlb_props = _compose_mlb_props(sources['mlb_props'], sources['playbook'])
    nfl       = _compose_other_sport_sides(sources['nfl_ctx'], 'NFL')
    ncaaf     = _compose_other_sport_sides(sources['ncaaf_ctx'], 'NCAAF')
    ncaab     = _compose_other_sport_sides(sources['ncaab_ctx'], 'NCAAB')
    nba       = _compose_other_sport_sides(sources['nba_ctx'], 'NBA')
    nhl       = _compose_other_sport_sides(sources['nhl_ctx'], 'NHL')
    ufc       = _compose_ufc(sources['ufc_reads'])

    all_items = [*mlb_sides, *mlb_props, *nfl, *ncaaf, *ncaab, *nba, *nhl, *ufc]

    print(f'  composed: MLB sides={len(mlb_sides)} MLB props={len(mlb_props)} '
          f'NFL={len(nfl)} NCAAF={len(ncaaf)} NCAAB={len(ncaab)} '
          f'NBA={len(nba)} NHL={len(nhl)} UFC={len(ufc)}  TOTAL={len(all_items)}')

    # (3) Cap total items with per-sport quota + tier priority.
    # Step A: sort each sport bucket by (tier, -units).
    # Step B: take top N per sport per SHARP_CARD_PER_SPORT_MAX.
    # Step C: if still over SHARP_CARD_ITEM_CAP, trim from lowest-tier
    #         of each sport proportionally.
    if SHARP_CARD_ITEM_CAP is not None and len(all_items) > SHARP_CARD_ITEM_CAP:
        pre = len(all_items)
        from collections import defaultdict as _dd
        by_sport: dict = _dd(list)
        for it in all_items:
            by_sport[it.get('sport') or '?'].append(it)
        capped: list = []
        for sport, items_s in by_sport.items():
            max_for_sport = SHARP_CARD_PER_SPORT_MAX.get(sport, 15)
            items_s.sort(key=lambda it: (
                _TIER_PRIORITY.get(it.get('tier'), 9),
                -float(it.get('units') or 0),
            ))
            capped.extend(items_s[:max_for_sport])
        # If per-sport quotas still overshoot total, sort combined and cap
        if len(capped) > SHARP_CARD_ITEM_CAP:
            capped.sort(key=lambda it: (
                _TIER_PRIORITY.get(it.get('tier'), 9),
                _SPORT_PRIORITY.get(it.get('sport'), 9),
                -float(it.get('units') or 0),
            ))
            capped = capped[:SHARP_CARD_ITEM_CAP]
        all_items = capped
        print(f'  cap applied: {pre} → {len(all_items)} (per-sport quotas + hard cap {SHARP_CARD_ITEM_CAP})')

    _publish(today, all_items, dry_run)
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    sys.exit(run(dry_run=ap.parse_args().dry_run))
