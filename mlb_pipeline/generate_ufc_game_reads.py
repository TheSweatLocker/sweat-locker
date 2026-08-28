"""
UFC game (fight) reads — server-side Jerry, parallels MLB/NBA versions.

For each fight on the upcoming card, assembles a structured read from
`ufc_picks` (per-fight model output: winner %, method probabilities, round
probabilities, edges, tier) + recent form from `ufc_fighter_history`,
feeds it to Claude with the UFC `game_read_*` prompt templates, and writes
{narrative, struct} to `jerry_cache` keyed game_read_ufc_<a-slug>_<b-slug>_<date>,
sport='ufc'.

Runs weekly during fight week (Thursdays-Sundays); idempotent on rerun.

Usage: python generate_ufc_game_reads.py [--force] [--limit N]
"""
import os
import re
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


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def sb_get(path, params=None):
    qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = f"{SUPABASE_URL}/rest/v1/{path}{'?' if qs else ''}{qs}"
    r = requests.get(url, headers=SB_READ, timeout=20)
    return r.json() if r.status_code == 200 else []


def load_templates():
    rows = sb_get("prompt_templates", {
        "name": "in.(game_read_wrapper,game_read_universal,game_read_rules)",
        "is_active": "is.true",
        "select": "name,sport,template",
    })
    out = {(r["name"], r["sport"]): r["template"] for r in rows}
    wrapper = out.get(("game_read_wrapper", "ALL"))
    universal = out.get(("game_read_universal", "ALL"))
    rules = out.get(("game_read_rules", "UFC"))
    if not (wrapper and universal and rules):
        print(f"  ⚠️ missing template rows — have: {list(out.keys())}")
        return None
    return {"wrapper": wrapper, "universal": universal, "rules": rules}


def fetch_upcoming_fights():
    today = today_et()
    # next 8 days of upcoming UFC fights
    horizon = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=8)).strftime("%Y-%m-%d")
    rows = sb_get("ufc_picks", {
        "event_date": f"gte.{today}",
        "select": "*",
        "order": "event_date.asc,fight_order.asc",
        "limit": "60",
    })
    return [r for r in rows if r.get("event_date", "9999") <= horizon]


def fetch_recent_form(fighter_name, limit=3):
    if not fighter_name:
        return []
    return sb_get("ufc_fighter_history", {
        "fighter_name": f"eq.{fighter_name}",
        "select": "fight_date,opponent_name,result,method,round,sig_strikes_landed,sig_strikes_attempted,takedowns_landed,takedowns_attempted",
        "order": "fight_date.desc",
        "limit": str(limit),
    })


def _build_casual_summary(struct):
    """Plain-English UFC headlines + bottom line."""
    headlines = []
    mo = struct.get("model") or {}
    methods = struct.get("method_probs") or {}
    rounds = struct.get("round_probs") or {}
    a, b = (struct.get("matchup") or " vs ").split(" vs ")
    pick = mo.get("winner_pick")

    if pick and mo.get("winner_prob"):
        wp = int(float(mo["winner_prob"]) * 100)
        tier = mo.get("tier") or ""
        headlines.append((15, f"✓ Model favors {pick} to win ({wp}% — {tier})" if tier else f"✓ Model favors {pick} ({wp}% win prob)"))

    # Method
    if methods:
        top_m = max(methods, key=methods.get)
        top_v = methods[top_m]
        if top_v >= 0.4:
            name = {"ko": "KO/TKO", "sub": "Submission", "dec": "Decision"}.get(top_m, top_m.upper())
            headlines.append((10, f"✓ Most likely method: {name} ({int(top_v*100)}%)"))

    # Edges vs market
    if mo.get("edge_method"):
        headlines.append((8, f"✓ Method-prop edge vs market: {mo['edge_method']}"))
    if mo.get("edge_distance"):
        headlines.append((8, f"✓ Goes-distance edge vs market: {mo['edge_distance']}"))

    # Recent form (3+ in a row)
    for name, hist in (struct.get("recent") or {}).items():
        if not hist:
            continue
        results = [str(h.get("result") or "").lower() for h in hist[:3]]
        if all(r.startswith("w") for r in results) and len(results) == 3:
            headlines.append((6, f"✓ {name} on a 3-fight win streak"))
        elif all(r.startswith("l") for r in results) and len(results) == 3:
            headlines.append((7, f"⚠ {name} dropped 3 of last 3"))

    headlines.sort(key=lambda x: -x[0])
    top = [h[1] for h in headlines[:4]]

    bottom = None
    if pick and mo.get("tier"):
        wp = int(float(mo.get("winner_prob") or 0) * 100)
        bottom = f"Model pick: {pick} to win ({wp}%, {mo['tier']})"
    elif pick:
        bottom = f"Model leans: {pick}"
    if not bottom:
        bottom = "Pick'em fight — no strong model edge"

    return {"headlines": top, "bottom_line": bottom}


