"""Batter-vs-Pitcher (BvP) matchup data — 2026-07-21 foundation.

Top MLB models track SPECIFIC batter career splits vs SPECIFIC pitchers
(e.g. Freeman is 8-for-12 lifetime vs Wheeler even though team-wide LAD wRC+
vs RHP is average). We currently use only team wRC+ vs opposing SP hand,
missing this level of granularity.

MLB Stats API endpoint for BvP:
  https://statsapi.mlb.com/api/v1/people/{batter_id}/stats?stats=vsPlayer
    &opposingPlayerId={pitcher_id}&group=hitting

Rate limits: MLB Stats API is public, 30 req/min soft. Batch by cache and
skip already-fresh lookups.

Usage:
    from batter_vs_pitcher import get_bvp_line
    line = get_bvp_line(batter_id=545361, pitcher_id=592332)
    # -> {'ab': 12, 'h': 8, 'avg': .667, 'ops': 1.320, 'hr': 2, 'so': 1}

INTEGRATION POINTS (this file is the data layer only):
  - generate_props.py::score_batter_hits_over — add BvP boost/fade
  - game_context.py — enrich mlb_game_context with per-batter BvP summary
  - jerry_cache game_read — surface BvP mastery/vulnerability for top-of-order

MIN_AB threshold: only trust BvP with >= 6 lifetime ABs (below that is
too noisy — MLB.com displays but they're not meaningful).
"""
import os
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests

MLB_BASE = "https://statsapi.mlb.com/api/v1"
MIN_AB_THRESHOLD = 6
CACHE_TTL_HOURS = 168  # 7d — BvP rarely changes intra-week
UA = {"User-Agent": "Mozilla/5.0 (SweatLocker BvP fetch)"}

# In-memory cache — populated on first hit, persisted via jerry_cache below
_MEMORY_CACHE = {}


def _cache_key(batter_id: int, pitcher_id: int) -> str:
    return f"bvp:{batter_id}:{pitcher_id}"


def get_bvp_line(batter_id: int, pitcher_id: int) -> Optional[dict]:
    """Return BvP career stat line for a batter/pitcher pairing.

    Returns None if:
      - The pairing has fewer than MIN_AB_THRESHOLD career ABs (too noisy)
      - API fetch failed
      - Either ID is missing/invalid

    Returns dict with: ab, h, hr, so, bb, rbi, avg, obp, slg, ops on hit.
    """
    if not batter_id or not pitcher_id:
        return None
    key = _cache_key(batter_id, pitcher_id)
    if key in _MEMORY_CACHE:
        return _MEMORY_CACHE[key]

    try:
        url = (f"{MLB_BASE}/people/{batter_id}/stats"
               f"?stats=vsPlayer&opposingPlayerId={pitcher_id}"
               f"&group=hitting&sportId=1")
        r = requests.get(url, headers=UA, timeout=8)
        if r.status_code != 200:
            _MEMORY_CACHE[key] = None
            return None
        data = r.json()
        stats = (data.get("stats") or [])
        if not stats:
            _MEMORY_CACHE[key] = None
            return None
        splits = stats[0].get("splits") or []
        if not splits:
            _MEMORY_CACHE[key] = None
            return None
        # Aggregate across all splits (usually one)
        agg = {"ab": 0, "h": 0, "hr": 0, "so": 0, "bb": 0, "rbi": 0}
        for s in splits:
            stat = s.get("stat", {}) or {}
            agg["ab"] += int(stat.get("atBats", 0) or 0)
            agg["h"] += int(stat.get("hits", 0) or 0)
            agg["hr"] += int(stat.get("homeRuns", 0) or 0)
            agg["so"] += int(stat.get("strikeOuts", 0) or 0)
            agg["bb"] += int(stat.get("baseOnBalls", 0) or 0)
            agg["rbi"] += int(stat.get("rbi", 0) or 0)
        if agg["ab"] < MIN_AB_THRESHOLD:
            _MEMORY_CACHE[key] = None
            return None
        # Compute rates
        result = dict(agg)
        result["avg"] = round(agg["h"] / agg["ab"], 3)
        # OBP proxy (missing HBP/SF from this endpoint)
        pa = agg["ab"] + agg["bb"]
        result["obp"] = round((agg["h"] + agg["bb"]) / pa, 3) if pa else 0.0
        # SLG requires 2b/3b — MLB Stats returns them in the split
        tb = 0
        for s in splits:
            stat = s.get("stat", {}) or {}
            singles = int(stat.get("hits", 0) or 0) - int(stat.get("doubles", 0) or 0) \
                      - int(stat.get("triples", 0) or 0) - int(stat.get("homeRuns", 0) or 0)
            tb += singles + 2 * int(stat.get("doubles", 0) or 0) + \
                  3 * int(stat.get("triples", 0) or 0) + 4 * int(stat.get("homeRuns", 0) or 0)
        result["slg"] = round(tb / agg["ab"], 3) if agg["ab"] else 0.0
        result["ops"] = round(result["obp"] + result["slg"], 3)
        _MEMORY_CACHE[key] = result
        return result
    except Exception:
        _MEMORY_CACHE[key] = None
        return None


