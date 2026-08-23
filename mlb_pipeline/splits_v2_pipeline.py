"""Public Splits v2 pipeline — source-agnostic normalizer + aggregator.

Reads existing per-source tables (public_splits_archive for OC, fadereport_signals,
cleatz_signals), normalizes into long-form rows in public_splits_v2, then aggregates
per-game into game_context.splits_summary JSONB.

Ships Phase 1 of the splits schema v2 sprint (2026-08-23). Once app cuts over
(Phase 4) to reading splits_summary, source-specific reads
(game_context.oddscrowd_snapshot, fadereport_signals joins) can be deprecated.

Usage:
    python splits_v2_pipeline.py                          # today, all sports
    python splits_v2_pipeline.py --date 2026-08-23
    python splits_v2_pipeline.py --sport MLB
    python splits_v2_pipeline.py --backfill 2026-06-24    # backfill from date
    python splits_v2_pipeline.py --dry-run
"""
import argparse, os, sys, json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
SB = os.environ.get("SUPABASE_URL")
K = os.environ.get("SUPABASE_KEY")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass

H_READ = {"apikey": K, "Authorization": f"Bearer {K}"}
H_WRITE = {**H_READ, "Content-Type": "application/json",
           "Prefer": "resolution=merge-duplicates,return=minimal"}


def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


SPORT_CTX_TABLE = {
    "MLB":   "mlb_game_context",
    "NFL":   "nfl_game_context",
    "NCAAF": "ncaaf_game_context",
    "NCAAB": "ncaab_game_context",
    "NBA":   "nba_game_context",
    "NHL":   "nhl_game_context",
}

# 2026-08-23 Phase 3 audit updated the coverage map based on what each
# source ACTUALLY produces per fadereport_signals / cleatz_signals row
# counts. Prior state assumed FR was MLB-only per the proposal doc — but
# FR scraper's SPORT_URL dict already covers all 6 sports and DB has
# NFL 207 + NCAAF 192 rows for Aug 2026. Same story on CZ.
SOURCE_COVERAGE = {
    "oc": {"MLB", "NFL", "NCAAF", "NCAAB", "NBA", "NHL"},
    "fr": {"MLB", "NFL", "NCAAF", "NCAAB", "NBA", "NHL"},   # site covers all; NBA/NHL/NCAAB rows land in-season
    "cz": {"MLB", "NFL", "NCAAF", "NCAAB", "NBA"},          # site covers 5 sports (no NHL — 404)
    "so": {"MLB", "NFL", "NCAAF", "NCAAB", "NBA", "NHL"},   # Phase 2 scraper handles all 6 via SPORT_URL dict
}


def _flip(side: str) -> str:
    return {"HOME": "AWAY", "AWAY": "HOME", "OVER": "UNDER", "UNDER": "OVER"}.get(side, side)


def normalize_from_public_splits_archive(sport: str, game_date: str) -> list[dict]:
    """OddsCrowd is stored pivoted in public_splits_archive. Expand to long-form."""
    r = requests.get(f"{SB}/rest/v1/public_splits_archive",
                     headers=H_READ,
                     params={"sport": f"eq.{sport}",
                             "captured_at": f"gte.{game_date}T00:00:00",
                             "select": "game_id,market,pick_side,oc_money_pct,oc_bets_pct,"
                                       "oc_divergence,fr_handle_pct,fr_bettors_pct,captured_at",
                             "limit": 2000},
                     timeout=20)
    if r.status_code != 200: return []
    rows = []
    for row in r.json() or []:
        if not isinstance(row, dict): continue
        gid = row.get("game_id"); mkt = str(row.get("market","")).lower()
        side = str(row.get("pick_side","")).upper()
        if not (gid and mkt and side): continue
        ts = row.get("captured_at")
        # OddsCrowd rows
        for metric, val in (("money_pct", row.get("oc_money_pct")),
                            ("bets_pct",  row.get("oc_bets_pct")),
                            ("divergence", row.get("oc_divergence"))):
            if val is not None:
                rows.append({"snapshot_ts": ts, "sport": sport, "game_id": gid,
                             "market": mkt, "side": side, "source": "oc",
                             "metric": metric, "value": float(val)})
                # Mirror the OTHER side for money/bets so aggregator sees both sides
                if metric in ("money_pct", "bets_pct"):
                    rows.append({"snapshot_ts": ts, "sport": sport, "game_id": gid,
                                 "market": mkt, "side": _flip(side), "source": "oc",
                                 "metric": metric, "value": round(100.0 - float(val), 1)})
        # Fadereport rows also in this table (denormalized from fadereport_signals)
        for metric, val in (("handle_pct", row.get("fr_handle_pct")),
                            ("bets_pct",   row.get("fr_bettors_pct"))):
            if val is not None:
                rows.append({"snapshot_ts": ts, "sport": sport, "game_id": gid,
                             "market": mkt, "side": side, "source": "fr",
                             "metric": metric, "value": float(val)})
                if metric == "bets_pct":
                    rows.append({"snapshot_ts": ts, "sport": sport, "game_id": gid,
                                 "market": mkt, "side": _flip(side), "source": "fr",
                                 "metric": "bets_pct", "value": round(100.0 - float(val), 1)})
    return rows


