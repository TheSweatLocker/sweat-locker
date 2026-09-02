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
    """Pull season pitching stats for all 30 teams. Cached per-process.

    2026-09-01 REWRITE — cutover from live MLB StatsAPI to persisted
    mlb_team_pitching table (populated nightly by mlb_team_pitching_pull.py).
    Kills 30 API calls per cron tick + eliminates the "MLB StatsAPI is
    slow/down" failure surface. Falls back to live-fetch when Supabase
    returns nothing (fresh-install / migration-not-yet-applied case).

    Return shape unchanged so downstream callers work as-is:
      {team_name: {team_era, team_whip, team_baa, team_k, team_bb,
                   team_k_bb, team_hr_allowed, team_ip,
                   rank_team_era, rank_team_whip, rank_team_baa,
                   rank_team_k_bb}}
    """
    if _TEAM_PITCHING_CACHE:
        return _TEAM_PITCHING_CACHE

    # ── PRIMARY PATH: read from persisted mlb_team_pitching ──
    by_team = {}
    try:
        import os, requests as _rq
        sb = os.environ.get("SUPABASE_URL"); k = os.environ.get("SUPABASE_KEY")
        if sb and k:
            season = datetime.now().year
            r = _rq.get(
                f"{sb}/rest/v1/mlb_team_pitching"
                f"?season=eq.{season}&select=*",
                headers={"apikey": k, "Authorization": f"Bearer {k}"},
                timeout=10,
            )
            if r.status_code == 200 and r.json():
                for row in r.json():
                    tname = row.get("team")
                    if not tname: continue
                    by_team[tname] = {
                        "team_era":         _f_or_none(row.get("era")),
                        "team_whip":        _f_or_none(row.get("whip")),
                        "team_baa":         _f_or_none(row.get("baa")),
                        "team_k":           int(row.get("k") or 0) or None,
                        "team_bb":          int(row.get("bb") or 0) or None,
                        "team_k_bb":        _f_or_none(row.get("k_bb_ratio")),
                        "team_hr_allowed":  int(row.get("hr_allowed") or 0) or None,
                        "team_ip":          _f_or_none(row.get("ip")),
                    }
    except Exception as e:
        print(f"  ⚠️ mlb_team_pitching read failed ({e}), falling back to live API")

    # ── FALLBACK: live MLB StatsAPI (original path) ──
    if not by_team:
        import urllib.request
        for team_name, team_id in _MLB_TEAM_IDS.items():
            try:
                url = f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats?stats=season&group=pitching&season={datetime.now().year}"
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

    # Compute league ranks (lower = better for ERA/WHIP/BAA; higher for K/BB ratio)
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

_NRFI_CAL_CACHE = {}  # {(tier_key, window): {hit_rate, total} or None}


def _nrfi_calibration_format(tier_key, min_n=30):
    """Pull live NRFI tier hit rate from mlb_tier_calibration.
    Returns formatted string ' (X% over W-L 30d)' when n >= min_n,
    else empty string (per feedback_sample_size_with_pct rule —
    don't quote a % below quotability threshold).

    Schema columns: tier, window_label, hits, total, hit_rate.
    Losses derived as total - hits.
    """
    if tier_key in _NRFI_CAL_CACHE:
        cached = _NRFI_CAL_CACHE[tier_key]
    else:
        cached = None
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/mlb_tier_calibration",
                params={'tier': f'eq.{tier_key}',
                        'window_label': 'eq.30d',
                        'select': 'hits,total,hit_rate',
                        'order': 'computed_date.desc',
                        'limit': '1'},
                headers=SB_READ, timeout=5,
            )
            rows = r.json() if r.status_code == 200 else []
            cached = rows[0] if rows and isinstance(rows[0], dict) else None
        except Exception:
            cached = None
        _NRFI_CAL_CACHE[tier_key] = cached
    if not cached:
        return ""
    n = cached.get('total') or 0
    rate = cached.get('hit_rate')
    hits = cached.get('hits')
    if n < min_n or rate is None:
        return ""
    try:
        n = int(n); rate = float(rate)
    except (TypeError, ValueError):
        return ""
    pct = rate * 100 if rate <= 1 else rate
    if hits is not None:
        losses = n - int(hits)
        return f" ({pct:.0f}% over {int(hits)}-{losses} 30d)"
    return f" ({pct:.0f}% over {n} games 30d)"


def _nrfi_tier(score):
    if score is None:
        return None
    s = float(score)
    # 2026-06-18 Tier A: pull live calibration from mlb_tier_calibration.
    # When n >= 30 the live rate + record is appended to the band label.
    # When n < 30 we omit the % entirely (no false-precision).
    if s >= 95:
        cal = _nrfi_calibration_format('nrfi_volatile_95plus')
        return f"{score} — volatile 95+ band{cal}"
    if s >= 90:
        cal = _nrfi_calibration_format('nrfi_prime_90_94')
        return f"{score} — PRIME 90-94 band{cal}"
    if s >= 70:
        cal = _nrfi_calibration_format('nrfi_lean_70_79')
        return f"{score} — mild NRFI lean 70-79{cal}"
    if s <= 25:
        cal = _nrfi_calibration_format('yrfi_lean_le40')
        return f"{score} — YRFI lean (≤25, sweet spot is 1st-inn ERA 6-8){cal}"
    if s <= 40:
        cal = _nrfi_calibration_format('yrfi_lean_le40')
        return f"{score} — soft YRFI lean (≤40, gate on 1st-inn ERA){cal}"
    return f"{score} — neutral"


