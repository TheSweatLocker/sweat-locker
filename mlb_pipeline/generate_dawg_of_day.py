"""
Dawg of the Day — single underdog ML pick per slate, model-identified.

A Dawg = a market underdog that our model likes more than the market does.
We identify games where sign(spread_delta) == sign(close_spread) —
meaning the model leans toward the side that market has as the underdog.

Stored in daily_dawg (one row per date). All users read the same record.

Table schema:
  CREATE TABLE daily_dawg (
    game_date DATE PRIMARY KEY,
    team TEXT NOT NULL,
    matchup TEXT NOT NULL,
    game_id TEXT NOT NULL,
    spread_delta NUMERIC NOT NULL,
    close_spread NUMERIC,
    conviction INT NOT NULL,
    tier TEXT NOT NULL,
    signals JSONB NOT NULL,
    narrative TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
  );
"""
import os
import sys
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
ODDS_API_KEY = os.environ.get('ODDS_API_KEY')

HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'resolution=merge-duplicates,return=minimal',
}

MIN_DELTA = 1.5  # legacy threshold — retained for backward refs but not used in new gate

# Jerry-first selection (2026-06-01 redesign).
#
# Previous selector was 17-22 (43.6%) — below market-implied for +120 dogs
# (~45% breakeven). Root cause: relied on v3 spread (mediocre) and v4 mastery
# unlock (degraded per project_may17_xgboost_degradation). Selected "least
# bad dog" not "actually strong dog."
#
# New gate: Jerry must see the underdog winning by JERRY_DOG_MAGNITUDE_GATE
# AND at least one co-sign (v3 same direction OR confluence on dog side
# OR favorite-pitcher anti-mastery vs dog team). 5/31 audit found pure
# Jerry contrarian = 3-0 WITH co-sign, 0-1 LONELY — this gate is the
# winning cohort, formalized.
#
# Price gate keeps Dawg in the "real dog" zone: market wants +100 to +250.
# Lottery tickets above +250 historically lose more than the price implies
# (books know more than the model does at extreme prices). Below +100 isn't
# really a dog.
JERRY_DOG_MAGNITUDE_GATE = 1.0   # Jerry must see dog winning by >=this on spread
MIN_ML_PRICE = 100               # any actual dog (existing)
MAX_ML_PRICE = 250               # below the "books know something" zone


def today_et():
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    return et.strftime('%Y-%m-%d')


def _f(v):
    try: return float(v)
    except: return None


def fetch_todays_games():
    gd = today_et()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context?game_date=eq.{gd}&select=*",
        headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
        timeout=20
    )
    return r.json() if r.status_code == 200 else []


def fetch_ml_odds_map():
    """Fetch PRE-GAME MLB moneyline odds. Returns {(home,away): {home_ml,away_ml,commence_time}}.

    PRE-GAME ONLY (added 2026-05-28): without a time filter the Odds API
    returns games up to 8 days out — including IN-PROGRESS games where
    the h2h price reflects live in-game state, not the pre-game line we
    actually used for selection. 5/28 trigger: DET @ LAA was in progress
    with DET losing, Odds API returned DET ML +400 (live, post-deficit
    swing) instead of the pre-game DET -180 / LAA +160. Picker selected
    DET as +400 home dog with model delta +0.6 — completely fake edge.
    Filter to commenceTimeFrom = now and skip any game already started.
    """
    ml_map = {}
    if not ODDS_API_KEY:
        return ml_map
    try:
        now_utc = datetime.now(timezone.utc)
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "h2h",
                "oddsFormat": "american",
                # Only games starting from now forward — drops live in-progress lines.
                "commenceTimeFrom": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            timeout=15
        )
        if r.status_code != 200:
            return ml_map
        skipped_live = 0
        for g in r.json():
            home = g.get("home_team")
            away = g.get("away_team")
            commence = g.get("commence_time")
            if not home or not away:
                continue
            # Defensive double-check: skip anything whose commence_time is in the past.
            if commence:
                try:
                    ct = datetime.fromisoformat(commence.replace("Z", "+00:00"))
                    if ct < now_utc:
                        skipped_live += 1
                        continue
                except Exception:
                    pass
            for bm in g.get("bookmakers", []):
                for mkt in bm.get("markets", []):
                    if mkt.get("key") != "h2h":
                        continue
                    home_ml = None
                    away_ml = None
                    for o in mkt.get("outcomes", []):
                        if o.get("name") == home:
                            home_ml = o.get("price")
                        elif o.get("name") == away:
                            away_ml = o.get("price")
                    if home_ml and away_ml:
                        ml_map[(home, away)] = {
                            "home_ml": home_ml, "away_ml": away_ml,
                            "commence_time": commence,
                        }
                        break
                if (home, away) in ml_map:
                    break
        if skipped_live:
            print(f"  ⏰ Skipped {skipped_live} in-progress/past games (live odds, not pre-game)")
    except Exception as e:
        print(f"  ⚠️ ML odds fetch failed: {e}")
    return ml_map