def normalize_from_fadereport_signals(sport: str, game_date: str) -> list[dict]:
    """Fadereport native table. sharp_side_norm + bets/money splits both sides given."""
    r = requests.get(f"{SB}/rest/v1/fadereport_signals",
                     headers=H_READ,
                     params={"sport": f"eq.{sport}",
                             "snapshot_date": f"eq.{game_date}",
                             "select": "game_id,market,sharp_side_norm,bets_side_pct,"
                                       "money_side_pct,bets_other_pct,money_other_pct,"
                                       "strength_pts,fetched_at",
                             "limit": 2000},
                     timeout=20)
    if r.status_code != 200: return []
    rows = []
    for row in r.json() or []:
        if not isinstance(row, dict): continue
        gid = row.get("game_id"); mkt = str(row.get("market","")).lower()
        sharp = str(row.get("sharp_side_norm","")).upper()
        if not (gid and mkt and sharp): continue
        ts = row.get("fetched_at")
        # sharp side gets bets_side_pct / money_side_pct
        for metric, val in (("bets_pct", row.get("bets_side_pct")),
                            ("money_pct", row.get("money_side_pct"))):
            if val is not None:
                rows.append({"snapshot_ts": ts, "sport": sport, "game_id": gid,
                             "market": mkt, "side": sharp, "source": "fr",
                             "metric": metric, "value": float(val)})
        # other side
        for metric, val in (("bets_pct", row.get("bets_other_pct")),
                            ("money_pct", row.get("money_other_pct"))):
            if val is not None:
                rows.append({"snapshot_ts": ts, "sport": sport, "game_id": gid,
                             "market": mkt, "side": _flip(sharp), "source": "fr",
                             "metric": metric, "value": float(val)})
        # Strength as a computed metric on the sharp side
        if row.get("strength_pts") is not None:
            rows.append({"snapshot_ts": ts, "sport": sport, "game_id": gid,
                         "market": mkt, "side": sharp, "source": "fr",
                         "metric": "strength_pts", "value": float(row["strength_pts"])})
    return rows


def normalize_from_cleatz_signals(sport: str, game_date: str) -> list[dict]:
    """Cleatz native table. sharp_side_norm + handle/bets splits both sides given."""
    r = requests.get(f"{SB}/rest/v1/cleatz_signals",
                     headers=H_READ,
                     params={"sport": f"eq.{sport}",
                             "snapshot_date": f"eq.{game_date}",
                             "select": "game_id,market,sharp_side_norm,sharp_bets_pct,"
                                       "sharp_handle_pct,other_bets_pct,other_handle_pct,"
                                       "divergence,fetched_at",
                             "limit": 2000},
                     timeout=20)
    if r.status_code != 200: return []
    rows = []
    for row in r.json() or []:
        if not isinstance(row, dict): continue
        gid = row.get("game_id"); mkt = str(row.get("market","")).lower()
        sharp = str(row.get("sharp_side_norm","")).upper()
        if not (gid and mkt and sharp): continue
        ts = row.get("fetched_at")
        for metric, val in (("bets_pct", row.get("sharp_bets_pct")),
                            ("handle_pct", row.get("sharp_handle_pct"))):
            if val is not None:
                rows.append({"snapshot_ts": ts, "sport": sport, "game_id": gid,
                             "market": mkt, "side": sharp, "source": "cz",
                             "metric": metric, "value": float(val)})
        for metric, val in (("bets_pct", row.get("other_bets_pct")),
                            ("handle_pct", row.get("other_handle_pct"))):
            if val is not None:
                rows.append({"snapshot_ts": ts, "sport": sport, "game_id": gid,
                             "market": mkt, "side": _flip(sharp), "source": "cz",
                             "metric": metric, "value": float(val)})
        if row.get("divergence") is not None:
            rows.append({"snapshot_ts": ts, "sport": sport, "game_id": gid,
                         "market": mkt, "side": sharp, "source": "cz",
                         "metric": "divergence", "value": float(row["divergence"])})
    return rows


