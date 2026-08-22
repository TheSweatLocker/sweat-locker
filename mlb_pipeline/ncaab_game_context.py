"""NCAAB game context pipeline — server-side analog of mlb_game_context.

Pulls today's NCAAB games from the Odds API, joins KenPom team stats
from ncaab_team_stats, computes model projections (spread, total) +
signal confluence + sweat score + primary_play, and upserts to
ncaab_game_context.

Model ported from the client-side calcGameSweatScore NCAAB branch
(2026-05-21) so app becomes a dumb renderer per the
feedback_backside_dictates_app_renders rule. Spreads/totals/ML only
for v1.0 — no NCAAB player props.

Offseason: this script can run year-round but produces 0 rows when the
Odds API has no NCAAB games. Cron activation deferred until November
when regular season starts. Built ahead so we have months to refine
with historical data backtests.

Naming: matches MLB convention (game_context = pick-time state for
today's slate, game_results = resolved outcomes).

Usage:
    python ncaab_game_context.py

Required env: SUPABASE_URL, SUPABASE_KEY, ODDS_API_KEY
"""
import os
import sys
import json
import requests
from datetime import datetime, date, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

READ_HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
WRITE_HEADERS = {**READ_HEADERS, "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"}

SEASON = "2025-26"
ODDS_SPORT_KEY = "basketball_ncaab"


# ─── data loaders ────────────────────────────────────────────────────────────

def today_et():
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    return et.strftime("%Y-%m-%d")


def fetch_odds_games():
    """Today's NCAAB games + market lines from Odds API."""
    if not ODDS_API_KEY:
        print("  ⚠️ Missing ODDS_API_KEY")
        return []
    try:
        r = requests.get(
            f"https://api.the-odds-api.com/v4/sports/{ODDS_SPORT_KEY}/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us,us2",
                "markets": "spreads,totals,h2h",
                "oddsFormat": "american",
                "bookmakers": "hardrockbet,draftkings,fanduel,espnbet,betmgm,caesars",
            },
            timeout=20,
        )
        if r.status_code != 200:
            print(f"  Odds API HTTP {r.status_code}: {r.text[:200]}")
            return []
        return r.json() or []
    except Exception as e:
        print(f"  Odds API error: {e}")
        return []


def fetch_team_stats():
    """All NCAAB teams' KenPom snapshot for the current season."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/ncaab_team_stats",
        params={"season": f"eq.{SEASON}", "select": "*", "limit": "500"},
        headers=READ_HEADERS, timeout=15,
    )
    if r.status_code != 200:
        return {}
    rows = r.json() or []
    return {r["team"]: r for r in rows}


def fetch_alias_map():
    """odds_api_name → canonical_name lookup."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/ncaab_team_aliases",
        params={"select": "canonical_name,odds_api_name,alt_names"},
        headers=READ_HEADERS, timeout=15,
    )
    if r.status_code != 200:
        return {}
    out = {}
    for row in r.json() or []:
        canonical = row["canonical_name"]
        if row.get("odds_api_name"):
            out[row["odds_api_name"].lower()] = canonical
        for alt in (row.get("alt_names") or []):
            out[alt.lower()] = canonical
        out[canonical.lower()] = canonical
    return out


def canonical_team(odds_name, alias_map):
    """Normalize an Odds API team name to the canonical name used in
    ncaab_team_stats. Falls back to identity if no alias match found —
    the join just returns None for stats, which we surface as a missing
    pipeline match rather than silently mis-attributing."""
    key = (odds_name or "").lower().strip()
    if key in alias_map:
        return alias_map[key]
    # Try stripping common mascot suffixes
    for suffix in (" wildcats", " bulldogs", " tigers", " eagles", " hawks",
                   " bears", " lions", " spartans", " hurricanes", " sooners"):
        if key.endswith(suffix):
            stripped = key.replace(suffix, "")
            if stripped in alias_map:
                return alias_map[stripped]
    return odds_name


# ─── model: projections + confluence + sweat score ─────────────────────────

def _avg_market_line(bookmakers, market_key, outcome_filter=None):
    """Average a numeric market line across books (spread, total)."""
    vals = []
    for bm in bookmakers or []:
        for mkt in bm.get("markets", []):
            if mkt.get("key") != market_key:
                continue
            for o in mkt.get("outcomes", []):
                if outcome_filter and not outcome_filter(o):
                    continue
                pt = o.get("point")
                if pt is not None:
                    vals.append(float(pt))
    return sum(vals) / len(vals) if vals else None