def _jerry_qualifies_dog(g, dog_side, magnitude_gate=JERRY_DOG_MAGNITUDE_GATE):
    """Returns (eligible_bool, jerry_signed_spread, cosign_reasons).

    Jerry-first eligibility check for the Dawg selector.

    HARD GATE: Jerry must see the dog winning by >= magnitude_gate.
    SOFT BOOST: co-signs add to conviction downstream but aren't required.

    Why not require co-sign as hard gate: 5/31 audit replay showed
    CHC/STL (Jerry +1.2, no co-sign) won outright — too aggressive a
    co-sign requirement misses real wins. The MIN_PUBLISH_CONVICTION 65
    floor + the Jerry-magnitude conviction bump together prevent forcing
    weak Jerry-only picks (lonely Jerry at +1.0 magnitude → ~46 conviction,
    below publish floor → no DAWG).

    Re-evaluate after ~2 weeks of data: if lonely Jerry wins as much as
    co-signed, the MIN_PUBLISH_CONVICTION can drop; if it loses, restore
    the co-sign hard gate.

    Args:
        g: mlb_game_context row
        dog_side: 'home' or 'away' — which team is the ML dog
        magnitude_gate: minimum |jerry_spread| in dog's direction

    Returns:
        (eligible, jerry_spread, cosigns)
    """
    jerry_spread = _f(g.get('jerry_pred_spread'))
    if jerry_spread is None:
        return False, None, []

    # Jerry sign convention: POSITIVE = home favored.
    # dog_side wants Jerry pointing AT the dog.
    jerry_points_at_dog = (
        (dog_side == 'home' and jerry_spread > 0) or
        (dog_side == 'away' and jerry_spread < 0)
    )
    if not jerry_points_at_dog:
        return False, jerry_spread, []

    if abs(jerry_spread) < magnitude_gate:
        return False, jerry_spread, []

    # Collect co-sign list — used by downstream signal/conviction logic.
    # Not used as a hard eligibility gate; magnitude alone qualifies.
    cosigns = []

    # Co-sign 1: v3 spread direction agrees with Jerry
    v3_spread = _f(g.get('projected_spread'))
    if v3_spread is not None:
        v3_points_at_dog = (
            (dog_side == 'home' and v3_spread > 0) or
            (dog_side == 'away' and v3_spread < 0)
        )
        if v3_points_at_dog and abs(v3_spread) >= 0.3:
            cosigns.append(f'v3 agrees (spread {v3_spread:+.2f})')

    # Co-sign 2: confluence net points at dog's side (per signal_confluence_breakdown)
    breakdown = g.get('signal_confluence_breakdown') or {}
    if isinstance(breakdown, str):
        try:
            import json as _json
            breakdown = _json.loads(breakdown)
        except Exception:
            breakdown = {}
    if isinstance(breakdown, dict):
        h_count = sum(1 for v in breakdown.values() if v == 'home')
        a_count = sum(1 for v in breakdown.values() if v == 'away')
        if dog_side == 'home' and (h_count - a_count) >= 2:
            cosigns.append(f'confluence +{h_count - a_count} on home')
        elif dog_side == 'away' and (a_count - h_count) >= 2:
            cosigns.append(f'confluence +{a_count - h_count} on away')

    # Co-sign 3: the favorite's starter has anti-mastery vs the dog's team.
    # When the favorite's pitcher historically gets tagged by the dog team
    # (career ERA >=5.5 on >=15 IP), that's a fundamental matchup signal
    # that the market+v3 often underweight.
    fav_prefix = 'away' if dog_side == 'home' else 'home'
    vt_era = _f(g.get(f'{fav_prefix}_pitcher_vs_team_era'))
    vt_ip = _f(g.get(f'{fav_prefix}_pitcher_vs_team_ip'))
    if vt_era is not None and vt_ip is not None and vt_ip >= 15 and vt_era >= 5.5:
        cosigns.append(f'favorite pitcher anti-mastery ({vt_era:.2f} ERA on {vt_ip:.1f} IP)')

    # Eligible on magnitude alone; co-signs surface for conviction boost downstream.
    return True, jerry_spread, cosigns


