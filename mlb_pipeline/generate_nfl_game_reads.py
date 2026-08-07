"""
NFL game reads — server-side Jerry. Lighter struct than MLB/NBA because
NFL Phase 2 (per-game picks pipeline) hasn't shipped yet — driven by Odds API
markets + `nfl_team_stats` season-long EPA/efficiency only. Will get richer
when Phase 2 lands (just extend build_struct).

For each NFL game in the next 8 days, assembles market context (spread, total,
ML) and per-team EPA/efficiency, feeds it to Claude with the NFL prompt
template, writes {narrative, struct} to jerry_cache keyed
game_read_<odds-api-id>_<ET date>, sport='nfl'.

Usage: python generate_nfl_game_reads.py [--force] [--limit N]
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
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

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


def load_templates():
    rows = sb_get("prompt_templates", {
        "name": "in.(game_read_wrapper,game_read_universal,game_read_rules)",
        "is_active": "is.true",
        "select": "name,sport,template",
    })
    out = {(r["name"], r["sport"]): r["template"] for r in rows}
    wrapper = out.get(("game_read_wrapper", "ALL"))
    universal = out.get(("game_read_universal", "ALL"))
    rules = out.get(("game_read_rules", "NFL")) or out.get(("game_read_rules", "NHL"))  # NFL falls back to market template until Phase 2
    if not (wrapper and universal and rules):
        print(f"  ⚠️ missing template rows — have: {list(out.keys())}")
        return None
    return {"wrapper": wrapper, "universal": universal, "rules": rules}


def fetch_odds_games():
    if not ODDS_API_KEY:
        print("  No ODDS_API_KEY — can't fetch NFL slate")
        return []
    r = requests.get(
        "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds",
        params={"apiKey": ODDS_API_KEY, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "american"},
        timeout=20,
    )
    if r.status_code != 200:
        print(f"  Odds API error: {r.status_code}")
        return []
    return r.json()


def fetch_team_stats():
    rows = sb_get("nfl_team_stats", {"select": "*"})
    return {r.get("team"): r for r in rows}


def median(arr):
    return sorted(arr)[len(arr) // 2] if arr else None


def extract_market(game):
    spreads, totals, hmls, amls = [], [], [], []
    for bm in game.get("bookmakers", []):
        for mkt in bm.get("markets", []):
            if mkt["key"] == "spreads":
                home = next((o for o in mkt["outcomes"] if o.get("name") == game.get("home_team")), None)
                if home and home.get("point") is not None:
                    spreads.append(home["point"])
            elif mkt["key"] == "totals":
                t = (mkt.get("outcomes") or [None])[0]
                if t and t.get("point") is not None:
                    totals.append(t["point"])
            elif mkt["key"] == "h2h":
                for o in mkt.get("outcomes") or []:
                    if o.get("name") == game.get("home_team"):
                        hmls.append(o.get("price"))
                    elif o.get("name") == game.get("away_team"):
                        amls.append(o.get("price"))
    return median(spreads), median(totals), median(hmls), median(amls)


def _team(stats, name):
    if not name:
        return {}
    if name in stats:
        return stats[name]
    last = name.split()[-1]
    return next((v for k, v in stats.items() if (k or "").split()[-1] == last), {}) or {}


def _build_casual_summary(struct):
    """NFL casual summary — lean since Phase 2 (per-game picks) hasn't shipped.
    Mostly market context + season EPA snapshot until then."""
    headlines = []
    m = struct.get("market") or {}
    eh = (struct.get("efficiency") or {}).get("home") or {}
    ea = (struct.get("efficiency") or {}).get("away") or {}
    away, home = (struct.get("matchup") or " @ ").split(" @ ")[0], (struct.get("matchup") or " @ ").split(" @ ")[-1]

    headlines.append((1, "ℹ Working from season EPA only — per-game model coming Phase 2"))

    # Pass EPA gap
    for label, val_h, val_a in [
        ("pass offense", eh.get("pass_epa"), ea.get("pass_epa")),
        ("rush offense", eh.get("rush_epa"), ea.get("rush_epa")),
    ]:
        if val_h is not None and val_a is not None:
            gap = float(val_h) - float(val_a)
            if abs(gap) >= 0.10:
                team = home if gap > 0 else away
                headlines.append((6, f"✓ {team} has the edge in {label} (EPA gap {abs(gap):.2f})"))

    # Defensive sacks / INTs
    for label, side, t in [("home", eh, home), ("away", ea, away)]:
        if side.get("def_ints") and int(side["def_ints"]) >= 12:
            headlines.append((4, f"✓ {t} defense forces turnovers ({side['def_ints']} INTs)"))

    # Market snapshot
    if m.get("spread") is not None:
        headlines.append((3, f"📊 Market: {home} {'+' if m['spread']>0 else ''}{m['spread']}, total {m.get('total','N/A')}"))

    headlines.sort(key=lambda x: -x[0])
    top = [h[1] for h in headlines[:4]]

    bottom = "Phase 2 NFL game model not active — market + season EPA only"
    return {"headlines": top, "bottom_line": bottom}


def build_struct(game, stats):
    home, away = game.get("home_team"), game.get("away_team")
    h, a = _team(stats, home), _team(stats, away)
    spread, total, hml, aml = extract_market(game)

    def eff(t):
        return {
            "games": t.get("games"),
            "pass_epa": _f(t.get("pass_epa")),
            "rush_epa": _f(t.get("rush_epa")),
            "pass_cpoe": _f(t.get("pass_cpoe")),
            "sacks_suffered": t.get("sacks_suffered"),
            "def_sacks": t.get("def_sacks"),
            "def_ints": t.get("def_ints"),
            "fg_pct": _f(t.get("fg_pct")),
        }

    struct = {
        "matchup": f"{away} @ {home}",
        "game_id": game.get("id"),
        "commence_time": game.get("commence_time"),
        "market": {"spread": spread, "total": total, "home_ml": hml, "away_ml": aml},
        "efficiency": {"home": eff(h), "away": eff(a)},
        "meta": {
            "game_date": today_et(),
            "game_has_not_been_played": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "phase": "Phase 1 — market + season EPA only; per-game model picks pending Phase 2",
        },
    }
    struct["casual_summary"] = _build_casual_summary(struct)
    return struct


def render_prompt(templates, struct):
    confidence_tier = "MARKET — NFL Phase 2 model not active yet. Working from market lines + season EPA/efficiency."
    context_block = (
        "NFL GAME CONTEXT (authoritative — analyze this, do not search for scores):\n"
        + json.dumps(struct, indent=2, default=str)
    )
    m = struct["market"]
    away, home = struct["matchup"].split(" @ ")
    return (
        templates["wrapper"]
        .replace("{today_et}", now_et_human())
        .replace("{away_team}", away)
        .replace("{home_team}", home)
        .replace("{commence_time_et}", struct.get("commence_time") or "soon")
        .replace("{sport}", "NFL")
        .replace("{sweat_score}", "—")
        .replace("{sweat_tier_label}", "")
        .replace("{spread_str}", str(m.get("spread") or "N/A"))
        .replace("{total_str}", str(m.get("total") or "N/A"))
        .replace("{model_lean}", "no game model active — market-based")
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
    if not ANTHROPIC_API_KEY:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
            json={"model": MODEL, "max_tokens": 800, "messages": [{"role": "user", "content": prompt}]},
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


def parse_nfl_synthesis(raw: str) -> dict:
    """Parse NFL Jerry LLM output (2026-08-06). Mirrors MLB's parse_synthesis
    from generate_jerry_synthesis.py — same SHORT/LONG/CALL contract now
    that seed_nfl_game_read_prompt has been updated.

    Returns:
      {
        'short_read': str,
        'long_read': str,
        'call_market': str | None,      # ml/spread/total/pass
        'call_side': str | None,        # HOME/AWAY/OVER/UNDER
        'call_line': float | None,
        'call_text': str | None,
        'conviction': int | None,       # 0-100
      }

    Falls back gracefully on malformed output — missing CALL block just
    means we store prose only (like pre-Phase 2 behavior).
    """
    import re as _re
    def _section(name):
        m = _re.search(rf"---{name}---\s*(.*?)(?=---[A-Z]+---|$)", raw, _re.S)
        return m.group(1).strip() if m else None

    short = _section("SHORT") or ""
    long_ = _section("LONG") or ""
    call_block = _section("CALL") or ""

    # Strip markdown for robust field extraction (Jerry sometimes writes **MARKET:** **ml**)
    call_block = _re.sub(r"\*+", "", call_block)
    call_block = _re.sub(r"_+", "", call_block)

    def _field(field):
        m = _re.search(rf"\**{field}\**\s*:\s*(.+?)(?=\n\**[A-Z_]+\**\s*:|$)",
                        call_block, _re.S)
        if not m: return None
        val = m.group(1).strip()
        val = _re.sub(r"^[*_\s]+|[*_\s]+$", "", val)
        return val or None

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
        conviction = max(0, min(100, int(_re.sub(r"\D", "", conv_raw or "")))) if conv_raw else None
    except ValueError:
        conviction = None

    _VALID_MARKETS = {'ml', 'spread', 'rl', 'total', 'prop', 'lean', 'pass', None}
    if market not in _VALID_MARKETS:
        print(f"  ⚠ parser invalid NFL call_market {market!r} — nulling")
        market = None; side = None
    _VALID_SIDES = {'HOME', 'AWAY', 'OVER', 'UNDER', None}
    if side not in _VALID_SIDES:
        print(f"  ⚠ parser invalid NFL call_side {side!r} — nulling")
        side = None

    return {
        "short_read": short,
        "long_read": long_,
        "call_market": market,
        "call_side": side,
        "call_line": line,
        "call_text": call_text,
        "conviction": conviction,
    }


def upsert_jerry_read_nfl(game, struct, parsed, narrative):
    """Write structured NFL Jerry read to jerry_reads table (2026-08-06 Phase 2).
    Uses (sport, game_id, game_date) unique key. This is what the sweat card
    queries for game-side picks — parity with MLB path."""
    game_id = game.get('id')  # Odds API game id
    # commence_time to game_date ET
    ct = game.get('commence_time', '')[:10] or today_et()
    payload = {
        'sport': 'NFL',
        'game_id': game_id,
        'game_date': ct,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'prompt_version': 'nfl_game_read_v2_2026-08-06',
        'input_snapshot': {'source': 'generate_nfl_game_reads', 'matchup': struct.get('matchup')},
        'short_read': parsed.get('short_read') or narrative[:500],
        'long_read': parsed.get('long_read') or narrative,
        'call_text': parsed.get('call_text'),
        'call_market': parsed.get('call_market'),
        'call_side': parsed.get('call_side'),
        'call_line': parsed.get('call_line'),
        'call_odds_est': None,
        'conviction': parsed.get('conviction') or 0,
    }
    r = requests.post(
        f'{SUPABASE_URL}/rest/v1/jerry_reads?on_conflict=sport,game_id,game_date',
        headers={**SB_WRITE, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
        json=payload, timeout=15,
    )
    if r.status_code not in (200, 201, 204):
        print(f"  ⚠️ jerry_reads upsert failed {r.status_code}: {r.text[:200]}")
        return False
    return True


def upsert_read(game, struct, narrative, parsed=None):
    key = f"game_read_{game.get('id')}_{today_et()}"
    payload = {
        "game_id": key,
        "cache_key": key,
        "sport": "nfl",
        "narrative": narrative,
        "data": json.dumps(struct, default=str),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=cache_key", headers=SB_WRITE, json=payload, timeout=15)
    ok = r.status_code in (200, 201, 204)
    if not ok:
        print(f"  ⚠️ jerry_cache upsert failed {r.status_code}: {r.text[:300]}")

    # DUAL-WRITE (2026-08-06 Phase 2): also write structured pick to
    # jerry_reads so sweat card can rank it alongside MLB Jerry picks
    # by conviction. This is the missing piece that had NFL sitting at
    # "prose-only, no structured selection" until now.
    if parsed and parsed.get('short_read'):
        upsert_jerry_read_nfl(game, struct, parsed, narrative or '')

    return ok


def run():
    force = "--force" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        try:
            limit = int(sys.argv[sys.argv.index("--limit") + 1])
        except Exception:
            limit = None

    print(f"=== NFL game reads {today_et()} ===")
    templates = load_templates()
    if not templates:
        sys.exit(1)

    games = fetch_odds_games()
    if not games:
        print("  No NFL games on the slate (offseason / no odds available).")
        return
    # Filter to next 8 days only (regular season scope)
    cutoff = datetime.now(timezone.utc) + timedelta(days=8)
    games = [g for g in games if g.get("commence_time") and g["commence_time"] <= cutoff.isoformat()]
    if not games:
        print("  No NFL games in the next 8 days.")
        return

    stats = fetch_team_stats()
    print(f"  {len(games)} game(s) | {len(stats)} team stat rows")

    done = 0
    for g in games:
        struct = build_struct(g, stats)
        away, home = struct["matchup"].split(" @ ")
        key = f"game_read_{g.get('id')}_{today_et()}"
        if not force:
            if sb_get("jerry_cache", {"cache_key": f"eq.{key}", "select": "cache_key"}):
                print(f"  • {away} @ {home}: exists, skip")
                continue
        prompt = render_prompt(templates, struct)
        narrative = call_claude(prompt)
        if not narrative:
            print(f"  • {away} @ {home}: no narrative — struct only")
        parsed = parse_nfl_synthesis(narrative) if narrative else {}
        if upsert_read(g, struct, narrative or "", parsed=parsed):
            call_str = ''
            if parsed.get('call_market'):
                call_str = f" · {parsed.get('call_text') or parsed['call_market']} ({parsed.get('conviction') or '-'})"
            print(f"  ✓ {away} @ {home}{call_str}")
            done += 1
        if limit and done >= limit:
            break

    print(f"=== wrote {done} NFL game reads ===")


if __name__ == "__main__":
    run()