def _l5_block_for_side(game_date, game_id, side):
    """Pull the L5 actuals payload for `side` (home/away) from the
    pitcher_l5 cache row. Returns the per-side dict or None.

    Loaded lazily via pitcher_l5_lookup so missing cache rows degrade
    gracefully — the read prompt will still render without recency
    context. Added 2026-06-07 to give Jerry the L5 numbers the user
    had to pull manually (Baz outs / Flaherty hits / Cameron ER on 6/7).
    """
    try:
        from pitcher_l5_lookup import get_l5
        l5 = get_l5(game_date, game_id)
    except Exception:
        return None
    if not l5:
        return None
    return l5.get(side)


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
        # L5 starts (added 2026-06-07) — outs/Ks/BB/hits/ER for each of
        # the last 5 starts + avg. Closes the recency-data gap. Schema:
        # { name, mlb_id, starts: [{date,outs,ks,bb,hits,er,opp}], avg:{...} }
        # None on miss so prompt falls back to season + L3 ERA context.
        "l5": _l5_block_for_side(g.get("game_date"), g.get("game_id"), side),
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


# -------------------------------------------------- buy-down live calibration
# Phase 6 of engine_clarity_refactor.md. Replaces the hardcoded buy-down
# cohort hit rates that were stripped earlier today. Live rates computed
# nightly by buy_down_calibration.py and stored in jerry_cache row
# `buy_down_calibration`. Cohort key strings match the buy-down classifier
# output in this module's buy_down section.

_BUY_DOWN_COHORT_KEY = {
    "consensus + loud edge": "consensus_loud",
    "model edge >= 2.0": "model_edge_2",
    "all three models agree": "consensus",
}

_BUY_DOWN_CALIB_CACHE = {"loaded": False, "by_key": {}}


def _load_buy_down_calibration():
    """Load buy_down_calibration jerry_cache row into the module cache once."""
    if _BUY_DOWN_CALIB_CACHE["loaded"]:
        return _BUY_DOWN_CALIB_CACHE["by_key"]
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/jerry_cache",
            params={"cache_key": "eq.buy_down_calibration", "select": "data"},
            headers=SB_READ,
            timeout=5,
        )
        rows = r.json() if r.status_code == 200 else []
        if rows:
            raw = rows[0].get("data")
            if isinstance(raw, str):
                raw = json.loads(raw)
            for rec in (raw.get("records") or []):
                k = (rec["cohort"], rec["direction"], rec["window"])
                _BUY_DOWN_CALIB_CACHE["by_key"][k] = rec
    except Exception:
        pass
    _BUY_DOWN_CALIB_CACHE["loaded"] = True
    return _BUY_DOWN_CALIB_CACHE["by_key"]