def _avg_ml(bookmakers, team_name):
    """Average ML price for a team."""
    vals = []
    for bm in bookmakers or []:
        for mkt in bm.get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            for o in mkt.get("outcomes", []):
                if o.get("name") == team_name and o.get("price") is not None:
                    vals.append(int(o["price"]))
    return int(sum(vals) / len(vals)) if vals else None


def compute_projections(home, away):
    """Compute projected_spread (positive = home favored), projected_total,
    pace, and per-team predicted points using KenPom efficiency formulas.

    Ported from client-side calcGameSweatScore NCAAB branch (line 2678):
      predictedSpread = (home.adjEM - away.adjEM) / 3
      projectedTotal  = ((avg_oe + avg_de) / 2 / 100) * pace * 2

    The /3 divisor is a calibration constant that translates adjEM
    differential (which is per-100-possession) to expected spread at
    typical tempo. Sourced from historical NCAAB backtest in the client
    branch — kept for parity until we collect server-side data."""
    h_em = home.get("adj_em")
    a_em = away.get("adj_em")
    h_oe = home.get("adj_oe")
    a_oe = away.get("adj_oe")
    h_de = home.get("adj_de")
    a_de = away.get("adj_de")
    h_tempo = home.get("tempo")
    a_tempo = away.get("tempo")

    out = {
        "projected_spread": None,
        "projected_total": None,
        "pace_avg": None,
        "model_pred_home_points": None,
        "model_pred_away_points": None,
        "adj_em_gap": None,
    }
    if None in (h_em, a_em):
        return out
    out["adj_em_gap"] = round(float(h_em) - float(a_em), 2)
    out["projected_spread"] = round(out["adj_em_gap"] / 3.0, 2)

    if None not in (h_oe, a_oe, h_de, a_de, h_tempo, a_tempo):
        pace = (float(h_tempo) + float(a_tempo)) / 2.0 - 2.5
        out["pace_avg"] = round(pace, 2)
        # Per-team point projection: own offense vs opp defense, scaled by pace
        # home_pts ≈ (home_oe + away_de) / 2 / 100 * pace
        # This is the symmetric efficiency formula; the divisor by 2 matches
        # client logic. Per-team breakout (vs aggregated total) lets us
        # compute spread + total from the same source.
        h_pts = ((float(h_oe) + float(a_de)) / 2.0 / 100.0) * pace
        a_pts = ((float(a_oe) + float(h_de)) / 2.0 / 100.0) * pace
        out["model_pred_home_points"] = round(h_pts, 1)
        out["model_pred_away_points"] = round(a_pts, 1)
        out["projected_total"] = round(h_pts + a_pts, 1)
    return out


def compute_confluence(home, away):
    """Count signals that lean toward home vs away, mirror of MLB's
    signal_confluence_breakdown. Returns (net, breakdown_dict)."""
    breakdown = {}

    # adj_em — pure efficiency
    if home.get("adj_em") is not None and away.get("adj_em") is not None:
        if home["adj_em"] > away["adj_em"]:
            breakdown["adj_em"] = "home"
        elif away["adj_em"] > home["adj_em"]:
            breakdown["adj_em"] = "away"

    # Four-factor exploit edges (offense rank vs opp defense rank)
    # If home_off_rank is materially better than away_def_rank → home edge
    EXPLOIT = 100  # rank-points threshold for an exploitable edge
    for factor in ("efg", "to", "or", "ftr"):
        h_off = home.get(f"{factor}_o_rank")
        h_def = home.get(f"{factor}_d_rank")
        a_off = away.get(f"{factor}_o_rank")
        a_def = away.get(f"{factor}_d_rank")
        # home exploits = home_off significantly better than away_def
        if h_off is not None and a_def is not None and (a_def - h_off) >= EXPLOIT:
            breakdown[f"{factor}_exploit"] = "home"
        elif a_off is not None and h_def is not None and (h_def - a_off) >= EXPLOIT:
            breakdown[f"{factor}_exploit"] = "away"

    # Defense edge — adj_de_rank
    if home.get("adj_de_rank") is not None and away.get("adj_de_rank") is not None:
        if abs(home["adj_de_rank"] - away["adj_de_rank"]) >= 30:
            breakdown["defense"] = "home" if home["adj_de_rank"] < away["adj_de_rank"] else "away"

    # Tempo edge — slower team tends to control pace
    if home.get("tempo") is not None and away.get("tempo") is not None:
        if abs(home["tempo"] - away["tempo"]) >= 3:
            breakdown["tempo_control"] = "home" if home["tempo"] < away["tempo"] else "away"

    # SOS edge — strength of schedule
    if home.get("sos") is not None and away.get("sos") is not None:
        if abs(home["sos"] - away["sos"]) >= 2.5:
            breakdown["sos"] = "home" if home["sos"] > away["sos"] else "away"

    # Luck — negative luck is a positive regression signal
    h_luck = home.get("luck"); a_luck = away.get("luck")
    if h_luck is not None and a_luck is not None:
        if a_luck < -0.05 and h_luck > a_luck + 0.05:
            breakdown["luck"] = "away"  # away due to regress UP (was unlucky)
        elif h_luck < -0.05 and a_luck > h_luck + 0.05:
            breakdown["luck"] = "home"

    h = sum(1 for v in breakdown.values() if v == "home")
    a = sum(1 for v in breakdown.values() if v == "away")
    net = h - a  # positive = home leans, negative = away
    return net, breakdown


