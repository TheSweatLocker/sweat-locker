"""
Daily Degen — 3-5 leg parlay generated once per day server-side.
All users read the same record, no per-user generation.

Selects diverse legs from pipeline data:
  - Top-conviction pipeline props (Ks, Hits)
  - NRFI picks (90-94 PRIME tier only)
  - ML spread delta ≥ 3 (HIGH conviction)
  - Over/under total delta ≥ 3

Runs after generate_props.py in afternoon cron. Writes to daily_degen
table. Narrative pre-generated via Haiku once per day.

Table schema:
  CREATE TABLE daily_degen (
    game_date DATE PRIMARY KEY,
    legs JSONB NOT NULL,
    narrative TEXT,
    leg_count INT NOT NULL,
    avg_conviction NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
  );
"""
import os
import sys

# Reconfigure stdout for UTF-8 on Windows (cp1252 default crashes on emoji
# in print statements when run locally; cron runs on Linux so no-op there).
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')

HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'resolution=merge-duplicates,return=minimal',
}

TARGET_LEGS = 3  # dropped from 4 → 3 on 2026-07-27.
# Rationale: 92-parlay backfill graded 7-81 (8.0%) at 4 legs. Legs
# ran 190-148 = 56.2%. At -110 avg juice, 4-leg break-even = 7.52%
# — we cleared it but barely. 3-leg break-even = 14.4%, and at
# 56.2% legs a 3-leg hits 17.6% (theoretical). Also drops the
# lowest-conviction 4th leg entirely, which should nudge realized
# legs% slightly upward. Re-audit after 30 3-leg parlays.
MIN_LEGS = 2

# Audit-based prioritization (added 2026-05-04). Each candidate's static
# conviction gets multiplied by (live_30d_hit_rate / BASELINE_RATE). Cohorts
# hitting above baseline get boosted; cohorts hitting below get demoted.
# Below SUPPRESS_RATE the cohort is dropped entirely.
BASELINE_RATE = 0.55     # parlay-leg neutral expectation; +EV starts above
SUPPRESS_RATE = 0.42     # below this and the cohort is faded out of the pool
PROP_RATE_DEFAULT = 0.60 # props don't have a calibrated cohort yet; slight prior


def today_et():
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    return et.strftime('%Y-%m-%d')


def _f(v):
    try: return float(v)
    except: return None


def fetch_tier_rates(sport='mlb'):
    """Return {tier_name: hit_rate} from latest 30d window in the
    tier_calibration table for the given sport. Falls back to empty
    dict on error so the rest of the Degen still runs (just skips
    audit weighting). Defaults to 'mlb' so existing callers keep working."""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_tier_calibration"
            f"?window_label=eq.30d&sport=eq.{sport}"
            f"&select=tier,hit_rate,computed_date,total"
            f"&order=computed_date.desc",
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
            timeout=15,
        )
        rows = r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f"  ⚠️ tier rate fetch failed ({e}) — falling back to static conviction")
        return {}
    rates = {}
    for row in rows:
        # Take only the freshest row per tier (sorted desc by computed_date,
        # so first occurrence wins). Need n≥10 for the rate to be meaningful.
        t = row.get('tier')
        if not t or t in rates:
            continue
        if (row.get('total') or 0) < 10:
            continue
        rates[t] = float(row.get('hit_rate') or 0)
    return rates


def fetch_prop_tier_rates(days_back=30, min_n=10):
    """Live-compute (prop_type, tier) hit rates from mlb_pipeline_props for
    the last N days. Returns dict keyed `prop_{prop_type}_{tier}` so the
    cohort_key_for() lookup can find them alongside other tier rates.

    Why this isn't in mlb_tier_calibration: audit_tier_calibration.py
    writes ML/NRFI/spread cohorts but doesn't currently emit per-(type,tier)
    prop cohorts. Until that's added there, this function pulls from the
    source-of-truth `result` column on resolved props directly.

    Returns {'prop_hits_over_PRIME': 0.773, ...}. Min sample size enforced
    so single-game flukes don't dominate."""
    from collections import defaultdict
    try:
        gd_end = today_et()
        gd_start = (datetime.strptime(gd_end, '%Y-%m-%d') - timedelta(days=days_back)).strftime('%Y-%m-%d')
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props"
            f"?game_date=gte.{gd_start}&game_date=lt.{gd_end}"
            f"&result=in.(Win,Loss)"
            f"&select=prop_type,tier,result&limit=5000",
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
            timeout=20,
        )
        rows = r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f"  ⚠️ prop tier rate fetch failed ({e}) — props will use flat default")
        return {}

    counts = defaultdict(lambda: {'wins': 0, 'total': 0})
    for row in rows:
        pt = row.get('prop_type')
        tier = row.get('tier')
        if not pt or not tier:
            continue
        key = f"prop_{pt}_{tier}"
        counts[key]['total'] += 1
        if row.get('result') == 'Win':
            counts[key]['wins'] += 1

    rates = {}
    for key, c in counts.items():
        if c['total'] >= min_n:
            rates[key] = c['wins'] / c['total']
    return rates


