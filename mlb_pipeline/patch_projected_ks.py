"""Patch today's mlb_game_context with projected_ks for each starter.

Runs AFTER compute_pitcher_class_projections.py (which writes the JSON cache
plus pitcher_projections Supabase table) and BEFORE generate_props.py + the
sweat card + Jerry game reads. Reads the EXACT same source the K scorer
uses so the published K-Over prop projection and the social-copy projection
are guaranteed identical.

Source-of-truth fallback chain (mirrors generate_props.py:get_pitcher_projection):
  1. data/pitcher_class_projections.json — l7_rolling.avg_k (preferred)
  2. pitcher_projections Supabase table — same data, more recent rebuild
  3. mlb_pitcher_stats.k_pct × 22 BF (~5.5 IP × 4 BF/IP) — fallback

Trigger: 2026-05-27 DeGrom social-copy gap. The implied "Over projected"
recommendation had no number attached; backing it out from K% × IP gave
~5.2; the book line was 5.5; he delivered 6. Won by luck, not process.

Usage:
    python patch_projected_ks.py [--date YYYY-MM-DD]
"""
import os
import sys
import json
import argparse
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SB = os.environ["SUPABASE_URL"]
KEY = os.environ["SUPABASE_KEY"]
H_READ = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
H_WRITE = {**H_READ, "Content-Type": "application/json", "Prefer": "return=minimal"}

CACHE_PATH = Path(__file__).parent / "data" / "pitcher_class_projections.json"


def today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def load_json_cache():
    if not CACHE_PATH.exists():
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {v["name"].lower(): v for v in data.values() if v.get("name")}
    except Exception:
        return {}


def get_supabase_projection(name):
    try:
        q = urllib.parse.quote(name)
        r = urllib.request.urlopen(
            urllib.request.Request(
                f"{SB}/rest/v1/pitcher_projections?player_name=eq.{q}&select=l7_rolling&limit=1",
                headers=H_READ,
            ),
            timeout=10,
        )
        rows = json.loads(r.read())
        if rows:
            return rows[0].get("l7_rolling")
    except Exception:
        pass
    return None


def get_pitcher_k_pct(name):
    try:
        q = urllib.parse.quote(name)
        r = urllib.request.urlopen(
            urllib.request.Request(
                f"{SB}/rest/v1/mlb_pitcher_stats?player_name=eq.{q}&season=eq.2026&select=k_pct&limit=1",
                headers=H_READ,
            ),
            timeout=10,
        )
        rows = json.loads(r.read())
        if rows and rows[0].get("k_pct") is not None:
            k = rows[0]["k_pct"]
            # mlb_pitcher_stats stores k_pct as a decimal (0.298) for some pitchers
            # and as integer pct (29.8) for others — normalize both to integer pct
            return float(k) * 100 if float(k) < 1.0 else float(k)
    except Exception:
        pass
    return None


def project_ks(name, json_cache):
    """Same chain as generate_props.py get_pitcher_projection."""
    # 1. JSON cache
    entry = json_cache.get((name or "").lower())
    if entry:
        l7 = (entry.get("l7_rolling") or {}).get("avg_k")
        if l7 is not None:
            return round(float(l7), 1), "json_l7"
    # 2. Supabase pitcher_projections
    sb_l7 = get_supabase_projection(name)
    if sb_l7 and sb_l7.get("avg_k") is not None:
        return round(float(sb_l7["avg_k"]), 1), "sb_l7"
    # 3. Fallback: season K% × 22 BF
    k_pct = get_pitcher_k_pct(name)
    if k_pct is not None:
        return round(k_pct / 100 * 22, 1), "k_pct_22bf"
    return None, "no_data"


def fetch_today_games(date_str):
    url = (
        f"{SB}/rest/v1/mlb_game_context?game_date=eq.{date_str}"
        f"&select=id,away_pitcher,home_pitcher,"
        f"away_pitcher_projected_ks,home_pitcher_projected_ks"
    )
    r = urllib.request.urlopen(urllib.request.Request(url, headers=H_READ), timeout=20)
    return json.loads(r.read())


def patch_row(row_id, payload):
    url = f"{SB}/rest/v1/mlb_game_context?id=eq.{row_id}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=H_WRITE, method="PATCH"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.status in (200, 204)
    except urllib.error.HTTPError as e:
        print(f"    ⚠️ PATCH failed {e.code}: {e.read().decode()[:200]}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=today_et(), help="Slate date (YYYY-MM-DD)")
    args = parser.parse_args()

    date_str = args.date
    print(f"=== Patch projected_ks for {date_str} ===")
    json_cache = load_json_cache()
    print(f"  loaded {len(json_cache)} pitchers from JSON cache")

    games = fetch_today_games(date_str)
    print(f"  {len(games)} games")

    updated = 0
    for g in games:
        payload = {}
        for side in ("away", "home"):
            name = g.get(f"{side}_pitcher")
            old = g.get(f"{side}_pitcher_projected_ks")
            if not name:
                continue
            proj, source = project_ks(name, json_cache)
            if proj is None:
                if old is not None:
                    payload[f"{side}_pitcher_projected_ks"] = None
                    print(f"  {name}: CLEARED (no projection data)")
                continue
            if old != proj:
                payload[f"{side}_pitcher_projected_ks"] = proj
                print(f"  {name}: projected_ks = {proj} [{source}]  (was {old})")
        if payload:
            if patch_row(g["id"], payload):
                updated += 1
    print(f"  patched {updated} rows")


if __name__ == "__main__":
    main()
