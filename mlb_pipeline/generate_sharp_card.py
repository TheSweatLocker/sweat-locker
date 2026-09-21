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
SHARP_CARD_ITEM_CAP = 20  # was 50 — tightened 2026-09-11 for curation

# (4) 2026-09-06 juice cap on SOLE game picks (all sports). ML picks
# juicier than this get auto-swapped to the spread side of the same
# game so users don't stake 2u to win 0.5u on -400 chalk. Ledger
# (chalk parlays, teasers) intentionally keeps heavier chalks — the
# compounding math on a 3-leg parlay of -300 legs still pays +170,
# so a juiced fav is fine as a parlay leg, not as a sole play.
SOLE_PICK_ML_JUICE_MAX = -300
LEDGER_ML_JUICE_MAX    = -450  # ledger-only; documented for the composer that builds parlays
# 2026-09-11 TIGHTER CAPS. Prior 25/12/10/10 quotas + 50 global produced
# 46-item cards on active MLB days (see 9/11: 13 MLB sides + 32 MLB
# props + 1 NCAAF). User feedback: "46 plays is alot for the sharp" —
# curation failure. A user opening Sharp Card should see 15-20 real
# edges, not scroll a 46-pick backlog. Tighter caps rely on the existing
# tier-sort (PRIME first) to keep the deck loaded with high-conviction
# picks. STRONG picks still get a slot when PRIMEs don't fill the quota.
# Post-launch v2 (option 2 from user discussion): explicit PRIME-first
# rule that drops STRONGs entirely when PRIMEs exceed quota — deferred
# because on quiet slates it could produce a 2-3 item card.
SHARP_CARD_PER_SPORT_MAX = {
    'MLB':   15,   # daily bread — was 25
    'NCAAF': 5,    # Sat slate — was 12
    'NFL':   5,    # Sun slate — was 10
    'NCAAB': 5,    # was 10
    'NBA':   3,    # was 8
    'NHL':   3,    # was 8
    'UFC':   3,    # was 5 per-card
}

# 2026-09-17 MLB MARKET SPLIT (Andy audit: "if PRIME props doing so well
# why not more prime props in sharp?"). MLB sides run 45-55% hit rate at
# -110 juice — losing money. MLB PRIME props run 79-84% hit rate — real
# +EV even at prop juice. Data-driven allocation weights toward props.
#
# 30d surface_records:
#   MLB `prop`  MTD: 79% hit / +533u / +34% ROI (n=1554)
#   MLB `sharp` MTD: 51% hit / -10.37u / -5.58% ROI (n=193) — bleeding
#
# Reserving 10 of MLB's 15 slots for props (was implicit split ~6:9 by
# conviction sort, which favored sides when tiers matched). Sides get
# up to 5 slots — enough coverage for the day's clear game leans without
# loading the deck with juice traps.
#
# When props are short (early season / thin slate), unused prop slots
# spill to sides so total MLB output isn't reduced.
SHARP_CARD_MLB_PROPS_TARGET = 10
SHARP_CARD_MLB_SIDES_TARGET = 5

# 2026-09-09 COLD-STREAK AUTO-TIGHTENING (user directive from surface walkthrough).
# When Sharp Card is running cold, publish a smaller + higher-conviction deck.
# Read 7-day rolling hit rate from daily_surface_records.sharp_card; if
# < COLD_HIT_RATE_THRESHOLD over N>=COLD_MIN_SAMPLE plays, enter cold-mode:
#   • Cap total items at COLD_ITEM_CAP (from SHARP_CARD_ITEM_CAP=50 → 15)
#   • Tag the card with cold_state='cold' so app renders warning banner
# Auto-normalizes when 7d rolling ≥ WARM_HIT_RATE_THRESHOLD.
COLD_LOOKBACK_DAYS = 7
COLD_MIN_SAMPLE = 15         # need at least 15 plays to trust the sample
COLD_HIT_RATE_THRESHOLD = 0.50   # < 50% = cold
WARM_HIT_RATE_THRESHOLD = 0.55   # ≥ 55% = normal
COLD_ITEM_CAP = 15            # tighter deck when cold
COLD_TIER_FILTER = {'PRIME', 'STRONG'}   # cold-mode: drop LEAN

_SPORT_PRIORITY = {'MLB': 0, 'NCAAF': 1, 'NFL': 2, 'NCAAB': 3, 'NBA': 4, 'NHL': 5, 'UFC': 6}
_TIER_PRIORITY = {'PRIME': 0, 'STRONG': 1, 'LEAN': 2}


def _compute_sharp_cold_state() -> dict:
    """Return {state, hit_rate, sample, window_start, window_end, message}.

    Reads last 7 days of daily_surface_records for surface='sharp_card',
    combines W+L across all sports (pushes excluded from rate denom).
    Returns state='cold' | 'warming' | 'normal' + rationale.

    Non-fatal: if the read fails or sample too small, returns state='normal'
    so the card publishes at full volume — better to over-publish than
    silently kill the card on a data hiccup.
    """
    from datetime import date as _date, timedelta as _td
    end = _date.today()
    start = end - _td(days=COLD_LOOKBACK_DAYS)
    try:
        r = requests.get(f'{SB}/rest/v1/daily_surface_records', headers=H_READ,
            params={'surface': 'eq.sharp_card',
                    'record_date': f'gte.{start.isoformat()}',
                    'select': 'record_date,sport,wins,losses,pushes,pick_count'},
            timeout=15)
        if r.status_code != 200:
            return {'state': 'normal', 'reason': f'lookup failed ({r.status_code})',
                    'hit_rate': None, 'sample': 0}
        rows = r.json() or []
    except Exception as e:
        return {'state': 'normal', 'reason': f'exception {e!r}',
                'hit_rate': None, 'sample': 0}
    total_w = sum(int(r.get('wins') or 0) for r in rows)
    total_l = sum(int(r.get('losses') or 0) for r in rows)
    total_p = sum(int(r.get('pushes') or 0) for r in rows)
    decisions = total_w + total_l  # pushes exclude from rate
    if decisions < COLD_MIN_SAMPLE:
        return {'state': 'normal', 'reason': f'insufficient sample n={decisions}',
                'hit_rate': None, 'sample': decisions}
    hit_rate = total_w / decisions
    if hit_rate < COLD_HIT_RATE_THRESHOLD:
        state = 'cold'
    elif hit_rate < WARM_HIT_RATE_THRESHOLD:
        state = 'warming'
    else:
        state = 'normal'
    return {
        'state': state,
        'hit_rate': round(hit_rate, 3),
        'sample': decisions,
        'wins': total_w, 'losses': total_l, 'pushes': total_p,
        'window_start': start.isoformat(),
        'window_end': end.isoformat(),
        'message': (
            f'🥶 Cold streak — tightening filters ({total_w}-{total_l} last 7 days)'
            if state == 'cold' else
            f'⚠ Warming up — cautious volume ({total_w}-{total_l} last 7 days)'
            if state == 'warming' else
            f'📈 Normal deck ({total_w}-{total_l} last 7 days)'
        ),
    }


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
    """Mirror app propTeamMatches(). Player_team must appear in matchup.

    2026-09-07 ROOT-CAUSE FIX: previously returned True when
    player_team was missing/UNKNOWN, on the theory that we shouldn't
    false-negative when team resolution was flaky. In practice this
    let orphan pitcher props through the composer — David Peterson
    (NYM) props stuck on the Cubs-vs-Brewers event because odds API
    mis-attached them; player_team='UNKNOWN' passed this gate → prop
    landed on The Sharp for a game the pitcher wasn't in. Flipped
    to REJECT unresolved-team props. Batter props are exempt because
    they arrive with team=None by design (enriched later via lineup
    attach step). Only PITCHER props are gated here — batter props
    reach this function only after their team was set upstream.
    """
    team = (player_team or '').strip().lower()
    if not team or team == 'unknown':
        return False
    m = (matchup or '').lower()
    if not m: return True
    short = team.split(' ')[-1] if team else ''
    if short == 'sox':  # Sox collision — require full name
        return team in m
    return short in m or team in m