def cohort_key_for(candidate, raw_score=None, sport='mlb'):
    """Map a Degen candidate to its audit cohort key. Returns None when
    no calibrated cohort applies (props use a default rate).

    `sport` is plumbed through so future NBA/NFL candidates can map to
    sport-specific cohort names. Currently only MLB cohorts are defined;
    when NBA pipeline launches, add an `if sport == 'nba'` branch."""
    ctype = candidate.get('type')
    sub = candidate.get('sub_type')
    if ctype == 'NRFI':
        # NRFI cohort buckets are score-defined; raw_score should be the
        # game's nrfi_score (passed in by extract_leg_candidates).
        if raw_score is None:
            return 'nrfi_prime_90_94'  # default — we only emit 90-94 NRFIs
        if 90 <= raw_score <= 94: return 'nrfi_prime_90_94'
        if raw_score >= 95:        return 'nrfi_volatile_95plus'
        if 70 <= raw_score <= 79:  return 'nrfi_lean_70_79'
        if 80 <= raw_score <= 89:  return 'nrfi_dead_80_89'
        if 60 <= raw_score <= 69:  return 'nrfi_60_69'
        if raw_score <= 40:        return 'yrfi_lean_le40'
        return 'nrfi_neutral_50_59'
    if ctype == 'ML':
        # raw_score for ML candidates is the confluence_net
        net = raw_score or 0
        if net >= 6:  return 'confluence_extreme_ge6'  # rare elite stack
        if net >= 4:  return 'confluence_prime_ge4'
        if net >= 2:  return 'confluence_strong_2_3'
        if net == 1:  return 'confluence_lean_1'
        return 'confluence_zero'
    if ctype == 'TOTAL':
        # v4 XGBoost total leans shipped 2026-05-18; no production audit yet.
        # Use a conservative baseline rate (slight prior over coin flip given
        # 62.4% direction acc on backtest, discounted for live-noise).
        return 'v4_total_lean'
    if ctype == 'PROP':
        # Per-cohort prop rate lookup keyed by (prop_type, tier). Populated
        # by fetch_prop_tier_rates() live query — replaces flat 0.60 default.
        return f"prop_{candidate.get('sub_type')}_{candidate.get('tier')}"
    return None


def fetch_todays_games():
    gd = today_et()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context?game_date=eq.{gd}&select=*",
        headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
        timeout=20
    )
    return r.json() if r.status_code == 200 else []


def fetch_pipeline_props():
    gd = today_et()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props?game_date=eq.{gd}&select=*&order=conviction.desc",
        headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
        timeout=20
    )
    return r.json() if r.status_code == 200 else []


