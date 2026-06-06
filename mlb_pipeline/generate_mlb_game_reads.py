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
        "select": "player_name,player_team,prop_type,prop_line,tier,conviction,signals,matchup",
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


# ---------------------------------------------------------------- team snapshots (2026-06-05)
# Per-team season context block — adds Doc-Sports-style depth (full-season
# batting line, K%/BB%, bullpen save%/holds, league ranks) to Jerry reads.
# Sources `mlb_team_offense` and `mlb_bullpen_stats` (both refreshed daily
# by team_stats.py / bullpen_stats.py). League ranks computed in-memory from
# all 30 teams so we don't need a separate ranks table. Phase B will add
# full-staff team pitching (ERA/WHIP/FIP) and career SP stats.

# MLB Stats API team IDs (all 30) — used for the full-staff pitching pull
# in Phase B. Keeps everything in-process (no schema migration, no new table).
_MLB_TEAM_IDS = {
    "Arizona Diamondbacks": 109, "Atlanta Braves": 144, "Baltimore Orioles": 110,
    "Boston Red Sox": 111, "Chicago Cubs": 112, "Chicago White Sox": 145,
    "Cincinnati Reds": 113, "Cleveland Guardians": 114, "Colorado Rockies": 115,
    "Detroit Tigers": 116, "Houston Astros": 117, "Kansas City Royals": 118,
    "Los Angeles Angels": 108, "Los Angeles Dodgers": 119, "Miami Marlins": 146,
    "Milwaukee Brewers": 158, "Minnesota Twins": 142, "New York Mets": 121,
    "New York Yankees": 147, "Oakland Athletics": 133, "Athletics": 133,
    "Philadelphia Phillies": 143, "Pittsburgh Pirates": 134, "San Diego Padres": 135,
    "San Francisco Giants": 137, "Seattle Mariners": 136, "St. Louis Cardinals": 138,
    "Tampa Bay Rays": 139, "Texas Rangers": 140, "Toronto Blue Jays": 141,
    "Washington Nationals": 120,
}

_TEAM_SNAPSHOTS_CACHE = {}
_TEAM_PITCHING_CACHE = {}
_CAREER_SP_CACHE = {}


def _f_or_none(v):
    """Convert MLB Stats API numeric strings to float; None on miss."""
    if v is None: return None
    try: return float(v)
    except (TypeError, ValueError): return None


def fetch_team_pitching_snapshots():
    """Pull season pitching stats for all 30 teams from MLB Stats API,
    compute league ranks, return {team_name: {era, whip, k_bb, baa, fip_proxy, ranks}}.
    Cached per-process. Added 2026-06-05 Phase B.

    Why MLB Stats API instead of a Supabase table: full-staff pitching wasn't
    being stored anywhere (mlb_bullpen_stats covers relievers only). Rather
    than add a migration + nightly fetcher, we pull live at read-generation
    time. 30 API calls take ~3s and only run once per cron tick."""
    if _TEAM_PITCHING_CACHE:
        return _TEAM_PITCHING_CACHE
    import urllib.request
    by_team = {}
    for team_name, team_id in _MLB_TEAM_IDS.items():
        try:
            url = f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats?stats=season&group=pitching&season=2026"
            with urllib.request.urlopen(url, timeout=10) as r:
                data = json.loads(r.read())
            for split in data.get("stats", []):
                for sp in split.get("splits", []):
                    st = sp.get("stat", {})
                    so = _f_or_none(st.get("strikeOuts"))
                    bb = _f_or_none(st.get("baseOnBalls"))
                    k_bb = round(so / bb, 2) if (so and bb and bb > 0) else None
                    by_team[team_name] = {
                        "team_era": _f_or_none(st.get("era")),
                        "team_whip": _f_or_none(st.get("whip")),
                        "team_baa": _f_or_none(st.get("avg")),
                        "team_k": int(so) if so else None,
                        "team_bb": int(bb) if bb else None,
                        "team_k_bb": k_bb,
                        "team_hr_allowed": int(_f_or_none(st.get("homeRuns")) or 0) or None,
                        "team_ip": _f_or_none(st.get("inningsPitched")),
                    }
        except Exception as e:
            print(f"  ⚠️ team pitching fetch failed for {team_name}: {e}")
    # Compute league ranks (lower = better for ERA/WHIP/BAA; higher = better for K/BB ratio)
    if by_team:
        ranks_era = _rank_dict({t: r.get("team_era") for t, r in by_team.items()}, ascending=True)
        ranks_whip = _rank_dict({t: r.get("team_whip") for t, r in by_team.items()}, ascending=True)
        ranks_baa = _rank_dict({t: r.get("team_baa") for t, r in by_team.items()}, ascending=True)
        ranks_kbb = _rank_dict({t: r.get("team_k_bb") for t, r in by_team.items()})
        for t, r in by_team.items():
            r["rank_team_era"] = ranks_era.get(t)
            r["rank_team_whip"] = ranks_whip.get(t)
            r["rank_team_baa"] = ranks_baa.get(t)
            r["rank_team_k_bb"] = ranks_kbb.get(t)
    _TEAM_PITCHING_CACHE.update(by_team)
    return _TEAM_PITCHING_CACHE