# ─── 2026-09-06 PROP PUBLISHABILITY GATE ───────────────────────────────
# Universal helpers extracted to prop_publishability.py so every composer
# for every sport uses ONE source of truth. See that module for context
# and feedback_signal_gate_over_tier_906 memory.
from prop_publishability import (
    is_publishable as _is_prop_publishable,
    effective_tier as _effective_prop_tier,
)


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


# 2026-09-10 KICKOFF FILTER — added after user reported Sharp Card + Sweat
# Card still showing yesterday's NE @ SEA TNF picks on 9/10 morning. UTC-vs-ET
# crossover: kickoff was 00:20 UTC 9/10 (8:20 PM ET 9/9), so game_date=2026-09-10
# in the DB but the game was already played. Same class of bug as the POTD
# fix (jerry_anchor_potd.py 9/10). Filter out any game whose kickoff is
# already in the past (grace: 15 min for late lock windows).
def _future_only(rows: list, kickoff_col: str) -> list:
    """Drop any ctx row whose kickoff_col value is < now - 15min."""
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    now_utc = _dt.now(_tz.utc)
    cutoff = now_utc - _td(minutes=15)
    out = []
    dropped = 0
    for r in rows:
        ks = r.get(kickoff_col)
        if ks:
            try:
                ko = _dt.fromisoformat(str(ks).replace('Z','+00:00'))
                if ko.tzinfo is None: ko = ko.replace(tzinfo=_tz.utc)
                if ko < cutoff:
                    dropped += 1
                    continue
            except Exception: pass
        out.append(r)
    if dropped:
        print(f'  ⏭  kickoff filter dropped {dropped} already-played games')
    return out


def _fetch_all(today: str) -> dict:
    """One-shot fetch of every source, keyed for downstream composition."""
    out = {}
    # MLB doesn't need kickoff filter — game_date matches ET play date,
    # rarely crosses UTC midnight. Column commence_time isn't on
    # mlb_game_context anyway. Only NFL/NCAAF need the filter (TNF/MNF
    # kickoffs 8:20 PM ET = next-day UTC).
    out['mlb_ctx']   = _get(f'{SB}/rest/v1/mlb_game_context',
                            params={'select': 'game_id,home_team,away_team,primary_play,'
                                    'home_ml_close,away_ml_close,home_ml_odds,away_ml_odds,'
                                    'close_spread,close_total',
                                    'game_date': f'eq.{today}'})
    out['mlb_props'] = _get(f'{SB}/rest/v1/mlb_pipeline_props',
                            params={'select': 'player_name,player_team,matchup,prop_type,prop_line,'
                                    'direction,tier,conviction,refit_conviction,book_line,'
                                    'book_over_odds,book_under_odds,game_id,signals',
                                    'game_date': f'eq.{today}',
                                    'tier': 'in.(PRIME,STRONG,LEAN)'})
    # 2026-09-09 DEEP LOCK — via shared helper. Overlay prop_pick_snapshots
    # (source of truth once locked) onto live mlb_pipeline_props. See
    # prop_snapshot_overlay module docstring for full context. Every
    # composer that reads props uses this same helper so drift can't
    # differ between surfaces.
    try:
        from prop_snapshot_overlay import overlay_from_snapshots
        out['mlb_props'] = overlay_from_snapshots(out['mlb_props'], today, sport='MLB')
    except Exception:
        pass  # helper missing / snapshot fetch failed — fall through to live

    # 2026-09-17 SHARED POLICY via prop_ban_policy.py.
    from prop_ban_policy import filter_mlb_props
    _before = len(out['mlb_props'] or [])
    out['mlb_props'], _dropped = filter_mlb_props(out['mlb_props'] or [])
    if _dropped:
        print(f'  [sharp_card] ban filter dropped {_dropped} banned prop-family rows')
    # 2026-09-05 FIX: NCAAF/NFL use `close_home_ml`/`close_away_ml`; MLB
    # uses `home_ml_close`/`away_ml_close`. Prior version requested MLB
    # column names for every sport → PostgREST 400 → silent empty list →
    # zero NCAAF/NFL picks on Sharp Card despite PRIME/STRONG picks existing
    # in game_context. Sport-aware select fixes it. `_compose_other_sport_sides`
    # already reads either alias (`home_ml_close or home_ml_odds`).
    for sport, tbl in [('nfl','nfl_game_context'), ('ncaaf','ncaaf_game_context'),
                       ('ncaab','ncaab_game_context'), ('nba','nba_game_context'),
                       ('nhl','nhl_game_context')]:
        # 2026-09-08 SCHEMA DISPATCH — per-sport column names differ:
        #   NFL/NCAAF: close_home_ml + kickoff_utc
        #   NCAAB:     close_home_ml (no kickoff_utc column)
        #   NBA/NHL:   home_ml_close (no kickoff_utc column)
        # 2026-09-10a added kickoff_utc for football; landed 42703 errors
        # on NBA/NHL/NCAAB which don't have the column. Fix: dispatch per
        # sport, only include kickoff_utc for football (they're the only
        # ones with the "already played" stale-card bug). Basketball/hockey
        # tables also lack kickoff_utc — skip the future-only filter there.
        # 2026-09-13 FIX: SELECT was missing close_spread + close_total.
        # `_compose_other_sport_sides` reads close_spread for two decisions:
        #   1. chalky-STRONG fade (line 786): |spread| > 20 + juiced ML → drop
        #   2. sole-pick juice swap (line 809): ML < -300 → swap to spread
        # Without close_spread in the fetch, g.get('close_spread') returned
        # None on every football row, so the swap branch fell through to the
        # "no spread available → skip" `continue` — silently dropping every
        # heavy-fav ML pick. Root cause of missing PRIME/STRONG NFL picks on
        # Sharp Card 9/13 today: JAX (PRIME -470 ML), DET (STRONG -325 ML),
        # LAC (STRONG -500 ML) all invisible on the card despite valid
        # spreads of 8.5, 7.0, 9.5 respectively. Adding close_spread and
        # close_total fixes both discipline gates in one shot.
        if sport in ('nfl', 'ncaaf'):
            # 2026-09-13: added spread_anchor_weight for anchor-refuse gate
            # in _compose_other_sport_sides. Data (n=228, NCAAF 9/12): picks
            # with any anchor active hit 30% (12-28) — worse than random.
            # Refuse-anchored beats cap-to-LEAN because Sharp Card audience
            # is buying signal + FOOTBALL_INCLUDE_LEAN=False anyway; making
            # the drop explicit surfaces it in the discipline-drops log.
            cols = ('game_id,home_team,away_team,primary_play,'
                    'close_home_ml,close_away_ml,close_spread,close_total,'
                    'spread_anchor_weight,'
                    'kickoff_utc')
        elif sport == 'ncaab':
            cols = ('game_id,home_team,away_team,primary_play,'
                    'close_home_ml,close_away_ml,close_spread,close_total')
        else:  # nba, nhl
            cols = ('game_id,home_team,away_team,primary_play,'
                    'home_ml_close,away_ml_close,close_spread,close_total')
        out[f'{sport}_ctx'] = _get(f'{SB}/rest/v1/{tbl}',
                                    params={'select': cols, 'game_date': f'eq.{today}'})
        # Future-only filter only applies to football (only sports that ship
        # a kickoff_utc column — the NE@SEA stale-card bug was a football
        # cross-day-UTC issue that doesn't reproduce for NBA/NHL/NCAAB).
        if sport in ('nfl', 'ncaaf'):
            out[f'{sport}_ctx'] = _future_only(out[f'{sport}_ctx'], 'kickoff_utc')
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