def score_dawg(g, diag=None, ml_map=None):
    """Evaluate a game for Dawg candidacy. Returns dict or None if not a Dawg.

    Source of truth: MONEYLINE ODDS (not close_spread sign).
    The ML dog is whichever team has ML >= +100. Run line spread sign in our
    storage has been unreliable, and run line ≠ ML status anyway (a team can
    be -1.5 RL but -110 ML, or +1.5 RL but ML favorite).

    Metric: dog_edge = model's projected differential for the dog + 1.5
            (MLB run line is always 1.5). Positive = model thinks the dog
            covers the run line by more than market expects.
    """
    matchup_label = f"{g.get('away_team')} @ {g.get('home_team')}"
    ps = _f(g.get('projected_spread'))  # positive = home wins by X

    if ps is None:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: no projected_spread")
        return None

    # Pitcher xERA gate — RELAXED 2026-04-25:
    # Originally rejected ANY game with one nulled pitcher because projected_spread
    # falls back to team R/G and creates artifact deltas. Now we allow the game IF
    # signal_confluence_net >= 2 (STRONG+) provides independent multi-signal evidence
    # that doesn't depend on projected_spread magnitude.
    # Reject only when BOTH pitchers null AND no confluence support.
    home_xera = _f(g.get('home_sp_xera'))
    away_xera = _f(g.get('away_sp_xera'))
    confluence_net_raw = g.get('signal_confluence_net')
    try:
        confluence_net = int(confluence_net_raw) if confluence_net_raw is not None else 0
    except (TypeError, ValueError):
        confluence_net = 0
    if home_xera is None and away_xera is None:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: both pitchers missing xERA — no projection")
        return None
    if (home_xera is None or away_xera is None) and confluence_net < 2:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: missing pitcher xERA AND no confluence support (net {confluence_net:+d})")
        return None

    # ML odds REQUIRED — no ML odds = no Dawg eligibility (we can't verify dog status)
    if not ml_map:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: no ML map loaded")
        return None
    ml_entry = ml_map.get((g.get('home_team'), g.get('away_team')))
    if not ml_entry:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: ML odds not found for this matchup")
        return None
    home_ml = ml_entry.get('home_ml')
    away_ml = ml_entry.get('away_ml')
    if home_ml is None or away_ml is None:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: incomplete ML odds (home={home_ml}, away={away_ml})")
        return None

    # Identify dog by ML — whichever team is plus money. If both negative
    # (rare pick'em with juice), skip — no clear dog.
    if home_ml >= 100 and away_ml < 0:
        is_home_dawg = True
        team_ml = home_ml
    elif away_ml >= 100 and home_ml < 0:
        is_home_dawg = False
        team_ml = away_ml
    else:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: no ML dog (home {home_ml:+d} / away {away_ml:+d})")
        return None

    team = g.get('home_team') if is_home_dawg else g.get('away_team')
    opp_team = g.get('away_team') if is_home_dawg else g.get('home_team')

    # Price gate (2026-06-01 redesign). Lottery tickets above +250 lose
    # more than the price implies — books know more than the model at
    # extreme prices. Below +100 isn't really a dog (already gated above).
    if team_ml > MAX_ML_PRICE:
        if diag is not None:
            diag.append(f"  ✗ {matchup_label}: {team.split()[-1]} ML {team_ml:+d} above MAX_ML_PRICE +{MAX_ML_PRICE} (lottery zone)")
        return None

    # Jerry-first eligibility gate (2026-06-01 redesign). Replaces the
    # v3 MIN_EDGE 1.3 path. See JERRY_DOG_MAGNITUDE_GATE comment at top.
    dog_side = 'home' if is_home_dawg else 'away'
    jerry_eligible, jerry_spread_val, jerry_cosigns = _jerry_qualifies_dog(g, dog_side)
    if not jerry_eligible:
        if diag is not None:
            if jerry_spread_val is None:
                diag.append(f"  ✗ {matchup_label}: Jerry not populated on this row")
            else:
                # Either Jerry doesn't point at the dog OR magnitude < gate OR no co-sign
                jerry_at_dog = (
                    (dog_side == 'home' and jerry_spread_val > 0) or
                    (dog_side == 'away' and jerry_spread_val < 0)
                )
                if not jerry_at_dog:
                    diag.append(f"  ✗ {matchup_label}: Jerry disagrees with dog direction (jerry_spread {jerry_spread_val:+.2f})")
                else:  # magnitude below gate
                    diag.append(f"  ✗ {matchup_label}: Jerry magnitude {abs(jerry_spread_val):.2f} < gate {JERRY_DOG_MAGNITUDE_GATE} (weak Jerry lean)")
        return None

    # Compute dog's projected differential: positive = dog wins
    dog_differential = ps if is_home_dawg else -ps

    # MLB run line is always 1.5. dog_edge = how much the dog beats the +1.5 cover line.
    # If model says dog wins by 0.5 (dog_diff=+0.5), dog_edge = 0.5 + 1.5 = 2.0
    # If model says dog loses by 0.8 (dog_diff=-0.8), dog_edge = -0.8 + 1.5 = 0.7
    dog_edge = dog_differential + 1.5

    # v4 mastery-aware edge (updated 2026-05-21): when v4 disagrees with
    # v3 in the dog's favor, USE v4's edge directly for the gate (not just
    # a relaxed threshold). This surfaces mastery-driven dawgs that v3
    # alone would mis-classify. Backed by autofade_dog_high_conv cohort
    # (58-65% hit rate lifetime) and the 5/19 TOR vs NYY trigger case:
    # v3 said NYY by 0.97 (TOR edge 0.53), v4 said TOR by 4.85
    # (edge 6.35) — now TOR surfaces as candidate via v4 edge directly.
    v4_ps = _f(g.get('model_pred_spread'))
    v4_disagrees_for_dog = False
    v4_dog_edge = None
    if v4_ps is not None:
        v4_dog_diff = v4_ps if is_home_dawg else -v4_ps
        v4_dog_edge = v4_dog_diff + 1.5
        # v4 must (a) point at dog winning, (b) disagree with v3 by ≥2.0
        # in the dog's direction
        if v4_dog_diff > dog_differential + 2.0 and v4_dog_diff > 0:
            v4_disagrees_for_dog = True

    # Confluence cross-check on v4 unlock (added 2026-05-28).
    # v4 direction accuracy has dropped to ~53% per the 5/17 audit
    # ([[project_may17_xgboost_degradation]]), so trusting v4 alone when
    # confluence strongly disagrees is unreliable. 5/28 trigger: v4 said
    # Twins win by 3.4 vs CWS, but confluence net was +5 ALL on home
    # (recency / hand-split / L14 heat / H2H both directions). v4 was
    # making noise. Require confluence to AT LEAST not meaningfully
    # disagree (|net| < 2 or net agrees with the dog side) before trusting
    # v4's unlock. Otherwise v4 is overridden and v3's threshold governs.
    if v4_disagrees_for_dog:
        confluence_for_dog = (
            (is_home_dawg and confluence_net > 0) or
            (not is_home_dawg and confluence_net < 0)
        )
        if not confluence_for_dog and abs(confluence_net) >= 2:
            if diag is not None:
                diag.append(
                    f"  ⚠ {matchup_label}: v4 unlock REJECTED — v4 says dog "
                    f"wins by {v4_dog_diff:+.1f} but confluence net {confluence_net:+d} "
                    f"disagrees (v4 unreliable when confluence diverges)"
                )
            v4_disagrees_for_dog = False

    # MIN_EDGE 1.3 gate removed (2026-06-01 redesign). Jerry-first
    # eligibility above is now the primary gate — v3 dog_edge is used only
    # for the legacy conviction math + display. Surface effective_dog_edge
    # so the existing display logic keeps working.
    effective_dog_edge = v4_dog_edge if v4_disagrees_for_dog else dog_edge

    # close_spread for display only — may be wrong sign but we'll show it
    cs = _f(g.get('close_spread'))
    if cs is None:
        cs = _f(g.get('open_spread'))
    sd = _f(g.get('spread_delta')) or 0

    # Team stats — pick the Dawg's side data
    prefix = 'home' if is_home_dawg else 'away'
    opp_prefix = 'away' if is_home_dawg else 'home'

    wrc_vs_hand = _f(g.get(f'{prefix}_wrc_vs_opp_hand'))
    wrc_season = _f(g.get(f'{prefix}_wrc_plus')) or 100
    team_wrc = wrc_vs_hand if wrc_vs_hand is not None else wrc_season
    bullpen = _f(g.get(f'{prefix}_bullpen_era'))
    opp_bullpen = _f(g.get(f'{opp_prefix}_bullpen_era'))
    starter = g.get(f'{prefix}_pitcher')
    opp_starter = g.get(f'{opp_prefix}_pitcher')
    xera = _f(g.get(f'{prefix}_sp_xera'))
    opp_xera = _f(g.get(f'{opp_prefix}_sp_xera'))
    opp_l3_era = _f(g.get(f'{opp_prefix}_pitcher_last_3_era'))
    l3_era = _f(g.get(f'{prefix}_pitcher_last_3_era'))

    signals = {}
    conviction = 40  # base for being a model-identified dawg

    # Jerry signal (2026-06-01) — Jerry already qualified the dog earlier.
    # Magnitude drives the base bump; co-signs add on top so co-signed Jerry
    # picks naturally rank higher in candidate selection.
    jerry_magnitude = abs(jerry_spread_val) if jerry_spread_val is not None else 0
    jerry_bump = min(20, int(jerry_magnitude * 5))  # 1.0 → 5pt, 4.0 → 20pt cap
    conviction += jerry_bump
    if jerry_cosigns:
        # +6 per co-sign, cap at +12 (2 co-signs is the realistic max)
        cosign_bump = min(12, len(jerry_cosigns) * 6)
        conviction += cosign_bump
        cosign_str = ' • '.join(jerry_cosigns)
        signals['jerry'] = (
            f"Jerry sees {team.split()[-1]} winning by {jerry_magnitude:.1f} runs "
            f"+ {len(jerry_cosigns)} co-sign{'s' if len(jerry_cosigns) > 1 else ''}: {cosign_str}"
        )
    else:
        # Lonely Jerry — flagged but not blocked. Conviction stays lower so
        # the publish-floor naturally filters weak Jerry-only picks.
        signals['jerry'] = (
            f"Jerry sees {team.split()[-1]} winning by {jerry_magnitude:.1f} runs "
            f"(lonely — no v3/confluence/mastery co-sign)"
        )

    # Dog edge — how much better the dog is per model vs market's run line
    edge_bump = min(35, int(dog_edge * 8))
    conviction += edge_bump
    dog_fate = "winning outright" if dog_differential > 0 else f"losing by only {abs(dog_differential):.1f}"
    signals['model_view'] = f"{team.split()[-1]} ML {team_ml:+d} — model sees them {dog_fate} ({dog_edge:+.1f} runs vs +1.5 RL)"

    # v4 mastery-aware conviction bump (2026-05-19)
    if v4_disagrees_for_dog:
        # v4_dog_edge is meaningfully bigger than v3 — add proportional bump
        # capped at +15 so small-sample mastery doesn't dominate. The actual
        # v4 number drives a transparent signal note so user sees the basis.
        v4_bump = min(15, int((v4_dog_edge - dog_edge) * 3))
        conviction += v4_bump
        signals['v4_mastery'] = (
            f"v4 model spread sees {team.split()[-1]} winning by "
            f"{v4_dog_diff:+.1f} (vs v3 {dog_differential:+.1f}) — mastery-aware "
            f"signal, +{v4_bump} conviction"
        )

    # Pre-compute display labels here so all signal strings below can use them.
    # (Previously defined later in the confluence block; moved up because the
    # wRC+ diff cohort signal added 2026-05-21 references team_label/opp_label
    # before the confluence block — caused UnboundLocalError on first run.)
    team_label = team.split()[-1]
    opp_label = opp_team.split()[-1]

    # Team offense vs opposing hand (absolute level)
    if team_wrc >= 110:
        conviction += 8
        signals['offense'] = f"{team.split()[-1]} wRC+ {team_wrc:.0f} vs opp hand — elite bat"
    elif team_wrc >= 100:
        conviction += 4
        signals['offense'] = f"{team.split()[-1]} wRC+ {team_wrc:.0f} vs opp hand — above avg"

    # wRC+ DIFFERENTIAL — dog has hitter advantage over opponent. This is the
    # wrc_diff_away_adv_ml / wrc_diff_home_adv_ml cohort (audit 2026-05-21:
    # 58% lifetime n=130+ when dog has wRC+ advantage). Real cohort signal,
    # not just absolute level. Added after spread_delta trap zone audit.
    opp_wrc_vs_hand = _f(g.get(f'{opp_prefix}_wrc_vs_opp_hand'))
    opp_wrc_season = _f(g.get(f'{opp_prefix}_wrc_plus')) or 100
    opp_wrc = opp_wrc_vs_hand if opp_wrc_vs_hand is not None else opp_wrc_season
    if team_wrc >= opp_wrc + 10:
        conviction += 6
        signals['wrc_diff'] = (
            f"{team_label} wRC+ {team_wrc:.0f} vs {opp_label} wRC+ {opp_wrc:.0f} "
            f"(+{team_wrc - opp_wrc:.0f} dog hitter edge — 58% cohort)"
        )
    elif team_wrc >= opp_wrc + 5:
        conviction += 3
        signals['wrc_diff'] = (
            f"{team_label} wRC+ {team_wrc:.0f} vs {opp_label} wRC+ {opp_wrc:.0f} "
            f"(+{team_wrc - opp_wrc:.0f} hitter edge)"
        )

    # Starter edge — Dawg's pitcher having a better matchup than expected
    if xera is not None and opp_xera is not None and opp_xera - xera >= 1.0:
        conviction += 8
        signals['sp_edge'] = f"{starter} ({xera:.2f} xERA) vs {opp_starter} ({opp_xera:.2f}) — pitching advantage"
    elif l3_era is not None and l3_era <= 3.0 and xera is not None and xera <= 4.0:
        conviction += 6
        signals['sp_form'] = f"{starter} L3 ERA {l3_era:.2f} — locked in"

    # Opposing starter form drift (weakness for the opposition)
    if opp_l3_era is not None and opp_l3_era >= 5.5:
        conviction += 6
        signals['opp_form'] = f"{opp_starter} L3 ERA {opp_l3_era:.2f} — trending wrong way"

    # Bullpen edge
    if bullpen is not None and opp_bullpen is not None and opp_bullpen - bullpen >= 0.8:
        conviction += 5
        signals['bullpen'] = f"{team.split()[-1]} BP {bullpen:.2f} vs {opp_team.split()[-1]} BP {opp_bullpen:.2f}"

    # Home dog bonus — home field counts for dogs
    if is_home_dawg:
        conviction += 5
        signals['venue'] = f"Home dog advantage ({g.get('venue')})"

    # Signal confluence — must be DIRECTION-aware (bugfix 2026-05-16).
    # `signal_confluence_net` was always positive when signals favored `model_pick`,
    # which could be home OR away. Reading net > 0 as "stacks on dog" was wrong
    # whenever model_pick was the favorite side (e.g. 5/16 ARI/COL — net +4 favored
    # ARI, but DOD picked COL home dog and incorrectly claimed PRIME confluence on it).
    # Fix: parse breakdown directly to determine which side the signals support, then
    # bonus only when aligned with the dog, penalty when opposed.
    breakdown = g.get('signal_confluence_breakdown') or {}
    if isinstance(breakdown, str):
        import json as _json
        try:
            breakdown = _json.loads(breakdown)
        except Exception:
            breakdown = {}
    h_count = sum(1 for v in breakdown.values() if v == 'home')
    a_count = sum(1 for v in breakdown.values() if v == 'away')
    if h_count > a_count:
        conf_side, conf_mag = 'home', h_count - a_count
    elif a_count > h_count:
        conf_side, conf_mag = 'away', a_count - h_count
    else:
        conf_side, conf_mag = None, 0

    dawg_side = 'home' if is_home_dawg else 'away'
    # team_label / opp_label defined earlier (above the wRC+ diff cohort signal)

    # CONFLUENCE LADDER — matches the SIDE dim reweight from commit 813d6ad
    # (2026-06-05). n=640 cohort scan revealed:
    #   net=4 + DOG RL: 82.6% (n=23) ← PEAK band
    #   net=5 + DOG RL: 50.0% (n=16) ← deteriorates
    #   net=6 + DOG RL: 28.6% (n=7)  ← worse than coinflip
    # Old DAWG selector treated conf_mag >= 4 as max bonus (+12) regardless
    # of mag — meant a net=6 over-saturated game got the same +12 as a
    # legitimate net=4 PEAK. Today's CWS@PHI dawg fired PRIME 100 conv at
    # net=+5 partly because of this miscalibration. New ladder peaks at
    # net=4 and decays after, same as SIDE dim.
    if conf_side == dawg_side and conf_mag >= 1:
        if conf_mag == 4:
            conviction += 12
            # Cohort % pulled fresh from cohort_stats.json (recomputed
            # nightly). Falls back to a generic label if the stats file
            # is missing or older than 36h — never surfaces a stale number.
            from cohort_lookup import format_label as _cohort_label
            _cohort = _cohort_label('conf4_dog_rl', fallback='lifetime cohort')
            signals['confluence'] = f"PEAK confluence (+{conf_mag} — {_cohort} DOG RL on {team_label})"
        elif conf_mag >= 5:
            # Over-saturated band — keep some weight but cap below PEAK
            conviction += 6
            signals['confluence'] = f"Over-saturated confluence (+{conf_mag} signals on {team_label} — market priced in)"
        elif conf_mag == 3:
            conviction += 6
            signals['confluence'] = f"Confluence edge (+{conf_mag} signals on {team_label})"
        elif conf_mag == 2:
            conviction += 3
            signals['confluence'] = f"Confluence lean (+{conf_mag} signals on {team_label})"
        else:
            conviction += 1
            signals['confluence'] = f"Slight confluence (+{conf_mag} on {team_label})"
    elif conf_side and conf_side != dawg_side and conf_mag >= 2:
        # Signals oppose the dog — penalize. Don't reject outright (dog_edge math may
        # still justify the play), but the conviction floor drops materially.
        if conf_mag >= 4:
            conviction -= 15
            signals['confluence_warn'] = f"⚠ PRIME confluence AGAINST (+{conf_mag} signals on {opp_label}) — fade risk"
        else:
            conviction -= 8
            signals['confluence_warn'] = f"⚠ STRONG confluence AGAINST (+{conf_mag} signals on {opp_label})"

    conviction = max(0, min(100, conviction))
    tier = 'PRIME' if conviction >= 80 else 'STRONG' if conviction >= 65 else 'LEAN'

    return {
        'team': team,
        'matchup': f"{g.get('away_team')} @ {g.get('home_team')}",
        'game_id': g.get('game_id'),
        'spread_delta': sd,
        'close_spread': cs,
        'team_ml': team_ml,
        'conviction': conviction,
        'tier': tier,
        'signals': signals,
        'venue': g.get('venue'),
        'opp_team': opp_team,
        'starter': starter,
        'opp_starter': opp_starter,
    }