def fetch_career_sp_stats(pitcher_name):
    """Look up a starter's career totals via MLB Stats API. Two-call sequence:
    search-by-name → get playerId → fetch career pitching. Cached per-process
    so 15 starters × 2 calls = ~30 API hits per cron run.

    Returns dict or None on miss. Added 2026-06-05 Phase B."""
    if not pitcher_name:
        return None
    if pitcher_name in _CAREER_SP_CACHE:
        return _CAREER_SP_CACHE[pitcher_name]
    import urllib.request, urllib.parse
    try:
        url = f"https://statsapi.mlb.com/api/v1/people/search?names={urllib.parse.quote(pitcher_name)}&sportIds=1"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        people = data.get("people", [])
        if not people:
            _CAREER_SP_CACHE[pitcher_name] = None
            return None
        # Prefer the first active pitcher match
        pid = None
        for p in people:
            if p.get("primaryPosition", {}).get("abbreviation") == "P":
                pid = p["id"]; break
        pid = pid or people[0]["id"]

        url2 = f"https://statsapi.mlb.com/api/v1/people/{pid}/stats?stats=career&group=pitching"
        with urllib.request.urlopen(url2, timeout=10) as r:
            data2 = json.loads(r.read())
        for split in data2.get("stats", []):
            for sp in split.get("splits", []):
                st = sp.get("stat", {})
                out = {
                    "mlb_player_id": pid,
                    "career_w": int(_f_or_none(st.get("wins")) or 0) or None,
                    "career_l": int(_f_or_none(st.get("losses")) or 0) or None,
                    "career_era": _f_or_none(st.get("era")),
                    "career_whip": _f_or_none(st.get("whip")),
                    "career_baa": _f_or_none(st.get("avg")),
                    "career_k": int(_f_or_none(st.get("strikeOuts")) or 0) or None,
                    "career_bb": int(_f_or_none(st.get("baseOnBalls")) or 0) or None,
                    "career_ip": _f_or_none(st.get("inningsPitched")),
                    "career_bf": int(_f_or_none(st.get("battersFaced")) or 0) or None,
                    "career_er": int(_f_or_none(st.get("earnedRuns")) or 0) or None,
                    "career_hr_allowed": int(_f_or_none(st.get("homeRuns")) or 0) or None,
                }
                _CAREER_SP_CACHE[pitcher_name] = out
                return out
    except Exception as e:
        print(f"  ⚠️ career fetch failed for {pitcher_name}: {e}")
    _CAREER_SP_CACHE[pitcher_name] = None
    return None


def _rank_dict(values_by_team, ascending=False):
    """Return {team: rank} where rank=1 means top of list. ascending=False
    treats higher = better (e.g. R/G, OPS); ascending=True for lower=better
    (e.g. BP ERA, blown saves)."""
    items = [(t, v) for t, v in values_by_team.items() if v is not None]
    items.sort(key=lambda x: x[1], reverse=not ascending)
    return {t: i + 1 for i, (t, _) in enumerate(items)}