def _lookup_buy_down_calibration(cohort_key, direction):
    """Return (hit_rate_pct, n, wins, losses) for the 60d window cell if
    quotable (n>=30). Returns (None, None, None, None) otherwise."""
    calib = _load_buy_down_calibration()
    # Prefer 60d (balances recency vs sample size); fall back to 90d / 30d.
    for window in ("60d", "90d", "30d"):
        rec = calib.get((cohort_key, direction, window))
        if rec and rec.get("quotable"):
            return (rec["hit_rate"] * 100,
                    rec["actionable_n"],
                    rec["wins"],
                    rec["losses"])
    return (None, None, None, None)


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
    #
    # 2026-06-17 — STRIPPED hardcoded hit-rate / juice claims. The previous
    # numbers ("80% OVER", "76% all-three", "+EV up to -400") were from a
    # 6-week backtest at original implementation and never refreshed.
    # 2026-06-17 (later) — Phase 6 of engine_clarity_refactor wired live
    # calibration via buy_down_calibration.py (nightly). Hit rates pulled
    # from jerry_cache.buy_down_calibration row when n >= 30. Below
    # threshold, cohort_hit_rate stays None and Jerry omits the % claim.
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
            if all_three_agree and loudest >= 2.0:
                cohort = "consensus + loud edge"
            elif loudest >= 2.0:
                cohort = "model edge >= 2.0"
            else:
                cohort = "all three models agree"
            # Look up live calibration (Phase 6). Returns (rate_pct, n) when
            # the (cohort, direction, 60d) cell has n >= 30; None otherwise.
            live_rate, live_n, live_w, live_l = _lookup_buy_down_calibration(
                _BUY_DOWN_COHORT_KEY[cohort], direction)
            if live_rate is not None:
                cohort_hit_rate = f"{live_rate:.0f}% (live, {live_w}-{live_l} 60d)"
                cohort_note = (f"Cohort: {cohort} - live hit rate {cohort_hit_rate} "
                               f"vs cheated line {round(cheated_line, 1)}")
            else:
                cohort_hit_rate = None
                cohort_note = f"Cohort: {cohort} (model agreement signal; calibration n<30 - rate withheld)"
            buy_down_play = {
                "direction": direction,
                "original_line": close_t,
                "cheated_line": round(cheated_line, 1),
                "buy_runs": BUY_DOWN_RUNS,
                "cohort": cohort,
                "cohort_hit_rate": cohort_hit_rate,
                "max_juice_ev": None,  # juice ceiling not yet derived from live data
                "cohort_note": cohort_note,
            }

    # ---- OddsCrowd bets%/money% block (2026-07-30) ----
    # Real bettor + real dollar percentages per market. Only populated when
    # data was pulled today (via T-45min oddscrowd cron). Passed to Jerry
    # so THE READ can reference actual money flow when material.
    # STRICT: never fabricate. If oddscrowd_snapshot is null/empty on the
    # ctx row, this stays None and prompt guard rules require Jerry to
    # skip any money-flow language.
    def _money_flow_block(gctx):
        oc = gctx.get("oddscrowd_snapshot") or {}
        if isinstance(oc, str):
            try:
                oc = json.loads(oc)
            except Exception:
                oc = {}
        if not oc or not isinstance(oc, dict):
            return None
        out = {}
        for surface in ("ml", "rl", "total"):
            m = oc.get(surface)
            if isinstance(m, dict) and m.get("money") is not None:
                out[surface] = {
                    "side": m.get("pick"),
                    "money_pct": m.get("money"),
                    "bets_pct": m.get("bets"),
                    "divergence_pp": m.get("div"),
                    "fade_flag": m.get("fade"),
                }
        if not out:
            return None
        out["pulled_at"] = oc.get("pulled_at")
        return out

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
            # oddscrowd bets%/money% — None when not available (never faked)
            "money_flow": _money_flow_block(g),
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
        # Cohort signals (Phase 1 surface — 2026-06-08). Data-discovered
        # rules from 518-game attribution backtest with Bayesian shrinkage
        # + recency veto. Read-only — does not affect conviction or card
        # eligibility yet. Phase 2 wires conviction adjustments. None when
        # cohort_signals jerry_cache row is missing or stale.
        # See cohort_signals.summarize_for_struct() for shape.
        "cohort_signals": _cohort_signals_block(g),
        # Resolver landing call (2026-06-10 evening — founder asked for ONE
        # call per game instead of a wall of conflicting signals). The
        # resolver aggregates v3/v4/jerry models + cohort engine net + prop
        # reverse into a single direction + tier + reason. Jerry MUST lead
        # with this in `Where the Model Sits` + `The Play` instead of
        # listing the raw conflicting signals. Retroactive audit (n=148 over
        # 30d): STRONG-tier picks hit 64.2% / +22.6% ROI. LIGHT and SKIP
        # tiers should never be cited as "the play" — frame as "no clean
        # read" instead.
        # 2026-06-12: added resolver_side so Jerry surfaces BOTH the total
        # AND side call when both fire STRONG+. Previously only resolver
        # (total) was in the struct — Jerry never knew when the side
        # resolver said STRONG, which created public-vs-app contradictions
        # (we tweeted BAL ML STRONG, Jerry only mentioned Over total).
        "resolver": _resolver_block(g),
        "resolver_side": _side_resolver_block(g),
        "meta": {
            "game_date": today_et(),
            "game_has_not_been_played": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    struct["casual_summary"] = _build_casual_summary(struct)
    return struct


def _resolver_block(g):
    """Compute resolver landing call for the game's total. Returns dict or
    None on failure. Jerry reads consume this as the primary signal source —
    `direction` + `tier` + `reason` is the headline; raw signals stay below.
    """
    try:
        from signal_resolver import resolve_total
        from cohort_signals import evaluate_game_for_play

        def _count(direction):
            m = evaluate_game_for_play(g, 'v3_tot', direction) or []
            return len([x for x in m
                        if x.get('tier') in ('LOCK', 'STRONG_EDGE')
                        and not x.get('id', '').endswith('|any')])

        # Pull prop_reverse if available
        pr_signal = None
        try:
            import os as _os, requests as _rq
            today = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')
            r = _rq.get(
                f"{_os.environ.get('SUPABASE_URL')}/rest/v1/jerry_cache",
                params={'select': 'data',
                        'cache_key': f'eq.prop_reverse_signals_{today}'},
                headers={'apikey': _os.environ.get('SUPABASE_KEY'),
                         'Authorization': f"Bearer {_os.environ.get('SUPABASE_KEY')}"},
                timeout=3,
            )
            rows = r.json() if r.status_code == 200 else []
            if rows:
                data = rows[0].get('data', {})
                if isinstance(data, dict):
                    key = f"{g.get('away_team')} @ {g.get('home_team')}"
                    pr_signal = (data.get('signals') or {}).get(key)
        except Exception:
            pass

        return resolve_total(
            close_total=(g.get('close_total') or g.get('open_total')),
            v3_total=g.get('projected_total'),
            v4_total=g.get('model_pred_total'),
            jerry_total=g.get('jerry_pred_total'),
            cohort_over_strong_count=_count('over'),
            cohort_under_strong_count=_count('under'),
            prop_reverse=pr_signal,
            # v4 trust-gate context (2026-06-19 calibration)
            park_run_factor=g.get('park_run_factor'),
            temperature=g.get('temperature'),
            is_dome=bool(g.get('is_dome')),
        )
    except Exception:
        return None


def _side_resolver_block(g):
    """Compute resolver landing call for the game's SIDE (ML/RL direction).

    Parallel to _resolver_block but for sides. Jerry consumes this in the
    `The Play` section — when side fires STRONG+, surface it alongside (or
    instead of) the total play. Returns dict or None on failure.

    Added 2026-06-12 to fix Jerry surfacing only the total play when both
    total and side resolvers fire (caused public-vs-app contradiction on
    SD@BAL — public posted BAL ML STRONG, Jerry only said Over total).
    """
    try:
        from signal_resolver import resolve_side
        from cohort_signals import evaluate_game_for_play

        def _ct(play, direction):
            m = evaluate_game_for_play(g, play, direction) or []
            return len([x for x in m
                        if x.get('tier') in ('LOCK', 'STRONG_EDGE', 'LEAN')
                        and not x.get('id', '').endswith('|any')])

        ml_h = sum(_ct(p, 'home') for p in ('v3_ml', 'v4_ml', 'jerry_ml', 'conf_ml'))
        ml_a = sum(_ct(p, 'away') for p in ('v3_ml', 'v4_ml', 'jerry_ml', 'conf_ml'))
        rl_h = sum(_ct(p, 'home') for p in ('v3_rl', 'v4_rl'))
        rl_a = sum(_ct(p, 'away') for p in ('v3_rl', 'v4_rl'))

        # Pull prop_reverse signal (same lookup as _resolver_block)
        pr_signal = None
        try:
            import os as _os, requests as _rq
            today = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')
            r = _rq.get(
                f"{_os.environ.get('SUPABASE_URL')}/rest/v1/jerry_cache",
                params={'select': 'data',
                        'cache_key': f'eq.prop_reverse_signals_{today}'},
                headers={'apikey': _os.environ.get('SUPABASE_KEY'),
                         'Authorization': f"Bearer {_os.environ.get('SUPABASE_KEY')}"},
                timeout=3,
            )
            rows = r.json() if r.status_code == 200 else []
            if rows:
                data = rows[0].get('data', {})
                if isinstance(data, dict):
                    key = f"{g.get('away_team')} @ {g.get('home_team')}"
                    pr_signal = (data.get('signals') or {}).get(key)
        except Exception:
            pass

        side = resolve_side(
            close_spread=(g.get('close_spread') or g.get('open_spread')),
            v3_spread=g.get('projected_spread'),
            v4_spread=g.get('model_pred_spread'),
            jerry_spread=g.get('jerry_pred_spread'),
            ml_home_cohort_count=ml_h, ml_away_cohort_count=ml_a,
            rl_home_cohort_count=rl_h, rl_away_cohort_count=rl_a,
            confluence_net=g.get('signal_confluence_net'),
            prop_reverse=pr_signal,
        )
        # Translate HOME/AWAY direction → actual team name for Jerry
        if side and side.get('direction') in ('HOME', 'AWAY'):
            side = dict(side)  # don't mutate the resolver's own return
            picked_team = (g.get('home_team') if side['direction'] == 'HOME'
                           else g.get('away_team'))
            side['team'] = picked_team
            # Stamp ML odds so Jerry can name the price
            ml_field = 'home_ml_close' if side['direction'] == 'HOME' else 'away_ml_close'
            ml_open = 'home_ml_open' if side['direction'] == 'HOME' else 'away_ml_open'
            side['ml_odds'] = g.get(ml_field) or g.get(ml_open)
        return side
    except Exception:
        return None


def _cohort_signals_block(g):
    """Phase 1 read-only surface of matched cohort rules for this game.
    Gracefully None on any failure — Jerry reads continue to render."""
    try:
        from cohort_signals import summarize_for_struct
        return summarize_for_struct(g)
    except Exception:
        return None


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
    # 2026-08-28: LLM call disabled per docs/LLM_AUDIT.md kill #1.
    # This script's Jerry narrative was a DUPLICATE of generate_jerry_synthesis.py
    # which is now the sole source of jerry_reads.short_read/long_read.
    # jerry_cache.narrative here is legacy — Numbers Panel now reads from
    # jerry_reads via game_id join instead of the game_read_* cache key.
    # Set env DISABLE_LEGACY_GAME_READS_LLM=0 to temporarily re-enable
    # (e.g. if a display regression appears).
    if os.environ.get('DISABLE_LEGACY_GAME_READS_LLM', '1') == '1':
        return None
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
            # max_tokens raised 1100 -> 1800 on 2026-06-05 after Phase B
            # added career SP + full-staff team pitching context. Multiple
            # reads (NYM@SD, MIL@COL) were truncating mid-Play section at
            # 1100. 1800 absorbs the new density with headroom for varying
            # game complexity.
            json={"model": MODEL, "max_tokens": 1800, "messages": [{"role": "user", "content": prompt}]},
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
        "sport": "MLB",  # 2026-08-25 case fix — see NCAAF commit 1a71199e
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


# -------------------------------------------------- attribution validator
# Catches the recurring class of bugs where the LLM writes the wrong
# team for a pitcher or hitter in the narrative despite the struct
# being correct. Origin: 5/14 Suarez/Luzardo, 5/16 Tolle/Boston, 5/20
# Benge/Nationals, 6/6 Vazquez/Padres. Prompt rules have been added
# each time and Claude still occasionally hallucinates — the only
# permanent fix is post-LLM check + regen-or-discard.

import re as _re

_FACING_VERBS = (
    "facing", "vs\\.?", "vs ", "against", "matchup with", "matches up with",
    "takes on", "squares off against", "draws", "battles", "duels",
    "opposes", "starts against", "starts vs", "going against", "going up against",
)
_FACING_RE = "(?:" + "|".join(_FACING_VERBS) + ")"

_OWN_PLURAL_NOUNS = (
    "lineup", "lineups", "hitters", "offense", "bats", "bullpen", "batters",
    "rotation", "staff", "pen",
)
_OWN_PLURAL_RE = "(?:" + "|".join(_OWN_PLURAL_NOUNS) + ")"


_AMBIGUOUS_CITIES = {"Chicago", "New York", "Los Angeles"}


def _team_keywords(team_name):
    """Return the substrings that uniquely identify a team in prose.

    For "San Diego Padres" → ["Padres", "San Diego"]. For "Boston Red Sox"
    → ["Red Sox", "Sox", "Boston"]. For "Chicago Cubs" → ["Cubs"] (city
    dropped — Chicago is ambiguous with the White Sox). Athletics has no
    city since 2026, returns ["Athletics"].
    """
    if not team_name:
        return []
    parts = team_name.strip().split()
    if not parts:
        return []
    # Detect two-word nicknames: "Red Sox", "White Sox", "Blue Jays",
    # "Diamondbacks" is one word so not handled here.
    if len(parts) >= 3 and parts[-2] in ("Red", "White", "Blue") and parts[-1] in ("Sox", "Jays"):
        nicknames = [" ".join(parts[-2:]), parts[-1]]  # ["Red Sox", "Sox"]
        city_parts = parts[:-2]
    else:
        nicknames = [parts[-1]]
        city_parts = parts[:-1]

    keywords = list(nicknames)
    if city_parts:
        city = " ".join(city_parts)
        # Don't add ambiguous cities (Chicago = Cubs + White Sox, etc.)
        if city not in _AMBIGUOUS_CITIES and len(city) >= 4:
            keywords.append(city)
    return keywords


def _last_name(full_name):
    if not full_name:
        return None
    n = full_name.strip().split()
    if not n:
        return None
    # Drop common suffixes (Jr., II, III)
    last = n[-1]
    if last.lower().rstrip(".") in ("jr", "sr", "ii", "iii", "iv") and len(n) >= 2:
        last = n[-2]
    return last


def _detect_attribution_errors(narrative, struct):
    """Return list of human-readable error strings. Empty = clean.

    Conservative — only flags patterns that are almost certainly wrong:
      1) "<pitcher_last> <facing-verb> <own_team_keyword>"
      2) "<own_team_keyword> <plural_noun> <pitcher_last> will/projects/grades"
      3) "<hitter_last> (<wrong_team_keyword>)" or "<wrong_team_keyword>'s <hitter_last>"
    Avoids false positives on possessives where the team is correct
    (e.g. "Padres' Canning" when Canning IS on the Padres).
    """
    if not narrative or not isinstance(narrative, str):
        return []
    errors = []
    text = narrative

    pitchers = (struct.get("pitchers") or {})
    for side in ("home", "away"):
        p = pitchers.get(side) or {}
        last = _last_name(p.get("name"))
        own_team = p.get("own_team")
        if not last or not own_team:
            continue
        own_keywords = _team_keywords(own_team)
        for own_kw in own_keywords:
            # Pattern 1: "<last> ... facing ... <own_team>" within 80 chars
            pat1 = _re.compile(
                rf"\b{_re.escape(last)}\b[^.\n]{{0,80}}\b{_FACING_RE}\s+(?:the\s+)?{_re.escape(own_kw)}\b",
                _re.IGNORECASE,
            )
            m = pat1.search(text)
            if m:
                errors.append(
                    f"pitcher attribution: '{m.group(0).strip()}' — {p.get('name')} plays FOR {own_team}, not against them"
                )
                continue
            # Pattern 2: "<own_team>('s)? ... <plural_noun> ... <last> verb"
            # — the plural_noun anchor (lineup/bats/offense/bullpen) is what
            # distinguishes "Boston's lineup Tolle will punish" (BAD — Tolle
            # is on Boston) from "Cubs' Horton owns the Cardinals" (CLEAN,
            # no plural noun anchor between Cubs and Horton).
            pat2 = _re.compile(
                rf"\b{_re.escape(own_kw)}(?:'s)?\b[^.\n]{{0,30}}\b{_OWN_PLURAL_RE}\b[^.\n]{{0,60}}\b{_re.escape(last)}\b\s+(?:will|projects|grades|sees|owns|dominates|punishes|carves|attacks|handles|gets|should|figures|has|carries|leans|sits|brings|shuts|silences|matches|exploits|tortures|punish|dominate|carve|silence|exploit|shut|attack|handle)",
                _re.IGNORECASE,
            )
            m = pat2.search(text)
            if m:
                errors.append(
                    f"pitcher attribution: '{m.group(0).strip()}' — {p.get('name')} pitches FOR {own_team}, can't be facing their own {own_team} lineup"
                )

    # Hitter attribution from best_plays
    best = struct.get("best_plays") or []
    for play in best:
        name = play.get("player")
        team = play.get("team")
        last = _last_name(name)
        if not last or not team:
            continue
        # Find opposing team within the matchup
        matchup = struct.get("matchup") or ""
        if " @ " in matchup:
            away, home = matchup.split(" @ ", 1)
            opp_team = away if team.strip() == home.strip() else home if team.strip() == away.strip() else None
        else:
            opp_team = None
        if not opp_team:
            continue
        for opp_kw in _team_keywords(opp_team):
            # Pattern 3a: "<opp_team>('s)? <plural_noun-like noun> <last>" — e.g. "Nationals hitter Benge"
            # but skip when the noun is generic
            pat = _re.compile(
                rf"\b{_re.escape(opp_kw)}'?s?\s+(?:hitter|hitters|batter|batters|outfielder|infielder|catcher|slugger|bat|bats|starter|veteran|rookie|lineup)\s+(?:\w+\s+){{0,2}}\b{_re.escape(last)}\b",
                _re.IGNORECASE,
            )
            m = pat.search(text)
            if m:
                errors.append(
                    f"hitter attribution: '{m.group(0).strip()}' — {name} plays for {team}, NOT {opp_team}"
                )
                continue
            # Pattern 3b: "<last> (...<opp_team>...)" — parenthetical team tag wrong
            pat_b = _re.compile(
                rf"\b{_re.escape(last)}\b\s*\([^)]{{0,40}}{_re.escape(opp_kw)}[^)]{{0,20}}\)",
                _re.IGNORECASE,
            )
            m = pat_b.search(text)
            if m:
                errors.append(
                    f"hitter attribution: '{m.group(0).strip()}' — {name} plays for {team}, NOT {opp_team}"
                )

    return errors


def _correction_prompt(original_prompt, narrative, errors):
    """Build the retry prompt: prepend an explicit correction header."""
    bullets = "\n".join(f"  - {e}" for e in errors)
    header = (
        "ATTRIBUTION ERROR DETECTED in your previous narrative. The struct is "
        "canonical — these mistakes contradict it:\n"
        f"{bullets}\n\n"
        "Regenerate the read from scratch. Re-read `pitchers.home.name` / "
        "`pitchers.home.own_team` and `pitchers.away.name` / `pitchers.away.own_team` "
        "and verify each pitcher reference. Re-read `best_plays[i].team` for every "
        "hitter you name. Do not write a pitcher facing their own team. Do not "
        "label a player with a team they don't play for.\n\n"
        "Previous (rejected) narrative for reference only — do NOT repeat its "
        "mistakes:\n---\n"
        f"{narrative}\n---\n\n"
        "Now produce the corrected read using the same prompt below:\n\n"
    )
    return header + original_prompt


# -------------------------------------------------- number-attribution validator
# Catches LLM-invented percentages and W-L counts in Jerry's narrative.
# Origin: 2026-06-17 morning audit — buy-down cohort hit rates were
# hardcoded ("76% / 80% / 87%") and fed to Jerry as if live. Same class
# of error possible from LLM hallucination (Claude invents a number not
# in the struct). This validator catches both surfaces by requiring
# every percentage and W-L count in narrative to be traceable to the
# struct within tolerance.
#
# Phase 4 of docs/engine_clarity_refactor.md.
#
# ADVISORY-FIRST MODE: in v1 we LOG violations but do NOT retry on them.
# Want to measure false-positive rate over ~50 game reads before flipping
# to retry-on-fail. Set NUMBER_VALIDATOR_ENFORCE = True to enable retry.

NUMBER_VALIDATOR_ENFORCE = True  # 2026-06-23 flipped to ENFORCE per user
# request after sample reads showed duplicate-career-stat bug (Senga/Cabrera
# both rendered identical career numbers). 4 days of advisory data accumulated
# since 2026-06-19; pitcher-career exemption proven stable. Flipped to gate
# narratives that hallucinate percentages/WL pairs not present in struct.

# Minimum sample size for a quoted percentage to be considered "valid"
# without requiring an explicit n citation. Below this, the percentage
# must have n called out in the same sentence (or omitted entirely).
PCT_QUOTABLE_MIN_N = 30

_PCT_RE = _re.compile(r"(?<!\d)(\d{1,3}(?:\.\d{1,2})?)\s*%")
_WL_RE = _re.compile(r"(?<!\d)(\d{1,3})\s*-\s*(\d{1,3})(?!\d)")
# Patterns for sample-size citations that should follow a percentage:
# "n=42", "over 158 games", "across 50 starts", "based on 28-11",
# "in 600 attempts" — used to validate that quoted %s are sample-gated.
_N_PROXIMITY_RE = _re.compile(
    r"(?:n\s*=\s*\d+|\d+\s*(?:games|starts|attempts|samples|n)|"
    r"over\s+\d+|across\s+\d+|based\s+on\s+\d+|in\s+\d+|"
    r"(?:^|\s)\d+-\d+(?:\s+lifetime)?)",
    _re.IGNORECASE,
)
# Hedging words that signal imprecise quotes — give wider tolerance
_HEDGED_RE = _re.compile(
    r"(?:approximately|around|roughly|about|near|~|nearly)\s+\d{1,3}",
    _re.IGNORECASE,
)


def _walk_struct_numbers(obj, out=None):
    """Recursively collect all numeric values from struct.
    Returns a set of floats."""
    if out is None:
        out = set()
    if isinstance(obj, dict):
        for v in obj.values():
            _walk_struct_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk_struct_numbers(v, out)
    elif isinstance(obj, bool):
        pass  # bool is subclass of int — skip
    elif isinstance(obj, (int, float)) and not (isinstance(obj, float) and obj != obj):  # not NaN
        out.add(float(obj))
    elif isinstance(obj, str):
        # Parse percentages and decimals embedded in struct string values
        # (e.g. "76.2% backtest", "70.4% historical")
        for m in _re.finditer(r"(\d{1,3}(?:\.\d{1,3})?)\s*%?", obj):
            try:
                out.add(float(m.group(1)))
            except ValueError:
                pass
    return out


def _walk_struct_wl_pairs(obj, out=None):
    """Collect W-L pair patterns from struct.
    Both as structured fields (raw_wins/raw_losses, wins/losses)
    and as embedded strings ("28-11 lifetime")."""
    if out is None:
        out = set()
    if isinstance(obj, dict):
        # Structured field shapes the cohort engine uses
        for w_key, l_key in (("raw_wins", "raw_losses"), ("wins", "losses"), ("w", "l")):
            w = obj.get(w_key); l = obj.get(l_key)
            if isinstance(w, int) and isinstance(l, int):
                out.add((w, l))
        for v in obj.values():
            _walk_struct_wl_pairs(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk_struct_wl_pairs(v, out)
    elif isinstance(obj, str):
        for m in _re.finditer(r"(?<!\d)(\d{1,3})\s*-\s*(\d{1,3})(?!\d)", obj):
            try:
                out.add((int(m.group(1)), int(m.group(2))))
            except ValueError:
                pass
    return out


def _detect_number_hallucinations(narrative, struct, pct_tol=2.5):
    """Find percentages and W-L counts in narrative not traceable to struct.
    Returns list of human-readable error strings. Conservative — only
    flags obvious cases to keep false-positive rate low.

    Rules:
      - Percentages must match a struct number within pct_tol (default 2.5pp)
      - W-L pairs must appear in struct (either ordering accepted) — only
        flag pairs where W+L >= 15 (smaller pairs are likely game scores)
      - Numbers near hedging words (approximately, around, ~) get wider tol
    """
    if not narrative or not isinstance(narrative, str):
        return []

    struct_nums = _walk_struct_numbers(struct)
    struct_wl = _walk_struct_wl_pairs(struct)

    errors = []

    # ── Percentages ──
    for m in _PCT_RE.finditer(narrative):
        try:
            val = float(m.group(1))
        except ValueError:
            continue
        # Check if context within 30 chars suggests hedging — looser tolerance
        start = max(0, m.start() - 30)
        ctx = narrative[start:m.end()]
        tol = pct_tol * 2 if _HEDGED_RE.search(ctx) else pct_tol
        # Match val (0-100 form) against struct nums in same range.
        # Also try val/100 (decimal form) but ONLY against struct nums in
        # [0,1] — without this guard, 90% → 0.9 falsely matches xERA 4.3
        # within 5pp tolerance. Decimals representing percentages live in
        # [0,1]; matching against arbitrary struct floats is unsafe.
        decimal_form = val / 100.0
        matched = (
            any(abs(val - n) <= tol for n in struct_nums)
            or any(abs(decimal_form - n) <= (tol / 100.0)
                   for n in struct_nums if 0 <= n <= 1)
        )
        if not matched:
            errors.append(
                f"unverified percentage: '{m.group(0).strip()}' "
                f"(no struct value within ±{tol}pp)"
            )
            continue
        # ── Sample-size gating check (added 2026-06-18) ──
        # A percentage that matches a struct value but lacks any nearby
        # n citation should be flagged as ungated. Window: 50 chars before
        # AND after the % token. If no n pattern in window AND we can't
        # find a struct W-L pair that explains the n implicitly, flag.
        proximity_start = max(0, m.start() - 50)
        proximity_end = min(len(narrative), m.end() + 50)
        window = narrative[proximity_start:proximity_end]
        has_nearby_n = bool(_N_PROXIMITY_RE.search(window))
        if not has_nearby_n:
            # Check whether the matched struct value comes from a high-n
            # source (any W-L pair in struct with W+L >= PCT_QUOTABLE_MIN_N
            # implicitly backs this %). If no high-n backing exists either,
            # the % is ungated by sample size.
            has_high_n_backing = any(
                (w + l) >= PCT_QUOTABLE_MIN_N for w, l in struct_wl
            )
            if not has_high_n_backing:
                errors.append(
                    f"ungated percentage: '{m.group(0).strip()}' "
                    f"cited without nearby sample size and no struct W-L "
                    f"pair has n >= {PCT_QUOTABLE_MIN_N}"
                )

    # ── W-L pairs ──
    # Pitcher career records (e.g. "112-93 career 3.91 ERA / 1,786 IP")
    # appear in Jerry's full-staff pitching context and are NOT cohort
    # signals. Skip pairs that show up next to pitcher-career markers.
    # Markers: ERA, WHIP, IP, "career", "rotation", "starts" within 60
    # chars on either side. Audit on 2026-06-18 reads identified all
    # Phase 4 false positives as Nola/E-Rod career records.
    _PITCHER_CAREER_MARKERS = _re.compile(
        r"\b(?:ERA|WHIP|career|rotation|starts?|IP)\b",
        _re.IGNORECASE,
    )
    for m in _WL_RE.finditer(narrative):
        try:
            w = int(m.group(1)); l = int(m.group(2))
        except ValueError:
            continue
        # Skip small pairs (game scores like 7-3) — only cohort-sized counts
        if (w + l) < 15:
            continue
        # Pitcher career exemption: scan 60 chars before + 80 chars after.
        # If a pitcher-stat marker is present, the W-L is a pitcher career
        # record and shouldn't be validated as a cohort signal.
        ctx_start = max(0, m.start() - 60)
        ctx_end = min(len(narrative), m.end() + 80)
        ctx = narrative[ctx_start:ctx_end]
        if _PITCHER_CAREER_MARKERS.search(ctx):
            continue
        # Both orderings accepted (struct may store loss-wins in some shapes)
        if (w, l) not in struct_wl and (l, w) not in struct_wl:
            errors.append(
                f"unverified W-L count: '{m.group(0).strip()}' "
                f"(not in struct cohort signals)"
            )

    return errors


def _scrub_unverified_numbers(narrative, errors):
    """Remove specific numbers flagged by the number validator from
    narrative text. Used when NUMBER_VALIDATOR_ENFORCE is True and
    retries exhausted — keeps the narrative but strips the lies."""
    out = narrative
    for e in errors:
        # Extract the original text from error message ('<text>')
        m = _re.search(r"'([^']+)'", e)
        if m:
            out = out.replace(m.group(1), "[unverified]")
    return out


# -------------------------------------------------- cross-dim narrative validator
# Catches narrative reasoning that cites WRONG-DIMENSION signals to justify
# a pick. Origin: 2026-06-17 morning incident where I (Claude) read the
# sweat card's NRFI lock + ace-duel total signals and recommended HOU ML
# as POTD, conflating total signals into a side picks rationale. This
# class of error is structurally invited by Jerry struct mixing all
# dimensions in one prompt input.
#
# Phase 5 of docs/engine_clarity_refactor.md.
#
# Scoping: only enforces when the read has an UNAMBIGUOUS POTD lean
# (struct.is_potd + struct.potd_lean.type ∈ ml/spread/rl/over/under/total).
# General multi-pick game reads naturally discuss multiple dimensions
# and shouldn't be flagged. Only POTD-as-side or POTD-as-total reads
# need rigid dim purity.
#
# ADVISORY-FIRST: logs but does not retry. Flip CROSS_DIM_ENFORCE after
# baseline false-positive audit.

CROSS_DIM_ENFORCE = True  # 2026-06-19 — audit over n=9 reads showed 0%
# flag rate. Phase 5 patterns are tight (specific dim-leakage idioms only)
# and didn't fire any false positives on real narratives. Flipping ENFORCE
# so any future cross-dim leakage triggers retry + correction.

# Total-only signal keywords — illegitimate as primary rationale for a side pick.
# These describe total/inning dynamics, not which team wins.
_TOTAL_ONLY_PATTERNS = (
    r"\bboth pitchers (?:elite|in elite form|lights[- ]?out|aces?)\b",
    r"\bboth starters (?:elite|lights[- ]?out|in elite form|aces?)\b",
    r"\bace duel\b",
    r"\bNRFI (?:lock|signal|edge|score|sweet spot)\b",
    r"\bno runs (?:in the )?first inning\b",
    r"\b1st[- ]?inning (?:lock|suppression|signal)\b",
    r"\bxERA gap\b",
    r"\bpark[- ]?suppressed\b",
)

# Side-only signal keywords — illegitimate as primary rationale for a total pick.
_SIDE_ONLY_PATTERNS = (
    r"\bconfluence (?:net|edge|signals?)\b",
    # autofade pattern: match the bare word and multi-segment suffixes
    # like autofade_dog_high_conv. \b after underscore doesn't fire
    # (underscore is a word char) so we use \w* to cover any suffix.
    r"\bautofade\w*\b",
    r"\bhome dog\b",
    r"\bdawg cohort\b",
    r"\b(?:home|away)[- ]?favorite signal\b",
    r"\bspread[- ]?delta cohort\b",
)

_TOTAL_PATTERNS_RE = [_re.compile(p, _re.IGNORECASE) for p in _TOTAL_ONLY_PATTERNS]
_SIDE_PATTERNS_RE = [_re.compile(p, _re.IGNORECASE) for p in _SIDE_ONLY_PATTERNS]


def _classify_potd_dim(struct):
    """Return 'side' | 'total' | 'prop' | None for POTD-classified reads.
    Returns None for non-POTD reads (no cross-dim check applies)."""
    if not struct.get("is_potd"):
        return None
    lean = struct.get("potd_lean") or {}
    if isinstance(lean, str):
        lean_type = lean.lower()
    elif isinstance(lean, dict):
        lean_type = (lean.get("type") or lean.get("lean_bet") or "").lower()
    else:
        return None
    # Check "prop" FIRST — prop_type strings like "prop_er_over" contain
    # "over"/"under" substrings that would falsely match the total check.
    if "prop" in lean_type:
        return "prop"
    if any(k in lean_type for k in ("ml", "spread", "rl", "side")):
        return "side"
    if any(k in lean_type for k in ("over", "under", "total")):
        return "total"
    return None


def _detect_cross_dim_leakage(narrative, struct):
    """Find wrong-dimension signal citations in POTD narratives.
    Returns list of error strings. Conservative — only flags POTD reads
    with unambiguous dim classification.

    For SIDE POTDs: flags total-only signal patterns.
    For TOTAL POTDs: flags side-only signal patterns.
    PROP POTDs: not enforced yet (props are independent).
    Non-POTD reads: not enforced (multi-dim by design).
    """
    if not narrative or not isinstance(narrative, str):
        return []
    dim = _classify_potd_dim(struct)
    if dim not in ("side", "total"):
        return []  # only side/total POTDs validated

    errors = []
    patterns = _TOTAL_PATTERNS_RE if dim == "side" else _SIDE_PATTERNS_RE
    forbidden_label = "TOTAL" if dim == "side" else "SIDE"

    for p in patterns:
        m = p.search(narrative)
        if m:
            errors.append(
                f"{dim} POTD narrative cites {forbidden_label}-only signal: "
                f"'{m.group(0).strip()}' — {forbidden_label} signals don't "
                f"justify a {dim} pick"
            )
    return errors


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

        # Attribution validator: up to 2 corrective regen attempts.
        # If still broken, drop the narrative and store struct only —
        # better to show no read than a read that confuses pitchers
        # with their own team. See `_detect_attribution_errors`.
        MAX_RETRIES = 2
        for attempt in range(MAX_RETRIES + 1):
            if not narrative:
                break
            errors = _detect_attribution_errors(narrative, struct)
            if not errors:
                break
            if attempt < MAX_RETRIES:
                print(f"  ⚠️ {away} @ {home}: attribution errors (retry {attempt + 1}/{MAX_RETRIES}):")
                for e in errors:
                    print(f"      - {e}")
                narrative = call_claude(_correction_prompt(prompt, narrative, errors))
            else:
                print(f"  ⛔ {away} @ {home}: attribution errors persisted after {MAX_RETRIES} retries — discarding narrative, storing struct only:")
                for e in errors:
                    print(f"      - {e}")
                narrative = None

        # Number-attribution validator (Phase 4 of engine clarity refactor).
        # Advisory-first: log violations, don't retry. Flip NUMBER_VALIDATOR_ENFORCE
        # to True after the baseline false-positive rate is measured.
        if narrative:
            num_errors = _detect_number_hallucinations(narrative, struct)
            if num_errors:
                mode = "ENFORCE" if NUMBER_VALIDATOR_ENFORCE else "ADVISORY"
                print(f"  🔢 {away} @ {home}: number validator [{mode}] flagged {len(num_errors)}:")
                for e in num_errors:
                    print(f"      - {e}")
                if NUMBER_VALIDATOR_ENFORCE:
                    # One retry attempt with correction prompt asking Claude not
                    # to invent numbers. Persistent failures get scrubbed.
                    retry = call_claude(
                        _correction_prompt(prompt, narrative, num_errors)
                        .replace("ATTRIBUTION ERROR DETECTED",
                                 "NUMBER HALLUCINATION DETECTED")
                    )
                    if retry:
                        retry_errors = _detect_number_hallucinations(retry, struct)
                        if not retry_errors:
                            narrative = retry
                        else:
                            print(f"  ⛔ {away} @ {home}: numbers still unverified after retry — scrubbing")
                            narrative = _scrub_unverified_numbers(retry, retry_errors)

        # Cross-dimensional narrative validator (Phase 5).
        # Only enforces on POTD reads with unambiguous side/total classification.
        # Advisory-first; flip CROSS_DIM_ENFORCE after baseline audit.
        if narrative:
            cd_errors = _detect_cross_dim_leakage(narrative, struct)
            if cd_errors:
                mode = "ENFORCE" if CROSS_DIM_ENFORCE else "ADVISORY"
                print(f"  🔀 {away} @ {home}: cross-dim validator [{mode}] flagged {len(cd_errors)}:")
                for e in cd_errors:
                    print(f"      - {e}")
                if CROSS_DIM_ENFORCE:
                    retry = call_claude(
                        _correction_prompt(prompt, narrative, cd_errors)
                        .replace("ATTRIBUTION ERROR DETECTED",
                                 "CROSS-DIMENSIONAL SIGNAL LEAKAGE DETECTED")
                    )
                    if retry:
                        retry_errors = _detect_cross_dim_leakage(retry, struct)
                        if not retry_errors:
                            narrative = retry
                        else:
                            print(f"  ⛔ {away} @ {home}: cross-dim still leaking after retry — keeping original (manual review required)")

        if not narrative:
            print(f"  • {away} @ {home}: no narrative (claude failed / no key / attribution rejected) — storing struct only")
        if upsert_read(g, narrative or "", struct):
            print(f"  ✓ {away} @ {home} ({len(struct['best_plays'])} plays{', POTD' if struct['is_potd'] else ''})")
            done += 1
        if limit and done >= limit:
            break

    print(f"=== wrote {done} game reads ===")


if __name__ == "__main__":
    run()
