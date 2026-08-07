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


# ── Bayesian blend of L7 vs season baseline (2026-08-07) ────────────
# Mirrors generate_props.py:_bayes_blend_l7_season. See that file's
# comment for the full derivation. Prior weight of 20 starts = "we
# trust the season baseline as much as 20 recent starts". Small enough
# that a real form change (10-12 new starts) shifts the blend, large
# enough that a 7-start hot streak doesn't stampede the projection.
# Applied here so game_context.{home,away}_pitcher_projected_* fields
# reflect the blended projection — same numbers the prop scorer uses,
# closes the Buehler-style discrepancy (game_context said 2.1 while
# prop scorer said 3.0 on the same pitcher, same night).
_SEASON_PRIOR_N_STARTS = 20


def _season_baseline_from_classes(entry, stat_key):
    """Weighted mean of stat_key across classes[*].n. Returns None if
    the entry has no classes data."""
    if not entry: return None
    classes = entry.get("classes") or {}
    if not classes: return None
    total_n, total_val = 0, 0.0
    for bucket in classes.values():
        if not isinstance(bucket, dict): continue
        n = bucket.get("n"); v = bucket.get(stat_key)
        try: n = int(n); v = float(v)
        except (TypeError, ValueError): continue
        if n <= 0: continue
        total_n += n
        total_val += v * n
    if total_n == 0: return None
    return total_val / total_n


def _bayes_blend(l7_val, l7_n, season_val, prior_n: int = _SEASON_PRIOR_N_STARTS):
    """(l7×l7_n + season×prior_n) / (l7_n + prior_n). Falls back cleanly
    to whichever side has a value if the other is None."""
    try:
        l7_val = float(l7_val) if l7_val is not None else None
        l7_n = int(l7_n) if l7_n is not None else 0
        season_val = float(season_val) if season_val is not None else None
    except (TypeError, ValueError):
        return None
    if l7_val is None and season_val is None: return None
    if l7_val is None: return season_val
    if season_val is None: return l7_val
    if l7_n <= 0: return season_val
    return (l7_val * l7_n + season_val * prior_n) / (l7_n + prior_n)


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
    """Fallback to Supabase pitcher_projections when JSON cache misses.
    Returns dict with l7_rolling AND classes (2026-08-07 — classes added
    so the blend has a season baseline even on cache-miss pitchers)."""
    try:
        q = urllib.parse.quote(name)
        # 2026-08-07 fix: table is keyed on `pitcher_name` not `player_name`.
        # Prior version silently returned None for every miss.
        r = urllib.request.urlopen(
            urllib.request.Request(
                f"{SB}/rest/v1/pitcher_projections?pitcher_name=eq.{q}&select=l7_rolling,classes&limit=1",
                headers=H_READ,
            ),
            timeout=10,
        )
        rows = json.loads(r.read())
        if rows:
            return rows[0]  # {'l7_rolling': ..., 'classes': ...}
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
                f"&select=k_pct,bb_pct,baa_allowed,last_5_era,xera,last_3_era&limit=1",
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
                "l5_era":  row.get("last_5_era"),
                "xera":    row.get("xera"),
                "l3_era":  row.get("last_3_era"),
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

    # 1. JSON cache (preferred). Entry shape: {l7_rolling, classes, name, ...}
    entry = json_cache.get((name or "").lower())
    if entry:
        out["source"] = "json_l7"
    else:
        # 2. Supabase pitcher_projections fallback — returns {l7_rolling, classes}
        entry = get_supabase_projection(name)
        if entry:
            out["source"] = "sb_l7"

    l7 = (entry or {}).get("l7_rolling") if entry else None

    if l7:
        # 2026-08-07 Bayesian blend: L7 vs season baseline from classes.
        # Blends every projection type at the game_context layer so panel
        # inputs (projected_er + projected_outs) and Jerry's game reads
        # see the same regressed numbers the prop scorer uses. Buehler-
        # style discrepancies (game_context 2.1 vs prop scorer 3.0)
        # disappear because both paths converge to the blended value.
        l7_n = l7.get("n_starts") or 7
        # (stat_key, l7_field, classes_stat_key, multiplier)
        blend_map = [
            ("ks",   "avg_k",    "avg_k",    1.0),
            ("bb",   "avg_bb",   "avg_bb",   1.0),
            ("hits", "avg_hits", "avg_hits", 1.0),
            ("er",   "avg_er",   "avg_er",   1.0),
            ("outs", "avg_ip",   "avg_outs", None),  # special: outs derives from IP
        ]
        for stat_key, l7_field, classes_stat, mult in blend_map:
            l7_val = l7.get(l7_field)
            if l7_val is None: continue
            l7_val = float(l7_val)
            season_val = _season_baseline_from_classes(entry, classes_stat)
            if stat_key == "outs":
                # classes has avg_outs directly; l7 has avg_ip. Convert
                # l7 to outs, then blend against classes.avg_outs.
                l7_outs = l7_val * 3.0
                blended = _bayes_blend(l7_outs, l7_n, season_val)
                out["outs"] = round(blended, 1) if blended is not None else round(l7_outs, 1)
            else:
                blended = _bayes_blend(l7_val, l7_n, season_val)
                out[stat_key] = round(blended, 1) if blended is not None else round(l7_val, 1)

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

        # OUTS + ER fallback (added 2026-07-25).
        # Previously "No clean fallback for outs/er without IP history —
        # leave None" which meant every pitcher on the season_fallback
        # path had projected_er = None, breaking panel computation for
        # ~60% of games (nothing to feed into the panel formula).
        # Fix: default outs = 15 (5 IP, league avg for SP), scale by
        # pitcher quality. ER = L5_ERA × IP / 9 (or xERA if L5 missing).
        # Credibility gate: skip if L5_ERA outside 1.5-9.0 (thin sample
        # or garbage).
        l5 = season.get("l5_era")
        xera = season.get("xera")
        era_source = l5 if (l5 is not None and 1.5 <= float(l5) <= 9.0) else xera
        if out["outs"] is None and era_source is not None:
            # Quality-adjusted outs: elite pitchers go deeper, bad ones get pulled.
            era_f = float(era_source)
            if era_f <= 3.00:   default_outs = 18.0  # 6 IP
            elif era_f <= 4.00: default_outs = 16.0  # 5.1 IP
            elif era_f <= 5.00: default_outs = 15.0  # 5 IP (league avg SP)
            else:               default_outs = 13.0  # 4.1 IP for struggling arms
            out["outs"] = default_outs
            if out["source"] == "no_data": out["source"] = "season_fallback"
        if out["er"] is None and era_source is not None and out["outs"] is not None:
            era_f = float(era_source)
            if 1.5 <= era_f <= 9.0:
                ip = float(out["outs"]) / 3.0
                out["er"] = round(era_f * ip / 9.0, 1)
                if out["source"] == "no_data": out["source"] = "season_fallback"

    return out