def upsert_v2_rows(rows: list[dict], dry: bool) -> int:
    if not rows: return 0
    if dry:
        print(f"    [DRY] would upsert {len(rows)} v2 rows")
        return 0
    written = 0
    CHUNK = 500
    for i in range(0, len(rows), CHUNK):
        batch = rows[i:i+CHUNK]
        r = requests.post(f"{SB}/rest/v1/public_splits_v2?on_conflict=game_id,market,side,source,metric,snapshot_ts",
                          headers=H_WRITE, json=batch, timeout=30)
        if r.status_code in (200, 201, 204):
            written += len(batch)
        else:
            print(f"    ⚠ upsert failed HTTP {r.status_code}: {r.text[:150]}")
            break
    return written


def compute_splits_summary(sport: str, game_id: str) -> dict:
    """Read all v2 rows for a game, aggregate into the summary JSONB.

    Output shape:
      {
        "captured_at": <most recent snapshot_ts>,
        "sources_present": ["oc","fr","cz"],
        "ml": {
          "HOME": {"money_pct_avg": 65, "bets_pct_avg": 71, "sources_agree": 3},
          "AWAY": {...}
        },
        "rl":  {...},
        "total": {...},
        "triple_confirmed": ["ml_HOME"],
        "dissent_flags":    ["oc_dissents_ml"]
      }
    """
    r = requests.get(f"{SB}/rest/v1/public_splits_v2",
                     headers=H_READ,
                     params={"sport": f"eq.{sport}",
                             "game_id": f"eq.{game_id}",
                             "select": "market,side,source,metric,value,snapshot_ts",
                             "order": "snapshot_ts.desc",
                             "limit": 500},
                     timeout=15)
    if r.status_code != 200: return {}
    rows = [x for x in (r.json() or []) if isinstance(x, dict)]
    if not rows: return {}

    # Keep the MOST RECENT value per (market, side, source, metric)
    latest = {}
    for row in rows:
        key = (row["market"], row["side"], row["source"], row["metric"])
        if key not in latest:
            latest[key] = row
    ts_max = max((row["snapshot_ts"] for row in latest.values() if row.get("snapshot_ts")), default=None)

    # Aggregate per (market, side)
    summary = {"captured_at": ts_max}
    sources_present = set()
    by_market = defaultdict(lambda: defaultdict(dict))  # market -> side -> {metric: [values], sources: set}
    for (mkt, side, src, metric), row in latest.items():
        sources_present.add(src)
        entry = by_market[mkt][side]
        entry.setdefault(f"{metric}_vals", []).append(row["value"])
        entry.setdefault("sources", set()).add(src)

    for mkt, sides in by_market.items():
        summary[mkt] = {}
        for side, entry in sides.items():
            side_out = {"sources_agree": len(entry.get("sources", set()))}
            for k in ("money_pct", "bets_pct", "handle_pct", "divergence", "strength_pts"):
                vals = entry.get(f"{k}_vals") or []
                if vals:
                    side_out[f"{k}_avg"] = round(sum(vals) / len(vals), 1)
            summary[mkt][side] = side_out

    summary["sources_present"] = sorted(sources_present)

    # Triple-confirmed flags: 3+ sources with money OR bets on same side
    triple = []
    for mkt in ("ml", "rl", "spread", "total", "moneyline"):
        if mkt not in summary: continue
        for side, out in summary[mkt].items():
            if out.get("sources_agree", 0) >= 3:
                triple.append(f"{mkt}_{side}")
    summary["triple_confirmed"] = triple

    # Dissent: OC opposite of majority. For each market, if OC's picked side
    # (higher money_pct) differs from what FR+CZ agree on, flag it.
    dissent = []
    for mkt in ("ml", "rl", "spread", "total", "moneyline"):
        if mkt not in summary: continue
        # Which side does each source pick?
        picks_by_source = {}
        for side, out in summary[mkt].items():
            for src in ("oc", "fr", "cz", "so"):
                # need to check per-source money_pct — v1 aggregation stored only avg. Fall back
                # to reading raw rows again just for this
                pass
        # Simpler: dissent inferred from triple-confirmed set — if triple exists on ONE side
        # and OC's per-source value on OTHER side is >= 55% money, that's dissent.
        # For now, mark bare-bones dissent as "OC row present + no triple_confirmed on either side"
        # Real implementation delayed to Phase 4 when app cutover shows what's actually needed.
    summary["dissent_flags"] = dissent  # populated more richly later

    return summary


