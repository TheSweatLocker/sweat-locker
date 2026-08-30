"""Jerry-as-synthesizer (Tier 2 · 2026-07-31).

Companion to generate_mlb_game_reads.py. Same underlying context struct
(reuses build_struct from that script) but ADDS external picks + full
model dump, prompts Claude for a two-part analytical synthesis (short
40-60w preview + long 200-300w analysis), and writes to the NEW
jerry_reads table so the app can rank/track/grade.

Difference from the legacy jerry_cache pipeline:
    generate_mlb_game_reads.py → jerry_cache (narrative + struct panel)
    generate_jerry_synthesis.py → jerry_reads (short + long + parseable
                                                directional call + conviction)

Both run nightly. jerry_cache stays alive for the "Numbers panel" render
which still works well. jerry_reads becomes the headline product.

Run order (in mlb_pipeline.yml cron):
    1. game_context + externals + resolve primary_play (existing)
    2. generate_mlb_game_reads.py (existing)
    3. generate_jerry_synthesis.py (this file)  ← NEW

Usage:
    python generate_jerry_synthesis.py [--force] [--date YYYY-MM-DD] [--limit N]
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

# Reuse the existing build_struct + helpers from the legacy generator
# so Jerry v2 sees the same rich context — no data reassembly.
from generate_mlb_game_reads import (
    build_struct,
    fetch_games,
    fetch_props_by_game,
    fetch_potd,
    _matches,
)

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

SB_READ = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
SB_WRITE = {**SB_READ, "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal"}

MODEL = "claude-haiku-4-5-20251001"
PROMPT_VERSION = "synthesis_v1"

# Sport-universal registry (2026-07-31 · Tabletop C).
# Adding a new sport = 1 line + confirming that sport's game_context table
# follows the same shape as mlb_game_context. NHL/UFC deferred by a couple
# days per user; NBA/NFL/NCAAF/NCAAB rows here become active when their
# feature_flags(sport, 'jerry_synthesis').enabled=true.
SPORT_REGISTRY: dict = {
    'MLB':  {'context_table': 'mlb_game_context',   'prompt_sport': 'MLB',   'active': True},
    'NBA':  {'context_table': 'nba_game_context',   'prompt_sport': 'NBA',   'active': False},
    'NFL':  {'context_table': 'nfl_game_context',   'prompt_sport': 'NFL',   'active': False},
    'NCAAF':{'context_table': 'ncaaf_game_context', 'prompt_sport': 'NCAAF', 'active': False},
    'NCAAB':{'context_table': 'ncaab_game_context', 'prompt_sport': 'NCAAB', 'active': False},
}


# ─── Data helpers ────────────────────────────────────────────────────────
def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def fetch_externals_for_game(game_id: str, game_date: str) -> list:
    """Every external_picks row for this game/date across all sources."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/external_picks",
        headers=SB_READ,
        params={
            "game_id": f"eq.{game_id}",
            "game_date": f"eq.{game_date}",
            "select": "source,surface,pick_side,pick_line,odds_american,confidence,raw_text,fade_flag",
            "order": "source.asc",
        },
        timeout=15,
    )
    return r.json() if r.status_code == 200 else []