def extract_leg_candidates(games, props):
    """Build a pool of candidate legs from all pipeline sources."""
    candidates = []

    # Pipeline props — use conviction as rank
    for p in props:
        ptype = p.get('prop_type')
        line = p.get('prop_line')
        sigs = p.get('signals') or {}
        # Uniform "{Over|Under} {line} {market}  ·  proj {proj}" format —
        # matches the server-composed _display_label in generate_props.py
        # so the Daily Degen surface, Sweat Card, and Prop Jerry all show
        # the SAME thing. 6/1 beta-user complaint: K props used to render
        # as "6.7 expected K (over)" hiding the actual book line, while
        # HA props rendered "Over 5.5 Hits Allowed · proj 7.1" showing
        # both. Unified to always show line + projection where projection
        # is available.
        #
        # Prefer the server-composed _display_label (already attaches the
        # player name and runs after Phase 2 recalibrate). Fall back to
        # legacy in-script composition for backward compat with rows that
        # predate the _display_label field.
        existing_label = sigs.get('_display_label')
        if existing_label:
            # _display_label includes player name; strip it for Daily Degen
            # which formats `{player} — {label}` separately.
            player_prefix = (p.get('player_name') or '') + ' '
            if existing_label.startswith(player_prefix):
                prop_label = existing_label[len(player_prefix):]
            else:
                prop_label = existing_label
        else:
            proj_ks = sigs.get('_projected_ks')
            proj_bb = sigs.get('_projected_bb')
            proj_ha = sigs.get('_projected_hits')
            proj_outs = sigs.get('_projected_outs')
            proj_er = sigs.get('_projected_er')
            if ptype == 'hits_over':
                prop_label = 'Over 0.5 Hits'
            elif ptype == 'hits_under':
                prop_label = 'Under 0.5 Hits'
            elif ptype == 'ks_over' and line is not None:
                prop_label = f"Over {line} Ks  ·  proj {proj_ks}" if proj_ks is not None else f"Over {line} Ks"
            elif ptype == 'ks_under' and line is not None:
                prop_label = f"Under {line} Ks  ·  proj {proj_ks}" if proj_ks is not None else f"Under {line} Ks"
            elif ptype == 'bb_over' and line is not None:
                prop_label = f"Over {line} BB  ·  proj {proj_bb}" if proj_bb is not None else f"Over {line} BB"
            elif ptype == 'bb_under' and line is not None:
                prop_label = f"Under {line} BB  ·  proj {proj_bb}" if proj_bb is not None else f"Under {line} BB"
            elif ptype == 'ha_over' and line is not None:
                prop_label = f"Over {line} Hits Allowed  ·  proj {proj_ha}" if proj_ha is not None else f"Over {line} Hits Allowed"
            elif ptype == 'ha_under' and line is not None:
                prop_label = f"Under {line} Hits Allowed  ·  proj {proj_ha}" if proj_ha is not None else f"Under {line} Hits Allowed"
            elif ptype == 'outs_over' and line is not None:
                prop_label = f"Over {line} Outs  ·  proj {proj_outs}" if proj_outs is not None else f"Over {line} Outs"
            elif ptype == 'outs_under' and line is not None:
                prop_label = f"Under {line} Outs  ·  proj {proj_outs}" if proj_outs is not None else f"Under {line} Outs"
            elif ptype == 'er_over' and line is not None:
                prop_label = f"Over {line} ER  ·  proj {proj_er}" if proj_er is not None else f"Over {line} ER"
            elif ptype == 'er_under' and line is not None:
                prop_label = f"Under {line} ER  ·  proj {proj_er}" if proj_er is not None else f"Under {line} ER"
            else:
                prop_label = f"{ptype} {line}"
        # 2026-08-11: use REAL book_odds when available, fall back to -150.
        # Also apply the same odds-range gate as sweat card ([-300, +150] per
        # feedback_prop_jerry_odds) and refit-trap check (skip if refit<30 on
        # high-tier props). Prevents DD from shipping legs at -400 juice or
        # trap-zone refits that Jerry would auto-PASS.
        direction = (p.get('direction') or '').lower()
        real_odds = (p.get('book_over_odds') if direction == 'over'
                     else p.get('book_under_odds') if direction == 'under'
                     else None)
        # Odds-range gate: skip legs priced outside [-300, +150]
        if real_odds is not None:
            try:
                oi = int(real_odds)
                if oi < -300 or oi > 150:
                    continue  # juice too heavy / payout too thin
            except (TypeError, ValueError):
                real_odds = None
        # Refit-trap check: skip if refit_conviction < 30 despite high raw
        # (Jerry auto-PASSes these — DD shouldn't publish them either)
        refit_c = p.get('refit_conviction')
        if refit_c is not None and refit_c < 30 and (p.get('conviction') or 0) >= 55:
            continue  # refit trap — skip
        # 2026-08-11: hits_over 0.5 null-odds guard. These props are almost
        # always priced -250 to -475 at book (batter has ~75% hit rate). If
        # sweeper didn't PATCH real odds, DEFAULT of -150 is a LIE that
        # over-promises payout. Skip unless real odds are populated.
        # feedback_batter_hits_juice_trap_803 documents the trap history.
        if p.get('prop_type') == 'hits_over' and real_odds is None:
            try:
                if float(p.get('prop_line') or 0) <= 0.5:
                    continue  # skip hits O 0.5 without real odds
            except (TypeError, ValueError): pass
        # Odds label: use real if known, otherwise stamped placeholder
        emit_odds = int(real_odds) if real_odds is not None else -150
        candidates.append({
            'type': 'PROP',
            'sub_type': p.get('prop_type'),
            'matchup': p.get('matchup'),
            'game_id': p.get('game_id'),
            'pick': f"{p.get('player_name')} — {prop_label}",
            'conviction': p.get('conviction'),
            'tier': p.get('tier'),
            'signals': list((p.get('signals') or {}).values())[:3],
            'odds_suggestion': emit_odds,
            '_real_odds': real_odds is not None,  # audit tag
        })

    # NRFI EMISSION RESHAPED 2026-06-06 after lifetime backtest
    # (_backtest_nrfi_reweight_606.py, n=855) revealed the right answer
    # isn't "kill NRFI" — it's "sharpen the gate." Lifetime cohort splits:
    #
    #   NRFI 95+ (volatile):    47.3% (n=93)   — losing, KILL
    #   NRFI 90-94 PRIME:       65.3% (n=49)   — +0.155 EV at -130, KEEP
    #   NRFI 85-89:             45.2% (n=42)   — losing, KILL
    #   NRFI 80-84:             50.0% (n=56)   — coinflip, KILL
    #   NRFI 70-79 mild lean:   53.4% (n=163)  — slight loss, KILL
    #
    #   Temp ≤45°F + NRFI ≥70:  65.0% (n=20)   — cold weather edge, KEEP
    #   Park ≤95 + NRFI ≥80:    58.6% (n=70)   — marginal, gate to PRIME
    #
    # New emission logic:
    #   1. PRIME 90-94 band emits as PRIME (65.3% lifetime, +0.155 EV)
    #   2. Cold weather (temp ≤45°F) + NRFI ≥70 emits as STRONG (65% n=20)
    #   3. All other bands suppressed
    #
    # YRFI sweet spot (6.0-7.9 1st-inn ERA + nrfi≤25) preserved at 63% audit.
    # The recent 14-day "decline" the morning audit flagged turned out to be
    # variance dip on n=11 — lifetime is solid. The fix is to read lifetime
    # rates in auto_fade, NOT to remove the band entirely.
    # Try to use the v2 logistic-regression scorer trained on n=448
    # held-out NRFI outcomes. Falls back to legacy "PRIME 90-94 only"
    # bucket if the scorer or weights file is unavailable.
    try:
        from nrfi_v2_scorer import score_nrfi, score_yrfi
        v2_available = True
    except Exception as _e:
        v2_available = False
        print(f"  ⚠️ nrfi_v2_scorer unavailable ({_e}); using legacy NRFI gate")

    # Need offense rows for v2 features. Pull once if available.
    offense_by_team = {}
    if v2_available:
        try:
            from generate_props import sb_get as _sb_get  # reuse already-imported helper
            _off = _sb_get('mlb_team_offense', {'season': 'eq.2026', 'select': 'team,inning_1_ops,inning_1_runs_per_game,inning_1_k_pct,inning_1_wrc_plus,inning_1_bb_pct,inning_1_hr_per_game'})
            offense_by_team = {o['team']: o for o in (_off or [])}
        except Exception:
            offense_by_team = {}

    seen_nrfi_games = set()
    for g in games:
        gid = g.get('game_id')
        if not gid or gid in seen_nrfi_games:
            continue
        nrfi_legacy = g.get('nrfi_score')
        h1 = _f(g.get('home_first_inning_era'))
        a1 = _f(g.get('away_first_inning_era'))
        try:
            max_fi = max(h1 or 0, a1 or 0)
        except (TypeError, ValueError):
            max_fi = 0.0

        pick_label = None
        conv = 0
        tier_label = None
        sig_label = None
        sub_type = None

        v2_nrfi_score = None
        v2_yrfi_score = None
        if v2_available:
            try:
                v2_nrfi_score = score_nrfi(g,
                    home_offense=offense_by_team.get(g.get('home_team')),
                    away_offense=offense_by_team.get(g.get('away_team')))
                v2_yrfi_score = score_yrfi(g,
                    home_offense=offense_by_team.get(g.get('home_team')),
                    away_offense=offense_by_team.get(g.get('away_team')))
            except Exception as _e:
                print(f"  ⚠️ v2 score failed for {gid[:8]}: {_e}")

        # v2 NRFI PRIME — top 10% lifetime hit 65.9%, holdout 77.8%
        if v2_nrfi_score is not None and v2_nrfi_score >= 70:
            pick_label = 'NRFI (No Run First Inning)'
            conv = 75
            tier_label = 'PRIME'
            sig_label = f"NRFI v2 model {v2_nrfi_score} — top 10% lifetime 65.9% (holdout 77.8%)"
            sub_type = 'nrfi'

        # v2 NRFI STRONG — 60-69 lifetime hit 69.3% n=75
        elif v2_nrfi_score is not None and v2_nrfi_score >= 60:
            pick_label = 'NRFI (No Run First Inning)'
            conv = 68
            tier_label = 'STRONG'
            sig_label = f"NRFI v2 model {v2_nrfi_score} — 60-69 band 69.3% lifetime"
            sub_type = 'nrfi'

        # v2 YRFI STRONG — top 20% predicts YRFI at 77.8% holdout
        # (require also 1st-inn ERA confirmation to avoid pure model-bet)
        elif v2_yrfi_score is not None and v2_yrfi_score >= 70 and max_fi >= 5.5:
            pick_label = 'YRFI (Run in First)'
            conv = 72
            tier_label = 'STRONG'
            sig_label = f"YRFI v2 model {v2_yrfi_score} + max 1st-inn ERA {max_fi:.1f} — top 20% lifetime 77.8%"
            sub_type = 'yrfi'

        # Legacy fallback when v2 unavailable: PRIME 90-94 only (65.3% lifetime)
        elif not v2_available and nrfi_legacy and 90 <= nrfi_legacy <= 94:
            pick_label = 'NRFI (No Run First Inning)'
            conv = 72
            tier_label = 'PRIME'
            sig_label = f"NRFI {nrfi_legacy} — legacy PRIME (v2 scorer unavailable)"
            sub_type = 'nrfi'

        else:
            continue  # nothing qualifying

        seen_nrfi_games.add(gid)
        candidates.append({
            'type': 'NRFI',
            'sub_type': sub_type,
            'matchup': f"{g.get('away_team')} @ {g.get('home_team')}",
            'game_id': gid,
            'pick': pick_label,
            'conviction': conv,
            'tier': tier_label,
            'signals': [
                sig_label,
                f"{g.get('home_pitcher')} xERA {g.get('home_sp_xera')}" if g.get('home_sp_xera') else f"{g.get('home_pitcher')}",
                f"{g.get('away_pitcher')} xERA {g.get('away_sp_xera')}" if g.get('away_sp_xera') else f"{g.get('away_pitcher')}",
            ],
            'odds_suggestion': -130,
            'raw_score': nrfi_legacy or 0,
            'nrfi_v2_score': v2_nrfi_score,
            'yrfi_v2_score': v2_yrfi_score,
        })

    # ML legs gated by SIGNAL CONFLUENCE + AUTO-FADE calibration (added 2026-04-25).
    # Confluence tiers: PRIME (>=+4) 71%, STRONG (>=+2) 55%, LEAN (>=+1) 47%.
    # Auto-fade then drops/flips picks in losing cohorts (ml_dog @ 25% hit, mixed
    # cohorts uncalibrated → SUPPRESS). See auto_fade.py for cohort logic.
    try:
        from auto_fade import adjust_pick
    except ImportError:
        adjust_pick = None

    for g in games:
        sd = _f(g.get('spread_delta'))
        ps = _f(g.get('projected_spread'))
        home_xera = _f(g.get('home_sp_xera'))
        away_xera = _f(g.get('away_sp_xera'))
        try:
            confluence_net = int(g.get('signal_confluence_net') or 0)
        except (TypeError, ValueError):
            confluence_net = 0
        if ps is None or home_xera is None or away_xera is None:
            continue
        if confluence_net < 1:
            continue
        # Hybrid tier formula: require both confluence + spread_delta to qualify.
        # Prevents zero-edge confluence picks (e.g. Dodgers PRIME +6 with delta +0.1)
        # from being selected. PRIME = ≥+4 AND |delta| ≥2.0; STRONG = ≥+2 AND ≥1.5;
        # LEAN = ≥+1 AND ≥1.0.
        abs_delta = abs(sd) if sd is not None else 0
        if confluence_net >= 4 and abs_delta < 2.0:
            continue
        if 2 <= confluence_net <= 3 and abs_delta < 1.5:
            continue
        if confluence_net == 1 and abs_delta < 1.0:
            continue

        # Apply auto-fade calibration
        fav_team = g.get('home_team') if ps > 0 else g.get('away_team')
        is_faded = False
        if adjust_pick is not None:
            res = adjust_pick(
                ps, g.get('close_spread'), confluence_net,
                g.get('home_team'), g.get('away_team'),
                home_ml=g.get('home_ml_odds'), away_ml=g.get('away_ml_odds')
            )
            if res['action'] == 'SUPPRESS':
                continue  # don't include in Degen pool
            fav_team = res['pick_team']
            is_faded = (res['action'] == 'FADE')

        # Hybrid tier: confluence net AND spread_delta both contribute. After the
        # threshold gate above, all picks here are valid; tier reflects strength.
        # NOTE (Phase 2 of engine_clarity_refactor): this is a DEGEN-SPECIFIC
        # tier label derived from confluence+spread alignment, NOT the
        # system-wide PRIME definition (sweat_dim score >= 80 + resolver
        # STRONG/ELITE). Daily Degen is a higher-volatility surface and
        # uses its own criteria. Universal taxonomy refactor will unify
        # these labels at the app render layer with explicit source tags.
        tier = 'PRIME' if confluence_net >= 4 else 'STRONG' if confluence_net >= 2 else 'LEAN'
        conviction = min(90, 60 + confluence_net * 6 + min(int(abs_delta * 3), 12))
        breakdown = g.get('signal_confluence_breakdown') or {}
        sig_str = ', '.join(f"{k}" for k in breakdown.keys()) or 'multiple signals'

        # Chalk ML alt-line check (added 2026-05-07 after Cubs 5/7 lesson).
        # When confluence agrees with model AND favorite ML is heavily juiced
        # (≤-180), the ML EV becomes thin even at PRIME hit rate. Swap pick
        # to RL -1.5 which prices at +130-150 historically — better EV at
        # similar projected outcome when model projects 1.5+ run margin.
        home_ml_val = g.get('home_ml_close') or g.get('home_ml_open')
        away_ml_val = g.get('away_ml_close') or g.get('away_ml_open')
        fav_ml = home_ml_val if ps > 0 else away_ml_val
        pick_label = f"{fav_team} ML"
        sub_type = 'moneyline'
        rl_alt_signal = None
        if fav_ml is not None and abs_delta >= 1.5:
            try:
                fav_ml_i = int(fav_ml)
                if fav_ml_i <= -180:
                    pick_label = f"{fav_team} -1.5"
                    sub_type = 'runline'
                    rl_alt_signal = f"ML at {fav_ml_i:+d} too juiced — RL -1.5 better priced at projected +{abs_delta:.1f} margin"
            except (TypeError, ValueError):
                pass

        signals_list = [
            f"Signal confluence {tier} (net {confluence_net:+d})",
            f"{sig_str} all favor {fav_team.split()[-1]}",
            f"Spread delta {sd:+.1f} runs" if sd is not None else f"Model projects {fav_team} favored",
        ]
        if rl_alt_signal:
            signals_list.append(rl_alt_signal)

        candidates.append({
            'type': 'ML',
            'sub_type': sub_type,
            'matchup': f"{g.get('away_team')} @ {g.get('home_team')}",
            'game_id': g.get('game_id'),
            'pick': pick_label,
            'conviction': conviction,
            'tier': tier,
            'signals': signals_list,
            'odds_suggestion': -130,
            'raw_score': confluence_net,  # cohort key uses confluence_net to pick the bucket
        })

    # Total lean — v4 XGBoost model leans (2026-05-19 rewrite).
    # Was: v3 projected_total only, delta ≥3.0, over_lean=True gate, OVER-only.
    # Now: v4 model_pred_total primary (v3 fallback if suppressed), delta ≥1.5,
    # both directions. Reasoning: v4 shipped 5/18 at 62.4% direction acc;
    # excluding it from Daily Degen left the model's actual best output
    # invisible. v3 over_lean is gate-only (xERA gap rule) — too narrow to
    # rely on as the sole total-leg source.
    #
    # 2026-09-01 (user-reported divergence): DD used to ship OVER on games
    # where Jerry / ensemble read UNDER (v4 disagrees with the fuller stack).
    # Users saw "DD: Over 8.5 · Jerry: lean under" on the same game and
    # (rightly) called it a transparency bug. Fix: cross-check jerry_pred_total.
    # If v4 says over AND jerry says under (or vice-versa) by ≥0.5 runs,
    # SKIP the DD leg — the narrative disagreement is loud enough that
    # publishing the v4 lean alone reads as contradiction.
    for g in games:
        v4t = _f(g.get('model_pred_total'))
        v3t = _f(g.get('projected_total'))
        ct = _f(g.get('close_total'))
        jt = _f(g.get('jerry_pred_total'))
        if ct is None:
            continue
        # Prefer v4 when available; v3 fallback when v4 was suppressed (e.g.
        # ace-duel disagreement guard fires in game_context.py).
        pt = v4t if v4t is not None else v3t
        if pt is None:
            continue
        delta = pt - ct
        if abs(delta) < 1.5:
            continue
        direction = 'over' if delta > 0 else 'under'
        # Jerry cross-check: if Jerry's projected total is on the OPPOSITE
        # side of the close by ≥ 0.5 runs, model + narrator disagree — skip.
        if jt is not None:
            jerry_dir = 'over' if (jt - ct) > 0.5 else 'under' if (jt - ct) < -0.5 else None
            if jerry_dir and jerry_dir != direction:
                print(f"  ⚠️ Dropping TOTAL {direction} for {g.get('away_team')} @ {g.get('home_team')}: "
                      f"v4/v3={pt:.1f} says {direction} but jerry={jt:.1f} says {jerry_dir} (vs close {ct:.1f})")
                continue
        model_label = 'v4' if v4t is not None else 'v3'
        # Tier scales with magnitude. Conviction caps at 80; v4 has 62.4%
        # backtest direction but no live audit yet, so don't push higher.
        abs_d = abs(delta)
        if abs_d >= 2.5:
            tier = 'STRONG'
        elif abs_d >= 1.5:
            tier = 'LEAN'
        else:
            continue
        conviction = min(80, 55 + int(abs_d * 6))
        candidates.append({
            'type': 'TOTAL',
            'sub_type': direction,
            'matchup': f"{g.get('away_team')} @ {g.get('home_team')}",
            'game_id': g.get('game_id'),
            'pick': f"{'Over' if direction == 'over' else 'Under'} {ct}",
            'conviction': conviction,
            'tier': tier,
            'signals': [
                f"{model_label} model projects {pt:.1f} runs ({delta:+.2f} vs market {ct:.1f})",
                f"Park factor {g.get('park_run_factor')}",
            ],
            'odds_suggestion': -110,
        })

    return candidates


