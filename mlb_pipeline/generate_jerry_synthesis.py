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
                  source_records: dict) -> dict:
    """Add the fields Jerry v2 needs on top of the legacy struct.

    Adds:
      externals: [{source, surface, side, line, odds, confidence, raw_text,
                   source_30d_hit_rate, source_30d_n}]
      full_models: every internal projection in one place — panel, MC, v4,
                   Jerry pred, cohorts v1+v2, mc_hc flags, align_status
      align_status: mirrored to top level for prompt convenience
      money_flow: oddscrowd snapshot at top level
    """
    struct = dict(struct)

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
def load_synthesis_prompt() -> str | None:
    """Fetch the jerry_synthesis/MLB prompt template. See seed_synthesis_prompt.py
    for the initial insert."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/prompt_templates",
        headers=SB_READ,
        params={"name": "eq.jerry_synthesis", "sport": "eq.MLB",
                "is_active": "eq.true", "select": "template"},
        timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
    return rows[0]["template"] if rows else None


def render_prompt(template: str, game: dict, struct: dict) -> str:
    """Substitute {STRUCT} in the template. Template does the framing."""
    struct_json = json.dumps(struct, indent=2, default=str)
    return (
        template
        .replace("{STRUCT}", struct_json)
        .replace("{AWAY_TEAM}", game.get("away_team", ""))
        .replace("{HOME_TEAM}", game.get("home_team", ""))
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

    def _field(field: str) -> str | None:
        m = re.search(rf"{field}\s*:\s*(.+?)(?=\n[A-Z_]+\s*:|$)", call_block, re.S)
        return m.group(1).strip() if m else None

    market = (_field("MARKET") or "").lower() or None
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
def run(force: bool = False, game_date: str | None = None, limit: int | None = None) -> None:
    gd = game_date or today_et()
    print(f"=== generate_jerry_synthesis · {gd} ===")

    template = load_synthesis_prompt()
    if not template:
        print("  ⛔ no jerry_synthesis prompt found — run seed_synthesis_prompt.py first")
        return

    games = fetch_games()
    if not games:
        print("  ⚠ no games on slate"); return
    print(f"  slate: {len(games)} games")
    props_by_game = fetch_props_by_game()
    potd = fetch_potd()
    source_records = fetch_source_track_records()
    print(f"  source records loaded: {len(source_records)} sources")

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
        struct = enrich_struct(base_struct, g, externals, source_records)
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

        if upsert_jerry_read(g, parsed, struct, gd):
            call_str = f"{parsed.get('call_text') or 'PASS'} ({parsed.get('conviction') or '-'})"
            print(f"  ✓ {away} @ {home}: {call_str}  [{len(externals)} externals]")
            done += 1
        if limit and done >= limit:
            break

    print(f"=== wrote {done} jerry_reads ===")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    p.add_argument("--date")
    p.add_argument("--limit", type=int)
    args = p.parse_args()
    run(force=args.force, game_date=args.date, limit=args.limit)