def compute_sweat_score(projected_spread, close_spread, confluence_net, projected_total, close_total):
    """Single sweat score 0-100. Mirror of MLB structure: blend of model
    edge magnitude + signal confluence + total disagreement.

    Tier cutoffs match MLB / play_of_day._sweat_tier:
      PRIME ≥ 80 | STRONG ≥ 65 | LIGHT ≥ 50 | PASS < 50
    """
    score = 45  # base

    # Spread edge — model vs market
    if projected_spread is not None and close_spread is not None:
        # close_spread is home perspective: negative = home favored
        # projected_spread positive = home favored → match signs
        spread_edge = abs(projected_spread + close_spread)  # MLB convention
        if spread_edge >= 4.0:
            score += 25
        elif spread_edge >= 3.0:
            score += 18
        elif spread_edge >= 2.0:
            score += 12
        elif spread_edge >= 1.0:
            score += 6

    # Confluence — multi-signal stacking
    abs_conf = abs(confluence_net)
    if abs_conf >= 5:
        score += 18
    elif abs_conf >= 4:
        score += 12
    elif abs_conf >= 3:
        score += 8
    elif abs_conf >= 2:
        score += 4

    # Total edge
    if projected_total is not None and close_total is not None:
        total_edge = abs(projected_total - close_total)
        if total_edge >= 8.0:
            score += 8
        elif total_edge >= 5.0:
            score += 5
        elif total_edge >= 3.0:
            score += 3

    return min(100, max(0, score))


def sweat_tier(score):
    if score >= 80: return "PRIME"
    if score >= 65: return "STRONG"
    if score >= 50: return "LIGHT_LEAN"
    return "PASS"


def compute_primary_play(ctx, alias_map=None):
    """Analog of mlb_game_context.compute_primary_play. Spreads/totals/ML
    only (no NCAAB props in v1).

    Priority order:
      1. PRIME ML — confluence ≥+4 AND |spread_edge| ≥ 2.0 (matches MLB trap zone audit)
      2. STRONG OVER/UNDER — model total vs market diff ≥ 5 points
      3. STRONG ML — confluence ≥+2 AND |spread_edge| ≥ 1.5
      4. LIGHT total lean — diff ≥ 3 points
    """
    conf = ctx.get("signal_confluence_net") or 0
    proj_spread = ctx.get("projected_spread")
    close_spread = ctx.get("close_spread") or ctx.get("open_spread")
    home_team = ctx.get("home_team") or "Home"
    away_team = ctx.get("away_team") or "Away"

    spread_edge = None
    if proj_spread is not None and close_spread is not None:
        # MLB convention: spread_edge = proj_spread + close_spread (opposite signs cancel/stack)
        spread_edge = round(float(proj_spread) + float(close_spread), 2)
    abs_edge = abs(spread_edge) if spread_edge is not None else 0.0
    fav = home_team if (proj_spread is not None and float(proj_spread) > 0) else away_team

    proj_total = ctx.get("projected_total")
    close_total = ctx.get("close_total") or ctx.get("open_total")
    total_edge = None
    if proj_total is not None and close_total is not None:
        total_edge = round(float(proj_total) - float(close_total), 2)

    # 1. PRIME ML — confluence + meaningful spread edge above trap zone
    if conf >= 4 and abs_edge >= 2.0:
        return {
            "type": "ml",
            "tier": "PRIME",
            "label": f"{fav} ML",
            "sub": f"PRIME confluence ({conf} signals, {abs_edge:.1f} edge)",
            "signal_floor": 85,
        }

    # 2. STRONG total — big model disagreement on total
    if total_edge is not None and abs(total_edge) >= 5.0:
        side = "Over" if total_edge > 0 else "Under"
        return {
            "type": "total",
            "tier": "STRONG",
            "label": f"{side} {close_total}",
            "sub": f"Model projects {ctx.get('projected_total'):.1f} vs market {close_total} ({total_edge:+.1f})",
            "signal_floor": 72,
        }

    # 3. STRONG ML — moderate confluence + meaningful spread edge
    if conf >= 2 and abs_edge >= 1.5:
        return {
            "type": "ml",
            "tier": "STRONG",
            "label": f"{fav} ML lean",
            "sub": f"STRONG confluence ({conf} signals, {abs_edge:.1f} edge)",
            "signal_floor": 70,
        }

    # 4. LIGHT total
    if total_edge is not None and abs(total_edge) >= 3.0:
        side = "Over" if total_edge > 0 else "Under"
        return {
            "type": "total",
            "tier": "LIGHT",
            "label": f"{side} {close_total}",
            "sub": f"Model {ctx.get('projected_total'):.1f} vs line {close_total}",
            "signal_floor": 60,
        }

    return None