def fetch_today_games(date_str):
    # Pull existing projection columns so we only PATCH on actual changes —
    # keeps the log clean and reduces churn on row updated_at.
    cols = ["id", "away_pitcher", "home_pitcher"]
    for side in ("away", "home"):
        for stat in ("ks", "bb", "hits", "outs", "er"):
            cols.append(f"{side}_pitcher_projected_{stat}")
        cols.append(f"{side}_pitcher_whip")
        # Panel inputs (bullpen_era + panel columns already present in row)
        cols.append(f"{side}_bullpen_era")
    cols.extend(["panel_implied_margin", "panel_implied_total"])
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
                # Don't overwrite existing values with None (2026-07-25 fix)
                # — season_fallback path returns None for some stats which
                # was silently nulling valid values game_context.py had set.
                if new is None and old is not None:
                    continue
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
        # ─────────────────────────────────────────────────────────────────
        # Panel_implied compute — do this AFTER stat patching (2026-07-25).
        #
        # Panel depends on projected_er + projected_outs + bullpen_era.
        # game_context.py:2313 tries to compute panel BEFORE this script
        # runs, so context.get('*_pitcher_projected_er') returns None → the
        # if-check silently fails → panel_implied stays NULL forever.
        # Fix: compute here where ER is authoritative (post-patch) and add
        # to the same PATCH payload. Mirrors game_context.py formula.
        # Applied on 12 games' worth of 7/25 slate + all future slates.
        # ─────────────────────────────────────────────────────────────────
        def _merged(side, stat_col):
            key = f"{side}_pitcher_projected_{stat_col}"
            return payload.get(key, g.get(key))

        aer = _merged("away", "er")
        her = _merged("home", "er")
        if aer is not None and her is not None:
            try:
                asp_er_f = float(aer); hsp_er_f = float(her)
                asp_outs_f = float(_merged("away", "outs") or 15)
                hsp_outs_f = float(_merged("home", "outs") or 15)
                abp = float(g.get("away_bullpen_era") or 4.10)
                hbp = float(g.get("home_bullpen_era") or 4.10)
                away_bp_ip = max(0, 9 - asp_outs_f / 3)
                home_bp_ip = max(0, 9 - hsp_outs_f / 3)
                home_scores = asp_er_f + abp * away_bp_ip / 9
                away_scores = hsp_er_f + hbp * home_bp_ip / 9
                new_pt = round(home_scores + away_scores, 2)
                new_pm = round(home_scores - away_scores, 2)
                if g.get("panel_implied_total") != new_pt or g.get("panel_implied_margin") != new_pm:
                    payload["panel_implied_total"] = new_pt
                    payload["panel_implied_margin"] = new_pm
                    print(f"    panel: margin={new_pm:+.2f} total={new_pt}")
            except (TypeError, ValueError):
                pass
        if payload:
            if patch_row(g["id"], payload):
                updated += 1
    print(f"  patched {updated} rows")


if __name__ == "__main__":
    main()