def build_struct(p):
    a, b = p["fighter_a"], p["fighter_b"]
    pwa = float(p.get("p_winner_a") or 0.5)
    side = p.get("recommended_side")  # 'a' or 'b' or null
    picked = a if side == "a" else (b if side == "b" else None)
    win_prob = pwa if side == "a" else (1 - pwa)

    methods = {
        "ko": float(p.get("p_method_ko") or 0),
        "sub": float(p.get("p_method_sub") or 0),
        "dec": float(p.get("p_method_dec") or 0),
    }
    rounds = {f"r{i}": float(p.get(f"p_round_{i}") or 0) for i in range(1, 6)}

    struct = {
        "matchup": f"{a} vs {b}",
        "event": p.get("event_name"),
        "event_date": p.get("event_date"),
        "fight_order": p.get("fight_order"),
        "model": {
            "winner_pick": picked,
            "winner_prob": round(win_prob, 3) if picked else None,
            "p_winner_a": round(pwa, 3),
            "conviction": p.get("conviction_winner"),
            "tier": p.get("tier_winner"),
            "edge_method": p.get("edge_method"),
            "edge_distance": p.get("edge_distance"),
        },
        "method_probs": {k: round(v, 3) for k, v in methods.items()},
        "round_probs": {k: round(v, 3) for k, v in rounds.items()},
        "p_distance": round(float(p.get("p_distance") or 0), 3),
        "recent": {
            a: fetch_recent_form(a),
            b: fetch_recent_form(b),
        },
        "meta": {
            "game_date": p.get("event_date"),
            "game_has_not_been_played": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    struct["casual_summary"] = _build_casual_summary(struct)
    return struct


def render_prompt(templates, struct):
    confidence_tier = "MODERATE — fighter stats + per-fight model (winner %, method, rounds) + public analyst consensus"
    context_block = (
        "UFC FIGHT CONTEXT (authoritative — analyze this, do not search for scores):\n"
        + json.dumps(struct, indent=2, default=str)
    )
    m = struct["model"]
    lean = (f"{m['winner_pick']} ({m['tier']} {m.get('conviction','')}, {int((m.get('winner_prob') or 0)*100)}%)"
            if m.get("winner_pick") else "pick'em")
    return (
        templates["wrapper"]
        .replace("{today_et}", now_et_human())
        .replace("{away_team}", struct["matchup"].split(" vs ")[0])
        .replace("{home_team}", struct["matchup"].split(" vs ")[1])
        .replace("{commence_time_et}", struct.get("event_date") or "soon")
        .replace("{sport}", "UFC")
        .replace("{sweat_score}", "—")
        .replace("{sweat_tier_label}", "")
        .replace("{spread_str}", "N/A")
        .replace("{total_str}", f"distance {struct.get('p_distance', 0):.2f}")
        .replace("{model_lean}", lean)
        .replace("{confidence_tier}", confidence_tier)
        .replace("{tournament_floor_note}", "")
        .replace("{full_score_context}", "")
        .replace("{model_context}", "")
        .replace("{sport_context}", context_block)
        .replace("{sport_rules}", templates["rules"])
        .replace("{universal_rules}", templates["universal"])
        .replace("{data_quality_note}", "")
    )


def call_claude(prompt):
    # 2026-08-28: LLM disabled per docs/LLM_AUDIT.md kill #3.
    # Duplicate of generate_ufc_fight_synthesis.py which is now the sole
    # source of UFC jerry_reads. Legacy jerry_cache narrative derives from
    # fight_synthesis's short_read via app-side join.
    if os.environ.get('DISABLE_LEGACY_GAME_READS_LLM', '1') == '1':
        return None
    if not ANTHROPIC_API_KEY:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
            json={"model": MODEL, "max_tokens": 900, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        data = r.json()
        if r.status_code != 200:
            print(f"  ⚠️ claude {r.status_code}: {str(data)[:300]}")
            return None
        return "".join(b.get("text", "") for b in (data.get("content") or []) if b.get("type") == "text").strip() or None
    except Exception as e:
        print(f"  ⚠️ claude failed: {e}")
        return None


def upsert_read(struct, narrative):
    a, b = struct["matchup"].split(" vs ")
    key = f"game_read_ufc_{_slug(a)}_{_slug(b)}_{struct.get('event_date')}"
    payload = {
        "game_id": key,
        "cache_key": key,
        "sport": "UFC",  # 2026-08-25 case fix — matches sport_registry convention
        "narrative": narrative,
        "data": json.dumps(struct, default=str),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    # 2026-08-23: fixed on_conflict from cache_key to game_id,sport
    r = requests.post(f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=game_id,sport", headers=SB_WRITE, json=payload, timeout=15)
    if r.status_code not in (200, 201, 204):
        print(f"  ⚠️ upsert failed {r.status_code}: {r.text[:300]}")
        return False, key
    return True, key


def run():
    force = "--force" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        try:
            limit = int(sys.argv[sys.argv.index("--limit") + 1])
        except Exception:
            limit = None

    print(f"=== UFC fight reads {today_et()} ===")
    templates = load_templates()
    if not templates:
        print("  ⚠️ template rows missing — aborting")
        sys.exit(1)

    fights = fetch_upcoming_fights()
    if not fights:
        print("  No upcoming UFC fights in next 8 days.")
        return
    print(f"  {len(fights)} upcoming fight(s)")

    done = 0
    for p in fights:
        struct = build_struct(p)
        a, b = struct["matchup"].split(" vs ")
        key = f"game_read_ufc_{_slug(a)}_{_slug(b)}_{struct.get('event_date')}"
        if not force:
            if sb_get("jerry_cache", {"cache_key": f"eq.{key}", "select": "cache_key"}):
                print(f"  • {a} vs {b} ({struct.get('event_date')}): exists, skip")
                continue
        prompt = render_prompt(templates, struct)
        narrative = call_claude(prompt)
        if not narrative:
            print(f"  • {a} vs {b}: no narrative — struct only")
        ok, _ = upsert_read(struct, narrative or "")
        if ok:
            print(f"  ✓ {a} vs {b} ({struct['event_date']}, tier={struct['model'].get('tier')})")
            done += 1
        if limit and done >= limit:
            break

    print(f"=== wrote {done} UFC fight reads ===")


if __name__ == "__main__":
    run()