def _lr_shadow_conflict(pp: dict) -> str | None:
    """Return a conflict reason if LR shadow STRONG-disagrees with pp side,
    else None. Rule: when the pipeline picked market X side S but the LR
    shadow on THAT MARKET has suggested_tier in {PRIME, STRONG} pointing
    to the opposite side, drop the pick — LR-shadow-vs-pipeline
    disagreement burned us on MIN@DET 9/7 (see project_lr_shadow_promotion_907).

    2026-09-08: added as a Sharp Card composer gate so users don't see
    conviction picks where our own LR overlay contradicts the pipeline.
    """
    pp_type = (pp.get('type') or '').lower()
    pp_side = (pp.get('side') or '').upper()
    if pp_type == 'ml':
        shadow = pp.get('_lr_ml_shadow') or {}
        shadow_tier = (shadow.get('suggested_tier') or '').upper()
        shadow_side = (shadow.get('suggested_side') or '').upper()
        if shadow_tier in ('PRIME', 'STRONG') and shadow_side and shadow_side != pp_side:
            return f'lr_ml_shadow_conflict:{shadow_tier}:{shadow_side}'
    elif pp_type == 'total':
        shadow = pp.get('_lr_total_shadow') or {}
        shadow_tier = (shadow.get('suggested_tier') or '').upper()
        shadow_side = (shadow.get('suggested_side') or '').upper()
        if shadow_tier in ('PRIME', 'STRONG') and shadow_side and shadow_side not in ('', 'NONE') and shadow_side != pp_side:
            return f'lr_total_shadow_conflict:{shadow_tier}:{shadow_side}'
    return None


def _ensure_mlb_market_floor(mlb_ctx: list, mlb_props: list,
                              sides_picks: list, props_picks: list) -> list[dict]:
    """Mutating backfill: guarantee ≥1 side + ≥1 total + ≥1 prop each day.

    2026-09-09 user directive: on days where primary_plays cluster in one
    market (e.g., all 15 games have ML primary → 0 totals), Sharp Card
    should still surface the strongest available total + prop even if
    convictions dipped below the normal 65 floor. Relaxed floor of 55
    (LEAN tier) — anything weaker isn't worth publishing.

    Mutates sides_picks + props_picks in place; returns list of added items
    for logging.
    """
    added = []
    have_side  = any(p.get('type') in ('ml','rl') for p in sides_picks)
    have_total = any(p.get('type') == 'total'    for p in sides_picks)
    have_prop  = bool(props_picks)

    # Backfill TOTAL: scan ctx for biggest sim vs market disagreement
    if not have_total:
        best = None
        best_edge = 0.0
        for g in mlb_ctx:
            mt = g.get('model_pred_total')
            ct = g.get('close_total')
            if mt is None or ct is None: continue
            edge = float(mt) - float(ct)
            if abs(edge) < 0.5: continue  # need meaningful edge
            if abs(edge) > best_edge:
                best_edge = abs(edge); best = (g, edge)
        if best:
            g, edge = best
            side = 'Over' if edge > 0 else 'Under'
            line = g.get('close_total')
            conv = min(72, 55 + int(abs(edge) * 4))  # 0.5→57, 2.0→63, 4.0→71
            tier = 'STRONG' if conv >= 70 else 'LEAN'
            item = {
                'sport': 'MLB',
                'matchup': f"{g.get('away_team')} @ {g.get('home_team')}",
                'tier': tier,
                'pick': f'{side} {line}',
                'type': 'total',
                'reason': f'Market floor · sim {g.get("model_pred_total"):.1f} vs close {line} · Δ{edge:+.1f}',
                'odds': -110,
                'line': line,
                'units': _units_for_pick(tier, 'total', -110, side_price_american=-110),
                '_floor_backfill': True,
            }
            if item['units'] > 0:
                sides_picks.append(item); added.append(item)

    # Backfill PROP: highest-conviction PRIME/STRONG prop even if publishability
    # gate would normally reject
    if not have_prop:
        best_prop = None
        best_conv = 0
        for p in mlb_props:
            tier = str(p.get('tier') or '').upper()
            if tier not in ('PRIME', 'STRONG'): continue
            conv = int(p.get('refit_conviction') or p.get('conviction') or 0)
            if conv < 55: continue
            if conv > best_conv:
                best_conv = conv; best_prop = p
        if best_prop:
            direction = str(best_prop.get('direction') or '').upper()
            odds = (best_prop.get('book_over_odds') if direction == 'OVER'
                    else best_prop.get('book_under_odds')) or -110
            item = {
                'sport': 'MLB',
                'matchup': best_prop.get('matchup') or '',
                'tier': best_prop.get('tier'),
                'pick': f"{best_prop.get('player_name')} {direction.title()} {best_prop.get('prop_line')} {best_prop.get('prop_type')}",
                'type': 'prop',
                'reason': f"Market floor · top prop conv {best_conv}",
                'odds': odds,
                'line': best_prop.get('prop_line'),
                'units': _units_for_pick(best_prop.get('tier'), 'prop', odds, side_price_american=odds),
                '_floor_backfill': True,
                'player_team': best_prop.get('player_team'),
                'playbook_lifted': False,
            }
            if item['units'] > 0:
                props_picks.append(item); added.append(item)

    return added