def select_diverse_legs(candidates, tier_rates=None):
    """Select 3-5 diverse legs — max 1 per game, type-aware caps.

    Audit-weighted (2026-05-04, rewritten 2026-05-19):
      - Each candidate's static conviction × (rate / BASELINE) folds in
        the live 30d cohort hit rate.
      - PROP candidates now get tier-aware rates from fetch_prop_tier_rates()
        instead of a flat 0.60 default. outs_under STRONG (~92%) and
        hits_over PRIME (~77%) now boost properly; lower-tier props demote.
      - Cohorts below SUPPRESS_RATE drop out entirely.

    Cap policy (relaxed 2026-05-19):
      - Max 3 PROPs total (was 2) — props are now the highest-audit pool
        and shouldn't be artificially capped.
      - Max 2 of any single PROP sub_type (no four hits_overs).
      - Max 2 non-PROP per type (NRFI / ML / TOTAL each).
    """
    from collections import defaultdict
    tier_rates = tier_rates or {}

    enriched = []
    suppressed_log = []
    for c in candidates:
        cohort = cohort_key_for(c, raw_score=c.get('raw_score'))
        rate = tier_rates.get(cohort) if cohort else None
        if rate is None:
            # Cohort missing (new prop type with no live data, or v4 total
            # before audit accumulates). Use the prior. v4_total_lean
            # specifically uses BASELINE_RATE so it's neutral until audited.
            rate = PROP_RATE_DEFAULT if (c.get('type') == 'PROP') else BASELINE_RATE
        if rate < SUPPRESS_RATE:
            suppressed_log.append((c.get('pick'), cohort, rate))
            continue
        adjusted = c['conviction'] * (rate / BASELINE_RATE)
        c2 = dict(c)
        c2['_audit_rate'] = round(rate, 3)
        c2['_adjusted_conviction'] = round(adjusted, 1)
        c2['_cohort'] = cohort
        enriched.append(c2)

    if suppressed_log:
        print(f"  🛑 Audit-suppressed {len(suppressed_log)} candidate(s) below {SUPPRESS_RATE:.0%}:")
        for pick, cohort, rate in suppressed_log[:5]:
            print(f"     - {pick} (cohort {cohort} @ {rate:.1%})")

    # Sort by audit-adjusted conviction desc
    enriched.sort(key=lambda c: c['_adjusted_conviction'], reverse=True)

    selected = []
    games_used = set()
    type_counts = {'PROP': 0, 'NRFI': 0, 'ML': 0, 'TOTAL': 0}
    sub_type_counts = defaultdict(int)

    for c in enriched:
        if len(selected) >= TARGET_LEGS:
            break
        # No same-game correlation
        if c['game_id'] in games_used:
            continue
        ctype = c['type']
        if ctype == 'PROP':
            if type_counts['PROP'] >= 3:
                continue
            # Don't pile up 3 hits_overs from different games — still some
            # cross-game correlation (slate-wide offense factors).
            if sub_type_counts[c.get('sub_type')] >= 2:
                continue
        else:
            if type_counts.get(ctype, 0) >= 2:
                continue
        selected.append(c)
        games_used.add(c['game_id'])
        type_counts[ctype] = type_counts.get(ctype, 0) + 1
        sub_type_counts[c.get('sub_type')] += 1

    return selected