# ─── orchestration ──────────────────────────────────────────────────────────

def build_context_row(event, team_stats, alias_map):
    """Build a single ncaab_game_context row from an Odds API event."""
    away_raw = event.get("away_team")
    home_raw = event.get("home_team")
    if not away_raw or not home_raw:
        return None

    away_canonical = canonical_team(away_raw, alias_map)
    home_canonical = canonical_team(home_raw, alias_map)

    away_stats = team_stats.get(away_canonical) or {}
    home_stats = team_stats.get(home_canonical) or {}

    bookmakers = event.get("bookmakers", [])

    # Market lines — average across books
    open_total = _avg_market_line(bookmakers, "totals")
    open_spread_home = _avg_market_line(
        bookmakers, "spreads",
        outcome_filter=lambda o: o.get("name") == home_raw,
    )
    away_ml = _avg_ml(bookmakers, away_raw)
    home_ml = _avg_ml(bookmakers, home_raw)

    proj = compute_projections(home_stats, away_stats)
    conf_net, conf_breakdown = compute_confluence(home_stats, away_stats)

    sweat = compute_sweat_score(
        proj.get("projected_spread"),
        open_spread_home,
        conf_net,
        proj.get("projected_total"),
        open_total,
    )
    tier = sweat_tier(sweat)

    row = {
        "game_id": event.get("id"),
        "game_date": today_et(),
        "season": SEASON,
        "home_team": home_canonical,
        "away_team": away_canonical,
        "conference_home": home_stats.get("conference"),
        "conference_away": away_stats.get("conference"),
        "home_adj_em": home_stats.get("adj_em"),
        "away_adj_em": away_stats.get("adj_em"),
        "adj_em_gap": proj.get("adj_em_gap"),
        "home_adj_oe": home_stats.get("adj_oe"),
        "away_adj_oe": away_stats.get("adj_oe"),
        "home_adj_de": home_stats.get("adj_de"),
        "away_adj_de": away_stats.get("adj_de"),
        "pace_avg": proj.get("pace_avg"),
        "home_tempo": home_stats.get("tempo"),
        "away_tempo": away_stats.get("tempo"),
        "home_efg_o": home_stats.get("efg_o"),
        "away_efg_o": away_stats.get("efg_o"),
        "home_efg_d": home_stats.get("efg_d"),
        "away_efg_d": away_stats.get("efg_d"),
        "home_to_o": home_stats.get("to_o"),
        "away_to_o": away_stats.get("to_o"),
        "home_or_o": home_stats.get("or_o"),
        "away_or_o": away_stats.get("or_o"),
        "home_ftr_o": home_stats.get("ftr_o"),
        "away_ftr_o": away_stats.get("ftr_o"),
        "home_record": f"{home_stats.get('wins', '?')}-{home_stats.get('losses', '?')}" if home_stats else None,
        "away_record": f"{away_stats.get('wins', '?')}-{away_stats.get('losses', '?')}" if away_stats else None,
        "open_total": open_total,
        "open_spread": open_spread_home,
        "home_ml_open": home_ml,
        "away_ml_open": away_ml,
        "projected_total": proj.get("projected_total"),
        "projected_spread": proj.get("projected_spread"),
        "model_pred_home_points": proj.get("model_pred_home_points"),
        "model_pred_away_points": proj.get("model_pred_away_points"),
        "signal_confluence_net": conf_net,
        "signal_confluence_breakdown": conf_breakdown,
        "game_time_et": event.get("commence_time"),
        "sweat_score": sweat,
        "sweat_tier": tier,
    }
    row["primary_play"] = compute_primary_play(row, alias_map)
    return row