def _compose_mlb_sides(mlb_ctx: list) -> list[dict]:
    picks = []
    lr_conflict_drops = 0
    for g in mlb_ctx:
        pp = g.get('primary_play') or {}
        if not isinstance(pp, dict) or not _is_any_tier(pp.get('tier')): continue
        # 2026-09-08 LR SHADOW GATE. If LR shadow on the same market says
        # the OPPOSITE side with STRONG/PRIME conviction, drop the pick.
        # Rationale: MIN@DET 9/7 — pipeline picked Twins ML COVERAGE,
        # LR shadow said PRIME AWAY on the opposite → pipeline lost.
        # Small precision hit (fewer picks), meaningful accuracy gain.
        conflict = _lr_shadow_conflict(pp)
        if conflict:
            lr_conflict_drops += 1
            continue
        home_ml = g.get('home_ml_close') or g.get('home_ml_odds')
        away_ml = g.get('away_ml_close') or g.get('away_ml_odds')
        side = pp.get('side')
        side_ml = home_ml if side == 'HOME' else away_ml if side == 'AWAY' else None
        pp_type = (pp.get('type') or 'ml').lower()
        # 2026-09-06 readability fix — earlier version stored pick='Under'
        # with no line for TOTAL items (users saw a bare "Over"/"Under"
        # on the sharp card). Root cause: pp.label sometimes lacks the
        # line when apply_lr_total_override fires before close_total is
        # populated, so label falls back to just the side.title(). Also
        # line was picked from close_spread first, wrong for totals.
        # Rebuild pick + line per market type so both fields always
        # carry the full context.
        raw_label = pp.get('label') or ''
        pp_line = pp.get('line')
        if pp_type == 'total':
            line = pp_line if pp_line is not None else g.get('close_total')
            side_str = (side or '').title() if side else raw_label.split()[0] if raw_label else '—'
            pick = f'{side_str} {line}'.strip() if line is not None else (raw_label or side_str)
        elif pp_type in ('rl', 'spread'):
            line = pp_line if pp_line is not None else g.get('close_spread')
            pick = raw_label or (f'{g.get("home_team") if side=="HOME" else g.get("away_team")} '
                                 f'{"+" if (line or 0) > 0 else ""}{line}').strip()
        else:  # ml
            line = None
            pick = raw_label or f'{g.get("home_team") if side=="HOME" else g.get("away_team")} ML'
        # (5) 2026-09-06 juice cap on MLB sole ML picks too (parallel to
        # _compose_other_sport_sides). Swap to spread if ML < -300 and
        # close_spread present. MLB spread = run line ±1.5 so treat
        # differently — swap to RL of the same team.
        juice_swapped = False
        if pp_type == 'ml' and side_ml is not None:
            try:
                if float(side_ml) < SOLE_PICK_ML_JUICE_MAX and g.get('close_spread') is not None:
                    sp = float(g.get('close_spread'))
                    rl_line = -sp if side == 'HOME' else sp
                    team = g.get('home_team') if side == 'HOME' else g.get('away_team')
                    sign = '+' if rl_line > 0 else ''
                    pp_type = 'rl'
                    pick = f'{team} {sign}{rl_line:g}'
                    line = rl_line
                    side_ml = -110  # RL odds default
                    juice_swapped = True
            except (TypeError, ValueError):
                pass
        picks.append({
            'sport': 'MLB',
            'matchup': f"{g.get('away_team')} @ {g.get('home_team')}",
            'tier': pp.get('tier'),
            'pick': pick,
            'type': pp_type,
            'reason': pp.get('sub') or '',
            'odds': side_ml if pp_type == 'ml' else -110,
            'line': line,
            'units': _units_for_pick(pp.get('tier'), pp_type,
                                     side_ml if pp_type == 'ml' else -110,
                                     side_price_american=side_ml),
            # 2026-09-17: preserve conviction + game_id so publish_lock
            # at write-time can snapshot (sport, market, source_id) →
            # tier + conviction. See prop_publish_lock.py.
            'conviction': pp.get('conviction'),
            'game_id': g.get('game_id'),
        })
    if lr_conflict_drops:
        print(f'  ⛔ MLB sides dropped by LR shadow gate: {lr_conflict_drops}')
    return [p for p in picks if p['units'] > 0]


