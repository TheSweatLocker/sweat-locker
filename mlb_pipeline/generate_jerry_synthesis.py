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
    """
    struct_json = json.dumps(struct, indent=2, default=str)
    return (
        template
        .replace("{STRUCT}", struct_json)
        .replace("{AWAY_TEAM}", game.get("away_team", ""))
        .replace("{HOME_TEAM}", game.get("home_team", ""))
        .replace("{AWAY_PITCHER}", game.get("away_pitcher") or "(TBD)")
        .replace("{HOME_PITCHER}", game.get("home_pitcher") or "(TBD)")
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
    _VALID_MARKETS = {'ml', 'rl', 'total', 'prop', 'lean', 'pass', None}
    if market not in _VALID_MARKETS:
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


# ─── Persist ─────────────────────────────────────────────────────────────
def upsert_jerry_read(game: dict, parsed: dict, struct: dict,
                     game_date: str) -> bool:
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
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/jerry_reads",
                headers=SB_READ,
                params={"sport": "eq.MLB", "game_id": f"eq.{gid}",
                        "game_date": f"eq.{gd}", "select": "game_id"},
                timeout=10,
            )
            if r.status_code == 200 and r.json():
                print(f"  • {away} @ {home}: exists, skip (--force to regen)")
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
            }
            num_report = _validate(parsed.get("short_read"), parsed.get("long_read"), struct)
            name_report = validate_pitcher_names(combined_prose, name_whitelist)

            if not num_report['is_valid'] or not name_report['valid']:
                # LAYER A: retry once with corrective feedback
                print(f"  ⚠ hallucination detected (nums={num_report.get('hallucinated_numbers')}, "
                      f"names={name_report.get('suspects')}) — regen with corrective prompt")
                corrective = build_corrective_prompt(prompt, num_report, name_report)
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
                        name2 = validate_pitcher_names(combined2, struct)
                        parsed = parsed2  # accept retry even if imperfect (usually better)
                        if num2['is_valid'] and name2['valid']:
                            print(f"  ✓ retry succeeded — clean output")
                            retry_worked = True
                        else:
                            # Update reports to reflect retry state for substitution below
                            name_report = name2
                            num_report = num2
                # LAYER C: if still hallucinating names after retry (or retry
                # produced nothing), substitute with 'the home/away starter'.
                if not retry_worked and name_report.get('suspects'):
                    parsed['short_read'] = substitute_hallucinated_names(
                        parsed.get('short_read',''), struct, name_report['suspects'])
                    parsed['long_read'] = substitute_hallucinated_names(
                        parsed.get('long_read',''), struct, name_report['suspects'])
                    print(f"  🔧 substituted {len(name_report['suspects'])} suspect name(s) with generic form")
        except ImportError:
            pass

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


def upsert_jerry_read_sport(game: dict, parsed: dict, struct: dict,
                             game_date: str, sport: str) -> bool:
    """Sport-universal upsert — same schema as upsert_jerry_read but writes
    the sport tag correctly for non-MLB sports."""
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
