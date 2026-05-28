"""Patch today's mlb_game_context with projected stats for each starter.

Originally projected_ks only (5/27 DeGrom incident). Extended same day to
cover EVERY pitcher prop (BB / Hits / Outs / ER) plus WHIP for narrative,
since the same data-quality gap could open on any of them — "Over the
projected ER" or "Under the projected hits" without a specific number is
just as unauditable as "Over the projected Ks" was.

Runs AFTER compute_pitcher_class_projections.py and BEFORE generate_props.py
+ sweat card + Jerry reads. Reads the SAME source the prop scorers use so
published prop projections and social-copy projections are guaranteed
identical.

Source chain (mirrors generate_props.py:get_pitcher_projection):
  1. data/pitcher_class_projections.json — l7_rolling.{avg_k, avg_bb,
     avg_hits, avg_er, avg_ip, whip} (preferred)
  2. pitcher_projections Supabase table — same data, more recent rebuild
  3. Per-stat fallbacks using mlb_pitcher_stats season rates

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


def get_pitcher_season_stats(name):
    """Pull season rates for fallback projections."""
    try:
        q = urllib.parse.quote(name)
        r = urllib.request.urlopen(
            urllib.request.Request(
                f"{SB}/rest/v1/mlb_pitcher_stats?player_name=eq.{q}&season=eq.2026"
                f"&select=k_pct,bb_pct,baa_allowed&limit=1",
                headers=H_READ,
            ),
            timeout=10,
        )
        rows = json.loads(r.read())
        if rows:
            row = rows[0]
            # Normalize percent encoding (some columns store 0.298, some 29.8)
            def _pct(v):
                if v is None:
                    return None
                f = float(v)
                return f * 100 if f < 1.0 else f
            return {
                "k_pct":   _pct(row.get("k_pct")),
                "bb_pct":  _pct(row.get("bb_pct")),
                "baa":     row.get("baa_allowed"),
            }
    except Exception:
        pass
    return {}


# Maps stat key to the l7_rolling field name + the fallback formula
# (per-batter rate × 22 BF assumed). Outs = avg_ip × 3.
def project_all_stats(name, json_cache):
    """Returns dict with projected_ks / bb / hits / outs / er + whip + source tag.
    None values for any stat the source chain can't supply."""
    out = {"ks": None, "bb": None, "hits": None, "outs": None, "er": None, "whip": None, "source": "no_data"}

    # 1. JSON cache (preferred)
    entry = json_cache.get((name or "").lower())
    l7 = (entry or {}).get("l7_rolling") if entry else None
    if not l7:
        # 2. Supabase pitcher_projections fallback
        l7 = get_supabase_projection(name)
        if l7:
            out["source"] = "sb_l7"
    else:
        out["source"] = "json_l7"

    if l7:
        if l7.get("avg_k") is not None:
            out["ks"] = round(float(l7["avg_k"]), 1)
        if l7.get("avg_bb") is not None:
            out["bb"] = round(float(l7["avg_bb"]), 1)
        if l7.get("avg_hits") is not None:
            out["hits"] = round(float(l7["avg_hits"]), 1)
        if l7.get("avg_er") is not None:
            out["er"] = round(float(l7["avg_er"]), 1)
        if l7.get("avg_ip") is not None:
            out["outs"] = round(float(l7["avg_ip"]) * 3.0, 1)
        if l7.get("whip") is not None:
            out["whip"] = round(float(l7["whip"]), 2)

    # 3. Season-rate fallback for any stat the l7 didn't supply.
    # Assume 22 BF (~5.5 IP × 4 BF/IP) for the fill estimates.
    #
    # CREDIBILITY GATE (added 2026-05-27 Cole incident): season fallback only
    # trustworthy if rates look like a real MLB starter. Gerrit Cole came back
    # from TJ rehab with 2 starts (6 IP/2K then 6.2/10K) — pitcher_stats stored
    # k_pct=0.091 from that first start. Patcher projected 2.0 Ks tonight; he
    # struck out 10. Same thin-sample class as the Matz vs-team incident.
    # Floors: k_pct 12-40%, bb_pct 0-14%, BAA 0.150-0.350. Anything outside
    # = thin sample, return None instead of garbage so Jerry shows nothing.
    needs_fallback = any(out[k] is None for k in ("ks", "bb", "hits", "outs", "er"))
    if needs_fallback:
        season = get_pitcher_season_stats(name)
        k = season.get("k_pct")
        b = season.get("bb_pct")
        baa = season.get("baa")
        k_ok   = k   is not None and 12.0 <= k   <= 40.0
        bb_ok  = b   is not None and  0.0 <= b   <= 14.0
        baa_ok = baa is not None and 0.150 <= float(baa) <= 0.350
        if out["ks"] is None and k_ok:
            out["ks"] = round(k / 100 * 22, 1)
            if out["source"] == "no_data": out["source"] = "season_fallback"
        if out["bb"] is None and bb_ok:
            out["bb"] = round(b / 100 * 22, 1)
            if out["source"] == "no_data": out["source"] = "season_fallback"
        if out["hits"] is None and baa_ok:
            out["hits"] = round(float(baa) * 20, 1)
            if out["source"] == "no_data": out["source"] = "season_fallback"
        # Flag the skipped cases so the cron log is readable
        if k is not None and not k_ok:
            out["source"] = "thin_sample_skipped"
        # No clean fallback for outs/er without IP history — leave None

    return out


def fetch_today_games(date_str):
    # Pull existing projection columns so we only PATCH on actual changes —
    # keeps the log clean and reduces churn on row updated_at.
    cols = ["id", "away_pitcher", "home_pitcher"]
    for side in ("away", "home"):
        for stat in ("ks", "bb", "hits", "outs", "er"):
            cols.append(f"{side}_pitcher_projected_{stat}")
        cols.append(f"{side}_pitcher_whip")
    url = (
        f"{SB}/rest/v1/mlb_game_context?game_date=eq.{date_str}"
        f"&select={','.join(cols)}"
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
            if not name:
                continue
            proj = project_all_stats(name, json_cache)
            changed_bits = []
            # Stats with dedicated columns
            for stat_key, col_suffix in (
                ("ks", "projected_ks"), ("bb", "projected_bb"),
                ("hits", "projected_hits"), ("outs", "projected_outs"),
                ("er", "projected_er"),
            ):
                col = f"{side}_pitcher_{col_suffix}"
                new = proj[stat_key]
                old = g.get(col)
                if old != new:
                    payload[col] = new
                    changed_bits.append(f"{col_suffix}={new}")
            # WHIP (its own column, not "projected_" prefixed since it's a stat we track not a prop)
            whip_col = f"{side}_pitcher_whip"
            if g.get(whip_col) != proj["whip"]:
                payload[whip_col] = proj["whip"]
                if proj["whip"] is not None:
                    changed_bits.append(f"whip={proj['whip']}")
            if changed_bits:
                print(f"  {name} [{proj['source']}]: " + ", ".join(changed_bits))
        if payload:
            if patch_row(g["id"], payload):
                updated += 1
    print(f"  patched {updated} rows")


if __name__ == "__main__":
    main()