def _compose_mlb_props(mlb_props: list, playbook: list) -> list[dict]:
    playbook_by_key = {}
    for d in playbook:
        k = f"{d.get('player_name')}|{d.get('prop_type')}|{d.get('direction')}|{d.get('prop_line')}"
        playbook_by_key[k] = d

    # 2026-09-12 DIRECTION-FLIP GUARD (v1.0.1 item #1 / feedback_fade_not_suppress_803).
    # apply_prop_refit + prop_tier_calibration can FLIP the winning direction
    # for a (player, family) — e.g. Alcantara ha_over PRIME → ha_under STRONG.
    # The flip lands in prop_jerry_reads with the new direction; the raw
    # mlb_pipeline_props row for the ORIGINAL direction stays at its old tier.
    # Prior composer iterated mp rows and could publish "Alcantara Over 5.5 HA"
    # while Jerry read said "fade the over". 14d audit: 102 such mismatches.
    #
    # Fix: fetch canonical direction per (player, family) from prop_jerry_reads.
    # If a mp row's direction doesn't match the canonical, skip it — the other
    # direction's mp row (if publishable) will surface with the correct side.
    _mlb_gd = None
    try:
        _mlb_gd = (mlb_props[0].get('game_date') if mlb_props else None) or _today_et()
    except Exception:
        _mlb_gd = _today_et()
    jerry_canonical: dict = {}   # (player_name, family) -> winning_direction
    try:
        _jr = requests.get(
            f'{SB}/rest/v1/prop_jerry_reads',
            params={'sport': 'eq.MLB', 'game_date': f'eq.{_mlb_gd}',
                    'call_verdict': 'in.(PRIME,STRONG,LEAN,BACK)',
                    'select': 'player_name,prop_type,direction,conviction'},
            headers={'apikey': K, 'Authorization': f'Bearer {K}'},
            timeout=10)
        if _jr.status_code == 200:
            for jrow in (_jr.json() or []):
                if not isinstance(jrow, dict): continue
                _pt = jrow.get('prop_type') or ''
                _fam = _pt[:-len('_over')] if _pt.endswith('_over') else (
                       _pt[:-len('_under')] if _pt.endswith('_under') else _pt)
                _k = (jrow.get('player_name'), _fam)
                _dir = jrow.get('direction')
                # If multiple jerry_reads exist for the same family (rare), keep
                # the one with the higher conviction — that's the winner post-flip.
                _existing = jerry_canonical.get(_k)
                if _existing is None or (jrow.get('conviction') or 0) > _existing[1]:
                    jerry_canonical[_k] = (_dir, jrow.get('conviction') or 0)
    except Exception as _e:
        # Never block composition on the lookup failure — fall through to
        # legacy behavior (composer publishes based on mp row direction).
        print(f'  ⚠ direction-flip guard lookup failed (non-fatal): {_e}')

    picks = []
    # 2026-09-06 gate: track why props got dropped so we can spot silent
    # regressions (e.g., pipeline flooding kill_gate flags on real signal).
    dropped = {'coverage_kill': 0, 'playbook_gate': 0, 'lr_tier_drift': 0,
               'direction_flip': 0}
    for p in mlb_props:
        # 2026-09-12 DIRECTION-FLIP GUARD. Skip if a jerry_read for the same
        # (player, family) exists with a DIFFERENT direction — that's the
        # canonical winner post-flip. The other-direction mp row (if
        # publishable) surfaces with the correct side; if it isn't
        # publishable, we correctly drop this (player, family) entirely
        # rather than ship contradiction.
        _pt = p.get('prop_type') or ''
        _fam = _pt[:-len('_over')] if _pt.endswith('_over') else (
               _pt[:-len('_under')] if _pt.endswith('_under') else _pt)
        _canonical = jerry_canonical.get((p.get('player_name'), _fam))
        if _canonical is not None and _canonical[0] != p.get('direction'):
            dropped['direction_flip'] += 1
            continue
        # ── Hard gate: publishability (signal-quality flags trump tier) ──
        publishable, reason = _is_prop_publishable(p)
        if not publishable:
            if 'coverage_kill' in reason: dropped['coverage_kill'] += 1
            elif 'playbook_gate' in reason: dropped['playbook_gate'] += 1
            elif 'lr_tier_drift' in reason: dropped['lr_tier_drift'] += 1
            continue
        pb_key = f"{p.get('player_name')}|{p.get('prop_type')}|{p.get('direction')}|{p.get('prop_line')}"
        pb = playbook_by_key.get(pb_key)
        # Tier: conservative of stored + LR raw, then playbook resolve
        base_tier = _effective_prop_tier(p) or p.get('tier')
        effective_tier = _resolve_tier(base_tier,
                                        pb.get('playbook_tier') if pb else None,
                                        pb.get('playbook_side') if pb else None)
        if not _is_ps(effective_tier): continue
        if not _prop_team_matches(p.get('player_team'), p.get('matchup')): continue
        # 2026-09-09 CONVICTION FLOOR (Fix C). Sharp Card was picking
        # marginal PRIMEs — 9/8 audit: overall PRIME pool 32-4 (89%),
        # but Sharp Card's 20-PRIME subset went 10-9 (52%). Root cause:
        # composer surfaced PRIMEs with conv 65-72 that graded near-random,
        # while high-conv PRIMEs (>=73) hit 90%+. Floor at 73 removes the
        # noise band. Refit_conviction preferred over base conviction when
        # available (it's the calibrated version).
        _p_conv = p.get('refit_conviction') if p.get('refit_conviction') else p.get('conviction')
        if effective_tier == 'PRIME' and (_p_conv or 0) < 73:
            continue
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
            # 2026-09-08: propagate player_team into composed item so
            # downstream (smoke test, render) can verify team assignment.
            # Prior composer stripped this field → every item had
            # player_team=null which made auditing orphan pitchers
            # impossible from the cache alone.
            'player_team': p.get('player_team'),
            'units': units,
            # 2026-09-17: preserve mlb_pipeline_props.id + conviction so
            # publish_lock at write-time can snapshot the effective tier.
            'id': p.get('id'),
            'conviction': _p_conv,
            'playbook_lifted': PROP_PLAYBOOK_ENABLED and pb and pb.get('playbook_tier')
                                and effective_tier != p.get('tier'),
        })
    if any(dropped.values()):
        print(f'  ⛔ Prop gate dropped: coverage_kill={dropped["coverage_kill"]} '
              f'playbook_gate={dropped["playbook_gate"]} lr_tier_drift={dropped["lr_tier_drift"]} '
              f'direction_flip={dropped["direction_flip"]}')
    return picks


