"""
NFL game reads — server-side Jerry. Lighter struct than MLB/NBA because
NFL Phase 2 (per-game picks pipeline) hasn't shipped yet — driven by Odds API
markets + `nfl_team_stats` season-long EPA/efficiency only. Will get richer
when Phase 2 lands (just extend build_struct).

For each NFL game in the next 8 days, assembles market context (spread, total,
ML) and per-team EPA/efficiency, feeds it to Claude with the NFL prompt
template, writes {narrative, struct} to jerry_cache keyed
game_read_<odds-api-id>_<ET date>, sport='nfl'.

Usage: python generate_nfl_game_reads.py [--force] [--limit N] [--force-offseason]

Season gate (2026-08-22): exits 0 immediately if NFL is off-season (March-
August) unless --force-offseason is passed. Removes the every-cron waste
where this script fetched preseason odds + iterated preseason games only
to skip them via panel_pred null checks downstream.
"""
import os
import sys
import json
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

# Season gate — must come before any imports/setup that hit APIs.
try:
    from season_gate import season_gate_or_exit
    season_gate_or_exit('NFL')
except ImportError:
    pass  # helper is new — if missing, fall through to legacy behavior
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

SB_READ = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
SB_WRITE = {**SB_READ, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}
MODEL = "claude-haiku-4-5-20251001"


def today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def nfl_week_start_thu():
    """Return the Thursday of the current NFL week as YYYY-MM-DD.

    NFL Week 1 Thursday 2026 = 2026-09-04. NFL week runs Thu -> Wed for
    Jerry-lock purposes (Thu-lock keeps reads stable across the entire
    weekly slate incl. TNF, SNF, MNF, and the Wed-night SEASON OPENER
    which anchors to that same week).

    Day-of-week handling:
      Wed: roll FORWARD to tomorrow (Thu). Wed-night opener belongs
           to the upcoming Thursday's week, not last week's. This
           makes the Wed 8am ET cron generate reads that Thu-lock
           preserves for the rest of the week.
      Thu: today (same-day generation baseline).
      Fri-Tue: roll BACK to the most recent Thursday.

    2026-09-02: introduced with Thu-lock. Cache keys switch from
    per-day to per-week so subsequent-day cron runs no-op.
    2026-09-02 v2: Wed rolls forward per NFL Week 1 opener (Wed 9/3).
    """
    now_et = datetime.now(timezone.utc) - timedelta(hours=4)
    dow = now_et.weekday()  # Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
    if dow == 2:  # Wednesday — roll forward to tomorrow (Thursday)
        thu = now_et + timedelta(days=1)
    else:
        days_since_thu = (dow - 3) % 7
        thu = now_et - timedelta(days=days_since_thu)
    return thu.strftime("%Y-%m-%d")


def now_et_human():
    d = datetime.now(timezone.utc) - timedelta(hours=4)
    return f"{d.strftime('%A, %B')} {d.day}, {d.year}"


def _f(v):
    try:
        return float(v)
    except Exception:
        return None


def sb_get(path, params=None):
    qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = f"{SUPABASE_URL}/rest/v1/{path}{'?' if qs else ''}{qs}"
    r = requests.get(url, headers=SB_READ, timeout=20)
    return r.json() if r.status_code == 200 else []


def load_templates():
    rows = sb_get("prompt_templates", {
        "name": "in.(game_read_wrapper,game_read_universal,game_read_rules)",
        "is_active": "is.true",
        "select": "name,sport,template",
    })
    out = {(r["name"], r["sport"]): r["template"] for r in rows}
    wrapper = out.get(("game_read_wrapper", "ALL"))
    universal = out.get(("game_read_universal", "ALL"))
    rules = out.get(("game_read_rules", "NFL")) or out.get(("game_read_rules", "NHL"))  # NFL falls back to market template until Phase 2
    if not (wrapper and universal and rules):
        print(f"  ⚠️ missing template rows — have: {list(out.keys())}")
        return None
    return {"wrapper": wrapper, "universal": universal, "rules": rules}


def fetch_odds_games():
    if not ODDS_API_KEY:
        print("  No ODDS_API_KEY — can't fetch NFL slate")
        return []
    # 2026-08-22: preseason merge REMOVED. The 8/13 change fetched preseason
    # odds and iterated preseason games only to skip them via panel_pred null
    # checks — pure waste. Betting edge on preseason games (starters play a
    # quarter) is near-zero and not worth Claude tokens.
    # Regular season only. Playoffs use the same 'americanfootball_nfl' key.
    sport_keys = ["americanfootball_nfl"]

    games: list = []
    for sk in sport_keys:
        r = requests.get(
            f"https://api.the-odds-api.com/v4/sports/{sk}/odds",
            params={"apiKey": ODDS_API_KEY, "regions": "us",
                    "markets": "h2h,spreads,totals", "oddsFormat": "american"},
            timeout=20,
        )
        if r.status_code != 200:
            print(f"  Odds API error for {sk}: {r.status_code}")
            continue
        games.extend(r.json() or [])
    print(f"  fetched {len(games)} NFL games across {len(sport_keys)} sport key(s)")
    return games


def fetch_team_stats():
    rows = sb_get("nfl_team_stats", {"select": "*"})
    return {r.get("team"): r for r in rows}