def fetch_team_snapshots():
    """Pull team_offense + bullpen_stats for all 30 teams once, compute league
    ranks, return {team_name: {snapshot_dict}}. Cached for the process."""
    if _TEAM_SNAPSHOTS_CACHE:
        return _TEAM_SNAPSHOTS_CACHE
    offense_rows = sb_get("mlb_team_offense", {
        "season": "eq.2026",
        "select": "team,avg,obp,slg,ops,k_pct,bb_pct,runs_per_game,hr_per_game,wrc_plus,woba,xwoba,barrel_pct,oaa,iso,games_played",
    })
    bullpen_rows = sb_get("mlb_bullpen_stats", {
        "season": "eq.2026",
        "select": "team,bullpen_era,save_pct,saves,blown_saves,holds",
    })
    if not offense_rows:
        return _TEAM_SNAPSHOTS_CACHE
    by_team = {r["team"]: dict(r) for r in offense_rows}
    for b in bullpen_rows or []:
        if b["team"] in by_team:
            by_team[b["team"]].update({k: v for k, v in b.items() if k != "team"})

    # Compute league ranks across all teams that have data.
    rank_runs = _rank_dict({t: _f(r.get("runs_per_game")) for t, r in by_team.items()})
    rank_ops = _rank_dict({t: _f(r.get("ops")) for t, r in by_team.items()})
    rank_wrc = _rank_dict({t: _f(r.get("wrc_plus")) for t, r in by_team.items()})
    rank_xwoba = _rank_dict({t: _f(r.get("xwoba")) for t, r in by_team.items()})
    rank_bp_era = _rank_dict({t: _f(r.get("bullpen_era")) for t, r in by_team.items()}, ascending=True)
    rank_save_pct = _rank_dict({t: _f(r.get("save_pct")) for t, r in by_team.items()})
    rank_holds = _rank_dict({t: _f(r.get("holds")) for t, r in by_team.items()})
    rank_oaa = _rank_dict({t: _f(r.get("oaa")) for t, r in by_team.items()})

    for team, r in by_team.items():
        r["rank_runs_per_game"] = rank_runs.get(team)
        r["rank_ops"] = rank_ops.get(team)
        r["rank_wrc_plus"] = rank_wrc.get(team)
        r["rank_xwoba"] = rank_xwoba.get(team)
        r["rank_bullpen_era"] = rank_bp_era.get(team)
        r["rank_save_pct"] = rank_save_pct.get(team)
        r["rank_holds"] = rank_holds.get(team)
        r["rank_oaa"] = rank_oaa.get(team)
    _TEAM_SNAPSHOTS_CACHE.update(by_team)
    return _TEAM_SNAPSHOTS_CACHE


def _team_snapshot_block(team_name):
    """Build the per-team snapshot used by the prompt + The Numbers panel.
    Pulls from fetch_team_snapshots() + fetch_team_pitching_snapshots()
    (both cached). Returns None on miss so the prompt can fall back to
    existing wrc_plus-only context."""
    snaps = fetch_team_snapshots()
    pitch = fetch_team_pitching_snapshots()
    r = snaps.get(team_name)
    if not r:
        return None
    p = pitch.get(team_name) or {}
    return {
        "team": team_name,
        "games_played": r.get("games_played"),
        # Batting line
        "avg": r.get("avg"),
        "obp": r.get("obp"),
        "slg": r.get("slg"),
        "ops": r.get("ops"),
        "iso": r.get("iso"),
        "k_pct": r.get("k_pct"),
        "bb_pct": r.get("bb_pct"),
        "wrc_plus": r.get("wrc_plus"),
        "woba": r.get("woba"),
        "xwoba": r.get("xwoba"),
        "barrel_pct": r.get("barrel_pct"),
        "runs_per_game": r.get("runs_per_game"),
        "hr_per_game": r.get("hr_per_game"),
        # Bullpen
        "bullpen_era": r.get("bullpen_era"),
        "bullpen_save_pct": r.get("save_pct"),
        "bullpen_saves": r.get("saves"),
        "bullpen_blown_saves": r.get("blown_saves"),
        "bullpen_holds": r.get("holds"),
        # Defense
        "oaa": r.get("oaa"),
        # Full-staff team pitching (Phase B 2026-06-05)
        "team_era": p.get("team_era"),
        "team_whip": p.get("team_whip"),
        "team_baa": p.get("team_baa"),
        "team_k": p.get("team_k"),
        "team_bb": p.get("team_bb"),
        "team_k_bb": p.get("team_k_bb"),
        "team_hr_allowed": p.get("team_hr_allowed"),
        "team_ip": p.get("team_ip"),
        # League ranks (1 = best)
        "rank_runs_per_game": r.get("rank_runs_per_game"),
        "rank_ops": r.get("rank_ops"),
        "rank_wrc_plus": r.get("rank_wrc_plus"),
        "rank_xwoba": r.get("rank_xwoba"),
        "rank_bullpen_era": r.get("rank_bullpen_era"),
        "rank_save_pct": r.get("rank_save_pct"),
        "rank_holds": r.get("rank_holds"),
        "rank_oaa": r.get("rank_oaa"),
        "rank_team_era": p.get("rank_team_era"),
        "rank_team_whip": p.get("rank_team_whip"),
        "rank_team_baa": p.get("rank_team_baa"),
        "rank_team_k_bb": p.get("rank_team_k_bb"),
    }


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
    if s <= 25:
        return f"{score} — YRFI lean (≤25, 30d audit ~48%; sweet spot is 1st-inn ERA 6-8 → 63%)"
    if s <= 40:
        return f"{score} — soft YRFI lean (≤40, 30d audit ~48% — gate on 1st-inn ERA)"
    return f"{score} — neutral"


