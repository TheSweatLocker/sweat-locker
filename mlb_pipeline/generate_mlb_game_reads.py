"""
MLB game reads — server-side Jerry (step 2 of the prompt-migration).

For each game on today's slate, assembles a structured "read context" from
pipeline data the cron already produced (model-vs-market edges, confluence
breakdown, umpire tendency, pitcher fragility flags, class projections,
mastery, the game's best plays), feeds it to Claude with prompt templates
pulled from the `prompt_templates` table, and stores {narrative, struct} in
`jerry_cache`. The app reads the narrative + renders the struct as a
deterministic "The Numbers" panel — same pattern as the sweat card.

Run order: after generate_props.py + play_of_day.py (needs props + POTD).

Cache key: game_read_<mlb_game_id>_<YYYY-MM-DD>, sport='mlb'.

Usage: python generate_mlb_game_reads.py [--force] [--limit N]
"""
import os
import sys
import json
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

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
SB_WRITE = {**SB_READ, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}

MODEL = "claude-haiku-4-5-20251001"


def today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def now_et_human():
    d = datetime.now(timezone.utc) - timedelta(hours=4)
    return f"{d.strftime('%A, %B')} {d.day}, {d.year}"


def _f(v):
    try:
        return float(v)
    except Exception:
        return None


def sb_get(path, params=None):
    qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = f"{SUPABASE_URL}/rest/v1/{path}{'?' if qs else ''}{qs}"
    r = requests.get(url, headers=SB_READ, timeout=20)
    return r.json() if r.status_code == 200 else []


# ---------------------------------------------------------------- templates

def load_templates():
    """Pull the active game-read templates. Returns dict or None on failure."""
    rows = sb_get("prompt_templates", {
        "name": "in.(game_read_wrapper,game_read_universal,game_read_rules)",
        "is_active": "is.true",
        "select": "name,sport,template",
    })
    if not rows:
        return None
    out = {}
    for r in rows:
        out[(r["name"], r["sport"])] = r["template"]
    wrapper = out.get(("game_read_wrapper", "ALL"))
    universal = out.get(("game_read_universal", "ALL"))
    mlb_rules = out.get(("game_read_rules", "MLB"))
    if not (wrapper and universal and mlb_rules):
        print(f"  ⚠️ missing template rows — have keys: {list(out.keys())}")
        return None
    return {"wrapper": wrapper, "universal": universal, "mlb_rules": mlb_rules}


# ---------------------------------------------------------------- data fetch

def fetch_games():
    return sb_get("mlb_game_context", {"game_date": f"eq.{today_et()}", "select": "*"})


def fetch_props_by_game():
    """Map matchup-string -> list of props (top conviction first)."""
    rows = sb_get("mlb_pipeline_props", {
        "game_date": f"eq.{today_et()}",
        "select": "player_name,prop_type,prop_line,tier,conviction,signals,matchup",
        "order": "conviction.desc",
    })
    by_game = {}
    for p in rows:
        by_game.setdefault((p.get("matchup") or "").strip(), []).append(p)
    return by_game


def fetch_potd():
    rows = sb_get("jerry_cache", {"game_id": f"eq.best_bet_{today_et()}", "select": "data,narrative"})
    if not rows:
        return None
    d = rows[0].get("data")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:
            d = None
    return d


# ---------------------------------------------------------------- struct

def _nrfi_tier(score):
    if score is None:
        return None
    s = float(score)
    if s >= 95:
        return f"{score} — volatile 95+ band (historically a trap zone)"
    if s >= 90:
        return f"{score} — PRIME 90-94 band (~69% audited 30d)"
    if s >= 70:
        return f"{score} — mild NRFI lean 70-79 (~58% audited)"
    if s <= 40:
        return f"{score} — strong YRFI lean (≤40, ~62-69% audited)"
    return f"{score} — neutral"


def _pitcher_block(g, side):
    name = g.get(f"{side}_pitcher")
    xera = _f(g.get(f"{side}_sp_xera"))
    l3 = _f(g.get(f"{side}_pitcher_last_3_era"))
    fi = _f(g.get(f"{side}_first_inning_era"))
    last_ip = _f(g.get(f"{side}_last_ip"))
    flags = []
    if l3 is not None and xera is not None and l3 - xera >= 1.5:
        flags.append(f"form drift: L3 ERA {l3:.2f} vs xERA {xera:.2f} (+{l3 - xera:.1f})")
    if fi is not None and fi >= 6.0:
        flags.append(f"shaky 1st inning ({fi:.1f} ERA)")
    if last_ip is not None and last_ip < 3.0:
        flags.append(f"last outing only {last_ip:.1f} IP — opener/short")
    if l3 is not None and l3 >= 6.0:
        flags.append(f"getting tagged lately (L3 ERA {l3:.2f}) — pull-early risk")
    return {
        "name": name,
        "xera": xera,
        "l3_era": l3,
        "first_inning_era": fi,
        "vs_team_era": _f(g.get(f"{side}_pitcher_vs_team_era")),
        "vs_team_avg": _f(g.get(f"{side}_pitcher_vs_team_avg")),
        "last_ip": last_ip,
        "flags": flags,
    }