def fetch_source_track_records() -> dict:
    """Load external_source_track_record — Jerry cites source W/L when
    weighing external opinions. Returns {source: {sport: {surface: {n, wins, losses, hit_rate}}}}."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/external_source_track_record",
        headers=SB_READ,
        params={"window_days": "eq.30", "select": "*"},
        timeout=15,
    )
    out: dict = {}
    for row in (r.json() if r.status_code == 200 else []):
        (out.setdefault(row["source"], {})
            .setdefault(row["sport"], {})
            [row["surface"]]) = {
                "n": row["n_picks"], "wins": row["n_wins"],
                "losses": row["n_losses"], "hit_rate": row["hit_rate"],
            }
    return out


def enrich_struct(struct: dict, game: dict, externals: list,
                  source_records: dict,
                  totals_cohort_stats: dict | None = None) -> dict:
    """Add the fields Jerry v2 needs on top of the legacy struct.

    Adds:
      externals: [{source, surface, side, line, odds, confidence, raw_text,
                   source_30d_hit_rate, source_30d_n}]
      full_models: every internal projection in one place — panel, MC, v4,
                   Jerry pred, cohorts v1+v2, mc_hc flags, align_status
      totals_cohorts: [{cohort, direction, pct, n, description}] — backtested
                   contextual signals firing for THIS game (E-4, 2026-08-01).
                   Populated when totals_cohort_stats dict passed in.
      align_status: mirrored to top level for prompt convenience
      money_flow: oddscrowd snapshot at top level
    """
    struct = dict(struct)

    # Totals cohort attribution — pulls firing signals from totals_cohort_signals
    # backtest so Jerry cites concrete historical hit rates on totals reads.
    if totals_cohort_stats is not None:
        try:
            from totals_cohort_attribution import attribute
            struct["totals_cohorts"] = attribute(game, totals_cohort_stats, sport='MLB')
        except Exception as e:
            struct["totals_cohorts"] = []
            print(f"  ⚠ totals cohort attribution failed: {e}")

    ext_rows = []
    for e in externals:
        src = e.get("source")
        sport_rec = source_records.get(src, {}).get("MLB", {})
        surface_rec = sport_rec.get(e.get("surface"), {}) or sport_rec.get("overall", {})
        ext_rows.append({
            "source": src,
            "surface": e.get("surface"),
            "side": e.get("pick_side"),
            "line": e.get("pick_line"),
            "odds": e.get("odds_american"),
            "confidence": e.get("confidence"),
            "raw_text": e.get("raw_text"),
            "source_30d_hit_rate": surface_rec.get("hit_rate"),
            "source_30d_n": surface_rec.get("n"),
        })
    struct["externals"] = ext_rows

    struct["full_models"] = {
        "primary_play": game.get("primary_play"),
        "jerry": {
            "pred_home_runs": game.get("jerry_pred_home_runs"),
            "pred_away_runs": game.get("jerry_pred_away_runs"),
            "pred_spread": game.get("jerry_pred_spread"),
            "pred_total": game.get("jerry_pred_total"),
        },
        "model_v4": {
            "pred_home_runs": game.get("model_pred_home_runs"),
            "pred_away_runs": game.get("model_pred_away_runs"),
            "pred_spread": game.get("model_pred_spread"),
            "pred_total": game.get("model_pred_total"),
        },
        "monte_carlo": {
            "probs": game.get("mc_probabilities"),
            "hc_flag": game.get("mc_high_conf_flag"),
            "hc_side": game.get("mc_high_conf_side"),
            "hc_pct": game.get("mc_high_conf_pct"),
        },
        "confluence": {
            "net": game.get("signal_confluence_net"),
            "v2_net": game.get("signal_confluence_v2_net"),
            "breakdown": game.get("signal_confluence_breakdown"),
            "v2_breakdown": game.get("signal_confluence_v2_breakdown"),
        },
        "panel": {
            "implied_total": game.get("panel_implied_total"),
            "implied_margin": game.get("panel_implied_margin"),
        },
        "sweat_tier": game.get("sweat_tier"),  # DB still populates; internal only
        "sweat_score": game.get("sweat_score"),
    }
    struct["align_status"] = game.get("align_status")
    struct["money_flow"] = game.get("oddscrowd_snapshot")

    # UMPIRE PRIOR (2026-08-06): the pipeline computes umpire O/U bias into
    # game.umpire_note ("Dan Bellino — hitter-friendly zone, 58% over rate")
    # but Jerry never saw it before. Wire it in as a first-class signal
    # alongside park factor + weather. Umps with 55%+ or 45%- over rate at
    # meaningful sample are a real edge nobody in the tools space surfaces.
    struct["umpire"] = {
        "name": game.get("umpire"),
        "note": game.get("umpire_note"),
    }

    # WEATHER × PARK INTERACTION (2026-08-06): park factor + weather are
    # both in the struct separately, but the INTERACTION is what actually
    # matters. High wind at Coors (park 118) ≠ high wind at Petco (park 92).
    # Compute a compact interaction signal Jerry can reason over.
    park_pf = game.get('park_run_factor')
    wind = game.get('wind_speed') or game.get('wind_mph')
    wind_dir = game.get('wind_direction')
    temp = game.get('temperature')
    wind_blowing_in = game.get('wind_blowing_in')
    interaction_notes = []
    try:
        pf = float(park_pf) if park_pf is not None else None
        w = float(wind) if wind is not None else None
        t = float(temp) if temp is not None else None
    except (TypeError, ValueError):
        pf = w = t = None

    if pf is not None and w is not None:
        if pf >= 108 and w >= 12 and (wind_blowing_in is False or wind_dir in ('SW', 'W', 'S', 'SE')):
            interaction_notes.append(f'HITTER PARK ({pf}) + WIND OUT ({w}mph {wind_dir}) — OVER amplifier')
        elif pf <= 95 and w >= 12 and (wind_blowing_in is True or wind_dir in ('N', 'NE', 'NW', 'E')):
            interaction_notes.append(f'PITCHER PARK ({pf}) + WIND IN ({w}mph {wind_dir}) — UNDER amplifier')
        elif pf >= 108 and w >= 15 and wind_blowing_in is True:
            interaction_notes.append(f'HITTER PARK ({pf}) BUT WIND IN ({w}mph) — neutralized')
    if t is not None and pf is not None:
        if t <= 50 and pf <= 100:
            interaction_notes.append(f'COLD ({t}F) + non-hitter park — UNDER lean (cold suppresses ball flight)')
        elif t >= 90 and pf >= 105:
            interaction_notes.append(f'HOT ({t}F) + hitter park — OVER amplifier (ball travels)')

    struct["park_weather"] = {
        "park_factor": park_pf,
        "wind_mph": wind, "wind_direction": wind_dir, "wind_blowing_in": wind_blowing_in,
        "temperature": temp,
        "interaction": interaction_notes,  # empty list if no notable interaction
    }

    # SHARP FADE CONTEXT (2026-08-09): compute fade-rule fires for the LIKELY
    # Jerry pick side so the prompt can see if any ACTIVE rules would cap
    # this direction. When 1+ ACTIVE rules fire on Jerry's leaning direction,
    # Jerry has three options: (a) flip the pick to the opposite side, (b)
    # keep pick but downgrade conviction, (c) explain why the fade doesn't
    # apply here despite the pattern. Whatever she picks gets capped
    # mechanically by tier_discipline_gate anyway — this just gives her
    # narrative awareness of the constraint.
    try:
        from sharp_fade_rules import compute_fade_context as _fade_ctx
        # Try both sides for each market so Jerry sees which direction is safe
        # and which would trigger caps.
        fade_ctx_view = {}
        for mkt in ('ml', 'total'):
            fade_ctx_view[mkt] = {}
            for side in (('HOME','AWAY') if mkt == 'ml' else ('OVER','UNDER')):
                try:
                    r = _fade_ctx(game, mkt, side)
                except Exception:
                    r = None
                if r and (r.get('triggers') or r.get('active_count', 0) > 0):
                    fade_ctx_view[mkt][side] = {
                        'active_rule_count': r.get('active_count', 0),
                        'cap_directive': r.get('cap_directive'),
                        'triggers': [{'rule': t['rule'], 'mode': t.get('mode'),
                                       'reason': t.get('reason','')[:120]}
                                       for t in r['triggers']],
                    }
        # Only surface the block if any triggers fired anywhere; otherwise noise
        has_signal = any(fade_ctx_view[m] for m in fade_ctx_view)
        if has_signal:
            struct["sharp_fade_context"] = fade_ctx_view
    except Exception:
        pass

    return struct


# ─── Prompt + LLM call ───────────────────────────────────────────────────
def load_synthesis_prompt(sport: str = 'MLB') -> str | None:
    """Fetch the jerry_synthesis/{sport} prompt template. Falls back to
    jerry_synthesis/ALL when no per-sport template exists (sport-universal
    prompt). Per-sport templates preferred so voice can be tuned."""
    for candidate_sport in [sport, 'ALL']:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/prompt_templates",
            headers=SB_READ,
            params={"name": "eq.jerry_synthesis", "sport": f"eq.{candidate_sport}",
                    "is_active": "eq.true", "select": "template"},
            timeout=15,
        )
        rows = r.json() if r.status_code == 200 else []
        if rows: return rows[0]["template"]
    return None


def render_prompt(template: str, game: dict, struct: dict) -> str:
    """Substitute {STRUCT} in the template. Template does the framing.

    Pitcher names surfaced as top-level template vars so the guardrail rule
    against pitcher-team misattribution (2026-08-02 · Cole/Yankees hallucination)
    can reference them directly instead of forcing Jerry to dig them out of
    the struct JSON.

    2026-08-12: injects sharp scenario matches for this game so Jerry SEES
    which historical public/sharp patterns are firing. Adds {SHARP_SCENARIOS}
    block to struct. Falls back to empty string if lookup fails.
    """
    # 2026-08-12: inject sharp scenario matches
    try:
        from sharp_scenario_lookup import matches_for_game, format_for_prompt
        matches = matches_for_game(game.get('game_id'), game.get('game_date'))
        sharp_block = format_for_prompt(matches)
    except Exception:
        sharp_block = ''
    if sharp_block:
        struct = dict(struct)  # avoid mutating caller
        struct['_sharp_scenario_matches'] = sharp_block

    # 2026-08-16: inject signal_registry (Playbook) as evidence Jerry can
    # cite by name. Bundle G — closes the loop between the Playbook doc
    # and the actual synthesis prompt. Filters to MLB-scope signals plus
    # universal ones (sport='*'), sorted by tier + hit_rate.
    try:
        from signal_registry_lookup import signals_for_scope, format_for_prompt as _reg_fmt
        sport_hint = (game.get('sport') or 'MLB').upper()
        # Pull all tiers so Jerry sees VALIDATED, DISCOVERY, AND ANTI_VALIDATED
        # — the ANTI ones tell Jerry what NOT to lean on as primary basis.
        reg_entries = signals_for_scope(sport=sport_hint, min_tier='ANTI_VALIDATED')
        registry_block = _reg_fmt(reg_entries)
    except Exception:
        registry_block = ''

    # 2026-08-16 CUTOVER: inject ensemble pick + supporting-signal prose.
    # ensemble_scorer v2 is now the DECISION authority. Jerry narrates.
    # If primary_play._engine == 'ensemble_v2', pass the pick + top
    # contributions into the prompt so Jerry writes prose about THIS pick
    # instead of re-deciding via the LLM.
    ensemble_block = ''
    try:
        # 2026-08-22 same nested-path bug as defer_call_to_ensemble — the
        # ENSEMBLE DECISION block never rendered because primary_play is
        # at struct['full_models']['primary_play'], not top-level. Now
        # the LLM actually receives the ensemble pick as narrator input
        # instead of silently falling back to handicapper mode.
        pp = _extract_primary_play(struct)
        if pp is not None and pp.get('_engine') == 'ensemble_v2':
            label = pp.get('label') or '?'
            tier = pp.get('tier') or 'LEAN'
            conv = pp.get('conviction')
            score = pp.get('score')
            all_markets = pp.get('_ensemble_all_markets') or {}
            sources = pp.get('_ensemble_sources') or []
            lines = [
                f'ENSEMBLE DECISION (mechanical scorer authority — this is the pick):',
                f'  Pick:       {label}',
                f'  Tier:       {tier}   conviction: {conv}   raw score: {score}',
            ]
            # Also show other markets that had a pick
            other_picks = [
                f'  {m.upper()}: {v.get("label")} ({v.get("tier")}, conv {v.get("conviction")})'
                for m, v in all_markets.items()
                if v.get('pick') is not None and v.get('label') != label
            ]
            if other_picks:
                lines.append('')
                lines.append('  Secondary market picks (available if you want to note):')
                lines.extend(other_picks)
            if sources:
                lines.append('')
                lines.append('  Supporting signals (weighted by historical hit rate):')
                for s in sources[:6]:
                    prose = s.get('prose') or s.get('signal_key', '')
                    weight = s.get('weight', 0)
                    n = s.get('n', 0)
                    lines.append(f'    - {prose}  [weight {weight:.2f}, n={n}]')
            ensemble_block = '\n'.join(lines)
    except Exception:
        pass

    struct_json = json.dumps(struct, indent=2, default=str)
    return (
        template
        .replace("{STRUCT}", struct_json)
        .replace("{AWAY_TEAM}", game.get("away_team", ""))
        .replace("{HOME_TEAM}", game.get("home_team", ""))
        .replace("{AWAY_PITCHER}", game.get("away_pitcher") or "(TBD)")
        .replace("{HOME_PITCHER}", game.get("home_pitcher") or "(TBD)")
        .replace("{SHARP_SCENARIOS}", sharp_block)
        .replace("{SIGNAL_PLAYBOOK}", registry_block)
        .replace("{ENSEMBLE_DECISION}", ensemble_block)
    )


def call_claude(prompt: str) -> str | None:
    if not ANTHROPIC_API_KEY:
        print("  ⚠ ANTHROPIC_API_KEY missing — synthesis skipped")
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=45,
        )
        if r.status_code != 200:
            print(f"  ⚠ claude {r.status_code}: {r.text[:200]}")
            return None
        return r.json()["content"][0]["text"]
    except Exception as e:
        print(f"  ⚠ claude call failed: {e}")
        return None


# ─── Response parser ─────────────────────────────────────────────────────
def parse_synthesis(raw: str) -> dict:
    """Parse Jerry's output into structured fields.

    Expected LLM format (enforced by prompt):
        ---SHORT---
        <40-60 word card preview>
        ---LONG---
        <200-300 word analysis>
        ---CALL---
        MARKET: ml|rl|total|prop|pass
        SIDE: HOME|AWAY|OVER|UNDER|null
        LINE: <number>|null
        CALL_TEXT: <human-readable e.g. "Pirates ML" / "Under 8.5" / "Pass">
        CONVICTION: <0-100>
    """
    def _section(name: str) -> str | None:
        m = re.search(rf"---{name}---\s*(.*?)(?=---[A-Z]+---|$)", raw, re.S)
        return m.group(1).strip() if m else None

    short = _section("SHORT") or ""
    long_ = _section("LONG") or ""
    call_block = _section("CALL") or ""

    # Defense-in-depth (2026-08-03): globally strip markdown from CALL block
    # BEFORE field extraction. Jerry occasionally wraps field names AND values
    # in ** (e.g. "**MARKET:** **ML**"), which broke prior surround-only fixes.
    # Wiping all * / _ pre-parse makes field extraction bulletproof regardless
    # of markdown flavor. Only applied to CALL block — preserves markdown in
    # short/long prose which is user-facing.
    call_block = re.sub(r"\*+", "", call_block)
    call_block = re.sub(r"_+", "", call_block)

    def _field(field: str) -> str | None:
        # Allow optional markdown around the field name itself (Jerry sometimes
        # writes "**MARKET:** pass") and strip surrounding markdown/whitespace
        # from the captured value. Was causing call_market='** pass' on PASS
        # rows and downstream Sweat Card display corruption (2026-08-03).
        m = re.search(rf"\**{field}\**\s*:\s*(.+?)(?=\n\**[A-Z_]+\**\s*:|$)",
                       call_block, re.S)
        if not m: return None
        val = m.group(1).strip()
        # Strip leading/trailing markdown asterisks + underscores
        val = re.sub(r"^[*_\s]+|[*_\s]+$", "", val)
        return val or None

    market = (_field("MARKET") or "").lower() or None
    # Extra scrub — some responses still leak a stray leading "**" that survives
    if market and market.startswith("*"):
        market = market.lstrip("*").strip()
    side = (_field("SIDE") or "").upper() or None
    if side == "NULL": side = None
    line_raw = _field("LINE")
    try:
        line = float(line_raw) if line_raw and line_raw.lower() != "null" else None
    except ValueError:
        line = None
    call_text = _field("CALL_TEXT")
    conv_raw = _field("CONVICTION")
    try:
        conviction = max(0, min(100, int(re.sub(r"\D", "", conv_raw or "")))) if conv_raw else None
    except ValueError:
        conviction = None

    # Post-parse validation (2026-08-03, expanded 2026-08-06 for persona shift):
    # ensure market falls in valid set, else log + null out. Prevents corrupted
    # values reaching downstream.
    # 2026-08-04: added 'lean' — Jerry emits when directional signal is
    # visible but conviction is 50-64 (soft directional).
    # 2026-08-06: no new market added — READ tier is expressed via ml/rl/total
    # with CONVICTION in 30-49 band. Tier derivation happens on the card side
    # from the conviction number, not from a distinct market label. This keeps
    # market values clean (ml/rl/total/prop) while the tier granularity lives
    # in CONVICTION. PASS remains but is now reserved for structurally blank
    # data (postponed, lineup missing, no market posted).
    # 2026-08-07: removed 'lean' from valid markets. LEAN is a TIER (derived
    # from CONVICTION), not a market. When Jerry emits MARKET=lean (tier/
    # market conflation), infer the actual market from SIDE: OVER/UNDER →
    # total, HOME/AWAY → ml (spread requires an explicit LINE). Prevents
    # call_market='lean' rows from persisting.
    _VALID_MARKETS = {'ml', 'rl', 'total', 'prop', 'pass', None}
    if market == 'lean':
        # Infer from side
        if side in ('OVER', 'UNDER'):
            market = 'total'
            print(f"  ⚠ MARKET=lean coerced to 'total' (inferred from SIDE={side})")
        elif side in ('HOME', 'AWAY'):
            market = 'ml'
            print(f"  ⚠ MARKET=lean coerced to 'ml' (inferred from SIDE={side})")
        else:
            print(f"  ⚠ MARKET=lean with SIDE={side!r} — can't infer, nulling")
            market = None; side = None
    elif market not in _VALID_MARKETS:
        print(f"  ⚠ parser produced invalid call_market {market!r} — nulling")
        market = None
        side = None
    _VALID_SIDES = {'HOME', 'AWAY', 'OVER', 'UNDER', None}
    if side not in _VALID_SIDES:
        print(f"  ⚠ parser produced invalid call_side {side!r} — nulling")
        side = None

    # Persona-shift enforcement (2026-08-06): the new prompt commands PASS
    # only for structurally broken data. If Jerry emits PASS with a
    # conviction > 30, that's a prompt violation — coerce to READ tier
    # (keep the directional take if any, otherwise flag and skip).
    # Log-only for the first week so we can watch drift, then hard-enforce.
    if market == 'pass' and conviction is not None and conviction > 30:
        print(f"  ⚠ PERSONA VIOLATION: MARKET=pass with CONVICTION={conviction} — "
              f"expected READ (ml/rl/total with conv 30-49) instead")

    return {
        "short_read": short,
        "long_read": long_,
        "call_market": market,
        "call_side": side,
        "call_line": line,
        "call_text": call_text,
        "conviction": conviction,
    }


# ─── Ensemble-authority defer ─────────────────────────────────────────────
# 2026-08-22 Option C consolidation: ensemble is the pick authority.
# Jerry writes prose but the CALL fields (call_market/side/text/line/
# conviction) come from primary_play so game detail hero + Jerry writeup
# always agree.
#
# Audit 8/22: yesterday 12/15 games had ensemble ML pick contradicting
# Jerry's writeup call (e.g. Braves@Brewers ensemble backed Brewers ML W,
# Jerry writeup called Over 6.0 L). Users saw the ensemble pick on the
# game card badge + Hero verdict card but Jerry prose recommended a
# different bet. Real cost of the divergence: Jerry graded 7-7-1 (50%)
# yesterday while ensemble ML went 11-1 (91.7%).
#
# Fix: after LLM writes prose, overwrite the parsed call_* fields with
# whatever ensemble_scorer picked. Prose stays as-is (the LLM was already
# prompted with the ensemble pick as authoritative — see ENSEMBLE_DECISION
# block in render_prompt). If ensemble has no pick / PASS, preserve
# Jerry's read as-is so we don't lose the read entirely.
_VALID_MARKETS = {'ml', 'rl', 'total', 'nrfi', 'yrfi', 'fight'}


def _extract_primary_play(struct: dict) -> dict | None:
    """Locate primary_play regardless of where it's nested in the struct.

    2026-08-22 bug: build_struct at line 165 puts primary_play at
    struct['full_models']['primary_play']. defer_call_to_ensemble was
    looking at struct.get('primary_play') top-level — always None, so
    defer silently no-op'd for every game. Result: 4 of 25 Jerry rows
    tonight had a market pick different from ensemble's primary_play
    (Detroit RL vs Jerry TOTAL, Rockies ML vs Jerry TOTAL, etc.).
    """
    if not isinstance(struct, dict): return None
    # Preferred: top-level (future-proof if we ever add it there)
    pp = struct.get('primary_play')
    if isinstance(pp, dict): return pp
    # Actual location today
    fm = struct.get('full_models')
    if isinstance(fm, dict):
        pp = fm.get('primary_play')
        if isinstance(pp, dict): return pp
    # Legacy: confluence.primary_play (generate_mlb_game_reads.py:914)
    conf = struct.get('confluence')
    if isinstance(conf, dict):
        pp = conf.get('primary_play')
        if isinstance(pp, dict): return pp
    return None


def defer_call_to_ensemble(parsed: dict, struct: dict) -> dict:
    """Overwrite parsed call_* with ensemble primary_play values. Idempotent.

    2026-08-30: Jerry-as-narrator gate. When ensemble tier is COVERAGE
    (MC dissent blocked publish) OR PASS/SKIP (no edge), overwrite the
    LLM's short_read with a passed-by-engine narrative so we never ship
    a BACK story on a dead pick. Rays 8/30 canonical: engine PRIME →
    MC 30.9% dissent → COVERAGE, but Jerry was narrating sharp-money BACK.
    """
    pp = _extract_primary_play(struct)
    if pp is None or pp.get('_engine') != 'ensemble_v2':
        return parsed  # no ensemble to defer to — keep LLM output
    market = str(pp.get('type') or '').lower()
    side = pp.get('side')
    label = pp.get('label')
    conviction = pp.get('conviction')
    line = pp.get('line')
    tier = str(pp.get('tier') or '').upper()

    # Engine passed — Jerry becomes the "why we passed" narrator.
    if tier in ('COVERAGE', 'PASS', 'SKIP') or (market not in _VALID_MARKETS or not side or not label):
        dissent = pp.get('_mc_dissent') or {}
        pct = dissent.get('mc_pick_win_pct')
        orig = dissent.get('orig_tier')
        if pct is not None and orig:
            new_short = (f'Engine passed — the {orig} setup collapses under MC sim '
                         f'({pct}% win prob for our side). No play.')
        else:
            engine_sub = str(pp.get('sub') or '').strip()
            new_short = (f'Engine passed — no publishable edge on this game. '
                         f'{engine_sub}' if engine_sub else 'Engine passed — no publishable edge on this game.')
        parsed['call_market'] = 'pass'
        parsed['call_side'] = None
        parsed['call_line'] = None
        parsed['call_text'] = 'Pass'
        parsed['conviction'] = 0
        parsed['short_read'] = new_short[:2000]
        return parsed

    parsed['call_market'] = market
    parsed['call_side'] = str(side).upper()
    parsed['call_line'] = line
    parsed['call_text'] = label  # human-readable e.g. "Brewers ML"
    if isinstance(conviction, (int, float)):
        parsed['conviction'] = max(0, min(100, int(conviction)))
    return parsed


# ─── Persist ─────────────────────────────────────────────────────────────
def upsert_jerry_read(game: dict, parsed: dict, struct: dict,
                     game_date: str) -> bool:
    # 2026-08-22 Option C: force call_* to ensemble pick before persist
    parsed = defer_call_to_ensemble(parsed, struct)
    # 2026-08-07 filter: skip empty PASS reads to prevent PASS-badge
    # pollution on graded games. If Jerry has no directional take AND
    # no call text, don't persist the row — the app's grading view
    # correctly shows "no read" instead of "PASS". Legitimate PASS
    # reads (postponed games, structurally broken data) should still
    # emit call_text explaining why — those DO persist.
    market = (parsed.get('call_market') or '').lower()
    side = parsed.get('call_side')
    text = (parsed.get('call_text') or '').strip()
    if market == 'pass' and not side and not text:
        print(f"  ⏭  skipping empty PASS read for {game.get('game_id')} — no take, no rationale")
        return False

    payload = {
        "sport": "MLB",
        "game_id": game.get("game_id"),
        "game_date": game_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prompt_version": PROMPT_VERSION,
        "input_snapshot": struct,
        **parsed,
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_reads?on_conflict=sport,game_id,game_date",
        headers=SB_WRITE,
        json=payload,
        timeout=20,
    )
    if r.status_code in (200, 201, 204):
        return True
    print(f"  ⚠ upsert {r.status_code}: {r.text[:200]}")
    return False


# ─── Main ────────────────────────────────────────────────────────────────
def _fetch_games_for_sport(sport: str, gd: str) -> list:
    """Sport-parametric: pulls today's game_context rows for the sport.
    Falls back to legacy fetch_games() for MLB backward compat."""
    if sport == 'MLB':
        return fetch_games()
    reg = SPORT_REGISTRY.get(sport)
    if not reg: return []
    table = reg['context_table']
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}",
                     headers=SB_READ,
                     params={"game_date": f"eq.{gd}", "select": "*"},
                     timeout=15)
    return r.json() if r.status_code == 200 else []


def run(force: bool = False, game_date: str | None = None,
        limit: int | None = None, sport: str = 'MLB') -> None:
    gd = game_date or today_et()
    sport = sport.upper()
    print(f"=== generate_jerry_synthesis · {sport} · {gd} ===")

    reg = SPORT_REGISTRY.get(sport)
    if not reg:
        print(f"  ⛔ sport {sport} not in SPORT_REGISTRY — abort")
        return
    if not reg['active'] and sport != 'MLB':
        # Belt-and-suspenders: allow MLB always; skip inactive sports even if
        # the cron accidentally invokes them. Flip 'active' when ready.
        print(f"  ⏭  sport {sport} not active in registry — skipping (safe no-op)")
        return

    template = load_synthesis_prompt(sport)
    if not template:
        print(f"  ⛔ no jerry_synthesis prompt for {sport} (and no ALL fallback)")
        return

    games = _fetch_games_for_sport(sport, gd)
    if not games:
        print(f"  ⚠ no {sport} games on slate"); return
    print(f"  slate: {len(games)} {sport} games")
    props_by_game = fetch_props_by_game()
    potd = fetch_potd()
    source_records = fetch_source_track_records()
    print(f"  source records loaded: {len(source_records)} sources")

    # Preload totals cohort stats once (2026-08-01 E-4). Jerry cites firing
    # cohorts with their backtested hit rates when synthesizing totals reads.
    totals_cohort_stats = {}
    try:
        from totals_cohort_attribution import load_stats
        totals_cohort_stats = load_stats(sport if sport in ('MLB',) else 'MLB')
        print(f"  totals cohort stats loaded: {len(totals_cohort_stats)} (sport={sport})")
    except Exception as e:
        print(f"  ⚠ totals cohort stats load failed: {e}")

    # Game bucket ROI (R-4 + Path B kill switch): load once, inject per game.
    # JERRY_BUCKET_ROI_ENABLED=false disables entirely for rollback safety.
    game_bucket_roi = {}
    BUCKET_ROI_ON = os.environ.get('JERRY_BUCKET_ROI_ENABLED', 'true').lower() != 'false'
    if BUCKET_ROI_ON:
        try:
            from bucket_roi_lookup import load_game_buckets
            game_bucket_roi = load_game_buckets(sport=sport if sport in ('MLB',) else 'MLB')
            print(f"  game bucket ROI loaded: {len(game_bucket_roi)} (sport={sport})")
        except Exception as e:
            print(f"  ⚠ game bucket ROI load failed: {e}")
    else:
        print(f"  JERRY_BUCKET_ROI_ENABLED=false — bucket ROI injection disabled")

    done = 0
    attempted = 0
    for g in games:
        if limit and attempted >= limit:
            break
        attempted += 1
        home, away = g.get("home_team"), g.get("away_team")
        gid = g.get("game_id")
        if not force:
            # 2026-08-29: only skip when a REAL synthesis row exists
            # (prompt_version = synthesis_v1). Previously any row blocked
            # regen — but sync_jerry_reads_from_ctx.py writes placeholder
            # bridge_v1 rows with "Analysis pending" short_read as a
            # fallback. Those placeholders were sticking permanently:
            # sync ran first, wrote placeholder, this gate then skipped
            # synthesis, so real LLM output never landed. All 17 MLB
            # games showed "Analysis pending" today.
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/jerry_reads",
                headers=SB_READ,
                params={"sport": "eq.MLB", "game_id": f"eq.{gid}",
                        "game_date": f"eq.{gd}",
                        "prompt_version": f"eq.{PROMPT_VERSION}",
                        "select": "game_id"},
                timeout=10,
            )
            if r.status_code == 200 and r.json():
                print(f"  • {away} @ {home}: real synthesis exists, skip (--force to regen)")
                continue

        props = next((v for k, v in props_by_game.items() if _matches(k, home, away)), [])
        base_struct = build_struct(g, props, potd)
        externals = fetch_externals_for_game(gid, gd)
        struct = enrich_struct(base_struct, g, externals, source_records,
                               totals_cohort_stats=totals_cohort_stats)

        # Inject game bucket ROI hints into struct so Jerry sees them in {STRUCT}
        # JSON. Look up bucket for the game's primary_play tier + market.
        try:
            from bucket_roi_lookup import lookup_game, format_game_hint
            pp = g.get('primary_play') or {}
            if isinstance(pp, dict) and pp.get('tier') and pp.get('type'):
                # Best-effort direction from label: 'HOME'/'AWAY' for ML/RL, 'OVER'/'UNDER' for totals
                label = str(pp.get('label') or '').lower()
                sub = str(pp.get('sub') or '').lower()
                if pp.get('type').lower() == 'total':
                    direction = 'OVER' if 'over' in label or 'over' in sub else 'UNDER'
                else:
                    direction = 'HOME' if 'home' in label or (g.get('home_team','').lower() in label) else 'AWAY'
                b = lookup_game(game_bucket_roi, pp['tier'], pp['type'], direction)
                struct['game_bucket_roi'] = b
                struct['game_bucket_hint'] = format_game_hint(b)
        except Exception as e:
            print(f"  ⚠ game bucket lookup: {e}")

        prompt = render_prompt(template, g, struct)
        raw = call_claude(prompt)
        if not raw:
            print(f"  ⚠ {away} @ {home}: no response, skip")
            continue

        parsed = parse_synthesis(raw)
        if not parsed.get("short_read") or not parsed.get("long_read"):
            print(f"  ⚠ {away} @ {home}: parse missing short/long sections")
            print(f"     raw head: {raw[:200]!r}")
            continue

        # Post-LLM brand-name sanitizer (2026-08-03) — belt-and-suspenders
        # for the prompt's BRAND ATTRIBUTION GUARDRAIL. Removes any leaked
        # data-provider or handicapper names from prose before storage.
        try:
            from sanitize_jerry_prose import scrub, audit
            leaked_before = audit(parsed.get("long_read", "")) + audit(parsed.get("short_read", ""))
            parsed["short_read"] = scrub(parsed.get("short_read"))
            parsed["long_read"] = scrub(parsed.get("long_read"))
            if leaked_before:
                print(f"  🔧 sanitized brand leaks: {sorted(set(leaked_before))}")
        except ImportError:
            pass  # sanitizer optional — prompt guardrail alone is defensive

        # Hallucination guard v2 (2026-08-06): auto-retry + name whitelist +
        # substitution fallback. Was LOG-ONLY before; user directive to
        # hard-enforce for paid-launch readiness (no more "David an analyst"
        # as a pitcher name, no more cited numbers not in struct).
        try:
            from validate_jerry_read import (validate as _validate,
                                              validate_pitcher_names,
                                              validate_style_rules,
                                              substitute_generic_pitcher_refs,
                                              build_corrective_prompt,
                                              substitute_hallucinated_names)
            combined_prose = (parsed.get("short_read") or "") + "\n" + (parsed.get("long_read") or "")
            # Build explicit whitelist from raw game row (struct is derived +
            # may not have top-level home_pitcher/away_pitcher fields).
            name_whitelist = {
                'home_pitcher': g.get('home_pitcher'),
                'away_pitcher': g.get('away_pitcher'),
                'home_team': g.get('home_team'),
                'away_team': g.get('away_team'),
                'home_lineup': g.get('home_lineup'),
                'away_lineup': g.get('away_lineup'),
                # 2026-08-08: include umpire name + full derived struct
                # (lineups, opposing_lineup, weather notes) so umpire
                # names like "Tom Hanahan" don't get flagged as
                # hallucinated pitcher names and rewritten by Layer C.
                'umpire': {'name': g.get('umpire')} if g.get('umpire') else None,
                'pitchers': (struct.get('pitchers') if isinstance(struct, dict) else None),
                'batters': (struct.get('batters') if isinstance(struct, dict) else None),
                'lineup': (struct.get('lineup') if isinstance(struct, dict) else None),
                'lineups': (struct.get('lineups') if isinstance(struct, dict) else None),
            }
            # 2026-08-08: pass pitcher names + market into struct-view Jerry
            # would have used, so style validator can check gap vs market and
            # generic pitcher references.
            style_struct = dict(struct) if isinstance(struct, dict) else {}
            # 2026-08-08: overwrite (not setdefault) — derived struct may
            # carry a nested pitchers.*.name shape without a flat
            # home_pitcher key, but if a flat null value slipped in the
            # setdefault would preserve None and the generic-ref check
            # skips (tbd branch), letting "the opposing starter" through.
            style_struct['home_pitcher'] = g.get('home_pitcher') or (
                (struct.get('pitchers', {}).get('home', {}) or {}).get('name')
                if isinstance(struct, dict) else None)
            style_struct['away_pitcher'] = g.get('away_pitcher') or (
                (struct.get('pitchers', {}).get('away', {}) or {}).get('name')
                if isinstance(struct, dict) else None)
            # 2026-08-08 (evening): expose the parsed CALL so
            # sim_pick_direction_mismatch rule can compare cited runs vs pick
            style_struct['call_market'] = parsed.get('call_market')
            style_struct['call_side'] = parsed.get('call_side')
            style_struct['call_line'] = parsed.get('call_line')
            num_report = _validate(parsed.get("short_read"), parsed.get("long_read"), struct)
            name_report = validate_pitcher_names(combined_prose, name_whitelist)
            style_report = validate_style_rules(parsed.get("short_read"),
                                                 parsed.get("long_read"), style_struct)

            if not num_report['is_valid'] or not name_report['valid'] or not style_report['valid']:
                # LAYER A: retry once with corrective feedback
                style_rules = [it.get('rule') for it in (style_report.get('issues') or [])]
                print(f"  ⚠ hallucination detected (nums={num_report.get('hallucinated_numbers')}, "
                      f"names={name_report.get('suspects')}, style={style_rules}) — regen with corrective prompt")
                corrective = build_corrective_prompt(prompt, num_report, name_report, style_report)
                raw2 = call_claude(corrective)
                retry_worked = False
                if raw2:
                    parsed2 = parse_synthesis(raw2)
                    if parsed2.get('short_read') and parsed2.get('long_read'):
                        try:
                            from sanitize_jerry_prose import scrub
                            parsed2['short_read'] = scrub(parsed2.get('short_read'))
                            parsed2['long_read'] = scrub(parsed2.get('long_read'))
                        except ImportError: pass
                        combined2 = (parsed2.get("short_read") or "") + "\n" + (parsed2.get("long_read") or "")
                        num2 = _validate(parsed2.get("short_read"), parsed2.get("long_read"), struct)
                        name2 = validate_pitcher_names(combined2, name_whitelist)
                        style2 = validate_style_rules(parsed2.get("short_read"),
                                                       parsed2.get("long_read"), style_struct)
                        parsed = parsed2  # accept retry even if imperfect (usually better)
                        if num2['is_valid'] and name2['valid'] and style2['valid']:
                            print(f"  ✓ retry succeeded — clean output")
                            retry_worked = True
                        else:
                            # Update reports to reflect retry state for substitution + logging
                            name_report = name2
                            num_report = num2
                            style_report = style2
                            if style2.get('issues'):
                                print(f"  ⚠ retry still has style issues: "
                                      f"{[i['rule'] for i in style2['issues']]}")
                # LAYER C: if still hallucinating names after retry (or retry
                # produced nothing), substitute with 'the home/away starter'.
                if not retry_worked and name_report.get('suspects'):
                    parsed['short_read'] = substitute_hallucinated_names(
                        parsed.get('short_read',''), struct, name_report['suspects'])
                    parsed['long_read'] = substitute_hallucinated_names(
                        parsed.get('long_read',''), struct, name_report['suspects'])
                    print(f"  🔧 substituted {len(name_report['suspects'])} suspect name(s) with generic form")

                # NUMBER HALLUCINATION HARD-ENFORCE (2026-08-06 v2, hardened 2026-08-09).
                # Names can be substituted safely (references), but numbers ARE the
                # pitch's meaning ("0.94 xERA" is not swappable). If retry STILL
                # leaves hallucinated numbers:
                #   - LIGHT (1-2 unverified numbers): cap conviction 55 + append
                #     transparency footer to short_read explaining the flag.
                #   - HEAVY (3+ unverified numbers): downgrade to READ tier +
                #     scrub the top-line CALL to prevent the read from ranking
                #     in top_8. Users still see the analysis (analytical take)
                #     but the pipeline treats it as non-actionable.
                # Both cases log for morning audit.
                if not retry_worked and num_report.get('hallucinated_numbers'):
                    orig_conv = parsed.get('conviction') or 0
                    hallus = num_report['hallucinated_numbers']
                    hallucinated_note = (f"[Numeric integrity flag: {len(hallus)} figure(s) "
                                         f"in this take couldn't be traced back to source data: "
                                         f"{', '.join(str(h) for h in hallus[:3])}. "
                                         f"Read as directional take, not verified numbers.]")
                    if len(hallus) >= 3:
                        # HEAVY hallucination — downgrade to READ tier equivalent
                        # (conv 45 caps below LEAN's 55 floor). Also append
                        # transparency note to short_read so if the read still
                        # surfaces anywhere, the user sees why it's demoted.
                        if orig_conv > 45:
                            parsed['conviction'] = 45
                            print(f"  🚨 conviction hard-floored {orig_conv}→45 (READ) — "
                                  f"{len(hallus)} unverified numbers: {hallus[:5]}")
                        # Append transparency note (idempotent: don't double-append)
                        cur_short = parsed.get('short_read') or ''
                        if 'Numeric integrity flag' not in cur_short:
                            parsed['short_read'] = f"{cur_short}\n\n{hallucinated_note}"[:2000]
                    elif orig_conv > 55:
                        # LIGHT hallucination — LEAN cap ONLY (no footer).
                        # 2026-08-12: hide footer for 1-2 flags. These are
                        # usually derived stats (L5 averages, K/BB ratios)
                        # that aren't literally in the source struct but
                        # arithmetically correct. LEAN cap keeps the safety
                        # net; hiding the footer reduces user-facing noise
                        # on legitimate derivations. Only 3+ flags (HEAVY
                        # branch above) still surface the footer to users.
                        parsed['conviction'] = 55
                        print(f"  🔒 conviction capped {orig_conv}→55 (LEAN) — "
                              f"unverified: {hallus[:3]} (footer hidden for LIGHT)")

                # STYLE HARD-CAP (2026-08-08): sim-vs-market gap + hitter-AB
                # fabrication + generic pitcher refs after retry all destroy
                # credibility on a bettor-facing read. Same treatment as
                # number hallucinations — cap at LEAN so downstream tier
                # signal warns instead of publishing at PRIME/STRONG.
                if not retry_worked and style_report.get('issues'):
                    critical_rules = {'generic_pitcher_ref', 'hitter_l7_ab_fabrication',
                                       'sim_market_gap_over_cap', 'post_bet_conditional',
                                       'bullpen_unit_missing', 'sim_pick_direction_mismatch'}
                    critical_hit = any(it.get('rule') in critical_rules
                                        for it in style_report['issues'])
                    if critical_hit:
                        orig_conv = parsed.get('conviction') or 0
                        if orig_conv > 55:
                            parsed['conviction'] = 55
                            print(f"  🔒 conviction capped {orig_conv}→55 (LEAN) due to style violations: "
                                  f"{[i['rule'] for i in style_report['issues'][:3]]}")

            # LAYER D (2026-08-09): mechanical scrub of 'the opposing starter'
            # → real pitcher name. Runs unconditionally after retry — belt-
            # and-suspenders for cases where LLM keeps the generic phrase
            # despite corrective prompt. On 2026-08-09, 8/15 games shipped
            # with 'the opposing starter' leaking past LEAN cap. Also
            # patches umpire/park hallucinations that Layer C created.
            sub_struct = dict(style_struct)
            sub_struct['home_team'] = g.get('home_team') or struct.get('home_team')
            sub_struct['away_team'] = g.get('away_team') or struct.get('away_team')
            for key in ('short_read', 'long_read'):
                before = parsed.get(key) or ''
                after = substitute_generic_pitcher_refs(before, sub_struct)
                if before != after:
                    parsed[key] = after
                    print(f"  🔧 Layer D scrubbed generic pitcher refs in {key}")

            # LAYER E (2026-08-10): stat-hallucination cross-check against MLB
            # Stats API. Catches numeric drift like "2.0 IP as opener" when
            # actual last outing was 6.0 IP, or "8.83 ERA L3" when real is
            # 4.02. Runs on BOTH pitchers per game.
            #
            # On critical hit → mechanically strip the offending sentence
            # (auto-repair path). Also cap conviction at LEAN if any critical
            # hallucinations survived stripping. No retry loop here — the
            # per-sentence strip is enough; a retry adds latency + cost with
            # marginal quality lift. If stripping fails, LAYER E logs the
            # violation and the pre-publish audit gate #10 will block on it.
            try:
                from jerry_stat_verifier import verify as _sv, strip_hallucinated_sentences
                pitchers_to_check = []
                for pkey in ('home_pitcher', 'away_pitcher'):
                    pname = sub_struct.get(pkey)
                    if pname and pname != '(TBD)':
                        pitchers_to_check.append(pname)
                halluc_count = 0
                for pname in pitchers_to_check:
                    for key in ('short_read', 'long_read'):
                        prose = parsed.get(key) or ''
                        if not prose: continue
                        sv = _sv(prose, pname)
                        crit = [v for v in sv['violations']
                                if v.get('severity') == 'critical']
                        if crit:
                            halluc_count += len(crit)
                            new_prose = strip_hallucinated_sentences(prose, crit)
                            if new_prose != prose:
                                parsed[key] = new_prose
                                print(f"  🚨 Layer E stripped {len(crit)} hallucinated "
                                      f"claim(s) about {pname} from {key}")
                            else:
                                print(f"  🚨 Layer E flagged {len(crit)} hallucination(s) "
                                      f"about {pname} in {key} but strip failed — audit will block")
                if halluc_count > 0:
                    orig_conv = parsed.get('conviction') or 0
                    if orig_conv > 55:
                        parsed['conviction'] = 55
                        print(f"  🔒 conviction capped {orig_conv}→55 (LEAN) after "
                              f"{halluc_count} stat hallucination(s)")
            except ImportError:
                pass
        except ImportError:
            pass

        # 2026-08-09: reconstruct call_text BEFORE upsert (was only fixing
        # the log line afterwards). Users saw stale "Pass" badges on the
        # game-list Jerry box for any read where the LLM omitted call_text
        # but did emit a directional market/side/line. Now the DB always
        # gets a non-null call_text so the badge renders correctly.
        if not parsed.get('call_text'):
            mkt = (parsed.get('call_market') or 'pass').lower()
            side = parsed.get('call_side') or ''
            line = parsed.get('call_line')
            if mkt == 'pass':
                parsed['call_text'] = 'PASS'
            elif mkt == 'total' and side:
                parsed['call_text'] = f"{side.title()} {line or ''}".strip()
            elif mkt == 'ml' and side:
                parsed['call_text'] = f"{side.title()} ML"
            elif mkt == 'rl' and side:
                parsed['call_text'] = f"{side.title()} RL {line or ''}".strip()
            elif mkt == 'fight' and side:
                parsed['call_text'] = f"Fighter {side}"

        if upsert_jerry_read_sport(g, parsed, struct, gd, sport):
            # Display fallback: prefer call_text; if missing, reconstruct from
            # market + side + line so logs don't misleadingly show "PASS" for
            # a real ml/total pick where the LLM omitted CALL_TEXT (persona
            # shift 2026-08-06 introduces more variance in LLM field discipline).
            call_text = parsed.get('call_text')
            if not call_text:
                mkt = parsed.get('call_market') or 'pass'
                side = parsed.get('call_side') or ''
                line = parsed.get('call_line')
                if mkt == 'pass':
                    call_text = 'PASS'
                elif mkt == 'total':
                    call_text = f"{side.title()} {line or ''}".strip()
                elif mkt == 'ml':
                    call_text = f"{side.title()} ML" if side else 'ML'
                elif mkt == 'rl':
                    call_text = f"{side.title()} RL {line or ''}".strip()
                else:
                    call_text = f"{mkt.upper()} {side}".strip()
            call_str = f"{call_text} ({parsed.get('conviction') or '-'})"
            print(f"  ✓ {away} @ {home}: {call_str}  [{len(externals)} externals]")
            done += 1
        if limit and done >= limit:
            break

    print(f"=== wrote {done} jerry_reads for {sport} ===")

    # 2026-08-08 auto-sync sweat card after Jerry regen.
    # Root cause discovered in 8/7 post-mortem: Jerry regenerated at 6:36pm
    # ET (1 hour before first pitch), flipping CLE @ CHW UNDER → OVER.
    # Card was cached from morning run at 9am with the stale UNDER — users
    # who bet from the card locked in the wrong direction. Now: any Jerry
    # regen for MLB triggers a fresh sweat card build so cached card always
    # matches current Jerry reads.
    # Only fires when done>0 (something actually wrote) and sport is MLB
    # (card generator is MLB-specific; per-sport cards ship separately).
    if done > 0 and sport == 'MLB':
        try:
            print()
            print(f'🔄 auto-syncing sweat card after Jerry regen...')
            import generate_sweat_card
            generate_sweat_card.build_card()
        except Exception as e:
            print(f'  ⚠ sweat card auto-sync failed (not fatal): {e}')


def upsert_jerry_read_sport(game: dict, parsed: dict, struct: dict,
                             game_date: str, sport: str) -> bool:
    """Sport-universal upsert — same schema as upsert_jerry_read but writes
    the sport tag correctly for non-MLB sports."""
    # 2026-08-22 Option C: force call_* to ensemble pick before persist
    parsed = defer_call_to_ensemble(parsed, struct)
    payload = {
        "sport": sport,
        "game_id": game.get("game_id"),
        "game_date": game_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prompt_version": PROMPT_VERSION,
        "input_snapshot": struct,
        **parsed,
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_reads?on_conflict=sport,game_id,game_date",
        headers=SB_WRITE, json=payload, timeout=20,
    )
    if r.status_code in (200, 201, 204): return True
    print(f"  ⚠ upsert {r.status_code}: {r.text[:200]}")
    return False


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    p.add_argument("--date")
    p.add_argument("--limit", type=int)
    p.add_argument("--sport", default="MLB",
                   help="MLB (default). NBA/NFL/NCAAF/NCAAB when their pipelines ship.")
    args = p.parse_args()
    run(force=args.force, game_date=args.date, limit=args.limit, sport=args.sport)
