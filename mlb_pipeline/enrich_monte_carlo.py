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

# 2026-07-23: migrated from monte_carlo_win_prob (thin Poisson-on-projections)
# to monte_carlo.simulate_game (rich per-inning simulator with SP/BP form,
# offense drift, hand splits, park, weather, pitcher-vs-team mastery, umpire,
# and defense multipliers). MC is now an INDEPENDENT lens, not a v4-echo.
from monte_carlo import simulate_game

SIM_N = 10000


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def fetch_slate(target_date: str) -> list:
    """Pull today's game_context rows with ALL fields (simulate_game consumes
    ~30 fields via _extract_sides; safer to select * than enumerate).
    """
    r = requests.get(
        f"{SB}/rest/v1/mlb_game_context"
        f"?game_date=eq.{target_date}&select=*",
        headers=H_READ,
        timeout=20,
    )
    return r.json() if r.status_code == 200 else []


def compute_mc_probabilities(row: dict) -> dict:
    """Run rich Monte Carlo (per-inning simulator) + return probability bundle.

    2026-07-23: switched from thin `simulate_total/side/spread` (which just
    Poisson-sampled v4's projected runs) to `simulate_game` which builds runs
    from scratch using SP/BP quality, form, offense drift, hand splits, park,
    weather, pitcher-vs-team mastery, umpire, and team defense.
    MC is now an INDEPENDENT lens, not a v4-echo.

    Bonus outputs added: mc_p_nrfi + mc_p_yrfi (built into simulate_game).
    """
    close_total = _f(row.get("close_total"))
    close_spread = _f(row.get("close_spread"))
    # simulate_game gracefully handles missing line but needs at least
    # season_rpg + xERA to produce meaningful probabilities.
    sim = simulate_game(row, n_iter=SIM_N, line=close_total, seed=42)
    if sim is None or not isinstance(sim, dict):
        return {}
    out = {
        "mc_computed_at": datetime.now(timezone.utc).isoformat(),
        "mc_p_home_win":  sim.get("p_home_win"),
        "mc_p_away_win":  sim.get("p_away_win"),
        # simulate_game returns mu_total / sigma_total (not mean_/std_)
        "mc_mean_total":  sim.get("mu_total"),
        "mc_std_total":   sim.get("sigma_total"),
        "mc_expected_margin": sim.get("expected_margin"),
    }
    if close_total is not None:
        out["mc_p_over"]  = sim.get("p_over")
        out["mc_p_under"] = sim.get("p_under")
        # p_push not returned natively — approximate as 1 - p_over - p_under
        po = sim.get("p_over"); pu = sim.get("p_under")
        if po is not None and pu is not None:
            out["mc_p_push"] = round(max(0.0, 1.0 - po - pu), 3)
    # Rich simulator gives NRFI/YRFI probabilities natively
    if sim.get("p_nrfi") is not None:
        out["mc_p_nrfi"] = sim.get("p_nrfi")
        out["mc_p_yrfi"] = sim.get("p_yrfi")
    # Spread cover — approximate via expected_margin + normal distribution
    # (simulate_game doesn't accept spread as an input; extend later if we
    # want exact per-line covers).
    if close_spread is not None:
        em = sim.get("expected_margin")
        st = sim.get("sigma_total")
        if em is not None and st is not None and st > 0:
            from math import erf, sqrt
            # MLB: positive close_spread = home is dog getting cs runs.
            # Home covers when actual_margin > -close_spread (loses by <cs OR wins).
            z = (em - (-close_spread)) / (st * 0.5 / sqrt(2))
            out["mc_p_home_covers"] = round(0.5 * (1 + erf(z)), 3)
            out["mc_p_away_covers"] = round(1 - out["mc_p_home_covers"], 3)
    return out