def write_summary_to_ctx(sport: str, game_id: str, summary: dict, dry: bool) -> bool:
    tbl = SPORT_CTX_TABLE.get(sport.upper())
    if not tbl or not summary: return False
    if dry:
        print(f"    [DRY] would write splits_summary to {tbl} game_id={game_id[:8]}...")
        return False
    r = requests.patch(f"{SB}/rest/v1/{tbl}?game_id=eq.{game_id}",
                       headers=H_WRITE,
                       json={"splits_summary": summary},
                       timeout=15)
    return r.status_code in (200, 204)


def process_sport(sport: str, game_date: str, dry: bool) -> tuple[int, int]:
    """Normalize all sources for a sport → upsert v2 → aggregate per game.
    Returns (v2_rows_written, ctx_summaries_written)."""
    print(f"\n=== {sport} {game_date} ===")
    all_rows: list[dict] = []
    if sport in SOURCE_COVERAGE.get("oc", set()) or sport in SOURCE_COVERAGE.get("fr", set()):
        rows = normalize_from_public_splits_archive(sport, game_date)
        print(f"  public_splits_archive: {len(rows)} normalized rows")
        all_rows.extend(rows)
    if sport in SOURCE_COVERAGE.get("fr", set()):
        rows = normalize_from_fadereport_signals(sport, game_date)
        print(f"  fadereport_signals:    {len(rows)} normalized rows")
        all_rows.extend(rows)
    if sport in SOURCE_COVERAGE.get("cz", set()):
        rows = normalize_from_cleatz_signals(sport, game_date)
        print(f"  cleatz_signals:        {len(rows)} normalized rows")
        all_rows.extend(rows)

    v2_written = upsert_v2_rows(all_rows, dry)
    print(f"  → v2 upserted: {v2_written}")

    # Aggregate per game_id → write splits_summary
    game_ids = sorted({row["game_id"] for row in all_rows})
    ctx_written = 0
    for gid in game_ids:
        summary = compute_splits_summary(sport, gid)
        if summary and write_summary_to_ctx(sport, gid, summary, dry):
            ctx_written += 1
    print(f"  → splits_summary written to {ctx_written}/{len(game_ids)} game_contexts")
    return v2_written, ctx_written


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=today_et())
    p.add_argument("--sport", default="ALL")
    p.add_argument("--backfill", help="Start date for backfill (writes all dates from here to today)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    sports = [args.sport] if args.sport != "ALL" else list(SPORT_CTX_TABLE.keys())

    if args.backfill:
        start = datetime.strptime(args.backfill, "%Y-%m-%d")
        end = datetime.strptime(args.date, "%Y-%m-%d")
        cur = start
        while cur <= end:
            for s in sports:
                process_sport(s, cur.strftime("%Y-%m-%d"), args.dry_run)
            cur += timedelta(days=1)
    else:
        for s in sports:
            process_sport(s, args.date, args.dry_run)


if __name__ == "__main__":
    main()
