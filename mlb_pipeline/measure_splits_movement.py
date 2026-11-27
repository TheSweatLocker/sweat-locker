"""Does money-flow MOVEMENT beat a money-flow SNAPSHOT? Measured on the history.

CONTEXT (2026-11-27)
════════════════════
The 2026-11-27 money-flow study read `game_context.splits_summary`, which is
one aggregated row per game, and found NCAAB sides +7.55% on n=300 at +1.37
standard errors - suggestive, not conclusive, and un-resolvable at that
sample size. I reported that the fix needed a migration.

It did not. `public_splits_v2` has held per-capture history since
2026-08-23 (791,942 rows, six sports); the aggregator was collapsing it.
See `splits_history_cache.py` for the full correction.

This script asks the question the collapsed column could not:

    LEVEL     where the money sits at the last pre-game capture
              (money_pct - bets_pct, the divergence the old study used)
    MOVEMENT  where the money has TRAVELLED between the first and last
              pre-game capture (d_money, d_div) - steam and reverse line
              movement, which need two observations and were being discarded

WHAT IS GUARDED, AND WHY EACH GUARD IS HERE
───────────────────────────────────────────
* PRE-KICKOFF ONLY. 81% of NCAAF captures and 75% of NFL captures landed
  after kickoff (measured by the 2026-09-29 guard in splits_v2_pipeline).
  Features come only from captures strictly before the start time, and a
  game with no resolvable start time is dropped. MLB is excluded entirely:
  it has no start-time column on context or results and zero line_history
  rows, so the guard cannot be honoured.
* UNITS, NOT HIT RATE. A 62.8% rule lost money earlier this week because
  its winners were heavy favourites. Every row below is priced at the
  stored closing price for the side actually backed, never a sign flip of
  the other side.
* A PRICE IS |odds| >= 100. 333 NCAAB rows hold values inside that band;
  they are not prices and are skipped.
* NCAAB NAMES ITS ML COLUMNS THE OTHER WAY. `close_home_ml` is 0% filled on
  ncaab_game_results while its twin `home_ml_close` is populated; football,
  NBA and NHL are the reverse. Reading the wrong one yields a silent zero
  sample, so the column names are per-sport.
* CONTROLS. always-HOME, always-FAVOURITE and always-OVER run alongside, so
  a "signal" that is really a side preference is visible as one.
* THRESHOLDS ARE SWEPT, NOT CHOSEN. Every cut is reported, including the
  losing ones, because picking the best cell after looking is how a
  threshold becomes an edge that is not there.

Nothing here changes a weight, a gate or a card. It is a measurement.

Usage:
    python measure_splits_movement.py                 # all served sports
    python measure_splits_movement.py NCAAB NCAAF
    python measure_splits_movement.py --force         # re-pull the caches
"""
from __future__ import annotations

import math
import os
import pickle
import sys
from collections import defaultdict

import requests
from dotenv import load_dotenv

from splits_history_cache import (_cache_path, _getall, _p, build_features,
                                  load_history, load_kickoffs)

load_dotenv()
SB = os.environ.get("SUPABASE_URL")
K = os.environ.get("SUPABASE_KEY")
H = {"apikey": K, "Authorization": f"Bearer {K}"}

SERVED = ("NCAAF", "NFL", "NCAAB", "NBA", "NHL")

# Per-sport results wiring. `ml_home`/`ml_away` differ by sport on purpose -
# see the NCAAB note in the docstring.
RESULTS = {
    "NCAAF": {"table": "ncaaf_game_results", "ml_home": "close_home_ml", "ml_away": "close_away_ml"},
    "NFL":   {"table": "nfl_game_results",   "ml_home": "close_home_ml", "ml_away": "close_away_ml"},
    "NBA":   {"table": "nba_game_results",   "ml_home": "close_home_ml", "ml_away": "close_away_ml"},
    # NHL results carries no `close_spread` at all (42703) - the handicap
    # lives on game_context as `close_puckline`. Selecting it here is what
    # found that. Nothing below needs the spread LINE, only the result and
    # the price, so it is simply not requested for NHL.
    "NHL":   {"table": "nhl_game_results",   "ml_home": "close_home_ml",
              "ml_away": "close_away_ml", "no_spread_col": True},
    "NCAAB": {"table": "ncaab_game_results", "ml_home": "home_ml_close", "ml_away": "away_ml_close"},
}
STD_PRICE = -110.0          # fallback ONLY where no juice is stored

# Real stored juice, where it exists. NCAAB and football store a spread and a
# total LINE but never the price on it (feedback_ncaab_engine_weights_a_
# favourite_detector), so those two markets fall back to -110 and the ROI
# there is "at -110", not "at the book". NBA and NHL DO store both prices, and
# using -110 for an NHL puckline would be badly wrong - pucklines routinely
# sit near +170/-200, so a -110 assumption would invent profit on dogs and
# destroy it on favourites. These live on game_context, not game_results.
PRICE_SOURCE = {
    "NBA": ("nba_game_context", {"rl_HOME": "close_spread_home_price",
                                 "rl_AWAY": "close_spread_away_price",
                                 "total_OVER": "close_total_over_price",
                                 "total_UNDER": "close_total_under_price"}),
    "NHL": ("nhl_game_context", {"rl_HOME": "close_puckline_home_price",
                                 "rl_AWAY": "close_puckline_away_price",
                                 "total_OVER": "close_total_over_price",
                                 "total_UNDER": "close_total_under_price"}),
}