def build_narrative(legs):
    """Generate a single 2-3 sentence Jerry narrative. One Haiku call per day.

    2026-07-26 fix: audit found ~half the recent daily_degen narratives were
    falling back to the boilerplate ("Model found edges across the slate.")
    because the 10s timeout was too tight for Haiku long-tail latency + no
    retry existed. Bumped timeout to 25s + added one retry with 2s backoff.
    Also surfaces the specific failure mode (timeout vs API error vs empty
    response) so silent regression is easier to catch next time.
    """
    if not ANTHROPIC_API_KEY:
        print("  (no ANTHROPIC_API_KEY in env — using default narrative)")
        return "Model found edges across the slate. That's the Degen Parlay."

    legs_desc = "\n".join(
        f"Leg {i+1}: {l['pick']} ({l['matchup']}) — {l['signals'][0] if l.get('signals') else ''}"
        for i, l in enumerate(legs)
    )
    prompt = f"""You are Jerry — sharp, energetic, slightly degenerate but always analytically grounded. Write the narrative for today's Degen Parlay.

Legs:
{legs_desc}

Write 2-3 sentences MAX. Reference specific data signals. Sound like a sharp friend who found edges today. End naturally — something like "That's the Degen Parlay." or "Jerry's riding all of these." Never say "bet" or "must play". High energy but data-backed. NEVER start with "Let me" or "Looking at" or any preamble — jump straight in."""

    payload = {
        'model': 'claude-haiku-4-5-20251001',
        'max_tokens': 240,
        'messages': [{'role': 'user', 'content': prompt}],
    }
    headers = {
        'Content-Type': 'application/json',
        'x-api-key': ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
    }

    import time
    last_err = None
    for attempt in (1, 2):
        try:
            print(f"  Calling Haiku for narrative (25s timeout, attempt {attempt}/2)...")
            r = requests.post(
                'https://api.anthropic.com/v1/messages',
                headers=headers, json=payload, timeout=25,
            )
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                print(f"  ⚠ narrative attempt {attempt} — {last_err}")
                if attempt == 1: time.sleep(2)
                continue
            data = r.json()
            text = ''.join(
                b.get('text', '') for b in (data.get('content') or [])
                if b.get('type') == 'text'
            ).strip()
            if text:
                return text
            last_err = 'empty response body'
        except requests.exceptions.Timeout:
            last_err = 'timeout (25s)'
            print(f"  ⚠ narrative attempt {attempt} — {last_err}")
            if attempt == 1: time.sleep(2)
        except Exception as e:
            last_err = f'{type(e).__name__}: {e}'
            print(f"  ⚠ narrative attempt {attempt} — {last_err}")
            if attempt == 1: time.sleep(2)

    print(f"  ⚠ narrative generation failed after 2 attempts: {last_err}")
    return "Model found edges across the slate. That's the Degen Parlay."