def _enrich_ncaab_rest_days(rows: list) -> None:
    """Populate home_days_rest / away_days_rest by walking recent
    ncaab_game_results (mirrors NHL / NFL pattern)."""
    from datetime import date, timedelta
    if not rows: return
    dates_in_slate = [r.get('game_date') for r in rows if r.get('game_date')]
    if not dates_in_slate: return
    # Coerce to date objects
    dobjs = []
    for d in dates_in_slate:
        if isinstance(d, str):
            try: dobjs.append(date.fromisoformat(d))
            except ValueError: continue
        elif isinstance(d, date):
            dobjs.append(d)
    if not dobjs: return
    lookback = (min(dobjs) - timedelta(days=14)).isoformat()

    # Bulk pull prior 14 days of results for date lookup
    try:
        r = requests.get(f'{SUPABASE_URL}/rest/v1/ncaab_game_results',
                         headers=READ_HEADERS,
                         params={'game_date': f'gte.{lookback}',
                                 'select': 'game_date,home_team,away_team'}, timeout=15)
        history = r.json() if r.status_code == 200 else []
    except Exception:
        history = []

    team_dates: dict = {}
    for h in history + rows:
        d = h.get('game_date')
        if isinstance(d, str):
            try: d = date.fromisoformat(d)
            except ValueError: continue
        if not d: continue
        for team in (h.get('home_team'), h.get('away_team')):
            if team:
                team_dates.setdefault(team, set()).add(d)

    for row in rows:
        d = row.get('game_date')
        if isinstance(d, str):
            try: d = date.fromisoformat(d)
            except ValueError: continue
        if not d: continue
        for prefix, team in (('home', row.get('home_team')),
                              ('away', row.get('away_team'))):
            if not team: continue
            prior = sorted(x for x in team_dates.get(team, set()) if x < d)
            row[f'{prefix}_days_rest'] = (d - prior[-1]).days if prior else None


def upsert_context(rows):
    if not rows:
        return 0
    n = 0
    for row in rows:
        # Strip None values to preserve existing column values on re-runs
        clean = {k: v for k, v in row.items() if v is not None}
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/ncaab_game_context",
            params={"on_conflict": "game_id"},
            headers=WRITE_HEADERS,
            json=clean,
            timeout=15,
        )
        if r.status_code in (200, 201, 204):
            n += 1
        else:
            print(f"  upsert failed for {row.get('game_id')}: {r.status_code} {r.text[:200]}")
    return n


def run():
    print(f"NCAAB game context — {today_et()}")
    events = fetch_odds_games()
    print(f"  {len(events)} games from Odds API")
    if not events:
        print("  (offseason or no games today — nothing to write)")
        return
    team_stats = fetch_team_stats()
    print(f"  {len(team_stats)} teams loaded from ncaab_team_stats")
    alias_map = fetch_alias_map()
    print(f"  {len(alias_map)} aliases loaded")

    rows = []
    for event in events:
        row = build_context_row(event, team_stats, alias_map)
        if row:
            rows.append(row)

    # 2026-08-22 (silent-bug audit #15b): enrich home_days_rest / away_days_rest
    # so the ncaab_home_rest_edge signal (reads ctx.home_days_rest) can fire.
    try:
        _enrich_ncaab_rest_days(rows)
    except Exception as _e:
        print(f'  ⚠ NCAAB rest-days enrichment failed (non-fatal): {_e}')

    n = upsert_context(rows)
    print(f"  ✓ upserted {n}/{len(rows)} rows")
    # Show top PRIME/STRONG plays for visibility
    primes = [r for r in rows if r.get("sweat_tier") in ("PRIME", "STRONG")]
    if primes:
        print(f"\n  Top plays ({len(primes)} PRIME/STRONG):")
        for r in sorted(primes, key=lambda x: -(x.get("sweat_score") or 0))[:5]:
            pp = r.get("primary_play") or {}
            print(f"    {r['away_team']} @ {r['home_team']} | "
                  f"{r['sweat_tier']} {r['sweat_score']} | "
                  f"{pp.get('label', '-')} ({pp.get('sub', '')[:50]})")


if __name__ == "__main__":
    try:
        from season_gate import season_gate_or_exit
        season_gate_or_exit('NCAAB')
    except ImportError:
        pass
    run()