def payout(odds) -> float | None:
    """Units won on a 1u winner. A plus price pays odds/100, not 100/odds."""
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if abs(o) < 100:
        return None          # not an American price
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def _result_map(sport: str, force: bool = False) -> dict[str, dict]:
    """splits game_id -> result row, using the sport's own key path.

    The key spaces are not uniform. NCAAF splits ids already match its
    results table. NFL splits ids are md5 and its results table keys on a
    slug, bridged by nfl_game_context.results_game_id. NCAAB has no bridge
    column at all, so it is resolved on names plus date.
    """
    path = _cache_path(f"{sport}_res")
    if os.path.exists(path) and not force:
        with open(path, "rb") as f:
            return pickle.load(f)
    cfg = RESULTS[sport]
    cols = ("game_id,game_date,home_team,away_team,home_score,away_score,"
            "close_total,spread_result,total_result,"
            f"{cfg['ml_home']},{cfg['ml_away']}")
    if not cfg.get("no_spread_col"):
        cols += ",close_spread"
    res = _getall(cfg["table"], cols, {}, f"{sport} results", key="game_id")
    out: dict[str, dict] = {}
    if sport in ("NCAAF", "NBA", "NHL"):
        for r in res:
            out[str(r["game_id"])] = r
    elif sport == "NFL":
        ctx = _getall("nfl_game_context", "game_id,results_game_id",
                      {"results_game_id": "not.is.null"}, "NFL ctx bridge",
                      key="game_id")
        by_res = {str(r["game_id"]): r for r in res}
        for c in ctx:
            r = by_res.get(str(c["results_game_id"]))
            if r:
                out[str(c["game_id"])] = r
    elif sport == "NCAAB":
        ctx = _getall("ncaab_game_context", "game_id,home_team,away_team,game_date",
                      {}, "NCAAB ctx", key="game_id")
        by_name: dict[tuple, dict] = {}
        for r in res:
            by_name[(str(r.get("home_team")), str(r.get("away_team")),
                     str(r.get("game_date")))] = r
        for c in ctx:
            key = (str(c.get("home_team")), str(c.get("away_team")), str(c.get("game_date")))
            r = by_name.get(key)
            if r:
                out[str(c["game_id"])] = r
    with open(path, "wb") as f:
        pickle.dump(out, f)
    return out


def _price_map(sport: str, force: bool = False) -> dict[str, dict]:
    """splits game_id -> stored spread/total prices, for sports that have them."""
    ent = PRICE_SOURCE.get(sport)
    if not ent:
        return {}
    tbl, cols = ent
    path = _cache_path(f"{sport}_px")
    if os.path.exists(path) and not force:
        with open(path, "rb") as f:
            return pickle.load(f)
    rows = _getall(tbl, "game_id," + ",".join(sorted(set(cols.values()))), {},
                   f"{sport} prices", key="game_id")
    out = {str(x["game_id"]): x for x in rows}
    with open(path, "wb") as f:
        pickle.dump(out, f)
    return out


def grade(sport: str, mkt: str, side: str, r: dict,
          px: dict | None = None) -> tuple[float, float] | None:
    """(units_returned, 1.0) for a 1u bet, or None when unplayable.

    units_returned is +payout on a win, -1 on a loss, 0 on a push.
    """
    cfg = RESULTS[sport]

    def _side_price() -> float:
        ent = PRICE_SOURCE.get(sport)
        if ent and px:
            col = ent[1].get(f"{mkt}_{side}")
            if col is not None:
                v = payout(px.get(col))
                if v is not None:
                    return v
        return payout(STD_PRICE)

    if mkt == "ml":
        hs, as_ = r.get("home_score"), r.get("away_score")
        if hs is None or as_ is None or hs == as_:
            return None
        price = r.get(cfg["ml_home"]) if side == "HOME" else r.get(cfg["ml_away"])
        pay = payout(price)
        if pay is None:
            return None
        won = (hs > as_) if side == "HOME" else (as_ > hs)
        return (pay if won else -1.0), 1.0
    if mkt == "rl":
        sr = r.get("spread_result")
        if not sr:
            return None
        if sr == "push":
            return 0.0, 1.0
        pay = _side_price()
        won = (sr == "home_covered") if side == "HOME" else (sr == "away_covered")
        return (pay if won else -1.0), 1.0
    if mkt == "total":
        tr = r.get("total_result")
        if not tr:
            return None
        if tr == "push":
            return 0.0, 1.0
        pay = _side_price()
        won = (tr == "over") if side == "OVER" else (tr == "under")
        return (pay if won else -1.0), 1.0
    return None


