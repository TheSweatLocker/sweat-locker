"""
Universal POTD candidate pool.

Per project_potd_universal_pool_720 (2026-07-20): POTD is the highest-conviction
play across ALL surfaces (props / sides / totals) and ALL sports (MLB / NBA /
NHL / NCAAB / NCAAF / UFC). Today's 2nd REST DAY in 3 days despite pipeline
firing PRIME 100 Springs ER + PRIME 68 Weathers outs @ 92-97% class rate
proved the old game-only candidate pool was starving POTD.

DESIGN:
- build_potd_candidates(sports) returns a flat list of Candidate dicts
- Each candidate carries a composite_score that ranks across play types
- REST DAY fires ONLY if top composite < FLOOR (default 60)

Wired from play_of_day.run() in place of the game-only candidate pool. Legacy
game-level candidate construction still runs for sweat_score writes to
mlb_game_context — this module handles the POTD selection layer.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}

# ── Ranking constants ──
POTD_FLOOR = 60.0           # composite score below this = REST DAY
MIN_CLASS_N = 20            # class calibration ignored below this sample size
JUICE_MAX_BE = 0.65         # skip candidates whose breakeven exceeds this (65%)
CROSS_SIGNAL_MAX = 4        # cap on cross-signal bonus

# ── Fallback class hit rates when prop_edge_calibration row is thin ──
FALLBACK_CLASS_RATES = {
    ('outs_under', 'PRIME'): 92.0,   # historical anchor from 90d
    ('outs_under', 'STRONG'): 88.0,
    ('outs_over', 'PRIME'): 62.0,
    ('er_over', 'PRIME'): 66.0,
    ('er_under', 'PRIME'): 70.0,
    ('ha_under', 'PRIME'): 66.7,     # from 7/20 audit — 85+ conv band
    ('ha_over', 'PRIME'): 56.0,
    ('ks_over', 'PRIME'): 62.0,
    ('ks_under', 'PRIME'): 60.0,
    ('bb_over', 'PRIME'): 55.0,
    ('bb_under', 'PRIME'): 58.0,
    ('hits_over', 'PRIME'): 62.0,    # batter hits O 0.5
}


@dataclass
class Candidate:
    """Unified POTD candidate — one instance per playable surface (prop or game side/total)."""
    sport: str
    play_type: str                        # 'prop' | 'side' | 'total' | 'runline'
    label: str                            # human display (e.g. "Ryan Weathers Under 17.5 outs")
    game_id: str
    matchup: str                          # "PIT @ NYY"

    # Conviction inputs
    pipeline_tier: str                    # PRIME / STRONG / LEAN / SKIP
    pipeline_conviction: float            # raw numeric 0-100

    # Empirical anchor
    class_hit_rate: Optional[float] = None    # from prop_edge_calibration or fallback
    class_sample_n: Optional[int] = None

    # Market
    book_odds: Optional[int] = None       # American odds
    breakeven_pct: Optional[float] = None
    line: Optional[float] = None

    # Model context
    projection: Optional[float] = None
    cushion: Optional[float] = None       # projection − line (or line − projection for unders)

    # Cross-signal
    cross_signal_count: int = 0           # independent lens agreements (0..N)

    # Derived
    composite_score: float = 0.0
    rank_notes: list = field(default_factory=list)

    # Payload for POTD writer
    source_row: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        d.pop('source_row', None)
        return d


# ═══════════════════════════════════════════════════════════════════════════
# Composite ranker
# ═══════════════════════════════════════════════════════════════════════════

def american_to_breakeven(odds: Optional[int]) -> Optional[float]:
    if odds is None:
        return None
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def score_composite(c: Candidate) -> float:
    """
    Composite = hit_rate × cushion_mult × cross_signal_weight − juice_penalty.

    Notes on shape:
    - hit_rate is % (0-100). If class sample is thin, fall back on pipeline_conviction
      scaled to a hit-rate estimate (PRIME ≈ 60, STRONG ≈ 55).
    - cushion_mult rewards projection cushion beyond line. Bounded [0.8, 1.3].
    - cross_signal_weight: 1.0 + 0.05 × min(cross_signal_count, CROSS_SIGNAL_MAX)
    - juice_penalty: if breakeven > 60%, subtract (breakeven - 60) × 0.5 points
    - Class sample < MIN_CLASS_N halves the hit_rate contribution
      (small-sample skepticism)
    """
    # 1. Hit-rate base
    if c.class_hit_rate is not None:
        hr = c.class_hit_rate
        if c.class_sample_n and c.class_sample_n < MIN_CLASS_N:
            # Regress toward pipeline-implied rate for thin samples
            fallback = _pipeline_implied_rate(c.pipeline_tier, c.pipeline_conviction)
            hr = (hr + fallback) / 2.0
            c.rank_notes.append(f'class n={c.class_sample_n} thin — averaged with pipeline-implied {fallback:.0f}')
    else:
        hr = _pipeline_implied_rate(c.pipeline_tier, c.pipeline_conviction)
        c.rank_notes.append(f'no class calibration — pipeline-implied {hr:.0f}')

    # 2. Cushion multiplier
    cushion_mult = 1.0
    if c.cushion is not None and c.line:
        pct_cushion = c.cushion / max(abs(c.line), 0.5)
        cushion_mult = min(1.3, max(0.8, 1.0 + pct_cushion * 0.5))

    # 3. Cross-signal weight
    xsig_mult = 1.0 + 0.05 * min(c.cross_signal_count, CROSS_SIGNAL_MAX)

    base = hr * cushion_mult * xsig_mult

    # 4. Juice penalty
    penalty = 0.0
    if c.breakeven_pct and c.breakeven_pct > 60:
        penalty = (c.breakeven_pct - 60) * 0.5
        c.rank_notes.append(f'juice penalty −{penalty:.1f} (BE {c.breakeven_pct:.1f}%)')

    return round(base - penalty, 2)


def _pipeline_implied_rate(tier: str, conv: float) -> float:
    """Map pipeline conviction to a hit-rate estimate when class data is unavailable."""
    tier = (tier or '').upper()
    if tier == 'PRIME':
        # PRIME base 60, +5 per 10 points of conv above 85
        return 60.0 + max(0, (conv - 85)) * 0.5
    if tier == 'STRONG':
        return 55.0 + max(0, (conv - 70)) * 0.3
    if tier == 'LEAN':
        return 52.0 + max(0, (conv - 55)) * 0.2
    return 50.0


# ═══════════════════════════════════════════════════════════════════════════
# Class calibration lookup
# ═══════════════════════════════════════════════════════════════════════════

def load_class_calibration() -> dict:
    """Fetch prop_edge_calibration into a lookup dict keyed (prop_type, tier)."""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/prop_edge_calibration?window_days=eq.30&select=prop_type,tier,direction,hit_rate,sample_size,category",
            headers=HEADERS,
            timeout=10,
        )
        rows = r.json() or []
    except Exception as e:
        print(f'  ⚠ class calibration fetch failed: {e}')
        rows = []

    lookup = {}
    for row in rows:
        key = (row.get('prop_type'), row.get('tier'))
        lookup[key] = {
            'hit_rate': row.get('hit_rate'),
            'sample_n': row.get('sample_size'),
            'category': row.get('category'),
        }
    return lookup


def resolve_class_rate(prop_type: str, tier: str, calibration: dict) -> tuple:
    """Return (hit_rate, sample_n) using calibration first, fallback second."""
    row = calibration.get((prop_type, tier))
    if row and row.get('hit_rate') is not None:
        return row['hit_rate'], row.get('sample_n')
    fb = FALLBACK_CLASS_RATES.get((prop_type, tier))
    return fb, None


# ═══════════════════════════════════════════════════════════════════════════
# MLB candidate builders
# ═══════════════════════════════════════════════════════════════════════════

def build_mlb_prop_candidates(game_date: str, calibration: dict) -> list[Candidate]:
    """Build PropCandidate from mlb_pipeline_props for PRIME + STRONG rows."""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props"
            f"?game_date=eq.{game_date}&tier=in.(PRIME,STRONG)"
            f"&select=game_id,player_name,player_team,matchup,prop_type,prop_line,"
            f"direction,tier,conviction,book_line,book_over_odds,book_under_odds,"
            f"signals&order=conviction.desc",
            headers=HEADERS,
            timeout=15,
        )
        rows = r.json() or []
    except Exception as e:
        print(f'  ⚠ prop pull failed: {e}')
        return []

    out = []
    for p in rows:
        direction = (p.get('direction') or '').lower()
        odds = p.get('book_over_odds') if direction == 'over' else p.get('book_under_odds')
        prop_type = p.get('prop_type')
        tier = p.get('tier')
        conv = p.get('conviction') or 0

        hit_rate, sample_n = resolve_class_rate(prop_type, tier, calibration)

        signals = p.get('signals') or {}
        if isinstance(signals, str):
            try:
                import json
                signals = json.loads(signals)
            except Exception:
                signals = {}
        projection = signals.get('projection') if isinstance(signals, dict) else None
        cushion = None
        line = p.get('prop_line')
        if projection is not None and line is not None:
            try:
                proj_f = float(projection)
                line_f = float(line)
                cushion = (line_f - proj_f) if direction == 'under' else (proj_f - line_f)
            except Exception:
                pass

        cand = Candidate(
            sport='MLB',
            play_type='prop',
            label=f"{p.get('player_name')} {direction.title()} {line} {prop_type.replace('_',' ')}",
            game_id=p.get('game_id'),
            matchup=p.get('matchup') or '',
            pipeline_tier=tier,
            pipeline_conviction=conv,
            class_hit_rate=hit_rate,
            class_sample_n=sample_n,
            book_odds=odds,
            breakeven_pct=american_to_breakeven(odds) * 100 if odds else None,
            line=line,
            projection=projection,
            cushion=cushion,
            cross_signal_count=1,  # base — bump if resolver/cohort also aligns
            source_row=p,
        )
        cand.composite_score = score_composite(cand)
        out.append(cand)
    return out


def build_mlb_game_candidates(game_date: str) -> list[Candidate]:
    """Build side + total candidates from resolver output in jerry_cache game_reads."""
    # Pull game_read rows for date
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/jerry_cache"
            f"?cache_key=like.game_read_%25_{game_date}&select=data",
            headers=HEADERS,
            timeout=15,
        )
        rows = r.json() or []
    except Exception as e:
        print(f'  ⚠ game_read pull failed: {e}')
        return []

    out = []
    import json
    for row in rows:
        d = row.get('data')
        if isinstance(d, str):
            try: d = json.loads(d)
            except Exception: continue
        if not d:
            continue

        matchup = d.get('matchup', '')
        gid = d.get('game_id', '')
        res_tot = d.get('resolver') or {}
        res_side = d.get('resolver_side') or {}
        conf = d.get('confluence') or {}
        cohort_net = _extract_cohort_net(d)

        # Total-side candidate
        if res_tot.get('tier') in ('STRONG', 'PRIME', 'ELITE'):
            cand = Candidate(
                sport='MLB',
                play_type='total',
                label=f"{res_tot.get('direction')} {(d.get('market') or {}).get('close_total')}",
                game_id=gid, matchup=matchup,
                pipeline_tier=res_tot.get('tier'),
                pipeline_conviction=_tier_to_conv(res_tot.get('tier')),
                cross_signal_count=_count_signals(conf, cohort_net),
                source_row=res_tot,
            )
            cand.composite_score = score_composite(cand)
            out.append(cand)

        # Side candidate
        if res_side.get('tier') in ('STRONG', 'PRIME', 'ELITE'):
            ml = res_side.get('ml_odds')
            cand = Candidate(
                sport='MLB',
                play_type='side',
                label=f"{res_side.get('team')} ML",
                game_id=gid, matchup=matchup,
                pipeline_tier=res_side.get('tier'),
                pipeline_conviction=_tier_to_conv(res_side.get('tier')),
                book_odds=int(ml) if ml is not None else None,
                breakeven_pct=american_to_breakeven(int(ml))*100 if ml else None,
                cross_signal_count=_count_signals(conf, cohort_net),
                source_row=res_side,
            )
            cand.composite_score = score_composite(cand)
            out.append(cand)

    return out


def _tier_to_conv(tier: str) -> float:
    return {'ELITE': 95, 'PRIME': 85, 'STRONG': 72, 'LEAN': 58, 'LIGHT': 52}.get(
        (tier or '').upper(), 50
    )


def _extract_cohort_net(game_read: dict) -> int:
    conf = game_read.get('confluence') or {}
    return conf.get('net') or 0


def _count_signals(confluence: dict, cohort_net: int) -> int:
    """Count independent aligned signals for cross-signal bonus."""
    voted = confluence.get('signals_voted') or 0
    total = confluence.get('signals_total') or 1
    ratio = voted / max(1, total)
    if ratio >= 0.7 and abs(cohort_net) >= 10:
        return 4
    if ratio >= 0.5 and abs(cohort_net) >= 5:
        return 3
    if ratio >= 0.4:
        return 2
    return 1


# ═══════════════════════════════════════════════════════════════════════════
# Other sports — thin adapters, expand as sports come online
# ═══════════════════════════════════════════════════════════════════════════

def build_nba_candidates(game_date: str, calibration: dict) -> list[Candidate]:
    # TODO: mirror mlb structure once NBA prop pipeline is unified per
    # [[project_nba_offseason_rebuild]]. Emit game-level candidates for now.
    return []


def build_nhl_candidates(game_date: str, calibration: dict) -> list[Candidate]:
    return []  # 2026-27 season target per [[project_nhl_scope]]


def build_ncaab_candidates(game_date: str) -> list[Candidate]:
    return []  # Nov 2026 target per [[project_ncaab_scope]]


def build_ncaaf_candidates(game_date: str) -> list[Candidate]:
    return []  # 2026 season target per [[project_ncaaf_scope]]


def build_ufc_candidates(game_date: str) -> list[Candidate]:
    return []  # per-event, activate on UFC card days


# ═══════════════════════════════════════════════════════════════════════════
# Main entry
# ═══════════════════════════════════════════════════════════════════════════

def build_potd_candidates(
    game_date: str,
    sports: tuple = ('MLB', 'NBA', 'NHL', 'NCAAB', 'NCAAF', 'UFC'),
) -> list[Candidate]:
    """Build the full unified candidate pool across all requested sports."""
    calibration = load_class_calibration()
    pool: list[Candidate] = []

    if 'MLB' in sports:
        pool.extend(build_mlb_prop_candidates(game_date, calibration))
        pool.extend(build_mlb_game_candidates(game_date))
    if 'NBA' in sports:
        pool.extend(build_nba_candidates(game_date, calibration))
    if 'NHL' in sports:
        pool.extend(build_nhl_candidates(game_date, calibration))
    if 'NCAAB' in sports:
        pool.extend(build_ncaab_candidates(game_date))
    if 'NCAAF' in sports:
        pool.extend(build_ncaaf_candidates(game_date))
    if 'UFC' in sports:
        pool.extend(build_ufc_candidates(game_date))

    # Filter: skip candidates priced above the juice ceiling
    pool = [c for c in pool if (c.breakeven_pct or 0) / 100 <= JUICE_MAX_BE]

    pool.sort(key=lambda c: c.composite_score, reverse=True)
    return pool


def select_potd(candidates: list[Candidate]) -> Optional[Candidate]:
    """Pick highest composite. REST DAY if top < POTD_FLOOR."""
    if not candidates:
        return None
    top = candidates[0]
    if top.composite_score < POTD_FLOOR:
        return None
    return top


# ═══════════════════════════════════════════════════════════════════════════
# CLI for standalone testing
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import sys, io
    from datetime import datetime, timedelta, timezone
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    date_arg = sys.argv[1] if len(sys.argv) > 1 else (
        (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()
    )

    print(f'\nUniversal POTD pool · {date_arg}\n' + '=' * 60)
    pool = build_potd_candidates(date_arg)
    print(f'Total candidates: {len(pool)}\n')

    print(f'{"RANK":<5} {"SPORT":<5} {"TYPE":<7} {"CONV":<5} {"HR%":<6} {"BE%":<7} {"CUSH":<6} {"XSIG":<5} {"COMP":<7} LABEL')
    print('-' * 130)
    limit = int(os.environ.get('POTD_POOL_LIMIT', 40))
    for i, c in enumerate(pool[:limit], 1):
        hr = f'{c.class_hit_rate:.0f}' if c.class_hit_rate else '-'
        be = f'{c.breakeven_pct:.1f}' if c.breakeven_pct else '-'
        cush = f'{c.cushion:+.1f}' if c.cushion is not None else '-'
        print(f'{i:<5} {c.sport:<5} {c.play_type:<7} {c.pipeline_conviction:<5.0f} {hr:<6} {be:<7} {cush:<6} {c.cross_signal_count:<5} {c.composite_score:<7.1f} {c.matchup} - {c.label}')

    print('\n' + '=' * 60)
    potd = select_potd(pool)
    if potd:
        print(f'\n[POTD] {potd.label}')
        print(f'   Sport: {potd.sport} - {potd.matchup}')
        print(f'   Tier: {potd.pipeline_tier} - Conv: {potd.pipeline_conviction}')
        print(f'   Class hit rate: {potd.class_hit_rate}% (n={potd.class_sample_n})')
        print(f'   Composite: {potd.composite_score}')
        print(f'   Notes: {"; ".join(potd.rank_notes)}')
    else:
        print(f'\n[REST DAY] top composite ({pool[0].composite_score if pool else "n/a"}) below floor ({POTD_FLOOR})')
