"""
NBA game reads — server-side Jerry (companion to generate_mlb_game_reads.py).

For each NBA game on today's slate, assembles a structured read-context from
pipeline data (model-vs-market spread/total edge, net-rating gap, efficiency
splits, recency drift, injuries / star-OUT, the game's picks), feeds it to
Claude with prompt templates pulled from `prompt_templates`, and stores
{narrative, struct} in `jerry_cache` keyed game_read_<game_id>_<ET date>,
sport='nba'. game_id matches the Odds API event id the app uses.

Run after nba_picks_generator.py (needs nba_game_picks + nba_team_stats).

Usage: python generate_nba_game_reads.py [--force] [--limit N]
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


def _is_nba_playoffs_now():
    """NBA playoffs run roughly mid-April through late June.
    Date-based heuristic — simple and good enough for the playoff flag
    until we have a real "is_playoff_game" field from a games API.

    Returns True when the current ET date is in the playoff window.
    Stays True well past Finals to avoid false-negative tail edge cases;
    regular-season starts late October, so any May/June/July date is
    safely classified as 'not regular season.'"""
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    m = et.month
    d = et.day
    if m in (5, 6, 7):
        return True
    if m == 4 and d >= 13:
        return True
    return False


# Module-level cache for the NBA team-name → balldontlie team_id map.
# Populated on first call; one balldontlie API hit per process.
_NBA_TEAM_ID_CACHE = None


def _fetch_nba_team_ids():
    """Lazy-load and cache the full team-name → balldontlie team_id map.
    Maps e.g. 'New York Knicks' → 20. Used by _fetch_series_state to
    translate the team names in the struct into the IDs balldontlie wants.
    Returns {} on failure (caller defaults to series_state=None)."""
    global _NBA_TEAM_ID_CACHE
    if _NBA_TEAM_ID_CACHE is not None:
        return _NBA_TEAM_ID_CACHE
    bdl_key = os.environ.get("BDL_API_KEY")
    if not bdl_key:
        _NBA_TEAM_ID_CACHE = {}
        return _NBA_TEAM_ID_CACHE
    try:
        r = requests.get(
            "https://api.balldontlie.io/nba/v1/teams",
            headers={"Authorization": bdl_key},
            timeout=15,
        )
        teams = r.json().get("data", [])
        _NBA_TEAM_ID_CACHE = {t.get("full_name"): t.get("id") for t in teams if t.get("full_name") and t.get("id")}
        print(f"  Loaded {len(_NBA_TEAM_ID_CACHE)} NBA team IDs from balldontlie")
    except Exception as e:
        print(f"  ⚠️ balldontlie team-id fetch failed: {e}")
        _NBA_TEAM_ID_CACHE = {}
    return _NBA_TEAM_ID_CACHE


def _fetch_series_state(away_team, home_team):
    """Pull the current playoff series state between two teams.

    Queries balldontlie for postseason games this season involving the home
    team, filters to games where the away team was the opponent, then
    aggregates the W-L record from each side.

    Series detection rules:
      - Only counts postseason games (postseason=true on balldontlie)
      - Only counts games in last 21 days (covers max 7-game series window)
      - Only counts games with status='Final'
      - Returns None if zero qualifying games (first game of series not yet
        played, OR no playoff series between these teams)

    Output shape:
      {
        'home_wins': int,
        'away_wins': int,
        'games_played': int,
        'next_game_num': int,
        'leader': str | 'TIED',
        'lead_margin': int,
        'elimination_for': str | None,  # team facing elimination (down 3)
        'series_summary': str,           # human-readable summary
      }
    """
    bdl_key = os.environ.get("BDL_API_KEY")
    if not bdl_key:
        return None
    team_map = _fetch_nba_team_ids()
    home_id = team_map.get(home_team)
    away_id = team_map.get(away_team)
    if not home_id or not away_id:
        return None

    # Season convention: balldontlie uses the starting year of the season.
    # NBA 2025-26 season → season=2025. May/June 2026 are the playoffs of
    # the 2025-26 season → still season=2025.
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    season = et.year - 1 if et.month < 10 else et.year

    try:
        r = requests.get(
            "https://api.balldontlie.io/nba/v1/games",
            headers={"Authorization": bdl_key},
            params={
                "seasons[]": season,
                "team_ids[]": home_id,
                "postseason": "true",
                "per_page": 100,
            },
            timeout=15,
        )
        games = r.json().get("data", []) if r.status_code == 200 else []
    except Exception as e:
        print(f"  ⚠️ balldontlie series fetch failed for {away_team} @ {home_team}: {e}")
        return None

    # Filter to games where BOTH teams played (away was the opponent),
    # game is finalized, and in the last 21 days (a current series).
    today = et.date()
    qualifying = []
    for g in games:
        gh = (g.get("home_team") or {}).get("id")
        gv = (g.get("visitor_team") or {}).get("id")
        if not (
            (gh == home_id and gv == away_id) or (gh == away_id and gv == home_id)
        ):
            continue
        if (g.get("status") or "").lower() not in ("final", "final/ot"):
            continue
        date_str = (g.get("date") or "").split("T")[0]
        try:
            game_date = datetime.fromisoformat(date_str).date()
        except Exception:
            continue
        if (today - game_date).days > 21:
            continue
        qualifying.append(g)

    if not qualifying:
        return None  # first game of series tonight OR no series

    # Count W-L from each side's perspective.
    home_wins = 0
    away_wins = 0
    for g in qualifying:
        h_score = g.get("home_team_score") or 0
        v_score = g.get("visitor_team_score") or 0
        if h_score == v_score:
            continue
        gh = (g.get("home_team") or {}).get("id")
        winner_id = gh if h_score > v_score else (g.get("visitor_team") or {}).get("id")
        if winner_id == home_id:
            home_wins += 1
        elif winner_id == away_id:
            away_wins += 1

    games_played = home_wins + away_wins
    if games_played == 0:
        return None

    if home_wins > away_wins:
        leader = home_team
        lead_margin = home_wins - away_wins
    elif away_wins > home_wins:
        leader = away_team
        lead_margin = away_wins - home_wins
    else:
        leader = "TIED"
        lead_margin = 0

    elimination_team = None
    if home_wins == 3:
        elimination_team = away_team
    elif away_wins == 3:
        elimination_team = home_team

    if leader == "TIED":
        summary = f"Series tied {home_wins}-{away_wins}"
    else:
        summary = f"{leader} leads {max(home_wins, away_wins)}-{min(home_wins, away_wins)}"

    return {
        "home_wins": home_wins,
        "away_wins": away_wins,
        "games_played": games_played,
        "next_game_num": games_played + 1,
        "leader": leader,
        "lead_margin": lead_margin,
        "elimination_for": elimination_team,
        "series_summary": summary,
    }


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
    if not rows:
        return None
    out = {(r["name"], r["sport"]): r["template"] for r in rows}
    wrapper = out.get(("game_read_wrapper", "ALL"))
    universal = out.get(("game_read_universal", "ALL"))
    rules = out.get(("game_read_rules", "NBA"))
    if not (wrapper and universal and rules):
        print(f"  ⚠️ missing template rows — have keys: {list(out.keys())}")
        return None
    return {"wrapper": wrapper, "universal": universal, "rules": rules}


def fetch_picks_by_game():
    rows = sb_get("nba_game_picks", {"game_date": f"eq.{today_et()}", "select": "*", "order": "conviction.desc.nullslast"})
    by_game = {}
    for p in rows:
        by_game.setdefault(p.get("game_id"), []).append(p)
    return by_game


def fetch_team_stats():
    rows = sb_get("nba_team_stats", {"select": "*"})
    return {r.get("team"): r for r in rows}


def _team(stats, name):
    if not name:
        return {}
    if name in stats:
        return stats[name]
    last = name.split()[-1]
    return next((v for k, v in stats.items() if k.split()[-1] == last), {}) or {}


def _build_casual_summary(struct):
    """Plain-English headlines + bottom line for NBA. Same pattern as MLB —
    rank signals by strength, surface top 3-4."""
    headlines = []
    m = struct.get("market") or {}
    mo = struct.get("model") or {}
    eh = (struct.get("efficiency") or {}).get("home") or {}
    ea = (struct.get("efficiency") or {}).get("away") or {}
    inj = struct.get("injuries") or {}
    away, home = (struct.get("matchup") or "").split(" @ ")[0], (struct.get("matchup") or "").split(" @ ")[-1]

    # 1. Star OUT — pre-empts everything
    if inj.get("star_out"):
        note = inj.get("star_out_note") or "key player out"
        headlines.append((20, f"⚠ {note} — model leans suppressed; line already adjusted"))

    # 2. ATS edge
    edge = mo.get("model_edge_vs_spread")
    if edge is not None and abs(float(edge)) >= 3.0:
        team = home if float(edge) > 0 else away
        headlines.append((10 + min(8, abs(float(edge))), f"✓ Model has {team} {abs(float(edge)):.1f} points stronger than the line"))

    # 3. Net rating gap
    nh, na = eh.get("net_rating"), ea.get("net_rating")
    if nh is not None and na is not None:
        gap = float(nh) - float(na)
        if abs(gap) >= 4:
            team = home if gap > 0 else away
            headlines.append((6 + min(5, abs(gap) / 2), f"✓ {team} is {abs(gap):.1f} points better in net efficiency this season"))

    # 4. Recency drift
    hd, ad = mo.get("home_drift"), mo.get("away_drift")
    for team, drift in [(home, hd), (away, ad)]:
        if drift is not None and float(drift) <= -3:
            headlines.append((5, f"⚠ {team} has cooled (L10 net rating dropped {drift:.1f})"))
        elif drift is not None and float(drift) >= 3:
            headlines.append((5, f"✓ {team} trending up (L10 net rating up {drift:+.1f})"))

    # 5. Total lean
    if mo.get("total_lean"):
        lean = str(mo["total_lean"]).upper()
        tot_tier = mo.get("total_tier")
        tier_suffix = f" ({tot_tier})" if tot_tier else ""
        headlines.append((6, f"✓ {lean} lean on the total{tier_suffix}"))

    # 6. Home/away record asymmetry
    if eh.get("home_record") and ea.get("away_record"):
        try:
            h_w, h_l = map(int, str(eh["home_record"]).split("-"))
            a_w, a_l = map(int, str(ea["away_record"]).split("-"))
            h_pct = h_w / max(1, h_w + h_l)
            a_pct = a_w / max(1, a_w + a_l)
            if h_pct >= 0.70 and a_pct <= 0.40:
                headlines.append((6, f"✓ Home dominance: {home} {eh['home_record']} home / {away} {ea['away_record']} road"))
        except Exception:
            pass

    headlines.sort(key=lambda x: -x[0])
    top = [h[1] for h in headlines[:4]]

    # Bottom line
    bottom = None
    if mo.get("ats_lean") and mo.get("ats_tier"):
        team = home if mo["ats_lean"] == "home" else away
        bottom = f"Model's ATS lean: {team} ({mo['ats_tier']})"
    elif mo.get("total_lean") and mo.get("total_tier"):
        bottom = f"Model's total lean: {str(mo['total_lean']).upper()} ({mo['total_tier']})"
    elif inj.get("star_out"):
        bottom = "Star OUT — model is sidelining this game"
    if not bottom:
        bottom = "Mixed signals — no strong directional edge"

    return {"headlines": top, "bottom_line": bottom}


def build_struct(game_id, picks, stats):
    if not picks:
        return None
    p0 = picks[0]
    home, away = p0.get("home_team"), p0.get("away_team")
    h, a = _team(stats, home), _team(stats, away)

    ats = next((p for p in picks if p.get("pick_type") == "ats"), None)
    tot = next((p for p in picks if p.get("pick_type") == "total"), None)
    star_out = next((p for p in picks if p.get("pick_type") == "star_out_skip"), None)

    spread = _f(p0.get("market_spread"))
    nr_gap = _f(p0.get("net_rating_gap"))
    model_edge = _f(ats.get("model_edge")) if ats else (round(nr_gap + spread, 2) if (nr_gap is not None and spread is not None) else None)

    def eff(t):
        return {
            "net_rating": _f(t.get("net_rating")),
            "off_rating": _f(t.get("offensive_rating")),
            "def_rating": _f(t.get("defensive_rating")),
            "efg_pct": _f(t.get("efg_pct")),
            "pace": _f(t.get("pace")),
            "l10_net_rating": _f(t.get("last_10_net_rating")),
            "home_record": t.get("home_record"),
            "away_record": t.get("away_record"),
            "injury_note": t.get("injury_note"),
        }

    best = []
    for p in picks:
        if p.get("pick_type") == "star_out_skip":
            continue
        sig = p.get("signals") or {}
        best.append({
            "pick_type": p.get("pick_type"),
            "label": p.get("pick_label"),
            "tier": p.get("tier"),
            "conviction": p.get("conviction"),
            "model_edge": p.get("model_edge"),
            "why": [f"{k}: {v}" for k, v in sig.items() if not str(k).startswith("_")][:4],
        })

    # Playoff context (added 2026-05-27, series_state wired 2026-05-27).
    # The efficiency block carries home_record/away_record/net_rating fields
    # populated by nba_pipeline.py from balldontlie — these are REGULAR-SEASON
    # totals. During playoffs, presenting them in present tense ("Cleveland
    # 34-7 at home this year") is misleading; they're a historical baseline,
    # not current-state data.
    #
    # _fetch_series_state pulls the actual W-L record from balldontlie's
    # postseason games endpoint. Returns None when no qualifying postseason
    # games found in the last 21 days (first game of series, or these two
    # teams aren't in a series). Jerry's prompt handles both states:
    #   - playoffs=true + series_state populated → lead with series context
    #   - playoffs=true + series_state=null      → no series cite, regular-
    #                                              season records as baseline
    playoffs = _is_nba_playoffs_now()
    series_state = _fetch_series_state(away, home) if playoffs else None

    struct = {
        "matchup": f"{away} @ {home}",
        "game_id": game_id,
        "market": {
            "spread": spread,
            "total": _f(p0.get("market_total")),
            "pace_avg": _f(p0.get("pace_avg")),
            "home_ml": p0.get("home_ml"),
            "away_ml": p0.get("away_ml"),
        },
        "model": {
            "net_rating_gap": nr_gap,
            "model_edge_vs_spread": model_edge,
            "ats_lean": (ats.get("pick_side") if ats else None),
            "ats_tier": (ats.get("tier") if ats else None),
            "total_lean": (tot.get("pick_side") if tot else None),
            "total_tier": (tot.get("tier") if tot else None),
            "home_drift": _f(p0.get("home_drift")),
            "away_drift": _f(p0.get("away_drift")),
        },
        "efficiency": {"home": eff(h), "away": eff(a)},
        "injuries": {
            "home": p0.get("home_injury_note") or (h.get("injury_note")),
            "away": p0.get("away_injury_note") or (a.get("injury_note")),
            "star_out": bool(star_out),
            "star_out_note": (star_out.get("signals", {}) or {}).get("note") if star_out else None,
        },
        "context": {
            "playoffs": playoffs,
            "series_state": series_state,
            "records_are": "regular-season baseline (playoffs in progress)" if playoffs else "current season",
        },
        "best_plays": best,
        "meta": {
            "game_date": today_et(),
            "game_has_not_been_played": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    struct["casual_summary"] = _build_casual_summary(struct)
    return struct


def render_prompt(templates, struct):
    away, home = struct["matchup"].split(" @ ")
    confidence_tier = "HIGH — NBA model active (net rating, DefRtg, opp eFG%, home/away records, last 5 net rating, injuries, pace)"
    context_block = (
        "NBA PIPELINE CONTEXT (authoritative — analyze this, do not search for scores):\n"
        + json.dumps(struct, indent=2, default=str)
    )
    m = struct["model"]
    lean = m.get("ats_lean")
    model_lean = (f"{home if lean == 'home' else away if lean == 'away' else 'neutral'} ATS"
                  + (f" ({m['model_edge_vs_spread']:+.1f} edge)" if m.get("model_edge_vs_spread") is not None else ""))
    return (
        templates["wrapper"]
        .replace("{today_et}", now_et_human())
        .replace("{away_team}", away)
        .replace("{home_team}", home)
        .replace("{commence_time_et}", "today")
        .replace("{sport}", "NBA")
        .replace("{sweat_score}", "—")
        .replace("{sweat_tier_label}", "")
        .replace("{spread_str}", str(struct["market"].get("spread") or "N/A"))
        .replace("{total_str}", str(struct["market"].get("total") or "N/A"))
        .replace("{model_lean}", model_lean)
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
            json={"model": MODEL, "max_tokens": 1000, "messages": [{"role": "user", "content": prompt}]},
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


def upsert_read(game_id, narrative, struct):
    key = f"game_read_{game_id}_{today_et()}"
    payload = {
        "game_id": key,
        "cache_key": key,
        "sport": "nba",
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


def run():
    force = "--force" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        try:
            limit = int(sys.argv[sys.argv.index("--limit") + 1])
        except Exception:
            limit = None

    print(f"=== NBA game reads {today_et()} ===")
    templates = load_templates()
    if not templates:
        print("  ⚠️ could not load prompt templates — aborting (run the 20260512_prompt_templates migration)")
        sys.exit(1)

    picks_by_game = fetch_picks_by_game()
    if not picks_by_game:
        print("  No NBA picks for today (no games / off day).")
        return
    stats = fetch_team_stats()
    print(f"  {len(picks_by_game)} games | {len(stats)} team stat rows")

    done = 0
    for game_id, picks in picks_by_game.items():
        struct = build_struct(game_id, picks, stats)
        if not struct:
            continue
        away, home = struct["matchup"].split(" @ ")
        key = f"game_read_{game_id}_{today_et()}"
        if not force:
            if sb_get("jerry_cache", {"cache_key": f"eq.{key}", "select": "cache_key"}):
                print(f"  • {away} @ {home}: exists, skip (--force to regen)")
                continue
        prompt = render_prompt(templates, struct)
        narrative = call_claude(prompt)
        if not narrative:
            print(f"  • {away} @ {home}: no narrative — storing struct only")
        if upsert_read(game_id, narrative or "", struct):
            print(f"  ✓ {away} @ {home} ({len(struct['best_plays'])} plays{', STAR OUT' if struct['injuries']['star_out'] else ''})")
            done += 1
        if limit and done >= limit:
            break

    print(f"=== wrote {done} NBA game reads ===")


if __name__ == "__main__":
    run()
