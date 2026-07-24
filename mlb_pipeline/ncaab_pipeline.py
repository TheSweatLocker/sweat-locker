"""NCAAB pipeline — server-side KenPom puller (Phase 1 prep).

Replaces the client-side KenPom fetch which exposed the API key in the app
bundle and ran per-user. Runs on the weekly Monday cron during regular
season. Pulls 365 D1 teams from KenPom, joins ratings + four-factors,
normalizes via ncaab_team_aliases, upserts to ncaab_team_stats.

Accepts --season to backfill historical snapshots. For seasons past their
final game, KenPom returns the end-of-season ratings — usable as an
approximation for backtest cohort work (with the known caveat that
end-of-season stats reflect the FULL season, not "as-of" any single date).

Required SQL: 20260506d_ncaab_foundation.sql

Usage:
    python ncaab_pipeline.py                    # current season (2025-26)
    python ncaab_pipeline.py --season 2024-25   # historical backfill
"""
import argparse
import os
import sys
import time
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
KENPOM_KEY = os.environ.get("KENPOM_KEY") or os.environ.get("EXPO_PUBLIC_KENPOM_KEY")

# Reconfigure stdout for UTF-8 on Windows (cp1252 default crashes on emoji)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
WRITE_HEADERS = {**HEADERS, "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"}


def parse_season(season_str: str) -> tuple[str, int]:
    """Convert '2025-26' → ('2025-26', 2026). KenPom uses end-of-season year."""
    if '-' not in season_str:
        raise ValueError(f"season must be like '2025-26', got {season_str!r}")
    start, end = season_str.split('-')
    start_year = int(start)
    # '2025-26' → end year 2026 (2-digit end suffix)
    end_year = int(f'{start_year // 100}{end}') if len(end) == 2 else int(end)
    return season_str, end_year


def fetch_kenpom(endpoint, season_year):
    if not KENPOM_KEY:
        print("  ❌ Missing KENPOM_KEY env var")
        return []
    try:
        r = requests.get(
            "https://kenpom.com/api.php",
            params={"endpoint": endpoint, "y": season_year},
            headers={"Authorization": f"Bearer {KENPOM_KEY}"},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"  KenPom {endpoint} HTTP {r.status_code}: {r.text[:200]}")
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"  KenPom {endpoint} error: {e}")
        return []


def fetch_alias_map():
    """Build kenpom_name → canonical_name lookup. Falls back to identity
    when alias table is empty (Phase 1 prep — alias seed runs separately)."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/ncaab_team_aliases?select=canonical_name,kenpom_name",
        headers=HEADERS, timeout=15,
    )
    if r.status_code != 200:
        return {}
    return {row["kenpom_name"]: row["canonical_name"]
            for row in r.json() if row.get("kenpom_name")}


def upsert_team_stats(rows):
    if not rows:
        return False
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/ncaab_team_stats?on_conflict=team,season",
        headers=WRITE_HEADERS, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f"  Upsert error {r.status_code}: {r.text[:300]}")
        return False
    return True


def run(season: str = "2025-26"):
    season_key, season_year = parse_season(season)
    print(f"=== NCAAB Phase 1 — KenPom snapshot ({season_key}, y={season_year}) ===")
    ratings = fetch_kenpom("ratings", season_year)
    time.sleep(1)
    four_factors = fetch_kenpom("four-factors", season_year)
    if not ratings:
        print("No KenPom data — bailing")
        return
    print(f"  Ratings: {len(ratings)} teams | Four-factors: {len(four_factors)} teams")

    ff_map = {t.get("TeamName"): t for t in four_factors}
    alias_map = fetch_alias_map()
    print(f"  Alias map: {len(alias_map)} entries (identity fallback when missing)")

    rows = []
    for t in ratings:
        kp_name = t.get("TeamName")
        if not kp_name:
            continue
        ff = ff_map.get(kp_name, {})
        canonical = alias_map.get(kp_name, kp_name)  # identity fallback
        try:
            adj_oe = float(t.get("AdjOE") or 0)
            adj_de = float(t.get("AdjDE") or 0)
        except (TypeError, ValueError):
            continue
        rows.append({
            "team": canonical,
            "season": season_key,
            "conference": t.get("ConfShort") or None,
            "adj_oe": adj_oe,
            "adj_de": adj_de,
            "adj_em": round(adj_oe - adj_de, 4),
            "adj_oe_rank": int(t.get("RankAdjOE") or 0) or None,
            "adj_de_rank": int(t.get("RankAdjDE") or 0) or None,
            "tempo": float(t.get("AdjTempo") or 0) or None,
            "tempo_rank": int(t.get("RankAdjTempo") or 0) or None,
            "efg_o": float(ff.get("eFG_Pct") or 0) or None,
            "efg_o_rank": int(ff.get("RankeFG_Pct") or 0) or None,
            "to_o": float(ff.get("TO_Pct") or 0) or None,
            "to_o_rank": int(ff.get("RankTO_Pct") or 0) or None,
            "or_o": float(ff.get("OR_Pct") or 0) or None,
            "or_o_rank": int(ff.get("RankOR_Pct") or 0) or None,
            "ftr_o": float(ff.get("FT_Rate") or 0) or None,
            "ftr_o_rank": int(ff.get("RankFT_Rate") or 0) or None,
            "efg_d": float(ff.get("DeFG_Pct") or 0) or None,
            "efg_d_rank": int(ff.get("RankDeFG_Pct") or 0) or None,
            "to_d": float(ff.get("DTO_Pct") or 0) or None,
            "to_d_rank": int(ff.get("RankDTO_Pct") or 0) or None,
            "or_d": float(ff.get("DOR_Pct") or 0) or None,
            "or_d_rank": int(ff.get("RankDOR_Pct") or 0) or None,
            "ftr_d": float(ff.get("DFT_Rate") or 0) or None,
            "ftr_d_rank": int(ff.get("RankDFT_Rate") or 0) or None,
            "wins": int(t.get("Wins") or 0),
            "losses": int(t.get("Losses") or 0),
            "luck": float(t.get("Luck") or 0) or None,
            "sos": float(t.get("SOS") or 0) or None,
            "seed": int(t.get("Seed")) if t.get("Seed") else None,
            "coach": t.get("Coach") or None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    if upsert_team_stats(rows):
        print(f"  ✅ Upserted {len(rows)} team rows")
    else:
        print(f"  ❌ Upsert failed")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025-26",
                    help="Season like '2025-26'. Default: current 2025-26.")
    args = ap.parse_args()
    run(season=args.season)
