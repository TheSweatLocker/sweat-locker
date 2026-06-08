"""
Fetch last-5-starts actuals for every starter on today's slate.

Closes the recency-data gap surfaced 6/7 (Baz outs / Flaherty hits /
Cameron ER all needed L5 data the user pulled manually because our
projections weren't using it). Stores in jerry_cache so reads + props
scorer can consume without a schema migration.

OUTPUT (per game): jerry_cache row with
    cache_key = 'pitcher_l5_{game_date}_{game_id}'
    data = {
        'home': { name, mlb_id, starts: [...], avg: {...} },
        'away': { name, mlb_id, starts: [...], avg: {...} },
    }

Each start row has: date, outs, ks, bb, hits, er, ip_str.
avg has: outs, ks, bb, hits, er over the L5 window.

USAGE:
    python compute_l5_pitcher_actuals.py            — today's slate
    python compute_l5_pitcher_actuals.py --date 2026-06-07
"""
import os
import sys
import json
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
WRITE_HEADERS = {**HEADERS, "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"}


def today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def _ip_to_outs(ip_str):
    """MLB Stats API reports IP as '6.2' meaning 6 innings + 2 outs = 20 outs.
    Convert reliably (the .1/.2 are LITERAL outs, not decimal fractions)."""
    if ip_str is None:
        return None
    s = str(ip_str)
    if "." in s:
        whole, frac = s.split(".", 1)
    else:
        whole, frac = s, "0"
    try:
        return int(whole) * 3 + int(frac)
    except ValueError:
        return None


def _find_player_id(full_name):
    """Match a pitcher name -> MLB personId. Uses the active-2026 roster.
    Cached per name within a single run."""
    if not full_name:
        return None
    if full_name in _PLAYER_CACHE:
        return _PLAYER_CACHE[full_name]
    if not _PLAYER_INDEX:
        _build_player_index()
    pid = _PLAYER_INDEX.get(full_name.lower())
    _PLAYER_CACHE[full_name] = pid
    return pid


_PLAYER_INDEX = {}
_PLAYER_CACHE = {}


def _build_player_index():
    """Load the active 2026 roster once. ~1200 players."""
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/sports/1/players?season=2026",
            timeout=20,
        )
        if r.status_code == 200:
            for p in r.json().get("people", []):
                name = p.get("fullName") or ""
                if name:
                    _PLAYER_INDEX[name.lower()] = p.get("id")
    except requests.RequestException as e:
        print(f"  !player index load failed: {e}")


def fetch_l5_starts(pitcher_name, season=2026):
    """Pull last-5 STARTS (filter on gamesStarted=1) for a pitcher.
    Returns a dict with starts[] + averages, or None on miss."""
    pid = _find_player_id(pitcher_name)
    if not pid:
        return None
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{pid}/stats"
            f"?stats=gameLog&group=pitching&season={season}",
            timeout=15,
        )
        if r.status_code != 200:
            return None
        splits = r.json().get("stats", [{}])[0].get("splits", []) or []
    except (requests.RequestException, ValueError, KeyError):
        return None
    # Filter to actual starts (relievers' gameLog rows have gamesStarted=0)
    starts = [s for s in splits if (s.get("stat") or {}).get("gamesStarted") == 1]
    last5 = starts[-5:]
    if not last5:
        return None
    rows = []
    for s in last5:
        st = s.get("stat") or {}
        ip_str = st.get("inningsPitched")
        rows.append({
            "date": s.get("date"),
            "ip": ip_str,
            "outs": _ip_to_outs(ip_str),
            "ks": st.get("strikeOuts"),
            "bb": st.get("baseOnBalls"),
            "hits": st.get("hits"),
            "er": st.get("earnedRuns"),
            "opp": (s.get("opponent") or {}).get("name"),
        })
    # Averages (skip None values defensively)
    def _avg(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None
    return {
        "name": pitcher_name,
        "mlb_id": pid,
        "starts": rows,
        "avg": {
            "outs": _avg("outs"),
            "ks": _avg("ks"),
            "bb": _avg("bb"),
            "hits": _avg("hits"),
            "er": _avg("er"),
        },
        "n_starts": len(rows),
    }


def upsert_l5(game_date, game_id, payload):
    """Write to jerry_cache. cache_key follows the same pattern as
    other game-keyed cache entries."""
    key = f"pitcher_l5_{game_date}_{game_id}"
    body = {
        "cache_key": key,
        "game_id": key,
        "sport": "mlb",
        "narrative": "",  # NOT NULL column
        "data": json.dumps(payload),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=cache_key",
        headers=WRITE_HEADERS, json=body, timeout=15,
    )
    return r.status_code in (200, 201, 204)


def run(game_date=None):
    if game_date is None:
        game_date = today_et()
    print(f"=== L5 pitcher actuals for {game_date} ===")
    games = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context"
        f"?game_date=eq.{game_date}"
        f"&select=game_id,home_team,away_team,home_pitcher,away_pitcher",
        headers=HEADERS, timeout=15,
    ).json()
    if not games:
        print("  No games found for date.")
        return
    print(f"  {len(games)} games — pulling L5 per starter")
    written = 0
    for g in games:
        gid = g.get("game_id")
        h_name = g.get("home_pitcher")
        a_name = g.get("away_pitcher")
        h_l5 = fetch_l5_starts(h_name) if h_name else None
        a_l5 = fetch_l5_starts(a_name) if a_name else None
        if not h_l5 and not a_l5:
            print(f"  • {a_name} @ {h_name}: no L5 data on either starter — skip")
            continue
        payload = {"home": h_l5, "away": a_l5}
        if upsert_l5(game_date, gid, payload):
            h_avg = (h_l5 or {}).get("avg") or {}
            a_avg = (a_l5 or {}).get("avg") or {}
            print(f"  [ok]{a_name:<22} @ {h_name:<22} | "
                  f"away L5 avg outs={a_avg.get('outs')}, ks={a_avg.get('ks')} | "
                  f"home L5 avg outs={h_avg.get('outs')}, ks={h_avg.get('ks')}")
            written += 1
        else:
            print(f"  !upsert failed for {gid}")
        # Polite throttle so we don't hammer statsapi
        time.sleep(0.3)
    print(f"=== wrote {written} L5 cache rows ===")


if __name__ == "__main__":
    date = None
    if "--date" in sys.argv:
        try:
            date = sys.argv[sys.argv.index("--date") + 1]
        except (IndexError, ValueError):
            pass
    run(date)