def upsert_daily_degen(game_date, legs, narrative):
    # Normalize each leg so the app gets a canonical `odds` field.
    # Candidates carry `odds_suggestion` (named for back-compat); the app
    # reads `leg.odds` and a None there silently corrupts the parlay math
    # downstream — americanToDecimal returns 1 on NaN, then decimalToAmerican
    # hits -100/0 = -Infinity. 6/9 incident: every leg shipped with odds=None,
    # making the "Add all to parlay" CTA produce -Infinity American odds.
    normalized_legs = []
    for leg in legs:
        leg = dict(leg)
        if leg.get('odds') is None:
            leg['odds'] = leg.get('odds_suggestion')
        normalized_legs.append(leg)
    avg_conv = round(sum(l['conviction'] for l in normalized_legs) / len(normalized_legs), 1) if normalized_legs else None
    payload = {
        'game_date': game_date,
        'legs': normalized_legs,
        'narrative': narrative,
        'leg_count': len(normalized_legs),
        'avg_conviction': avg_conv,
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/daily_degen?on_conflict=game_date",
        headers=HEADERS,
        json=payload,
        timeout=15
    )
    if r.status_code not in (200, 201, 204):
        print(f"  ⚠️ upsert failed {r.status_code}: {r.text[:300]}")
        return False
    return True