def build_struct(g, props, potd):
    home, away = g.get("home_team"), g.get("away_team")
    close_t = _f(g.get("close_total")) or _f(g.get("open_total"))
    model_t = _f(g.get("model_pred_total")) or _f(g.get("projected_total"))
    total_delta = (model_t - close_t) if (model_t is not None and close_t is not None) else None
    model_spr = _f(g.get("model_pred_spread")) or _f(g.get("projected_spread"))

    # best plays for this game
    best = []
    for p in (props or [])[:6]:
        sig = p.get("signals") or {}
        proj = sig.get("_projected_ks") or sig.get("_projected_bb") or sig.get("_projected_hits")
        reasons = [str(v) for k, v in sig.items() if not k.startswith("_")][:4]
        best.append({
            "player": p.get("player_name"),
            "prop_type": p.get("prop_type"),
            "line": p.get("prop_line"),
            "tier": p.get("tier"),
            "conviction": p.get("conviction"),
            "projection": proj,
            "why": reasons,
        })

    potd_game = ""
    if isinstance(potd, dict):
        gv = potd.get("game") or potd.get("matchup") or ""
        potd_game = gv if isinstance(gv, str) else json.dumps(gv, default=str)
    is_potd = bool(home and away and home in potd_game and away in potd_game)

    return {
        "matchup": f"{away} @ {home}",
        "game_id": g.get("game_id"),
        "venue": g.get("venue"),
        "market": {
            "close_total": close_t,
            "model_total": model_t,
            "total_delta": round(total_delta, 2) if total_delta is not None else None,
            "total_lean": (
                "OVER" if (total_delta is not None and total_delta >= 1.5)
                else "UNDER" if (total_delta is not None and total_delta <= -1.5)
                else "neutral"
            ),
            "close_spread": _f(g.get("close_spread")),
            "model_spread": round(model_spr, 2) if model_spr is not None else None,
            "home_ml": g.get("home_ml_odds"),
            "away_ml": g.get("away_ml_odds"),
        },
        "confluence": {
            "net": g.get("signal_confluence_net"),
            "breakdown": g.get("signal_confluence_breakdown"),
            "primary_play": g.get("primary_play"),
        },
        "situational": {
            "park_run_factor": g.get("park_run_factor"),
            "temperature": g.get("temperature"),
            "wind_speed": g.get("wind_speed"),
            "wind_direction": g.get("wind_direction"),
            "umpire": g.get("umpire"),
            "umpire_note": g.get("umpire_note"),
            "home_bp_relievers_3d": g.get("home_bp_relievers_3d"),
            "away_bp_relievers_3d": g.get("away_bp_relievers_3d"),
            "nrfi_score": g.get("nrfi_score"),
            "nrfi_tier": _nrfi_tier(g.get("nrfi_score")),
            "home_l10_rpg": g.get("home_last10_runs_per_game"),
            "away_l10_rpg": g.get("away_last10_runs_per_game"),
            "home_offense_drift": g.get("home_offense_drift"),
            "away_offense_drift": g.get("away_offense_drift"),
            "home_wrc_plus": g.get("home_wrc_plus"),
            "away_wrc_plus": g.get("away_wrc_plus"),
            "lineup_confirmed": g.get("lineup_confirmed"),
        },
        "pitchers": {"home": _pitcher_block(g, "home"), "away": _pitcher_block(g, "away")},
        "best_plays": best,
        "is_potd": is_potd,
        "meta": {
            "game_date": today_et(),
            "game_has_not_been_played": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }


# ---------------------------------------------------------------- prompt

def render_prompt(templates, g, struct):
    sweat = None  # the sweat-score model isn't in mlb_game_context; let the app keep that, leave blank here
    confidence_tier = "HIGH — MLB model active (pitcher xERA, wOBA, K rate gap, platoon, bullpen, park, weather, umpire)"

    # The pipeline already has the full struct — feed it as the "context block"
    # rather than re-deriving it. Jerry summarizes a fixed JSON struct (less
    # hallucination) instead of free-associating off scattered fields.
    context_block = (
        "MLB PIPELINE CONTEXT (authoritative — analyze this, do not search for scores):\n"
        + json.dumps(struct, indent=2, default=str)
    )

    wrapper = templates["wrapper"]
    filled = (
        wrapper
        .replace("{today_et}", now_et_human())
        .replace("{away_team}", struct["matchup"].split(" @ ")[0])
        .replace("{home_team}", struct["matchup"].split(" @ ")[1])
        .replace("{commence_time_et}", "today")
        .replace("{sport}", "MLB")
        .replace("{sweat_score}", "—")
        .replace("{sweat_tier_label}", "")
        .replace("{spread_str}", str(struct["market"].get("close_spread") or "N/A"))
        .replace("{total_str}", str(struct["market"].get("close_total") or "N/A"))
        .replace("{model_lean}", struct["confluence"].get("breakdown") and json.dumps(struct["confluence"]["breakdown"]) or "neutral")
        .replace("{confidence_tier}", confidence_tier)
        .replace("{tournament_floor_note}", "")
        .replace("{full_score_context}", "")
        .replace("{model_context}", "")
        .replace("{sport_context}", context_block)
        .replace("{sport_rules}", templates["mlb_rules"])
        .replace("{universal_rules}", templates["universal"])
        .replace("{data_quality_note}", "" if struct["situational"].get("lineup_confirmed") else "Note: lineups not yet confirmed — frame projections accordingly.")
    )
    return filled


def call_claude(prompt):
    if not ANTHROPIC_API_KEY:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            json={"model": MODEL, "max_tokens": 1100, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        data = r.json()
        if r.status_code != 200:
            print(f"  ⚠️ claude {r.status_code}: {str(data)[:300]}")
            return None
        return "".join(b.get("text", "") for b in (data.get("content") or []) if b.get("type") == "text").strip() or None
    except Exception as e:
        print(f"  ⚠️ claude call failed: {e}")
        return None


def upsert_read(g, narrative, struct):
    key = f"game_read_{g.get('game_id')}_{today_et()}"
    payload = {
        "game_id": key,
        "cache_key": key,
        "sport": "mlb",
        "narrative": narrative,
        "data": json.dumps(struct, default=str),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=cache_key", headers=SB_WRITE, json=payload, timeout=15)
    if r.status_code not in (200, 201, 204):
        print(f"  ⚠️ upsert failed {r.status_code}: {r.text[:300]}")
        return False
    return True


# ---------------------------------------------------------------- run

def _matches(matchup_key, home, away):
    if not matchup_key:
        return False
    h = (home or "").split()[-1]
    a = (away or "").split()[-1]
    return h in matchup_key and a in matchup_key


def run():
    force = "--force" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        try:
            limit = int(sys.argv[sys.argv.index("--limit") + 1])
        except Exception:
            limit = None

    print(f"=== MLB game reads {today_et()} ===")
    templates = load_templates()
    if not templates:
        print("  ⚠️ could not load prompt templates from prompt_templates table — aborting (run the 20260512_prompt_templates migration)")
        sys.exit(1)

    games = fetch_games()
    if not games:
        print("  No games in mlb_game_context for today.")
        return
    props_by_game = fetch_props_by_game()
    potd = fetch_potd()
    print(f"  {len(games)} games | {sum(len(v) for v in props_by_game.values())} props loaded")

    done = 0
    for g in games:
        home, away = g.get("home_team"), g.get("away_team")
        key = f"game_read_{g.get('game_id')}_{today_et()}"
        if not force:
            existing = sb_get("jerry_cache", {"cache_key": f"eq.{key}", "select": "cache_key"})
            if existing:
                print(f"  • {away} @ {home}: exists, skip (--force to regen)")
                continue
        props = next((v for k, v in props_by_game.items() if _matches(k, home, away)), [])
        struct = build_struct(g, props, potd)
        prompt = render_prompt(templates, g, struct)
        narrative = call_claude(prompt)
        if not narrative:
            print(f"  • {away} @ {home}: no narrative (claude failed / no key) — storing struct only")
        if upsert_read(g, narrative or "", struct):
            print(f"  ✓ {away} @ {home} ({len(struct['best_plays'])} plays{', POTD' if struct['is_potd'] else ''})")
            done += 1
        if limit and done >= limit:
            break

    print(f"=== wrote {done} game reads ===")


if __name__ == "__main__":
    run()
