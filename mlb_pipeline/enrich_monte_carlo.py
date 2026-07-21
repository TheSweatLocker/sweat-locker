"""Enrich mlb_game_context with Monte Carlo probabilities.

2026-07-21: turns point-estimate projections into actual probabilities users
can act on. Instead of "gap +1.2 UNDER", the app shows "P(UNDER 8.5) = 61%".

Runs after game_context.py in the cron. For each row in today's slate:
  1. Pull projected home runs, projected away runs
  2. If both exist: run Monte Carlo (Poisson approximation, n=10000)
  3. Write back:
       mc_p_over          — P(actual total > close_total)
       mc_p_under         — P(actual total < close_total)
       mc_p_home_win      — P(home team wins)
       mc_p_home_covers   — P(home covers close_spread)
       mc_mean_total      — MC-derived mean total (should be close to
                            projected_home + projected_away)
       mc_computed_at     — timestamp
  4. Add to game_read struct in jerry_cache so the app can display

CLI:
    python enrich_monte_carlo.py             # today's slate
    python enrich_monte_carlo.py 2026-07-21  # specific date
"""
import os
import sys
import json
import argparse
from datetime import datetime, timedelta, timezone
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SB = os.environ["SUPABASE_URL"]
KEY = os.environ["SUPABASE_KEY"]
H_READ = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
H_WRITE = {**H_READ, "Content-Type": "application/json", "Prefer": "return=minimal"}

from monte_carlo_win_prob import simulate_total, simulate_side, simulate_spread

SIM_N = 10000


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def fetch_slate(target_date: str) -> list:
    """Pull today's game_context rows with the fields we need."""
    r = requests.get(
        f"{SB}/rest/v1/mlb_game_context"
        f"?game_date=eq.{target_date}"
        f"&select=game_id,home_team,away_team,close_total,close_spread,"
        f"projected_total,model_pred_home_runs,model_pred_away_runs,"
        f"jerry_pred_home_runs,jerry_pred_away_runs,model_pred_total,jerry_pred_total",
        headers=H_READ,
        timeout=20,
    )
    return r.json() if r.status_code == 200 else []


def _split_projected_teams(row: dict) -> tuple:
    """Return (proj_home_runs, proj_away_runs).

    Uses model_pred_{home,away}_runs if both present (v4 XGBoost split),
    else jerry_pred_{home,away}_runs, else splits projected_total 50/50.
    """
    mh = _f(row.get("model_pred_home_runs"))
    ma = _f(row.get("model_pred_away_runs"))
    if mh is not None and ma is not None:
        return mh, ma
    jh = _f(row.get("jerry_pred_home_runs"))
    ja = _f(row.get("jerry_pred_away_runs"))
    if jh is not None and ja is not None:
        return jh, ja
    # Fall back: split projected_total 50/50
    pt = _f(row.get("projected_total"))
    if pt is not None:
        return pt / 2.0, pt / 2.0
    return None, None


def compute_mc_probabilities(row: dict) -> dict:
    """Run Monte Carlo sims + return probability bundle."""
    proj_h, proj_a = _split_projected_teams(row)
    if proj_h is None or proj_a is None:
        return {}
    close_total = _f(row.get("close_total"))
    close_spread = _f(row.get("close_spread"))
    out = {"mc_computed_at": datetime.now(timezone.utc).isoformat()}

    # Total probabilities
    if close_total is not None:
        tot_result = simulate_total(proj_h, proj_a, close_total, n=SIM_N, seed=42)
        out["mc_p_over"] = tot_result["p_over"]
        out["mc_p_under"] = tot_result["p_under"]
        out["mc_p_push"] = tot_result["p_push"]
        out["mc_mean_total"] = tot_result["mean_total"]
        out["mc_std_total"] = tot_result["std_total"]

    # Side probability (always compute — line-independent)
    side_result = simulate_side(proj_h, proj_a, n=SIM_N, seed=42)
    out["mc_p_home_win"] = side_result["p_home_win"]
    out["mc_p_away_win"] = side_result["p_away_win"]

    # Spread cover probability
    if close_spread is not None:
        sp_result = simulate_spread(proj_h, proj_a, close_spread, n=SIM_N, seed=42)
        out["mc_p_home_covers"] = sp_result["p_home_covers"]
        out["mc_p_away_covers"] = sp_result["p_away_covers"]

    return out


def write_mc_to_context(game_id: str, mc_data: dict) -> bool:
    """Patch mlb_game_context with the MC probability columns.

    Note: writes to a JSON blob column `mc_probabilities` since we don't want
    to require a schema migration for each MC field. This makes the enrichment
    ship-safe without touching mlb_game_context DDL.
    """
    if not mc_data:
        return False
    payload = {"mc_probabilities": mc_data}
    r = requests.patch(
        f"{SB}/rest/v1/mlb_game_context?game_id=eq.{game_id}",
        headers=H_WRITE, json=payload, timeout=15,
    )
    return r.status_code in (200, 204)


def update_jerry_cache_read(game_id: str, game_date: str, mc_data: dict) -> bool:
    """Patch the jerry_cache game_read row's data.market to include mc probs
    so the app can render them alongside line + composite.

    Fetch → merge → write pattern (jerry_cache stores JSON as string sometimes).
    """
    cache_key = f"game_read_{game_id}_{game_date}"
    r = requests.get(
        f"{SB}/rest/v1/jerry_cache?cache_key=eq.{cache_key}&select=data",
        headers=H_READ, timeout=10,
    )
    rows = r.json() if r.status_code == 200 else []
    if not rows:
        return False
    data = rows[0].get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return False
    if not isinstance(data, dict):
        return False
    market = data.get("market") or {}
    market["mc_probabilities"] = mc_data
    data["market"] = market
    payload = {"data": json.dumps(data) if isinstance(rows[0].get("data"), str) else data}
    r = requests.patch(
        f"{SB}/rest/v1/jerry_cache?cache_key=eq.{cache_key}",
        headers=H_WRITE, json=payload, timeout=15,
    )
    return r.status_code in (200, 204)


def run(target_date: str = None, dry_run: bool = False) -> None:
    target_date = target_date or _today_et()
    print(f"=== Monte Carlo enrichment {target_date} ===")
    slate = fetch_slate(target_date)
    print(f"  Games to enrich: {len(slate)}")

    for row in slate:
        gid = row["game_id"]
        matchup = f"{row['away_team']} @ {row['home_team']}"
        mc = compute_mc_probabilities(row)
        if not mc:
            print(f"  ⚠ SKIP {matchup} — no projections")
            continue

        line = row.get("close_total")
        p_over = mc.get("mc_p_over")
        p_home = mc.get("mc_p_home_win")
        p_cover = mc.get("mc_p_home_covers")
        po_s = f"{p_over:.3f}" if p_over is not None else "?"
        ph_s = f"{p_home:.3f}" if p_home is not None else "?"
        pc_s = f"{p_cover:.3f}" if p_cover is not None else "?"
        print(f"  {matchup[:38]:38} · line {line} · P(O)={po_s} · P(H)={ph_s} · P(cover)={pc_s}")

        if dry_run:
            continue

        ctx_ok = write_mc_to_context(gid, mc)
        cache_ok = update_jerry_cache_read(gid, target_date, mc)
        status = "✅" if (ctx_ok and cache_ok) else "⚠"
        print(f"    {status} write: ctx={ctx_ok}, cache={cache_ok}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default=None,
                    help="YYYY-MM-DD (defaults to today ET)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(target_date=args.date, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