def _compose_other_sport_sides(rows: list, sport: str) -> list[dict]:
    is_football = sport in ('NCAAF', 'NFL')
    tier_gate = _is_ps if (is_football and not FOOTBALL_INCLUDE_LEAN) else _is_any_tier
    dropped_lean = dropped_chalk = dropped_pass = 0
    dropped_lr_conflict = 0
    dropped_anchor = 0
    picks = []
    for g in rows:
        pp = g.get('primary_play') or {}
        if not isinstance(pp, dict): continue
        # 2026-09-13 ANCHOR REFUSE (football-only). Any pick where the
        # market spread anchor fired (weight > 0) is refused from the
        # Sharp Card entirely. Signal_attribution data as of 9/13: anchor-
        # fired picks hit 30% (12-28) — anchored picks are the ensemble
        # saying "market disagrees strongly, we're blending toward it"
        # and historically we lose that trade. Refuse > cap because Sharp
        # Card is our highest-conviction surface.
        if is_football:
            try:
                aw = float(g.get('spread_anchor_weight') or 0)
                if aw > 0:
                    dropped_anchor += 1
                    continue
            except (TypeError, ValueError):
                pass
        # 2026-09-06 sanitize: primary_play.type='pass' means Jerry chose
        # NO ACTION. Don't publish these — they'd render as broken items
        # on the sharp card. If we want a "no plays tonight" empty state
        # that's handled at render, not composition.
        if (pp.get('type') or '').lower() == 'pass':
            dropped_pass += 1
            continue
        # 2026-09-08 LR SHADOW GATE (parallel to _compose_mlb_sides).
        # For NCAAF this catches ~100% of shadow-endorsed conflicts since
        # NCAAF total LR runs in shadow-only mode by design. For NFL,
        # catches whatever shadow coverage exists (~41% today).
        if _lr_shadow_conflict(pp):
            dropped_lr_conflict += 1
            continue
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
        pick_type = pp.get('type') or 'ml'
        pick_label = pp.get('label') or '—'
        pick_line  = pp.get('line')
        pick_odds  = side_ml if pick_type == 'ml' else None

        # (5) 2026-09-06 sole-pick juice cap. If the primary_play is an ML
        # juicier than SOLE_PICK_ML_JUICE_MAX, auto-swap to the spread
        # side of the same team so users don't stake 2u to win 0.5u.
        # Ledger surface builds its own composer and keeps heavier chalks.
        # Requires close_spread to be present; else we skip the pick
        # rather than publish an over-juiced ML.
        juice_swapped = False
        if pick_type == 'ml' and side_ml is not None:
            try:
                if float(side_ml) < SOLE_PICK_ML_JUICE_MAX:
                    close_spread = g.get('close_spread')
                    if close_spread is not None:
                        # Store convention: our `close_spread` is stored
                        # away-perspective (positive = home favored by
                        # that many). Compute the pick-side line.
                        sp = float(close_spread)
                        spread_line = -sp if side == 'HOME' else sp
                        team = g.get('home_team') if side == 'HOME' else g.get('away_team')
                        sign = '+' if spread_line > 0 else ''
                        pick_type = 'spread'
                        pick_label = f'{team} {sign}{spread_line:g}'
                        pick_line = spread_line
                        pick_odds = -110
                        juice_swapped = True
                    else:
                        # No spread available — skip rather than publish over-juiced ML
                        continue
            except (TypeError, ValueError):
                pass

        units = _units_for_pick(pp.get('tier'), pick_type,
                                 pick_odds,
                                 side_price_american=side_ml)
        if units <= 0: continue
        picks.append({
            'sport': sport,
            'matchup': f"{g.get('away_team')} @ {g.get('home_team')}",
            'tier': pp.get('tier'),
            'pick': pick_label,
            'type': pick_type,
            'reason': (pp.get('sub') or '') + (' [juice-cap swap: ML→spread]' if juice_swapped else ''),
            'odds': pick_odds,
            'line': pick_line,
            'units': units,
            'juice_swapped': juice_swapped,
        })
    if is_football and (dropped_lean or dropped_chalk or dropped_pass or dropped_anchor):
        print(f'  {sport} discipline drops: LEAN={dropped_lean}  chalky-STRONG={dropped_chalk}  '
              f'no-play={dropped_pass}  anchor={dropped_anchor}')
    elif dropped_pass:
        print(f'  {sport} no-play drops: {dropped_pass}')
    if dropped_lr_conflict:
        print(f'  {sport} LR-shadow-conflict drops: {dropped_lr_conflict}')
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

