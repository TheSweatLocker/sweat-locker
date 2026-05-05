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

TARGET_LEGS = 4  # 4 legs = juicy parlay without being ridiculous
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


def fetch_tier_rates():
    """Return {tier_name: hit_rate} from latest 30d window in
    mlb_tier_calibration. Falls back to empty dict on error so the rest
    of the Degen still runs (just skips audit weighting)."""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_tier_calibration"
            f"?window_label=eq.30d&select=tier,hit_rate,computed_date,total"
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


def cohort_key_for(candidate, raw_score=None):
    """Map a Degen candidate to its audit cohort key. Returns None when
    no calibrated cohort applies (props use a default rate)."""
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
        if net >= 4:  return 'confluence_prime_ge4'
        if net >= 2:  return 'confluence_strong_2_3'
        if net == 1:  return 'confluence_lean_1'
        return 'confluence_zero'
    if ctype == 'TOTAL':
        # No total cohort calibrated yet — treat as spread_delta-like.
        # Use spread_delta_ge2 as a reasonable proxy (totals model edges
        # behave similarly to spread edges audit-wise).
        return 'spread_delta_ge2'
    return None  # PROPs use PROP_RATE_DEFAULT


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
        if ptype == 'hits_over':
            prop_label = 'Over 0.5 Hits'
        elif ptype == 'hits_under':
            prop_label = 'Under 0.5 Hits'
        elif ptype == 'ks_over':
            prop_label = f"Over {line} Strikeouts"
        elif ptype == 'ks_under':
            prop_label = f"Under {line} Strikeouts"
        else:
            prop_label = f"{ptype} {line}"  # fallback for unknown types
        candidates.append({
            'type': 'PROP',
            'sub_type': p.get('prop_type'),
            'matchup': p.get('matchup'),
            'game_id': p.get('game_id'),
            'pick': f"{p.get('player_name')} — {prop_label}",
            'conviction': p.get('conviction'),
            'tier': p.get('tier'),
            'signals': list((p.get('signals') or {}).values())[:3],
            'odds_suggestion': -150,  # typical prop range, app can overlay posted odds
        })

    # Emit NRFI/YRFI candidates broadly — let the audit weighting decide
    # which cohorts survive. Old behavior was to pre-gate at 90-94 only,
    # which silently hid YRFI ≤40 (68.6% audited) and mild-lean 70-79
    # (57% audited) signals on nights when no PRIME-band score existed.
    seen_nrfi_games = set()
    for g in games:
        nrfi = g.get('nrfi_score')
        gid = g.get('game_id')
        if nrfi is None or gid in seen_nrfi_games:
            continue
        # Categorize and pick a static conviction; cohort_key_for + audit
        # weighting will rerank these accordingly.
        if 90 <= nrfi <= 94:
            pick_label, conv, tier_label = 'NRFI (No Run First Inning)', 72, 'PRIME'
            sig_label = f"NRFI Score {nrfi} — PRIME band (90-94)"
        elif nrfi >= 95:
            pick_label, conv, tier_label = 'NRFI (No Run First Inning)', 65, 'LEAN'
            sig_label = f"NRFI Score {nrfi} — volatile band (95+)"
        elif 70 <= nrfi <= 79:
            pick_label, conv, tier_label = 'NRFI (No Run First Inning)', 60, 'LEAN'
            sig_label = f"NRFI Score {nrfi} — mild lean (70-79)"
        elif nrfi <= 40:
            pick_label, conv, tier_label = 'YRFI (Run in First)', 70, 'STRONG'
            sig_label = f"NRFI Score {nrfi} — YRFI lean (≤40)"
        else:
            continue  # 41-69 and 80-89 are audited dead zones
        seen_nrfi_games.add(gid)
        candidates.append({
            'type': 'NRFI',
            'sub_type': 'yrfi' if pick_label.startswith('YRFI') else 'nrfi',
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
            'raw_score': nrfi,  # cohort_key_for uses this to pick the right tier rate
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
        tier = 'PRIME' if confluence_net >= 4 else 'STRONG' if confluence_net >= 2 else 'LEAN'
        conviction = min(90, 60 + confluence_net * 6 + min(int(abs_delta * 3), 12))
        breakdown = g.get('signal_confluence_breakdown') or {}
        sig_str = ', '.join(f"{k}" for k in breakdown.keys()) or 'multiple signals'
        candidates.append({
            'type': 'ML',
            'sub_type': 'moneyline',
            'matchup': f"{g.get('away_team')} @ {g.get('home_team')}",
            'game_id': g.get('game_id'),
            'pick': f"{fav_team} ML",
            'conviction': conviction,
            'tier': tier,
            'signals': [
                f"Signal confluence {tier} (net {confluence_net:+d})",
                f"{sig_str} all favor {fav_team.split()[-1]}",
                f"Spread delta {sd:+.1f} runs" if sd is not None else f"Model projects {fav_team} favored",
            ],
            'odds_suggestion': -130,
            'raw_score': confluence_net,  # cohort key uses confluence_net to pick the bucket
        })

    # Total lean — over only (per NRFI audit memory: over leans active, under disabled)
    for g in games:
        pt = _f(g.get('projected_total'))
        ct = _f(g.get('close_total'))
        if pt is None or ct is None:
            continue
        delta = pt - ct
        if delta >= 3.0 and g.get('over_lean') is True:
            candidates.append({
                'type': 'TOTAL',
                'sub_type': 'over',
                'matchup': f"{g.get('away_team')} @ {g.get('home_team')}",
                'game_id': g.get('game_id'),
                'pick': f"Over {ct}",
                'conviction': min(80, 55 + int(delta * 4)),
                'tier': 'STRONG' if delta >= 4 else 'LEAN',
                'signals': [
                    f"Model projects {pt:.1f} runs — {delta:.1f} run gap vs posted",
                    f"Park factor {g.get('park_run_factor')}",
                ],
                'odds_suggestion': -110,
            })

    return candidates


def select_diverse_legs(candidates, tier_rates=None):
    """Select 3-5 diverse legs — max 1 per game, max 2 per type.

    Audit-weighted (added 2026-05-04): each candidate's static conviction
    is multiplied by (live_30d_hit_rate / BASELINE_RATE) to fold in rolling
    cohort calibration. Cohorts hitting below SUPPRESS_RATE are dropped
    entirely (e.g. confluence_prime_ge4 at 27% historical → suppressed).
    Cohorts hitting above baseline rise; below baseline get demoted.
    Props get a static PROP_RATE_DEFAULT prior since they have no
    calibrated cohort yet.
    """
    tier_rates = tier_rates or {}

    enriched = []
    suppressed_log = []
    for c in candidates:
        cohort = cohort_key_for(c, raw_score=c.get('raw_score'))
        rate = tier_rates.get(cohort) if cohort else None
        if rate is None:
            # Props (no cohort) and any tier we don't have data for use the prior.
            rate = PROP_RATE_DEFAULT
        if rate < SUPPRESS_RATE:
            suppressed_log.append((c.get('pick'), cohort, rate))
            continue
        adjusted = c['conviction'] * (rate / BASELINE_RATE)
        c2 = dict(c)
        c2['_audit_rate'] = round(rate, 3)
        c2['_adjusted_conviction'] = round(adjusted, 1)
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

    for c in enriched:
        if len(selected) >= TARGET_LEGS:
            break
        # No same-game correlation (except props can coexist with NRFI in edge cases — still skip for cleanliness)
        if c['game_id'] in games_used:
            continue
        # Max 2 per type — don't build an all-props parlay
        if type_counts.get(c['type'], 0) >= 2:
            continue
        selected.append(c)
        games_used.add(c['game_id'])
        type_counts[c['type']] = type_counts.get(c['type'], 0) + 1

    return selected


def build_narrative(legs):
    """Generate a single 2-3 sentence Jerry narrative. One Haiku call per day."""
    if not ANTHROPIC_API_KEY:
        print("  (no ANTHROPIC_API_KEY in env — using default narrative)")
        return "Model found edges across the slate. That's the Degen Parlay."
    print("  Calling Haiku for narrative (10s timeout)...")

    legs_desc = "\n".join(
        f"Leg {i+1}: {l['pick']} ({l['matchup']}) — {l['signals'][0] if l.get('signals') else ''}"
        for i, l in enumerate(legs)
    )
    prompt = f"""You are Jerry — sharp, energetic, slightly degenerate but always analytically grounded. Write the narrative for today's Degen Parlay.

Legs:
{legs_desc}

Write 2-3 sentences MAX. Reference specific data signals. Sound like a sharp friend who found edges today. End naturally — something like "That's the Degen Parlay." or "Jerry's riding all of these." Never say "bet" or "must play". High energy but data-backed. NEVER start with "Let me" or "Looking at" or any preamble — jump straight in."""

    try:
        r = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'Content-Type': 'application/json',
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
            },
            json={
                'model': 'claude-haiku-4-5-20251001',
                'max_tokens': 240,
                'messages': [{'role': 'user', 'content': prompt}]
            },
            timeout=10
        )
        data = r.json()
        text = ''.join(
            b.get('text', '') for b in (data.get('content') or [])
            if b.get('type') == 'text'
        )
        return text.strip() or "Model found edges across the slate. That's the Degen Parlay."
    except Exception as e:
        print(f"  ⚠️ narrative generation failed: {e}")
        return "Model found edges across the slate. That's the Degen Parlay."


def upsert_daily_degen(game_date, legs, narrative):
    avg_conv = round(sum(l['conviction'] for l in legs) / len(legs), 1) if legs else None
    payload = {
        'game_date': game_date,
        'legs': legs,
        'narrative': narrative,
        'leg_count': len(legs),
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
    print(f"  Source pool: {len(games)} games, {len(props)} pipeline props, {len(tier_rates)} calibrated cohorts")

    candidates = extract_leg_candidates(games, props)
    print(f"  Candidate legs before selection: {len(candidates)}")

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