def tally(name: str, rets: list[float]) -> str:
    n = len(rets)
    if n == 0:
        return f"    {name:<34s}      n=0"
    wins = sum(1 for x in rets if x > 0)
    dec = sum(1 for x in rets if x != 0)
    roi = 100.0 * sum(rets) / n
    hit = 100.0 * wins / dec if dec else 0.0
    if n > 1:
        mu = sum(rets) / n
        var = sum((x - mu) ** 2 for x in rets) / (n - 1)
        se = 100.0 * math.sqrt(var / n)
    else:
        se = float("nan")
    ses = roi / se if se and se == se and se > 0 else float("nan")
    return (f"    {name:<34s} n={n:<5d} hit={hit:5.1f}%  "
            f"ROI={roi:+6.2f}%  units={sum(rets):+7.2f}  {ses:+5.2f} SE")


def run(sport: str, force: bool = False) -> None:
    _p(f"\n{'='*78}\n{sport}\n{'='*78}")
    rows = load_history(sport, force=force)
    ko = load_kickoffs(sport, force=force)
    if not ko:
        _p("  no start-time source; skipped (see splits_history_cache.KICKOFF_SOURCE)")
        return
    feat = build_features(rows, ko)
    res = _result_map(sport, force=force)
    pxm = _price_map(sport, force=force)
    games_feat = {g for (g, _, _) in feat}
    joined = {g for g in games_feat if g in res}
    _p(f"  pre-kickoff pairs {len(feat):,} over {len(games_feat):,} games | "
       f"joined to a result: {len(joined):,} "
       f"({100.0*len(joined)/max(len(games_feat),1):.1f}%)")

    # playable rows: (market, side, features, result)
    play = []
    for (gid, mkt, side), f in feat.items():
        r = res.get(gid)
        if not r:
            continue
        g = grade(sport, mkt, side, r, pxm.get(gid))
        if g is None:
            continue
        play.append((mkt, side, f, r, g[0]))
    _p(f"  playable graded rows: {len(play):,}")
    if not play:
        return

    by_mkt = defaultdict(list)
    for p in play:
        by_mkt[p[0]].append(p)

    for mkt in ("ml", "rl", "total"):
        rowsm = by_mkt.get(mkt) or []
        if not rowsm:
            continue
        multi = [p for p in rowsm if (p[2].get("n_cap") or 0) > 1
                 and p[2].get("d_money") is not None]
        _p(f"\n  -- {sport} {mkt.upper()}  rows={len(rowsm):,}  "
           f"with movement={len(multi):,}")
        _p(tally("ALL rows (back every side)", [p[4] for p in rowsm]))

        # controls
        _p(tally("control: always HOME/OVER",
                 [p[4] for p in rowsm if p[1] in ("HOME", "OVER")]))
        if mkt == "ml":
            favs = []
            for p in rowsm:
                r = p[3]
                cfg = RESULTS[sport]
                hp, ap = payout(r.get(cfg["ml_home"])), payout(r.get(cfg["ml_away"]))
                if hp is None or ap is None:
                    continue
                is_fav_side = (p[1] == "HOME") if hp < ap else (p[1] == "AWAY")
                if is_fav_side:
                    favs.append(p[4])
            _p(tally("control: always FAVOURITE", favs))

        # LEVEL: divergence at the last pre-game capture
        for thr in (5, 10, 15, 20):
            sel = [p[4] for p in rowsm
                   if p[2].get("last_div") is not None and p[2]["last_div"] >= thr]
            _p(tally(f"LEVEL last_div >= {thr}pp", sel))

        # MOVEMENT: money travelling toward the side before kickoff
        for thr in (2, 5, 10):
            sel = [p[4] for p in multi if p[2]["d_money"] >= thr]
            _p(tally(f"MOVE d_money >= {thr}pp", sel))
            sel = [p[4] for p in multi if p[2]["d_money"] <= -thr]
            _p(tally(f"MOVE d_money <= -{thr}pp (fade)", sel))
        for thr in (2, 5):
            sel = [p[4] for p in multi
                   if p[2].get("d_div") is not None and p[2]["d_div"] >= thr]
            _p(tally(f"MOVE d_div >= {thr}pp", sel))

        # REVERSE: money arriving on the side the tickets are NOT on
        for mt, bt in ((5, 45), (10, 45), (5, 40)):
            sel = [p[4] for p in multi
                   if p[2]["d_money"] >= mt and (p[2].get("last_bets") or 100) <= bt]
            _p(tally(f"REVERSE d_money>={mt} & bets<={bt}%", sel))

        # LEVEL + MOVEMENT together
        for dv, mt in ((10, 2), (10, 5)):
            sel = [p[4] for p in multi
                   if (p[2].get("last_div") or -99) >= dv and p[2]["d_money"] >= mt]
            _p(tally(f"last_div>={dv} AND d_money>={mt}", sel))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    force = any(a.lower() == "--force" for a in args)
    want = [a.upper() for a in args if not a.startswith("--")] or list(SERVED)
    for s in want:
        if s not in RESULTS:
            _p(f"{s}: not served")
            continue
        try:
            run(s, force=force)
        except Exception as e:
            import traceback
            _p(f"{s}: FAILED {type(e).__name__}: {e}")
            traceback.print_exc()