def fetch_nfl_contexts():
    """2026-08-09 Phase 2: pull nfl_game_context rows with model/panel
    predictions + primary_play. Keyed by (home_team, away_team) for
    lookup during struct build."""
    from datetime import timedelta as _td
    today = today_et()
    # 2026-09-06: bumped 10→12 days. Week 1 MNF at 9/15 8pm ET fell just
    # past the 10-day-minus-4h horizon on Sat morning runs. 12 days safely
    # covers Thu opener → next-week's Thursday early lookahead.
    horizon = (datetime.now(timezone.utc) + _td(days=12) - _td(hours=4)).strftime('%Y-%m-%d')
    url = (f"{SUPABASE_URL}/rest/v1/nfl_game_context"
           f"?game_date=gte.{today}&game_date=lte.{horizon}"
           f"&select=game_id,home_team,away_team,game_date,close_total,close_spread,"
           f"projected_total,projected_spread,model_pred_home_points,model_pred_away_points,"
           f"panel_pred_home_pts,panel_pred_away_pts,panel_pred_total,panel_confidence,"
           f"panel_source,panel_players_used,panel_injury_outs,"
           f"signal_confluence_net,cohort_tags,sweat_score,sweat_tier,primary_play")
    r = requests.get(url, headers=SB_READ, timeout=20)
    if r.status_code != 200: return {}
    ctxs = r.json()
    # Key by (home, away) using team abbreviations from the context
    return {(c.get('home_team'), c.get('away_team')): c for c in ctxs if c.get('home_team')}