def build_narrative(dawg):
    """One-sentence Jerry take on why the dog is barking."""
    if not ANTHROPIC_API_KEY:
        return f"Market's got {dawg['team'].split()[-1]} at {dawg.get('team_ml', 0):+d} ML, but Jerry sees this one closer to a coin flip — value's on the dog."

    signals_text = " | ".join(dawg['signals'].values())
    prompt = f"""You are Jerry — sharp, energetic, slightly degenerate but always analytically grounded. Today's Dawg of the Day is {dawg['team']} ML vs {dawg['opp_team']}.

What the model sees:
{signals_text}

Write ONE paragraph (3-4 sentences) in Jerry's voice — confident, data-specific, a touch of swagger, acknowledging they're the underdog but explaining why the model loves them anyway. Close with something like "That's why this dog is barking today." or similar — vary the close, don't template.

Rules:
- Start immediately with analysis (no "Let me look at..." preamble)
- Reference specific data points from what the model sees
- Sound like a sharp friend, not a marketing pitch
- Never say "bet" or "must play" or "lock it in"
- High energy but data-backed"""

    try:
        r = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'Content-Type': 'application/json',
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
            },
            json={
                'model': 'claude-haiku-4-5-20251001',
                'max_tokens': 260,
                'messages': [{'role': 'user', 'content': prompt}]
            },
            timeout=10
        )
        data = r.json()
        text = ''.join(
            b.get('text', '') for b in (data.get('content') or [])
            if b.get('type') == 'text'
        )
        return text.strip() or f"Market's got {dawg['team'].split()[-1]} as a dog, but the model disagrees across multiple signals. This one's barking."
    except Exception as e:
        print(f"  ⚠️ narrative failed: {e}")
        return f"Market's got {dawg['team'].split()[-1]} at {dawg.get('team_ml', 0):+d} ML, but Jerry sees this one closer to a coin flip — value's on the dog."