def _compute_nrfi_ensemble(mc_p_nrfi: float, nrfi_score: float) -> dict:
    """NRFI ensemble scorer combining MC's native p_nrfi + sklearn's nrfi_score.

    Backtest (project_mc_v2_backtest_723, n=241 with both signals):
      Both agree + right = 61.8% hit rate (baseline signal lane)
      MC 70-79% conf alone = 63.6% (best single-signal)
      Sklearn 80%+ conf alone = 58.4% n=166 (still real)

    Tier ladder (highest signal first):
      ELITE  — both agree AND (MC>=65% OR sklearn>=75)
      STRONG — MC 70%+ alone OR sklearn 80%+ alone
      LEAN   — both agree at moderate conf
      SKIP   — models disagree at moderate conf

    Returns dict with ensemble_pick/tier/conf/reason or {} if neither
    signal available.
    """
    if mc_p_nrfi is None and nrfi_score is None:
        return {}
    # Normalize both to same "P(NRFI)" scale
    mc_p = float(mc_p_nrfi) if mc_p_nrfi is not None else None
    sk_p = float(nrfi_score) / 100.0 if nrfi_score is not None else None

    # Decide picks (NRFI if >0.5)
    mc_pick = ('NRFI' if mc_p > 0.5 else 'YRFI') if mc_p is not None else None
    sk_pick = ('NRFI' if sk_p > 0.5 else 'YRFI') if sk_p is not None else None
    mc_conf = max(mc_p, 1 - mc_p) if mc_p is not None else 0.0
    sk_conf = max(sk_p, 1 - sk_p) if sk_p is not None else 0.0

    both_agree = mc_pick is not None and sk_pick is not None and mc_pick == sk_pick
    tier = None
    reason = None
    pick = None
    conf = None
    if both_agree:
        if mc_conf >= 0.65 or sk_conf >= 0.75:
            tier, pick, conf = 'ELITE', mc_pick, (mc_conf + sk_conf) / 2
            reason = f'MC {int(mc_conf*100)}% + sklearn {int(sk_conf*100)}% agree'
        else:
            tier, pick, conf = 'LEAN', mc_pick, (mc_conf + sk_conf) / 2
            reason = f'both models lean {pick} at moderate conf'
    else:
        # No agreement — check single-signal thresholds
        if mc_p is not None and mc_conf >= 0.70:
            tier, pick, conf = 'STRONG', mc_pick, mc_conf
            reason = f'MC {int(mc_conf*100)}% (63.6% hit @ 70-79% band)'
        elif sk_p is not None and sk_conf >= 0.80:
            tier, pick, conf = 'STRONG', sk_pick, sk_conf
            reason = f'sklearn {int(sk_conf*100)}% (58.4% hit @ 80%+ band)'
        else:
            tier, pick, conf = 'SKIP', None, None
            reason = f'models disagree ({mc_pick} vs {sk_pick}) w/o high conf'
    return {
        'nrfi_ensemble_tier': tier,
        'nrfi_ensemble_pick': pick,
        'nrfi_ensemble_conf': round(conf, 3) if conf is not None else None,
        'nrfi_ensemble_reason': reason,
    }


def _compute_high_conf_flag(mc_data: dict) -> dict:
    """Extract MC high-confidence flag from probability bundle.

    Fires when |p_home_win - 0.5| >= 0.30 (i.e. MC says >=80% or <=20%).
    Backtest showed 71.1% hit rate at this threshold on n=135 (v2 post-
    mastery-unlock). App renders as Tier-1 chip.

    Returns {mc_high_conf_flag, mc_high_conf_side, mc_high_conf_pct}
    or {} if not qualifying.
    """
    p = mc_data.get('mc_p_home_win')
    if p is None:
        return {}
    try:
        p = float(p)
    except (TypeError, ValueError):
        return {}
    delta = abs(p - 0.5)
    if delta < 0.30:  # under 80% either side
        return {
            'mc_high_conf_flag': False,
            'mc_high_conf_side': None,
            'mc_high_conf_pct': None,
        }
    return {
        'mc_high_conf_flag': True,
        'mc_high_conf_side': 'HOME' if p >= 0.80 else 'AWAY',
        'mc_high_conf_pct': round(p if p >= 0.80 else 1 - p, 3),
    }


def write_mc_to_context(game_id: str, mc_data: dict) -> bool:
    """Patch mlb_game_context with MC probability blob + high-conf flags.

    Writes mc_probabilities as a JSON blob (schema-free) AND populates
    dedicated mc_high_conf_flag/_side/_pct columns for the app card badge
    (added migration 20260723_context_mc_high_conf.sql).
    """
    if not mc_data:
        return False
    payload = {"mc_probabilities": mc_data}
    # Merge high-confidence flag columns for the Tier-1 chip
    payload.update(_compute_high_conf_flag(mc_data))
    # NRFI ensemble — needs sklearn nrfi_score which lives on ctx.
    # Fetch it in a quick round trip so ensemble writes atomically.
    try:
        rr = requests.get(
            f"{SB}/rest/v1/mlb_game_context?game_id=eq.{game_id}&select=nrfi_score",
            headers=H_READ, timeout=8,
        )
        sk_score = None
        if rr.status_code == 200 and rr.json():
            sk_score = rr.json()[0].get('nrfi_score')
        ens = _compute_nrfi_ensemble(mc_data.get('mc_p_nrfi'), sk_score)
        payload.update(ens)
    except Exception:
        pass
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