def run():
    gd = today_et()
    print(f"=== Daily Degen {gd} ===")

    # No overwrite guard — each cron regenerates to incorporate latest
    # pipeline props + live game state. Afternoon run with confirmed
    # lineups produces a stronger parlay than morning run's K-only pool.

    games = fetch_todays_games()
    props = fetch_pipeline_props()
    tier_rates = fetch_tier_rates()
    # Merge in live per-(prop_type, tier) rates from mlb_pipeline_props.
    # These aren't in mlb_tier_calibration yet — pulled directly from the
    # source-of-truth result column. Keyed `prop_{type}_{tier}` so the
    # cohort_key_for() PROP branch finds them.
    prop_rates = fetch_prop_tier_rates(days_back=30, min_n=10)
    tier_rates.update(prop_rates)
    print(f"  Source pool: {len(games)} games, {len(props)} pipeline props, "
          f"{len(tier_rates) - len(prop_rates)} calibrated cohorts + {len(prop_rates)} live prop cohorts")

    candidates = extract_leg_candidates(games, props)
    print(f"  Candidate legs before selection: {len(candidates)}")

    # 2026-08-19: primary_play conflict filter. Drop legs that contradict
    # a STRONG+ ensemble_v2 primary_play in the same game. Prevents Ledger
    # parlay from including "Diamondbacks +1.5" when Sharp Card has locked
    # in "Boston Red Sox ML STRONG" for the same matchup.
    try:
        from primary_play_gate import primary_play_conflicts
        games_by_id = {g.get('game_id'): g for g in games if g.get('game_id')}
        pre_ct = len(candidates)
        clean = []
        for c in candidates:
            gid = c.get('game_id')
            g = games_by_id.get(gid)
            # Map DD candidate types to markets recognized by the gate
            ctype = (c.get('type') or '').lower()
            sub = (c.get('sub_type') or '').lower()
            if ctype == 'prop':
                # Props aren't a side/total pick — pass through.
                clean.append(c); continue
            if ctype == 'ml':
                market = 'rl' if sub == 'runline' else 'ml'
            elif ctype == 'total':
                market = 'over' if sub == 'over' else 'under' if sub == 'under' else 'total'
            elif ctype in ('nrfi',):
                # NRFI/YRFI live outside standard side/total taxonomy.
                clean.append(c); continue
            else:
                clean.append(c); continue
            side_label = c.get('pick') or ''
            conflict = primary_play_conflicts(market, side_label, g or {})
            if conflict:
                print(f"  ⚠️ Dropping {c['type']}: {c['pick']} — {conflict}")
                continue
            clean.append(c)
        candidates = clean
        if pre_ct != len(candidates):
            print(f"  primary_play filter: {pre_ct} → {len(candidates)}")
    except ImportError:
        pass

    legs = select_diverse_legs(candidates, tier_rates=tier_rates)

    if len(legs) < MIN_LEGS:
        print(f"  ⚠️ Only {len(legs)} legs found — not enough for a Degen Parlay today")
        return

    print(f"\n✅ Selected {len(legs)} legs:")
    for l in legs:
        rate = l.get('_audit_rate')
        adj = l.get('_adjusted_conviction')
        rate_str = f" | audit {rate:.0%} → adj {adj:.0f}" if rate is not None else ""
        print(f"  [{l['conviction']}] {l['type']}: {l['pick']} — {l['matchup']}{rate_str}")
        for s in l.get('signals', [])[:2]:
            print(f"      · {s}")

    narrative = build_narrative(legs)
    print(f"\n  Narrative: {narrative[:150]}...")

    if upsert_daily_degen(gd, legs, narrative):
        print(f"\n✅ Daily Degen stored for {gd}")


if __name__ == "__main__":
    run()