def _pitcher_block(g, side):
    """Build per-pitcher block with EXPLICIT opp-lineup attribution baked in.

    Reformulated 2026-05-16 to fix the cross-team mixup bug — Claude was reading
    raw home_wrc_plus/away_wrc_plus and occasionally writing sentences like
    "Boston's 90 wRC+, a soft lineup Tolle will punish" when Tolle pitches FOR
    Boston (vs Atlanta's 118 wRC+). Same attribution-error family as the Suarez
    5/14 Luzardo mixup. Fix: pre-compute pitcher.opp_* fields so the model
    doesn't have to do the home/away → faces-which-lineup mapping itself.
    """
    name = g.get(f"{side}_pitcher")
    opp_side = "away" if side == "home" else "home"
    own_team = g.get("home_team") if side == "home" else g.get("away_team")
    opp_team = g.get("home_team") if opp_side == "home" else g.get("away_team")
    xera = _f(g.get(f"{side}_sp_xera"))
    l3 = _f(g.get(f"{side}_pitcher_last_3_era"))
    fi = _f(g.get(f"{side}_first_inning_era"))
    last_ip = _f(g.get(f"{side}_last_ip"))
    # K gap stored on pitcher's own side: e.g. away_k_gap = away pitcher's K%
    # advantage over the home team lineup (the opp lineup). Same prefix pairs
    # always — see [[feedback_verify_pitcher_attribution]].
    k_gap_vs_opp = _f(g.get(f"{side}_k_gap"))
    opp_lineup_wrc = _f(g.get(f"{opp_side}_wrc_plus"))
    opp_lineup_k_pct = _f(g.get(f"{opp_side}_team_k_pct"))
    flags = []
    if l3 is not None and xera is not None and l3 - xera >= 1.5:
        flags.append(f"form drift: L3 ERA {l3:.2f} vs xERA {xera:.2f} (+{l3 - xera:.1f})")
    if fi is not None and fi >= 6.0:
        flags.append(f"shaky 1st inning ({fi:.1f} ERA)")
    if last_ip is not None and last_ip < 3.0:
        flags.append(f"last outing only {last_ip:.1f} IP — opener/short")
    if l3 is not None and l3 >= 6.0:
        flags.append(f"getting tagged lately (L3 ERA {l3:.2f}) — pull-early risk")
    # All five projections + WHIP populated by patch_projected_ks.py from the
    # same JSON cache the prop scorers use. Lets Jerry cite the EXACT number
    # downstream surfaces will see, even when no prop of that type is in the
    # published list. WHIP is descriptive color (≤0.95 elite, ≥1.50 shaky).
    whip = _f(g.get(f"{side}_pitcher_whip"))
    whip_flag = None
    if whip is not None:
        if whip <= 0.95: whip_flag = "elite"
        elif whip >= 1.50: whip_flag = "shaky"
    return {
        "name": name,
        "own_team": own_team,
        "opp_team": opp_team,
        "opp_lineup_wrc": opp_lineup_wrc,
        "opp_lineup_k_pct": opp_lineup_k_pct,
        "k_gap_vs_opp": k_gap_vs_opp,
        "xera": xera,
        "l3_era": l3,
        "first_inning_era": fi,
        "vs_team_era": _f(g.get(f"{side}_pitcher_vs_team_era")),
        "vs_team_avg": _f(g.get(f"{side}_pitcher_vs_team_avg")),
        "projected_ks":    _f(g.get(f"{side}_pitcher_projected_ks")),
        "projected_bb":    _f(g.get(f"{side}_pitcher_projected_bb")),
        "projected_hits":  _f(g.get(f"{side}_pitcher_projected_hits")),
        "projected_outs":  _f(g.get(f"{side}_pitcher_projected_outs")),
        "projected_er":    _f(g.get(f"{side}_pitcher_projected_er")),
        "whip": whip,
        "whip_flag": whip_flag,
        "last_ip": last_ip,
        "flags": flags,
        # Career SP totals (Phase B 2026-06-05) — fetched live from MLB
        # Stats API per [[project_jerry_read_phase_b_career_team_pitching]].
        # None on miss so prompt falls back to season-only context.
        "career": fetch_career_sp_stats(name),
    }