def _publish(today: str, items: list[dict], dry_run: bool, force: bool = False,
             cold_state: dict | None = None):
    # 2026-09-09 PUBLISH LOCK.
    # Sharp Card was being overwritten on every cron cycle (~9 hours of
    # rewrites/day). Users bet based on 8am board, then 2pm cron shipped
    # different picks — trust destroyed. Once the card is published for
    # a given day, later crons skip the write. Odds/line updates still
    # flow via primary_play + jerry_reads elsewhere; the SHARP CARD
    # ITEMS themselves are what stay frozen for the user.
    #
    # Bypass with --force (manual admin correction) OR set env
    # SHARP_CARD_ALLOW_REPUBLISH=1 (rare emergency).
    #
    # 2026-09-12 HARD LOCK — added by Andy directive after NCAAF picks
    # flipped mid-day when I --force'd a republish at 11am ET to add
    # missing PRIME props. Users saw picks change — trust killer, and
    # Ledger would grade the LATEST pick, not what users saw. Fix: past
    # HARD_LOCK_ET_HOUR (default 11 = 11am ET), no republish is
    # accepted regardless of --force or SHARP_CARD_ALLOW_REPUBLISH.
    # Emergency override: SHARP_CARD_EMERGENCY_UNLOCK=1 (distinct env
    # so a stray --force can't sneak through). Once hard-locked, users
    # + Ledger + Ladder + POTD all agree on the same displayed picks
    # for the rest of the day. Backend re-scoring flows to tomorrow's
    # cron, not today's locked publication.
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    _et_hour = (_dt.now(_tz.utc) - _td(hours=4)).hour  # EDT — TODO: DST switch
    # 2026-09-18 Andy approved: bump lock 11 → 15 ET. Gives late-morning
    # window for view fixes / migration reloads to land before the card
    # freezes. Prior 11am lock trapped 5am cron output when a fix landed
    # after the lock hour (case: PRIME-honors-raw view migration 20260918c
    # applied ~13:00 ET, Sharp Card was already locked with stale PRIMEs).
    # 2026-09-19 Andy: "whatever comes out in the morning stays. No more
    # refreshing the sweat card, sharp, or jerry game analysis picks."
    # 15 → 12. Today's Sharp Card published 10:52 ET and republished at
    # 12:09 — legal under a 15:00 lock, and precisely the churn described
    # above. Kept in step with SWEAT_CARD_HARD_LOCK_ET_HOUR and
    # MLB_PICK_LOCK_ET_HOUR; if these three drift apart the card, the
    # pick and the read stop agreeing with each other.
    _HARD_LOCK_HOUR = int(os.environ.get('SHARP_CARD_HARD_LOCK_ET_HOUR', '12'))
    _emergency = os.environ.get('SHARP_CARD_EMERGENCY_UNLOCK') == '1'
    _past_lock = _et_hour >= _HARD_LOCK_HOUR

    allow_republish = force or os.environ.get('SHARP_CARD_ALLOW_REPUBLISH') == '1'
    if _past_lock and not _emergency and not dry_run:
        # Check if existing publication has real content — only hard-lock
        # a card that was actually published; empty-stub case still allows
        # first publish even past the lock hour (unusual but possible on
        # a cron reboot).
        try:
            r_ex = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H_READ,
                params={'cache_key': f'eq.sharp_card_{today}',
                        'select': 'fetched_at,data'}, timeout=10)
            if r_ex.status_code == 200 and r_ex.json():
                _ex_data = (r_ex.json()[0].get('data') or {})
                if (_ex_data.get('count') or 0) > 0:
                    print(f'  🔒🔒 sharp_card_{today} HARD-LOCK — {_et_hour:02d}:00 ET '
                          f'past {_HARD_LOCK_HOUR:02d}:00 lock. Republish REFUSED '
                          f'even with --force. Emergency override: '
                          f'SHARP_CARD_EMERGENCY_UNLOCK=1')
                    return
        except Exception as _e:
            print(f'  ⚠ hard-lock check failed: {_e} — proceeding')

    if not dry_run and not allow_republish:
        try:
            r_existing = requests.get(
                f'{SB}/rest/v1/jerry_cache',
                headers=H_READ,
                params={'cache_key': f'eq.sharp_card_{today}',
                        'select': 'fetched_at,data'},
                timeout=10,
            )
            if r_existing.status_code == 200 and r_existing.json():
                row = r_existing.json()[0]
                existing_count = ((row.get('data') or {}).get('count') or 0)
                # Only lock if existing card has real items (not an empty stub)
                if existing_count > 0:
                    # 2026-09-11 MATERIALLY-BETTER OVERRIDE. Prior lock was
                    # absolute — 8am cron published 2 PRIME + 13 STRONG props
                    # because pipeline was mid-landing, then 11am refit
                    # produced 15 PRIME survivors but lock blocked update.
                    # Result: user's Sharp tab showed STRONG props while a
                    # much stronger PRIME deck sat un-published. Fix: allow
                    # republish when new composition has MORE PRIME items
                    # than the existing card — that's a materially better
                    # deck, not churn. STRONG-count churn still locked.
                    # 2026-09-19: judge the DECK, not just the PRIME count.
                    #
                    # The old rule was `_new_primes > _existing_primes`. That
                    # blocks a materially better deck whenever the count is
                    # flat — e.g. 9/19, when 24 wrongly-demoted props were
                    # restored to PRIME but the card's 6 PRIMEs were all
                    # SIDES, so the comparison read "6 vs 6, no improvement"
                    # and held a deck of conviction-65 STRONG props in place.
                    # It also can't tell "same picks" from "completely
                    # different picks that happen to tier the same".
                    #
                    # Quality is compared as an ordered tuple:
                    #   (PRIME count, STRONG count, total conviction)
                    # Strictly better on that tuple => republish. Equal or
                    # worse => hold, which preserves the anti-churn intent:
                    # a merely DIFFERENT deck still does not get to flip
                    # picks out from under users mid-day.
                    def _deck_quality(_items):
                        _p = _s = 0
                        _conv = 0.0
                        _ids = set()
                        for _it in _items:
                            if not isinstance(_it, dict): continue
                            _t = str(_it.get('tier', '')).upper()
                            if _t == 'PRIME': _p += 1
                            elif _t == 'STRONG': _s += 1
                            try: _conv += float(_it.get('conviction') or 0)
                            except (TypeError, ValueError): pass
                            _ids.add(str(_it.get('pick') or _it.get('label') or ''))
                        return (_p, _s, round(_conv, 1)), _ids

                    _existing_items = (row.get('data') or {}).get('items') or []
                    _old_q, _old_ids = _deck_quality(_existing_items)
                    _new_q, _new_ids = _deck_quality(items)
                    _existing_primes, _new_primes = _old_q[0], _new_q[0]
                    _changed = len(_new_ids ^ _old_ids)

                    if _new_q > _old_q:
                        print(f'  🔓 sharp_card_{today} republish allowed: '
                              f'PRIME {_old_q[0]}→{_new_q[0]}, STRONG {_old_q[1]}→{_new_q[1]}, '
                              f'conviction {_old_q[2]}→{_new_q[2]} '
                              f'({_changed} pick(s) differ) — materially better deck')
                    else:
                        print(f'  🔒 sharp_card_{today} already published '
                              f'({existing_count} items, {_existing_primes} PRIME '
                              f'@ {row["fetched_at"][:19]}) — new deck '
                              f'(PRIME {_new_q[0]}, STRONG {_new_q[1]}, conv {_new_q[2]}) '
                              f'is not better than current '
                              f'(PRIME {_old_q[0]}, STRONG {_old_q[1]}, conv {_old_q[2]}), '
                              f'skipping.')
                        return
        except Exception as _e:
            print(f'  ⚠ publish-lock check failed: {_e} — proceeding with write')

    payload = {
        'items': items,
        'count': len(items),
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'config_version': CONFIG_VERSION,
        # 2026-09-09: cold-streak banner state — app renders warning banner
        # when state != 'normal' so users see the auto-tightening.
        'cold_state': cold_state or {'state': 'normal'},
    }
    if dry_run:
        print(f'  [DRY] would write {len(items)} items to jerry_cache.sharp_card_{today}')
        by_sport = {}
        for p in items:
            by_sport.setdefault(p['sport'], 0)
            by_sport[p['sport']] += 1
        print(f'         breakdown: {by_sport}')
        return
    # 2026-09-17: PUBLISH-LOCK. Snapshot tier + conviction for every
    # item at the moment it lands on the Sharp Card. First-publisher-
    # wins semantics via shared publish_lock table (migration
    # 20260917e). Grader honors these locks so mid-day tier mutations
    # to the live row can never change what the record counts.
    # Fail-soft: any lock error stays silent so a Supabase hiccup
    # doesn't block the cache write.
    try:
        from prop_publish_lock import lock_publish as _lock
        for _it in items:
            if not isinstance(_it, dict): continue
            _t  = (_it.get('type') or '').lower()
            _market = _t if _t in ('prop','ml','rl','total') else None
            if not _market: continue
            _sport = (_it.get('sport') or '').upper()
            if _market == 'prop':
                _sid = _it.get('id')
            else:
                _sid = _it.get('game_id')
            if not _sport or not _sid: continue
            _lock(_sport, _market, _sid,
                  (_it.get('tier') or '').upper(),
                  _it.get('conviction'),
                  'sharp_card')
    except Exception as _e:
        print(f'  ⚠ publish_lock (sharp_card) failed silently: {_e}')

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
        # 2026-09-20 LIVE RECEIPTS. Only after the publish actually
        # succeeded — a receipt for a card that failed to publish would be
        # evidence of something users never saw, which is worse than no
        # receipt.
        #
        # The Sharp had ZERO receipts of any kind before this, while its
        # record is the one quoted publicly. Every one of the 12,774
        # existing receipts is 'reconstructed' (rebuilt after the fact
        # from mutable tables); this is the first surface to produce
        # 'live' evidence, written in the same breath as the publish.
        #
        # First-write-wins on (sport, surface, game_date, source_id), so a
        # later reconstruction can never overwrite it, and a republish is
        # idempotent. Fail-soft — never lose the card over an audit row —
        # but never silent.
        try:
            from public_receipt import capture as _capture
            from public_receipt import sharp_card_rows as _rows
            _capture(_rows(items, today), surface='sharp_card')
        except Exception as _e:
            print(f'  ⚠ live receipt capture (sharp_card) failed: {_e}')
    else:
        print(f'  ✗ publish failed {r.status_code}: {r.text[:200]}')