def classify_bvp(bvp_line: dict) -> Optional[str]:
    """Classify a BvP line as MASTERY, TROUBLE, or NEUTRAL.

    Returns one of:
      'BATTER_MASTERY'  — .350+ AVG or .900+ OPS on n>=8 AB (batter owns pitcher)
      'BATTER_TROUBLE'  — .180- AVG or .500- OPS on n>=8 AB (pitcher owns batter)
      'NEUTRAL'         — middle band or thin sample
    """
    if not bvp_line:
        return None
    ab = bvp_line.get("ab", 0)
    if ab < 8:
        return "NEUTRAL"
    avg = bvp_line.get("avg", 0)
    ops = bvp_line.get("ops", 0)
    if avg >= 0.350 or ops >= 0.900:
        return "BATTER_MASTERY"
    if avg <= 0.180 or ops <= 0.500:
        return "BATTER_TROUBLE"
    return "NEUTRAL"


def get_lineup_bvp_summary(lineup: list, pitcher_id: int) -> dict:
    """Aggregate BvP metrics for an entire lineup against one pitcher.

    Args:
      lineup: list of dicts with keys 'batter_id' and 'name' (from lineup_confirmed)
      pitcher_id: opposing SP's MLB ID

    Returns:
      {
        'lineup_ops_vs_sp': avg OPS across lineup (weighted by AB),
        'mastery_count': number of BATTER_MASTERY hitters in lineup,
        'trouble_count': number of BATTER_TROUBLE hitters,
        'coverage': fraction of lineup with meaningful (>=6 AB) BvP data,
        'per_batter': list of {name, ab, avg, ops, classification},
      }
    """
    total_ab = 0
    total_h = 0
    total_pa = 0
    total_tb = 0
    mastery = 0
    trouble = 0
    covered = 0
    per_batter = []
    for entry in lineup or []:
        bid = entry.get("batter_id")
        name = entry.get("name")
        bvp = get_bvp_line(bid, pitcher_id) if bid else None
        if bvp:
            covered += 1
            total_ab += bvp["ab"]
            total_h += bvp["h"]
            total_pa += bvp["ab"] + bvp["bb"]
            total_tb += int(round(bvp["slg"] * bvp["ab"]))
            cls = classify_bvp(bvp)
            if cls == "BATTER_MASTERY": mastery += 1
            elif cls == "BATTER_TROUBLE": trouble += 1
            per_batter.append({
                "name": name, "ab": bvp["ab"], "avg": bvp["avg"],
                "ops": bvp["ops"], "classification": cls,
            })
        else:
            per_batter.append({"name": name, "ab": 0, "classification": None})
    if not total_pa:
        return {
            "lineup_ops_vs_sp": None, "mastery_count": mastery,
            "trouble_count": trouble, "coverage": 0.0, "per_batter": per_batter,
        }
    obp = (total_h + (total_pa - total_ab)) / total_pa
    slg = total_tb / total_ab if total_ab else 0.0
    return {
        "lineup_ops_vs_sp": round(obp + slg, 3),
        "mastery_count": mastery,
        "trouble_count": trouble,
        "coverage": round(covered / len(lineup), 2) if lineup else 0.0,
        "per_batter": per_batter,
    }


if __name__ == "__main__":
    # Quick sanity check — Freddie Freeman vs Zack Wheeler
    # Freeman id 518692, Wheeler id 554430
    line = get_bvp_line(518692, 554430)
    print(f"Freeman vs Wheeler: {line}")
    print(f"Classification: {classify_bvp(line)}")
