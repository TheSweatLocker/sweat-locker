"""Auto-compute rolling tier hit rates from resolved games.

Replaces manual audits ("352-game NRFI tier audit", "PRIME confluence ML
backtest") with automated weekly reports. Stores per-tier hit rates in
mlb_tier_calibration so the app + Jerry's Track Record can show LIVE rates
instead of stale numbers.

Computes:
- NRFI tier rolling hit rates (90-94 PRIME, 80-89, 70-79, 60-69, 50-59, <=40, 95+)
- ML tier rolling hit rates (PRIME confluence ≥+4, STRONG ≥+2, LEAN ≥+1, vs zero/negative)
- Spread tier rolling hit rates (delta ≥ 2, 1.5-2, 1.0-1.5, <1.0)
- Bucketed by 7-day, 30-day, season-to-date windows

Usage:
    python audit_tier_calibration.py
"""

import os
import sys
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def sb_get(table, params, range_header=None):
    qs = urllib.parse.urlencode(params, safe=",.()")
    url = f"{SUPABASE_URL}/rest/v1/{table}?{qs}"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    if range_header:
        headers["Range"] = range_header
        headers["Prefer"] = "count=exact"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"  Supabase {table} error {e.code}: {e.read().decode()[:200]}")
        return []


def sb_upsert(table, rows, on_conflict):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    req = urllib.request.Request(
        url, method="POST", headers=headers,
        data=json.dumps(rows).encode("utf-8"),
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status in (200, 201, 204)
    except urllib.error.HTTPError as e:
        print(f"  Upsert {table} error {e.code}: {e.read().decode()[:300]}")
        return False


def fetch_all_resolved():
    """Pull all resolved games with NRFI + ML + confluence + spread fields."""
    all_rows = []
    offset = 0
    while True:
        rows = sb_get("mlb_game_results", {
            "nrfi_result": "not.is.null",
            "select": "game_date,nrfi_score,nrfi_result,signal_confluence_net,spread_delta,home_win,close_spread,home_spread_covered",
            "order": "game_date.asc",
            "limit": "1000",
            "offset": str(offset),
        })
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000
    return all_rows


def classify_nrfi_tier(score):
    if score is None:
        return None
    s = int(score)
    if s >= 95:
        return "nrfi_volatile_95plus"
    if s >= 90:
        return "nrfi_prime_90_94"
    if s >= 80:
        return "nrfi_dead_80_89"
    if s >= 70:
        return "nrfi_lean_70_79"
    if s >= 60:
        return "nrfi_60_69"
    if s >= 50:
        return "nrfi_neutral_50_59"
    if s <= 40:
        return "yrfi_lean_le40"
    return None


def classify_confluence_tier(net):
    if net is None:
        return None
    n = int(net)
    if n >= 4:
        return "confluence_prime_ge4"
    if n >= 2:
        return "confluence_strong_2_3"
    if n >= 1:
        return "confluence_lean_1"
    if n <= -1:
        return "confluence_negative"
    return "confluence_zero"


def classify_spread_delta_tier(delta):
    if delta is None:
        return None
    try:
        d = abs(float(delta))
    except (TypeError, ValueError):
        return None
    if d >= 2.0:
        return "spread_delta_ge2"
    if d >= 1.5:
        return "spread_delta_1_5_2"
    if d >= 1.0:
        return "spread_delta_1_1_5"
    return "spread_delta_lt1"


def classify_autofade_cohort(spread_delta, close_spread, confluence_net):
    """Mirrors auto_fade.cohort_for_pick using only fields available in
    mlb_game_results. Without ml odds we bucket on RL direction agreement
    only — this matches what auto_fade does when ml_market_home is None.

    spread_delta sign convention (per game_context.py): positive = model
    favors home; close_spread negative = home is RL favorite.
    """
    if spread_delta is None or close_spread is None:
        return None
    try:
        sd = float(spread_delta)
        cs = float(close_spread)
    except (TypeError, ValueError):
        return None
    cn = int(confluence_net) if confluence_net is not None else 0
    model_home = sd > 0
    rl_market_home = cs < 0
    agrees = model_home == rl_market_home
    corrected_delta_abs = abs(sd + cs)
    if not agrees:
        if cn >= 2:
            return "autofade_dog_high_conv"
        return "autofade_dog"
    if corrected_delta_abs >= 1.5:
        return "autofade_chalk_high_mag"
    return "autofade_chalk"


def compute_window_rates(rows, days_back, end_date):
    """Compute hit rate per tier within rolling window ending end_date."""
    cutoff = end_date - timedelta(days=days_back)
    in_window = [
        r for r in rows
        if r.get("game_date") and datetime.strptime(r["game_date"], "%Y-%m-%d").date() >= cutoff
    ]

    tier_stats = defaultdict(lambda: {"hits": 0, "total": 0})

    for r in in_window:
        # NRFI tier
        nrfi_tier = classify_nrfi_tier(r.get("nrfi_score"))
        if nrfi_tier:
            res = (r.get("nrfi_result") or "").upper()
            if res in ("NRFI", "YRFI"):
                tier_stats[nrfi_tier]["total"] += 1
                # NRFI tiers in 70+ band hit if NRFI; <=40 yrfi lean hits if YRFI
                expected = "YRFI" if nrfi_tier == "yrfi_lean_le40" else "NRFI"
                if res == expected:
                    tier_stats[nrfi_tier]["hits"] += 1

        # Confluence tier (ML — direction inferred from spread_delta sign)
        conf_tier = classify_confluence_tier(r.get("signal_confluence_net"))
        sd = r.get("spread_delta")
        hw = r.get("home_win")
        if conf_tier and sd is not None and hw is not None:
            try:
                # Positive delta = bet HOME, negative = bet AWAY
                bet_home = float(sd) > 0
                hit = (bet_home and hw) or (not bet_home and not hw)
                tier_stats[conf_tier]["total"] += 1
                if hit:
                    tier_stats[conf_tier]["hits"] += 1
            except (TypeError, ValueError):
                pass

        # Spread delta tier (independent of confluence)
        sd_tier = classify_spread_delta_tier(sd)
        if sd_tier and sd is not None and hw is not None:
            try:
                bet_home = float(sd) > 0
                hit = (bet_home and hw) or (not bet_home and not hw)
                tier_stats[sd_tier]["total"] += 1
                if hit:
                    tier_stats[sd_tier]["hits"] += 1
            except (TypeError, ValueError):
                pass

        # Auto-fade cohort (matches auto_fade.cohort_for_pick logic so
        # auto_fade can read these rates back instead of hard-coding)
        af_cohort = classify_autofade_cohort(
            sd, r.get("close_spread"), r.get("signal_confluence_net")
        )
        if af_cohort and sd is not None and hw is not None:
            try:
                bet_home = float(sd) > 0
                hit = (bet_home and hw) or (not bet_home and not hw)
                tier_stats[af_cohort]["total"] += 1
                if hit:
                    tier_stats[af_cohort]["hits"] += 1
            except (TypeError, ValueError):
                pass

    return tier_stats


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Missing SUPABASE env vars.")
        sys.exit(1)

    print("Pulling resolved games...")
    rows = fetch_all_resolved()
    print(f"Total resolved games: {len(rows)}")
    if not rows:
        return

    et_today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()

    upsert_rows = []
    print(f"\n{'TIER':35s} {'7d':>14s} {'30d':>14s} {'STD':>14s}")
    print("-" * 80)

    # Compute for each window
    windows = [("7d", 7), ("30d", 30), ("std", 9999)]
    all_tiers = {
        "nrfi_volatile_95plus", "nrfi_prime_90_94", "nrfi_dead_80_89",
        "nrfi_lean_70_79", "nrfi_60_69", "nrfi_neutral_50_59", "yrfi_lean_le40",
        "confluence_prime_ge4", "confluence_strong_2_3", "confluence_lean_1",
        "confluence_zero", "confluence_negative",
        "spread_delta_ge2", "spread_delta_1_5_2", "spread_delta_1_1_5", "spread_delta_lt1",
        "autofade_chalk_high_mag", "autofade_chalk", "autofade_dog", "autofade_dog_high_conv",
    }

    window_data = {}
    for label, days in windows:
        window_data[label] = compute_window_rates(rows, days, et_today)

    for tier in sorted(all_tiers):
        cells = []
        for label, _ in windows:
            stats = window_data[label].get(tier, {"hits": 0, "total": 0})
            if stats["total"] == 0:
                cells.append("—".rjust(14))
            else:
                rate = stats["hits"] / stats["total"] * 100
                cells.append(f"{stats['hits']}-{stats['total']-stats['hits']} ({rate:.1f}%)".rjust(14))
        print(f"{tier:35s} {cells[0]} {cells[1]} {cells[2]}")

        # Build upsert rows for storage (one row per tier × window)
        for label, days in windows:
            stats = window_data[label].get(tier, {"hits": 0, "total": 0})
            total = stats["total"]
            if total == 0:
                continue
            upsert_rows.append({
                "tier": tier,
                "window_label": label,
                "computed_date": et_today.isoformat(),
                "hits": stats["hits"],
                "total": total,
                "hit_rate": round(stats["hits"] / total, 4),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })

    if upsert_rows:
        print(f"\nUpserting {len(upsert_rows)} tier-window rows to mlb_tier_calibration...")
        ok = sb_upsert("mlb_tier_calibration", upsert_rows, on_conflict="tier,window_label,computed_date")
        print("✅ Upsert complete" if ok else "❌ Upsert failed")
    else:
        print("\nNo tier rows to upsert.")


if __name__ == "__main__":
    main()
