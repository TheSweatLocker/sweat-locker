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


def today_et():
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    return et.strftime('%Y-%m-%d')


def _f(v):
    try: return float(v)
    except: return None


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
        prop_label = 'Over 0.5 Hits' if p.get('prop_type') == 'hits_over' else f"Over {p.get('prop_line')} Strikeouts"
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

    # NRFI PRIME tier (90-94 only — per tier calibration, 95+ is volatile trap)
    for g in games:
        nrfi = g.get('nrfi_score')
        if nrfi is None:
            continue
        if 90 <= nrfi <= 94:
            candidates.append({
                'type': 'NRFI',
                'sub_type': 'nrfi',
                'matchup': f"{g.get('away_team')} @ {g.get('home_team')}",
                'game_id': g.get('game_id'),
                'pick': 'NRFI (No Run First Inning)',
                'conviction': 72,  # PRIME tier constant for NRFI — 90-94 range
                'tier': 'STRONG',
                'signals': [
                    f"NRFI Score {nrfi} — PRIME tier",
                    f"{g.get('home_pitcher')} xERA {g.get('home_sp_xera')}" if g.get('home_sp_xera') else f"{g.get('home_pitcher')}",
                    f"{g.get('away_pitcher')} xERA {g.get('away_sp_xera')}" if g.get('away_sp_xera') else f"{g.get('away_pitcher')}",
                ],
                'odds_suggestion': -130,
            })

    # ML spread delta ≥ 1.0 (corrected) — retuned 2026-04-24 from 3.0 after sign-bug fix.
    # Require BOTH starters to have xERA — skips games where projected_spread
    # came from 'no pitcher data' fallback (creates artifact-driven huge deltas).
    for g in games:
        sd = _f(g.get('spread_delta'))
        home_xera = _f(g.get('home_sp_xera'))
        away_xera = _f(g.get('away_sp_xera'))
        if sd is None or home_xera is None or away_xera is None:
            continue
        if abs(sd) >= 1.0:
            fav_team = g.get('home_team') if sd > 0 else g.get('away_team')
            # Conviction scaled to corrected magnitudes: 1.0 = base 60, 1.5 = +8, 2.0 = +16, cap at 85
            conviction = min(85, 60 + int(abs(sd) * 15))
            tier = 'STRONG' if abs(sd) >= 1.5 else 'LEAN'
            candidates.append({
                'type': 'ML',
                'sub_type': 'moneyline',
                'matchup': f"{g.get('away_team')} @ {g.get('home_team')}",
                'game_id': g.get('game_id'),
                'pick': f"{fav_team} ML",
                'conviction': conviction,
                'tier': tier,
                'signals': [
                    f"Spread delta {sd:+.1f} runs vs market — {tier}",
                    f"Model projects {fav_team} favored",
                ],
                'odds_suggestion': -130,
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


def select_diverse_legs(candidates):
    """Select 3-5 diverse legs — max 1 per game, max 2 per type."""
    # Sort by conviction desc
    candidates.sort(key=lambda c: c['conviction'], reverse=True)
    selected = []
    games_used = set()
    type_counts = {'PROP': 0, 'NRFI': 0, 'ML': 0, 'TOTAL': 0}

    for c in candidates:
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
    print(f"  Source pool: {len(games)} games, {len(props)} pipeline props")

    candidates = extract_leg_candidates(games, props)
    print(f"  Candidate legs before selection: {len(candidates)}")

    legs = select_diverse_legs(candidates)

    if len(legs) < MIN_LEGS:
        print(f"  ⚠️ Only {len(legs)} legs found — not enough for a Degen Parlay today")
        return

    print(f"\n✅ Selected {len(legs)} legs:")
    for l in legs:
        print(f"  [{l['conviction']}] {l['type']}: {l['pick']} — {l['matchup']}")
        for s in l.get('signals', [])[:2]:
            print(f"      · {s}")

    narrative = build_narrative(legs)
    print(f"\n  Narrative: {narrative[:150]}...")

    if upsert_daily_degen(gd, legs, narrative):
        print(f"\n✅ Daily Degen stored for {gd}")


if __name__ == "__main__":
    run()