def run(dry_run: bool = False, force: bool = False):
    today = _today_et()
    print(f'== Generate Sharp Card · {today} ==')

    # 2026-09-09 COLD-STREAK GATE — evaluate before compose. Cold-mode
    # tightens item cap + drops LEAN tier so the card publishes a smaller,
    # higher-conviction deck during losing runs. Auto-normalizes when
    # rolling 7-day hit rate climbs back.
    cold_state = _compute_sharp_cold_state()
    print(f'  cold-state check: state={cold_state["state"]} '
          f'hit_rate={cold_state.get("hit_rate")} '
          f'sample={cold_state["sample"]} · {cold_state.get("message","")}')

    # 2026-09-06 race guard. Sharp card was firing during partial pipeline
    # runs before apply_refit_verdict_override had stamped LR tiers today,
    # so the composed card saw stale tiers (props stuck at pre-LR values,
    # PRIMEs still marked LEAN/COVERAGE). All prop composers on this card
    # depend on `tier` and `signals._lr_tier_raw`, which that script sets.
    # Solution: self-run the override BEFORE composing. It's idempotent
    # (uses _coverage_kill_gate + _playbook_prop_gate tags to skip already-
    # processed rows), so calling it twice/day is safe. Cost is minimal
    # (~5-10s when everything's already tagged, ~30s on a fresh slate).
    # Cross-sport safe: when NFL/NCAAF prop composition wires into this
    # script, the same guarantee holds — tiers must be finalized before
    # composition regardless of which pipeline invoked us.
    print(f'  [guard] running apply_refit_verdict_override first (idempotent)')
    try:
        import subprocess as _sp
        _p = _sp.run(['python', str(Path(__file__).parent / 'apply_refit_verdict_override.py')],
                     capture_output=True, text=True, timeout=180)
        # Log summary only, not full noisy stdout
        _tail = '\n'.join((_p.stdout or '').splitlines()[-5:])
        print(f'  [guard] refit override completed rc={_p.returncode}')
        if _tail: print(f'  [guard] tail: {_tail}')
    except Exception as _e:
        print(f'  ⚠️  [guard] refit override call failed non-fatal: {_e}')

    sources = _fetch_all(today)
    print(f'  fetched: MLB ctx={len(sources["mlb_ctx"])} props={len(sources["mlb_props"])} '
          f'NFL={len(sources["nfl_ctx"])} NCAAF={len(sources["ncaaf_ctx"])} '
          f'NCAAB={len(sources["ncaab_ctx"])} NBA={len(sources["nba_ctx"])} '
          f'NHL={len(sources["nhl_ctx"])} UFC={len(sources["ufc_reads"])} '
          f'playbook={len(sources["playbook"])}')

    mlb_sides = _compose_mlb_sides(sources['mlb_ctx'])
    mlb_props = _compose_mlb_props(sources['mlb_props'], sources['playbook'])
    # 2026-09-09 MARKET FLOOR (user directive from surface walkthrough):
    # Sharp Card should always render at least 1 side + 1 total + 1 prop
    # for MLB even on days where convictions cluster in one market. If the
    # normal composers left any market empty, backfill with the strongest
    # candidate for that market from ctx/props (relaxed conv floor to 55).
    _floor_added = _ensure_mlb_market_floor(sources['mlb_ctx'], sources['mlb_props'],
                                             mlb_sides, mlb_props)
    if _floor_added:
        print(f'  ✚ market floor added: {len(_floor_added)} backfill picks')

    # 2026-09-17 MLB MARKET SPLIT — pre-cap sides + props separately
    # so props (79% hit rate) get slot priority over sides (51% hit rate).
    # See SHARP_CARD_MLB_PROPS_TARGET / SHARP_CARD_MLB_SIDES_TARGET rationale.
    # Sort each pool by tier priority then conviction desc BEFORE capping so
    # we take the strongest of each market rather than a mixed-in ordering.
    def _rank(it):
        return (_TIER_PRIORITY.get(it.get('tier'), 9),
                -float(it.get('conviction') or it.get('units') or 0))
    _pre_sides = len(mlb_sides)
    _pre_props = len(mlb_props)
    mlb_sides = sorted(mlb_sides, key=_rank)
    mlb_props = sorted(mlb_props, key=_rank)
    # Reserve prop slots first; unused prop capacity spills to sides.
    props_taken = mlb_props[:SHARP_CARD_MLB_PROPS_TARGET]
    prop_slack  = max(0, SHARP_CARD_MLB_PROPS_TARGET - len(props_taken))
    sides_cap   = SHARP_CARD_MLB_SIDES_TARGET + prop_slack
    sides_taken = mlb_sides[:sides_cap]
    if len(mlb_sides) > len(sides_taken) or len(mlb_props) > len(props_taken):
        print(f'  ⚖ MLB market split: sides {_pre_sides}→{len(sides_taken)} '
              f'(cap {sides_cap}), props {_pre_props}→{len(props_taken)} '
              f'(cap {SHARP_CARD_MLB_PROPS_TARGET}) — data-driven '
              f'prop-heavy allocation')
    mlb_sides = sides_taken
    mlb_props = props_taken
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

    # 2026-09-09 COLD-STREAK APPLIED HERE: drop LEAN when cold, and use
    # tighter cap. Runs BEFORE the normal cap so the cap logic sees the
    # already-filtered pool.
    if cold_state['state'] == 'cold':
        pre_cold = len(all_items)
        all_items = [it for it in all_items if str(it.get('tier','')).upper() in COLD_TIER_FILTER]
        print(f'  🥶 COLD-MODE filter: {pre_cold} → {len(all_items)} (dropped LEAN tier)')

    # Effective cap based on cold state
    _effective_cap = COLD_ITEM_CAP if cold_state['state'] == 'cold' else SHARP_CARD_ITEM_CAP

    # (3) Cap total items with per-sport quota + tier priority.
    # Step A: sort each sport bucket by (tier, -units).
    # Step B: take top N per sport per SHARP_CARD_PER_SPORT_MAX.
    # Step C: if still over _effective_cap, trim from lowest-tier
    #         of each sport proportionally.
    if _effective_cap is not None and len(all_items) > _effective_cap:
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
        if len(capped) > _effective_cap:
            capped.sort(key=lambda it: (
                _TIER_PRIORITY.get(it.get('tier'), 9),
                _SPORT_PRIORITY.get(it.get('sport'), 9),
                -float(it.get('units') or 0),
            ))
            capped = capped[:_effective_cap]
        all_items = capped
        print(f'  cap applied: {pre} → {len(all_items)} (per-sport quotas + hard cap {_effective_cap})')

    _publish(today, all_items, dry_run, force=force, cold_state=cold_state)
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true',
                    help='Bypass the publish lock — overwrites today\'s '
                         'card even if already published. Reserved for '
                         'manual admin correction of a bad card. Normal '
                         'crons should NOT use this flag.')
    args = ap.parse_args()
    sys.exit(run(dry_run=args.dry_run, force=args.force))