def _build_casual_summary(struct):
    """Rank signals from the struct by 'deviation from norm' and surface the
    3-4 strongest as plain-English bullets, plus a one-line bottom_line.
    Deterministic (no LLM) so it's always available + free + predictable."""
    headlines = []
    m = struct.get("market") or {}
    c = struct.get("confluence") or {}
    sit = struct.get("situational") or {}
    ph = (struct.get("pitchers") or {}).get("home") or {}
    pa = (struct.get("pitchers") or {}).get("away") or {}
    home = (struct.get("matchup") or "").split(" @ ")[-1]
    away = (struct.get("matchup") or "").split(" @ ")[0]

    # 1. Total edge — strongest game-level signal when present
    td = m.get("total_delta")
    if td is not None and abs(td) >= 1.5:
        lean = "OVER" if td > 0 else "UNDER"
        headlines.append((
            10 + min(5, abs(td)),
            f"✓ Model expects ~{m.get('model_total')} total runs vs the market's {m.get('close_total')} — {lean} lean ({td:+.1f} runs)"
        ))

    # 2. Confluence — multi-signal agreement on the side
    if c.get("net") is not None and abs(int(c["net"])) >= 4:
        net = int(c["net"])
        bd = c.get("breakdown") or {}
        if isinstance(bd, dict) and bd:
            # majority side
            sides = [v for v in bd.values() if v in ("home", "away")]
            tally = {"home": sides.count("home"), "away": sides.count("away")}
            top = max(tally, key=tally.get) if any(tally.values()) else None
            team = home if top == "home" else away if top == "away" else None
            if team:
                headlines.append((
                    9 + min(5, abs(net)),
                    f"✓ {tally[top]} of {len(sides)} model signals point to {team} — strong stack on this side"
                ))

    # 3. Pitcher fragility flags — already derived in struct
    for side, pdata, team in [("away", pa, away), ("home", ph, home)]:
        flags = pdata.get("flags") or []
        if flags:
            # one bundled bullet per pitcher
            nm = pdata.get("name") or f"{team} starter"
            headlines.append((
                8,
                f"⚠ {nm}: {flags[0]}"
            ))

    # 4. Mastery (favorable history) — tightened 2026-05-13 after Liberatore
    # fired "owns this lineup" at 0.0 ERA / 5.3 IP vs ATH (a 2-start sample).
    # We don't store vs-team IP in the struct, so we proxy sample-size
    # robustness by: (a) tightening the "owns" threshold from ≤3.0 to ≤2.0
    # (a 2.0 career ERA across the typical 1-3 starts we see is still loud
    # enough to surface), and (b) suppressing the "owns" headline entirely
    # when other signals contradict it — i.e., the starter's xERA / L3 ERA
    # is materially worse than the vs-team number, which is the noise pattern
    # we're worried about. Torched-by-lineup side stays at ≥7.0 — that's a
    # red flag worth showing even on tiny sample.
    for side, pdata, team, opp in [("away", pa, away, home), ("home", ph, home, away)]:
        vsera = pdata.get("vs_team_era")
        if vsera is None:
            continue
        xera = pdata.get("xera")
        l3_era = pdata.get("l3_era")
        # signal-conflict suppression — if season form is much worse than the
        # vs-team history, the history is probably small-sample noise.
        season_form = max(float(xera) if xera is not None else 0.0,
                          float(l3_era) if l3_era is not None else 0.0)
        if float(vsera) <= 2.0 and season_form <= 5.0:
            headlines.append((7, f"✓ {pdata.get('name') or f'{team} starter'} owns this lineup (career {vsera:.2f} ERA vs {opp})"))
        elif float(vsera) >= 7.0:
            headlines.append((9, f"⚠ {pdata.get('name') or f'{team} starter'} has been torched by this lineup historically ({vsera:.2f} ERA)"))

    # 5. Bullpen workload — gassed pens
    h_bp = sit.get("home_bp_relievers_3d")
    a_bp = sit.get("away_bp_relievers_3d")
    for team, n in [(home, h_bp), (away, a_bp)]:
        try:
            if n is not None and int(n) >= 12:
                headlines.append((6, f"⚠ {team}'s bullpen is gassed ({n} relievers used in last 3 days)"))
        except Exception:
            pass

    # 6. NRFI / YRFI lean
    nrfi = sit.get("nrfi_score")
    try:
        if nrfi is not None:
            s = float(nrfi)
            if s >= 90:
                headlines.append((7, f"✓ Both starters have elite first-inning history — strong no-runs-in-the-1st signal"))
            elif s <= 30:
                headlines.append((7, f"⚠ Both starters get tagged in the 1st — runs likely early"))
    except Exception:
        pass

    # 7. Umpire signal
    ump_note = sit.get("umpire_note")
    if ump_note and isinstance(ump_note, str):
        if "k-friendly" in ump_note.lower() or "over-friendly" in ump_note.lower():
            headlines.append((4, f"✓ Umpire {sit.get('umpire','')}: {ump_note.split('—')[-1].strip()}"))

    # 8. Park factor — only if extreme
    park = sit.get("park_run_factor")
    try:
        if park is not None and float(park) >= 110:
            headlines.append((4, f"✓ Hitter-friendly park (factor {park}) — runs come easier"))
        elif park is not None and float(park) <= 92:
            headlines.append((4, f"✓ Pitcher-friendly park (factor {park}) — runs harder to come by"))
    except Exception:
        pass

    # POTD gets a top-of-list star (highest priority headline)
    if struct.get("is_potd") and struct.get("potd_lean"):
        headlines.append((100, f"⭐ This is today's Play of the Day — {struct['potd_lean']}"))

    # Take top 4 by score, drop the scores
    headlines.sort(key=lambda x: -x[0])
    top = [h[1] for h in headlines[:4]]

    # Bottom line — derive from strongest signal hierarchy.
    # POTD > total edge > confluence stack > primary_play (lowest — sometimes
    # stale from the xERA-gap rule which v2 may override).
    bottom = None
    if struct.get("is_potd") and struct.get("potd_lean"):
        bottom = f"Today's Play of the Day — {struct['potd_lean']}"
    elif td is not None and abs(td) >= 1.5:
        lean = "OVER" if td > 0 else "UNDER"
        bottom = f"Model's lean: {lean} {m.get('close_total')} (model {m.get('model_total')} vs market {m.get('close_total')})"
    elif c.get("net") is not None and abs(int(c["net"])) >= 4:
        bd = c.get("breakdown") or {}
        if isinstance(bd, dict):
            sides = [v for v in bd.values() if v in ("home", "away")]
            tally = {"home": sides.count("home"), "away": sides.count("away")}
            top_side = max(tally, key=tally.get) if any(tally.values()) else None
            team = home if top_side == "home" else away if top_side == "away" else None
            if team:
                bottom = f"Model's lean: {team} side (confluence {c['net']:+d})"
    else:
        pp = c.get("primary_play")
        if isinstance(pp, dict) and pp.get("label"):
            tier = pp.get("tier") or ""
            bottom = f"Model's lean: {pp['label']}" + (f" ({tier})" if tier else "")
    if not bottom:
        bottom = "Mixed signals — no strong directional edge"

    return {"headlines": top, "bottom_line": bottom}


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
            "team": p.get("player_team"),
            "prop_type": p.get("prop_type"),
            "line": p.get("prop_line"),
            "tier": p.get("tier"),
            "conviction": p.get("conviction"),
            "projection": proj,
            "why": reasons,
        })

    potd_game = ""
    potd_lean = None
    if isinstance(potd, dict):
        gv = potd.get("game") or potd.get("matchup") or ""
        potd_game = gv if isinstance(gv, str) else json.dumps(gv, default=str)
        # POTD lean can live under a few different keys depending on the
        # play_of_day.py version — try the common ones. Added `leanDisplay`
        # 2026-05-13 after SF/LAD POTD's UNDER lean wasn't flowing through
        # to the casual summary (that's the actual key play_of_day writes).
        pick_obj = potd.get("pick") if isinstance(potd.get("pick"), dict) else None
        potd_lean = (
            potd.get("leanDisplay")
            or potd.get("lean")
            or potd.get("label")
            or (pick_obj.get("label") if pick_obj else None)
        )
    is_potd = bool(home and away and home in potd_game and away in potd_game)

    # ---- Buy-down play qualifier (2026-06-03 backtest-driven) ----
    # When v3 + v4 + Jerry have non-trivial directional agreement (>=0.3
    # runs in the same direction, no opposition), and at least one cohort
    # entry condition is met, surface a cheated-line recommendation.
    # Cohorts validated against 683 graded games (6 weeks):
    #   - all-three agree (n=22): 76.2% buy-down hit rate, +EV up to -300
    #   - any model >=2.0 edge with no opposition (n=75): 80% OVER / 87.5%
    #     UNDER buy-down hit rate, +EV up to -400/-700 juice
    # See mlb_pipeline/_backtest_buy_down_split.py for the methodology.
    buy_down_play = None
    if close_t is not None:
        v3 = _f(g.get("projected_total"))
        v4 = _f(g.get("model_pred_total"))
        jr = _f(g.get("jerry_pred_total"))
        edges = []
        for v in (v3, v4, jr):
            edges.append(v - close_t if v is not None else None)
        # Dead-band (±0.3 runs): smaller than that is model "silent" — too
        # small to count as agreement or disagreement.
        DEAD_BAND = 0.3
        BUY_DOWN_RUNS = 2.0
        loud_dirs = []
        for e in edges:
            if e is not None and abs(e) >= DEAD_BAND:
                loud_dirs.append("OVER" if e > 0 else "UNDER")
        all_loud_agree = len(loud_dirs) > 0 and all(d == loud_dirs[0] for d in loud_dirs)
        # All-three-agree cohort: every model populated, every model loud,
        # all agree. The 76% consensus cohort.
        all_three_agree = (
            v3 is not None and v4 is not None and jr is not None
            and all(e is not None and abs(e) >= DEAD_BAND for e in edges)
            and all_loud_agree
        )
        loudest = max((abs(e) for e in edges if e is not None), default=0)
        qualifies = all_loud_agree and (all_three_agree or loudest >= 2.0)
        if qualifies:
            direction = loud_dirs[0]
            cheated_line = close_t - BUY_DOWN_RUNS if direction == "OVER" else close_t + BUY_DOWN_RUNS
            # Cohort label + hit rate + EV juice ceiling
            if all_three_agree and loudest >= 2.0:
                cohort = "consensus + loud edge"
                hit_rate = "~80%" if direction == "OVER" else "~87%"
                max_juice = "-400" if direction == "OVER" else "-700"
            elif loudest >= 2.0:
                cohort = "model edge >= 2.0"
                hit_rate = "80%" if direction == "OVER" else "87.5%"
                max_juice = "-400" if direction == "OVER" else "-700"
            else:
                cohort = "all three models agree"
                hit_rate = "76%"
                max_juice = "-300"
            buy_down_play = {
                "direction": direction,
                "original_line": close_t,
                "cheated_line": round(cheated_line, 1),
                "buy_runs": BUY_DOWN_RUNS,
                "cohort": cohort,
                "cohort_hit_rate": hit_rate,
                "max_juice_ev": max_juice,
                "cohort_note": f"Cohort: {cohort} · backtest hit rate {hit_rate} · +EV up to {max_juice} juice",
            }

    struct = {
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
            # Normalized denominators added 2026-05-28. App should render
            # "{net} of {voted} voted ({total} possible)" rather than just
            # the breakdown count which varied 5-9 per game and confused
            # the "all signals agree" message.
            "signals_voted": g.get("signal_confluence_signals_voted"),
            "signals_total": g.get("signal_confluence_signals_total"),
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
        # Team season snapshots (added 2026-06-05) — Doc-Sports-style depth
        # for the prompt + The Numbers panel. Computed from mlb_team_offense
        # + mlb_bullpen_stats with league ranks. See fetch_team_snapshots().
        "team_snapshot": {
            "home": _team_snapshot_block(g.get("home_team")),
            "away": _team_snapshot_block(g.get("away_team")),
        },
        "best_plays": best,
        "is_potd": is_potd,
        "potd_lean": potd_lean if is_potd else None,
        # Buy-down cheat-line recommendation (None when game doesn't
        # qualify for a backtest cohort). Surfaced in The Numbers panel.
        "buy_down_play": buy_down_play,
        "meta": {
            "game_date": today_et(),
            "game_has_not_been_played": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    struct["casual_summary"] = _build_casual_summary(struct)
    return struct


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
