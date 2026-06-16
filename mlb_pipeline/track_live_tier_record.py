"""Track live (tier × category) hit rates for the resolver framework.

WHY: The retroactive audit (n=129 historical) said STRONG side hits ~78%,
but the live forward-test (n=6 over 3 days) is sitting at 50%. Tier names
imply equal confidence across categories that the live data doesn't yet
justify. Without surfacing the live record, every pick recommendation is
implicitly weighting picks by the retroactive prior instead of the
observed posterior.

WHAT: After every nightly resolve_game_results cron, walk all graded
games in the last 30 days and:
  1. Read resolver_total tier from each game's sweat_breakdown (already
     persisted by play_of_day) and grade against actual total.
  2. Compute resolver_side tier inline (not persisted yet) using
     signal_resolver.resolve_side + cohort counts, grade against
     actual ML result.
  3. Aggregate mlb_pipeline_props.result by tier (PROP × tier).
  4. Compute lifetime / 30d / 7d records per (tier × category).
  5. Write to jerry_cache.live_tier_records so picks can surface the
     real hit rate alongside the tier name.

STORAGE: jerry_cache row `live_tier_records` (no schema migration).

USAGE:
  Hooked into resolve_game_results.run() — runs nightly after props
  grading completes. Manual: python track_live_tier_record.py [--dryrun]
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
WRITE_HEADERS = {
    **HEADERS,
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
}

CACHE_KEY = "live_tier_records"

# When the resolver was wired live. Records before this date are
# retroactive-fit and shouldn't pollute the live track-record.
RESOLVER_LIVE_DATE = "2026-06-11"


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _et_today():
    return _et_now().strftime("%Y-%m-%d")


def _fetch_all(path, params):
    """Paginated GET — handles >1000 rows via Range header."""
    rows = []
    offset = 0
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{path}",
            params=params,
            headers={**HEADERS, "Range-Unit": "items",
                     "Range": f"{offset}-{offset + 999}"},
            timeout=30,
        )
        if r.status_code not in (200, 206):
            break
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return rows


def _classify_tier(tier):
    """Normalize tier strings; treat None / SKIP as un-actionable."""
    if not tier or tier in ("SKIP", "?"):
        return None
    return tier


def _grade_total(direction, line, total):
    if direction not in ("OVER", "UNDER") or line is None:
        return None
    if total == 0:
        return "PPD"
    if total > line:
        actual = "OVER"
    elif total < line:
        actual = "UNDER"
    else:
        return "PUSH"
    return "W" if actual == direction else "L"


def _grade_side(direction, home_win):
    if direction not in ("HOME", "AWAY") or home_win is None:
        return None
    if direction == "HOME":
        return "W" if home_win else "L"
    return "W" if not home_win else "L"


def _grade_prop(result):
    if not result:
        return None
    r = result.lower()
    if r in ("win", "w"):
        return "W"
    if r in ("loss", "l"):
        return "L"
    if r in ("push", "p"):
        return "PUSH"
    if r in ("void", "v"):
        return None
    return None


def _compute_side_tier(ctx):
    """Inline side-resolver call. resolve_side isn't persisted on
    mlb_game_context yet, so we recompute it from stored model + cohort
    counts. Returns (tier, direction) or (None, None) on failure."""
    try:
        from signal_resolver import resolve_side
        from cohort_signals import evaluate_game_for_play

        def cnt(play, direction):
            m = evaluate_game_for_play(ctx, play, direction) or []
            return len([x for x in m
                        if x.get("tier") in ("LOCK", "STRONG_EDGE", "LEAN")
                        and not x.get("id", "").endswith("|any")])

        ml_h = sum(cnt(p, "home") for p in ("v3_ml", "v4_ml", "jerry_ml", "conf_ml"))
        ml_a = sum(cnt(p, "away") for p in ("v3_ml", "v4_ml", "jerry_ml", "conf_ml"))
        rl_h = sum(cnt(p, "home") for p in ("v3_rl", "v4_rl"))
        rl_a = sum(cnt(p, "away") for p in ("v3_rl", "v4_rl"))

        res = resolve_side(
            close_spread=(ctx.get("close_spread") or ctx.get("open_spread")),
            v3_spread=ctx.get("projected_spread"),
            v4_spread=ctx.get("model_pred_spread"),
            jerry_spread=ctx.get("jerry_pred_spread"),
            ml_home_cohort_count=ml_h, ml_away_cohort_count=ml_a,
            rl_home_cohort_count=rl_h, rl_away_cohort_count=rl_a,
            confluence_net=ctx.get("signal_confluence_net"),
            prop_reverse=None,
        )
        return res.get("tier"), res.get("direction")
    except Exception:
        return None, None


def _agg(records):
    """Reduce a list of W/L/PUSH grades to {n, w, l, p, pct} dict."""
    w = records.count("W")
    l = records.count("L")
    p = records.count("PUSH")
    n = w + l
    return {
        "n": n + p,
        "actionable": n,
        "w": w,
        "l": l,
        "p": p,
        "pct": round(100.0 * w / n, 1) if n else None,
    }


def _window_filter(items, days, today):
    """Keep only items whose date is within `days` of today (ET)."""
    if days is None:
        return items
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    return [i for i in items if i[0] >= cutoff]


def run(dryrun=False):
    today = _et_today()
    print(f"[track_live_tier_record] computing through {today}...")

    # ── Pull data ──
    # Game contexts since resolver went live
    contexts = _fetch_all("mlb_game_context", {
        "select": "*",
        "game_date": f"gte.{RESOLVER_LIVE_DATE}",
        "order": "game_date.asc",
    })
    print(f"  {len(contexts)} game contexts since {RESOLVER_LIVE_DATE}")

    # Game results since resolver went live
    results = _fetch_all("mlb_game_results", {
        "select": "game_date,away_team,home_team,away_score,home_score,home_win,close_total,open_total",
        "game_date": f"gte.{RESOLVER_LIVE_DATE}",
        "order": "game_date.asc",
    })
    results_map = {(r["game_date"], r["away_team"], r["home_team"]): r for r in results}
    print(f"  {len(results)} game results since {RESOLVER_LIVE_DATE}")

    # Props for tier×type — use a 30-day window independent of the
    # resolver-live date because prop_type baseline rates are useful
    # regardless of when the side/total resolvers were wired. We need
    # a meaningful sample (200+) to call a type "edge" vs "coinflip".
    prop_window_floor = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    props = _fetch_all("mlb_pipeline_props", {
        "select": "game_date,tier,prop_type,result",
        "game_date": f"gte.{prop_window_floor}",
        "result": "not.is.null",
    })
    print(f"  {len(props)} graded props since {prop_window_floor} (30d window)")

    # ── Walk each context, grade total + side ──
    total_records = defaultdict(list)  # tier → [(date, W/L/PUSH), ...]
    side_records = defaultdict(list)
    for ctx in contexts:
        key = (ctx["game_date"], ctx["away_team"], ctx["home_team"])
        res = results_map.get(key)
        if not res:
            continue
        line = ctx.get("close_total") or ctx.get("open_total")
        total = (res.get("away_score") or 0) + (res.get("home_score") or 0)

        # TOTAL: resolver_total already persisted in sweat_breakdown
        sb = ctx.get("sweat_breakdown")
        if isinstance(sb, str):
            try:
                sb = json.loads(sb)
            except json.JSONDecodeError:
                sb = None
        if isinstance(sb, dict):
            rt = (sb.get("dimensions") or {}).get("resolver_total") or {}
            tier = _classify_tier(rt.get("tier"))
            if tier:
                grade = _grade_total(rt.get("direction"), line, total)
                if grade and grade != "PPD":
                    total_records[tier].append((ctx["game_date"], grade))

        # SIDE: not persisted, recompute inline
        side_tier, side_dir = _compute_side_tier(ctx)
        side_tier = _classify_tier(side_tier)
        if side_tier:
            grade = _grade_side(side_dir, res.get("home_win"))
            if grade:
                side_records[side_tier].append((ctx["game_date"], grade))

    # ── Walk props ──
    prop_records = defaultdict(list)  # tier → [(date, W/L), ...]
    prop_type_x_tier = defaultdict(lambda: defaultdict(list))  # prop_type → tier → [(date, W/L), ...]
    prop_type_overall = defaultdict(list)  # prop_type → [(date, W/L), ...] all tiers combined
    for p in props:
        tier = (p.get("tier") or "").upper()
        if tier not in ("PRIME", "STRONG", "LEAN", "LIGHT_LEAN"):
            continue
        grade = _grade_prop(p.get("result"))
        if grade:
            prop_records[tier].append((p["game_date"], grade))
            ptype = (p.get("prop_type") or "").lower()
            if ptype:
                prop_type_x_tier[ptype][tier].append((p["game_date"], grade))
                prop_type_overall[ptype].append((p["game_date"], grade))

    # ── Build payload with lifetime / 30d / 7d windows ──
    payload = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "resolver_live_since": RESOLVER_LIVE_DATE,
        "lookback_today": today,
        "categories": {},
        # Prop sub-payload: type-level breakdown (overall + per-tier) so
        # the prop pipeline can filter coinflip combos (PRIME bb_under
        # at 53%) and chase real edges (outs_under at 96%). 30d window
        # was the right horizon for the 6/15 analysis that drove this.
        "prop_type_breakdown": {
            "overall": {},        # prop_type → {lifetime, 30d, 7d}
            "by_tier": {},        # prop_type → tier → {lifetime, 30d, 7d}
        },
    }
    for category, records in (("TOTAL", total_records),
                              ("SIDE", side_records),
                              ("PROP", prop_records)):
        cat_payload = {}
        for tier, items in records.items():
            cat_payload[tier] = {
                "lifetime": _agg([g for _, g in items]),
                "30d": _agg([g for _, g in _window_filter(items, 30, today)]),
                "7d": _agg([g for _, g in _window_filter(items, 7, today)]),
            }
        payload["categories"][category] = cat_payload

    # Prop type × tier breakdown
    for ptype, items in prop_type_overall.items():
        payload["prop_type_breakdown"]["overall"][ptype] = {
            "lifetime": _agg([g for _, g in items]),
            "30d": _agg([g for _, g in _window_filter(items, 30, today)]),
            "7d": _agg([g for _, g in _window_filter(items, 7, today)]),
        }
    for ptype, tier_dict in prop_type_x_tier.items():
        payload["prop_type_breakdown"]["by_tier"][ptype] = {}
        for tier, items in tier_dict.items():
            payload["prop_type_breakdown"]["by_tier"][ptype][tier] = {
                "lifetime": _agg([g for _, g in items]),
                "30d": _agg([g for _, g in _window_filter(items, 30, today)]),
                "7d": _agg([g for _, g in _window_filter(items, 7, today)]),
            }

    # ── Print summary ──
    print()
    print("LIVE (tier × category) RECORDS:")
    for category in ("TOTAL", "SIDE", "PROP"):
        cat = payload["categories"].get(category) or {}
        if not cat:
            continue
        print(f"  {category}:")
        for tier in ("ELITE", "STRONG", "LEAN", "LIGHT", "PRIME"):
            stat = cat.get(tier)
            if not stat:
                continue
            life = stat["lifetime"]
            d30 = stat["30d"]
            d7 = stat["7d"]
            print(f"    {tier:<8} lifetime {life['w']}W-{life['l']}L "
                  f"({life['pct']}%, n={life['actionable']}) | "
                  f"30d {d30['w']}-{d30['l']} ({d30['pct']}%) | "
                  f"7d {d7['w']}-{d7['l']} ({d7['pct']}%)")

    print()
    print("PROP TYPE × OVERALL HIT RATE (30d):")
    overall = payload["prop_type_breakdown"]["overall"]
    rows = []
    for ptype, windows in overall.items():
        d30 = windows["30d"]
        if (d30["actionable"] or 0) >= 10:
            rows.append((d30["pct"] or 0, d30["actionable"], ptype, d30["w"], d30["l"]))
    rows.sort(key=lambda r: -r[0])
    for pct, n, ptype, w, l in rows:
        marker = ("EDGE" if pct >= 60 else ("CALIBRATED" if pct >= 50 else "COINFLIP/FADE"))
        print(f"  {ptype:<14} {w}-{l} ({pct}%, n={n})  {marker}")

    if dryrun:
        print("[track_live_tier_record] DRYRUN — not writing.")
        return

    # ── Persist ──
    body = {
        "cache_key": CACHE_KEY,
        "game_id": CACHE_KEY,
        "sport": "mlb",
        "narrative": "",
        "data": json.dumps(payload),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=cache_key",
        headers=WRITE_HEADERS, json=body, timeout=15,
    )
    if r.status_code in (200, 201, 204):
        print(f"\n  ✅ wrote jerry_cache.{CACHE_KEY}")
    else:
        print(f"\n  ⚠ write FAILED {r.status_code}: {r.text[:200]}")


if __name__ == "__main__":
    run(dryrun="--dryrun" in sys.argv)