def median(arr):
    return sorted(arr)[len(arr) // 2] if arr else None


def extract_market(game):
    spreads, totals, hmls, amls = [], [], [], []
    for bm in game.get("bookmakers", []):
        for mkt in bm.get("markets", []):
            if mkt["key"] == "spreads":
                home = next((o for o in mkt["outcomes"] if o.get("name") == game.get("home_team")), None)
                if home and home.get("point") is not None:
                    spreads.append(home["point"])
            elif mkt["key"] == "totals":
                t = (mkt.get("outcomes") or [None])[0]
                if t and t.get("point") is not None:
                    totals.append(t["point"])
            elif mkt["key"] == "h2h":
                for o in mkt.get("outcomes") or []:
                    if o.get("name") == game.get("home_team"):
                        hmls.append(o.get("price"))
                    elif o.get("name") == game.get("away_team"):
                        amls.append(o.get("price"))
    return median(spreads), median(totals), median(hmls), median(amls)


def _team(stats, name):
    if not name:
        return {}
    if name in stats:
        return stats[name]
    last = name.split()[-1]
    return next((v for k, v in stats.items() if (k or "").split()[-1] == last), {}) or {}


def _build_casual_summary(struct):
    """NFL casual summary — lean since Phase 2 (per-game picks) hasn't shipped.
    Mostly market context + season EPA snapshot until then."""
    headlines = []
    m = struct.get("market") or {}
    eh = (struct.get("efficiency") or {}).get("home") or {}
    ea = (struct.get("efficiency") or {}).get("away") or {}
    away, home = (struct.get("matchup") or " @ ").split(" @ ")[0], (struct.get("matchup") or " @ ").split(" @ ")[-1]

    headlines.append((1, "ℹ Working from season EPA only — per-game model coming Phase 2"))

    # Pass EPA gap
    for label, val_h, val_a in [
        ("pass offense", eh.get("pass_epa"), ea.get("pass_epa")),
        ("rush offense", eh.get("rush_epa"), ea.get("rush_epa")),
    ]:
        if val_h is not None and val_a is not None:
            gap = float(val_h) - float(val_a)
            if abs(gap) >= 0.10:
                team = home if gap > 0 else away
                headlines.append((6, f"✓ {team} has the edge in {label} (EPA gap {abs(gap):.2f})"))

    # Defensive sacks / INTs
    for label, side, t in [("home", eh, home), ("away", ea, away)]:
        if side.get("def_ints") and int(side["def_ints"]) >= 12:
            headlines.append((4, f"✓ {t} defense forces turnovers ({side['def_ints']} INTs)"))

    # Market snapshot
    if m.get("spread") is not None:
        headlines.append((3, f"📊 Market: {home} {'+' if m['spread']>0 else ''}{m['spread']}, total {m.get('total','N/A')}"))

    headlines.sort(key=lambda x: -x[0])
    top = [h[1] for h in headlines[:4]]

    bottom = "Phase 2 NFL game model not active — market + season EPA only"
    return {"headlines": top, "bottom_line": bottom}


def build_struct(game, stats, contexts=None):
    home, away = game.get("home_team"), game.get("away_team")
    h, a = _team(stats, home), _team(stats, away)
    spread, total, hml, aml = extract_market(game)

    def eff(t):
        return {
            "games": t.get("games"),
            "pass_epa": _f(t.get("pass_epa")),
            "rush_epa": _f(t.get("rush_epa")),
            "pass_cpoe": _f(t.get("pass_cpoe")),
            "sacks_suffered": t.get("sacks_suffered"),
            "def_sacks": t.get("def_sacks"),
            "def_ints": t.get("def_ints"),
            "fg_pct": _f(t.get("fg_pct")),
        }

    struct = {
        "matchup": f"{away} @ {home}",
        "game_id": game.get("id"),
        "commence_time": game.get("commence_time"),
        "market": {"spread": spread, "total": total, "home_ml": hml, "away_ml": aml},
        "efficiency": {"home": eff(h), "away": eff(a)},
        "meta": {
            "game_date": today_et(),
            "game_has_not_been_played": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "phase": "Phase 2 LIVE — EPA-matchup model + fantasy-aggregated Panel model",
        },
    }

    # 2026-08-09 Phase 2: merge in nfl_game_context predictions if available
    ctx = None
    if contexts:
        ctx = (contexts.get((home, away))
               or contexts.get((_short_team(home), _short_team(away)))
               or None)
    if ctx:
        struct["models"] = {
            # EPA-matchup model (per-game total using offense × opp defense)
            "matchup": {
                "projected_total": ctx.get('projected_total'),
                "projected_spread": ctx.get('projected_spread'),
                "home_pts": ctx.get('model_pred_home_points'),
                "away_pts": ctx.get('model_pred_away_points'),
            },
            # Panel model (fantasy-aggregated per-player projections)
            "panel": {
                "total": ctx.get('panel_pred_total'),
                "home_pts": ctx.get('panel_pred_home_pts'),
                "away_pts": ctx.get('panel_pred_away_pts'),
                "confidence": ctx.get('panel_confidence'),
                "source": ctx.get('panel_source'),
                "players_used": ctx.get('panel_players_used'),
                "injury_outs": ctx.get('panel_injury_outs'),
            },
        }
        struct["confluence"] = {
            "net": ctx.get('signal_confluence_net'),
            "cohort_tags": ctx.get('cohort_tags'),
        }
        struct["sweat"] = {
            "score": ctx.get('sweat_score'),
            "tier": ctx.get('sweat_tier'),
        }
        pp = ctx.get('primary_play')
        if pp:
            struct["primary_play"] = pp

        # 2026-09-07 ANTI-HALLUCINATION PRE-PARSE.
        # Before this block, Jerry got raw fields (`projected_spread: 2.38`,
        # `close_home_ml: 145`, `close_away_ml: -175`) and had to infer sign
        # conventions on his own. Result on 9/7 BAL @ IND:
        #   - "The model's pulling Baltimore ahead by 2.38 points" (WRONG —
        #     projected_spread is H+/A- so IND was actually favored by 2.38)
        #   - "money line odds (-180 Ravens, +150 Colts)" (WRONG — actual
        #     -175/+145; Jerry rounded/hallucinated)
        # Fix: pre-parse every directional value into an unambiguous English
        # string that Jerry MUST quote verbatim. No sign-convention math left
        # for the LLM. Passed as struct.pre_parsed_facts + surfaced at the
        # top of the model_context block so it's the FIRST thing Jerry sees.
        facts = {}
        # ── Money line — verbatim, no rounding. ML SIGN IS THE ONLY UNAMBIGUOUS
        # DIRECTION SIGNAL: negative = favorite (giving vig), positive = dog.
        # We derive fav/dog from ML, then attribute the spread number to the
        # correct team. Do NOT trust close_spread's sign to identify who's
        # favored — it varies by data source (some feeds store the away-team
        # line, some store the home-team line). ML is universal.
        aml = ctx.get('close_away_ml'); hml = ctx.get('close_home_ml')
        _fav_team = _dog_team = None
        _fav_ml = _dog_ml = None
        if aml is not None and hml is not None:
            aml_i, hml_i = int(aml), int(hml)
            if aml_i < 0 and hml_i > 0:
                _fav_team, _dog_team = away, home
                _fav_ml, _dog_ml = aml_i, hml_i
            elif hml_i < 0 and aml_i > 0:
                _fav_team, _dog_team = home, away
                _fav_ml, _dog_ml = hml_i, aml_i
            # If both ML same sign or both zero (rare), leave fav/dog null
            facts["moneyline_verbatim"] = f"{away} {aml_i:+d} / {home} {hml_i:+d}"
            if _fav_team:
                facts["moneyline_favorite"] = f"{_fav_team} at {_fav_ml:+d}"
                facts["moneyline_dog"]      = f"{_dog_team} at {_dog_ml:+d}"
        # ── Spread — attribute the spread MAGNITUDE to the ML-identified fav ──
        sp = ctx.get('close_spread')
        if sp is not None and _fav_team is not None:
            mag = abs(float(sp))
            facts["market_spread_verbatim"] = f"{_fav_team} {-mag:+.1f} / {_dog_team} {+mag:+.1f}"
            facts["market_favors"] = f"{_fav_team} by {mag:.1f} points"
        elif sp is not None:
            # No ML to disambiguate — report raw with WARNING
            facts["market_spread_verbatim"] = f"spread {float(sp):+.1f} (raw — ML not available to identify favorite)"
        # ── Model direction — model_pred_*_points is SOURCE OF TRUTH ──
        # projected_spread comes from a different lens in the pipeline (v3-era
        # signed number) and doesn't always match model_pred_home - model_pred_away.
        # BAL @ IND example: projected_spread=2.38 but model_pred=26.6/25.1
        # (margin 1.5). Don't feed Jerry the projected_spread number alone —
        # the derived-from-scores string is unambiguous and Jerry can quote it.
        hp = ctx.get('model_pred_home_points')
        ap = ctx.get('model_pred_away_points')
        if hp is not None and ap is not None:
            hp_f, ap_f = float(hp), float(ap)
            model_winner = home if hp_f > ap_f else away
            model_loser  = away if hp_f > ap_f else home
            margin = abs(hp_f - ap_f)
            facts["model_favors"] = (
                f"{model_winner} by {margin:.1f} points "
                f"({home} {hp_f:.1f} vs {away} {ap_f:.1f})"
            )
            # Edge vs market — how much more/less does the model favor the fav?
            if _fav_team:
                # market margin for fav (positive)
                mkt_margin = abs(float(sp)) if sp is not None else None
                # model margin for fav (positive if model agrees, negative if model likes dog)
                model_margin_for_fav = (hp_f - ap_f) if _fav_team == home else (ap_f - hp_f)
                if mkt_margin is not None:
                    edge_pts = model_margin_for_fav - mkt_margin
                    if edge_pts >= 0.5:
                        facts["edge_side"] = f"{_fav_team} — model favors them by {abs(edge_pts):.1f} MORE points than market"
                    elif edge_pts <= -0.5:
                        facts["edge_side"] = f"{_dog_team} — model has {_fav_team} winning by less than market ({abs(edge_pts):.1f} pt gap → take {_dog_team} with points)"
                    else:
                        facts["edge_side"] = f"none — model and market within {abs(edge_pts):.1f} pt"
        elif ctx.get('projected_spread') is not None:
            # No model_pred_*_points — fall back to projected_spread with EXPLICIT
            # sign-convention warning so Jerry doesn't guess.
            ps_f = float(ctx.get('projected_spread'))
            facts["model_favors_ambiguous"] = (
                f"projected_spread={ps_f:+.2f} — DO NOT interpret sign without model_pred_*_points confirmation. "
                "If you cite this, quote the raw number and say 'thin model coverage'."
            )
        # ── Total — resolve ONE canonical projection to cite; label others clearly ──
        # Panel and projected_total are DIFFERENT lenses. Jerry was double-citing
        # them as if they were one number (9/13 ARI @ LAC: "42.48 vs 46.5" AND
        # "39.95 combined score" in same read). Give him one canonical.
        pt = ctx.get('projected_total'); panel = ctx.get('panel_pred_total')
        if pt is not None or panel is not None:
            # Canonical: prefer projected_total (EPA-matchup, the primary lens).
            canonical = pt if pt is not None else panel
            canonical_source = "EPA-matchup model" if pt is not None else "Panel model"
            facts["total_canonical"] = f"{float(canonical):.2f} (per {canonical_source})"
            if pt is not None and panel is not None and abs(float(pt) - float(panel)) > 0.5:
                facts["total_secondary_lens"] = (
                    f"Panel model separately projects {float(panel):.2f}. "
                    f"If citing both, label them: EPA-matchup {float(pt):.2f} · Panel {float(panel):.2f}. "
                    "DO NOT quote them as the same projection."
                )
            # Market delta
            if ctx.get('close_total') is not None:
                delta = float(canonical) - float(ctx['close_total'])
                facts["total_market_delta"] = (
                    f"Model {float(canonical):.2f} vs market {float(ctx['close_total']):.1f} — "
                    f"{'OVER lean' if delta > 0 else 'UNDER lean'} of {abs(delta):.2f} pts"
                )
        # ── Sweat score vs pick tier disambiguation ──
        sw_score = ctx.get('sweat_score'); sw_tier = ctx.get('sweat_tier')
        pp_tier = (pp or {}).get('tier') if pp else None
        if sw_score is not None and pp_tier:
            if sw_tier != pp_tier:
                facts["tier_note"] = (
                    f"GAME sweat score is {sw_score} ({sw_tier}) but PICK tier is {pp_tier}. "
                    "These are different — sweat = game-level signal density, "
                    "pick tier = per-market conviction after juice caps. "
                    "Do NOT conflate. If citing sweat, add 'the specific pick is [pp_tier] tier'."
                )
        if facts:
            struct["pre_parsed_facts"] = facts

    struct["casual_summary"] = _build_casual_summary(struct)
    return struct


def _short_team(name):
    """Bridge full team name → abbrev if needed. Odds API uses full names,
    nfl_game_context typically stores abbrevs like BAL/PHI."""
    if not name: return None
    NAME_TO_ABBR = {
        'Arizona Cardinals':'ARI','Atlanta Falcons':'ATL','Baltimore Ravens':'BAL',
        'Buffalo Bills':'BUF','Carolina Panthers':'CAR','Chicago Bears':'CHI',
        'Cincinnati Bengals':'CIN','Cleveland Browns':'CLE','Dallas Cowboys':'DAL',
        'Denver Broncos':'DEN','Detroit Lions':'DET','Green Bay Packers':'GB',
        'Houston Texans':'HOU','Indianapolis Colts':'IND','Jacksonville Jaguars':'JAX',
        'Kansas City Chiefs':'KC','Las Vegas Raiders':'LV','Los Angeles Chargers':'LAC',
        'Los Angeles Rams':'LAR','Miami Dolphins':'MIA','Minnesota Vikings':'MIN',
        'New England Patriots':'NE','New Orleans Saints':'NO','New York Giants':'NYG',
        'New York Jets':'NYJ','Philadelphia Eagles':'PHI','Pittsburgh Steelers':'PIT',
        'San Francisco 49ers':'SF','Seattle Seahawks':'SEA','Tampa Bay Buccaneers':'TB',
        'Tennessee Titans':'TEN','Washington Commanders':'WAS',
    }
    return NAME_TO_ABBR.get(name, name)


def _build_model_lean(m_total, p_total, struct):
    """2026-08-25: replaces the prior hardcoded 'no game model active —
    market-based' string that misled Jerry into writing market-only prose
    on NFL games where Panel + EPA-matchup models were actually populated.
    Cites whichever models are present so Jerry can reason across them."""
    parts = []
    try:
        if p_total is not None:
            parts.append(f'Panel projects {float(p_total):.1f} total')
    except (TypeError, ValueError):
        pass
    try:
        if m_total is not None:
            parts.append(f'EPA-matchup {float(m_total):.1f}')
    except (TypeError, ValueError):
        pass
    conf_net = (struct.get('confluence') or {}).get('net')
    if conf_net is not None:
        parts.append(f'confluence net {conf_net}')
    if parts:
        return ' · '.join(parts)
    return 'market-based (Panel + EPA models pending for this game)'


def render_prompt(templates, struct):
    # 2026-08-09: Phase 2 confidence copy depends on whether per-game models
    # actually populated (models block present with real numbers).
    models = struct.get('models') or {}
    m_total = (models.get('matchup') or {}).get('projected_total')
    p_total = (models.get('panel') or {}).get('total')
    if m_total or p_total:
        confidence_tier = (
            f"PHASE 2 LIVE — EPA-matchup model total {m_total} · "
            f"Panel model total {p_total} · confluence {struct.get('confluence',{}).get('net')}. "
            "Reason across both models + market."
        )
    else:
        confidence_tier = "MARKET — model data not yet available for this game."
    # 2026-09-07 v3: hoist pre_parsed_facts OUT of the JSON dump and into a
    # plain-English "CONFIRMED FACTS" block at the TOP of the sport_context.
    # V2 still had a spread-attribution bug (BAL @ IND regen wrote
    # "Colts -3.5" when BAL was the ML favorite -175 → BAL -3.5). Root:
    # Jerry saw both `pre_parsed_facts` AND raw `market.spread` in the same
    # JSON dump and sometimes re-derived from the raw signed number instead
    # of consuming the pre-parsed English string. Fix: put the pre-parsed
    # facts BEFORE the JSON, in prose form, with strict "quote verbatim"
    # framing. Also strip market.spread + home_ml + away_ml from the JSON
    # dump so the only path to those numbers is via pre_parsed_facts.
    _struct_for_json = dict(struct)
    _pf = _struct_for_json.pop('pre_parsed_facts', None)
    if _pf:
        # Redact ambiguous raw fields — force Jerry through the parsed strings.
        if 'market' in _struct_for_json and isinstance(_struct_for_json['market'], dict):
            _mkt = dict(_struct_for_json['market'])
            for _k in ('spread', 'home_ml', 'away_ml'):
                _mkt.pop(_k, None)
            _struct_for_json['market'] = _mkt
    facts_block = ""
    if _pf:
        _lines = ["CONFIRMED FACTS (source of truth — quote these VERBATIM in prose, do not re-derive from other fields):"]
        for _k in ['moneyline_verbatim', 'moneyline_favorite', 'moneyline_dog',
                   'market_spread_verbatim', 'market_favors',
                   'model_favors', 'model_favors_ambiguous',
                   'edge_side',
                   'total_canonical', 'total_secondary_lens', 'total_market_delta',
                   'tier_note']:
            if _pf.get(_k):
                _lines.append(f"  - {_k}: {_pf[_k]}")
        facts_block = "\n".join(_lines) + "\n\n"
    context_block = (
        facts_block
        + "NFL GAME CONTEXT (analytical — do not search for scores; when raw fields conflict with CONFIRMED FACTS above, the facts win):\n"
        + json.dumps(_struct_for_json, indent=2, default=str)
    )
    m = struct["market"]
    away, home = struct["matchup"].split(" @ ")
    return (
        templates["wrapper"]
        .replace("{today_et}", now_et_human())
        .replace("{away_team}", away)
        .replace("{home_team}", home)
        .replace("{commence_time_et}", struct.get("commence_time") or "soon")
        .replace("{sport}", "NFL")
        .replace("{sweat_score}", "—")
        .replace("{sweat_tier_label}", "")
        .replace("{spread_str}", str(m.get("spread") or "N/A"))
        .replace("{total_str}", str(m.get("total") or "N/A"))
        .replace("{model_lean}", _build_model_lean(m_total, p_total, struct))
        .replace("{confidence_tier}", confidence_tier)
        .replace("{tournament_floor_note}", "")
        .replace("{full_score_context}", "")
        .replace("{model_context}", "")
        .replace("{sport_context}", context_block)
        .replace("{sport_rules}", templates["rules"])
        .replace("{universal_rules}", templates["universal"])
        .replace("{data_quality_note}", "")
    )


def call_claude(prompt):
    if not ANTHROPIC_API_KEY:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
            json={"model": MODEL, "max_tokens": 800, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        data = r.json()
        if r.status_code != 200:
            print(f"  ⚠️ claude {r.status_code}: {str(data)[:300]}")
            return None
        return "".join(b.get("text", "") for b in (data.get("content") or []) if b.get("type") == "text").strip() or None
    except Exception as e:
        print(f"  ⚠️ claude failed: {e}")
        return None


def parse_nfl_synthesis(raw: str) -> dict:
    """Parse NFL Jerry LLM output (2026-08-06). Mirrors MLB's parse_synthesis
    from generate_jerry_synthesis.py — same SHORT/LONG/CALL contract now
    that seed_nfl_game_read_prompt has been updated.

    Returns:
      {
        'short_read': str,
        'long_read': str,
        'call_market': str | None,      # ml/spread/total/pass
        'call_side': str | None,        # HOME/AWAY/OVER/UNDER
        'call_line': float | None,
        'call_text': str | None,
        'conviction': int | None,       # 0-100
      }

    Falls back gracefully on malformed output — missing CALL block just
    means we store prose only (like pre-Phase 2 behavior).
    """
    import re as _re
    def _section(name):
        m = _re.search(rf"---{name}---\s*(.*?)(?=---[A-Z]+---|$)", raw, _re.S)
        return m.group(1).strip() if m else None

    short = _section("SHORT") or ""
    long_ = _section("LONG") or ""
    call_block = _section("CALL") or ""

    # Strip markdown for robust field extraction (Jerry sometimes writes **MARKET:** **ml**)
    call_block = _re.sub(r"\*+", "", call_block)
    call_block = _re.sub(r"_+", "", call_block)

    def _field(field):
        m = _re.search(rf"\**{field}\**\s*:\s*(.+?)(?=\n\**[A-Z_]+\**\s*:|$)",
                        call_block, _re.S)
        if not m: return None
        val = m.group(1).strip()
        val = _re.sub(r"^[*_\s]+|[*_\s]+$", "", val)
        return val or None

    market = (_field("MARKET") or "").lower() or None
    side = (_field("SIDE") or "").upper() or None
    if side == "NULL": side = None
    line_raw = _field("LINE")
    try:
        line = float(line_raw) if line_raw and line_raw.lower() != "null" else None
    except ValueError:
        line = None
    call_text = _field("CALL_TEXT")
    conv_raw = _field("CONVICTION")
    try:
        conviction = max(0, min(100, int(_re.sub(r"\D", "", conv_raw or "")))) if conv_raw else None
    except ValueError:
        conviction = None

    _VALID_MARKETS = {'ml', 'spread', 'rl', 'total', 'prop', 'lean', 'pass', None}
    if market not in _VALID_MARKETS:
        print(f"  ⚠ parser invalid NFL call_market {market!r} — nulling")
        market = None; side = None
    _VALID_SIDES = {'HOME', 'AWAY', 'OVER', 'UNDER', None}
    if side not in _VALID_SIDES:
        print(f"  ⚠ parser invalid NFL call_side {side!r} — nulling")
        side = None

    return {
        "short_read": short,
        "long_read": long_,
        "call_market": market,
        "call_side": side,
        "call_line": line,
        "call_text": call_text,
        "conviction": conviction,
    }


_NFL_VALID_MARKETS = {'ml', 'rl', 'spread', 'total'}


def defer_call_to_ensemble_nfl(parsed: dict, struct: dict) -> dict:
    """Force NFL jerry_read.call_* to match primary_play at write time.

    2026-09-10 PERMANENT FIX for the chronic pick-vs-narrative mismatch bug.
    User pain (repeated across weeks): BAL@IND primary_play=BAL ML but
    Jerry narrative called OVER; BUF@HOU primary_play=BUF ML but Jerry
    called UNDER. Same class of bug MLB had in Aug — MLB fixed with
    defer_call_to_ensemble (generate_jerry_synthesis.py:609). NFL never
    got the same treatment so every soft-signal game shipped two
    different picks on the same card. This function ports the pattern.

    Rules:
    - Ensemble primary_play is the SOURCE OF TRUTH for the pick.
    - LLM's prose (short_read/long_read/conviction) is preserved as-is.
    - Only call_market/call_side/call_line/call_text are overwritten so
      the badge on the game card and the pick chip in the narrative
      always show the same thing.
    - When ensemble tier=COVERAGE/PASS/SKIP, force call_market='pass' +
      rewrite short_read to explain the pass ("Engine passed — no
      publishable edge") so we never surface a soft pick as a hero play.
    """
    pp = struct.get('primary_play') if isinstance(struct, dict) else None
    if not isinstance(pp, dict): return parsed
    market = str(pp.get('type') or '').lower()
    side = pp.get('side')
    label = pp.get('label')
    conviction = pp.get('conviction')
    line = pp.get('line')
    tier = str(pp.get('tier') or '').upper()
    # 2026-09-12 UNBLOCK: NFL Week 2 slate had 16 of 27 games force-passed
    # because ensemble is still calibrating and tiering games to COVERAGE.
    # Original rule was ALL COVERAGE → force PASS. New rule: COVERAGE with
    # conviction >= 60 ships as a LEAN pick (visible to users) instead of
    # PASS. COVERAGE conv < 60 + PASS/SKIP tiers keep the pass path — those
    # are genuinely soft. This surfaces the ensemble's best guess when it
    # exists but doesn't manufacture picks on games with no signal at all.
    _COVERAGE_LEAN_FLOOR = 60
    _cov_promotable = (tier == 'COVERAGE'
                       and isinstance(conviction, (int, float))
                       and int(conviction) >= _COVERAGE_LEAN_FLOOR
                       and market in _NFL_VALID_MARKETS and side and label)
    if _cov_promotable:
        # Downgrade tier for display but keep the pick — treat as LEAN
        tier = 'LEAN'
    # Engine PASS path — LLM prose can stay, but badge shows PASS + engine reason
    if tier in ('COVERAGE', 'PASS', 'SKIP') or market not in _NFL_VALID_MARKETS or not side or not label:
        engine_sub = str(pp.get('sub') or '').strip()
        new_short = (f'Engine passed — no publishable edge on this game. '
                     f'{engine_sub}' if engine_sub else 'Engine passed — no publishable edge on this game.')
        parsed['call_market'] = 'pass'
        parsed['call_side'] = None
        parsed['call_line'] = None
        parsed['call_text'] = 'Pass'
        parsed['conviction'] = 0
        # Preserve original short_read if it's genuinely analytical (long enough),
        # only replace when it's empty/short
        orig_short = (parsed.get('short_read') or '').strip()
        if len(orig_short) < 60:
            parsed['short_read'] = new_short[:2000]
        return parsed
    # Real pick — force the badge fields to match ensemble
    parsed['call_market'] = market
    parsed['call_side'] = str(side).upper()
    parsed['call_line'] = line
    parsed['call_text'] = label
    if isinstance(conviction, (int, float)):
        parsed['conviction'] = max(0, min(100, int(conviction)))
    return parsed


def upsert_jerry_read_nfl(game, struct, parsed, narrative):
    """Write structured NFL Jerry read to jerry_reads table (2026-08-06 Phase 2).
    Uses (sport, game_id, game_date) unique key. This is what the sweat card
    queries for game-side picks — parity with MLB path."""
    # 2026-09-10: enforce ensemble alignment BEFORE writing so badge + prose agree.
    parsed = defer_call_to_ensemble_nfl(parsed, struct)
    game_id = game.get('id')  # Odds API game id
    # commence_time to game_date ET
    ct = game.get('commence_time', '')[:10] or today_et()
    payload = {
        'sport': 'NFL',
        'game_id': game_id,
        'game_date': ct,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'prompt_version': 'nfl_game_read_v2_2026-08-06',
        # 2026-09-12 DEPTH: Was only serializing {source, matchup} — 2 keys
        # vs MLB's 28. Andy complaint "NFL reads should be as deep as MLB".
        # Now serializes the full primary_play (has LR shadows, ensemble
        # sources, per-market breakdown) plus struct signals so the app can
        # render richer sections. App renders whatever keys exist and skips
        # missing ones, so this is additive/safe — doesn't break any
        # existing UI path.
        'input_snapshot': {
            'source': 'generate_nfl_game_reads',
            'matchup': struct.get('matchup'),
            'primary_play': struct.get('primary_play'),
            'align_status': struct.get('align_status'),
            'signals': struct.get('signals'),
            'team_snapshot': {
                'away': {k: v for k, v in (struct.get('away') or {}).items()
                         if v is not None and not k.startswith('_')},
                'home': {k: v for k, v in (struct.get('home') or {}).items()
                         if v is not None and not k.startswith('_')},
            } if isinstance(struct.get('away'), dict) or isinstance(struct.get('home'), dict) else None,
        },
        'short_read': parsed.get('short_read') or narrative[:500],
        'long_read': parsed.get('long_read') or narrative,
        'call_text': parsed.get('call_text'),
        'call_market': parsed.get('call_market'),
        'call_side': parsed.get('call_side'),
        'call_line': parsed.get('call_line'),
        'call_odds_est': None,
        'conviction': parsed.get('conviction') or 0,
    }
    r = requests.post(
        f'{SUPABASE_URL}/rest/v1/jerry_reads?on_conflict=sport,game_id,game_date',
        headers={**SB_WRITE, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
        json=payload, timeout=15,
    )
    if r.status_code not in (200, 201, 204):
        print(f"  ⚠️ jerry_reads upsert failed {r.status_code}: {r.text[:200]}")
        return False
    return True


def upsert_read(game, struct, narrative, parsed=None):
    # 2026-09-02: Thu-lock cache key. Was per-day (game_read_<id>_<YYYY-MM-DD>)
    # which regenerated Jerry reads daily even on stable NFL slates. Now
    # per-week — key ties to the Thursday start of the NFL week. Subsequent
    # daily cron runs check the same key + skip. Fresh Thursday morning
    # generates the whole week; injury-triggered regen uses --force to
    # bust the specific game's cache.
    week_key = nfl_week_start_thu()
    key = f"game_read_{game.get('id')}_nfl_week_{week_key}"
    lock_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "game_id": key,
        "cache_key": key,
        "sport": "NFL",  # 2026-08-25 case fix — matches sport_registry convention
        "narrative": narrative,
        "data": json.dumps({**struct, "_locked_at": lock_iso, "_lock_week_thu": week_key}, default=str),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    # 2026-08-23: fixed on_conflict from cache_key to game_id,sport (matches
    # actual UNIQUE constraint on jerry_cache). Every re-run was silently
    # 409-ing after the first insert per game.
    r = requests.post(f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=game_id,sport", headers=SB_WRITE, json=payload, timeout=15)
    ok = r.status_code in (200, 201, 204)
    if not ok:
        print(f"  ⚠️ jerry_cache upsert failed {r.status_code}: {r.text[:300]}")

    # DUAL-WRITE (2026-08-06 Phase 2): also write structured pick to
    # jerry_reads so sweat card can rank it alongside MLB Jerry picks
    # by conviction. This is the missing piece that had NFL sitting at
    # "prose-only, no structured selection" until now.
    if parsed and parsed.get('short_read'):
        upsert_jerry_read_nfl(game, struct, parsed, narrative or '')

    return ok


def run():
    force = "--force" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        try:
            limit = int(sys.argv[sys.argv.index("--limit") + 1])
        except Exception:
            limit = None
    # 2026-09-02: --game-id filter for injury-triggered targeted regen.
    # nfl_injury_regen_check.py calls this with a specific game_id when
    # QB1 status changes post-Thu-lock. --force required alongside to
    # bust the existing week-lock for THAT game only.
    game_id_filter = None
    if "--game-id" in sys.argv:
        try:
            game_id_filter = sys.argv[sys.argv.index("--game-id") + 1]
        except Exception:
            game_id_filter = None

    print(f"=== NFL game reads {today_et()} ===")
    if game_id_filter:
        print(f"  --game-id filter: {game_id_filter} (targeted regen mode)")
    templates = load_templates()
    if not templates:
        sys.exit(1)

    games = fetch_odds_games()
    if not games:
        print("  No NFL games on the slate (offseason / no odds available).")
        return
    # Filter to next 10 days only (regular season scope).
    # 2026-09-06: bumped 8→10 to reach Week 1 MNF (game 9/15 8pm ET was
    # 9+ days out on Sat morning runs — the fetch_nfl_contexts horizon
    # already extends to 12 days, this cutoff matched them).
    cutoff = datetime.now(timezone.utc) + timedelta(days=10)
    games = [g for g in games if g.get("commence_time") and g["commence_time"] <= cutoff.isoformat()]
    # Apply game_id filter for targeted regen
    if game_id_filter:
        games = [g for g in games if g.get("id") == game_id_filter]
        if not games:
            print(f"  ⚠ game_id {game_id_filter} not in fetched odds list")
            return
    if not games:
        print("  No NFL games in the next 8 days.")
        return

    stats = fetch_team_stats()
    print(f"  {len(games)} game(s) | {len(stats)} team stat rows")

    # 2026-08-09 Phase 2: pull per-game context (model + Panel predictions)
    contexts = fetch_nfl_contexts()
    print(f"  Phase 2 contexts loaded: {len(contexts)}")

    # 2026-09-02: Thu-lock — cache key ties to NFL week's Thursday start.
    # Subsequent-day runs check same key, find it, skip. Only Thursday
    # morning cron generates fresh (or manual --force for injury regen).
    week_key = nfl_week_start_thu()
    print(f"  Thu-lock week: {week_key}")

    done = 0
    for g in games:
        struct = build_struct(g, stats, contexts=contexts)
        away, home = struct["matchup"].split(" @ ")
        key = f"game_read_{g.get('id')}_nfl_week_{week_key}"
        if not force:
            if sb_get("jerry_cache", {"cache_key": f"eq.{key}", "select": "cache_key"}):
                print(f"  • {away} @ {home}: locked (Thu {week_key}), skip")
                continue
        prompt = render_prompt(templates, struct)
        narrative = call_claude(prompt)
        if not narrative:
            print(f"  • {away} @ {home}: no narrative — struct only")
        parsed = parse_nfl_synthesis(narrative) if narrative else {}

        # 2026-08-09: NFL number-hallucination hard-enforce (mirrors MLB
        # Jerry). Only checks numbers (sport-universal), not pitcher names.
        # If numbers hallucinated, retry once with corrective prompt; if
        # still bad, cap conviction to LEAN (55).
        if narrative and parsed.get('short_read') and parsed.get('long_read'):
            try:
                from validate_jerry_read import validate as _validate, build_corrective_prompt
                num_report = _validate(parsed.get('short_read'), parsed.get('long_read'), struct)
                if not num_report['is_valid']:
                    print(f"  ⚠ NFL num hallucination: {num_report.get('hallucinated_numbers',[])[:3]} — retry")
                    corrective = build_corrective_prompt(prompt, num_report, {'suspects': []})
                    narrative2 = call_claude(corrective)
                    if narrative2:
                        parsed2 = parse_nfl_synthesis(narrative2)
                        if parsed2.get('short_read') and parsed2.get('long_read'):
                            num2 = _validate(parsed2.get('short_read'), parsed2.get('long_read'), struct)
                            narrative = narrative2
                            parsed = parsed2
                            if num2['is_valid']:
                                print(f"  ✓ retry cleaned numbers")
                            elif (parsed.get('conviction') or 0) > 55:
                                parsed['conviction'] = 55
                                print(f"  🔒 conviction capped→55 (LEAN) due to unverified numbers: {num2.get('hallucinated_numbers',[])[:3]}")
            except ImportError:
                pass

        if upsert_read(g, struct, narrative or "", parsed=parsed):
            call_str = ''
            if parsed.get('call_market'):
                call_str = f" · {parsed.get('call_text') or parsed['call_market']} ({parsed.get('conviction') or '-'})"
            print(f"  ✓ {away} @ {home}{call_str}")
            done += 1
        if limit and done >= limit:
            break

    print(f"=== wrote {done} NFL game reads ===")


if __name__ == "__main__":
    run()