def upsert_dawg(gd, dawg, narrative):
    payload = {
        'game_date': gd,
        'team': dawg['team'],
        'matchup': dawg['matchup'],
        'game_id': dawg['game_id'],
        'spread_delta': dawg['spread_delta'],
        'close_spread': dawg['close_spread'],
        'conviction': dawg['conviction'],
        'tier': dawg['tier'],
        'signals': dawg['signals'],
        'narrative': narrative,
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/daily_dawg?on_conflict=game_date",
        headers=HEADERS,
        json=payload,
        timeout=15
    )
    if r.status_code not in (200, 201, 204):
        print(f"  ⚠️ upsert failed {r.status_code}: {r.text[:300]}")
        return False
    return True


def run():
    gd = today_et()
    print(f"=== Dawg of the Day {gd} ===")

    # CLI flags
    force = '--force' in sys.argv
    dry_run = '--dry-run' in sys.argv
    if dry_run:
        print("  [DRY RUN MODE — no writes]")

    # Overwrite guard — if today's row already exists, don't regenerate unless --force
    if not force:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/daily_dawg?game_date=eq.{gd}&select=team,conviction",
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
            timeout=10
        )
        if r.status_code == 200 and r.json():
            existing = r.json()[0]
            print(f"  Dawg already exists for {gd}: {existing.get('team')} (conviction {existing.get('conviction')})")
            print(f"  Skipping — pass --force to overwrite")
            return

    games = fetch_todays_games()
    print(f"  Evaluating {len(games)} games...")

    ml_map = fetch_ml_odds_map()
    if ml_map:
        print(f"  Loaded ML odds for {len(ml_map)} games")
    else:
        print("  ⚠️ No ML odds available — Dawg filter will reject all candidates")

    dawg_candidates = []
    diag = []
    for g in games:
        d = score_dawg(g, diag=diag, ml_map=ml_map)
        if d:
            dawg_candidates.append(d)

    if not dawg_candidates:
        print("  No Dawg candidates today — diag:")
        for line in diag[:20]:
            print(line)
        return

    dawg_candidates.sort(key=lambda d: d['conviction'], reverse=True)
    top = dawg_candidates[0]

    # Minimum-conviction floor (added 2026-05-28). When the best candidate
    # is below this, output "no eligible Dawg today" instead of forcing a
    # publish on a weak signal. Floor calibrated to STRONG-tier — a 65-
    # conviction Dawg has cleared the v3 edge gate + xERA gate + ML gate
    # AND has at least one supporting signal beyond raw edge. Below 65,
    # the upside doesn't justify the social commitment of publishing.
    MIN_PUBLISH_CONVICTION = 65
    if top['conviction'] < MIN_PUBLISH_CONVICTION:
        print(f"\n  ⚠️ Top candidate {top['team']} at conviction {top['conviction']} below publish floor ({MIN_PUBLISH_CONVICTION})")
        print(f"  No eligible Dawg today — best of {len(dawg_candidates)} candidate(s) wasn't strong enough.")
        print(f"\n  Candidate ranking:")
        for d in dawg_candidates[:5]:
            print(f"    [{d['conviction']}] {d['team']} — {d['matchup']}  (ML {d.get('team_ml', 0):+d})")
        return

    print(f"\n🐕 Dawg of the Day: {top['team']} ({top['tier']} {top['conviction']})")
    print(f"  {top['matchup']}")
    print(f"  ML {top.get('team_ml', 0):+d} | Model delta {top['spread_delta']:+.1f}")
    for s in top['signals'].values():
        print(f"      · {s}")

    if len(dawg_candidates) > 1:
        print(f"\n  Runners-up:")
        for d in dawg_candidates[1:4]:
            print(f"    [{d['conviction']}] {d['team']} — {d['matchup']}")

    # --dry-run honored properly now (added 2026-05-28). Previously the flag
    # was accepted but the upsert ran anyway, so testing the picker against
    # a live row was destructive. Now --dry-run prints the narrative + would-
    # be Dawg without persisting.
    if dry_run:
        print(f"\n  [DRY RUN] Skipping narrative generation + upsert.")
        print(f"  Would store: {top['team']} ({top['matchup']}) — conviction {top['conviction']}")
        return

    print(f"\n  Building Jerry narrative...")
    narrative = build_narrative(top)
    print(f"  Narrative: {narrative[:200]}...")

    if upsert_dawg(gd, top, narrative):
        print(f"\n✅ Dawg of the Day stored for {gd}")


if __name__ == "__main__":
    run()
